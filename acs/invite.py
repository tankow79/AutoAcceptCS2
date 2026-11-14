"""Auto-accept Steam friend invites from whitelisted friends - by Steam ID, straight from Steam's own data.

Steam's UI runs in an embedded browser. With a `.cef-enable-remote-debugging` file in the Steam folder it listens on
127.0.0.1:8080, and we hook its NotificationStore: every friend invite to a game arrives there as a message whose
data holds the sender's Steam ID and the join link `steam://joinlobby/730/<lobby>/<steamid64>`. If the sender is on
the whitelist we run that link through Steam exactly like the "Play" button does.

If CS2 is running the join link is run at once; if it is not, CS2 is launched first, and the join link is run once
its window is up and the main menu has had time to load.

CS2 then asks "Join another game?" (and, if the lobby is gone, shows a one-button "Failed to join session" box).
Both boxes are confirmed by clicking OK at a fixed spot relative to the window (the game cannot be screenshotted, and
the Enter key is ignored by the box shortly after the game started)."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Callable, NamedTuple, Optional

from . import cdp, friends, winapi
from .i18n import tr
from .notifier import Notifier
from .settings import Store

log = logging.getLogger("acs.invite")

CS2_APP = "730"
_JOIN_URL = re.compile(rf"steam://joinlobby/{CS2_APP}/(\d{{1,20}})/(\d{{1,20}})")
LAUNCH_URL = f"steam://rungameid/{CS2_APP}"  # (steam://rungame/730 does nothing: verified on a real Steam)
STATUSES = ("ok", "off", "no_ids", "no_port", "port_busy", "no_ui", "error")

# Installed once per Steam UI session (guarded by a flag); afterwards it only hands over the queued invites.
HOOK_JS = r"""(() => {
  if (window.__acs_inv_v === 1) return window.__acs_inv.splice(0);
  if (typeof NotificationStore === 'undefined' || typeof NotificationStore.OnNewNotificationReceived !== 'function')
    throw new Error('steam ui not ready');  // right after a Steam restart: try again, do not mark as installed
  window.__acs_inv = [];
  const orig = NotificationStore.OnNewNotificationReceived;
  NotificationStore.OnNewNotificationReceived = function (n, ...rest) {
    try {
      const a = n && n.data && n.data.array;
      if (n && n.eType === 8 && Array.isArray(a) && a.some(x => typeof x === 'string' && x.startsWith('steam://joinlobby/')))
        window.__acs_inv.push({t: Date.now(), n: String(n.notificationID), etype: n.eType, arr: a.slice(0, 10).map(String)});
    } catch (e) {}
    return orig.call(this, n, ...rest);
  };
  window.__acs_inv_v = 1;  // last: only a fully installed hook counts
  return [];
})()"""


class Invite(NamedTuple):
    sender: int  # sender's account id (steamid64 - 76561197960265728)
    url: str  # steam://joinlobby/730/<lobby>/<steamid64>
    key: str


def parse_invite(ev) -> Optional[Invite]:
    """A CS2 lobby invite out of a captured notification; None for anything else (never raises)."""
    try:
        if not isinstance(ev, dict) or ev.get("etype") != 8 or not isinstance(ev.get("arr"), list):
            return None
        arr = [x for x in ev["arr"] if isinstance(x, str)]
        url = next((x for x in arr if x.startswith("steam://")), "")
        if not _JOIN_URL.fullmatch(url) or len(arr) < 2:
            return None
        ids = friends.parse_ids(arr[1]) if arr[1].isdigit() else []
        if not ids:
            return None
        return Invite(ids[0], url, f"{ev.get('n')}")
    except Exception:
        return None


def _cs2_running() -> bool:
    return winapi.find_cs2_window() is not None


def _ready_cs2_window() -> bool:
    """Bring CS2 to the front (it may be minimised or slow to open); True once it is in front with a real size."""
    hwnd = winapi.find_cs2_window()
    return bool(hwnd) and (winapi.is_foreground(hwnd) or winapi.focus(hwnd)) and winapi.client_rect(hwnd) is not None


# Where the OK button of CS2's boxes sits. Best source: the user's own click during calibration, saved per window size.
# Fallback for sizes never calibrated: a fit of two real measurements (1080x1080: OK at (690, 616); 2560x1440: (1532, 796)):
# "offset from the window centre = H*_DX_H + W*_DX_W", "y = H*_Y_H". The "Join another game?" OK and the
# "Failed to join" OK are ~12 px apart: one point in between hits both.
_DX_H, _DX_W, _Y_H = 0.0925, 0.0464, 0.56
CALIB_URL = f"steam://joinlobby/{CS2_APP}/109775244545640742/76561198000000000"  # no such lobby: only opens the box


def guess_ok_point(rect: tuple) -> tuple:
    """rect = (left, top, width, height) of the game window's client area."""
    left, top, w, h = rect
    return round(left + w / 2 + h * _DX_H + w * _DX_W), round(top + h * _Y_H)


def size_key(rect: tuple) -> str:
    return f"{rect[2]}x{rect[3]}"


def saved_ok(text: str, rect: tuple) -> Optional[tuple]:
    """The calibrated OK point for this window size, or None."""
    try:
        fx, fy = json.loads(text or "{}")[size_key(rect)]
        fx, fy = float(fx), float(fy)
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
            return None
        return min(round(rect[0] + fx * rect[2]), rect[0] + rect[2] - 1), min(round(rect[1] + fy * rect[3]), rect[1] + rect[3] - 1)
    except Exception:
        return None


def ok_point(rect: tuple, saved: str = "") -> tuple:
    return saved_ok(saved, rect) or guess_ok_point(rect)


class InviteMonitor:
    COOLDOWN = 20.0  # after a join, further invites are ignored for this long
    STALE = 60.0  # an invite older than this (seconds) is not acted on
    LAUNCH_TIMEOUT = 120.0  # how long to wait for the CS2 window after launching it
    SETTLE = 20.0  # main menu loading time after the window appears, before the join link is run again
    RETRY_GAP = 15.0  # a second repeat, in case the menu was still loading
    REJOINS = 1
    ARM_SEC = 25.0  # CS2's boxes count as ours only this long after we ran a join link
    READY_TIMEOUT = 15.0  # how long to wait for the game window to open and come to the front
    RENDER_DELAY = 1.5  # let the box finish opening (a game that was in the background renders late) before the click
    MAX_PRESSES = 2  # the "Join another game?" box, then (if the lobby is gone) the one-button "Failed to join" box

    def __init__(self, store: Store, notifier: Notifier, *, pages: Callable[[], list] = cdp.list_pages,
                 connect: Callable = cdp.Page, ensure_flag: Callable[[], bool] = winapi.ensure_steam_debug_flag,
                 cs2_running: Callable[[], bool] = _cs2_running,
                 running_app: Callable[[], int] = winapi.steam_running_app,
                 port_owner: Callable[[int], Optional[str]] = winapi.port_owner, now: Callable[[], float] = time.time,
                 interval: float = 0.5):
        self.store, self.notifier = store, notifier
        self._pages, self._connect, self._ensure_flag, self._now, self._interval = pages, connect, ensure_flag, now, interval
        self._cs2_running, self._running_app, self._port_owner = cs2_running, running_app, port_owner
        self._port_checked, self.port_name = -1e9, ""  # who holds the debug port when it is not Steam
        self._ready = _ready_cs2_window
        self._enter = self._click_ok
        self._calibrating = False
        self._join_gen = 0  # bumped by every accepted invite: an older launch-then-join thread gives up
        self._flag_lock = threading.Lock()  # _confirming / _pending_dialog are touched by the watcher and confirm threads
        self._warned: set = set()  # window sizes already reported as not calibrated
        self._armed_until = 0.0
        self._presses_left = 0
        self._confirming = False
        self._pending_dialog = False  # a second box was reported while the first was being answered
        self.status = "off"
        self._page = None
        self._lock = threading.Lock()  # the CDP connection is used by the poll thread and the rejoin thread
        self._flag_done = False
        self._last_join = -1e9
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="invites", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._drop()  # no lock: a poll stuck on a hung Steam must not block quitting

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception:
                log.exception("invite monitor error")
                self.status = "error"

    def _drop(self) -> None:
        page, self._page = self._page, None
        if page is not None:
            try:
                page.close()
            except Exception:
                pass

    def _port_busy(self) -> bool:
        """True when a program that is not Steam listens on the debug port (checked at most every 20 s)."""
        now = time.monotonic()
        if now - self._port_checked > 20.0:
            self._port_checked = now
            name = self._port_owner(cdp.PORT)
            self.port_name = name if name and not name.lower().startswith("steam") else ""
        return bool(self.port_name)

    def _ui_page(self) -> bool:
        """Make sure self._page is connected to Steam's UI context (sets status when it cannot)."""
        if self._stop.is_set():
            return False
        if self._page is not None:
            return True
        pages = self._pages()
        ui = next((p for p in pages if p.get("title") == "SharedJSContext" and p.get("webSocketDebuggerUrl")), None)
        if ui is None:
            self.status = "no_port" if not pages else "no_ui"
            if self._port_busy():  # something else holds 8080, so Steam cannot open its debug port there
                self.status = "port_busy"
            return False
        try:
            self._page = self._connect(ui["webSocketDebuggerUrl"])
        except Exception:
            self.status = "no_ui"
            return False
        log.info("connected to Steam's UI")
        return True

    def tick(self) -> None:
        """One poll: make sure Steam's UI is hooked, collect new invites, accept the whitelisted ones."""
        cfg = self.store.get()
        ids = friends.all_ids(cfg.invite_ids)
        with self._lock:
            if not cfg.invite_enabled or not ids:
                self.status = "off" if not cfg.invite_enabled else "no_ids"
                self._flag_done = False
                self._drop()
                return
            if not self._flag_done:
                try:
                    self._flag_done = bool(self._ensure_flag())  # retried until it works
                except Exception:
                    log.exception("could not prepare Steam's debug flag")
            if not self._ui_page():
                return
            try:
                events = self._page.evaluate(HOOK_JS)
            except Exception:
                self._drop()
                self.status = "no_ui"
                return
            self.status = "ok"
        for ev in events if isinstance(events, list) else []:
            try:
                self._handle(ev, ids)
            except Exception:
                log.exception("could not handle an invite event")

    def _exec(self, url: str, arm: bool = True) -> bool:
        """Run a steam:// link through Steam's UI (what the "Play"/"Join" buttons do)."""
        if arm and url != LAUNCH_URL:  # a join link makes CS2 ask "Join another game?": that box is then ours to answer
            self._armed_until = time.monotonic() + self.ARM_SEC
            self._presses_left, self._pending_dialog = self.MAX_PRESSES, False
        with self._lock:
            if self._ui_page():
                try:
                    self._page.evaluate("SteamClient.URL.ExecuteSteamURL(" + json.dumps(url) + ")")
                    return True
                except Exception:
                    log.exception("could not run the join link")
                    self._drop()
        self._armed_until = 0.0  # nothing was run, so no box is ours
        return False

    def _handle(self, ev: dict, ids: list[int]) -> None:
        inv = parse_invite(ev)
        if inv is None:
            return
        log.info("game invite received from account %s (listed: %s)", inv.sender, inv.sender in ids)
        if inv.sender not in ids:
            log.info("invite from a friend who is not on the list: ignored")
            return
        age = self._now() - float(ev.get("t", 0)) / 1000
        if abs(age) > self.STALE:
            log.info("stale invite ignored")
            return
        if self._now() - self._last_join < self.COOLDOWN:
            log.info("invite ignored (just joined)")
            return
        other = self._running_app()
        if other not in (0, int(CS2_APP)):  # another Steam game is being played: never pull the user out of it
            log.info("invite ignored: another game is running (app %s)", other)
            return
        running = self._cs2_running()
        if not self._exec(inv.url if running else LAUNCH_URL):  # game closed: start it first, join later
            return
        self._last_join = self._now()
        log.info("accepted a friend's invite (game %s)", "running" if running else "launching")
        self.notifier.send(tr("msg.invite_joined"), "match")
        self._join_gen += 1
        if not running:
            threading.Thread(target=self._join_after_launch, args=(inv.url, self._join_gen), name="join", daemon=True).start()

    def _join_after_launch(self, url: str, gen: int) -> None:
        """CS2 was closed and has just been launched: once its window is up (and the menu loaded), join the lobby.
        Gives up if a newer invite arrived meanwhile (it has its own link) or the feature was switched off."""
        def stale() -> bool:
            return gen != self._join_gen or not self.store.get().invite_enabled

        end = time.monotonic() + self.LAUNCH_TIMEOUT
        while not self._cs2_running():
            if self._stop.wait(0.5) or time.monotonic() > end or stale():
                return
        if self._stop.wait(self.SETTLE) or stale():
            return
        for i in range(self.REJOINS):
            if stale() or not self._exec(url):
                return
            log.info("join link run after the game started (%d)", i + 1)
            if self._stop.wait(self.RETRY_GAP):
                return

    def _click_ok(self) -> bool:
        """Click the OK button of CS2's box (Enter is not accepted by the box right after the game started)."""
        hwnd = winapi.find_cs2_window()
        rect = winapi.client_rect(hwnd) if hwnd else None
        if not rect:
            return False
        saved = self.store.get().invite_ok
        if saved_ok(saved, rect) is None and size_key(rect) not in self._warned:
            self._warned.add(size_key(rect))  # once per size: the guess may miss, so tell the user to calibrate
            log.info("window size %s is not calibrated", size_key(rect))
            self.notifier.send(tr("msg.calibrate_needed", size=size_key(rect)), "match")
        x, y = ok_point(rect, saved)
        for step in (-60, -30, 0):  # approach from the left: the game needs to see the hover first
            winapi.user32.SetCursorPos(x + step, y)
            time.sleep(0.08)
        time.sleep(0.4)
        return winapi.click(x, y, hwnd)

    # ---- calibration: learn where OK is from one real click of the user ----
    def calibrate(self, done: Callable[[str], None], wait: float = 60.0) -> None:
        """Opens CS2's "Join another game?" box (on a lobby that does not exist) and records where the user clicks OK.
        done(status): "saved" | "no_game" | "no_steam" | "timeout" | "outside"."""
        if self._calibrating:
            done("busy")
            return
        self._calibrating = True
        threading.Thread(target=self._calibrate, args=(done, wait), name="calibrate", daemon=True).start()

    def _calibrate(self, done: Callable[[str], None], wait: float) -> None:
        status = "timeout"
        try:
            hwnd = winapi.find_cs2_window()
            if not hwnd:
                status = "no_game"
                return
            winapi.focus(hwnd)
            if not self._exec(CALIB_URL, arm=False):
                status = "no_steam"
                return
            end = time.monotonic() + wait
            self._stop.wait(1.5)  # the box opens; the button the user may still hold from pressing Calibrate is let go
            while not self._stop.is_set() and winapi.left_button_down():
                self._stop.wait(0.05)
            while not self._stop.is_set() and time.monotonic() < end:
                if winapi.left_button_down():
                    x, y = winapi.cursor_pos()
                    rect = winapi.client_rect(hwnd)
                    if not rect or not (rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]):
                        status = "outside"
                        return
                    data = {}
                    try:
                        data = json.loads(self.store.get().invite_ok or "{}")
                        data = data if isinstance(data, dict) else {}
                    except Exception:
                        pass
                    data[size_key(rect)] = [round((x - rect[0]) / rect[2], 4), round((y - rect[1]) / rect[3], 4)]
                    self.store.update(invite_ok=json.dumps(data))
                    log.info("OK position saved for %s", size_key(rect))
                    status = "saved"
                    return
                self._stop.wait(0.02)
        except Exception:
            log.exception("calibration failed")
            status = "no_game"
        finally:
            self._calibrating = False
            try:
                done(status)
            except Exception:
                pass

    # ---- CS2's confirmation boxes ----
    def on_dialog(self) -> None:
        """Called when CS2's console.log reports a confirmation box. Only a box that follows our own join link is
        answered; any other confirmation the user opens is left alone. Never blocks the caller."""
        if (time.monotonic() > self._armed_until or self._presses_left <= 0
                or not self.store.get().invite_enabled):
            return
        with self._flag_lock:
            if self._confirming:
                self._pending_dialog = True  # answered right after the current box
                return
            self._confirming = True
        threading.Thread(target=self._confirm, name="confirm", daemon=True).start()

    def _confirm(self) -> None:
        try:
            end = time.monotonic() + self.READY_TIMEOUT
            while not self._ready():  # the game window may be minimised or slow to open
                if self._stop.wait(0.3) or time.monotonic() > end:
                    log.info("CS2 window did not come to the front: its box is left alone")
                    return
            if self._stop.wait(self.RENDER_DELAY):
                return
            self._presses_left -= 1
            ok = self._enter()
            log.info("clicked OK on CS2's box: %s", ok)
        except Exception:
            log.exception("could not answer CS2's confirmation")
        finally:
            with self._flag_lock:
                self._confirming = False
                again, self._pending_dialog = self._pending_dialog, False
            if again:
                self.on_dialog()
