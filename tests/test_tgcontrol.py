"""Telegram control panel: owner-only, whitelisted settings, no backlog replay (no network: a fake API)."""
from __future__ import annotations

import json

import pytest

from acs import tgcontrol
from acs.settings import Store
from acs.tgcontrol import TgControl, TgError, apply, build

TOKEN = "123456789:" + "A" * 30
CHAT = "555"


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "c.json")
    s.load()
    s.update(tg_enabled=True, tg_control=True, tg_token=TOKEN, tg_chat_id=CHAT)
    return s


class Api:
    def __init__(self, batches):
        self.batches, self.calls = list(batches), []

    def __call__(self, token, method, params, timeout=0):
        self.calls.append((method, params))
        if method == "getUpdates":
            b = self.batches.pop(0) if self.batches else []
            if isinstance(b, Exception):
                raise b
            return b
        return {}


def upd(uid, data=None, text=None, chat=555, ctype="private"):
    msg = {"message_id": 7, "chat": {"id": chat, "type": ctype}}
    if data is not None:
        return {"update_id": uid, "callback_query": {"id": "q", "data": data, "message": msg}}
    return {"update_id": uid, "message": {**msg, "text": text}}


def test_every_button_callback_is_short_and_known(store):
    for menu in ("main", "notify"):
        _text, kb = build(store.get(), menu)
        for row in kb:
            for b in row:
                assert len(b["callback_data"].encode()) <= 64
                assert apply(store, b["callback_data"]) in ("main", "notify")


def test_toggles_numbers_and_language_change_settings(store):
    assert store.get().beep is False
    assert apply(store, "t:beep") == "main" and store.get().beep is True
    assert apply(store, "t:tg_on_ping") == "notify" and store.get().tg_on_ping is False
    assert apply(store, "l:toggle") == "main" and store.get().language in ("en", "uk")


def test_only_whitelisted_settings_can_be_changed(store):
    for bad in ("t:tg_token", "t:tg_chat_id", "t:log_path", "t:tg_control", "t:autostart", "t:log_auto", "n:click_x_pct:+s",
                "x:enabled", "t:", "garbage"):
        assert apply(store, bad) == ""
    assert store.get().tg_token == TOKEN and store.get().tg_control is True


def test_only_the_owners_private_chat_is_obeyed_and_strangers_get_no_answer(store):
    api = Api([[], [upd(1, "t:beep", chat=999), upd(2, "t:beep", ctype="group"), upd(3, text="/start", chat=999)]])
    c = TgControl(store, api)
    c.poll_once()  # skips the backlog
    c.poll_once()
    assert store.get().beep is False
    assert [m for m, _ in api.calls if m != "getUpdates"] == []


def test_owner_buttons_edit_the_menu_and_commands_open_it(store):
    api = Api([[upd(5)], [upd(6, "t:beep"), upd(7, text="/start")]])
    c = TgControl(store, api)
    c.poll_once()
    assert c._offset == 6  # the old update 5 is skipped, never replayed
    c.poll_once()
    assert store.get().beep is True
    methods = [m for m, _ in api.calls if m != "getUpdates"]
    assert methods == ["answerCallbackQuery", "editMessageText", "sendMessage"]
    assert json.dumps(api.calls[-1][1]["reply_markup"])  # a real keyboard


def test_it_is_idle_unless_switched_on_and_configured(store):
    api = Api([[upd(1, "t:beep")]])
    for off in ({"tg_control": False}, {"tg_enabled": False}, {"tg_token": "bad"}, {"tg_chat_id": ""}):
        store.update(**off)
        assert TgControl(store, api).poll_once() == 2.0
        store.update(tg_enabled=True, tg_control=True, tg_token=TOKEN, tg_chat_id=CHAT)
    assert api.calls == []


def test_a_conflict_or_bad_token_backs_off_and_never_raises(store):
    for code, wait in ((409, 30.0), (401, 30.0), (500, 8.0), (0, 8.0)):
        c = TgControl(store, Api([[], TgError(code)]))
        c.poll_once()
        assert c.poll_once() == wait


def test_the_token_never_appears_in_an_error(store):
    e = TgError(401, "Unauthorized")
    assert TOKEN not in str(e)


def test_the_menu_after_a_language_switch_is_already_in_the_new_language(store):
    from acs import i18n
    store.update(language="en")
    i18n.set_language("en")
    apply(store, "l:toggle")
    text, _kb = build(store.get(), "main")
    assert store.get().language == "uk" and "Натисни" in text
    i18n.set_language("en")


def test_a_new_token_starts_a_fresh_offset(store):
    api = Api([[upd(500)], [], [upd(3)]])
    c = TgControl(store, api)
    c.poll_once()
    assert c._offset == 501
    store.update(tg_token="987654321:" + "B" * 30)  # another bot: its update ids are unrelated
    c.poll_once()
    assert c._offset == 0  # backlog skipped again, not "501"


def test_a_429_waits_what_telegram_asks_and_http_errors_are_wrapped(store, monkeypatch):
    c = TgControl(store, Api([[], TgError(429, "Too Many Requests", 75.0)]))
    c.poll_once()
    assert c.poll_once() == 75.0
    c = TgControl(store, Api([[], TgError(429, "x", 0.0)]))
    c.poll_once()
    assert c.poll_once() == 8.0
    import http.client

    class Boom:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): raise http.client.IncompleteRead(b"")
        def close(self): pass

    monkeypatch.setattr(tgcontrol.http.client, "HTTPSConnection", Boom)
    with pytest.raises(TgError):
        tgcontrol.call(TOKEN, "getUpdates", {})


def test_toggle_is_atomic_and_only_for_booleans(store):
    import threading
    n0 = store.get().beep
    ts = [threading.Thread(target=store.toggle, args=("beep",)) for _ in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert store.get().beep == n0  # an even number of flips: none lost
    with pytest.raises(ValueError):
        store.toggle("tg_token")
