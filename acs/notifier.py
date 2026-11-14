"""Telegram delivery on a background queue: never blocks the watcher, never raises, never spams the API.

Safety rules (to avoid duplicate messages and Telegram throttling/blocks):
* one message at a time, at least MIN_INTERVAL seconds apart (Telegram allows ~1 msg/s per chat);
* identical messages within DEDUPE_WINDOW seconds are dropped;
* a request is retried ONLY when we know it was not delivered (connect failure, HTTP 429/5xx);
  after a read timeout the outcome is unknown, so we do NOT retry (that would duplicate the message);
* HTTP 429 honours `retry_after`; 400/401/403/404 (bad token, chat not found, bot blocked) stop sending
  until the token/chat id are changed, so a broken config can't hammer the API;
* tokens never appear in logs or error text.
"""
from __future__ import annotations

import http.client
import json
import logging
import math
import queue
import re
import threading
import time
import urllib.parse
from typing import NamedTuple

from .settings import Store

log = logging.getLogger("acs.notify")

_KIND_FLAG = {"match": "tg_on_match", "ping": "tg_on_ping", "expired": "tg_on_expired"}
_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
HOST = "api.telegram.org"
MIN_INTERVAL = 1.2  # seconds between two sends
DEDUPE_WINDOW = 8.0  # seconds
MAX_ATTEMPTS = 3
BLOCK_SECONDS = 300.0  # a fatal error pauses sending this long (or until the settings change)
QUEUE_SIZE = 20


_gate = threading.Lock()  # one Telegram request at a time, shared by the worker and the "test" button
_gate_state = {"last": 0.0, "not_before": 0.0}  # monotonic times: last send, and "no requests before" after a 429
_ok_epoch = [0]  # bumped on every successful send: a working token clears any fatal latch
MAX_FLOOD_WAIT = 600.0  # never block longer than this, even if Telegram asks for more
MAX_INLINE_WAIT = 30.0  # longer flood-waits drop the message instead of blocking (a stale notification is useless)


class SendResult(NamedTuple):
    ok: bool
    info: str
    safe_retry: bool = False  # delivery definitely did not happen -> retrying cannot duplicate
    retry_after: float = 0.0
    fatal: bool = False  # configuration problem: stop until settings change


def _fit(text: str, limit: int = 4096) -> str:
    """Telegram counts UTF-16 code units: cut so that emoji-heavy text cannot exceed the limit."""
    text = text[:limit]  # a character is at least one unit
    while len(text.encode("utf-16-le")) // 2 > limit:
        text = text[:-1]
    return text


def send_once(token: str, chat_id: str, text: str, timeout: float = 10.0) -> SendResult:
    token, chat_id = (token or "").strip(), (chat_id or "").strip()
    if not token or not chat_id:
        return SendResult(False, "token or chat id is empty", fatal=True)
    if not _TOKEN_RE.match(token):
        return SendResult(False, "invalid token format", fatal=True)  # never send obviously wrong requests
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": _fit(text), "disable_web_page_preview": "true"})
    conn = http.client.HTTPSConnection(HOST, timeout=timeout)
    try:
        try:
            conn.connect()
        except OSError as e:  # DNS / refused / connect timeout: nothing was sent
            return SendResult(False, type(e).__name__, safe_retry=True)
        try:
            conn.request("POST", f"/bot{token}/sendMessage", body,
                         {"Content-Type": "application/x-www-form-urlencoded"})
            resp = conn.getresponse()
            raw = resp.read(65536)
        except Exception as e:  # request may or may not have reached Telegram: do not retry
            return SendResult(False, f"{type(e).__name__} (delivery unknown)")
        status = resp.status
    finally:
        try:
            conn.close()
        except Exception:
            pass
    try:
        data = json.loads(raw.decode("utf-8", "ignore"))
    except (ValueError, RecursionError):
        data = {}
    if not isinstance(data, dict):  # unexpected body (HTML error page, JSON list...)
        data = {}
    desc = str(data.get("description", "")).replace(token, "***")[:80]
    if 200 <= status < 300 and data.get("ok", True):
        return SendResult(True, f"HTTP {status}")
    if status == 429:
        params = data.get("parameters")
        try:
            wait = float(params.get("retry_after", 5) or 5) if isinstance(params, dict) else 5.0
        except (TypeError, ValueError, OverflowError):
            wait = 5.0
        if not math.isfinite(wait):
            wait = 5.0
        return SendResult(False, "HTTP 429 (rate limited)", safe_retry=True, retry_after=max(wait, 1.0))
    if status == 400 and "too long" in desc.lower():
        return SendResult(False, f"HTTP 400: {desc}")  # this one message is bad, the setup is fine
    if status in (400, 401, 403, 404):
        return SendResult(False, f"HTTP {status}: {desc}" if desc else f"HTTP {status}", fatal=True)
    if status >= 500:
        return SendResult(False, f"HTTP {status}", safe_retry=True)
    return SendResult(False, f"HTTP {status}: {desc}" if desc else f"HTTP {status}")


def send_gated(token: str, chat_id: str, text: str, stop: threading.Event | None = None) -> SendResult:
    """send_once() behind the shared gate: serialised, >= MIN_INTERVAL apart, honours Telegram's flood-wait."""
    with _gate:
        wait = max(_gate_state["last"] + MIN_INTERVAL, _gate_state["not_before"]) - time.monotonic()
        if wait > MAX_INLINE_WAIT:
            return SendResult(False, f"rate limited, retry in {int(wait)} s")
        if wait > 0 and (stop.wait(wait) if stop else (time.sleep(wait) or False)):
            return SendResult(False, "stopped")
        try:
            r = send_once(token, chat_id, text)
        except Exception as e:
            r = SendResult(False, type(e).__name__)
        now = time.monotonic()
        _gate_state["last"] = now
        if r.retry_after:
            _gate_state["not_before"] = now + min(r.retry_after, MAX_FLOOD_WAIT)
        return r


def post_message(token: str, chat_id: str, text: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Single gated attempt, for the 'Send test message' button."""
    r = send_gated(token, chat_id, text)
    if r.ok:
        _ok_epoch[0] += 1
    return r.ok, r.info


class Notifier:
    def __init__(self, store: Store):
        self.store = store
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._stop = threading.Event()
        self._recent: dict[str, float] = {}
        self._blocked_sig: tuple[str, str] | None = None
        self._blocked_at = 0.0
        self._blocked_epoch = 0
        self._thread = threading.Thread(target=self._worker, name="notifier", daemon=True)
        self._thread.start()

    def send(self, text: str, kind: str = "match") -> None:
        cfg = self.store.get()
        if not cfg.tg_enabled or not getattr(cfg, _KIND_FLAG.get(kind, "tg_on_match")):
            return
        try:
            self._q.put_nowait(text)
        except queue.Full:
            log.warning("notification queue full; dropped")

    def unblock(self) -> None:
        """Resume sending after a fatal error (e.g. the user fixed the bot on Telegram's side)."""
        self._blocked_sig = None

    def stop(self) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    # ----- worker -----
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._q.get()
                if text is None:
                    return
                self._deliver(text)
            except Exception:
                log.exception("notifier worker error")

    def _deliver(self, text: str) -> None:
        cfg = self.store.get()
        sig = (cfg.tg_token, cfg.tg_chat_id)
        if (self._blocked_sig == sig and self._blocked_epoch == _ok_epoch[0]
                and time.monotonic() - self._blocked_at < BLOCK_SECONDS):
            return  # config known to be broken: wait until the user fixes it
        self._blocked_sig = None
        now = time.monotonic()
        self._recent = {t: ts for t, ts in self._recent.items() if now - ts < DEDUPE_WINDOW}
        if text in self._recent:
            log.info("duplicate notification suppressed")
            return
        self._recent[text] = now
        for attempt in range(1, MAX_ATTEMPTS + 1):
            cfg = self.store.get()
            if not cfg.tg_enabled:  # switched off while the message was waiting in the queue
                return
            r = send_gated(cfg.tg_token, cfg.tg_chat_id, text, self._stop)
            if r.ok:
                self._recent[text] = time.monotonic()  # the window counts from delivery, not from the first try
                return
            if r.fatal:
                self._blocked_sig = (cfg.tg_token, cfg.tg_chat_id)
                self._blocked_at = time.monotonic()
                self._blocked_epoch = _ok_epoch[0]
                log.warning("telegram disabled until settings change: %s", r.info)
                return
            log.warning("telegram send failed: %s (attempt %d)", r.info, attempt)
            if not r.safe_retry:
                return  # outcome unknown: a retry could duplicate the message
            if attempt == MAX_ATTEMPTS or r.retry_after > MAX_INLINE_WAIT:
                return  # out of attempts, or Telegram asked for a long pause: drop this stale message
            if self._stop.wait(r.retry_after or 2.0 * attempt):
                return
