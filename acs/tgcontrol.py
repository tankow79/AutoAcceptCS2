"""Control the app from the user's own Telegram bot: every setting as an inline button.

Telegram lets only ONE reader (getUpdates) per bot, so this works for a bot that belongs to a single user. A bot shared
by many people can still send notifications, but must not be switched on here (the switch says so).

Safety: only the configured chat (a private one) is obeyed, everybody else gets no answer at all; only whitelisted
setting names can be changed (never the bot token, chat id or paths); the pending backlog is skipped at start-up so an
old button press never replays; the bot token never reaches the log."""
from __future__ import annotations

import http.client
import json
import logging
import re
import threading
from typing import Any, Callable, Optional

from .i18n import set_language, tr
from .settings import Settings, Store

log = logging.getLogger("acs.tgcontrol")

HOST = "api.telegram.org"
_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
POLL_SEC = 20  # long-poll length of one getUpdates request

# toggles (callback "t:<key>") -> label key
TOGGLES = {
    "enabled": "ctl.enabled", "beep": "ctl.beep", "invite_enabled": "ctl.invites",
    "tg_on_match": "ctl.on_match", "tg_on_ping": "ctl.on_ping", "tg_on_expired": "ctl.on_expired",
}
MAIN_TOGGLES = ("enabled", "beep", "invite_enabled")
NOTIFY_TOGGLES = ("tg_on_match", "tg_on_ping", "tg_on_expired")
Keyboard = list


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _toggle_row(cfg: Settings, key: str) -> dict:
    return _btn(("✅ " if getattr(cfg, key) else "⬜ ") + tr(TOGGLES[key]), "t:" + key)


def build(cfg: Settings, menu: str = "main") -> tuple[str, Keyboard]:
    """(text, inline keyboard) of one menu page."""
    kb: Keyboard = []
    if menu == "notify":
        text = tr("ctl.notify_title")
        kb = [[_toggle_row(cfg, k)] for k in NOTIFY_TOGGLES] + [[_btn(tr("ctl.back"), "m:main")]]
    else:
        text = tr("ctl.title") + "\n" + tr("ctl.help")
        rows = [_toggle_row(cfg, k) for k in MAIN_TOGGLES]
        kb = [rows[i:i + 2] for i in range(0, len(rows), 2)]
        kb.append([_btn(tr("ctl.notify"), "m:notify")])
        kb.append([_btn("🌐 " + ("Українська" if cfg.language == "uk" else "English") + " ⇄", "l:toggle"),
                   _btn(tr("ctl.refresh"), "m:main")])
    return text, kb


def apply(store: Store, data: str) -> str:
    """Execute one callback; returns the menu to show next ("" = unknown callback)."""
    try:
        kind, _, rest = data.partition(":")
        cfg = store.get()
        if kind == "m" and rest in ("main", "notify"):
            return rest
        if kind == "t" and rest in TOGGLES:
            store.toggle(rest)
            return "notify" if rest in NOTIFY_TOGGLES else "main"
        if kind == "l" and rest == "toggle":
            store.update(language="en" if cfg.language == "uk" else "uk")
            set_language(store.get().language)  # the menu below is drawn at once, before the GUI thread applies it
            return "main"
    except Exception:
        log.exception("could not apply a Telegram button")
    return ""


class TgError(Exception):
    def __init__(self, code: int, desc: str = "", retry_after: float = 0.0):
        super().__init__(f"{code} {desc}")
        self.code = code
        self.retry_after = retry_after  # from a 429: how long Telegram wants us to stay away


def call(token: str, method: str, params: dict, timeout: float = POLL_SEC + 10) -> Any:
    """One Telegram Bot API call. Raises TgError; the token is never put in an error text."""
    conn = http.client.HTTPSConnection(HOST, timeout=timeout)
    try:
        body = json.dumps(params)
        conn.request("POST", f"/bot{token}/{method}", body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read(4_000_000)
        try:
            out = json.loads(raw)
        except ValueError:
            raise TgError(resp.status, "bad reply") from None
        if not isinstance(out, dict) or not out.get("ok"):
            if not isinstance(out, dict):
                raise TgError(resp.status)
            wait = 0.0
            try:
                wait = float((out.get("parameters") or {}).get("retry_after", 0) or 0)
            except (TypeError, ValueError, AttributeError, OverflowError):
                pass
            raise TgError(int(out.get("error_code", resp.status)), str(out.get("description", "")), wait)
        return out.get("result")
    except (OSError, http.client.HTTPException) as e:
        raise TgError(0, type(e).__name__) from None
    finally:
        conn.close()


class TgControl:
    def __init__(self, store: Store, api: Callable[..., Any] = call, wait: Callable[[float], bool] | None = None):
        self.store, self._api = store, api
        self._stop = threading.Event()
        self._wait = wait or self._stop.wait
        self._thread = threading.Thread(target=self._run, name="tgcontrol", daemon=True)
        self._offset: Optional[int] = None  # None: the backlog has not been skipped yet
        self._offset_token = ""  # the offset belongs to this bot: another token starts over
        self._warned = ""

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _creds(self) -> Optional[tuple[str, str]]:
        cfg = self.store.get()
        token, chat = cfg.tg_token.strip(), cfg.tg_chat_id.strip()
        if cfg.tg_enabled and cfg.tg_control and _TOKEN_RE.match(token) and chat.lstrip("-").isdigit():
            return token, chat
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            delay = 0.0
            try:
                delay = self.poll_once()
            except Exception:
                log.exception("telegram control error")
                delay = 10.0
            if delay and self._wait(delay):
                return

    def poll_once(self) -> float:
        """One round; returns how long to pause before the next one."""
        creds = self._creds()
        if creds is None:
            self._offset = None  # switched off: skip whatever piles up meanwhile
            return 2.0
        token, chat = creds
        if token != self._offset_token:  # a different bot has different update ids
            self._offset, self._offset_token = None, token
        try:
            if self._offset is None:  # first round: skip the backlog (never replay old presses)
                last = self._api(token, "getUpdates", {"offset": -1, "limit": 1, "timeout": 0}, 15)
                self._offset = (int(last[-1]["update_id"]) + 1) if last else 0
                return 0.0
            updates = self._api(token, "getUpdates", {"offset": self._offset, "timeout": POLL_SEC,
                                                       "allowed_updates": ["message", "callback_query"]})
        except TgError as e:
            note = "conflict" if e.code == 409 else "error"
            if self._warned != note:
                self._warned = note
                log.warning("telegram control: %s (code %s)%s", note, e.code,
                            ": another program reads this bot's updates" if e.code == 409 else "")
            if e.code == 429:
                return min(max(e.retry_after, 8.0), 600.0)
            return 30.0 if e.code in (401, 403, 404, 409) else 8.0
        self._warned = ""
        for u in updates if isinstance(updates, list) else []:
            if isinstance(u, dict) and isinstance(u.get("update_id"), int):
                self._offset = u["update_id"] + 1
                try:
                    self._handle(token, chat, u)
                except Exception:
                    log.exception("could not handle a Telegram update")
        return 0.0

    def _send_menu(self, token: str, chat: str, menu: str, message_id: Optional[int] = None) -> None:
        text, kb = build(self.store.get(), menu)
        params: dict = {"chat_id": chat, "text": text, "reply_markup": {"inline_keyboard": kb}}
        try:
            if message_id is None:
                self._api(token, "sendMessage", params, 15)
            else:
                params["message_id"] = message_id
                self._api(token, "editMessageText", params, 15)
        except TgError as e:
            if "not modified" not in str(e):  # pressing a button that changes nothing is fine
                raise

    def _handle(self, token: str, chat: str, u: dict) -> None:
        cb = u.get("callback_query")
        msg = (cb or {}).get("message") if cb else u.get("message")
        if not isinstance(msg, dict) or not isinstance(msg.get("chat"), dict):
            return
        if str(msg["chat"].get("id")) != chat or msg["chat"].get("type") != "private":
            return  # not our owner: no answer at all
        if cb:
            menu = apply(self.store, str(cb.get("data", "")))
            try:
                self._api(token, "answerCallbackQuery", {"callback_query_id": cb.get("id")}, 15)
            except TgError:
                pass
            if menu:
                self._send_menu(token, chat, menu, msg.get("message_id"))
            return
        if re.match(r"^/start(@\w+)?(\s|$)", str(msg.get("text") or "").strip()):  # the one and only command
            self._send_menu(token, chat, "main")
