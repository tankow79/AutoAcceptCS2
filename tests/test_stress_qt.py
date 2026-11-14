import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PyQt6")
from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDoubleSpinBox, QLineEdit, QPlainTextEdit, QSpinBox  # noqa: E402

from acs import app as app_mod  # noqa: E402
from acs import hotkeys, i18n, ui, watcher  # noqa: E402
from acs.settings import Settings, Store  # noqa: E402
from acs.widgets import Switch  # noqa: E402

STATUS = {"state": "on", "accepted": 3, "last": ("123", time.time()), "cs2": True, "log": r"C:\x\console.log", "data": r"C:\d"}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(ui.winapi, "dark_titlebar", lambda *_: None)
    monkeypatch.setattr(ui.sounds, "play", lambda *_: None)
    yield
    i18n.set_language("en")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "c.json")
    s.load()
    return s


def flush():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QApplication.processEvents()


def make_dialog(store, status=lambda: dict(STATUS)):
    d = ui.SettingsDialog(store, status)
    d.show()
    return d


# ---------------------------------------------------------------- dialog lifecycle

def test_open_close_50_times_no_leaks(qapp, store):
    before = len(qapp.topLevelWidgets())
    dialogs = []
    for _ in range(50):
        d = make_dialog(store)
        QApplication.processEvents()
        d.close()
        dialogs.append(d)
        flush()
    assert all(sip.isdeleted(d) for d in dialogs)
    assert len(qapp.topLevelWidgets()) <= before + 1


def test_cancel_button_does_not_save(qapp, store):
    d = make_dialog(store)
    d._get["tg_chat_id"].__self__.setText("changed")
    d.close()
    flush()
    assert Store(store.path).load().tg_chat_id == ""


def test_navigate_all_pages_and_refresh(qapp, store):
    d = make_dialog(store)
    try:
        for _ in range(3):
            for row in list(range(d.nav.count())) + [0, 4, 2, 1, 3, 0]:
                d.nav.setCurrentRow(row)
                QApplication.processEvents()
                assert d.stack.currentIndex() == row
        assert d._hero_title.text() == i18n.tr("hero.on")
        assert d._tile_vals["accepted"].text() == "3"
    finally:
        d.close()
        flush()


@pytest.mark.parametrize("state", ["on", "off", "nolog"])
def test_status_page_all_states(qapp, store, state):
    d = make_dialog(store, lambda: {**STATUS, "state": state, "last": None, "log": "", "cs2": False})
    try:
        d._refresh_status(force=True)
        assert d._hero_title.text() == i18n.tr(f"hero.{state}")
        assert d._tile_vals["last"].text() == i18n.tr("st.none")
        assert d._info_vals["log"].text() == i18n.tr("st.log_missing")
    finally:
        d.close()
        flush()


def test_status_fn_raising_is_survived(qapp, store):
    def bad():
        raise RuntimeError("x")

    d = make_dialog(store, bad)
    try:
        d._refresh_status(force=True)
        d.nav.setCurrentRow(1)
        d.nav.setCurrentRow(0)
    finally:
        d.close()
        flush()


def test_dialog_without_status_fn(qapp, store):
    d = ui.SettingsDialog(store)
    d.show()
    d._refresh_status(force=True)
    d.close()
    flush()


def test_switch_language_100_times(qapp, store):
    d = make_dialog(store)
    try:
        for i in range(100):
            code = "uk" if i % 2 == 0 else "en"
            d._on_lang(code)
            assert d.nav.item(1).text() == i18n.tr("nav.general")
            assert d.windowTitle() == i18n.tr("window.title")
        assert Store(store.path).load().language == "en"
        d._on_lang("uk")
        assert d.nav.item(1).text() == "Загальні"
        d.nav.setCurrentRow(2)
        d._on_lang("en")
        assert d._listen_btn.text() == i18n.tr("cal.start")
    finally:
        d.close()
        flush()


def test_flip_every_control_and_save_persists(qapp, store):
    d = make_dialog(store)
    widgets = {k: g.__self__ for k, g in d._get.items() if hasattr(g, "__self__")}
    expected = {}
    default = Settings()
    for k, w in widgets.items():
        if isinstance(w, Switch):
            expected[k] = not getattr(default, k)
            w.setChecked(expected[k])
        elif isinstance(w, QDoubleSpinBox):
            expected[k] = 12.5
            w.setValue(12.5)
        elif isinstance(w, QSpinBox):
            expected[k] = w.maximum()
            w.setValue(w.maximum())
        elif isinstance(w, QLineEdit):
            expected[k] = {"match_keyword": "kw_id", "log_path": r"C:\tmp\console.log"}.get(k, f"val-{k}")
            w.setText(expected[k])
        elif isinstance(w, QPlainTextEdit):
            expected[k] = "39734273, 76561198000000001"
            w.setPlainText(expected[k])
    d._hk_capture.set_combo("Ctrl+Shift+F5")
    d._hk_test.set_combo("Alt+F6")
    expected.update(hk_capture="Ctrl+Shift+F5", hk_test="Alt+F6")
    d._save()
    flush()
    cfg = Store(store.path).load()
    for k, v in expected.items():
        assert getattr(cfg, k) == v, k
    assert set(expected) == set(d._get)  # every control was exercised


def test_spin_ranges_match_settings_ranges(qapp, store):
    d = make_dialog(store)
    try:
        for key, (lo, hi) in ui.RANGES.items():
            w = d._get[key].__self__
            assert w.minimum() == lo and w.maximum() == hi
            w.setValue(hi + 1000)
            assert w.value() == hi
            w.setValue(lo - 1000)
            assert w.value() == lo
    finally:
        d.close()
        flush()


def test_save_roundtrip_of_untouched_dialog_keeps_defaults(qapp, store):
    d = make_dialog(store)
    d._save()
    flush()
    assert Store(store.path).load() == Settings()


# ---------------------------------------------------------------- listening

class FakeKeys:
    def __init__(self):
        self.down = set()

    def __call__(self, vk):
        return vk in self.down


@pytest.fixture
def fake_keys(monkeypatch):
    k = FakeKeys()
    real = hotkeys.HotkeyPoller
    monkeypatch.setattr(ui.hotkeys, "HotkeyPoller", lambda b: real(b, down=k))
    return k


def test_start_stop_listening_repeatedly(qapp, store, fake_keys):
    d = make_dialog(store)
    try:
        d.nav.setCurrentRow(2)
        for _ in range(100):
            d._start_listen()
            assert d._poll_timer.isActive() and d._poller is not None
            d._toggle_listen()
            assert not d._poll_timer.isActive() and d._poller is None
        d._start_listen()
        d._start_listen()  # double start must not stack timers
        d.nav.setCurrentRow(0)
        assert not d._poll_timer.isActive()
        d._stop_listen()  # stop while not listening
    finally:
        d.close()
        flush()


def test_listening_stops_on_close(qapp, store, fake_keys):
    d = make_dialog(store)
    d.nav.setCurrentRow(2)
    d._start_listen()
    timer = d._poll_timer
    d.close()
    flush()
    assert not timer.isActive() if not sip.isdeleted(timer) else True


def test_hotkeys_drive_capture_and_test(qapp, store, fake_keys, monkeypatch):
    monkeypatch.setattr(ui.accept, "capture_cursor_pct", lambda: (12.3, 45.6))
    clicks = []
    monkeypatch.setattr(ui.accept, "click_pct", lambda x, y: clicks.append((x, y)) or True)
    d = make_dialog(store)
    try:
        d.nav.setCurrentRow(2)
        d._start_listen()
        fake_keys.down = {0x11, hotkeys.KEYS["F8"]}
        d._poll()
        assert (d._x.value(), d._y.value()) == (12.3, 45.6)
        d._poll()  # held: no second capture
        fake_keys.down = {0x11, hotkeys.KEYS["F9"]}
        d._poll()
        assert clicks == [(12.3, 45.6)]
        fake_keys.down = set()
        d._poll()
    finally:
        d.close()
        flush()


def test_capture_failure_reports_without_exception(qapp, store, fake_keys, monkeypatch):
    monkeypatch.setattr(ui.accept, "capture_cursor_pct", lambda: None)
    monkeypatch.setattr(ui.accept, "click_pct", lambda x, y: False)
    d = make_dialog(store)
    try:
        d.nav.setCurrentRow(2)
        d._start_listen()
        d._do_capture()
        assert d._listen_msg[0] == "cal.fail"
        d._do_test()
        assert d._listen_msg[0] == "cal.test_fail"
    finally:
        d.close()
        flush()


def test_hotkey_conflict_is_reverted(qapp, store):
    d = make_dialog(store)
    try:
        d._hk_test.set_combo(d._hk_capture.combo())
        d._on_hotkey_changed(d._hk_test)
        assert d._hk_test.combo() == d._hk_test.prev
        assert d._listen_msg[0] == "hk.conflict"
    finally:
        d.close()
        flush()


# ---------------------------------------------------------------- telegram thread

def test_close_dialog_while_telegram_test_running(qapp, store, monkeypatch):
    done = threading.Event()
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda a: errors.append(a.exc_value))

    def slow_post(token, chat, text):
        time.sleep(0.3)
        done.set()
        return True, "HTTP 200"

    monkeypatch.setattr(ui, "post_message", slow_post)
    d = make_dialog(store)
    d._get["tg_token"].__self__.setText("123456:" + "A" * 30)
    d._get["tg_chat_id"].__self__.setText("1")
    d._test_telegram()
    assert d._tg_label.text() == i18n.tr("tg.sending")
    d.close()
    flush()
    assert done.wait(3)
    time.sleep(0.1)
    flush()
    assert errors == []



def test_telegram_result_updates_label(qapp, store, monkeypatch):
    monkeypatch.setattr(ui, "post_message", lambda *a: (False, "HTTP 401"))
    d = make_dialog(store)
    try:
        d._test_telegram()
        end = time.monotonic() + 3
        while d._tg_label.text() != i18n.tr("tg.fail", info="HTTP 401") and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        assert d._tg_label.text() == i18n.tr("tg.fail", info="HTTP 401")
        d._on_lang("uk")
        assert d._tg_label.text() == i18n.tr("tg.fail", info="HTTP 401")
    finally:
        d.close()
        flush()


# ---------------------------------------------------------------- tray

@pytest.fixture
def tray(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod.winapi, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(app_mod.winapi, "migrate_legacy_config", lambda p: None)
    monkeypatch.setattr(app_mod.winapi, "find_cs2_window", lambda: None)
    autostart = []
    monkeypatch.setattr(app_mod.winapi, "set_autostart", lambda v: autostart.append(v) or True)
    monkeypatch.setattr(app_mod.winapi, "legacy_instance_running", lambda: False)
    t = app_mod.TrayApp(qapp)
    t.autostart_calls = autostart
    yield t
    t.watcher.stop()
    t.notifier.stop()
    if t._dialog is not None and not sip.isdeleted(t._dialog):
        t._dialog.close()
    flush()


def test_tray_menu_actions_exist(tray):
    for name in ("act_enabled", "act_telegram", "act_settings", "act_autostart", "act_quit", "sec_control", "sec_app"):
        assert hasattr(tray, name), name
    texts = [a.text() for a in tray.menu.actions() if a.text()]
    assert i18n.tr("tray.auto") in texts and i18n.tr("tray.quit") in texts and i18n.tr("tray.settings") in texts


@pytest.mark.parametrize("state", ["on", "off", "nolog"])
def test_tray_refresh_all_states(tray, state):
    if state == "off":
        tray.store.update(enabled=False)
    else:
        tray.store.update(enabled=True)
        tray.watcher.status = watcher.WATCHING if state == "on" else watcher.NO_LOG
    tray.refresh()
    assert tray._state() == state
    assert i18n.tr("state." + state) in tray.tray.toolTip()
    assert not tray.tray.icon().isNull()
    assert i18n.tr("state." + state) in tray.header.status.text()


def test_tray_refresh_many_times(tray):
    for i in range(100):
        tray.watcher.status = watcher.WATCHING if i % 2 else watcher.NO_LOG
        tray.refresh()
    tray.status_changed.emit()
    QApplication.processEvents()


def test_tray_toggle_actions_persist(tray):
    tray.act_enabled.trigger()
    assert tray.store.get().enabled is False and Store(tray.store.path).load().enabled is False
    tray.act_enabled.trigger()
    assert tray.store.get().enabled is True
    tray.act_telegram.trigger()
    assert tray.store.get().tg_enabled is True
    tray.act_autostart.trigger()
    assert tray.store.get().autostart is True and tray.autostart_calls[-1] is True
    for _ in range(20):
        tray.act_enabled.trigger()
    assert tray._state() in ("on", "off", "nolog")


def test_tray_language_switch_retranslates_menu(tray):
    tray.store.update(language="en")
    en = [tray.act_enabled.text(), tray.act_settings.text(), tray.act_quit.text(), tray.sec_control.text()]
    tray.store.update(language="uk")
    uk = [tray.act_enabled.text(), tray.act_settings.text(), tray.act_quit.text(), tray.sec_control.text()]
    assert en != uk and all(a != b for a, b in zip(en, uk, strict=True))
    assert uk[0] == i18n.tr("tray.auto") and uk[3] == i18n.tr("tray.sec_control").upper()
    for i in range(50):
        tray.store.update(language="en" if i % 2 else "uk")
    assert tray.act_quit.text() == i18n.tr("tray.quit")


def test_tray_open_settings_twice_reuses_dialog_and_status_dict(tray, monkeypatch):
    monkeypatch.setattr(ui.winapi, "dark_titlebar", lambda *_: None)
    tray.open_settings()
    first = tray._dialog
    assert first is not None and first.isVisible()
    tray.open_settings()
    assert tray._dialog is first
    s = tray._status()
    assert set(s) == {"state", "accepted", "last", "cs2", "log", "data"} and s["cs2"] is False
    first.close()
    flush()
    assert tray._dialog is None


def test_tray_settings_change_from_dialog_updates_tray(tray, monkeypatch):
    monkeypatch.setattr(ui.winapi, "dark_titlebar", lambda *_: None)
    tray.open_settings()
    d = tray._dialog
    d._on_lang("uk")
    assert tray.act_quit.text() == i18n.tr("tray.quit")
    d._get["enabled"].__self__.setChecked(False)
    d._save()
    flush()
    assert tray._state() == "off"


def test_autostart_registry_is_written_only_when_the_setting_changes(tray):
    tray.autostart_calls.clear()
    tray.store.update(language="uk")
    tray.store.update(tg_enabled=True)
    assert tray.autostart_calls == []
    tray.store.update(autostart=True)
    assert tray.autostart_calls == [True]
