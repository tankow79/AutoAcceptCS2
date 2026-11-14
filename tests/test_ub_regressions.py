"""Regression tests for defects found by the adversarial audit (each failed before its fix)."""
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

from acs import winapi
from acs.accept import Accepter
from acs.settings import Store
from acs.watcher import WATCHING, Watcher


def make_watcher(tmp_path: Path, log: Path):
    store = Store(tmp_path / "config.json")
    store.load()
    store.update(log_auto=False, log_path=str(log))
    acc, ntf = MagicMock(), MagicMock()
    return Watcher(store, acc, ntf), acc


def wait_for(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def ids(acc):
    return [c.args[0] for c in acc.on_match.call_args_list]


# ---- 1. the tail must not lock the game's log file --------------------------------------------
def test_tail_does_not_block_rename_and_follows_the_replacement_file(tmp_path):
    log = tmp_path / "console.log"
    log.write_text("old line\n")
    w, acc = make_watcher(tmp_path, log)
    w.start()
    try:
        assert wait_for(lambda: w.status == WATCHING, 5)
        os.replace(log, tmp_path / "console.log.old")  # what CS2 may do on start-up; PermissionError before the fix
        log.write_text("lobby match_id=77 ready\n")  # brand-new file: must be read from its first byte
        assert wait_for(lambda: ids(acc) == ["77"], 6)
    finally:
        w.stop()


# ---- 2. truncation is noticed immediately, not after a 2 s poll ---------------------------------
def test_truncated_log_is_followed_within_one_second(tmp_path):
    log = tmp_path / "console.log"
    log.write_text("x\n" * 200)  # tail position ends up at 400
    w, acc = make_watcher(tmp_path, log)
    w.start()
    try:
        assert wait_for(lambda: w.status == WATCHING, 5)
        time.sleep(0.25)  # the tail has parked at EOF
        log.write_text("match_id=5\n")  # truncate + rewrite, shorter than the parked position
        assert wait_for(lambda: ids(acc) == ["5"], 1.0)
    finally:
        w.stop()


# ---- 3. never click if focus left CS2 between the check and the press ---------------------------
class FakeUser32:
    def __init__(self):
        self.foreground = 111
        self.events: list[tuple] = []

    def SetCursorPos(self, x, y):  # noqa: N802
        self.events.append(("move", x, y))
        return 1

    def mouse_event(self, flags, *_):
        self.events.append(("btn", flags))

    def GetForegroundWindow(self):  # noqa: N802
        return self.foreground


def test_click_is_aborted_when_the_game_loses_focus_after_the_hover(tmp_path, monkeypatch):
    fake = FakeUser32()
    monkeypatch.setattr(winapi, "user32", fake)
    monkeypatch.setattr(winapi.time, "sleep", lambda _s: None)
    orig = fake.SetCursorPos
    fake.SetCursorPos = lambda x, y: (orig(x, y), setattr(fake, "foreground", 222))[0]  # alt-tab during the hover
    assert winapi.click(5, 6, hwnd=111) is False
    assert [e for e in fake.events if e[0] == "btn"] == []


def test_click_presses_and_releases_when_the_game_keeps_focus(monkeypatch):
    fake = FakeUser32()
    monkeypatch.setattr(winapi, "user32", fake)
    monkeypatch.setattr(winapi.time, "sleep", lambda _s: None)
    assert winapi.click(5, 6, hwnd=111) is True
    assert fake.events == [("move", 5, 6), ("btn", 0x0002), ("btn", 0x0004)]


def test_accepter_hands_the_checked_window_to_the_click(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(winapi, "find_cs2_window", lambda: 4242)
    monkeypatch.setattr(winapi, "client_rect", lambda h: (0, 0, 1000, 500))
    monkeypatch.setattr(winapi, "is_foreground", lambda h: True)
    monkeypatch.setattr(winapi, "focus", lambda h: True)
    monkeypatch.setattr(winapi, "click", lambda x, y, hwnd=None: calls.append((x, y, hwnd)) or True)
    store = Store(tmp_path / "c.json")
    store.load()
    store.update(click_delay_ms=0, click_interval_ms=50, click_count=1, cooldown_sec=0)
    Accepter(store, MagicMock())._accept("1")
    assert calls == [(500, 205, 4242)]


# ---- 4. a failed save must be visible and two processes must not share one temp file ------------
def test_save_failure_is_reported_and_memory_state_survives(tmp_path):
    blocker = tmp_path / "config.json"
    blocker.mkdir()  # os.replace onto a directory fails
    store = Store(blocker)
    store.update(tg_chat_id="77")
    assert store.get().tg_chat_id == "77"
    assert store.last_error is not None


def test_temp_file_name_is_unique_per_process(tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append(Path(a).name), real_replace(a, b))[1])
    store = Store(tmp_path / "config.json")
    store.update(tg_chat_id="1")
    assert str(os.getpid()) in seen[0]
    assert store.last_error is None


# ---- 5. review round 2 ------------------------------------------------------------------------
def test_match_id_is_not_reacted_to_again_after_a_long_match(tmp_path, monkeypatch):
    w, acc = make_watcher(tmp_path, tmp_path / "x.log")
    line = "Received Steam datagram ticket for server steamid:123 vport 0. match_id=777"
    w.handle_line(line)
    real = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: real + 3 * 3600)  # three hours later (reconnect replay)
    w.handle_line(line)
    assert ids(acc) == ["777"]


def test_cancel_before_the_accept_thread_starts_is_not_lost(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(winapi, "find_cs2_window", lambda: 1)
    monkeypatch.setattr(winapi, "client_rect", lambda h: (0, 0, 100, 100))
    monkeypatch.setattr(winapi, "is_foreground", lambda h: True)
    monkeypatch.setattr(winapi, "click", lambda x, y, hwnd=None: calls.append(1) or True)
    store = Store(tmp_path / "c.json")
    store.load()
    store.update(click_delay_ms=200, click_interval_ms=50, click_count=1, cooldown_sec=0)
    acc = Accepter(store, MagicMock())
    acc.on_match("9")
    acc.cancel()  # the expiry line arrives right after the match line
    time.sleep(0.6)
    assert calls == []


def test_clicks_are_retried_while_the_game_window_is_not_ready_yet(tmp_path, monkeypatch):
    state = {"n": 0}
    calls = []

    def foreground(h):
        return state["n"] >= 2

    def find():
        state["n"] += 1
        return 1

    monkeypatch.setattr(winapi, "find_cs2_window", find)
    monkeypatch.setattr(winapi, "client_rect", lambda h: (0, 0, 100, 100))
    monkeypatch.setattr(winapi, "is_foreground", foreground)
    monkeypatch.setattr(winapi, "focus", lambda h: False)
    monkeypatch.setattr(winapi, "click", lambda x, y, hwnd=None: calls.append(1) or True)
    store = Store(tmp_path / "c.json")
    store.load()
    store.update(click_delay_ms=0, click_interval_ms=50, click_count=3, cooldown_sec=0)
    Accepter(store, MagicMock())._accept("1")
    assert calls  # the first attempt failed, a later one succeeded


def test_notifier_unblocks_after_the_config_is_fixed_by_a_successful_test(tmp_path, monkeypatch):
    from acs import notifier as n
    store = Store(tmp_path / "c.json")
    store.load()
    store.update(tg_enabled=True, tg_token="1" * 8 + ":" + "a" * 30, tg_chat_id="5")
    sent = []
    results = iter([n.SendResult(False, "HTTP 403", fatal=True), n.SendResult(True, "HTTP 200")])
    monkeypatch.setattr(n, "send_gated", lambda t, c, text, stop=None: sent.append(text) or next(results))
    ntf = n.Notifier(store)
    ntf._deliver("a")  # blocked by a 403
    ntf._deliver("b")  # still blocked: no request
    assert sent == ["a"]
    n.post_message  # noqa: B018  (test button path must clear the latch)
    ntf.unblock()
    ntf._deliver("c")
    assert sent == ["a", "c"]


def test_unreadable_config_is_never_overwritten_with_defaults(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"tg_token": "keep-me"}', encoding="utf-8")
    store = Store(path)
    real = Path.read_text

    def locked(self, *a, **k):
        if self == path:
            raise PermissionError("locked by antivirus")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", locked)
    store.load()
    store.update(enabled=False)  # must not clobber the real file
    monkeypatch.setattr(Path, "read_text", real)
    assert "keep-me" in path.read_text(encoding="utf-8") and not path.with_suffix(".bad").exists()


# ---- 6. fuzz findings ---------------------------------------------------------------------------
def test_lone_surrogate_in_a_setting_does_not_break_saving(tmp_path):
    store = Store(tmp_path / "config.json")
    store.load()
    store.update(tg_chat_id="\ud800")  # must not raise
    assert not list(tmp_path.glob("*.tmp"))
    assert store.get().enabled is True


def test_ping_search_is_fast_on_a_hostile_long_line(tmp_path):
    w, acc = make_watcher(tmp_path, tmp_path / "x.log")
    w.handle_line("Received Steam datagram ticket for server steamid:1 vport 0. match_id=5")
    t = time.monotonic()
    w.handle_line("Selecting as primary " * 380)
    assert time.monotonic() - t < 0.2


def test_send_once_survives_garbage_429_and_nan_retry_after(monkeypatch):
    import json
    from acs import notifier as n

    def fake(body, status):
        class R:
            def __init__(s):
                s.status = status

            def read(s, k=-1):
                return body

        class C:
            def __init__(s, *a, **k): ...
            def connect(s): ...
            def request(s, *a, **k): ...
            def getresponse(s):
                return R()

            def close(s): ...
        return C

    tok = "1" * 8 + ":" + "a" * 30
    for params in ('"abc"', "[1]", "{\"retry_after\": \"x\"}", "{\"retry_after\": NaN}", "{\"retry_after\": Infinity}"):
        monkeypatch.setattr(n.http.client, "HTTPSConnection", fake(('{"parameters": %s}' % params).encode(), 429))
        r = n.send_once(tok, "1", "x")
        assert r.retry_after == r.retry_after and r.retry_after < 1e9 and r.safe_retry  # finite, retryable
    monkeypatch.setattr(n.http.client, "HTTPSConnection", fake(b"[" * 50000, 200))
    assert n.send_once(tok, "1", "x").ok in (True, False)  # no RecursionError
    assert json


# ---- 7. second black-box round -----------------------------------------------------------------
def test_config_with_utf8_bom_is_loaded_not_quarantined(tmp_path):
    p = tmp_path / "config.json"
    p.write_bytes(b'\xef\xbb\xbf{"enabled": false, "tg_token": "keepme"}')
    s = Store(p)
    assert s.load().tg_token == "keepme" and not p.with_suffix(".bad").exists()


def test_click_point_never_leaves_the_window():
    from acs.accept import target_point
    from acs.settings import Settings
    assert target_point(Settings(click_x_pct=100, click_y_pct=100), (-1920, 0, 1920, 1080)) == (-1, 1079)


def test_cancelled_sequence_does_not_start_the_cooldown_or_swallow_the_next_match(tmp_path, monkeypatch):
    clicks = []
    monkeypatch.setattr(winapi, "find_cs2_window", lambda: 1)
    monkeypatch.setattr(winapi, "client_rect", lambda h: (0, 0, 100, 100))
    monkeypatch.setattr(winapi, "is_foreground", lambda h: True)
    monkeypatch.setattr(winapi, "click", lambda x, y, hwnd=None: clicks.append(time.monotonic()) or True)
    store = Store(tmp_path / "c.json")
    store.load()
    store.update(click_delay_ms=0, click_interval_ms=200, click_count=10, cooldown_sec=60)
    acc = Accepter(store, MagicMock())
    acc.on_match("1")
    assert wait_for(lambda: len(clicks) >= 1, 2)
    acc.cancel()
    acc.on_match("2")  # right after the expiry: must not be lost to "still running" nor to the cooldown
    n = len(clicks)
    assert wait_for(lambda: len(clicks) > n, 3)
    time.sleep(0.5)
    assert len(clicks) < 12  # the first sequence did not resume (10 + 10 clicks would be ~20)


def test_disabling_the_app_during_the_delay_sends_no_false_no_window_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(winapi, "find_cs2_window", lambda: 1)
    store = Store(tmp_path / "c.json")
    store.load()
    store.update(click_delay_ms=200, cooldown_sec=0)
    ntf = MagicMock()
    acc = Accepter(store, ntf)
    acc.on_match("1")
    time.sleep(0.05)
    store.update(enabled=False)
    time.sleep(0.4)
    texts = [c.args[0] for c in ntf.send.call_args_list]
    assert len(texts) == 1  # only the "match found" message


def test_notifier_duplicate_window_counts_from_delivery(tmp_path, monkeypatch):
    from acs import notifier as n
    store = Store(tmp_path / "c.json")
    store.load()
    store.update(tg_enabled=True, tg_token="1" * 8 + ":" + "a" * 30, tg_chat_id="5")
    clock = [0.0]
    sent = []

    def gated(t, c, text, stop=None):
        sent.append(text)
        clock[0] += 10  # a slow delivery (flood-wait, timeouts)
        return n.SendResult(True, "ok")

    monkeypatch.setattr(n, "send_gated", gated)
    monkeypatch.setattr(n.time, "monotonic", lambda: clock[0])
    ntf = n.Notifier(store)
    ntf._deliver("match")
    ntf._deliver("match")
    assert sent == ["match"]


def test_poller_prime_ignores_keys_already_held():
    from acs import hotkeys
    p = hotkeys.HotkeyPoller({"a": "F6"}, down=lambda vk: True)
    p.prime()
    assert p.poll() == []
