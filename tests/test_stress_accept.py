import threading
import time
from unittest.mock import MagicMock

import pytest

from acs import accept, i18n
from acs.accept import Accepter, capture_cursor_pct, click_pct, pct_in_rect
from acs.settings import Store

RECT = (10, 20, 1000, 500)


class FakeWin:
    def __init__(self):
        self.hwnd = 100
        self.rect = RECT
        self.foreground = True
        self.focus_ok = True
        self.clicks: list[tuple[int, int]] = []
        self.focus_calls = 0
        self.on_click = None
        self.click_error = None

    def find_cs2_window(self):
        return self.hwnd

    def client_rect(self, hwnd):
        return self.rect

    def is_foreground(self, hwnd):
        return self.foreground

    def focus(self, hwnd):
        self.focus_calls += 1
        if self.focus_ok:
            self.foreground = True
        return self.focus_ok

    def click(self, x, y, hwnd=None):
        if self.click_error:
            raise self.click_error
        self.clicks.append((x, y))
        if self.on_click:
            self.on_click(len(self.clicks))
        return True

    def cursor_pos(self):
        return (0, 0)


@pytest.fixture
def win(monkeypatch):
    w = FakeWin()
    for name in ("find_cs2_window", "client_rect", "is_foreground", "focus", "click", "cursor_pos"):
        monkeypatch.setattr(accept.winapi, name, getattr(w, name))
    monkeypatch.setattr(accept.sounds, "play", lambda *_a, **_k: pytest.fail("beep is off by default"))
    return w


@pytest.fixture
def make(tmp_path):
    def _make(**kw):
        store = Store(tmp_path / "c.json")
        store.load()
        base = dict(click_delay_ms=0, click_interval_ms=50, cooldown_sec=0, click_count=3)
        base.update(kw)
        store.update(**base)
        ntf = MagicMock()
        return Accepter(store, ntf), ntf

    return _make


def msgs(ntf):
    return [c.args[0] for c in ntf.send.call_args_list]


# ---------------------------------------------------------------- normal flow

def test_normal_flow_clicks_count_times_at_right_pixel(win, make):
    acc, ntf = make(click_x_pct=50, click_y_pct=41, click_count=3)
    acc._safe_accept("1")
    assert win.clicks == [(10 + 500, 20 + 205)] * 3
    assert acc.accepted_count == 1 and acc.last_match[0] == "1"
    assert i18n.tr("msg.match") in msgs(ntf) and i18n.tr("msg.no_window") not in msgs(ntf)


@pytest.mark.parametrize("count", [1, 2, 5])
def test_click_count_respected(win, make, count):
    acc, _ = make(click_count=count)
    acc._safe_accept("1")
    assert len(win.clicks) == count


def test_click_position_extremes_stay_inside_window(win, make):
    for x, y in ((0, 0), (100, 100), (0, 100), (100, 0)):
        win.clicks.clear()
        acc, _ = make(click_x_pct=x, click_y_pct=y, click_count=1)
        acc._safe_accept("1")
        px, py = win.clicks[0]
        assert 10 <= px <= 1010 and 20 <= py <= 520


def test_negative_origin_multi_monitor_rect(win, make):
    win.rect = (-1920, -50, 1920, 1080)
    acc, _ = make(click_x_pct=50, click_y_pct=50, click_count=1)
    acc._safe_accept("1")
    assert win.clicks == [(-960, 490)]


def test_on_match_runs_in_background_thread(win, make):
    acc, _ = make(click_count=1)
    acc.on_match("5")
    end = time.monotonic() + 3
    while not win.clicks and time.monotonic() < end:
        time.sleep(0.01)
    assert len(win.clicks) == 1


def test_disabled_during_sequence_stops_clicks(win, make):
    acc, _ = make(click_count=5)
    win.on_click = lambda n: acc.store.update(enabled=False) if n == 2 else None
    acc._safe_accept("1")
    assert len(win.clicks) == 2


# ---------------------------------------------------------------- cancel

def test_cancel_mid_sequence_stops_further_clicks(win, make):
    acc, ntf = make(click_count=5)
    win.on_click = lambda n: acc.cancel() if n == 2 else None
    acc._safe_accept("1")
    assert len(win.clicks) == 2
    assert i18n.tr("msg.no_window") not in msgs(ntf)


def test_cancel_during_delay_means_no_click_and_no_warning(win, make):
    acc, ntf = make(click_delay_ms=2000, click_count=3)
    t = threading.Thread(target=acc._safe_accept, args=("1",))
    t0 = time.monotonic()
    t.start()
    time.sleep(0.05)
    acc.cancel()
    t.join(3)
    assert not t.is_alive() and time.monotonic() - t0 < 1.5
    assert win.clicks == [] and i18n.tr("msg.no_window") not in msgs(ntf)


def test_cancel_before_next_match_does_not_poison_it(win, make):
    acc, _ = make(click_count=2)
    acc.cancel()  # stale cancel from an earlier expiry
    acc.on_match("1")  # a new match re-arms the accepter
    deadline = time.monotonic() + 3
    while len(win.clicks) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(win.clicks) == 2


# ---------------------------------------------------------------- failure modes

def test_window_missing_no_click_and_warning(win, make):
    win.hwnd = None
    acc, ntf = make()
    acc._safe_accept("1")
    assert win.clicks == []
    assert msgs(ntf) == [i18n.tr("msg.match"), i18n.tr("msg.no_window")]
    assert acc.accepted_count == 0


def test_rect_unavailable_no_click(win, make):
    win.rect = None
    acc, ntf = make()
    acc._safe_accept("1")
    assert win.clicks == [] and i18n.tr("msg.no_window") in msgs(ntf)


def test_not_foreground_and_focus_fails_no_click(win, make):
    win.foreground, win.focus_ok = False, False
    acc, ntf = make()
    acc._safe_accept("1")
    assert win.clicks == [] and win.focus_calls >= 1  # retried on every attempt
    assert i18n.tr("msg.no_window") in msgs(ntf)


def test_not_foreground_focus_disabled_never_focuses_or_clicks(win, make):
    win.foreground = False
    acc, _ = make(focus_cs2=False)
    acc._safe_accept("1")
    assert win.clicks == [] and win.focus_calls == 0


def test_not_foreground_focus_succeeds_clicks(win, make):
    win.foreground = False
    acc, _ = make(click_count=2)
    acc._safe_accept("1")
    assert len(win.clicks) == 2 and win.focus_calls == 1


def test_focus_stolen_midway_stops_clicking(win, make):
    acc, _ = make(click_count=4, focus_cs2=False)

    def steal(n):
        if n == 2:
            win.foreground = False

    win.on_click = steal
    acc._safe_accept("1")
    assert len(win.clicks) == 2  # never clicks into another app


def test_click_exception_is_swallowed_and_logged(win, make, caplog):
    win.click_error = RuntimeError("SendInput failed")
    acc, _ = make()
    with caplog.at_level("ERROR", logger="acs.accept"):
        acc._safe_accept("1")  # must not raise
    assert "click failed" in caplog.text
    assert acc.accepted_count == 0
    assert acc._busy.acquire(blocking=False)  # lock released
    acc._busy.release()


def test_unexpected_exception_in_accept_is_logged_and_lock_released(win, make, caplog, monkeypatch):
    acc, _ = make()
    monkeypatch.setattr(accept.winapi, "find_cs2_window", MagicMock(side_effect=OSError("boom")))
    with caplog.at_level("ERROR", logger="acs.accept"):
        acc._safe_accept("1")
    assert "accept failed" in caplog.text
    assert acc._busy.acquire(blocking=False)
    acc._busy.release()


def test_notifier_exception_does_not_wedge_accepter(win, make):
    acc, ntf = make()
    ntf.send.side_effect = RuntimeError("tg down")
    acc._safe_accept("1")  # raises inside _accept -> swallowed
    ntf.send.side_effect = None
    acc._safe_accept("2")
    assert len(win.clicks) == 3


def test_beep_plays_when_enabled(win, make, monkeypatch):
    played = []
    monkeypatch.setattr(accept.sounds, "play", played.append)
    acc, _ = make(beep=True, click_count=1)
    acc._safe_accept("1")
    assert played == ["match"]


# ---------------------------------------------------------------- cooldown / concurrency

def test_cooldown_suppresses_second_match(win, make):
    acc, ntf = make(cooldown_sec=60, click_count=1)
    acc._safe_accept("1")
    acc._safe_accept("2")
    assert len(win.clicks) == 1 and acc.last_match[0] == "1"
    assert msgs(ntf).count(i18n.tr("msg.match")) == 1


def test_cooldown_elapses(win, make, monkeypatch):
    acc, _ = make(cooldown_sec=5, click_count=1)
    acc._safe_accept("1")
    acc._last_done -= 6
    acc._safe_accept("2")
    assert len(win.clicks) == 2


def test_20_concurrent_matches_yield_single_sequence(win, make):
    acc, ntf = make(click_count=2, cooldown_sec=0)
    gate = threading.Event()
    win.on_click = lambda n: gate.wait(5)  # hold the first sequence open while the others try
    barrier = threading.Barrier(20)

    def go(i):
        barrier.wait()
        acc._safe_accept(str(i))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    for t in ts:
        t.start()
    end = time.monotonic() + 5
    while sum(t.is_alive() for t in ts) > 1 and time.monotonic() < end:
        time.sleep(0.01)
    assert sum(t.is_alive() for t in ts) == 1  # 19 lost the race and returned immediately
    gate.set()
    for t in ts:
        t.join(5)
    assert len(win.clicks) == 2
    assert msgs(ntf).count(i18n.tr("msg.match")) == 1
    assert acc.accepted_count == 1


def test_20_on_match_calls_via_public_api(win, make):
    acc, _ = make(click_count=1, click_delay_ms=300, cooldown_sec=5)
    for i in range(20):
        acc.on_match(str(i))
    time.sleep(0.8)
    assert len(win.clicks) == 1


# ---------------------------------------------------------------- helpers

def test_pct_in_rect_boundaries():
    r = (10, 20, 100, 50)
    assert pct_in_rect(10, 20, r) == (0.0, 0.0)  # left/top inclusive
    assert pct_in_rect(109, 69, r) == (99.0, 98.0)
    assert pct_in_rect(110, 20, r) is None  # right/bottom exclusive
    assert pct_in_rect(10, 70, r) is None
    assert pct_in_rect(9, 20, r) is None and pct_in_rect(10, 19, r) is None
    assert pct_in_rect(60, 45, r) == (50.0, 50.0)


def test_pct_in_rect_degenerate_and_negative():
    assert pct_in_rect(0, 0, (0, 0, 0, 0)) is None
    assert pct_in_rect(5, 5, (5, 5, 0, 10)) is None
    assert pct_in_rect(5, 5, (5, 5, 10, 0)) is None
    assert pct_in_rect(-960, 490, (-1920, -50, 1920, 1080)) == (50.0, 50.0)
    assert pct_in_rect(10**9, 10**9, (0, 0, 10, 10)) is None


def test_capture_cursor_pct(win, monkeypatch):
    monkeypatch.setattr(accept.winapi, "cursor_pos", lambda: (510, 270))
    assert capture_cursor_pct() == (50.0, 50.0)
    monkeypatch.setattr(accept.winapi, "cursor_pos", lambda: (5000, 270))
    assert capture_cursor_pct() is None
    win.hwnd = None
    assert capture_cursor_pct() is None
    win.hwnd, win.rect = 1, None
    assert capture_cursor_pct() is None


def test_click_pct_calibration(win):
    assert click_pct(50, 50) is True and win.clicks == [(510, 270)]
    win.foreground, win.focus_ok = False, False
    assert click_pct(50, 50) is False and len(win.clicks) == 1
    win.hwnd = None
    assert click_pct(50, 50) is False


def test_target_point_rounding_is_monotonic_and_inside(win, make):
    acc, _ = make()
    cfg = acc.store.get()
    prev = -1
    for pct in range(0, 101):
        x, _y = accept.target_point(type(cfg)(**{**cfg.__dict__, "click_x_pct": float(pct)}), RECT)
        assert x >= prev and RECT[0] <= x <= RECT[0] + RECT[2]
        prev = x
