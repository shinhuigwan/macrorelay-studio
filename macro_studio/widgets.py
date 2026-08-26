from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .theme import COLORS


class PageHeader(QtWidgets.QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(3)
        heading = QtWidgets.QLabel(title)
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        if subtitle:
            description = QtWidgets.QLabel(subtitle)
            description.setObjectName("PageSubtitle")
            description.setWordWrap(True)
            layout.addWidget(description)


class Card(QtWidgets.QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")


class WheelSafeSpinBox(QtWidgets.QSpinBox):
    """페이지 스크롤 중 숫자가 실수로 바뀌지 않는 스핀 박스입니다."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class MetricCard(Card):
    def __init__(self, label: str, value: str = "0", accent: str | None = None, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(4)
        self.value_label = QtWidgets.QLabel(value)
        self.value_label.setObjectName("Metric")
        if accent:
            self.value_label.setStyleSheet(f"color: {accent};")
        title = QtWidgets.QLabel(label)
        title.setObjectName("Muted")
        layout.addWidget(self.value_label)
        layout.addWidget(title)

    def set_value(self, value: int | str) -> None:
        self.value_label.setText(str(value))


class EmptyState(QtWidgets.QWidget):
    def __init__(self, title: str, detail: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setAlignment(QtCore.Qt.AlignCenter)
        icon = QtWidgets.QLabel("◇")
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setStyleSheet(f"font-size: 30pt; color: {COLORS['accent']};")
        label = QtWidgets.QLabel(title)
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setStyleSheet("font-size: 13pt; font-weight: 700;")
        layout.addWidget(icon)
        layout.addWidget(label)
        if detail:
            description = QtWidgets.QLabel(detail)
            description.setObjectName("Muted")
            description.setAlignment(QtCore.Qt.AlignCenter)
            description.setWordWrap(True)
            layout.addWidget(description)


def primary_button(text: str) -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton(text)
    button.setObjectName("Primary")
    return button


def danger_button(text: str) -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton(text)
    button.setObjectName("Danger")
    return button
