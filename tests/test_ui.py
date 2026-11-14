import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from acs.settings import Store  # noqa: E402
from acs.ui import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_dialog_roundtrip_saves_changes(qapp, tmp_path):
    store = Store(tmp_path / "c.json")
    store.load()
    dlg = SettingsDialog(store, lambda: {"state": "on", "accepted": 0, "last": None, "cs2": False, "log": "", "data": "x"})
    dlg.show()
    dlg._get["click_x_pct"].__self__.setValue(60.5)
    dlg._get["tg_chat_id"].__self__.setText("777")
    dlg._get["beep"].__self__.setChecked(True)
    dlg._save()
    cfg = Store(tmp_path / "c.json").load()
    assert (cfg.click_x_pct, cfg.tg_chat_id, cfg.beep) == (60.5, "777", True)


def test_listening_stops_on_save(qapp, tmp_path):
    store = Store(tmp_path / "c.json")
    store.load()
    dlg = SettingsDialog(store)
    dlg.show()
    dlg.nav.setCurrentRow(2)
    dlg._start_listen()
    assert dlg._poll_timer.isActive()
    dlg.nav.setCurrentRow(0)  # leaving the Click tab stops listening
    assert not dlg._poll_timer.isActive()
    dlg.nav.setCurrentRow(2)
    dlg._start_listen()
    dlg._save()
    assert not dlg._poll_timer.isActive()


def test_language_switch_is_live_and_persisted(qapp, tmp_path):
    from acs import i18n

    store = Store(tmp_path / "c.json")
    store.load()
    dlg = SettingsDialog(store)
    dlg.show()
    try:
        dlg._on_lang("uk")
        assert dlg.nav.item(1).text() == "Загальні"
        assert dlg.windowTitle().endswith("Налаштування")
        assert Store(tmp_path / "c.json").load().language == "uk"
        dlg._on_lang("en")
        assert dlg.nav.item(1).text() == "General"
    finally:
        i18n.set_language("en")
        dlg.close()
