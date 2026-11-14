"""Global key combos by polling GetAsyncKeyState (no hooks, no threads). Used only while calibrating the click."""
from __future__ import annotations

from typing import Callable, Optional

from . import winapi

_MODS = {"Ctrl": (0x11,), "Alt": (0x12,), "Shift": (0x10,), "Win": (0x5B, 0x5C)}
_MOD_ORDER = ("Ctrl", "Alt", "Shift", "Win")

KEYS: dict[str, int] = {f"F{i}": 0x6F + i for i in range(1, 25)}
KEYS.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
KEYS.update({str(d): 0x30 + d for d in range(10)})
KEYS.update({f"Num{d}": 0x60 + d for d in range(10)})
KEYS.update({"Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
             "Space": 0x20, "Tab": 0x09})
_VK_ESC = 0x1B

DownFn = Callable[[int], bool]


def parse_combo(text: str) -> Optional[tuple[frozenset[str], int]]:
    """'Ctrl+F8' -> (frozenset({'Ctrl'}), vk). None when invalid (unknown key, no key, several keys)."""
    parts = [p.strip() for p in str(text).split("+") if p.strip()]
    mods = {p for p in parts if p in _MODS}
    keys = [p for p in parts if p not in _MODS]
    if len(keys) != 1 or keys[0] not in KEYS or len(mods) != len(parts) - 1:
        return None
    return frozenset(mods), KEYS[keys[0]]


def format_combo(mods: set[str] | frozenset[str], key: str) -> str:
    return "+".join([m for m in _MOD_ORDER if m in mods] + [key])


def normalize(text: str, default: str) -> str:
    parsed = parse_combo(text)
    if parsed is None:
        return default
    name = next(k for k, v in KEYS.items() if v == parsed[1])
    return format_combo(parsed[0], name)


def _mod_down(mod: str, down: DownFn) -> bool:
    return any(down(vk) for vk in _MODS[mod])


class HotkeyPoller:
    """poll() returns the names of bindings whose combo was just pressed (edge-triggered, exact modifiers)."""

    def __init__(self, bindings: dict[str, str], down: DownFn = winapi.key_down):
        self.down = down
        self.bindings = {n: p for n, c in bindings.items() if (p := parse_combo(c)) is not None}
        self._held: set[str] = set()

    def prime(self) -> None:
        """Treat combos that are held right now as already pressed (they must not fire on the first poll)."""
        self.poll()

    def poll(self) -> list[str]:
        fired: list[str] = []
        pressed_mods = {m for m in _MODS if _mod_down(m, self.down)}
        for name, (mods, vk) in self.bindings.items():
            active = pressed_mods == set(mods) and self.down(vk)
            if active and name not in self._held:
                fired.append(name)
            if active:
                self._held.add(name)
            else:
                self._held.discard(name)
        return fired


def record_step(down: DownFn = winapi.key_down) -> Optional[str]:
    """One polling step while the user presses a new combo. Returns the combo, '' if cancelled (Esc), else None."""
    if down(_VK_ESC):
        return ""
    mods = {m for m in _MODS if _mod_down(m, down)}
    for name, vk in KEYS.items():
        if down(vk):
            return format_combo(mods, name)
    return None
