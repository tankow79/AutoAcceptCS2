"""Settings window (PyQt6): minimal, card-based, live EN/UK language switch. No timers run while idle."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import accept, hotkeys, i18n, icons, sounds, theme, winapi
from .i18n import tr
from .icon import render
from .notifier import post_message
from .settings import RANGES, Store
from . import friends
from .invite import STATUSES as INV_STATUSES
from .widgets import Card, DSpin, HotkeyButton, LangSwitch, PositionPreview, Spin, StatusDot, Switch

log = logging.getLogger("acs.ui")

NAV = (("nav.status", "status"), ("nav.general", "general"), ("nav.click", "click"),
       ("nav.telegram", "telegram"), ("nav.friends", "friends"), ("nav.log", "log"))


class Page(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("page")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(34, 30, 34, 20)
        self.lay.setSpacing(10)
        self.setWidget(body)


class SettingsDialog(QDialog):
    CLICK_PAGE = 2
    tg_result = pyqtSignal(bool, str)
    inv_result = pyqtSignal(str)
    inv_calib = pyqtSignal(str)

    def __init__(self, store: Store, status_fn: Callable[[], dict[str, Any]] | None = None, parent=None, invites=None):
        super().__init__(parent)
        self.store, self.status_fn, self._invites = store, status_fn, invites
        self._get: dict[str, Callable[[], Any]] = {}
        self._bound: list[Callable[[], None]] = []
        self._poller: hotkeys.HotkeyPoller | None = None
        self._initial: dict[str, Any] | None = None  # values when the dialog opened (Save writes only the diff)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(30)
        self._poll_timer.timeout.connect(self._poll)
        self._listen_msg: tuple[str, bool | None, dict] = ("cal.off", None, {})
        self._tg_msg: tuple[str, dict] | None = None
        self._inv_msg: str | None = None
        self._inv_confirm = False
        i18n.set_language(store.get().language)

        self.setWindowIcon(QIcon(theme.pixmap(render(64))))
        self.setMinimumSize(760, 540)
        self.resize(880, 640)
        self.setStyleSheet(theme.DIALOG_QSS)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        right = QWidget()
        right.setObjectName("right")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.stack = QStackedWidget()
        for builder in (self._status_page, self._general_page, self._click_page, self._telegram_page, self._friends_page, self._log_page):
            self.stack.addWidget(builder())
        rl.addWidget(self.stack, 1)
        rl.addLayout(self._build_footer())
        root.addWidget(right, 1)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.currentRowChanged.connect(self._on_page)
        self.nav.setCurrentRow(0)
        self.tg_result.connect(self._on_tg_result)
        self.inv_result.connect(self._on_inv_result)
        self.inv_calib.connect(self._on_calib)
        self._bind(lambda: self.setWindowTitle(tr("window.title")))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.timeout.connect(self._render_inv_result)
        self._timer.timeout.connect(self._render_inv)
        self._timer.start(1000)

    # ---------- translation plumbing ----------
    def _bind(self, fn: Callable[[], None]) -> None:
        self._bound.append(fn)
        fn()

    def _label(self, key: str, role: str | None = None, wrap: bool = True, **kw: Any) -> QLabel:
        lbl = QLabel()
        lbl.setWordWrap(wrap)
        if role:
            lbl.setProperty("role", role)
        lbl.setOpenExternalLinks(True)  # texts with <a href> (BotFather, Telegram docs) open in the browser
        self._bind(lambda: lbl.setText(tr(key, **kw).replace("<a ", f'<a style="color:{theme.ACCENT}" ')))
        return lbl

    def _button(self, key: str, role: str | None = None) -> QPushButton:
        btn = QPushButton()
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if role:
            btn.setProperty("role", role)
        self._bind(lambda: btn.setText(tr(key)))
        return btn

    def retranslate(self) -> None:
        for fn in self._bound:
            fn()
        for i, (key, _ic) in enumerate(NAV):
            self.nav.item(i).setText(tr(key))
        self._refresh_status(force=True)

    def _on_lang(self, code: str) -> None:
        i18n.set_language(code)
        self._hk_capture.cancel()
        self._hk_test.cancel()
        self.store.update(language=code)
        self.retranslate()

    # ---------- chrome ----------
    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(216)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 20, 14, 16)
        lay.setSpacing(0)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        ico = QLabel()
        ico.setPixmap(theme.pixmap(render(96)).scaled(
            30, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        name = QLabel("AutoAccept CS2")
        name.setProperty("role", "brand")
        brand.addWidget(ico)
        brand.addSpacing(8)
        brand.addWidget(name, 1)
        lay.addLayout(brand)
        lay.addSpacing(22)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for key, ic in NAV:
            item = QListWidgetItem(icons.icon(ic, theme.MUTED, theme.ACCENT), tr(key))
            self.nav.addItem(item)
        lay.addWidget(self.nav, 1)
        self.lang = LangSwitch(i18n.resolve(self.store.get().language))
        self.lang.changed.connect(self._on_lang)
        lay.addWidget(self.lang, 0, Qt.AlignmentFlag.AlignHCenter)
        return side

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(34, 8, 34, 22)
        row.setSpacing(10)
        cancel, save = self._button("btn.cancel"), self._button("btn.save", "primary")
        cancel.clicked.connect(self.close)
        save.clicked.connect(self._save)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(save)
        return row

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        winapi.dark_titlebar(int(self.winId()))
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)  # Enter in a spin box must not press a random button
            b.setDefault(False)
        if self._initial is None:
            self._initial = self.values()
        self._refresh_status(force=True)

    def _on_page(self, row: int) -> None:
        if row != self.CLICK_PAGE:
            self._stop_listen()
            self._hk_capture.cancel()
            self._hk_test.cancel()
        self._refresh_status(force=True)

    # ---------- building blocks ----------
    def _page(self, title_key: str, sub_key: str) -> tuple[Page, QVBoxLayout]:
        page = Page()
        page.lay.addWidget(self._label(title_key, "h1"))
        page.lay.addWidget(self._label(sub_key, "muted"))
        page.lay.addSpacing(14)
        return page, page.lay

    def _section(self, lay: QVBoxLayout, key: str) -> None:
        lay.addSpacing(10)
        lbl = QLabel()
        lbl.setProperty("role", "section")
        self._bind(lambda: lbl.setText(tr(key).upper()))
        lay.addWidget(lbl)
        lay.addSpacing(2)

    def _row(self, card: Card, key: str, control: QWidget, hint: str | None = None) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(18, 13, 18, 13)
        h.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(self._label(key))
        if hint:
            col.addWidget(self._label(hint, "muted"))
        h.addLayout(col, 1)
        h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        card.add(w)
        return control

    def _path_block(self, card: Card, cap_key: str) -> QLabel:
        """Caption on top, the path below in a monospace 'chip' that wraps instead of sticking to the right edge."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 13, 18, 15)
        v.setSpacing(8)
        v.addWidget(self._label(cap_key))
        chip = QLabel()
        chip.setWordWrap(True)
        chip.setStyleSheet(f"background:{theme.FIELD}; border:1px solid {theme.BORDER}; border-radius:8px; padding:8px 10px;"
                           f" color:{theme.TEXT}; font-family:Consolas,'Cascadia Mono',monospace; font-size:12px;")
        v.addWidget(chip)
        card.add(w)
        return chip

    def _switch(self, key: str) -> Switch:
        sw = Switch(bool(getattr(self.store.get(), key)))
        self._get[key] = sw.isChecked
        return sw

    def _spin(self, key: str, unit: str | None = None) -> Spin:
        lo, hi = RANGES[key]
        sp = Spin()
        sp.setRange(int(lo), int(hi))
        sp.setValue(int(getattr(self.store.get(), key)))
        if unit:
            self._bind(lambda: sp.setSuffix(" " + tr(unit)))
        self._get[key] = sp.value
        return sp

    def _dspin(self, key: str) -> DSpin:
        lo, hi = RANGES[key]
        sp = DSpin()
        sp.setRange(lo, hi)
        sp.setDecimals(1)
        sp.setSingleStep(0.5)
        sp.setSuffix(" %")
        sp.setValue(float(getattr(self.store.get(), key)))
        self._get[key] = sp.value
        return sp

    def _line(self, key: str, password: bool = False, width: int = 240) -> QLineEdit:
        le = QLineEdit(str(getattr(self.store.get(), key)))
        le.setFixedWidth(width)
        if password:
            le.setEchoMode(QLineEdit.EchoMode.Password)
        self._get[key] = le.text
        return le

    # ---------- pages ----------
    def _status_page(self) -> Page:
        page, lay = self._page("page.status", "page.status_sub")

        hero = Card()
        hw = QWidget()
        hl = QHBoxLayout(hw)
        hl.setContentsMargins(22, 20, 22, 20)
        hl.setSpacing(18)
        self._dot = StatusDot()
        col = QVBoxLayout()
        col.setSpacing(2)
        self._hero_title, self._hero_sub = QLabel(), QLabel()
        self._hero_title.setProperty("role", "big")
        self._hero_sub.setProperty("role", "muted")
        self._hero_sub.setWordWrap(True)
        col.addStretch(1)
        col.addWidget(self._hero_title)
        col.addWidget(self._hero_sub)
        col.addStretch(1)
        hl.addWidget(self._dot)
        hl.addLayout(col, 1)
        hero.add(hw)
        lay.addWidget(hero)

        lay.addSpacing(6)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self._tile_vals: dict[str, QLabel] = {}
        for key, cap in (("accepted", "st.accepted"), ("last", "st.last")):
            tile = Card()
            tw = QWidget()
            tl = QVBoxLayout(tw)
            tl.setContentsMargins(20, 16, 20, 16)
            tl.setSpacing(2)
            val = QLabel("—")
            val.setProperty("role", "big")
            tl.addWidget(val)
            tl.addWidget(self._label(cap, "muted"))
            tile.add(tw)
            tiles.addWidget(tile, 1)
            self._tile_vals[key] = val
        lay.addLayout(tiles)

        lay.addSpacing(6)
        info = Card()
        self._info_vals: dict[str, QLabel] = {}
        cs2 = QLabel()
        cs2.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._row(info, "st.cs2", cs2)
        self._info_vals["cs2"] = cs2
        for key, cap in (("log", "st.log"), ("data", "st.data")):
            self._info_vals[key] = self._path_block(info, cap)
        lay.addWidget(info)

        self._section(lay, "sec.how")
        how = Card()
        hw2 = QWidget()
        hv = QVBoxLayout(hw2)
        hv.setContentsMargins(18, 14, 18, 14)
        hv.addWidget(self._label("how.text", "muted"))
        how.add(hw2)
        lay.addWidget(how)
        lay.addStretch(1)
        return page

    def _general_page(self) -> Page:
        page, lay = self._page("page.general", "page.general_sub")
        self._section(lay, "sec.behaviour")
        c = Card()
        for key, label, hint in (("enabled", "g.enabled", "g.enabled_h"), ("autostart", "g.autostart", "g.autostart_h"),
                                 ("focus_cs2", "g.focus", "g.focus_h"), ("beep", "g.beep", "g.beep_h")):
            self._row(c, label, self._switch(key), hint)
        lay.addWidget(c)
        self._section(lay, "sec.safety")
        c2 = Card()
        self._row(c2, "g.cooldown", self._spin("cooldown_sec", "unit.s"), "g.cooldown_h")
        lay.addWidget(c2)
        lay.addStretch(1)
        return page

    def _click_page(self) -> Page:
        page, lay = self._page("page.click", "page.click_sub")
        cur = self.store.get()

        self._section(lay, "sec.calibrate")
        cal = Card()
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(18, 14, 18, 14)
        bl.setSpacing(12)
        bl.addWidget(self._label("cal.steps"))
        row = QHBoxLayout()
        row.setSpacing(12)
        self._listen_btn = QPushButton()
        self._listen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._listen_btn.clicked.connect(self._toggle_listen)
        self._listen_status = QLabel()
        self._listen_status.setWordWrap(True)
        row.addWidget(self._listen_btn)
        row.addWidget(self._listen_status, 1)
        bl.addLayout(row)
        bl.addWidget(self._label("cal.note", "muted"))
        cal.add(box)
        self._hk_capture, self._hk_test = HotkeyButton(cur.hk_capture), HotkeyButton(cur.hk_test)
        self._get["hk_capture"], self._get["hk_test"] = self._hk_capture.combo, self._hk_test.combo
        for b in (self._hk_capture, self._hk_test):
            b.changed.connect(lambda _c, btn=b: self._on_hotkey_changed(btn))
        self._row(cal, "cal.hk_capture", self._hk_capture, "cal.hk_capture_h")
        self._row(cal, "cal.hk_test", self._hk_test, "cal.hk_test_h")
        lay.addWidget(cal)
        self._bind(self._render_listen)

        self._section(lay, "sec.position")
        pos = Card()
        self._x, self._y = self._dspin("click_x_pct"), self._dspin("click_y_pct")
        self._row(pos, "pos.x", self._x, "pos.x_h")
        self._row(pos, "pos.y", self._y, "pos.y_h")
        self.prev = PositionPreview()
        self.prev.set_point(cur.click_x_pct, cur.click_y_pct)
        for sp in (self._x, self._y):
            sp.valueChanged.connect(lambda _v: self.prev.set_point(self._x.value(), self._y.value()))
        pw = QWidget()
        pl = QHBoxLayout(pw)
        pl.setContentsMargins(18, 16, 18, 16)
        pl.addStretch(1)
        pl.addWidget(self.prev)
        pl.addStretch(1)
        pos.add(pw)
        lay.addWidget(pos)

        self._section(lay, "sec.timing")
        tm = Card()
        self._row(tm, "t.delay", self._spin("click_delay_ms", "unit.ms"))
        self._row(tm, "t.count", self._spin("click_count"))
        self._row(tm, "t.interval", self._spin("click_interval_ms", "unit.ms"))
        lay.addWidget(tm)
        lay.addStretch(1)
        return page

    def _telegram_page(self) -> Page:
        page, lay = self._page("page.telegram", "page.telegram_sub")
        self._section(lay, "sec.bot")
        c = Card()
        self._row(c, "tg.enabled", self._switch("tg_enabled"), "tg.enabled_h")
        self._row(c, "tg.token", self._line("tg_token", password=True), "tg.token_h")
        self._row(c, "tg.chat", self._line("tg_chat_id"), "tg.chat_h")
        self._row(c, "tg.control", self._switch("tg_control"), "tg.control_h")
        tw = QWidget()
        tl = QHBoxLayout(tw)
        tl.setContentsMargins(18, 13, 18, 13)
        tl.setSpacing(14)
        btn = self._tg_btn = self._button("tg.test")
        btn.clicked.connect(self._test_telegram)
        self._tg_label = QLabel()
        self._tg_label.setProperty("role", "value")
        self._tg_label.setWordWrap(True)
        tl.addWidget(btn)
        tl.addWidget(self._tg_label, 1)
        c.add(tw)
        self._bind(self._render_tg)
        lay.addWidget(c)
        self._section(lay, "sec.notify")
        n = Card()
        for key, label in (("tg_on_match", "tg.on_match"), ("tg_on_ping", "tg.on_ping"), ("tg_on_expired", "tg.on_expired")):
            self._row(n, label, self._switch(key))
        lay.addWidget(n)
        lay.addStretch(1)
        return page

    def _friends_page(self) -> Page:
        page, lay = self._page("page.friends", "page.friends_sub")
        self._section(lay, "sec.invites")
        c = Card()
        self._row(c, "inv.enabled", self._switch("invite_enabled"), "inv.enabled_h")
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 13, 18, 15)
        v.setSpacing(8)
        v.addWidget(self._label("inv.ids"))
        v.addWidget(self._label("inv.ids_h", "muted"))
        self._inv_edit = QPlainTextEdit(str(self.store.get().invite_ids))
        self._inv_edit.setFixedHeight(96)
        self._inv_edit.setPlaceholderText("76561198000000001\nhttps://steamcommunity.com/profiles/76561198…")
        self._get["invite_ids"] = self._inv_edit.toPlainText
        self._inv_edit.textChanged.connect(self._render_inv)
        self._inv_debounce = QTimer(self)
        self._inv_debounce.setSingleShot(True)
        self._inv_debounce.setInterval(1200)
        self._inv_debounce.timeout.connect(self._lookup_inv)
        self._inv_edit.textChanged.connect(self._inv_debounce.start)
        v.addWidget(self._inv_edit)
        self._inv_names = QLabel()
        self._inv_names.setProperty("role", "value")
        self._inv_names.setWordWrap(True)
        v.addWidget(self._inv_names)
        c.add(w)
        sw = QWidget()
        sl = QHBoxLayout(sw)
        sl.setContentsMargins(18, 13, 18, 13)
        sl.setSpacing(14)
        self._inv_btn = self._button("inv.restart")
        self._inv_btn.clicked.connect(self._restart_steam)
        self._calib_btn = self._button("inv.calib")
        self._calib_btn.clicked.connect(self._calibrate)
        self._inv_result = QLabel()
        self._inv_result.setProperty("role", "value")
        self._inv_result.setWordWrap(True)
        sl.addWidget(self._inv_btn)
        sl.addWidget(self._calib_btn)
        sl.addWidget(self._inv_result, 1)
        c.add(sw)
        lay.addWidget(c)
        lay.addWidget(self._label("inv.note", "muted"))
        self._bind(self._render_inv)
        self._bind(self._render_inv_result)
        lay.addStretch(1)
        return page

    def _render_inv(self) -> None:
        text = self._inv_edit.toPlainText()
        ids = friends.all_ids(text, lookup=False)  # cache only: lookups start after typing stops (_lookup_inv)
        if not ids and not friends.vanity_names(text):
            self._inv_names.setText(tr("inv.names_empty"))
            return
        names = friends.friend_names(ids)
        lines = [tr("inv.names", names=", ".join(names)) if names else tr("inv.names_none")] if ids else []
        pending = friends.unresolved(text)
        if pending:  # vanity links (steamcommunity.com/id/...) are looked up online in the background
            lines.append(tr("inv.vanity", names=", ".join(pending)))
        self._inv_names.setText("\n".join(lines))

    def _lookup_inv(self) -> None:
        """Typing has stopped: resolve any profile links (online, in the background)."""
        friends.all_ids(self._inv_edit.toPlainText(), lookup=True)
        self._render_inv()

    def _render_inv_result(self) -> None:
        """Live state of the invite monitor (it runs in the background once the feature is saved as enabled)."""
        if self._inv_msg:
            self._inv_result.setText(tr(self._inv_msg))
            return
        st = self._invites.status if self._invites is not None else "off"
        name = getattr(self._invites, "port_name", "") if self._invites is not None else ""
        self._inv_result.setText(tr("inv.s." + (st if st in INV_STATUSES else "error"), name=name))

    def _restart_steam(self) -> None:
        """Two clicks: Steam must restart once to open its local debug port (the app prepared everything else)."""
        if not self._inv_confirm:
            self._inv_confirm = True
            self._inv_msg = "inv.restart_confirm"
            self._render_inv_result()
            QTimer.singleShot(6000, self._reset_inv_confirm)
            return
        self._inv_confirm = False
        self._inv_msg = "inv.restarting"
        self._inv_btn.setEnabled(False)
        self._render_inv_result()

        def work() -> None:
            try:
                ok = winapi.restart_steam()
            except Exception:
                ok = False
            try:
                self.inv_result.emit("done" if ok else "failed")
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _calibrate(self) -> None:
        """CS2 must be running: the app opens its box and learns where the user clicks OK."""
        if self._invites is None or self._inv_msg == "inv.calib_wait":
            return
        self._inv_msg = "inv.calib_wait"
        self._calib_btn.setEnabled(False)
        self._render_inv_result()

        def done(status: str) -> None:
            try:
                self.inv_calib.emit(status)
            except RuntimeError:
                pass  # dialog already closed

        self._invites.calibrate(done)

    def _on_calib(self, status: str) -> None:
        self._inv_msg = {"saved": "inv.calib_saved", "no_game": "inv.calib_no_game", "no_steam": "inv.calib_no_steam",
                         "timeout": "inv.calib_timeout", "outside": "inv.calib_outside",
                         "busy": "inv.calib_busy"}.get(status, "inv.calib_no_game")
        self._calib_btn.setEnabled(True)
        self._render_inv_result()
        QTimer.singleShot(10000, self._reset_inv_msg)

    def _reset_inv_msg(self) -> None:
        try:
            if self._inv_msg in ("inv.restart_failed", "inv.calib_saved", "inv.calib_no_game", "inv.calib_no_steam",
                                 "inv.calib_timeout", "inv.calib_outside", "inv.calib_busy"):
                self._inv_msg = None
                self._render_inv_result()
        except RuntimeError:
            pass  # dialog already closed

    def _reset_inv_confirm(self) -> None:
        try:
            if self._inv_confirm:
                self._inv_confirm = False
                self._inv_msg = None
                self._render_inv_result()
        except RuntimeError:
            pass  # dialog already closed

    def _on_inv_result(self, status: str) -> None:
        self._inv_msg = None if status == "done" else "inv.restart_failed"
        self._inv_btn.setEnabled(True)
        self._render_inv_result()
        if self._inv_msg:
            QTimer.singleShot(10000, self._reset_inv_msg)

    def _log_page(self) -> Page:
        page, lay = self._page("page.log", "page.log_sub")
        self._section(lay, "sec.log")
        c = Card()
        self._row(c, "log.auto", self._switch("log_auto"), "log.auto_h")
        path = self._line("log_path", width=190)
        browse = self._button("log.browse")
        browse.clicked.connect(lambda: self._browse(path))
        pw = QWidget()
        pl = QHBoxLayout(pw)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        pl.addWidget(path)
        pl.addWidget(browse)
        self._row(c, "log.path", pw, "log.path_h")
        nb = QWidget()
        nl = QVBoxLayout(nb)
        nl.setContentsMargins(18, 13, 18, 13)
        nl.addWidget(self._label("log.condebug", "muted"))
        c.add(nb)
        lay.addWidget(c)
        self._section(lay, "sec.detect")
        d = Card()
        self._row(d, "log.kw", self._line("match_keyword", width=160), "log.kw_h")
        lay.addWidget(d)
        lay.addStretch(1)
        return page

    # ---------- status ----------
    @staticmethod
    def _wrap_path(path: str) -> str:
        return path.replace("\\", r"\​")  # allow line breaks after backslashes

    def _refresh_status(self, force: bool = False) -> None:
        if not self.status_fn or not self.isVisible() or self.stack.currentIndex() != 0:
            return
        try:
            s = self.status_fn()
            state = s["state"]
            self._dot.set_state(state)
            self._hero_title.setText(tr(f"hero.{state}"))
            self._hero_sub.setText(tr(f"hero.{state}_sub"))
            self._tile_vals["accepted"].setText(str(s["accepted"]))
            last = s["last"]
            self._tile_vals["last"].setText(
                tr("st.last_fmt", id=last[0], time=time.strftime("%H:%M:%S", time.localtime(last[1]))) if last else tr("st.none"))
            cs2 = self._info_vals["cs2"]
            cs2.setText(tr("st.cs2_on" if s["cs2"] else "st.cs2_off"))
            cs2.setStyleSheet(f"color:{theme.OK if s['cs2'] else theme.MUTED}; font-weight:600;")
            self._info_vals["log"].setText(self._wrap_path(s["log"]) if s["log"] else tr("st.log_missing"))
            self._info_vals["data"].setText(self._wrap_path(s["data"]))
        except Exception:
            log.exception("status refresh failed")

    # ---------- click calibration (hotkeys live only while "listening") ----------
    def _set_listen(self, key: str, ok: bool | None = None, **kw: Any) -> None:
        self._listen_msg = (key, ok, kw)
        self._render_listen()

    def _render_listen(self) -> None:
        key, ok, kw = self._listen_msg
        color = {True: theme.OK, False: theme.WARN, None: theme.MUTED}[ok]
        self._listen_status.setStyleSheet(f"color:{color}; font-weight:600;")
        self._listen_status.setText(tr(key, **kw))
        active = self._poll_timer.isActive()
        self._listen_btn.setText(tr("cal.stop" if active else "cal.start"))
        self._listen_btn.setProperty("role", "primary" if active else "")
        self._listen_btn.style().unpolish(self._listen_btn)
        self._listen_btn.style().polish(self._listen_btn)

    def _toggle_listen(self) -> None:
        if self._poll_timer.isActive():
            self._stop_listen()
        else:
            self._start_listen()

    def _start_listen(self) -> None:
        self._poller = hotkeys.HotkeyPoller({"capture": self._hk_capture.combo(), "test": self._hk_test.combo()})
        self._poller.prime()  # keys still held from re-binding must not fire an action
        self._poll_timer.start()
        self._set_listen("cal.on", True, hotkey=self._hk_capture.combo())

    def _stop_listen(self) -> None:
        if not self._poll_timer.isActive():
            return
        self._poll_timer.stop()
        self._poller = None
        self._set_listen("cal.off")

    def _on_hotkey_changed(self, btn: HotkeyButton) -> None:
        if self._hk_capture.combo() == self._hk_test.combo():
            btn.set_combo(btn.prev)
            self._set_listen("hk.conflict", False)
            return
        if self._poll_timer.isActive():
            self._start_listen()

    def _poll(self) -> None:
        if self._poller is None or self._hk_capture.recording or self._hk_test.recording:
            return
        for name in self._poller.poll():
            if name == "capture":
                self._do_capture()
            else:
                self._do_test()

    @staticmethod
    def _beep(ok: bool) -> None:
        sounds.play("ok" if ok else "fail")

    def _do_capture(self) -> None:
        pos = accept.capture_cursor_pct()
        if pos is None:
            self._beep(False)
            self._set_listen("cal.fail", False)
            return
        self._x.setValue(pos[0])
        self._y.setValue(pos[1])
        self._beep(True)
        self._set_listen("cal.captured", True, x=pos[0], y=pos[1])

    def _do_test(self) -> None:
        ok = accept.click_pct(self._x.value(), self._y.value())
        self._beep(ok)
        if ok:
            self._set_listen("cal.test_ok", True, x=self._x.value(), y=self._y.value())
        else:
            self._set_listen("cal.test_fail", False)

    # ---------- telegram / log ----------
    def _render_tg(self) -> None:
        self._tg_label.setText(tr(self._tg_msg[0], **self._tg_msg[1]) if self._tg_msg else "")

    def _test_telegram(self) -> None:
        token, chat = str(self._get["tg_token"]()).strip(), str(self._get["tg_chat_id"]()).strip()
        self._tg_msg = ("tg.sending", {})
        self._render_tg()

        self._tg_btn.setEnabled(False)  # one test request at a time

        def work() -> None:
            ok, info = post_message(token, chat, tr("msg.test"))
            try:
                self.tg_result.emit(ok, info)
            except RuntimeError:
                pass  # dialog was closed while the request was in flight

        threading.Thread(target=work, daemon=True).start()

    def _on_tg_result(self, ok: bool, info: str) -> None:
        self._tg_msg = ("tg.ok", {}) if ok else ("tg.fail", {"info": info})
        self._tg_btn.setEnabled(True)
        self._render_tg()

    def _browse(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("log.pick"), edit.text(), "Log (*.log);;All (*)")
        if path:
            edit.setText(path)

    # ---------- save / close ----------
    def values(self) -> dict[str, Any]:
        return {k: g() for k, g in self._get.items()}

    def _save(self) -> None:
        self._stop_listen()
        initial = self._initial or {}
        # only what the user changed here: don't roll back toggles flipped in the tray meanwhile
        self.store.update(**{k: v for k, v in self.values().items() if initial.get(k, object()) != v})
        self.close()

    def reject(self) -> None:
        """Esc cancels a hotkey recording instead of closing the dialog."""
        btns = (self._hk_capture, self._hk_test)
        if any(b.recording or time.monotonic() - b.ended_at < 0.4 for b in btns):
            return
        super().reject()

    def closeEvent(self, e) -> None:  # noqa: N802
        self._stop_listen()
        super().closeEvent(e)
