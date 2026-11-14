"""Thin ctypes wrappers around the Win32 calls the app needs (no pywin32/psutil/pyautogui)."""
from __future__ import annotations

import ctypes
import functools
import msvcrt
import os
import subprocess
import sys
import time
import winreg
from ctypes import wintypes as wt
from pathlib import Path
from typing import BinaryIO, Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [_WNDENUMPROC, wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetForegroundWindow.restype = wt.HWND
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE

_SW_RESTORE = 9
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_ERROR_ALREADY_EXISTS = 183
CS2_TITLE = "Counter-Strike 2"

_mutex_handle = None  # kept alive for the lifetime of the process


def dark_titlebar(hwnd: int) -> None:
    """Ask Windows 10/11 for a dark title bar (immersive dark mode)."""
    try:
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def single_instance(name: str = "AutoAcceptCS2-6b7e0f2e") -> bool:
    """Return True if this is the only running instance."""
    global _mutex_handle
    _mutex_handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
    return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS


_OLD_MUTEX = r"Global\AutoAcceptCS2-6b7e0f2e-eba8-4007-b910-4bbf62a7f1c2"  # mutex of the legacy CS2Watcher app


def legacy_instance_running() -> bool:
    """True if the old CS2Watcher build is running (it would send duplicate notifications)."""
    try:
        kernel32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
        kernel32.OpenMutexW.restype = wt.HANDLE
        h = kernel32.OpenMutexW(0x00100000, False, _OLD_MUTEX)  # SYNCHRONIZE
        if h:
            kernel32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def _process_name(hwnd: int) -> str:
    """Executable file name of the window's process ('' if it can't be queried)."""
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wt.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def find_cs2_window() -> Optional[int]:
    """The CS2 game window: title starts with 'Counter-Strike 2' AND it belongs to cs2.exe (a browser tab with a
    similar title must never match). If the process can't be queried, only an exact title is accepted."""
    found: list[int] = []

    def cb(hwnd, _lparam):
        try:
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n >= len(CS2_TITLE):
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value.startswith(CS2_TITLE):
                        proc = _process_name(hwnd)
                        if proc == "cs2.exe" or (not proc and buf.value == CS2_TITLE):
                            found.append(hwnd)
                            return False
        except Exception:
            pass
        return True

    user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return found[0] if found else None


def client_rect(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """Client area of a window in screen coordinates: (left, top, width, height)."""
    rc = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    pt = wt.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    if rc.right <= 0 or rc.bottom <= 0:
        return None
    return pt.x, pt.y, rc.right, rc.bottom


def is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd


def focus(hwnd: int) -> bool:
    """Bring a window to the foreground; True if it ended up in front. Retries with an ALT-tap fallback."""
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)
        for attempt in range(3):
            if is_foreground(hwnd):
                return True
            cur = kernel32.GetCurrentThreadId()
            fg = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(fg_tid) and fg_tid != cur
            if attached:
                user32.AttachThreadInput(cur, fg_tid, True)
            try:
                if attempt:  # a tap on ALT satisfies Windows' foreground-lock rules
                    user32.keybd_event(0x12, 0, 0, 0)
                    user32.keybd_event(0x12, 0, 2, 0)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(cur, fg_tid, False)
            time.sleep(0.06)
        return is_foreground(hwnd)
    except Exception:
        return False


user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CreateFileW.restype = wt.HANDLE
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def open_shared(path) -> BinaryIO:
    """Open a file for reading WITHOUT blocking other processes from deleting/renaming/rewriting it
    (Python's open() omits FILE_SHARE_DELETE, which would stop CS2 from replacing its console.log)."""
    h = kernel32.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)  # READ, share R|W|D, OPEN_EXISTING
    if h is None or h == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(h, os.O_RDONLY), "rb")


def move_cursor(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def steam_running_app() -> int:
    """AppID of the Steam game that is running right now (0 = none, or Steam does not say)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return int(winreg.QueryValueEx(k, "RunningAppID")[0])
    except (OSError, ValueError, TypeError):
        return 0


def left_button_down() -> bool:
    primary = 0x02 if user32.GetSystemMetrics(23) else 0x01  # SM_SWAPBUTTON: the primary button may be the right one
    return bool(user32.GetAsyncKeyState(primary) & 0x8000)


def click(x: int, y: int, hwnd: Optional[int] = None) -> bool:
    """Move, let the game register the hover, then press and release (games drop instant move+click).

    With `hwnd`, the press is skipped (returns False) if that window lost the foreground during the hover,
    so a click never lands in another application."""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    if hwnd is not None and not is_foreground(hwnd):
        return False
    user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    try:
        time.sleep(0.04)
    finally:
        user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)  # never leave the button held down
    return True


# ---------- autostart ----------
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "AutoAcceptCS2"


def steam_exe() -> Optional[Path]:
    for r in steam_roots():
        if (r / "steam.exe").exists():
            return r / "steam.exe"
    return None


def ensure_steam_debug_flag() -> bool:
    """Create the file that makes Steam's embedded browser listen on 127.0.0.1:8080 (read at Steam's next start)."""
    ok = False
    for r in steam_roots():
        try:
            (r / ".cef-enable-remote-debugging").touch(exist_ok=True)
            ok = True
        except OSError:
            pass
    return ok


def port_owner(port: int) -> Optional[str]:
    """Name of the program that is listening on a local TCP port (None: nobody, or it cannot be told)."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, creationflags=0x08000000,
                             timeout=10).stdout.decode("utf-8", "ignore")
        pid = next((int(p[-1]) for ln in out.splitlines() if "LISTENING" in ln
                    for p in [ln.split()] if len(p) >= 5 and p[1].endswith(f":{port}") and p[-1].isdigit()), None)
        if pid is None:
            return None
        row = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             creationflags=0x08000000, timeout=10).stdout.decode("utf-8", "ignore").strip()
        return row.split('","')[0].strip('"') if row.startswith('"') else f"pid {pid}"
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _running(image: str) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"], capture_output=True,
                             creationflags=0x08000000, timeout=15).stdout.decode("utf-8", "ignore").lower()
    except (OSError, subprocess.SubprocessError):
        return True  # cannot tell: assume it is still running
    return image in out


def _clean_env() -> dict:
    """Environment for programs we start: without PyInstaller's private variables (Steam/CS2 must not inherit them)."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("_MEI", "_PYI"))}


def restart_steam() -> bool:
    """Exit Steam and start it again (so the debug flag is picked up). Takes up to a minute: call from a thread."""
    exe = steam_exe()
    if exe is None:
        return False
    kw = {"creationflags": 0x08000000, "env": _clean_env(), "cwd": str(exe.parent)}
    ensure_steam_debug_flag()  # the whole point of the restart
    subprocess.Popen([str(exe), "-shutdown"], **kw)
    deadline = time.monotonic() + 180  # cloud sync and a running game can make Steam close slowly
    while time.monotonic() < deadline:
        time.sleep(1.5)
        if not _running("steam.exe") and not _running("steamwebhelper.exe"):
            subprocess.Popen([str(exe)], **kw)
            return True
    subprocess.Popen([str(exe)], **kw)  # did not close in time: starting it again only wakes the running one
    return False


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    script = Path(sys.argv[0]).resolve()
    return f'"{exe}" "{script}"'


def set_autostart(enabled: bool) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, _autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, _RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ---------- Steam / console.log discovery ----------
def _reg_str(root, subkey: str, value: str) -> Optional[str]:
    try:
        with winreg.OpenKey(root, subkey) as k:
            return str(winreg.QueryValueEx(k, value)[0])
    except OSError:
        return None


def steam_roots() -> list[Path]:
    cands = [
        _reg_str(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        _reg_str(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        _reg_str(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    out: list[Path] = []
    for c in cands:
        if c and Path(c).exists() and Path(c) not in out:
            out.append(Path(c))
    return out


def library_folders(vdf: Path) -> list[Path]:
    import re

    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return [Path(m.group(1).replace("\\\\", "\\")) for m in re.finditer(r'"path"\s*"([^"]+)"', text)]


_CS2_LOG_REL = Path("steamapps/common/Counter-Strike Global Offensive/game/csgo/console.log")


def find_cs2_log() -> Optional[Path]:
    try:
        libs: list[Path] = []
        for root in steam_roots():
            libs.append(root)
            libs += library_folders(root / "steamapps" / "libraryfolders.vdf")
        for lib in libs:
            p = lib / _CS2_LOG_REL
            if p.exists():
                return p
    except Exception:
        pass
    return None


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


@functools.lru_cache(maxsize=1)
def data_dir() -> Path:
    """Config and log live next to the exe (portable); AppData only if that folder is read-only."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    d = base / "data"
    if _writable(d):
        return d
    for fallback in (Path(os.environ.get("APPDATA") or Path.home()) / "AutoAcceptCS2",
                     Path(os.environ.get("TEMP") or ".") / "AutoAcceptCS2"):
        if _writable(fallback):
            return fallback
    return d  # nothing is writable: callers must tolerate failures


def migrate_legacy_config(dest: Path) -> None:
    """One-time move of config.json from the old %APPDATA% location."""
    import shutil

    legacy = Path(os.environ.get("APPDATA") or Path.home()) / "AutoAcceptCS2" / "config.json"
    try:
        if not dest.exists() and legacy.is_file() and legacy.parent != dest.parent:
            shutil.copy2(legacy, dest)
    except OSError:
        pass
