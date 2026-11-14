"""What happens when a match is found: focus CS2 and click the Accept button inside its window."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from . import sounds, winapi
from .i18n import tr
from .notifier import Notifier
from .settings import Settings, Store

log = logging.getLogger("acs.accept")


def _in_rect(origin: int, size: int, pct: float) -> int:
    return origin + min(int(size * pct / 100), max(size - 1, 0))


def target_point(cfg: Settings, rect: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, w, h = rect
    return _in_rect(left, w, cfg.click_x_pct), _in_rect(top, h, cfg.click_y_pct)  # never one pixel past the edge


def pct_in_rect(px: int, py: int, rect: tuple[int, int, int, int]) -> Optional[tuple[float, float]]:
    """Screen point -> (x%, y%) of the client rect, or None if the point is outside it."""
    left, top, w, h = rect
    if not (left <= px < left + w and top <= py < top + h):
        return None
    return round((px - left) * 100 / w, 1), round((py - top) * 100 / h, 1)


def capture_cursor_pct() -> Optional[tuple[float, float]]:
    """Calibration: where the mouse is inside the CS2 window, in percent. None if CS2 is missing / cursor outside."""
    hwnd = winapi.find_cs2_window()
    rect = winapi.client_rect(hwnd) if hwnd else None
    return pct_in_rect(*winapi.cursor_pos(), rect) if rect else None


def click_pct(x_pct: float, y_pct: float) -> bool:
    """Calibration test click at a position given in percent of the CS2 window."""
    hwnd = winapi.find_cs2_window()
    rect = winapi.client_rect(hwnd) if hwnd else None
    if not rect or not (winapi.is_foreground(hwnd) or winapi.focus(hwnd)):
        return False
    left, top, w, h = rect
    return winapi.click(_in_rect(left, w, x_pct), _in_rect(top, h, y_pct), hwnd=hwnd)


class Accepter:
    def __init__(self, store: Store, notifier: Notifier):
        self.store = store
        self.notifier = notifier
        self._busy = threading.Lock()
        self._cancel = threading.Event()
        self._last_done = -1e9
        self.last_match: Optional[tuple[str, float]] = None  # (match id, wall-clock time)
        self.accepted_count = 0

    def cancel(self) -> None:
        """Stop an in-progress click sequence (e.g. the reservation expired) and re-arm for the next match."""
        self._cancel.set()
        self._last_done = -1e9

    def on_match(self, match_id: str) -> None:
        # each match gets its own cancel flag: a cancel() right after this call cannot be lost, and a new match
        # can never revive the still-winding-down sequence of the previous one
        wait = 1.0 if self._cancel.is_set() else 0.0  # only a cancelled predecessor is worth waiting for
        ev = threading.Event()
        self._cancel = ev
        threading.Thread(target=self._safe_accept, args=(match_id, ev, wait), name="accept", daemon=True).start()

    def _safe_accept(self, match_id: str, cancel: Optional[threading.Event] = None, wait: float = 0.0) -> None:
        if not self._busy.acquire(timeout=wait):  # a cancelled sequence winds down within one wait
            log.warning("match %s ignored: previous accept still running", match_id)
            return
        try:
            if self._accept(match_id, cancel or self._cancel):
                self._last_done = time.monotonic()
        except Exception:
            log.exception("accept failed")
        finally:
            self._busy.release()

    def _accept(self, match_id: str, cancel: Optional[threading.Event] = None) -> bool:
        """Returns True when at least one click was made (starts the cooldown)."""
        cancel = cancel or self._cancel
        cfg = self.store.get()
        if time.monotonic() - self._last_done < cfg.cooldown_sec:
            log.info("match %s ignored (cooldown)", match_id)
            return False
        log.info("match found: %s", match_id)
        self.last_match = (match_id, time.time())
        if cfg.beep:
            sounds.play("match")
        self.notifier.send(tr("msg.match"), "match")

        if cancel.wait(cfg.click_delay_ms / 1000):
            return False
        clicked = 0
        aborted = False
        for _ in range(cfg.click_count):
            cfg = self.store.get()
            if cancel.is_set() or not cfg.enabled:
                aborted = True  # expiry, or the user switched the app off: not a "window missing" case
                break
            ready = self._ready_point(cfg, cancel)
            if ready is None:
                if cancel.wait(cfg.click_interval_ms / 1000):
                    break
                continue  # window not ready yet (minimised / focus refused): try again
            try:
                if winapi.click(*ready[1], hwnd=ready[0]):
                    clicked += 1
            except Exception:
                log.exception("click failed")
                break
            if cancel.wait(cfg.click_interval_ms / 1000):
                break
        if clicked == 0 and not (aborted or cancel.is_set()):
            log.warning("no click: CS2 window not found or not in front")
            self.notifier.send(tr("msg.no_window"), "match")
        elif clicked:
            self.accepted_count += 1
            log.info("clicked %d time(s)", clicked)
        return clicked > 0 and not cancel.is_set()  # a cancelled sequence must not start the cooldown

    def _ready_point(self, cfg: Settings, cancel: threading.Event) -> Optional[tuple[int, tuple[int, int]]]:
        hwnd = winapi.find_cs2_window()
        if hwnd is None:
            return None
        if not winapi.is_foreground(hwnd):
            if cfg.focus_cs2:
                winapi.focus(hwnd)
                if cancel.wait(0.15):  # let the game redraw before the click
                    return None
            if not winapi.is_foreground(hwnd):  # never click into another app
                return None
        rect = winapi.client_rect(hwnd)
        return (hwnd, target_point(cfg, rect)) if rect else None
