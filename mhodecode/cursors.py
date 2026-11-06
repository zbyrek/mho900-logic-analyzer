"""The look of the cursors, after PulseView: a shaded span, flags, and the distance between them.

PulseView keeps the flags and the distance in its ruler above the traces, so here they have a strip of
their own above the time axis, where they cover nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

BAND_ALPHA = 34
LINE_WIDTH = 2
FLAG_MARGIN = 4  # room on both sides of the letter
ARROW_HEAD = 7
OVERLAY_DEPTH = 1000  # above every row of the view
LETTERS = ("A", "B")


def span_text(distance: float) -> str:
    """Time and frequency between the cursors. Two cursors at one place have no frequency."""
    text = pg.siFormat(distance, suffix="s")
    return f"{text} / {pg.siFormat(1 / distance, suffix='Hz')}" if distance > 0 else text


def flag_centers(positions: tuple[float, float], half: float) -> tuple[float, float]:
    """Where the flags go. Cursors too close for two flags get them side by side, so both letters show."""
    first, second = positions
    if abs(second - first) >= 2 * half:
        return positions
    middle = (first + second) / 2
    return (middle - half, middle + half) if first <= second else (middle + half, middle - half)


@dataclass(frozen=True)
class CursorView:
    """Everything needed to paint the cursors, in scene coordinates."""
    positions: tuple[float, float]
    left: float  # the part of the view that holds the traces
    right: float
    strip_top: float
    strip_bottom: float
    bottom: float
    text: str
    color: str
    flag_text: str
    font: QtGui.QFont


class CursorStrip(pg.GraphicsWidget):
    """The strip above the time axis. It only takes the mouse: the flags on it can be dragged."""

    def __init__(self, nearest: Callable[[float], int], drag: Callable[[int, float], None]):
        super().__init__()
        self.nearest, self.drag = nearest, drag
        self.dragged = 0
        self.setCursor(QtCore.Qt.SizeHorCursor)

    def mouseDragEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            event.ignore()
            return
        event.accept()
        if event.isStart():
            self.dragged = self.nearest(event.buttonDownScenePos().x())
        self.drag(self.dragged, event.scenePos().x())


class CursorOverlay(QtWidgets.QGraphicsObject):
    """Paints over all rows and takes no mouse events, so the lines and bars under it stay usable."""

    def __init__(self, describe: Callable[[], CursorView | None]):
        super().__init__()
        self.describe = describe
        self.area = QtCore.QRectF()
        self.setZValue(OVERLAY_DEPTH)
        self.setAcceptedMouseButtons(QtCore.Qt.NoButton)

    def refresh(self):
        scene = self.scene()
        if scene is not None and scene.sceneRect() != self.area:
            self.prepareGeometryChange()
            self.area = QtCore.QRectF(scene.sceneRect())
        self.update()

    def boundingRect(self):
        return self.area

    def shape(self):
        return QtGui.QPainterPath()

    def paint(self, painter, _option, _widget=None):
        view = self.describe()
        if view is None:
            return
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setFont(view.font)
        color = QtGui.QColor(view.color)
        metrics = QtGui.QFontMetricsF(view.font)
        height = metrics.height()
        low, high = sorted(view.positions)
        shown_low, shown_high = max(low, view.left), min(high, view.right)
        flag_top = view.strip_bottom - height
        if shown_high > shown_low:
            band = QtGui.QColor(color)
            band.setAlpha(BAND_ALPHA)
            painter.fillRect(QtCore.QRectF(shown_low, flag_top, shown_high - shown_low, view.bottom - flag_top), band)
        pen = QtGui.QPen(color, LINE_WIDTH)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for position in view.positions:
            if view.left <= position <= view.right:
                painter.drawLine(QtCore.QPointF(position, flag_top), QtCore.QPointF(position, view.bottom))
        flag_half = metrics.horizontalAdvance("A") / 2 + FLAG_MARGIN
        self._paint_arrow(painter, view, color, low + flag_half, high - flag_half, flag_top + height / 2)
        self._paint_text(painter, view, color, metrics, shown_low, shown_high)
        centers = flag_centers(view.positions, flag_half)
        for letter, position, center in zip(LETTERS, view.positions, centers):
            if view.left <= position <= view.right:
                self._paint_flag(painter, view, color, letter, center, flag_half, flag_top, height)

    @staticmethod
    def _paint_arrow(painter, view, color, start, end, y):
        """A shaft that reaches a head only where that end of the span is in view."""
        if end - start < 2 * ARROW_HEAD:
            return
        shaft_start, shaft_end = max(start, view.left), min(end, view.right)
        if shaft_end <= shaft_start:
            return
        painter.drawLine(QtCore.QPointF(shaft_start, y), QtCore.QPointF(shaft_end, y))
        painter.save()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color)
        for tip, direction, shown in ((start, 1, start >= view.left), (end, -1, end <= view.right)):
            if shown:
                painter.drawPolygon(QtGui.QPolygonF([
                    QtCore.QPointF(tip, y),
                    QtCore.QPointF(tip + direction * ARROW_HEAD, y - ARROW_HEAD / 2 - 1),
                    QtCore.QPointF(tip + direction * ARROW_HEAD, y + ARROW_HEAD / 2 + 1)]))
        painter.restore()

    @staticmethod
    def _paint_text(painter, view, color, metrics, shown_low, shown_high):
        """Over the middle of what is in view, and always whole, also when the cursors are close."""
        width = metrics.horizontalAdvance(view.text)
        middle = (shown_low + shown_high) / 2
        start = min(max(middle - width / 2, view.left), view.right - width)
        painter.setPen(color)
        painter.drawText(QtCore.QRectF(start, view.strip_top, width, metrics.height()), QtCore.Qt.AlignCenter, view.text)

    @staticmethod
    def _paint_flag(painter, view, color, letter, position, half, top, height):
        rect = QtCore.QRectF(position - half, top, half * 2, height)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QtGui.QColor(view.flag_text))
        painter.drawText(rect, QtCore.Qt.AlignCenter, letter)
