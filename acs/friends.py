"""Steam ids -> friend names, read from Steam's own local cache (userdata/<id>/config/localconfig.vdf).

The whitelist is kept as Steam ids (id64, account id or profile URL); the names a friend is shown with in
Steam windows (persona name + name history) are looked up here, so the user never types a name."""
from __future__ import annotations

import http.client
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

from . import winapi

log = logging.getLogger("acs.friends")

ID64_BASE = 76561197960265728  # steamid64 = this + account id
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')
_UNESC = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def parse_ids(text: str) -> list[int]:
    """Account ids from free text: steamid64, account id, or .../profiles/<id64> URLs. Junk and vanity URLs are ignored."""
    out: list[int] = []
    for tok in re.split(r"[\s,;]+", text or ""):
        m = re.search(r"/profiles/(\d{5,20})", tok) or re.fullmatch(r"(\d{5,20})", tok)
        if not m:
            continue
        n = int(m.group(1))
        acc = n - ID64_BASE if ID64_BASE <= n < ID64_BASE + 2**32 else n if 0 < n < 2**32 else 0
        if acc and acc not in out:
            out.append(acc)
    return out


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _UNESC.get(m.group(1), m.group(1)), s)


def parse_vdf(text: str) -> dict:
    """Minimal Valve KeyValues parser: nested {} blocks with quoted keys/values (enough for localconfig.vdf)."""
    root: dict = {}
    stack = [root]
    key: Optional[str] = None
    for m in _TOKEN.finditer(text):
        if m.group(2) == "{":
            child: dict = {}
            if key is not None:
                stack[-1][key.lower()] = child
            stack.append(child)
            key = None
        elif m.group(2) == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = _unescape(m.group(1))
            if key is None:
                key = s
            else:
                stack[-1][key.lower()] = s
                key = None
    return root


def find_localconfig() -> Optional[Path]:
    """The most recently written localconfig.vdf of any Steam account on this PC."""
    best: Optional[tuple[float, Path]] = None
    for root in winapi.steam_roots():
        for p in (root / "userdata").glob("*/config/localconfig.vdf"):
            try:
                t = p.stat().st_mtime
            except OSError:
                continue
            if best is None or t > best[0]:
                best = (t, p)
    return best[1] if best else None


_cache: dict[tuple, dict] = {}


def _friends_block(path: Path) -> dict:
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key not in _cache:
            _cache.clear()
            root = parse_vdf(path.read_text(encoding="utf-8", errors="ignore"))
            top = next(iter(root.values()), {}) if len(root) == 1 else root
            friends = top.get("friends", {}) if isinstance(top, dict) else {}
            _cache[key] = friends if isinstance(friends, dict) else {}
        return _cache[key]
    except OSError:
        return {}


def friend_names(ids: list[int], path: Optional[Path] = None) -> list[str]:
    """Every name Steam shows for these friends (current persona name first, then the name history)."""
    path = path or find_localconfig()
    if path is None:
        return []
    block = _friends_block(path)
    names: list[str] = []
    for acc in ids:
        entry = block.get(str(acc))
        if not isinstance(entry, dict):
            continue
        cand = [entry.get("name")] + list((entry.get("namehistory") or {}).values() if isinstance(entry.get("namehistory"), dict) else [])
        for n in cand:
            if isinstance(n, str) and n.strip() and n not in names:
                names.append(n)
    return names


# ---- vanity profile links: steamcommunity.com/id/<name> has no number in it, so it is looked up once online ----
_VANITY_LINK = re.compile(r"steamcommunity\.com/id/([A-Za-z0-9_-]{2,32})(?![A-Za-z0-9_-])")
_VANITY_NAME = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
RETRY_AFTER = 120.0  # seconds before a failed lookup is tried again
_vanity: dict[str, int] = {}  # name (lower case) -> account id
_failed: dict[str, float] = {}  # name -> monotonic time of the last failed lookup
_pending: set[str] = set()
_vlock = threading.Lock()
_lookups = threading.Semaphore(2)  # a pasted list of links must not fire a burst of requests


def vanity_names(text: str) -> list[str]:
    """Profile names out of .../id/<name> links (in order, without duplicates)."""
    out: list[str] = []
    for m in _VANITY_LINK.finditer(text or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def fetch_vanity(name: str) -> Optional[int]:
    """steamid64 of a vanity profile name, from Steam's public profile page (None if unknown)."""
    if not _VANITY_NAME.match(name or ""):
        return None
    conn = http.client.HTTPSConnection("steamcommunity.com", timeout=8)
    try:
        conn.request("GET", f"/id/{name}/?xml=1", headers={"User-Agent": "AutoAcceptCS2"})
        resp = conn.getresponse()
        body = resp.read(200_000).decode("utf-8", "ignore")
    finally:
        conn.close()
    m = re.search(r"<steamID64>(\d{15,20})</steamID64>", body)
    return int(m.group(1)) if m else None


def _lookup(name: str) -> None:
    try:
        with _lookups:
            sid = fetch_vanity(name)
    except Exception:
        sid = None
    with _vlock:
        key = name.lower()
        _pending.discard(key)
        ids = parse_ids(str(sid)) if sid else []
        if ids:
            _vanity[key] = ids[0]
            _failed.pop(key, None)
        else:
            _failed[key] = time.monotonic()


def reset_vanity_cache() -> None:
    with _vlock:
        _vanity.clear()
        _failed.clear()
        _pending.clear()


def all_ids(text: str, lookup: bool = True) -> list[int]:
    """parse_ids() plus the vanity links that have been resolved so far; unresolved ones are looked up in the
    background (never blocks) and picked up by a later call - unless `lookup` is False (cache only)."""
    ids = parse_ids(text)
    for name in vanity_names(text):
        key = name.lower()
        with _vlock:
            acc = _vanity.get(key)
            due = lookup and key not in _pending and time.monotonic() - _failed.get(key, -1e9) >= RETRY_AFTER
            if acc is None and due:
                _pending.add(key)
        if acc is not None:
            if acc not in ids:
                ids.append(acc)
        elif due:
            threading.Thread(target=_lookup, args=(name,), name="vanity", daemon=True).start()
    return ids


def unresolved(text: str) -> list[str]:
    """Vanity names in the text that have no account id (yet)."""
    with _vlock:
        return [n for n in vanity_names(text) if n.lower() not in _vanity]
