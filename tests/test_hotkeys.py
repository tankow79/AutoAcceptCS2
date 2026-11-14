import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from acs import hotkeys  # noqa: E402
from acs.accept import pct_in_rect  # noqa: E402
from acs.settings import Settings  # noqa: E402


def fake_down(*vks):
    held = set(vks)
    return lambda vk: vk in held


def test_parse_and_normalize():
    assert hotkeys.parse_combo("Ctrl+F8") == (frozenset({"Ctrl"}), 0x77)
    assert hotkeys.parse_combo("F8+F9") is None
    assert hotkeys.parse_combo("Ctrl+") is None
    assert hotkeys.normalize("shift+ctrl+f8", "X") == "X"  # case-sensitive names: invalid -> default
    assert hotkeys.normalize("Shift+Ctrl+F8", "X") == "Ctrl+Shift+F8"


def test_poller_is_edge_triggered_and_exact_on_modifiers():
    state = {"keys": {0x11, 0x77}}  # Ctrl + F8
    p = hotkeys.HotkeyPoller({"capture": "Ctrl+F8"}, down=lambda vk: vk in state["keys"])
    assert p.poll() == ["capture"]
    assert p.poll() == []  # still held: no repeat
    state["keys"] = set()
    assert p.poll() == []
    state["keys"] = {0x11, 0x10, 0x77}  # Ctrl+Shift+F8 must not fire Ctrl+F8
    assert p.poll() == []
    state["keys"] = {0x11, 0x77}
    assert p.poll() == ["capture"]


def test_record_step():
    assert hotkeys.record_step(fake_down()) is None
    assert hotkeys.record_step(fake_down(0x1B)) == ""
    assert hotkeys.record_step(fake_down(0x11, 0x10, 0x79)) == "Ctrl+Shift+F10"


def test_pct_in_rect():
    rect = (100, 200, 1000, 500)
    assert pct_in_rect(600, 450, rect) == (50.0, 50.0)
    assert pct_in_rect(50, 450, rect) is None
    assert pct_in_rect(1100, 450, rect) is None


def test_settings_validate_hotkeys():
    s = Settings.from_dict({"hk_capture": "nonsense", "hk_test": "nonsense"})
    assert (s.hk_capture, s.hk_test) == ("Ctrl+F8", "Ctrl+F9")
    s = Settings.from_dict({"hk_capture": "Ctrl+F1", "hk_test": "Ctrl+F1"})  # duplicates reset
    assert s.hk_capture != s.hk_test
