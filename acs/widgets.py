"""Small custom widgets: toggle switch, language switch, status dot, position preview, hotkey button."""
from __future__ import annotations

import time

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QDoubleSpinBox, QFrame, QPushButton, QSpinBox, QVBoxLayout, QWidget

from . import hotkeys, icons, theme
from .i18n import tr


def _mix(a: str, b: str, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    return QColor(*(int(x + (y - x) * t) for x, y in zip(ca.getRgb()[:3], cb.getRgb()[:3], strict=True)))


class Switch(QAbstractButton):
    """iOS-style toggle. Animates only while toggling (no idle cost)."""

    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._t = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"t", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(44, 24)

    def _get_t(self) -> float:
        return self._t

    def _set_t(self, v: float) -> None:
        self._t = v
        self.update()

    t = pyqtProperty(float, _get_t, _set_t)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        w, h = self.width(), self.height()
        p.setBrush(_mix("#26344d", theme.ACCENT, self._t))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        d = h - 6
        x = 3 + self._t * (w - d - 6)
        p.setBrush(QColor("white") if self.isEnabled() else QColor("#9aa7bb"))
        p.drawEllipse(QRectF(x, 3, d, d))


class Spin(QSpinBox):
    """Compact number field without arrows; the wheel only works when focused (so page scrolling isn't hijacked)."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(120)

    def wheelEvent(self, e) -> None:  # noqa: N802
        super().wheelEvent(e) if self.hasFocus() else e.ignore()


class DSpin(QDoubleSpinBox):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(120)

    def wheelEvent(self, e) -> None:  # noqa: N802
        super().wheelEvent(e) if self.hasFocus() else e.ignore()


class Card(QFrame):
    """Rounded panel whose rows are divided by hairlines."""

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._n = 0

    def add(self, widget: QWidget) -> None:
        if self._n:
            sep = QFrame()
            sep.setProperty("role", "sep")
            sep.setFixedHeight(1)
            self._lay.addWidget(sep)
        self._lay.addWidget(widget)
        self._n += 1


class LangSwitch(QWidget):
    """Segmented  [flag EN | flag UA]  control. Emits the chosen language code."""

    changed = pyqtSignal(str)
    _ITEMS = (("en", "EN"), ("uk", "UA"))

    def __init__(self, current: str):
        super().__init__()
        self._cur = current
        self.setFixedSize(168, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_current(self, code: str) -> None:
        self._cur = code
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        code = self._ITEMS[0 if e.position().x() < self.width() / 2 else 1][0]
        if code != self._cur:
            self._cur = code
            self.update()
            self.changed.emit(code)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        p.setBrush(QColor(theme.FIELD))
        p.drawRoundedRect(r, 10, 10)
        half = r.width() / 2
        font = QFont("Segoe UI Variable Text")
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        for i, (code, label) in enumerate(self._ITEMS):
            seg = QRectF(r.left() + i * half + 3, r.top() + 3, half - 6, r.height() - 6)
            active = code == self._cur
            if active:
                p.setPen(QPen(QColor(theme.ACCENT), 1))
                p.setBrush(QColor(56, 189, 248, 36))
                p.drawRoundedRect(seg, 8, 8)
            icons.draw_flag(p, code, QRectF(seg.left() + 12, seg.center().y() - 7, 21, 14))
            p.setPen(QColor(theme.ACCENT if active else theme.MUTED))
            p.drawText(QRectF(seg.left() + 38, seg.top(), seg.width() - 38, seg.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)


class StatusDot(QWidget):
    """Big round status badge with a soft halo: check (ready), pause (off) or '!' (problem)."""

    def __init__(self):
        super().__init__()
        self.state = "on"
        self.setFixedSize(64, 64)

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        color = {"on": theme.OK, "off": theme.MUTED, "nolog": theme.WARN}[self.state]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(32, 32)
        p.setPen(Qt.PenStyle.NoPen)
        for r, a in ((31, 28), (25, 55)):
            col = QColor(color)
            col.setAlpha(a)
            p.setBrush(col)
            p.drawEllipse(c, r, r)
        p.setBrush(QColor(color))
        p.drawEllipse(c, 19, 19)
        pen = QPen(QColor("white"), 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if self.state == "on":
            p.drawPolyline(icons._pts(24, 33, 29.5, 38.5, 40, 26))
        elif self.state == "off":
            p.drawLine(QPointF(27, 25), QPointF(27, 39))
            p.drawLine(QPointF(37, 25), QPointF(37, 39))
        else:
            p.drawLine(QPointF(32, 24), QPointF(32, 34))
            p.drawPoint(QPointF(32, 40))


class PositionPreview(QWidget):
    """Mini game window showing where the Accept click will land."""

    def __init__(self):
        super().__init__()
        self.x_pct, self.y_pct = 50.0, 41.0
        self.setFixedSize(280, 158)

    def set_point(self, x: float, y: float) -> None:
        self.x_pct, self.y_pct = x, y
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        p.setBrush(QColor(theme.FIELD))
        p.drawRoundedRect(r, 10, 10)
        p.setPen(QColor(theme.MUTED))
        p.drawText(r.adjusted(0, 8, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, tr("pos.preview"))
        cx, cy = r.left() + r.width() * self.x_pct / 100, r.top() + r.height() * self.y_pct / 100
        guide = QColor(theme.ACCENT)
        guide.setAlpha(110)
        p.setPen(QPen(guide, 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(r.left() + 6, cy), QPointF(r.right() - 6, cy))
        p.drawLine(QPointF(cx, r.top() + 6), QPointF(cx, r.bottom() - 6))
        halo = QColor(theme.OK)
        halo.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(cx, cy), 12, 12)
        p.setBrush(QColor(theme.OK))
        p.setPen(QPen(QColor("white"), 2))
        p.drawEllipse(QPointF(cx, cy), 6, 6)


class HotkeyButton(QPushButton):
    """Shows a key combo; click it, then press the new combo (Esc cancels)."""

    changed = pyqtSignal(str)

    def __init__(self, combo: str):
        super().__init__(combo)
        self._combo = self.prev = combo
        self.recording = False
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self.setMinimumWidth(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._begin)
        self.ended_at = 0.0

    def combo(self) -> str:
        return self._combo

    def set_combo(self, combo: str) -> None:
        self._combo = combo
        self.setText(combo)

    def cancel(self) -> None:
        """Stop recording without changing the binding (page switch, language switch, Esc)."""
        if self.recording:
            self._timer.stop()
            self.recording = False
            self.ended_at = time.monotonic()
            self.setText(self._combo)

    def _begin(self) -> None:
        self.recording = True
        self.setText(tr("hk.press"))
        QTimer.singleShot(250, self._timer.start)  # let go of the Enter/Space that activated the button

    def _tick(self) -> None:
        res = hotkeys.record_step()
        if res is None:
            return
        self._timer.stop()
        self.recording = False
        self.ended_at = time.monotonic()
        if res and res != self._combo:
            self.prev = self._combo
            self.set_combo(res)
            self.changed.emit(res)
        else:
            self.setText(self._combo)
