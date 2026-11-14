# AutoAccept CS2

**English** · [Українська](README.uk.md)

A small Windows tray app that accepts a found **Counter-Strike 2** match for you, tells you about it on Telegram, and can
automatically join a lobby when a friend from your list invites you.

## Rules of use

- Free for personal use. The program is provided "as is", without any warranty: you use it at your own risk.
- It is an independent project, **not affiliated with or endorsed by Valve or Telegram**. Counter-Strike 2 and Steam are
  trademarks of Valve Corporation.
- It does not touch the game's memory or files. It reads CS2's `console.log`, simulates a mouse click on the *Accept*
  button (and, in friend mode, on the game's *OK* box) and talks to Steam's local interface. You are responsible for
  following the rules of the services you use.
- Keep your Telegram bot token private: it is stored in `data\config.json` next to the exe.

## Quick start (ready-made release)

1. Open the [**Releases**](../../releases) page (the newest release is at the top) and download **`AutoAcceptCS2.exe`**
   (or the **`.zip`**: unpack it anywhere, it contains the same exe and these guides).
2. Put the exe in its own folder, for example `C:\Tools\AutoAcceptCS2\`. Do not run it from inside the zip.
3. Double-click it. If Windows SmartScreen warns about an unknown publisher (the exe is not code-signed), choose
   *More info → Run anyway*. The icon appears in the system tray (bottom-right, maybe under the `^` arrow).
4. In Steam add the launch option `-condebug` to Counter-Strike 2 (details in [First run](#first-run-5-minutes)).
5. Tray icon → **Settings**: pick the Accept button position on the *Click* page and turn on *Auto-accept*.

Settings and the log are created in a `data` folder next to the exe. To update, replace the exe with the new one and
keep the `data` folder. To uninstall, quit the app from the tray and delete its folder.

## What it does

- **Auto-accept.** Watches CS2's `console.log`. When a match is found it brings CS2 to the front and clicks *Accept*.
- **Telegram (optional).** Messages when a match is found, when the server ping arrives and when the reservation expires.
  With your own bot you can also change the main settings with buttons.
- **Friend invites (optional).** When a friend from your whitelist invites you to CS2, the app joins their lobby.
  If CS2 is closed it launches the game first. CS2's confirmation boxes are closed for you.
  A friend is recognised by Steam ID, never by name. Invites are ignored while another Steam game is running.
- **Safe by design.** One message at a time to Telegram (no duplicates, no flood), every network call has a timeout,
  the app survives a missing log, a locked config and a closed game.
- English and Ukrainian interface, switchable live. Midnight Cyan dark theme.

## Requirements

- Windows 10 or 11.
- Counter-Strike 2 installed through Steam.
- For running from source only: Python 3.10 or newer.

## Install

### Option A: ready-made exe (easiest)

1. Open the **Releases** page of this repository and download `AutoAcceptCS2.exe`.
2. Put it in any folder you like, for example `C:\Tools\AutoAcceptCS2\`. The app is portable: its settings and log are
   created in a `data` folder next to the exe.
3. Double-click it. The icon appears in the tray (bottom-right, maybe under the `^` arrow).

Windows SmartScreen may warn about an unknown publisher because the exe is not code-signed: choose *More info → Run anyway*.

### Option B: run or build from source

```
git clone https://github.com/tankow79/AutoAcceptCS2.git
cd AutoAcceptCS2
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe autoaccept.pyw
```

Build your own exe: run `build.bat` (result: `out\AutoAcceptCS2.exe`).
Tests: `.venv\Scripts\python.exe -m pytest -q`. Lint: `.venv\Scripts\python.exe -m ruff check .`

## First run (5 minutes)

1. **Tell CS2 to write its console log.** In Steam: Library → right-click *Counter-Strike 2* → Properties →
   *General* → *Launch options*, and enter `-condebug`. Restart CS2 if it is running.
   Without this option CS2 does not write `console.log` and nothing can be detected.
2. **Start AutoAccept CS2** and open the tray icon → **Settings**.
3. **Log page.** Leave *Find automatically* on. The app searches your Steam libraries. If it cannot find the log
   (the status page says so), switch it off and pick `...\game\csgo\console.log` by hand.
4. **Click page: set the Accept button position.** Start CS2, get to a screen where *Accept* is visible
   (or use the test hotkey), then use *Calibrate with the mouse*: put the cursor on the *Accept* button and press the
   capture hotkey (default **Ctrl+F8**). The position is saved as a percentage of the game window, so it keeps working
   if you change the resolution. **Ctrl+F9** is a test click.
5. **General page.** Turn on *Auto-accept*. Optionally turn on *Start with Windows*.

That is all for the basic function. The Status page shows whether the log is found and the game is detected.

## Settings, page by page

| Page | What it is for |
| --- | --- |
| **Status** | Live overview: auto-accept on/off, log found, CS2 running, accepted matches. |
| **General** | Auto-accept switch, start with Windows, bring CS2 to front, sound on match, ignore repeats (cooldown). Interface language is the switch at the bottom of the window. |
| **Click** | Accept button position (% of the window), delay before the click, number of clicks and interval, hotkeys. |
| **Telegram** | Bot token, chat ID, test button, which events to send, remote control switch. |
| **Friends** | Friend invites: switch, list of Steam IDs, restart Steam, calibrate OK. |
| **Log** | Automatic or manual `console.log` path and the match keyword (default `match_id`). |

Settings are applied at once and saved to `data\config.json` next to the exe. The log is `data\app.log`.

## Telegram notifications

1. In Telegram open [@BotFather](https://t.me/BotFather), send `/newbot`, follow the steps and copy the **token**
   (looks like `123456789:AA...`).
2. Open your new bot and press **Start** (a bot cannot write to you before that).
3. Find your **chat ID**: the number of your private chat. The Telegram docs describe the field in the
   [sendMessage method](https://core.telegram.org/bots/api#sendmessage); bots such as `@userinfobot` show your ID.
4. In the app: Settings → **Telegram**: turn it on, paste the token and the chat ID, press **Test**.
5. Choose which events you want to receive.

### Control from Telegram (optional, your own bot only)

Turn on *Control from Telegram* on the Telegram page, then send **/start** to your bot. You get buttons for
auto-accept, sound, friend invites, which notifications to receive and the language.

> Telegram lets only **one** program read a bot's messages. Use this with **your own bot**. A bot shared by several
> people can still send notifications, but control must stay off there. Only your private chat is obeyed.

## Friend invites (optional)

Lets a friend pull you into their lobby without you touching anything.

1. Settings → **Friends**: turn on *Accept invites from listed friends*.
2. Put the friends' Steam IDs into the list, one per line or separated by commas. Accepted forms:
   Steam ID64 (`7656119…`), account ID, a profile link `https://steamcommunity.com/profiles/…`, or a custom link
   `https://steamcommunity.com/id/name`. Names found in Steam's local data are shown under the list.
3. Press **Restart Steam** (twice: the second click confirms). Steam creates a local debug port that the app listens to.
   This is needed **once**. Later reboots and Steam restarts need nothing.
4. Press **Calibrate OK**, wait for CS2's box (*Join another game?* or *Failed to join*) and click its **OK** button once.
   The app remembers where OK is **for this window size**. After you change the game resolution, calibrate again
   (the app reminds you on Telegram).
5. Ask a friend to send an invite. The status line on the Friends page tells you what the app sees.

What happens next: the invite is read from Steam, the friend must be on your list, then the app joins the lobby.
If CS2 is closed it starts the game, waits for the menu, joins, and clicks OK on the game's box.
If another Steam game is running, the invite is ignored.

**About the debug port.** The app creates an empty file `.cef-enable-remote-debugging` in the Steam folder. Steam then
listens on `127.0.0.1:8080`, on this PC only, never on the network. Any program on your PC can use that port to drive
Steam's interface, so switch the feature off and delete the file if you do not want that. If another program holds port
8080, the status line names it.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| "Log not found" | Add `-condebug` to CS2's launch options, restart CS2, or pick `console.log` by hand on the Log page. |
| The click misses the Accept button | Re-calibrate on the Click page (Ctrl+F8 with the cursor on the button). |
| Telegram "Test" fails | Check the token, press Start in the bot, check the chat ID. Wrong settings pause sending; fixing them resumes it. |
| Friends: "waiting for Steam" | Press **Restart Steam** once. |
| Friends: "port 8080 is held by …" | Close that program, then restart Steam. |
| The OK click misses | Press **Calibrate OK** again for the current resolution. |
| The exe cannot be rebuilt | The app is still running and locks the file: quit it from the tray first. |

## Privacy and safety

- Nothing is sent anywhere except the Telegram messages you enable and, for profile links like `steamcommunity.com/id/name`,
  one lookup on steamcommunity.com.
- The bot token is stored in `data\config.json` in plain text: keep that folder private and never share it.
- The app never reads or changes game memory and sends no input except the mouse click on *Accept*, and in friend mode the
  click on the game's *OK* box.

## License

Add a license of your choice to the repository (for example MIT) before sharing it widely.
