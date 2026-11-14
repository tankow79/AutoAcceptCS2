"""Tray application entry point (PyQt6)."""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget, QWidgetAction

from . import i18n, icons, theme, winapi
from .accept import Accepter
from .i18n import tr
from .invite import InviteMonitor
from .icon import render
from .notifier import Notifier
from .tgcontrol import TgControl
from .settings import Settings, Store
from .watcher import NO_LOG, Watcher

log = logging.getLogger("acs")

_STATE_COLOR = {"on": theme.OK, "off": theme.MUTED, "nolog": theme.WARN}

def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            winapi.data_dir() / "app.log", maxBytes=256_000, backupCount=1, encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    except Exception:
        handler = logging.NullHandler()  # logging must never prevent the app from starting
    root.addHandler(handler)
    sys.excepthook = lambda *a: log.critical("uncaught", exc_info=a)  # also stops PyQt from aborting on slot errors
    threading.excepthook = lambda a: log.critical(
        "thread %s crashed", a.thread, exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


class MenuHeader(QWidget):
    """Top card of the tray menu: app icon, name and live status."""

    def __init__(self) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 18, 8)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(36, 36)
        col = QVBoxLayout()
        col.setSpacing(1)
        title = QLabel("AutoAccept CS2")
        title.setStyleSheet("color:#f8fafc; font-size:13px; font-weight:600; background:transparent;")
        self.status = QLabel()
        self.status.setStyleSheet(f"color:{theme.MUTED}; font-size:11px; background:transparent;")
        col.addWidget(title)
        col.addWidget(self.status)
        lay.addWidget(self.icon)
        lay.addLayout(col)

    def update_state(self, state: str, pm) -> None:
        self.icon.setPixmap(pm.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        self.status.setText(f'<span style="color:{_STATE_COLOR[state]}">●</span>  {tr("state." + state)}')


class TrayApp(QObject):
    status_changed = pyqtSignal()  # watcher thread -> GUI thread
    settings_changed = pyqtSignal(object)  # any thread (Telegram buttons, calibration) -> GUI thread

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        cfg_path = winapi.data_dir() / "config.json"
        winapi.migrate_legacy_config(cfg_path)
        self.store = Store(cfg_path)
        self.store.load()
        i18n.set_language(self.store.get().language)
        self.notifier = Notifier(self.store)
        self.accepter = Accepter(self.store, self.notifier)
        self.invites = InviteMonitor(self.store, self.notifier)
        self.tgcontrol = TgControl(self.store)
        self.watcher = Watcher(self.store, self.accepter, self.notifier, on_status=lambda _s: self.status_changed.emit(),
                               on_dialog=self.invites.on_dialog)
        self._dialog = None
        self._autostart_applied = self.store.get().autostart  # run() syncs the registry once at start-up

        self.tray = QSystemTrayIcon(self)
        self._build_menu()
        self.tray.activated.connect(self._on_activated)
        self.status_changed.connect(self.refresh)
        self.settings_changed.connect(self._on_settings)  # queued when emitted from another thread: widgets stay on the GUI thread
        self.store.on_change(self.settings_changed.emit)
        self.refresh()

    # ----- state -----
    def _state(self) -> str:
        if not self.store.get().enabled:
            return "off"
        return "nolog" if self.watcher.status == NO_LOG else "on"

    def _status(self) -> dict:
        w, a = self.watcher, self.accepter
        return {
            "state": self._state(),
            "accepted": a.accepted_count,
            "last": a.last_match,
            "cs2": winapi.find_cs2_window() is not None,
            "log": str(w.log_path) if w.log_path else "",
            "data": str(winapi.data_dir()),
        }

    def refresh(self) -> None:
        try:
            state = self._state()
            self.tray.setIcon(QIcon(theme.pixmap(render(64, state))))
            self.tray.setToolTip(tr("tray.tooltip", state=tr("state." + state)))
            self.header.update_state(state, theme.pixmap(render(96, state)))
            self.header.adjustSize()
            self.menu.adjustSize()  # the header text may have grown since the menu was last shown
            cfg = self.store.get()
            self.act_enabled.setIcon(icons.switch_icon(cfg.enabled, theme.ACCENT, "#2a3a57"))
            self.act_telegram.setIcon(icons.switch_icon(cfg.tg_enabled, theme.ACCENT, "#2a3a57"))
            self.act_autostart.setIcon(icons.switch_icon(cfg.autostart, theme.ACCENT, "#2a3a57"))
        except Exception:
            log.exception("refresh failed")

    def _on_settings(self, _cfg: Settings) -> None:
        cfg = self.store.get()  # queued signals can arrive out of order: always apply the newest state
        if cfg.autostart != self._autostart_applied:
            self._autostart_applied = cfg.autostart
            winapi.set_autostart(cfg.autostart)
        i18n.set_language(cfg.language)
        self._retranslate()
        self.refresh()

    def _retranslate(self) -> None:
        self.sec_control.setText(tr("tray.sec_control").upper())
        self.sec_app.setText(tr("tray.sec_app").upper())
        self.act_enabled.setText(tr("tray.auto"))
        self.act_telegram.setText(tr("tray.telegram"))
        self.act_settings.setText(tr("tray.settings"))
        self.act_autostart.setText(tr("tray.autostart"))
        self.act_quit.setText(tr("tray.quit"))
        if hasattr(self, "menu"):
            self.menu.adjustSize()  # texts differ in length between languages

    # ----- menu -----
    @staticmethod
    def _section_label(menu: QMenu) -> QLabel:
        lbl = QLabel()
        lbl.setStyleSheet(f"color:{theme.MUTED}; font-size:10px; font-weight:700; letter-spacing:1px;"
                          " background:transparent; padding:6px 14px 2px 14px;")
        act = QWidgetAction(menu)
        act.setDefaultWidget(lbl)
        menu.addAction(act)
        return lbl

    def _build_menu(self) -> None:
        menu = QMenu()
        menu.setStyleSheet(theme.MENU_QSS)
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.header = MenuHeader()
        card = QWidgetAction(menu)
        card.setDefaultWidget(self.header)
        menu.addAction(card)
        menu.addSeparator()

        self.sec_control = self._section_label(menu)
        self.act_enabled = QAction(menu)
        self.act_enabled.triggered.connect(lambda: self.store.update(enabled=not self.store.get().enabled))
        menu.addAction(self.act_enabled)
        self.act_telegram = QAction(menu)
        self.act_telegram.triggered.connect(lambda: self.store.update(tg_enabled=not self.store.get().tg_enabled))
        menu.addAction(self.act_telegram)
        menu.addSeparator()

        self.sec_app = self._section_label(menu)
        self.act_settings = QAction(icons.icon("general", theme.MUTED), "", menu)
        self.act_settings.triggered.connect(self.open_settings)
        menu.addAction(self.act_settings)
        self.act_autostart = QAction(menu)
        self.act_autostart.triggered.connect(lambda: self.store.update(autostart=not self.store.get().autostart))
        menu.addAction(self.act_autostart)
        menu.addSeparator()

        self.act_quit = QAction(icons.icon("power", theme.DANGER), "", menu)
        self.act_quit.triggered.connect(self.quit)
        menu.addAction(self.act_quit)
        self._retranslate()

        self.menu = menu
        self.tray.setContextMenu(menu)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            self.open_settings()

    def open_settings(self) -> None:
        try:
            if self._dialog is not None and self._dialog.isVisible():
                self._dialog.raise_()
                self._dialog.activateWindow()
                return
            from .ui import SettingsDialog

            dlg = SettingsDialog(self.store, self._status, invites=self.invites)
            dlg.destroyed.connect(lambda *_: setattr(self, "_dialog", None))
            self._dialog = dlg
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        except Exception:
            log.exception("could not open settings")
            self._dialog = None

    def quit(self) -> None:
        self.accepter.cancel()
        self.watcher.stop()
        self.invites.stop()
        self.tgcontrol.stop()
        self.notifier.stop()
        self.tray.hide()
        self.app.quit()

    def run(self) -> None:
        winapi.set_autostart(self.store.get().autostart)
        self.watcher.start()
        self.invites.start()
        self.tgcontrol.start()
        self._show_tray(0)
        if winapi.legacy_instance_running():
            log.warning("legacy CS2Watcher instance detected")
            QTimer.singleShot(4000, lambda: self.tray.showMessage(
                "AutoAccept CS2", tr("warn.legacy"), QSystemTrayIcon.MessageIcon.Warning, 15000))

    def _show_tray(self, attempt: int) -> None:
        """At sign-in the taskbar may not exist yet: keep retrying instead of running invisibly."""
        if QSystemTrayIcon.isSystemTrayAvailable() or attempt >= 60:
            self.tray.show()
            if attempt and not self.tray.isVisible():
                log.warning("tray icon could not be shown")
            return
        QTimer.singleShot(2000, lambda: self._show_tray(attempt + 1))


def main() -> None:
    if not winapi.single_instance():
        return
    _setup_logging()
    log.info("starting")
    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)  # the tray keeps the app alive
        app.setStyle("Fusion")
        app.setStyleSheet(theme.APP_QSS)
        app.setWindowIcon(QIcon(theme.pixmap(render(64))))
        tray = TrayApp(app)
        tray.run()
        app.exec()
    except Exception:
        log.critical("fatal", exc_info=True)
