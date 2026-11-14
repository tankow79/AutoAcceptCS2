"""Validated, atomically-saved settings. A corrupt or partial file can never break startup."""
from __future__ import annotations

import json
import math
import logging
import os
import threading
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from . import hotkeys

log = logging.getLogger("acs.settings")

# key -> (min, max) for numeric fields
RANGES: dict[str, tuple[float, float]] = {
    "click_x_pct": (0, 100),
    "click_y_pct": (0, 100),
    "click_delay_ms": (0, 5000),
    "click_count": (1, 10),
    "click_interval_ms": (50, 3000),
    "cooldown_sec": (0, 120),
}


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    autostart: bool = False
    language: str = ""  # "" = follow Windows UI language until the user picks one

    log_auto: bool = True
    log_path: str = ""
    match_keyword: str = "match_id"

    focus_cs2: bool = True
    beep: bool = False
    click_x_pct: float = 50.0
    click_y_pct: float = 41.0
    click_delay_ms: int = 300
    click_count: int = 3
    click_interval_ms: int = 400
    cooldown_sec: int = 5
    hk_capture: str = "Ctrl+F8"
    hk_test: str = "Ctrl+F9"

    invite_enabled: bool = False  # auto-accept Steam game invites from the friends below
    invite_ok: str = ""  # JSON {"WxH": [x, y]}: where OK sits in CS2's box, as fractions of the window (calibration)
    invite_ids: str = ""  # whitelist: steamid64 / account id / profile URL, separated by commas or new lines

    tg_enabled: bool = False
    tg_token: str = ""
    tg_chat_id: str = ""
    tg_control: bool = False  # change settings with buttons in the bot chat (own bot only: one reader per bot)
    tg_on_match: bool = True
    tg_on_ping: bool = True
    tg_on_expired: bool = True

    @classmethod
    def from_dict(cls, data: Any, base: "Settings | None" = None) -> "Settings":
        """Build settings from untrusted data; bad values fall back to `base` (or defaults)."""
        base = base or cls()
        if not isinstance(data, dict):
            return base
        out: dict[str, Any] = {}
        for f in fields(cls):
            default = getattr(base, f.name)
            v = data.get(f.name, default)
            try:
                if isinstance(default, bool):
                    if not isinstance(v, bool):
                        raise ValueError
                elif isinstance(default, (int, float)):
                    if isinstance(v, bool):
                        raise ValueError
                    v = float(v)
                    if not math.isfinite(v):
                        raise ValueError
                    lo, hi = RANGES.get(f.name, (float("-inf"), float("inf")))
                    v = float(min(max(v, lo), hi))
                    v = int(round(v)) if isinstance(default, int) else v
                else:
                    if v is None or isinstance(v, (dict, list, tuple, set)):
                        raise ValueError
                    v = str(v).strip()
            except (ValueError, TypeError, OverflowError):
                v = default
            out[f.name] = v
        if out["language"] not in ("en", "uk"):
            out["language"] = base.language if base.language in ("en", "uk") else ""
        for k in ("hk_capture", "hk_test"):
            out[k] = hotkeys.normalize(out[k], getattr(base, k))
        if out["hk_capture"] == out["hk_test"]:  # a clash: keep what was valid before, else the defaults
            if base.hk_capture != base.hk_test:
                out["hk_capture"], out["hk_test"] = base.hk_capture, base.hk_test
            else:
                out["hk_capture"], out["hk_test"] = "Ctrl+F8", "Ctrl+F9"
        if not out["match_keyword"]:
            out["match_keyword"] = cls.match_keyword
        return cls(**out)


class Store:
    """Thread-safe holder; readers get immutable snapshots."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._cfg = Settings()
        self._listeners: list = []
        self.last_error: str | None = None  # set when the last save failed
        self._read_failed = False  # config exists but could not be read: never overwrite it

    def load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))  # tolerate a BOM (PowerShell/Notepad)
            cfg = Settings.from_dict(raw)
        except FileNotFoundError:
            cfg = Settings()
        except OSError as e:  # locked / unreadable right now: keep the file, don't overwrite it with defaults
            log.warning("config could not be read (%s); not saving until restart", type(e).__name__)
            cfg = Settings()
            self._read_failed = True
        except Exception as e:
            log.warning("config unreadable (%s); using defaults", type(e).__name__)
            try:
                os.replace(self.path, self.path.with_suffix(".bad"))
            except OSError:
                pass
            cfg = Settings()
        with self._lock:
            self._cfg = cfg
        return cfg

    def get(self) -> Settings:
        return self._cfg  # reference swap is atomic; the object is immutable

    def on_change(self, fn) -> None:
        self._listeners.append(fn)

    def toggle(self, key: str) -> Settings:
        """Flip a boolean setting atomically (a read-modify-write outside the lock could lose a concurrent change)."""
        with self._lock:
            cur = getattr(self._cfg, key)
            if not isinstance(cur, bool):
                raise ValueError(key)
            cfg = Settings.from_dict({**asdict(self._cfg), key: not cur}, base=self._cfg)
            self._cfg = cfg
            self._save(cfg)
        for fn in list(self._listeners):
            try:
                fn(cfg)
            except Exception:
                log.exception("settings listener failed")
        return cfg

    def update(self, **kw: Any) -> Settings:
        with self._lock:
            cfg = Settings.from_dict({**asdict(self._cfg), **kw}, base=self._cfg)
            self._cfg = cfg
            self._save(cfg)
        for fn in list(self._listeners):
            try:
                fn(cfg)
            except Exception:
                log.exception("settings listener failed")
        return cfg

    def _save(self, cfg: Settings) -> None:
        if self._read_failed:
            self.last_error = "ReadFailed"
            return
        try:
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")  # unique per process: two instances can't clobber
            try:
                tmp.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, self.path)
            except (OSError, ValueError):
                tmp.unlink(missing_ok=True)
                raise
            self.last_error = None
        except (OSError, ValueError) as e:  # ValueError: unencodable text (lone surrogate)
            self.last_error = type(e).__name__  # surfaced to callers; in-memory settings stay authoritative
            log.exception("could not save settings")

    @staticmethod
    def with_values(cfg: Settings, **kw: Any) -> Settings:
        return replace(cfg, **kw)
