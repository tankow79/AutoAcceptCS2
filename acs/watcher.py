"""Tails CS2's console.log and reacts to match lines. Self-healing: any error restarts the loop."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import winapi
from .accept import Accepter
from .i18n import tr
from .notifier import Notifier
from .settings import Settings, Store

log = logging.getLogger("acs.watcher")

RE_PING = re.compile(r"Selecting\s+.*?\bas primary\b.*?Ping\s*=\s*(\d+)", re.IGNORECASE)
RE_EXPIRED = re.compile(r"ReservationExpired", re.IGNORECASE)
RE_STEAMID = re.compile(r"steamid:(\d+)")
MAX_LINE = 8192
MAX_PING_LINE = 1024  # real ping lines are ~150 chars; the lazy regex is quadratic on long hostile ones
DIALOG_MARKER = "GenericConfirmText_Label"
RECENT_MAX = 50  # remember the last match ids (a match lasts far longer than any time window)

WATCHING, NO_LOG = "watching", "nolog"
_MAX_PARTIAL = 1 << 20


def _identity(st: os.stat_result):
    """(volume, file index) of a file; None when the filesystem doesn't provide one."""
    return (st.st_dev, st.st_ino) if st.st_ino else None


def resolve_log(cfg: Settings) -> Optional[Path]:
    if cfg.log_auto:
        return winapi.find_cs2_log()
    p = Path(cfg.log_path) if cfg.log_path else None
    return p if p and p.is_file() else None


class Watcher:
    def __init__(self, store: Store, accepter: Accepter, notifier: Notifier,
                 on_status: Optional[Callable[[str], None]] = None, on_dialog: Optional[Callable[[], None]] = None):
        self.store, self.accepter, self.notifier = store, accepter, notifier
        self.on_status = on_status
        self.on_dialog = on_dialog  # CS2 opened a generic confirmation box (e.g. "Join another game?")
        self.status = NO_LOG
        self.log_path: Optional[Path] = None
        self._pos: Optional[tuple[Path, object, int]] = None  # (path, file identity, resume offset)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._supervise, name="watcher", daemon=True)
        self._kw = ""
        self._re_match = re.compile("$^")
        self._recent: dict[str, float] = {}  # match id -> monotonic time first seen
        self._server_id: Optional[str] = None  # steamid of the current match server (from the log)
        self._waiting_ping = False
        self._expired_notified = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _set_status(self, s: str) -> None:
        if s != self.status:
            self.status = s
            if self.on_status:
                try:
                    self.on_status(s)
                except Exception:
                    log.exception("status callback failed")

    def _supervise(self) -> None:
        while not self._stop.is_set():
            try:
                self._tail()
            except Exception:
                log.exception("watcher loop crashed; restarting")
                self._set_status(NO_LOG)
                self._stop.wait(3)

    def _tail(self) -> None:
        path = resolve_log(self.store.get())
        self.log_path = path
        if path is None:
            self._set_status(NO_LOG)
            self._stop.wait(3)
            return
        with winapi.open_shared(path) as f:
            st = os.fstat(f.fileno())
            ident = _identity(st)
            f.seek(self._start_offset(path, ident, st.st_size))
            buf = b""
            try:
                self._set_status(WATCHING)
                log.info("watching %s", path)
                last_check = time.monotonic()
                while not self._stop.is_set():
                    if os.fstat(f.fileno()).st_size < f.tell():  # truncated / rewritten in place
                        f.seek(0)
                        buf = b""
                    chunk = f.read(65536)
                    if chunk:
                        buf += chunk
                        *lines, buf = buf.split(b"\n")
                        if len(buf) > _MAX_PARTIAL:
                            buf = b""
                        for raw in lines:
                            try:
                                self.handle_line(raw.decode("utf-8", "ignore"))
                            except Exception:
                                log.exception("line handling failed")
                        continue
                    self._stop.wait(0.1)
                    try:
                        if _identity(os.stat(path)) != ident:
                            return  # replaced by a new file: reopen it from the start
                    except OSError:
                        return  # file vanished: re-resolve
                    now = time.monotonic()
                    if now - last_check >= 2:
                        last_check = now
                        if resolve_log(self.store.get()) != path:
                            return  # settings changed
            finally:
                self._pos = (path, ident, f.tell() - len(buf))

    def _start_offset(self, path: Path, ident, size: int) -> int:
        """Where to start reading: new content only, except for a file we already follow or a replacement of it."""
        if self._pos is not None and self._pos[0] == path:
            if self._pos[1] != ident:
                return 0  # the file was replaced (e.g. CS2 re-created console.log): read it all
            if self._pos[2] <= size:
                return self._pos[2]  # same file: continue exactly where we stopped
            return 0  # truncated while we were away
        return size  # first time we see this file: never replay old history

    def _matcher(self, keyword: str) -> "re.Pattern[str]":
        if keyword != self._kw:
            self._kw = keyword
            self._re_match = re.compile(rf"\b{re.escape(keyword)}=(\d+)\b", re.IGNORECASE)
        return self._re_match

    def _is_stale(self, line: str) -> bool:
        """True if the line mentions a different server than the current match (event of an older connection)."""
        sid = RE_STEAMID.search(line)
        return bool(sid and self._server_id and sid.group(1) != self._server_id)

    def handle_line(self, line: str) -> None:
        cfg = self.store.get()
        if DIALOG_MARKER in line:
            if self.on_dialog is not None:
                self.on_dialog()
            return  # (also with auto-accept off: the invite feature has its own switch)
        if not cfg.enabled:
            return
        if RE_EXPIRED.search(line):
            if self._is_stale(line):
                return
            self.accepter.cancel()
            self._waiting_ping = False
            if not self._expired_notified:  # CS2 logs the expiry on two lines: report it once per match
                self._expired_notified = True
                self.notifier.send(tr("msg.expired"), "expired")
            return
        m = self._matcher(cfg.match_keyword).search(line)
        if m:
            mid, now = m.group(1), time.monotonic()
            if mid in self._recent:
                return  # same match reported again (log replay, reconnect): never act twice
            self._recent[mid] = now
            while len(self._recent) > RECENT_MAX:
                self._recent.pop(next(iter(self._recent)))
            sid = RE_STEAMID.search(line)
            self._server_id = sid.group(1) if sid else None
            self._waiting_ping = True
            self._expired_notified = False
            self.accepter.on_match(mid)
            return
        if self._waiting_ping and len(line) <= MAX_PING_LINE and not self._is_stale(line):  # ping regex is slow on huge lines
            p = RE_PING.search(line)
            if p:
                self._waiting_ping = False
                self.notifier.send(tr("msg.ping", ping=p.group(1)), "ping")
