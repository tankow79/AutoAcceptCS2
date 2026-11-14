import random
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from acs import watcher
from acs.settings import Store
from acs.watcher import WATCHING, Watcher


def make_store(tmp_path: Path, **kw) -> Store:
    s = Store(tmp_path / "config.json")
    s.load()
    if kw:
        s.update(**kw)
    return s


def make_watcher(tmp_path, **kw):
    acc, ntf = MagicMock(), MagicMock()
    return Watcher(make_store(tmp_path, **kw), acc, ntf), acc, ntf


def ids(acc):
    return [c.args[0] for c in acc.on_match.call_args_list]


def sent(ntf, kind):
    return [c for c in ntf.send.call_args_list if c.args[1] == kind]


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---------------------------------------------------------------- handle_line

def test_duplicate_ids_trigger_once(tmp_path):
    w, acc, _ = make_watcher(tmp_path)
    for _ in range(100):
        w.handle_line("lobby match_id=42 server")
    assert ids(acc) == ["42"]


def test_replayed_id_after_other_ids_is_ignored(tmp_path):
    w, acc, _ = make_watcher(tmp_path)
    for line in ("match_id=1", "match_id=2", "match_id=1", "match_id=2", "match_id=3", "match_id=1"):
        w.handle_line(line)
    assert ids(acc) == ["1", "2", "3"]


def test_match_id_memory_is_bounded_and_evicts_the_oldest(tmp_path):
    w, acc, _ = make_watcher(tmp_path)
    for i in range(watcher.RECENT_MAX + 1):
        w.handle_line(f"match_id={i}")
    w.handle_line("match_id=0")  # evicted -> treated as new
    w.handle_line(f"match_id={watcher.RECENT_MAX}")  # still remembered
    assert ids(acc).count("0") == 2 and ids(acc).count(str(watcher.RECENT_MAX)) == 1


def test_garbage_lines_never_raise_and_never_match(tmp_path):
    w, acc, ntf = make_watcher(tmp_path)
    rng = random.Random(1234)
    alphabet = [chr(c) for c in range(0, 0x250)] + ["퟿", "\U0001f600", "match_id", "=", "steamid:", "Ping"]
    for _ in range(10_000):
        kind = rng.random()
        if kind < 0.4:
            line = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200)))
        elif kind < 0.7:
            line = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 300))).decode("utf-8", "ignore")
        elif kind < 0.9:
            line = "match_id" + rng.choice(["", "=", "=x", "==5", " =5", "=-5", "=5x", "_=5"]) + "z" * rng.randint(0, 50)
        else:
            line = "x" * rng.randint(10_000, 50_000)
        w.handle_line(line)
    acc.on_match.assert_not_called()
    assert sent(ntf, "match") == []


def test_very_long_lines(tmp_path):
    w, acc, _ = make_watcher(tmp_path)
    w.handle_line("a" * 2_000_000)
    w.handle_line("a" * 1_000_000 + " match_id=9 " + "b" * 1_000_000)
    assert ids(acc) == ["9"]


def test_match_id_with_huge_number(tmp_path):
    w, acc, _ = make_watcher(tmp_path)
    w.handle_line("match_id=" + "9" * 5000)
    assert len(ids(acc)) == 1


def test_stale_expired_does_not_cancel_current_match(tmp_path):
    w, acc, ntf = make_watcher(tmp_path)
    w.handle_line("match_id=7 steamid:222")
    w.handle_line("ReservationExpired steamid:111")  # older server
    acc.cancel.assert_not_called()
    assert sent(ntf, "expired") == []
    w.handle_line("ReservationExpired steamid:222")
    acc.cancel.assert_called_once()
    assert len(sent(ntf, "expired")) == 1


def test_expired_without_steamid_cancels(tmp_path):
    w, acc, ntf = make_watcher(tmp_path)
    w.handle_line("match_id=7 steamid:222")
    w.handle_line("ReservationExpired")
    acc.cancel.assert_called_once()
    assert len(sent(ntf, "expired")) == 1


def test_expired_pair_notifies_once_per_match(tmp_path):
    w, acc, ntf = make_watcher(tmp_path)
    for n in range(1, 6):
        w.handle_line(f"match_id={n} steamid:900{n}")
        w.handle_line(f"[Client] ReservationExpired steamid:900{n}")
        w.handle_line(f"[Client] ... disconnect: ReservationExpired (steamid:900{n})")
        assert len(sent(ntf, "expired")) == n
    assert acc.cancel.call_count == 10  # every line still cancels the click sequence


def test_ping_only_once_per_match_and_ignores_stale(tmp_path):
    w, _, ntf = make_watcher(tmp_path)
    ping = "Selecting dc1 as primary (steamid:{sid}) Ping = {p}"
    w.handle_line("Selecting dc1 as primary Ping = 5")  # no match yet -> ignored
    assert sent(ntf, "ping") == []
    w.handle_line("match_id=1 steamid:11")
    w.handle_line(ping.format(sid=99, p=10))  # other server -> ignored
    assert sent(ntf, "ping") == []
    w.handle_line(ping.format(sid=11, p=20))
    w.handle_line(ping.format(sid=11, p=30))
    assert len(sent(ntf, "ping")) == 1 and "20" in sent(ntf, "ping")[0].args[0]
    w.handle_line("match_id=2 steamid:12")
    w.handle_line(ping.format(sid=12, p=40))
    assert len(sent(ntf, "ping")) == 2


def test_expired_cancels_waiting_ping(tmp_path):
    w, _, ntf = make_watcher(tmp_path)
    w.handle_line("match_id=1")
    w.handle_line("ReservationExpired")
    w.handle_line("Selecting dc as primary Ping = 3")
    assert sent(ntf, "ping") == []


def test_disabling_mid_stream_stops_everything(tmp_path):
    w, acc, ntf = make_watcher(tmp_path)
    w.handle_line("match_id=1")
    w.store.update(enabled=False)
    w.handle_line("match_id=2")
    w.handle_line("ReservationExpired")
    assert ids(acc) == ["1"]
    acc.cancel.assert_not_called()
    assert sent(ntf, "expired") == []


def test_keyword_change_and_case_insensitive(tmp_path):
    w, acc, _ = make_watcher(tmp_path)
    w.handle_line("MATCH_ID=1")
    w.store.update(match_keyword="a.b")  # regex metacharacters must be escaped
    w.handle_line("aXb=2")
    w.handle_line("a.b=3")
    assert ids(acc) == ["1", "3"]


def test_ping_regex_is_not_quadratic_on_hostile_line(tmp_path):
    w, _, _ = make_watcher(tmp_path)
    w.handle_line("match_id=1")
    line = "Selecting " + "as primary " * 6000
    t0 = time.perf_counter()
    w.handle_line(line)
    assert time.perf_counter() - t0 < 0.5



# ---------------------------------------------------------------- tail loop

@pytest.fixture
def fast_clock(monkeypatch):
    """Run the 2 s housekeeping check of _tail 50x faster (loop sleeps stay real and short)."""
    monkeypatch.setattr(watcher, "time", SimpleNamespace(monotonic=lambda: time.monotonic() * 50))


@pytest.fixture
def tailed(tmp_path, fast_clock):
    log = tmp_path / "console.log"
    log.write_bytes(b"old match_id=1000\n")
    w, acc, ntf = make_watcher(tmp_path, log_auto=False, log_path=str(log))
    w.start()
    assert wait_for(lambda: w.status == WATCHING)
    yield w, acc, ntf, log
    w.stop()
    w._thread.join(5)
    assert not w._thread.is_alive()


def append(log: Path, data: bytes) -> None:
    with open(log, "ab") as f:
        f.write(data)


def test_tail_ignores_existing_content(tailed):
    w, acc, _, log = tailed
    append(log, b"match_id=1\n")
    assert wait_for(lambda: ids(acc) == ["1"])


def test_tail_line_split_across_many_tiny_writes(tailed):
    _, acc, _, log = tailed
    for ch in b"xx match_id=555 steamid:1\n":
        append(log, bytes([ch]))
    assert wait_for(lambda: ids(acc) == ["555"])


def test_tail_crlf(tailed):
    _, acc, _, log = tailed
    append(log, b"match_id=11\r\nmatch_id=12\r\n")
    assert wait_for(lambda: ids(acc) == ["11", "12"])


def test_tail_invalid_utf8_bytes_inside_lines(tailed):
    _, acc, _, log = tailed
    append(log, b"\xff\xfe\x00junk match_id=21 \xc3\x28\n\x80\x80\x80\nmatch_id=22\n")
    assert wait_for(lambda: ids(acc) == ["21", "22"])


def test_tail_huge_partial_line_is_dropped_and_loop_recovers(tailed):
    _, acc, _, log = tailed
    append(log, b"x" * (3 * 1024 * 1024))  # 3 MB, no newline
    append(log, b"\nmatch_id=77\n")
    assert wait_for(lambda: ids(acc) == ["77"], timeout=10)


def test_tail_burst_of_2000_lines_with_50_ids(tailed):
    _, acc, _, log = tailed
    rng = random.Random(7)
    lines = []
    for i in range(2000):
        if i % 40 == 0:
            lines.append(f"[Client] found match_id={100 + i // 40} steamid:5")
        else:
            lines.append(f"noise {rng.random()} line {i}")
    # replay a few ids later in the burst: must not trigger again
    lines += [f"again match_id={100 + k}" for k in (0, 10, 20, 49)]
    append(log, ("\n".join(lines) + "\n").encode())
    assert wait_for(lambda: len(ids(acc)) >= 50)
    time.sleep(0.3)
    got = ids(acc)
    assert len(got) == 50 and len(set(got)) == 50 and got == [str(100 + k) for k in range(50)]


def test_tail_truncation_then_new_content(tailed):
    _, acc, _, log = tailed
    append(log, b"match_id=1\n" + b"padding line\n" * 50)
    assert wait_for(lambda: ids(acc) == ["1"])
    log.write_bytes(b"match_id=2\n")  # truncated (shorter than the read position)
    assert wait_for(lambda: ids(acc) == ["1", "2"])


def test_tail_switches_to_new_log_when_settings_change(tailed, tmp_path):
    # On Windows the watched file cannot be deleted/renamed while open (no FILE_SHARE_DELETE), so the
    # "file replaced" path is exercised through a settings change that points to a fresh file.
    w, acc, _, log = tailed
    other = tmp_path / "other.log"
    other.write_bytes(b"")
    w.store.update(log_path=str(other))
    n = [0]

    def feed():  # the loop re-attaches at the END of the new file, so keep writing fresh ids
        n[0] += 1
        append(other, f"match_id={2000 + n[0]}\n".encode())
        return len(ids(acc)) > 0

    assert wait_for(feed, timeout=8)
    assert w.log_path == other
    assert len(ids(acc)) == len(set(ids(acc)))


def test_tail_file_appears_later(tmp_path, fast_clock):
    log = tmp_path / "late.log"
    w, acc, _ = make_watcher(tmp_path, log_auto=False, log_path=str(log))
    watcher_stop_wait = w._stop.wait
    w._stop.wait = lambda t=None: watcher_stop_wait(min(t or 0, 0.05))  # do not sleep 3 s while NO_LOG
    w.start()
    try:
        assert wait_for(lambda: w.status == watcher.NO_LOG)
        log.write_bytes(b"")
        assert wait_for(lambda: w.status == WATCHING)
        n = [0]

        def feed():
            n[0] += 1
            append(log, f"match_id={n[0]}\n".encode())
            return len(ids(acc)) >= 1

        assert wait_for(feed)
    finally:
        w.stop()
        w._thread.join(5)
    assert not w._thread.is_alive()


def test_tail_survives_handle_line_exceptions(tailed):
    w, acc, _, log = tailed
    real = w.handle_line
    calls = {"n": 0}

    def flaky(line):
        calls["n"] += 1
        if "boom" in line:
            raise RuntimeError("boom")
        real(line)

    w.handle_line = flaky
    append(log, b"boom\nmatch_id=31\nboom\nmatch_id=32\n")
    assert wait_for(lambda: ids(acc) == ["31", "32"])


def test_stop_terminates_thread_quickly(tailed):
    w, *_ = tailed
    t0 = time.monotonic()
    w.stop()
    w._thread.join(3)
    assert not w._thread.is_alive() and time.monotonic() - t0 < 2.5


def test_status_callback_exceptions_do_not_kill_watcher(tmp_path, fast_clock):
    log = tmp_path / "c.log"
    log.write_bytes(b"")
    acc, ntf = MagicMock(), MagicMock()
    w = Watcher(make_store(tmp_path, log_auto=False, log_path=str(log)), acc, ntf,
                on_status=MagicMock(side_effect=RuntimeError("x")))
    w.start()
    try:
        assert wait_for(lambda: w.status == WATCHING)
        append(log, b"match_id=1\n")
        assert wait_for(lambda: ids(acc) == ["1"])
    finally:
        w.stop()
        w._thread.join(5)
