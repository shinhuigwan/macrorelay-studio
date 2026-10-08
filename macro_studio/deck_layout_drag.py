"""Lightweight, mouse-transparent drag preview; never moves executable widgets."""

from PySide6 import QtCore, QtGui, QtWidgets


class SlotDragPreview(QtWidgets.QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground)
        self.animation = QtCore.QVariantAnimation(self)
        self.animation.setDuration(120)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self._animate)
        self.animation.finished.connect(self.clear)
        self.clear()

    def clear(self):
        self.animation.stop()
        self.source = QtCore.QRect()
        self.target = QtCore.QRect()
        self.ghost = QtCore.QRect()
        self.swap = QtCore.QRect()
        self.pixmap = QtGui.QPixmap()
        self.swap_pixmap = QtGui.QPixmap()
        self.landing = False
        self.progress = 0.0
        self.hide()

    def begin(self, source, pixmap, offset):
        self.clear()
        self.setGeometry(self.parentWidget().rect())
        self.source = QtCore.QRect(source)
        self.ghost = QtCore.QRect(source)
        self.pixmap = pixmap
        self.offset = offset

    def move_preview(self, point, target, swap_pixmap=None):
        self.ghost.moveTopLeft(point - self.offset)
        self.target = QtCore.QRect(target) if target is not None else QtCore.QRect()
        self.swap_pixmap = swap_pixmap or QtGui.QPixmap()
        self.swap = QtCore.QRect(self.source)
        self.show()
        self.raise_()
        self.update()

    def land(self, destination):
        self.landing = True
        self.ghost_start = QtCore.QRect(self.ghost)
        self.destination = QtCore.QRect(destination)
        self.swap_start = QtCore.QRect(self.swap)
        self.swap_destination = QtCore.QRect(self.target)
        self.animation.start()

    def _animate(self, progress):
        self.progress = float(progress)
        for name, start, end in (("ghost", self.ghost_start, self.destination),
                                 ("swap", self.swap_start, self.swap_destination)):
            point = start.topLeft() + (end.topLeft() - start.topLeft()) * self.progress
            rect = QtCore.QRect(start)
            rect.moveTopLeft(point)
            setattr(self, name, rect)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        if not self.landing:
            # Cover the original image before drawing the swapped preview;
            # blending two labels makes their names appear superimposed.
            painter.setBrush(QtGui.QColor("#080F17"))
            painter.setPen(QtGui.QPen(QtGui.QColor("#5485A3"), 1, QtCore.Qt.DashLine))
            painter.drawRoundedRect(self.source.adjusted(1, 1, -1, -1), 8, 8)
            if not self.swap_pixmap.isNull():
                painter.setPen(QtCore.Qt.NoPen)
                painter.drawRoundedRect(self.target, 8, 8)
                painter.setOpacity(0.55)
                painter.drawPixmap(self.swap, self.swap_pixmap)
        painter.setOpacity(0.82 * (1.0 - self.progress) if self.landing else 0.82)
        painter.drawPixmap(self.ghost, self.pixmap)
        if self.landing and not self.swap_pixmap.isNull():
            painter.drawPixmap(self.swap, self.swap_pixmap)
        if not self.landing and not self.target.isEmpty() and self.target != self.source:
            painter.setOpacity(1.0)
            painter.setBrush(QtGui.QColor(56, 189, 248, 30))
            painter.setPen(QtGui.QPen(QtGui.QColor("#38BDF8"), 3))
            painter.drawRoundedRect(self.target.adjusted(2, 2, -2, -2), 8, 8)
            caption = "자리 교환" if not self.swap_pixmap.isNull() else "이동"
            font = painter.font()
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            width = QtGui.QFontMetrics(font).horizontalAdvance(caption) + 10
            badge = QtCore.QRect(self.target.center().x() - width // 2,
                                 self.target.bottom() - 19, width, 18)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#075985"))
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QtGui.QColor("#FFFFFF"))
            painter.drawText(badge, QtCore.Qt.AlignCenter, caption)
