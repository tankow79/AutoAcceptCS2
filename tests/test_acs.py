import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from acs import notifier
from acs.accept import target_point
from acs.settings import Settings, Store
from acs.watcher import WATCHING, Watcher


def make_store(tmp_path: Path, **kw) -> Store:
    s = Store(tmp_path / "config.json")
    s.load()
    if kw:
        s.update(**kw)
    return s


def test_corrupt_config_falls_back_and_is_backed_up(tmp_path):
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    s = make_store(tmp_path)
    assert s.get() == Settings()
    assert (tmp_path / "config.bad").exists()


def test_bad_values_are_clamped_or_defaulted(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"click_count": 999, "click_x_pct": "abc", "enabled": "yes", "match_keyword": ""}), encoding="utf-8")
    c = make_store(tmp_path).get()
    assert c.click_count == 10
    assert c.click_x_pct == 50.0
    assert c.enabled is True
    assert c.match_keyword == "match_id"


def test_update_persists(tmp_path):
    make_store(tmp_path, tg_chat_id="42", click_y_pct=33.5)
    again = make_store(tmp_path).get()
    assert again.tg_chat_id == "42" and again.click_y_pct == 33.5


def test_target_point():
    assert target_point(Settings(click_x_pct=50, click_y_pct=25), (100, 200, 1000, 800)) == (600, 400)


def make_watcher(store):
    acc, ntf = MagicMock(), MagicMock()
    return Watcher(store, acc, ntf), acc, ntf


def test_match_ping_expired_flow(tmp_path):
    w, acc, ntf = make_watcher(make_store(tmp_path))
    w.handle_line("xx match_id=123 yy")
    w.handle_line("again match_id=123")  # duplicate id is ignored
    acc.on_match.assert_called_once_with("123")
    w.handle_line("Selecting dc1 as primary ... Ping = 37")
    ntf.send.assert_called_with("📍 Ping: 37 ms", "ping")
    w.handle_line("ReservationExpired")
    acc.cancel.assert_called_once()


def test_disabled_ignores_lines(tmp_path):
    w, acc, _ = make_watcher(make_store(tmp_path, enabled=False))
    w.handle_line("match_id=5")
    acc.on_match.assert_not_called()


def test_custom_keyword(tmp_path):
    w, acc, _ = make_watcher(make_store(tmp_path, match_keyword="mm_id"))
    w.handle_line("match_id=1")
    w.handle_line("mm_id=2")
    acc.on_match.assert_called_once_with("2")


def test_tail_reads_new_lines_and_survives_rotation(tmp_path):
    log = tmp_path / "console.log"
    log.write_text("old match_id=1\n", encoding="utf-8")
    w, acc, _ = make_watcher(make_store(tmp_path, log_auto=False, log_path=str(log)))
    w.start()
    try:
        deadline = time.time() + 5
        while w.status != WATCHING and time.time() < deadline:
            time.sleep(0.05)
        assert w.status == WATCHING
        with open(log, "ab") as f:
            f.write(b"match_id=2\nmatch_")
        with open(log, "ab") as f:
            f.write(b"id=3\n")  # line split across writes
        while acc.on_match.call_count < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert [c.args[0] for c in acc.on_match.call_args_list] == ["2", "3"]
        log.write_text("match_id=4\n", encoding="utf-8")  # truncated/rotated
        deadline = time.time() + 6
        while acc.on_match.call_count < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert acc.on_match.call_args_list[-1].args[0] == "4"
    finally:
        w.stop()


def test_telegram_errors_never_leak_token(monkeypatch, caplog):
    token = "123456:" + "A" * 30

    class BoomConn:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            raise OSError(f"https://api.telegram.org/bot{token}/sendMessage failed")

        def close(self):
            pass

    monkeypatch.setattr(notifier.http.client, "HTTPSConnection", BoomConn)
    with caplog.at_level("DEBUG"):
        ok, info = notifier.post_message(token, "1", "hi")
    assert not ok and token not in info
    assert token not in caplog.text
