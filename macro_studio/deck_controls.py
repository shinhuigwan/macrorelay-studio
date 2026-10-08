"""Compact, touch-friendly controls outside the Deck's slot area."""

from PySide6 import QtCore, QtGui, QtWidgets

DECK_FOOTER_HEIGHT = 24


def control_icon(kind: str) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(28, 28)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtGui.QPen(QtGui.QColor("#E2E8F0"), 1.8,
                            QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    if kind == "home":
        painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in
                                             ((3, 13), (14, 4), (25, 13))]))
        painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in
                                             ((6, 11), (6, 24), (22, 24), (22, 11))]))
        painter.drawRect(11, 16, 6, 8)
    elif kind == "mode":
        painter.drawRoundedRect(7, 12, 14, 12, 3, 3)
        painter.drawArc(QtCore.QRectF(9, 3, 10, 16), 0, 180 * 16)
        painter.drawLine(14, 17, 14, 20)
    else:
        for x in (5, 16):
            for y in (5, 16):
                painter.drawRoundedRect(x, y, 7, 7, 1, 1)
    painter.end()
    return QtGui.QIcon(pixmap)


class DeckControlsPopup(QtWidgets.QFrame):
    action_requested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_NoMouseReplay)
        self.setObjectName("DeckControlsPopup")
        self.setStyleSheet("""
            QFrame#DeckControlsPopup { background: #10151D; border: 1px solid #526172; border-radius: 12px; }
            QLabel { color: #F1F5F9; border: none; font-weight: 600; }
            QToolButton { color: #CBD5E1; background: transparent; border: none; }
            QToolButton#DeckControlAction { color: #E2E8F0; background: #202833; border: 1px solid #465260;
                          border-radius: 8px; padding: 8px; }
            QToolButton#DeckControlAction:hover { background: #314253; border-color: #38BDF8; }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        title_row = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel()
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        close = QtWidgets.QToolButton()
        self.close_button = close
        close.setText("×")
        close.setFixedSize(30, 30)
        close.setAccessibleName("설정 메뉴 닫기")
        close.clicked.connect(self.hide)
        title_row.addWidget(close)
        layout.addLayout(title_row)
        actions = QtWidgets.QHBoxLayout()
        self.buttons = {}
        for key, text in (("home", "홈"), ("mode", "자동 / 고정"), ("layout", "배치 편집")):
            button = QtWidgets.QToolButton()
            button.setObjectName("DeckControlAction")
            button.setText(text)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            button.setIcon(control_icon(key))
            button.setIconSize(QtCore.QSize(24, 24))
            button.setMinimumSize(88, 70)
            button.clicked.connect(lambda checked=False, action=key: self._request(action))
            self.buttons[key] = button
            actions.addWidget(button)
        layout.addLayout(actions)

    def _request(self, action: str) -> None:
        self.hide()
        self.action_requested.emit(action)

    def set_preset(self, name: str, pinned: bool) -> None:
        self.title_label.setText(name)
        self.buttons["mode"].setText("자동으로 전환" if pinned else "프리셋 고정")
        self.buttons["mode"].setToolTip("현재 프리셋을 유지하거나 활성 창에 맞춰 자동으로 전환합니다.")

    def popup_at(self, anchor: QtWidgets.QWidget) -> None:
        rect = QtCore.QRect(anchor.mapToGlobal(QtCore.QPoint()), anchor.size())
        screen = QtGui.QGuiApplication.screenAt(rect.center()) or anchor.screen()
        area = screen.availableGeometry()
        self.setMaximumWidth(area.width())
        self.adjustSize()
        # Prefer opening below the corner; at the bottom edge open upward.
        y = rect.bottom() + 5
        if y + self.height() > area.bottom() + 1:
            y = rect.top() - self.height() - 4
        x = max(area.left(), min(rect.left(), area.right() - self.width() + 1))
        y = max(area.top(), min(y, area.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
        self.raise_()
