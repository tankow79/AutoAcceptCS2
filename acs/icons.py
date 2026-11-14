"""Crisp line icons and flags drawn with QPainter (24x24 grid) — no font or image files."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

_SCALE = 2  # render at 2x so icons stay sharp on any display


def _pts(*xy: float) -> QPolygonF:
    return QPolygonF([QPointF(xy[i], xy[i + 1]) for i in range(0, len(xy), 2)])


def _draw(name: str, p: QPainter) -> None:
    if name == "status":
        p.drawPolyline(_pts(2, 12, 7, 12, 10, 5, 14, 19, 17, 12, 22, 12))
    elif name == "general":  # sliders
        for y, kx in ((6, 15), (12, 8), (18, 16)):
            p.drawLine(QPointF(3, y), QPointF(kx - 3, y))
            p.drawLine(QPointF(kx + 3, y), QPointF(21, y))
            p.drawEllipse(QPointF(kx, y), 2.4, 2.4)
    elif name == "click":  # crosshair
        p.drawEllipse(QPointF(12, 12), 6.5, 6.5)
        for x1, y1, x2, y2 in ((12, 2, 12, 6), (12, 18, 12, 22), (2, 12, 6, 12), (18, 12, 22, 12)):
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif name == "telegram":
        p.drawPolygon(_pts(3, 11, 21, 3, 14, 21, 11, 13))
        p.drawLine(QPointF(11, 13), QPointF(21, 3))
    elif name == "log":
        p.drawPolygon(_pts(6, 3, 14, 3, 19, 8, 19, 21, 6, 21))
        p.drawPolyline(_pts(14, 3, 14, 8, 19, 8))
        p.drawLine(QPointF(9, 13), QPointF(16, 13))
        p.drawLine(QPointF(9, 17), QPointF(16, 17))
    elif name == "friends":  # two people
        p.drawEllipse(QPointF(9, 8), 3.5, 3.5)
        p.drawArc(QRectF(2.5, 14, 13, 10), 0, 180 * 16)
        p.drawEllipse(QPointF(17, 9), 2.6, 2.6)
        p.drawArc(QRectF(13.5, 14.5, 9, 8), 0, 180 * 16)
    elif name == "folder":
        p.drawPolygon(_pts(3, 6, 9, 6, 11, 9, 21, 9, 21, 19, 3, 19))
    elif name == "power":
        p.drawArc(QRectF(5, 5, 14, 14), 120 * 16, 300 * 16)
        p.drawLine(QPointF(12, 3), QPointF(12, 11))


def pixmap(name: str, color: str, size: int = 20, width: float = 1.7) -> QPixmap:
    pm = QPixmap(size * _SCALE, size * _SCALE)
    pm.setDevicePixelRatio(_SCALE)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    p.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    _draw(name, p)
    p.end()
    return pm


def icon(name: str, color: str, active: str | None = None, size: int = 20) -> QIcon:
    """Icon with a normal colour and (optionally) another one for selected / hovered states."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal)
    if active:
        hi = pixmap(name, active, size)
        ic.addPixmap(hi, QIcon.Mode.Selected)
        ic.addPixmap(hi, QIcon.Mode.Active)
    return ic


def switch_icon(on: bool, accent: str, off: str, w: int = 30, h: int = 16) -> QIcon:
    """Tiny toggle-switch picture used for the checkable tray items."""
    pm = QPixmap(w * _SCALE, h * _SCALE)
    pm.setDevicePixelRatio(_SCALE)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(accent if on else off))
    p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
    p.setBrush(QColor("white"))
    d = h - 4
    p.drawEllipse(QRectF(w - d - 2 if on else 2, 2, d, d))
    p.end()
    return QIcon(pm)


def draw_flag(p: QPainter, code: str, r: QRectF) -> None:
    """Draw the flag for a language code ('en' = Union Jack, 'uk' = Ukraine) into rect r."""
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(r, 2.5, 2.5)
    p.setClipPath(clip)
    p.setPen(Qt.PenStyle.NoPen)
    if code == "uk":
        p.setBrush(QColor("#0057b7"))
        p.drawRect(QRectF(r.left(), r.top(), r.width(), r.height() / 2))
        p.setBrush(QColor("#ffd700"))
        p.drawRect(QRectF(r.left(), r.center().y(), r.width(), r.height() / 2))
    else:
        p.setBrush(QColor("#012169"))
        p.drawRect(r)
        tl, br, tr_, bl = r.topLeft(), r.bottomRight(), r.topRight(), r.bottomLeft()
        for color, w in (("white", r.height() * 0.22), ("#c8102e", r.height() * 0.09)):
            p.setPen(QPen(QColor(color), w))
            p.drawLine(tl, br)
            p.drawLine(tr_, bl)
        c = r.center()
        for color, w in (("white", r.height() * 0.38), ("#c8102e", r.height() * 0.22)):
            p.setPen(QPen(QColor(color), w))
            p.drawLine(QPointF(r.left(), c.y()), QPointF(r.right(), c.y()))
            p.drawLine(QPointF(c.x(), r.top()), QPointF(c.x(), r.bottom()))
    p.restore()
