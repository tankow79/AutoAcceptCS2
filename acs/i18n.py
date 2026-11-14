"""Tiny live-switchable translations (English / Ukrainian). No Qt: plain dicts, zero cost at runtime."""
from __future__ import annotations

import ctypes

LANGS = ("en", "uk")
_lang = "en"

EN: dict[str, str] = {
    # navigation / chrome
    "nav.friends": "Friends", "page.friends": "Friend invites", "page.friends_sub": "Join a friend's lobby automatically",
    "sec.invites": "Auto-accept invites", "inv.enabled": "Accept invites from listed friends",
    "inv.enabled_h": "Runs the invite's join link, like pressing Play/Join",
    "inv.ids": "Friends (Steam ID)", "inv.ids_h": "Steam ID64, account ID or profile link (also steamcommunity.com/id/name); one per line or separated by commas",
    "inv.names": "Found in Steam: {names}", "inv.names_none": "No friend with these IDs in Steam's local cache",
    "inv.vanity": "Looking up profile links online: {names}",
    "tg.control": "Control from Telegram", "tg.control_h": "Buttons in the bot chat for the main settings (send /start). Only for YOUR OWN bot: a bot can have just one reader, so never switch this on for a bot shared with others",
    "ctl.title": "⚙️ AutoAcceptCS2", "ctl.help": "Tap a button to change it",
    "ctl.enabled": "Auto-accept", "ctl.beep": "Beep",
    "ctl.invites": "Friend invites", "ctl.on_match": "Match found", "ctl.on_ping": "Server ping",
    "ctl.on_expired": "Expired", "ctl.notify": "Notifications ▸", "ctl.back": "◂ Back",
    "ctl.refresh": "🔄 Refresh", "ctl.notify_title": "Which Telegram messages to send",

    "inv.names_empty": "Nobody listed yet", "inv.restart": "Restart Steam",
    "inv.restart_confirm": "Steam will close and start again (it opens a local port for this). Click again to confirm",
    "inv.calib": "Calibrate OK", "inv.calib_wait": "CS2 shows a box now (\"Join another game?\" or \"Failed to join\"): click its OK button once",
    "inv.calib_saved": "Saved for this window size: from now on the app clicks there",
    "inv.calib_no_game": "Start CS2 first", "inv.calib_no_steam": "Steam is not ready (see the status below)",
    "inv.calib_timeout": "No click within a minute: try again", "inv.calib_busy": "Calibration is already running", "inv.calib_outside": "The click was outside the game window: try again",
    "inv.restarting": "Restarting Steam…", "inv.restart_failed": "Could not restart Steam; restart it yourself",
    "inv.s.ok": "Working: listening to Steam for invites", "inv.s.off": "Off: the switch is off (turn it on and press Save)", "inv.s.no_ids": "Switch is on, but the friend list is empty: add a Steam ID and press Save",
    "inv.s.no_port": "Waiting for Steam: restart it once (button on the left) so it opens its local port",
    "inv.s.port_busy": "Port 8080 is held by \"{name}\", so Steam cannot open it: close that program, then restart Steam",
    "inv.s.no_ui": "Steam is starting or its interface is not ready yet", "inv.s.error": "Error (see app.log)",
    "inv.note": "How it works: when a friend from the list invites you to CS2, the app reads their Steam ID from Steam. If CS2 is running, it joins their lobby; if not, it launches CS2 first and joins once the game is up. The game then asks \"Join another game?\": the app brings CS2 to the front and clicks OK (works in any language). Invites from anyone else are ignored.",
    "nav.status": "Status", "nav.general": "General", "nav.click": "Click", "nav.telegram": "Telegram", "nav.log": "Log",
    "btn.save": "Save", "btn.cancel": "Cancel",
    "window.title": "AutoAccept CS2 — Settings",
    # status page
    "page.status": "Status", "page.status_sub": "Live overview of the auto-accept",
    "hero.on": "Ready", "hero.off": "Paused", "hero.nolog": "Log not found",
    "hero.on_sub": "Waiting for a match…", "hero.off_sub": "Auto-accept is turned off",
    "hero.nolog_sub": "Start CS2 with the launch option -condebug",
    "st.accepted": "Matches accepted", "st.last": "Last match", "st.log": "console.log", "st.cs2": "CS2 window",
    "st.data": "Data folder", "st.cs2_on": "running", "st.cs2_off": "not running", "st.none": "—",
    "st.log_missing": "not found", "st.last_fmt": "#{id} at {time}",
    "sec.how": "How it works",
    "how.text": "Reads CS2's console.log live and waits for a match.\n"
                "Brings the game to the front and clicks Accept at the position you set.\n"
                "Never clicks if CS2 isn't in front; stops if the reservation expires.\n"
                "Can report match, ping and expiry to Telegram.",
    # general
    "page.general": "General", "page.general_sub": "How the app behaves",
    "sec.behaviour": "Behaviour", "sec.safety": "Safety",
    "g.enabled": "Auto-accept", "g.enabled_h": "Accept found matches automatically",
    "g.autostart": "Start with Windows", "g.autostart_h": "Launch in the tray when you sign in",
    "g.focus": "Bring CS2 to front", "g.focus_h": "Switch to the game when a match is found",
    "g.beep": "Sound on match", "g.beep_h": "Play a short beep when a match is found",
    "g.cooldown": "Ignore repeats for", "g.cooldown_h": "Avoids accepting the same match twice",
    "unit.s": "s", "unit.ms": "ms",
    # click
    "page.click": "Click", "page.click_sub": "Where and how the Accept button is clicked",
    "sec.calibrate": "Calibrate with the mouse", "sec.position": "Accept button position", "sec.timing": "Timing",
    "cal.steps": "1. Press “Start listening”.\n2. In CS2, hover the mouse over the Accept button.\n"
                 "3. Press the capture hotkey — the position is filled in below.\n4. Press the test hotkey to click there.",
    "cal.note": "Hotkeys work only while listening. Listening stops on Save, on close, or when you leave this tab.",
    "cal.start": "Start listening", "cal.stop": "Stop listening", "cal.off": "Hotkeys are off",
    "cal.on": "Listening — hover the Accept button in CS2 and press {hotkey}",
    "cal.hk_capture": "Capture hotkey", "cal.hk_capture_h": "Reads the mouse position inside the game window",
    "cal.hk_test": "Test click hotkey", "cal.hk_test_h": "Clicks at the position set below",
    "hk.press": "Press keys…  (Esc cancels)", "hk.conflict": "That combo is already used by the other action",
    "cal.fail": "Capture failed: CS2 is not running or the cursor is outside its window",
    "cal.captured": "Captured X={x}%  Y={y}% — press Save to keep it",
    "cal.test_ok": "Test click at X={x}%  Y={y}%", "cal.test_fail": "Test click failed: CS2 window not found",
    "pos.x": "Horizontal (X)", "pos.x_h": "% of the game window width",
    "pos.y": "Vertical (Y)", "pos.y_h": "% of the game window height", "pos.preview": "game window",
    "t.delay": "Delay before first click", "t.count": "Number of clicks", "t.interval": "Interval between clicks",
    # telegram
    "page.telegram": "Telegram", "page.telegram_sub": "Get notified on your phone",
    "sec.bot": "Bot", "sec.notify": "Notify me about",
    "tg.enabled": "Send notifications", "tg.enabled_h": "Match found, ping and expiry",
    "tg.token": "Bot token", "tg.token_h": "From <a href=\"https://t.me/BotFather\">@BotFather</a>", "tg.chat": "Chat ID", "tg.chat_h": "Your ID or a channel @username (<a href=\"https://core.telegram.org/bots/api#sendmessage\">what is this</a>)",
    "tg.test": "Send test message", "tg.sending": "Sending…", "tg.ok": "Telegram OK", "tg.fail": "Failed: {info}",
    "tg.on_match": "Match found", "tg.on_ping": "Server ping", "tg.on_expired": "Reservation expired",
    # log
    "page.log": "Log", "page.log_sub": "Where CS2 writes its console log",
    "sec.log": "console.log", "sec.detect": "Detection",
    "log.auto": "Find automatically", "log.auto_h": "Searches your Steam libraries",
    "log.path": "Manual path", "log.path_h": "Used when automatic search is off", "log.browse": "Browse…",
    "log.pick": "Select console.log",
    "log.condebug": "CS2 writes console.log only with the launch option  -condebug  "
                    "(Steam → CS2 → Properties → Launch options).",
    "log.kw": "Match keyword", "log.kw_h": "A line like match_id=123 marks a found match",
    # tray
    "tray.auto": "Auto-accept", "tray.settings": "Settings…", "tray.autostart": "Start with Windows",
    "tray.telegram": "Telegram notifications", "tray.sec_control": "Control", "tray.sec_app": "App", "tray.quit": "Quit", "tray.tooltip": "AutoAccept CS2 — {state}",
    "state.on": "Watching CS2 — auto-accept ON", "state.off": "Auto-accept is OFF", "state.nolog": "console.log not found",
    # messages sent to Telegram
    "warn.legacy": "An older AutoAccept (CS2Watcher) is still running — you may get duplicate notifications. Close it and remove it from startup.",
    "msg.match": "🎮 CS2: match found", "msg.invite_joined": "👥 CS2: joined a friend's lobby",
    "msg.calibrate_needed": "⚠️ CS2: window size {size} is not calibrated, the OK click may miss. Open the app → Friends → Calibrate OK",
    "msg.no_window": "⚠️ CS2: match found but the game window was not available",
    "msg.expired": "⏱️ CS2: reservation expired (match not accepted)",
    "msg.ping": "📍 Ping: {ping} ms", "msg.test": "✅ AutoAccept CS2: test message",
}

UK: dict[str, str] = {
    "nav.friends": "Друзі", "page.friends": "Запрошення друзів", "page.friends_sub": "Автоматично приєднуватись до лобі друга",
    "sec.invites": "Авто-прийом запрошень", "inv.enabled": "Приймати запрошення від друзів зі списку",
    "inv.enabled_h": "Виконує посилання запрошення, як кнопка «Грати»",
    "inv.ids": "Друзі (Steam ID)", "inv.ids_h": "Steam ID64, account ID або посилання на профіль (також steamcommunity.com/id/ім'я); по одному в рядок або через кому",
    "inv.names": "Знайдено в Steam: {names}", "inv.names_none": "Друзів із такими ID немає в локальному кеші Steam",
    "inv.vanity": "Шукаю посилання на профіль онлайн: {names}",
    "tg.control": "Керування з Telegram", "tg.control_h": "Кнопки в чаті бота для головних налаштувань (надішли /start). Лише для ВЛАСНОГО бота: бот може мати лише одного читача, тому не вмикай це для бота, яким користуються інші",
    "ctl.title": "⚙️ AutoAcceptCS2", "ctl.help": "Натисни кнопку, щоб змінити",
    "ctl.enabled": "Автоприйняття", "ctl.beep": "Звук",
    "ctl.invites": "Інвайти друзів", "ctl.on_match": "Матч знайдено", "ctl.on_ping": "Пінг сервера",
    "ctl.on_expired": "Прострочено", "ctl.notify": "Сповіщення ▸", "ctl.back": "◂ Назад",
    "ctl.refresh": "🔄 Оновити", "ctl.notify_title": "Які повідомлення слати в Telegram",

    "inv.names_empty": "Список порожній", "inv.restart": "Перезапустити Steam",
    "inv.restart_confirm": "Steam закриється і запуститься знову (для цього він відкриває локальний порт). Натисніть ще раз",
    "inv.calib": "Калібрувати OK", "inv.calib_wait": "CS2 показала табличку («Join another game?» або «Failed to join»): натисни на ній кнопку OK один раз",
    "inv.calib_saved": "Збережено для цього розміру вікна: далі програма клікає туди",
    "inv.calib_no_game": "Спочатку запусти CS2", "inv.calib_no_steam": "Steam не готовий (див. статус нижче)",
    "inv.calib_timeout": "Кліку не було за хвилину: спробуй ще раз", "inv.calib_busy": "Калібрування вже триває", "inv.calib_outside": "Клік був поза вікном гри: спробуй ще раз",
    "inv.restarting": "Перезапуск Steam…", "inv.restart_failed": "Не вдалось перезапустити Steam; перезапустіть самі",
    "inv.s.ok": "Працює: слухаю Steam на запрошення", "inv.s.off": "Вимкнено: перемикач вимкнено (увімкніть і натисніть «Зберегти»)", "inv.s.no_ids": "Перемикач увімкнено, але список друзів порожній: додайте Steam ID і натисніть «Зберегти»",
    "inv.s.no_port": "Чекаю на Steam: перезапустіть його один раз (кнопка зліва), щоб він відкрив локальний порт",
    "inv.s.port_busy": "Порт 8080 займає «{name}», тому Steam не може його відкрити: закрийте цю програму і перезапустіть Steam",
    "inv.s.no_ui": "Steam запускається або його інтерфейс ще не готовий", "inv.s.error": "Помилка (див. app.log)",
    "inv.note": "Як це працює: коли друг зі списку запрошує вас у CS2, програма бере його Steam ID зі Steam. Якщо CS2 запущена, вона приєднується до його лобі; якщо ні, спочатку запускає CS2 і приєднується, коли гра завантажиться. Гра питає «Join another game?»: програма виводить CS2 на передній план і натискає OK (працює будь-якою мовою). Запрошення від інших ігноруються.",
    "nav.status": "Статус", "nav.general": "Загальні", "nav.click": "Клік", "nav.telegram": "Telegram", "nav.log": "Лог",
    "btn.save": "Зберегти", "btn.cancel": "Скасувати",
    "window.title": "AutoAccept CS2 — Налаштування",
    "page.status": "Статус", "page.status_sub": "Поточний стан автоприйняття",
    "hero.on": "Готово", "hero.off": "Призупинено", "hero.nolog": "Лог не знайдено",
    "hero.on_sub": "Чекаю на матч…", "hero.off_sub": "Автоприйняття вимкнено",
    "hero.nolog_sub": "Запустіть CS2 з параметром -condebug",
    "st.accepted": "Прийнято матчів", "st.last": "Останній матч", "st.log": "console.log", "st.cs2": "Вікно CS2",
    "st.data": "Папка даних", "st.cs2_on": "запущено", "st.cs2_off": "не запущено", "st.none": "—",
    "st.log_missing": "не знайдено", "st.last_fmt": "#{id} о {time}",
    "sec.how": "Як це працює",
    "how.text": "Читає console.log гри в реальному часі й чекає на матч.\n"
                "Виводить гру на передній план і натискає «Прийняти» у заданій позиції.\n"
                "Не клікає, якщо CS2 не на передньому плані; зупиняється, якщо резервація скінчилась.\n"
                "Може надсилати в Telegram матч, пінг і завершення резервації.",
    "page.general": "Загальні", "page.general_sub": "Поведінка застосунку",
    "sec.behaviour": "Поведінка", "sec.safety": "Безпека",
    "g.enabled": "Автоприйняття", "g.enabled_h": "Приймати знайдені матчі автоматично",
    "g.autostart": "Запуск з Windows", "g.autostart_h": "Запускати в треї при вході в систему",
    "g.focus": "Виводити CS2 наперед", "g.focus_h": "Перемикатись у гру, коли знайдено матч",
    "g.beep": "Звук при матчі", "g.beep_h": "Короткий сигнал, коли знайдено матч",
    "g.cooldown": "Ігнорувати повтори", "g.cooldown_h": "Щоб не прийняти один матч двічі",
    "unit.s": "с", "unit.ms": "мс",
    "page.click": "Клік", "page.click_sub": "Де і як натискається кнопка «Прийняти»",
    "sec.calibrate": "Калібрування мишею", "sec.position": "Позиція кнопки «Прийняти»", "sec.timing": "Таймінги",
    "cal.steps": "1. Натисніть «Почати прослуховування».\n2. У CS2 наведіть мишу на кнопку «Прийняти».\n"
                 "3. Натисніть хоткей захоплення — позиція підставиться нижче.\n4. Натисніть хоткей тесту, щоб клікнути туди.",
    "cal.note": "Хоткеї працюють лише під час прослуховування. Воно вимикається при збереженні, закритті або виході з цієї вкладки.",
    "cal.start": "Почати прослуховування", "cal.stop": "Зупинити прослуховування", "cal.off": "Хоткеї вимкнено",
    "cal.on": "Слухаю — наведіть мишу на «Прийняти» в CS2 і натисніть {hotkey}",
    "cal.hk_capture": "Хоткей захоплення", "cal.hk_capture_h": "Зчитує позицію миші у вікні гри",
    "cal.hk_test": "Хоткей тестового кліку", "cal.hk_test_h": "Клікає у позиції, заданій нижче",
    "hk.press": "Натисніть клавіші…  (Esc — скасувати)", "hk.conflict": "Ця комбінація вже використовується іншою дією",
    "cal.fail": "Не вдалося: CS2 не запущено або курсор поза його вікном",
    "cal.captured": "Захоплено X={x}%  Y={y}% — натисніть «Зберегти»",
    "cal.test_ok": "Тестовий клік у X={x}%  Y={y}%", "cal.test_fail": "Тестовий клік не вдався: вікно CS2 не знайдено",
    "pos.x": "По горизонталі (X)", "pos.x_h": "% ширини вікна гри",
    "pos.y": "По вертикалі (Y)", "pos.y_h": "% висоти вікна гри", "pos.preview": "вікно гри",
    "t.delay": "Затримка перед першим кліком", "t.count": "Кількість кліків", "t.interval": "Інтервал між кліками",
    "page.telegram": "Telegram", "page.telegram_sub": "Сповіщення на телефон",
    "sec.bot": "Бот", "sec.notify": "Сповіщати про",
    "tg.enabled": "Надсилати сповіщення", "tg.enabled_h": "Матч, пінг і завершення резервації",
    "tg.token": "Токен бота", "tg.token_h": "Від <a href=\"https://t.me/BotFather\">@BotFather</a>", "tg.chat": "ID чату", "tg.chat_h": "Ваш ID або @username каналу (<a href=\"https://core.telegram.org/bots/api#sendmessage\">що це</a>)",
    "tg.test": "Надіслати тест", "tg.sending": "Надсилаю…", "tg.ok": "Telegram OK", "tg.fail": "Помилка: {info}",
    "tg.on_match": "Знайдено матч", "tg.on_ping": "Пінг сервера", "tg.on_expired": "Резервація скінчилась",
    "page.log": "Лог", "page.log_sub": "Де CS2 пише свій консольний лог",
    "sec.log": "console.log", "sec.detect": "Розпізнавання",
    "log.auto": "Шукати автоматично", "log.auto_h": "Пошук у бібліотеках Steam",
    "log.path": "Шлях вручну", "log.path_h": "Використовується, якщо автопошук вимкнено", "log.browse": "Огляд…",
    "log.pick": "Виберіть console.log",
    "log.condebug": "CS2 пише console.log лише з параметром запуску  -condebug  "
                    "(Steam → CS2 → Властивості → Параметри запуску).",
    "log.kw": "Ключове слово матчу", "log.kw_h": "Рядок на кшталт match_id=123 означає знайдений матч",
    "tray.auto": "Автоприйняття", "tray.settings": "Налаштування…", "tray.autostart": "Запуск з Windows",
    "tray.telegram": "Сповіщення Telegram", "tray.sec_control": "Керування", "tray.sec_app": "Застосунок", "tray.quit": "Вийти", "tray.tooltip": "AutoAccept CS2 — {state}",
    "state.on": "Стежу за CS2 — автоприйняття УВІМК.", "state.off": "Автоприйняття ВИМКНЕНО", "state.nolog": "console.log не знайдено",
    "warn.legacy": "Ще працює старий AutoAccept (CS2Watcher) — можливі подвійні сповіщення. Закрийте його й приберіть з автозапуску.",
    "msg.match": "🎮 CS2: знайдено матч", "msg.invite_joined": "👥 CS2: приєднався до лобі друга",
    "msg.calibrate_needed": "⚠️ CS2: розмір вікна {size} не відкалібровано, клік по OK може схибити. Відкрий програму → Друзі → Калібрувати OK",
    "msg.no_window": "⚠️ CS2: матч знайдено, але вікно гри було недоступне",
    "msg.expired": "⏱️ CS2: резервація скінчилась (матч не прийнято)",
    "msg.ping": "📍 Пінг: {ping} мс", "msg.test": "✅ AutoAccept CS2: тестове повідомлення",
}

_TABLES = {"en": EN, "uk": UK}


def detect() -> str:
    """UI language of Windows: Ukrainian -> 'uk', anything else -> 'en'."""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "uk" if (lang_id & 0x3FF) == 0x22 else "en"
    except Exception:
        return "en"


def resolve(code: str) -> str:
    return code if code in LANGS else detect()


def set_language(code: str) -> str:
    global _lang
    _lang = resolve(code)
    return _lang


def language() -> str:
    return _lang


def tr(key: str, **kw: object) -> str:
    text = _TABLES[_lang].get(key) or EN.get(key) or key
    try:
        return text.format(**kw) if kw else text
    except (KeyError, IndexError, ValueError):
        return text
