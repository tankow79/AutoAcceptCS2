"""Midnight Cyan theme (same palette as the SCUM translator), refined: flat cards, no heavy effects."""
from __future__ import annotations

from PIL import Image
from PyQt6.QtGui import QImage, QPixmap

BG = "#0a101a"
SIDEBAR = "#0d1523"
SURFACE = "#111a2b"
SURFACE_ALT = "#16213a"
FIELD = "#0c1424"
BORDER = "#1e2b43"
BORDER_STRONG = "#2a3a57"
TEXT = "#e2e8f0"
MUTED = "#8294b0"
ACCENT = "#38bdf8"
ACCENT_DARK = "#0284c7"
OK = "#10b981"
WARN = "#f59e0b"
DANGER = "#ef4444"

_FONT = "font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;"

DIALOG_QSS = f"""
* {{ {_FONT} outline: 0; }}
QDialog {{ background-color: {BG}; color: {TEXT}; font-size: 13px; }}
QWidget#page, QWidget#right {{ background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel[role="h1"] {{ color: #f8fafc; font-size: 22px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {MUTED}; font-size: 12px; }}
QLabel[role="section"] {{ color: {MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel[role="value"] {{ color: {ACCENT}; font-weight: 600; }}
QLabel[role="big"] {{ color: #f8fafc; font-size: 26px; font-weight: 600; }}
QLabel[role="brand"] {{ color: #f8fafc; font-size: 14px; font-weight: 600; }}

QFrame#card {{ background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#card QLabel {{ background: transparent; border: none; }}
QFrame[role="sep"] {{ background-color: {BORDER}; border: none; max-height: 1px; min-height: 1px; }}
QWidget#sidebar {{ background-color: {SIDEBAR}; border-right: 1px solid {BORDER}; }}

QListWidget#nav {{ background: transparent; border: none; padding: 0; }}
QListWidget#nav::item {{ color: {MUTED}; padding: 0 12px; height: 40px; border-radius: 10px; margin: 2px 0; }}
QListWidget#nav::item:hover {{ background-color: {SURFACE}; color: {TEXT}; }}
QListWidget#nav::item:selected {{ background-color: rgba(56, 189, 248, 0.12); color: {ACCENT}; font-weight: 600; }}

QLineEdit, QSpinBox, QDoubleSpinBox {{ background-color: {FIELD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px;
    color: #f8fafc; padding: 6px 10px; min-height: 20px; selection-background-color: {ACCENT_DARK}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0px; height: 0px; border: none; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}

QPushButton {{ background-color: transparent; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: 9px; padding: 7px 18px; }}
QPushButton:hover {{ border-color: {ACCENT}; color: #ffffff; background-color: {SURFACE_ALT}; }}
QPushButton:pressed {{ background-color: {SURFACE}; }}
QPushButton[role="primary"] {{ background-color: {ACCENT}; border-color: {ACCENT}; color: #04121f; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background-color: #7dd3fc; border-color: #7dd3fc; color: #04121f; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4d6e; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
"""

MENU_QSS = f"""
QMenu {{ background-color: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: 12px; padding: 6px;
    {_FONT} font-size: 13px; }}
QMenu::item {{ padding: 8px 28px 8px 6px; border-radius: 8px; margin: 1px 0; }}
QMenu::item:selected {{ background-color: {SURFACE_ALT}; color: #ffffff; }}
QMenu::item:disabled {{ color: #5b6b85; }}
QMenu::icon {{ padding-left: 10px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 10px; }}
"""

APP_QSS = MENU_QSS + f"""
QToolTip {{ background-color: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 4px 6px; }}
QMessageBox {{ background-color: {BG}; color: {TEXT}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""


def pixmap(img: Image.Image) -> QPixmap:
    """PIL RGBA image -> QPixmap."""
    img = img.convert("RGBA")
    qimg = QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimg)
