# AutoAcceptCS2 (CS2 console.log watcher + tray)

A small Windows tray app that **tails CS2 `console.log` in real time**, detects a “match found” marker, and then:
- prints **only** 3 user-facing messages (match found / ping / timeout),
- optionally sends **Telegram** notifications,
- optionally brings **Counter-Strike 2** to the foreground,
- provides tray controls (enable/disable monitoring, console logging toggle, tests, open folder/config, exit).

> **Note on fair play / ToS:** this project reads local logs and can trigger local actions. If you add any gameplay/UI automation, you’re responsible for complying with Steam/Valve rules and any server policies.

---

## How it works

- The watcher reads `config.ini` and builds a regex from `detect.match_keyword` using the pattern `keyword=(digits)`.
- It watches `console.log` (tail -f style) and reacts to three events:
  1. **Match found**: a new `keyword=123...` value  
  2. **Ping**: the first “Selecting … as primary … Ping = X” line after match found  
  3. **Timeout**: `ReservationExpired` (deduplicated by cooldown)

- The app can attempt to bring CS2 to the foreground using Win32 APIs (pywin32 + psutil).

---

## Requirements

- **Windows 10/11**
- **Python 3.12+** (tested with 3.13)
- CS2 must write `console.log`. A common way is adding the Steam launch option `-condebug`, which writes console output to `...game\csgo\console.log`.

---

## Project structure

```
AutoAcceptCS2/
  main.py          # tray app + log watcher + config
  click.py         # optional mouse-click helpers (pyautogui)
  config.ini       # user settings
```

`click.py` contains helper functions that use `pyautogui` to move/click at the screen center with an adjustable Y offset.

---

## Installation

Create & activate a venv, then:

```bash
pip install -U pywin32 psutil pystray pillow pyautogui
```

---

## Configuration (`config.ini`)

`main.py` reads these keys (with safe fallbacks):

### `[telegram]`
- `enabled` — enable/disable Telegram notifications (`true/false`)
- `token` — bot token from **@BotFather**
- `chat_id` — your chat id (number) or `@channelusername`

**Security:** keep `token` private. If it leaks, regenerate it in @BotFather.

### `[app]`
- `start_enabled` — start monitoring immediately (`true/false`)
- `bring_cs2_to_front` — bring CS2 to foreground when match is detected (`true/false`)
- `beep_on_match` — beep on match (`true/false`)
- `reservation_cooldown_sec` — dedupe window for repeated `ReservationExpired` logs (seconds)
- `y_cordinate` — integer offset used by click helper functions (read with `getint`)

### `[detect]`
- `match_keyword` — the keyword to detect match lines in the log.  
  The watcher searches for `match_keyword=(digits)`.  
  Default is `match_id`.

### `[console]`
- `enabled_default` — whether console logging is ON by default at app start.

> The tray toggle does **not** modify `config.ini`, so after restart it returns to this default (see comment in code).

### `[log]`
- `auto` — auto-detect `console.log` path (`true/false`)
- `manual_path` — used when `auto=false`

Auto-detection tries:
1) Steam via Windows `PATH`, then  
2) Steam via registry, then  
3) Steam libraries from `libraryfolders.vdf`.

---

## Running

```bash
python main.py
```

The app runs in the tray. When a match is detected it prints/sends:
- `🎮 CS2: знайдено`
- `📍 Ping: XX ms`
- `⏱️ CS2: (схоже, матч не підтвердили/таймаут).`

---

## Tray menu

The tray menu includes:
- Enable monitoring / Disable monitoring
- Enable console logging / Disable console logging
- Test click
- Test Telegram
- Open app folder
- Open config.ini
- Exit

pystray menus are created by passing a `pystray.Menu` with `pystray.MenuItem` entries.

---

## Building an EXE (PyInstaller)

Install PyInstaller:

```bash
pip install -U pyinstaller
```

Build a single-file, no-console EXE and bundle `config.ini`:

```bash
pyinstaller --onefile --noconsole --add-data "config.ini;." main.py
```

- You can bundle data files via `--add-data` or in a spec file.
- On **Windows**, `--add-data` uses a **semicolon** (`;`) separator between source and destination in the argument (e.g., `"config.ini;."`).

---

## Troubleshooting

### `console.log` not found / nothing happens
- Ensure CS2 is writing `console.log` (often via launch option `-condebug`).
- If auto-detect fails, set `[log] auto=false` and provide `manual_path`.

### Telegram “FAILED”
- Check `[telegram] enabled=true`, and verify `token` / `chat_id` values.

### `ReservationExpired` prints multiple times
- Increase `reservation_cooldown_sec` in `[app]` to reduce duplicates.

---

## Notes

- If you add any UI automation (mouse/keyboard), do it responsibly and understand the risk of violating ToS.
