import os
import sys
import time
import re
import json
import threading
import urllib.request
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil
import win32gui
import win32con
import win32api
import win32process
import winreg
import winsound
import ctypes

from win32event import CreateMutex
from win32api import GetLastError
from winerror import ERROR_ALREADY_EXISTS

import configparser
import pystray
from PIL import Image, ImageDraw


from click import click_center_three_times
from click import click_center_three_times_test

# =========================
# SINGLE INSTANCE
# =========================
def ensure_single_instance(app_id: str, tray_window_name: str = None):
    """
    Гарантує запуск лише однієї копії.
    app_id: унікальний рядок (напр. UUID).
    tray_window_name: назва прихованого вікна вашої програми (якщо є).
    """
    # 1. Створюємо системний Мутекс
    # Зберігаємо його в атрибуті функції, щоб Garbage Collector не видалив його
    ensure_single_instance.mutex = CreateMutex(None, False, f"Global\\{app_id}")

    if GetLastError() == ERROR_ALREADY_EXISTS:
        # 2. Якщо програма вже працює, намагаємось знайти її вікно
        if tray_window_name:
            hwnd = win32gui.FindWindow(None, tray_window_name)
            if hwnd:
                # Навіть якщо вікна немає, ми можемо відправити системне повідомлення
                # або просто вивести його з фону (якщо воно приховане)
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)

        # 3. Завершуємо роботу дубліката
        sys.exit(0)

# =========================
#   PATHS
# =========================
# Визначаємо шлях до папки, де лежить EXE (або скрипт)
if getattr(sys, 'frozen', False):
    # Якщо програма скомпільована (EXE)
    APP_DIR = os.path.dirname(sys.executable)
else:
    # Якщо програма запущена як звичайний .py скрипт
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Шлях до вашого конфігу поруч із EXE
CONFIG_PATH = os.path.join(APP_DIR, 'config.ini')


# =========================
# AUTOSTART (REGISTRY)
# =========================

def ensure_autostart():
    try:
        exe = str(Path(sys.executable))

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_ALL_ACCESS
        )

        try:
            current = winreg.QueryValueEx(key, "CS2Watcher")[0]
        except FileNotFoundError:
            current = None

        if current != exe:
            winreg.SetValueEx(key, "CS2Watcher", 0, winreg.REG_SZ, exe)

        winreg.CloseKey(key)

    except Exception:
        pass


# =========================
#   CONFIG
# =========================
@dataclass
class AppConfig:
    # Telegram
    tg_enabled: bool
    tg_token: str
    tg_chat_id: str

    # App behavior
    start_enabled: bool
    bring_cs2_to_front: bool
    beep_on_match: bool
    reservation_cooldown_sec: float
    y_cordinate: int

    # Log path
    log_auto: bool
    log_manual_path: str

    # Detect
    match_keyword: str  # e.g. "match_id" -> searches "match_id=123"

    # Console logging
    console_enabled_default: bool


def load_config(path: Path) -> AppConfig:
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")

    return AppConfig(
        tg_enabled=cp.getboolean("telegram", "enabled", fallback=False),
        tg_token=cp.get("telegram", "token", fallback="").strip(),
        tg_chat_id=cp.get("telegram", "chat_id", fallback="").strip(),

        start_enabled=cp.getboolean("app", "start_enabled", fallback=True),
        bring_cs2_to_front=cp.getboolean("app", "bring_cs2_to_front", fallback=True),
        beep_on_match=cp.getboolean("app", "beep_on_match", fallback=True),
        reservation_cooldown_sec=cp.getfloat("app", "reservation_cooldown_sec", fallback=5),
        y_cordinate=cp.getint("app", "y_cordinate", fallback=100),

        log_auto=cp.getboolean("log", "auto", fallback=True),
        log_manual_path=cp.get("log", "manual_path", fallback=""),

        match_keyword=cp.get("detect", "match_keyword", fallback="match_id"),

        console_enabled_default=cp.getboolean("console", "enabled_default", fallback=False),
    )


# =========================
#   RUNTIME CONSOLE TOGGLE
# =========================
kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
SW_HIDE = 0
SW_SHOW = 5

_console_lock = threading.Lock()
_console_allocated_by_app = False
_console_logging_enabled = False

# redirect targets (so prints don't crash when no console)
_devnull_out = open(os.devnull, "w")


def _has_console() -> bool:
    return bool(kernel32.GetConsoleWindow())


def enable_console_logging():
    """
    For --noconsole builds: AllocConsole() and bind stdout/stderr.
    For console runs: show console.
    """
    global _console_allocated_by_app, _console_logging_enabled
    with _console_lock:
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            # no console (e.g., windowed exe)
            if kernel32.AllocConsole():
                _console_allocated_by_app = True
                # bind stdout/stderr to the new console
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="ignore")
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)

        _console_logging_enabled = True


def disable_console_logging():
    """
    Hide console + stop logging to console (stdout->devnull).
    If we allocated console ourselves, FreeConsole().
    """
    global _console_allocated_by_app, _console_logging_enabled
    with _console_lock:
        _console_logging_enabled = False

        # redirect prints to nowhere (so no output)
        sys.stdout = _devnull_out
        sys.stderr = _devnull_out

        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)

        if _console_allocated_by_app:
            try:
                kernel32.FreeConsole()
            except Exception:
                pass
            _console_allocated_by_app = False


def log(msg: str):
    """Your app 'logging' — prints ONLY when console logging enabled."""
    if _console_logging_enabled:
        try:
            print(msg, flush=True)
        except Exception:
            pass


# =========================
#   TELEGRAM
# =========================
def tg_send(cfg: AppConfig, text: str, retries: int = 3) -> bool:
    if not cfg.tg_enabled:
        return False
    if not cfg.tg_token or not cfg.tg_chat_id:
        return False

    url = f"https://api.telegram.org/bot{cfg.tg_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": cfg.tg_chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = resp.read().decode("utf-8", errors="ignore")
                j = json.loads(payload) if payload else {}
                return bool(j.get("ok", False))

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            try:
                j = json.loads(body)
            except Exception:
                j = {}

            if e.code == 429:
                retry_after = int(j.get("parameters", {}).get("retry_after", 2))
                time.sleep(max(1, retry_after))
                continue

            time.sleep(0.6 * attempt)

        except Exception:
            time.sleep(0.6 * attempt)

    return False


# =========================
#   STEAM LOG PATH AUTODETECT
# =========================
def _steam_from_windows_path() -> Optional[Path]:
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip().strip('"')
        if not d:
            continue
        p = Path(d)
        if (p / "steam.exe").exists():
            return p
    return None


def _reg_get_str(root, subkey: str, value: str) -> Optional[str]:
    try:
        with winreg.OpenKey(root, subkey) as k:
            v, _ = winreg.QueryValueEx(k, value)
            return str(v)
    except Exception:
        return None


def _steam_from_registry() -> Optional[Path]:
    candidates = [
        _reg_get_str(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        _reg_get_str(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        _reg_get_str(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for c in candidates:
        if c:
            p = Path(c)
            if p.exists():
                return p
    return None


def _parse_libraryfolders(vdf_path: Path) -> list[Path]:
    if not vdf_path.exists():
        return []
    text = vdf_path.read_text(encoding="utf-8", errors="ignore")

    libs: list[Path] = []
    for m in re.finditer(r'"path"\s*"([^"]+)"', text, flags=re.IGNORECASE):
        raw = m.group(1).replace("\\\\", "\\")
        p = Path(raw)
        if p.exists():
            libs.append(p)

    seen = set()
    out = []
    for p in libs:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def find_cs2_console_log() -> Optional[Path]:
    steam_root = _steam_from_windows_path() or _steam_from_registry()
    if not steam_root:
        return None

    libs = [steam_root]
    libs += _parse_libraryfolders(steam_root / "steamapps" / "libraryfolders.vdf")

    rel = Path("steamapps/common/Counter-Strike Global Offensive/game/csgo/console.log")
    for lib in libs:
        candidate = lib / rel
        if candidate.exists():
            return candidate
    return None


# =========================
#   CS2 WINDOW FOCUS
# =========================
def _iter_windows():
    hwnds = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            hwnds.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return hwnds


def _get_process_name_from_hwnd(hwnd: int) -> Optional[str]:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0:
            return None
        return psutil.Process(pid).name().lower()
    except Exception:
        return None


def find_cs2_hwnd() -> Optional[int]:
    for hwnd in _iter_windows():
        title = (win32gui.GetWindowText(hwnd) or "").strip()
        if not title:
            continue

        proc = _get_process_name_from_hwnd(hwnd)
        if proc == "cs2.exe":
            return hwnd

        if "counter-strike" in title.lower():
            return hwnd

    return None


def activate_window(hwnd: int) -> bool:
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.BringWindowToTop(hwnd)

        fg = win32gui.GetForegroundWindow()
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
        target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        cur_tid = win32api.GetCurrentThreadId()

        win32process.AttachThreadInput(cur_tid, fg_tid, True)
        win32process.AttachThreadInput(cur_tid, target_tid, True)

        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetActiveWindow(hwnd)

        win32process.AttachThreadInput(cur_tid, target_tid, False)
        win32process.AttachThreadInput(cur_tid, fg_tid, False)
        return True
    except Exception:
        return False


def focus_cs2_if_needed(cfg: AppConfig):
    if not cfg.bring_cs2_to_front:
        return
    hwnd = find_cs2_hwnd()
    if hwnd:
        activate_window(hwnd)


# =========================
#   REGEX - WATCHER (only 3 outputs)
# =========================
RE_PRIMARY_PING = re.compile(r"Selecting\s+.*?\bas primary\b.*?Ping\s*=\s*(\d+)", re.IGNORECASE)
RE_RES_EXPIRED = re.compile(r"ReservationExpired", re.IGNORECASE)


@dataclass
class State:
    last_match_id: Optional[str] = None
    waiting_ping: bool = False
    expired_sent: bool = False
    expired_cooldown_until: float = 0.0


class CS2Watcher:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

        self.enabled = threading.Event()
        if cfg.start_enabled:
            self.enabled.set()

        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

        self.state = State()
        self.log_path: Optional[Path] = None

        kw = re.escape(cfg.match_keyword)
        self.re_match = re.compile(rf"\b{kw}=(\d+)\b", re.IGNORECASE)

    def start(self):
        self.thread.start()

    def set_enabled(self, value: bool):
        if value:
            self.enabled.set()
        else:
            self.enabled.clear()

    def is_enabled(self) -> bool:
        return self.enabled.is_set()

    def shutdown(self):
        self.stop.set()
        self.enabled.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def _resolve_log_path(self) -> Optional[Path]:
        if self.cfg.log_auto:
            return find_cs2_console_log()
        if self.cfg.log_manual_path:
            p = Path(self.cfg.log_manual_path)
            return p if p.exists() else None
        return None

    def _run(self):
        self.log_path = self._resolve_log_path()
        if not self.log_path:
            log("Не вдалося знайти console.log (auto/manual).")
            return

        path_str = str(self.log_path)
        log(f"[OK] Watching: {path_str}")

        while not os.path.exists(path_str) and not self.stop.is_set():
            time.sleep(1)

        with open(path_str, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(0, os.SEEK_END)
            last_size = os.path.getsize(path_str)

            while not self.stop.is_set():
                line = f.readline()
                if not line:
                    time.sleep(0.2)
                    try:
                        size = os.path.getsize(path_str)
                    except OSError:
                        size = last_size
                    if size < last_size:
                        f.seek(0, os.SEEK_SET)
                    last_size = size
                    continue

                if not self.enabled.is_set():
                    continue

                self._process_line(line.rstrip("\n"))

    def _process_line(self, line: str):
        st = self.state

        # 1) timeout — only once (cooldown)
        if RE_RES_EXPIRED.search(line):
            now = time.time()
            if (not st.expired_sent) and (now >= st.expired_cooldown_until):
                st.expired_sent = True
                st.expired_cooldown_until = now + float(self.cfg.reservation_cooldown_sec)
                st.waiting_ping = False

                msg = "⏱️ CS2: (схоже, матч не підтвердили/таймаут)."
                log(msg)
                tg_send(self.cfg, msg)
            return

        # 2) match found (keyword=digits)
        m = self.re_match.search(line)
        if m:
            match_id = m.group(1)
            if match_id != st.last_match_id:
                st.last_match_id = match_id
                st.waiting_ping = True
                st.expired_sent = False
                st.expired_cooldown_until = 0.0

                if self.cfg.beep_on_match:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)

                focus_cs2_if_needed(self.cfg)

                click_center_three_times(self.cfg.y_cordinate)

                msg = "🎮 CS2: знайдено"
                log(msg)
                tg_send(self.cfg, msg)
            return

        # 3) ping — first after match found
        if st.waiting_ping:
            mp = RE_PRIMARY_PING.search(line)
            if mp:
                ping = mp.group(1)
                msg = f"📍 Ping: {ping} ms"
                log(msg)
                tg_send(self.cfg, msg)
                st.waiting_ping = False


# =========================
#   TRAY UI
# =========================
def make_icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (24, 24, 24, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((10, 10, 54, 54), outline=(255, 255, 255, 255), width=3)
    d.text((22, 22), "CS", fill=(255, 255, 255, 255))
    return img


def open_app_folder(_icon=None, _item=None):
    os.startfile(str(APP_DIR))


def open_config(_icon=None, _item=None):
    os.startfile(str(CONFIG_PATH))


def main():
    # Вкажіть унікальний ID та назву вашої програми в треї
    ensure_single_instance(
        app_id="AutoAcceptCS2-6b7e0f2e-eba8-4007-b910-4bbf62a7f1c2",
        tray_window_name="AutoAcceptCS2"
    )

    ensure_autostart()

    cfg = load_config(CONFIG_PATH)

    # console logging default from config (after restart must return to default)
    if cfg.console_enabled_default:
        enable_console_logging()
    else:
        disable_console_logging()

    watcher = CS2Watcher(cfg)
    watcher.start()

    icon = pystray.Icon("CS2Watcher", make_icon_image(), "CS2 Watcher", None)

    def set_title():
        icon.title = f"CS2 Watcher [{'ON' if watcher.is_enabled() else 'OFF'}] | Console [{'ON' if _console_logging_enabled else 'OFF'}]"

    def enable_monitoring(_icon, _item):
        watcher.set_enabled(True)
        set_title()

    def disable_monitoring(_icon, _item):
        watcher.set_enabled(False)
        set_title()

    def enable_console(_icon, _item):
        enable_console_logging()
        set_title()
        log("[TRAY] Console logging enabled")

    def disable_console(_icon, _item):
        log("[TRAY] Console logging disabled")
        disable_console_logging()
        set_title()

    def test_click(_icon, _item):
        click_center_three_times_test(cfg.y_cordinate)

    def test_telegram(_icon, _item):
        ok = tg_send(cfg, "✅ CS2 Watcher: Telegram test")
        # в консоль тільки якщо вона увімкнена
        log(f"[TRAY] Telegram test: {'OK' if ok else 'FAILED (check token/chat_id/config)'}")

    def quit_app(_icon, _item):
        watcher.shutdown()
        icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem("Увімкнути моніторинг", enable_monitoring, enabled=lambda item: not watcher.is_enabled()),
        pystray.MenuItem("Вимкнути моніторинг", disable_monitoring, enabled=lambda item: watcher.is_enabled()),
        pystray.Menu.SEPARATOR,

        pystray.MenuItem("Увімкнути консоль/логування", enable_console, enabled=lambda item: not _console_logging_enabled),
        pystray.MenuItem("Вимкнути консоль/логування", disable_console, enabled=lambda item: _console_logging_enabled),

        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Тест клік", test_click),
        pystray.MenuItem("Тест Telegram", test_telegram),

        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Відкрити папку програми", open_app_folder),
        pystray.MenuItem("Відкрити config.ini", open_config),

        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Вийти", quit_app),
    )

    set_title()
    icon.run()

    time.sleep(0.05)


if __name__ == "__main__":
    main()