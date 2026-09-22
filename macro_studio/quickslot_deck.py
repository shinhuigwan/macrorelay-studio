"""QuickSlot Stream Deck Standalone Window for MacroRelay Studio.

Features:
- Right-click context menu with custom icon loading and icon editor dialog (SlotIconEditDialog).
- Full tile card stretch mode for custom icon images (up to 300px or full stretch fill).
- Adjustable text position (Bottom, Top, Center, Hidden), text font size (8-24pt), and spacing (0-40px).
- Real-time live preview inside the edit dialog.
- Config persistence in .quickslot_deck_config.json.
- Frameless dark titlebar with logo, status badge, top actions, and window controls.
- Touch hold radial menu and drag-to-move window controls.
"""

from __future__ import annotations

import base64
import copy
import ctypes
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import webbrowser
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import winreg
except ImportError:
    winreg = None

from PySide6 import QtCore, QtGui, QtWidgets

from macro_studio.repository import MacroRepository
from macro_studio.theme import stylesheet


REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "MacroRelayQuickSlot"
PRESET_ICON_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".ico"}
BASE_TILE_SIDE = 190
MIN_TILE_SIDE = 48
WINDOW_PADDING = 16
PRESET_STYLE_KEYS = ("theme_index", "tile_scale", "tile_gap", "tile_radius", "hover_glow", "empty_slot_opacity", "show_empty_slots")
QUICKSLOT_DOUBLE_CLICK_INTERVAL_MS = 240


def glass_dialog_stylesheet() -> str:
    """Light frosted controls matching the runtime radial wheel."""
    return """
        QDialog, QScrollArea, QScrollArea > QWidget > QWidget {
            background: #F4F7FB;
            color: #172033;
        }
        QLabel, QCheckBox, QRadioButton, QGroupBox {
            color: #172033;
            background: transparent;
        }
        QGroupBox {
            border: 1px solid #CBD5E1;
            border-radius: 12px;
            margin-top: 10px;
            padding-top: 10px;
            font-weight: 700;
        }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTextEdit {
            color: #172033;
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid #C5CEDB;
            border-radius: 8px;
            padding: 6px 9px;
            selection-background-color: #B9DAFF;
        }
        QComboBox::drop-down { border: none; width: 24px; }
        QComboBox QAbstractItemView {
            color: #172033;
            background: #FFFFFF;
            border: 1px solid #CBD5E1;
            selection-background-color: #DCEEFF;
        }
        QPushButton, QToolButton {
            color: #263449;
            background: rgba(255, 255, 255, 0.94);
            border: 1px solid #BCC7D6;
            border-radius: 9px;
            padding: 7px 13px;
            font-weight: 700;
        }
        QPushButton:hover, QToolButton:hover {
            background: #E7F2FF;
            border-color: #7CB7F2;
        }
        QPushButton:pressed, QToolButton:pressed { background: #CFE7FF; }
        QPushButton:disabled { color: #9AA7B8; background: #E9EEF4; }
        QSlider::groove:horizontal {
            height: 5px; background: #D6DEE9; border-radius: 2px;
        }
        QSlider::handle:horizontal {
            width: 16px; margin: -6px 0; border-radius: 8px;
            background: #78B7F2; border: 2px solid #FFFFFF;
        }
        QScrollBar:vertical, QScrollBar:horizontal { background: #EDF1F6; border: none; }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #B9C5D4; border-radius: 5px; min-height: 24px; min-width: 24px;
        }
        QTabWidget::pane {
            border: 1px solid #C8D2DF;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.82);
            padding: 10px;
        }
        QTabBar::tab {
            color: #68768A;
            background: #E9EEF5;
            border: 1px solid #CDD6E2;
            padding: 9px 15px;
            font-weight: 700;
            border-top-left-radius: 9px;
            border-top-right-radius: 9px;
            margin-right: 4px;
        }
        QTabBar::tab:selected {
            color: #172033;
            background: #FFFFFF;
            border-bottom: 2px solid #75B8F7;
        }
    """


def position_dialog_beside(parent: QtWidgets.QWidget, dialog: QtWidgets.QDialog, gap: int = 18) -> QtCore.QPoint:
    """Place tools beside the deck, preferring its left side without leaving the screen."""
    dialog.ensurePolished()
    target_size = dialog.size().expandedTo(dialog.minimumSizeHint()).expandedTo(dialog.minimumSize())
    dialog.resize(target_size)
    parent_rect = parent.frameGeometry()
    screen = QtGui.QGuiApplication.screenAt(parent_rect.center()) or parent.screen()
    available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1920, 1080)
    left_x = parent_rect.left() - dialog.width() - gap
    right_x = parent_rect.right() + gap + 1
    if left_x >= available.left():
        x = left_x
    elif right_x + dialog.width() - 1 <= available.right():
        x = right_x
    else:
        left_space = parent_rect.left() - available.left()
        right_space = available.right() - parent_rect.right()
        x = available.left() if left_space >= right_space else available.right() - dialog.width() + 1
    y = max(available.top(), min(parent_rect.top(), available.bottom() - dialog.height() + 1))
    point = QtCore.QPoint(x, y)
    dialog.move(point)
    return point


def quickslot_preset_icon_paths(root: Path) -> List[Path]:
    preset_dir = root / "branding" / "quickslot-icons"
    if not preset_dir.is_dir():
        return []
    return sorted(
        (path for path in preset_dir.iterdir() if path.is_file() and path.suffix.lower() in PRESET_ICON_EXTENSIONS),
        key=lambda path: path.name.casefold(),
    )


def quickslot_user_icon_paths(root: Path) -> List[Path]:
    user_dir = root / "quickslot-user-icons"
    if not user_dir.is_dir():
        return []
    return sorted(
        (path for path in user_dir.iterdir() if path.is_file() and path.suffix.lower() in PRESET_ICON_EXTENSIONS),
        key=lambda path: path.name.casefold(),
    )


def save_quickslot_user_icons(root: Path, source_paths: List[str]) -> List[Path]:
    user_dir = root / "quickslot-user-icons"
    user_dir.mkdir(parents=True, exist_ok=True)
    saved: List[Path] = []
    for source_text in source_paths:
        source = Path(source_text)
        if not source.is_file() or source.suffix.lower() not in PRESET_ICON_EXTENSIONS:
            continue
        if source.stat().st_size > 10 * 1024 * 1024:
            continue
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:12]
        safe_stem = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in source.stem).strip("-") or "icon"
        destination = user_dir / f"{safe_stem[:48]}-{digest}{source.suffix.lower()}"
        if not destination.exists():
            shutil.copy2(source, destination)
        saved.append(destination)
    return saved


def install_quickslot_desktop_shortcut(root: Path) -> Path:
    """Create or update the portable QuickStream desktop shortcut."""
    installer = root / "tools" / "install_quickslot_shortcut.ps1"
    launcher = root / "run_quickslot.ps1"
    if not installer.exists() or not launcher.exists():
        raise FileNotFoundError("퀵스트림 설치 파일이 누락되었습니다.")
    completed = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(installer), "-Root", str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "바로가기를 만들지 못했습니다.").strip()
        raise RuntimeError(detail)
    output = (completed.stdout or "").strip().splitlines()
    if not output:
        raise RuntimeError("생성된 바로가기 경로를 확인하지 못했습니다.")
    return Path(output[-1].strip())


def is_autostart_enabled() -> bool:
    if not winreg:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return bool(val)
    except Exception:
        return False


def set_autostart_enabled(enabled: bool, root: Optional[Path] = None) -> bool:
    if not winreg:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
        if enabled:
            app_root = root or Path(__file__).resolve().parents[1]
            launcher = app_root / "run_quickslot.ps1"
            cmd = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{launcher}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def load_pixmap_from_config(config: Dict[str, Any]) -> QtGui.QPixmap:
    """Load QPixmap from Base64 image_data or fallback to image_path file."""
    image_data = str(config.get("image_data", "")).strip()
    if image_data:
        if "," in image_data:
            image_data = image_data.split(",", 1)[1]
        try:
            raw_bytes = base64.b64decode(image_data)
            pix = QtGui.QPixmap()
            if pix.loadFromData(raw_bytes) and not pix.isNull():
                return pix
        except Exception:
            pass

    image_path = str(config.get("image_path", "")).strip()
    if image_path and os.path.exists(image_path):
        try:
            pix = QtGui.QPixmap(image_path)
            if not pix.isNull():
                return pix
        except Exception:
            pass

    return QtGui.QPixmap()


def encode_image_file_to_base64(file_path: str) -> str:
    """Encode an image file on disk to a Base64 data URI string."""
    try:
        p = Path(file_path)
        if p.exists() and p.is_file() and p.stat().st_size <= 10 * 1024 * 1024:
            data = p.read_bytes()
            ext = p.suffix.lower().lstrip(".") or "png"
            if ext == "jpg":
                ext = "jpeg"
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:image/{ext};base64,{b64}"
    except Exception:
        pass
    return ""


class TouchSwipeWidget(QtWidgets.QWidget):
    """Widget container supporting touch drag / swipe gesture for page navigation."""

    swipe_left = QtCore.Signal()   # Next page
    swipe_right = QtCore.Signal()  # Prev page

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._drag_start: Optional[QtCore.QPoint] = None
        self._is_swiping = False
        self.setAttribute(QtCore.Qt.WA_AcceptTouchEvents, True)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            win = self.window()
            if hasattr(win, "_start_window_drag_candidate") and win._start_window_drag_candidate(event):
                event.accept()
                return

            self._drag_start = event.pos()
            self._is_swiping = False
            if hasattr(win, "_start_mouse_hold_check"):
                win._start_mouse_hold_check(event.globalPos())
        elif event.button() == QtCore.Qt.RightButton:
            win = self.window()
            if hasattr(win, "radial_menu"):
                win.radial_menu.popup_at(event.globalPos())
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            win = self.window()
            if hasattr(win, "_show_preset_radial"):
                win._show_preset_radial(event.globalPos(), sticky=True)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        win = self.window()
        if hasattr(win, "_handle_window_drag_move") and win._handle_window_drag_move(event):
            event.accept()
            return

        if self._drag_start and (event.buttons() & QtCore.Qt.LeftButton):
            delta = event.pos() - self._drag_start
            if abs(delta.x()) > 20 and abs(delta.x()) > abs(delta.y()):
                self._is_swiping = True
                if hasattr(win, "_cancel_mouse_hold_check"):
                    win._cancel_mouse_hold_check()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        win = self.window()
        if hasattr(win, "_handle_window_drag_release") and win._handle_window_drag_release(event):
            event.accept()
            return

        if hasattr(win, "_cancel_mouse_hold_check"):
            win._cancel_mouse_hold_check()
        if self._drag_start and event.button() == QtCore.Qt.LeftButton:
            delta = event.pos() - self._drag_start
            if self._is_swiping and abs(delta.x()) > 60:
                if delta.x() < 0:
                    self.swipe_left.emit()
                else:
                    self.swipe_right.emit()
            self._drag_start = None
            self._is_swiping = False
        super().mouseReleaseEvent(event)


class DraggableTextLabel(QtWidgets.QLabel):
    """QLabel supporting click and drag positioning within parent frame."""

    text_moved = QtCore.Signal(float, float)  # x_percent, y_percent

    def __init__(self, text: str = "", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(text, parent)
        self._drag_start_pos: Optional[QtCore.QPoint] = None
        self._label_start_pos: Optional[QtCore.QPoint] = None
        self.setCursor(QtCore.Qt.SizeAllCursor)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint()
            self._label_start_pos = self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_start_pos and self._label_start_pos and (event.buttons() & QtCore.Qt.LeftButton):
            delta = event.globalPosition().toPoint() - self._drag_start_pos
            new_pos = self._label_start_pos + delta

            parent = self.parentWidget()
            if parent:
                max_x = max(1, parent.width() - self.width())
                max_y = max(1, parent.height() - self.height())

                clamped_x = max(0, min(max_x, new_pos.x()))
                clamped_y = max(0, min(max_y, new_pos.y()))

                self.move(clamped_x, clamped_y)

                x_pct = (clamped_x / max_x * 100.0)
                y_pct = (clamped_y / max_y * 100.0)
                self.text_moved.emit(x_pct, y_pct)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_pos = None
            self._label_start_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SlotIconEditDialog(QtWidgets.QDialog):
    """Dialog for editing slot icon image, full tile stretch, drag text position, font size, and spacing."""

    live_config_changed = QtCore.Signal(int, object)

    def __init__(
        self,
        slot_index: int,
        macro_name: str,
        icon_config: Optional[Dict[str, Any]] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ):
        super().__init__(parent)
        self.slot_index = slot_index
        self.macro_name = macro_name or f"슬롯 #{slot_index + 1}"
        self.icon_config = dict(icon_config or {})
        repository = getattr(parent, "repository", None)
        self.app_root = Path(getattr(repository, "root", Path(__file__).resolve().parents[1]))
        self.preset_buttons: Dict[str, QtWidgets.QToolButton] = {}

        self.setWindowTitle(f"아이콘 및 타일 상세 편집 - 슬롯 #{slot_index + 1}")
        self.setMinimumSize(720, 700)
        self.resize(760, 840)
        self.setStyleSheet(glass_dialog_stylesheet())

        self._init_ui()
        self._load_current_values()

    def _init_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(20, 18, 20, 18)
        root_layout.setSpacing(12)

        # Title
        header_title = QtWidgets.QLabel(f"🖼️ 슬롯 #{self.slot_index + 1} 커스텀 아이콘 & 텍스트 편집")
        header_title.setStyleSheet("font-size: 14pt; font-weight: 800; color: #172033;")
        header_title.setMinimumHeight(32)
        root_layout.addWidget(header_title)

        content_scroll = QtWidgets.QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content_host = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content_host)
        layout.setContentsMargins(2, 2, 8, 2)
        layout.setSpacing(14)
        content_scroll.setWidget(content_host)
        root_layout.addWidget(content_scroll, 1)

        # Form Card
        form_card = QtWidgets.QFrame()
        form_card.setObjectName("IconFormCard")
        form_card.setStyleSheet("QFrame#IconFormCard { background: rgba(255,255,255,0.88); border: 1px solid #CBD5E1; border-radius: 12px; padding: 12px; }")
        form_layout = QtWidgets.QFormLayout(form_card)
        form_layout.setSpacing(12)
        form_layout.setHorizontalSpacing(16)

        # Image File Picker
        file_box = QtWidgets.QHBoxLayout()
        self.file_edit = QtWidgets.QLineEdit()
        self.file_edit.setPlaceholderText("이미지 파일 경로 (.png, .jpg, .ico, .svg)")
        self.file_edit.textChanged.connect(self._update_preview)

        btn_browse = QtWidgets.QPushButton("📁 파일 선택...")
        btn_browse.setStyleSheet("background: #FFFFFF; border: 1px solid #B9C6D6; color: #263449; font-weight: 700; padding: 6px 12px; border-radius: 8px;")
        btn_browse.clicked.connect(self._browse_image)

        file_box.addWidget(self.file_edit, 1)
        file_box.addWidget(btn_browse)
        form_layout.addRow("아이콘 이미지 파일:", file_box)

        preset_section = QtWidgets.QFrame()
        preset_section.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.78); border: 1px solid #CBD5E1; border-radius: 11px; }"
        )
        preset_section_layout = QtWidgets.QVBoxLayout(preset_section)
        preset_section_layout.setContentsMargins(12, 10, 12, 10)
        preset_section_layout.setSpacing(7)
        preset_scroll = QtWidgets.QScrollArea()
        preset_scroll.setWidgetResizable(True)
        preset_scroll.setFixedHeight(112)
        preset_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        preset_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        preset_scroll.setStyleSheet(
            "QScrollArea { background: #EDF3F9; border: 1px solid #CBD5E1; border-radius: 9px; }"
        )
        self.preset_host = QtWidgets.QWidget()
        self.preset_row = QtWidgets.QHBoxLayout(self.preset_host)
        self.preset_row.setContentsMargins(8, 8, 8, 8)
        self.preset_row.setSpacing(8)
        preset_scroll.setWidget(self.preset_host)
        preset_title = QtWidgets.QLabel("아이콘 보관함 · 좌우로 넘겨서 선택")
        preset_title.setStyleSheet("color: #334155; font-weight: 700; border: none;")
        add_icons_button = QtWidgets.QPushButton("＋ 아이콘 여러 개 저장")
        add_icons_button.setToolTip("여러 이미지 파일을 선택해 프로그램 아이콘 보관함에 저장합니다.")
        add_icons_button.clicked.connect(self._import_icons_to_library)
        preset_header = QtWidgets.QHBoxLayout()
        preset_header.addWidget(preset_title)
        preset_header.addStretch(1)
        preset_header.addWidget(add_icons_button)
        preset_section_layout.addLayout(preset_header)
        preset_section_layout.addWidget(preset_scroll)
        self._reload_icon_gallery()

        # Full Stretch Checkbox
        self.stretch_check = QtWidgets.QCheckBox("타일 카드 전체 가득 채우기 (Full Tile Stretch)")
        self.stretch_check.setStyleSheet("font-weight: 700; color: #5547C8;")
        self.stretch_check.toggled.connect(self._update_preview)
        form_layout.addRow("", self.stretch_check)

        # Show Text Checkbox
        self.show_text_check = QtWidgets.QCheckBox("매크로 이름 텍스트 표시하기 (Show Text)")
        self.show_text_check.setStyleSheet("font-weight: 700; color: #168566;")
        self.show_text_check.setChecked(True)
        self.show_text_check.toggled.connect(self._update_preview)
        form_layout.addRow("", self.show_text_check)

        # Emoji / Badge Text
        self.emoji_edit = QtWidgets.QLineEdit()
        self.emoji_edit.setPlaceholderText("이모지 또는 배지 텍스트 (예: 🖱️, 🚀, OB, ⭐)")
        self.emoji_edit.textChanged.connect(self._update_preview)
        form_layout.addRow("이모지 / 배지 텍스트:", self.emoji_edit)

        # Icon Size (16px ~ 300px)
        size_box = QtWidgets.QHBoxLayout()
        self.size_spin = QtWidgets.QSpinBox()
        self.size_spin.setRange(16, 300)
        self.size_spin.setValue(40)
        self.size_spin.setSuffix(" px")

        self.size_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.size_slider.setRange(16, 300)
        self.size_slider.setValue(40)

        self.size_spin.valueChanged.connect(self.size_slider.setValue)
        self.size_slider.valueChanged.connect(self.size_spin.setValue)
        self.size_spin.valueChanged.connect(self._update_preview)

        size_box.addWidget(self.size_slider, 1)
        size_box.addWidget(self.size_spin)
        form_layout.addRow("아이콘 크기 (Icon Size):", size_box)

        # Text Position ComboBox (Bottom, Top, Center, Custom, Hidden)
        self.pos_combo = QtWidgets.QComboBox()
        self.pos_combo.addItems(["하단 (Bottom)", "상단 (Top)", "중앙 (Center)", "커스텀 드래그 (Custom)", "숨김 / 제거 (Hidden)"])
        self.pos_combo.currentIndexChanged.connect(self._on_pos_combo_changed)
        form_layout.addRow("매크로 이름 텍스트 위치:", self.pos_combo)

        # Custom Drag X & Y percent controls
        pos_xy_box = QtWidgets.QHBoxLayout()
        self.x_spin = QtWidgets.QSpinBox()
        self.x_spin.setRange(0, 100)
        self.x_spin.setValue(50)
        self.x_spin.setPrefix("X: ")
        self.x_spin.setSuffix(" %")

        self.y_spin = QtWidgets.QSpinBox()
        self.y_spin.setRange(0, 100)
        self.y_spin.setValue(85)
        self.y_spin.setPrefix("Y: ")
        self.y_spin.setSuffix(" %")

        self.x_spin.valueChanged.connect(self._on_xy_changed)
        self.y_spin.valueChanged.connect(self._on_xy_changed)

        pos_xy_box.addWidget(self.x_spin)
        pos_xy_box.addWidget(self.y_spin)
        form_layout.addRow("텍스트 X/Y 수동 위치:", pos_xy_box)

        # Font Size (8pt ~ 24pt)
        font_box = QtWidgets.QHBoxLayout()
        self.font_spin = QtWidgets.QSpinBox()
        self.font_spin.setRange(8, 24)
        self.font_spin.setValue(12)
        self.font_spin.setSuffix(" pt")

        self.font_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.font_slider.setRange(8, 24)
        self.font_slider.setValue(12)

        self.font_spin.valueChanged.connect(self.font_slider.setValue)
        self.font_slider.valueChanged.connect(self.font_spin.setValue)
        self.font_spin.valueChanged.connect(self._update_preview)

        font_box.addWidget(self.font_slider, 1)
        font_box.addWidget(self.font_spin)
        form_layout.addRow("텍스트 글자 크기 (Font Size):", font_box)

        # Spacing (0px ~ 40px)
        spacing_box = QtWidgets.QHBoxLayout()
        self.spacing_spin = QtWidgets.QSpinBox()
        self.spacing_spin.setRange(0, 40)
        self.spacing_spin.setValue(6)
        self.spacing_spin.setSuffix(" px")

        self.spacing_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.spacing_slider.setRange(0, 40)
        self.spacing_slider.setValue(6)

        self.spacing_spin.valueChanged.connect(self.spacing_slider.setValue)
        self.spacing_slider.valueChanged.connect(self.spacing_spin.setValue)
        self.spacing_spin.valueChanged.connect(self._update_preview)

        spacing_box.addWidget(self.spacing_slider, 1)
        spacing_box.addWidget(self.spacing_spin)
        form_layout.addRow("아이콘-텍스트 여백 간격:", spacing_box)

        layout.addWidget(form_card)
        layout.addWidget(preset_section)

        # Live Preview Container
        preview_header = QtWidgets.QHBoxLayout()
        preview_label = QtWidgets.QLabel("👁️ 실시간 미리보기")
        preview_label.setStyleSheet("font-size: 11pt; font-weight: 700; color: #536278;")
        drag_hint = QtWidgets.QLabel("💡 카드 내부의 텍스트를 마우스로 직접 클릭 & 드래그해보세요!")
        drag_hint.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: #168566;")

        preview_header.addWidget(preview_label)
        preview_header.addStretch(1)
        preview_header.addWidget(drag_hint)
        layout.addLayout(preview_header)

        self.preview_card = StreamDeckButton(self.slot_index, self)
        self.preview_card.setMinimumSize(160, 130)
        self.preview_card.setMaximumSize(220, 145)
        self.preview_card.text_position_changed.connect(self._on_text_dragged_in_preview)

        prev_box = QtWidgets.QHBoxLayout()
        prev_box.addStretch(1)
        prev_box.addWidget(self.preview_card)
        prev_box.addStretch(1)

        layout.addLayout(prev_box)
        layout.addStretch(1)

        # Action Buttons
        btn_box = QtWidgets.QHBoxLayout()
        btn_reset = QtWidgets.QPushButton("🔄 초기화")
        btn_reset.setStyleSheet("background: #FFF1F2; border: 1px solid #FDA4AF; color: #BE123C; font-weight: 700; padding: 8px 16px; border-radius: 9px;")
        btn_reset.clicked.connect(self._reset_config)

        btn_cancel = QtWidgets.QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)

        btn_save = QtWidgets.QPushButton("적용 및 저장")
        btn_save.setStyleSheet("background: #DCEEFF; border: 1.5px solid #72B2EE; color: #173A5E; font-weight: 800; padding: 8px 20px; border-radius: 9px;")
        btn_save.clicked.connect(self.accept)

        btn_box.addWidget(btn_reset)
        btn_box.addStretch(1)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_save)

        root_layout.addLayout(btn_box)

    def _on_pos_combo_changed(self, idx: int) -> None:
        if idx == 0:  # Bottom
            self.x_spin.setValue(50)
            self.y_spin.setValue(85)
        elif idx == 1:  # Top
            self.x_spin.setValue(50)
            self.y_spin.setValue(15)
        elif idx == 2:  # Center
            self.x_spin.setValue(50)
            self.y_spin.setValue(50)
        self._update_preview()

    def _on_xy_changed(self) -> None:
        if self.pos_combo.currentIndex() != 3 and self.pos_combo.currentIndex() != 4:
            self.pos_combo.blockSignals(True)
            self.pos_combo.setCurrentIndex(3)  # Custom
            self.pos_combo.blockSignals(False)
        self._update_preview()

    def _on_text_dragged_in_preview(self, x_pct: float, y_pct: float) -> None:
        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)
        self.x_spin.setValue(int(x_pct))
        self.y_spin.setValue(int(y_pct))
        self.x_spin.blockSignals(False)
        self.y_spin.blockSignals(False)

        self.pos_combo.blockSignals(True)
        self.pos_combo.setCurrentIndex(3)  # Custom Drag
        self.pos_combo.blockSignals(False)

    def _load_current_values(self) -> None:
        img_path = self.icon_config.get("image_path", "")
        self._current_base64 = str(self.icon_config.get("image_data", "")).strip()
        full_stretch = bool(self.icon_config.get("full_stretch", True if (img_path or self._current_base64) else False))
        text_show = bool(self.icon_config.get("text_show", True))
        emoji = self.icon_config.get("emoji", "")
        size_val = int(self.icon_config.get("icon_size", 40))
        text_pos = str(self.icon_config.get("text_position", "bottom"))
        font_val = int(self.icon_config.get("font_size", 12))
        spacing_val = int(self.icon_config.get("spacing", 6))
        x_val = int(self.icon_config.get("text_x_percent", 50))
        y_val = int(self.icon_config.get("text_y_percent", 85))

        pos_map = {"bottom": 0, "top": 1, "center": 2, "custom": 3, "hidden": 4}

        self.file_edit.setText(img_path)
        self._sync_preset_selection(str(img_path))
        self.stretch_check.setChecked(full_stretch)
        self.show_text_check.setChecked(text_show)
        self.emoji_edit.setText(emoji)
        self.size_spin.setValue(size_val)
        self.pos_combo.setCurrentIndex(pos_map.get(text_pos, 0))
        self.font_spin.setValue(font_val)
        self.spacing_spin.setValue(spacing_val)
        self.x_spin.setValue(x_val)
        self.y_spin.setValue(y_val)

        self._update_preview()

    def _browse_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "아이콘 이미지 선택",
            "",
            "이미지 파일 (*.png *.jpg *.jpeg *.bmp *.ico *.svg);;모든 파일 (*.*)",
        )
        if path:
            self.file_edit.setText(path)
            self.stretch_check.setChecked(True)
            self._current_base64 = encode_image_file_to_base64(path)
            self._sync_preset_selection(path)
            self._update_preview()

    def _reload_icon_gallery(self) -> None:
        while self.preset_row.count():
            item = self.preset_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.preset_buttons.clear()

        bundled = quickslot_preset_icon_paths(self.app_root)
        user_icons = quickslot_user_icon_paths(self.app_root)
        icon_paths = bundled + user_icons
        for icon_path in icon_paths:
            button = QtWidgets.QToolButton()
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setFixedSize(82, 82)
            button.setIcon(QtGui.QIcon(str(icon_path)))
            button.setIconSize(QtCore.QSize(68, 68))
            category = "내 아이콘" if icon_path in user_icons else "기본 아이콘"
            button.setToolTip(f"{category} · {icon_path.stem}")
            button.setStyleSheet(
                "QToolButton { background: #111827; border: 1px solid #334155; border-radius: 9px; padding: 5px; }"
                "QToolButton:hover { border: 2px solid #38BDF8; }"
                "QToolButton:checked { border: 2px solid #7C6CFF; background: #1F193E; }"
            )
            button.clicked.connect(lambda _checked=False, p=icon_path: self._select_preset_icon(p))
            self.preset_buttons[str(icon_path.resolve())] = button
            self.preset_row.addWidget(button)
        if not icon_paths:
            empty_label = QtWidgets.QLabel("저장된 아이콘이 없습니다.")
            empty_label.setObjectName("Muted")
            self.preset_row.addWidget(empty_label)
        self.preset_row.addStretch(1)
        current_path = self.file_edit.text().strip() if hasattr(self, "file_edit") else ""
        self._sync_preset_selection(current_path)

    def _import_icons_to_library(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "아이콘 여러 개 저장",
            "",
            "이미지 파일 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.ico);;모든 파일 (*.*)",
        )
        if not paths:
            return
        try:
            saved = save_quickslot_user_icons(self.app_root, list(paths))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "아이콘 저장 실패", str(exc))
            return
        self._reload_icon_gallery()
        if saved:
            self._select_preset_icon(saved[-1])
        skipped = len(paths) - len(saved)
        message = f"{len(saved)}개 아이콘을 프로그램 보관함에 저장했습니다."
        if skipped > 0:
            message += f"\n{skipped}개 파일은 형식 또는 10MB 제한으로 제외되었습니다."
        QtWidgets.QMessageBox.information(self, "아이콘 저장 완료", message)

    def _select_preset_icon(self, path: Path) -> None:
        resolved = str(path.resolve())
        self._current_base64 = encode_image_file_to_base64(resolved)
        self.file_edit.setText(resolved)
        self.stretch_check.setChecked(True)
        self.emoji_edit.clear()
        self._sync_preset_selection(resolved)
        self._update_preview()

    def _sync_preset_selection(self, path: str) -> None:
        try:
            resolved = str(Path(path).resolve()) if path else ""
        except Exception:
            resolved = ""
        for preset_path, button in self.preset_buttons.items():
            button.setChecked(preset_path == resolved)

    def _reset_config(self) -> None:
        self.file_edit.clear()
        self._current_base64 = ""
        self._sync_preset_selection("")
        self.stretch_check.setChecked(False)
        self.show_text_check.setChecked(True)
        self.emoji_edit.clear()
        self.size_spin.setValue(40)
        self.pos_combo.setCurrentIndex(0)
        self.font_spin.setValue(12)
        self.spacing_spin.setValue(6)
        self.x_spin.setValue(50)
        self.y_spin.setValue(85)
        self._update_preview()

    def _update_preview(self) -> None:
        cfg = self.get_config()
        self.preview_card.set_custom_icon_config(cfg)
        self.preview_card.set_slot_data(self.macro_name, "Alt+1", "hybrid", False)
        self.live_config_changed.emit(self.slot_index, cfg)

    def get_config(self) -> Dict[str, Any]:
        pos_keys = ["bottom", "top", "center", "custom", "hidden"]
        idx = max(0, min(4, self.pos_combo.currentIndex()))
        img_path = self.file_edit.text().strip()

        b64_data = getattr(self, "_current_base64", "")
        if not b64_data and img_path and os.path.exists(img_path):
            b64_data = encode_image_file_to_base64(img_path)
            self._current_base64 = b64_data
        elif not img_path:
            b64_data = ""

        return {
            "image_path": img_path,
            "image_data": b64_data,
            "full_stretch": self.stretch_check.isChecked(),
            "text_show": self.show_text_check.isChecked() and idx != 4,
            "emoji": self.emoji_edit.text().strip(),
            "icon_size": self.size_spin.value(),
            "text_position": pos_keys[idx],
            "font_size": self.font_spin.value(),
            "spacing": self.spacing_spin.value(),
            "text_x_percent": self.x_spin.value(),
            "text_y_percent": self.y_spin.value(),
        }


class StreamDeckButton(QtWidgets.QFrame):
    """Slot Card Widget supporting custom images, full fill, drag text position, font size, and spacing."""

    slot_triggered = QtCore.Signal(int, str)  # index, macro_name
    slot_stopped = QtCore.Signal(int, str)    # index, macro_name
    edit_icon_requested = QtCore.Signal(int)  # index
    remove_slot_requested = QtCore.Signal(int)  # index
    text_position_changed = QtCore.Signal(float, float)  # x_pct, y_pct

    def __init__(self, slot_index: int, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("SlotCard")
        self.slot_index = slot_index
        self.macro_name = ""
        self.display_title = ""
        self.hotkey = ""
        self.mode = "hybrid"
        self.is_running = False
        self.custom_icon_config: Dict[str, Any] = {}
        self._suppress_next_release = False

        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMinimumSize(42, 42)

        self._init_ui()
        self._update_appearance()

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        # Top Bar (Option Menu ... on right)
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)

        self.slot_number_label = QtWidgets.QLabel(f"#{self.slot_index + 1}")
        self.slot_number_label.setStyleSheet(
            "background: transparent; border: none; color: #AFC7F5; font-size: 10pt; font-weight: 800;"
        )

        self.opt_btn = QtWidgets.QToolButton()
        self.opt_btn.setText("···")
        self.opt_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.opt_btn.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                color: #64748B;
                font-size: 13pt;
                font-weight: 800;
                padding: 0px 4px;
            }
            QToolButton:hover { color: #FFFFFF; }
        """)
        self.opt_btn.clicked.connect(self._show_options_menu)

        top_bar.addWidget(self.slot_number_label)
        top_bar.addStretch(1)
        top_bar.addWidget(self.opt_btn)

        # 번호와 점 메뉴는 화면에서 숨기고, 동일 메뉴는 타일 우클릭으로 제공합니다.
        self.slot_number_label.hide()
        self.opt_btn.hide()

        # Center Container Area
        self.center_container = QtWidgets.QWidget()
        self.center_container.setStyleSheet("background: transparent; border: none;")
        self.center_layout = QtWidgets.QVBoxLayout(self.center_container)
        self.center_layout.setContentsMargins(0, 0, 0, 0)
        self.center_layout.setSpacing(4)
        self.center_layout.setAlignment(QtCore.Qt.AlignCenter)

        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setAlignment(QtCore.Qt.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent; border: none;")

        self.center_layout.addWidget(self.icon_label)
        layout.addWidget(self.center_container, 1)

        # Free-floating Draggable Title Label parented directly to self
        self.title_label = DraggableTextLabel("", self)
        self.title_label.setAlignment(QtCore.Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("background: transparent; border: none; font-size: 12pt; font-weight: 800; color: #FFFFFF;")
        self.title_label.text_moved.connect(self._on_title_label_dragged)

        # Bottom Glow Accent Bar
        glow_layout = QtWidgets.QHBoxLayout()
        glow_layout.setContentsMargins(0, 0, 0, 0)

        self.glow_bar = QtWidgets.QFrame()
        self.glow_bar.setFixedHeight(3.5)
        self.glow_bar.setFixedWidth(44)
        self.glow_bar.setStyleSheet("background: transparent; border-radius: 2px;")

        glow_layout.addStretch(1)
        glow_layout.addWidget(self.glow_bar)
        glow_layout.addStretch(1)

        layout.addLayout(glow_layout)

    def set_display_number(self, number: int) -> None:
        self.slot_number_label.setText(f"#{max(1, int(number))}")

    def _on_title_label_dragged(self, x_pct: float, y_pct: float) -> None:
        self.custom_icon_config["text_x_percent"] = x_pct
        self.custom_icon_config["text_y_percent"] = y_pct
        self.custom_icon_config["text_position"] = "custom"
        self.text_position_changed.emit(x_pct, y_pct)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        tiny = min(self.width(), self.height()) < 70
        self.slot_number_label.setVisible(False)
        self.opt_btn.setVisible(False)
        win_cfg = getattr(self.window(), "config", {}) if hasattr(self.window(), "config") else {}
        theme = THEMES.get(int(win_cfg.get("theme_index", 0)), THEMES[0])
        self.glow_bar.setVisible(not tiny and not bool(theme.get("icon_only", False)))
        self._reposition_title_label()
        self.update()

    @staticmethod
    def _scale_full_stretch_pixmap(pixmap: QtGui.QPixmap, target_size: QtCore.QSize) -> QtGui.QPixmap:
        if pixmap.isNull() or target_size.width() <= 0 or target_size.height() <= 0:
            return QtGui.QPixmap()
        return pixmap.scaled(
            target_size,
            QtCore.Qt.IgnoreAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )

    def _reposition_title_label(self) -> None:
        if not hasattr(self, "title_label") or not self.title_label:
            return

        text_show = bool(self.custom_icon_config.get("text_show", True))
        text_pos = str(self.custom_icon_config.get("text_position", "bottom"))
        win_cfg = getattr(self.window(), "config", {}) if hasattr(self.window(), "config") else {}
        theme = THEMES.get(int(win_cfg.get("theme_index", 0)), THEMES[0])

        if bool(theme.get("icon_only", False)) or min(self.width(), self.height()) < 70 or not self.display_title or not text_show or text_pos == "hidden":
            self.title_label.setVisible(False)
            return

        self.title_label.setVisible(True)
        self.title_label.adjustSize()

        if text_pos == "bottom":
            x_pct, y_pct = 50.0, 85.0
        elif text_pos == "top":
            x_pct, y_pct = 50.0, 15.0
        elif text_pos == "center":
            x_pct, y_pct = 50.0, 50.0
        else:  # custom
            x_pct = float(self.custom_icon_config.get("text_x_percent", 50.0))
            y_pct = float(self.custom_icon_config.get("text_y_percent", 85.0))

        parent_w = max(1, self.width())
        parent_h = max(1, self.height())
        lw = self.title_label.width()
        lh = self.title_label.height()

        max_x = max(0, parent_w - lw)
        max_y = max(0, parent_h - lh)

        x = int(max_x * (x_pct / 100.0))
        y = int(max_y * (y_pct / 100.0))

        self.title_label.move(x, y)
        self.title_label.raise_()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)

        win = self.window()
        win_cfg = getattr(win, "config", {}) if hasattr(win, "config") else {}
        tile_radius = float(win_cfg.get("tile_radius", 14))
        theme_idx = int(win_cfg.get("theme_index", 0))
        theme = THEMES.get(theme_idx, THEMES[0])
        border_width = float(theme.get("grid_border_width", 2.0))
        icon_only = bool(theme.get("icon_only", False))

        pix = load_pixmap_from_config(self.custom_icon_config)
        has_custom = not pix.isNull()
        full_stretch = bool(self.custom_icon_config.get("full_stretch", True if has_custom else False))
        is_empty = not self.macro_name and not has_custom

        # Icon-grid themes always paint their own physical tile surface.
        # Relying on QSS made transparent PNGs and empty cells appear as loose
        # floating icons on some Windows/Qt combinations.
        if icon_only or (has_custom and full_stretch):
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)

            rect = QtCore.QRectF(self.rect())
            path = QtGui.QPainterPath()
            path.addRoundedRect(rect, tile_radius, tile_radius)

            if icon_only:
                palette = list(theme.get("card_palette") or [])
                if is_empty:
                    fill_color = QtGui.QColor(str(theme.get("grid_empty_bg", "#181A1E")))
                    alpha = max(0, min(100, int(win_cfg.get("empty_slot_opacity", 100)))) / 100.0
                    fill_color.setAlphaF(alpha)
                    border_color = QtGui.QColor(str(theme.get("grid_border", "#5B616B")))
                    border_color.setAlphaF(max(0.55, alpha))
                else:
                    card_bg = palette[self.slot_index % len(palette)] if palette else (
                        theme["card_bg_1"] if self.slot_index % 2 == 0 else theme["card_bg_2"]
                    )
                    fill_color = QtGui.QColor(str(card_bg))
                    border_color = QtGui.QColor(str(theme.get("grid_border") or theme.get("palette_border") or "#5B616B"))
                painter.fillPath(path, fill_color)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(QtGui.QPen(border_color, border_width))
                border_rect = rect.adjusted(border_width / 2.0, border_width / 2.0, -border_width / 2.0, -border_width / 2.0)
                painter.drawRoundedRect(border_rect, tile_radius, tile_radius)

            target_size = self.size()
            if has_custom and full_stretch and target_size.width() > 0 and target_size.height() > 0:
                painter.setClipPath(path)
                scaled_pix = self._scale_full_stretch_pixmap(pix, target_size)
                painter.drawPixmap(self.rect(), scaled_pix)
                border_color = str(theme.get("grid_border") or theme.get("palette_border") or (theme["card_border_1"] if self.slot_index % 2 == 0 else theme["card_border_2"]))
                painter.setClipping(False)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(QtGui.QPen(QtGui.QColor(border_color), border_width))
                border_rect = QtCore.QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
                painter.drawRoundedRect(border_rect, tile_radius, tile_radius)
            painter.end()

    def set_custom_icon_config(self, config: Dict[str, Any]) -> None:
        self.custom_icon_config = dict(config or {})
        self._update_appearance()

    def set_slot_data(
        self,
        macro_name: str,
        hotkey: str = "",
        mode: str = "hybrid",
        is_running: bool = False,
        display_title: Optional[str] = None,
    ) -> None:
        self.macro_name = (macro_name or "").strip()
        self.display_title = self.macro_name if display_title is None else str(display_title).strip()
        self.hotkey = (hotkey or "").strip()
        self.mode = mode
        self.is_running = is_running
        self._update_appearance()

    def set_running(self, running: bool) -> None:
        if self.is_running != running:
            self.is_running = running
            self._update_appearance()

    def _update_appearance(self) -> None:
        win = self.window()
        win_cfg = getattr(win, "config", {}) if hasattr(win, "config") else {}
        empty_opac = int(win_cfg.get("empty_slot_opacity", 100))
        tile_radius = int(win_cfg.get("tile_radius", 14))
        hover_glow = bool(win_cfg.get("hover_glow", True))
        theme_idx = int(win_cfg.get("theme_index", 0))
        theme = THEMES.get(theme_idx, THEMES[0])
        icon_only = bool(theme.get("icon_only", False))

        # Custom Icon parameters
        pix = load_pixmap_from_config(self.custom_icon_config)
        has_custom = not pix.isNull()
        full_stretch = bool(self.custom_icon_config.get("full_stretch", True if has_custom else False))
        text_show = bool(self.custom_icon_config.get("text_show", True))
        custom_emoji = self.custom_icon_config.get("emoji", "")
        icon_size = int(self.custom_icon_config.get("icon_size", 40))
        text_pos = str(self.custom_icon_config.get("text_position", "bottom"))
        font_size = int(self.custom_icon_config.get("font_size", 12))
        spacing = int(self.custom_icon_config.get("spacing", 6))

        self.center_layout.setSpacing(spacing)

        main_layout = self.layout()
        if main_layout:
            if full_stretch and has_custom:
                main_layout.setContentsMargins(0, 0, 0, 0)
            elif icon_only:
                main_layout.setContentsMargins(4, 4, 4, 4)
            else:
                main_layout.setContentsMargins(10, 8, 10, 8)

        if not self.macro_name and not has_custom:
            self.opt_btn.setVisible(False)
            self.title_label.setVisible(False)
            self.glow_bar.setStyleSheet("background: transparent;")
            self.setCursor(QtCore.Qt.ArrowCursor)
            self.icon_label.setPixmap(QtGui.QPixmap())
            self.icon_label.clear()
            self.icon_label.setVisible(False)

            if empty_opac <= 0:
                self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
                self.setStyleSheet("""
                    #SlotCard {
                        background-color: transparent;
                        background: transparent;
                        border: none;
                    }
                """)
            else:
                alpha = empty_opac / 100.0
                border_alpha = max(0.45, alpha)
                empty_bg = QtGui.QColor(str(theme.get("grid_empty_bg", "#121826")))
                grid_border = QtGui.QColor(str(theme.get("grid_border") or theme.get("palette_border") or "#4B5563"))
                empty_bg_css = f"rgba({empty_bg.red()}, {empty_bg.green()}, {empty_bg.blue()}, {alpha:.2f})"
                border_css = f"rgba({grid_border.red()}, {grid_border.green()}, {grid_border.blue()}, {border_alpha:.2f})"
                border_width = float(theme.get("grid_border_width", 1.5))
                self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
                if hover_glow:
                    self.setStyleSheet(f"""
                        #SlotCard {{
                            background-color: {empty_bg_css};
                            border: {border_width}px solid {border_css};
                            border-radius: {tile_radius}px;
                        }}
                        #SlotCard:hover {{
                            background-color: {empty_bg_css};
                            border: {border_width + 0.5}px solid rgba(148, 163, 184, {max(0.70, alpha):.2f});
                            border-radius: {tile_radius}px;
                        }}
                    """)
                else:
                    self.setStyleSheet(f"""
                        #SlotCard {{
                            background-color: {empty_bg_css};
                            border: {border_width}px solid {border_css};
                            border-radius: {tile_radius}px;
                        }}
                    """)
            self.update()
            return

        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.opt_btn.setVisible(False)
        self.slot_number_label.setVisible(False)
        if icon_only:
            self.glow_bar.setVisible(False)
            text_show = False
            icon_size = max(24, min(icon_size, max(24, min(self.width(), self.height()) - 12)))

        # Apply Image or Emoji Icon
        if has_custom:
            if full_stretch:
                # Full Card Fill mode: Hide center icon_label so image covers 100% of card surface via paintEvent
                self.icon_label.setVisible(False)
                self.icon_label.setPixmap(QtGui.QPixmap())
            else:
                self.icon_label.setVisible(True)
                scaled_pix = pix.scaled(icon_size, icon_size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                self.icon_label.setPixmap(scaled_pix)
                self.icon_label.setStyleSheet("background: transparent; border: none;")
        elif custom_emoji:
            self.icon_label.setVisible(True)
            self.icon_label.setPixmap(QtGui.QPixmap())
            self.icon_label.setText(custom_emoji)
            emoji_font_size = max(10, int(icon_size * 0.55))
            self.icon_label.setStyleSheet(f"background: transparent; border: none; font-size: {emoji_font_size}pt;")
        else:
            self.icon_label.setVisible(True)
            self.icon_label.setPixmap(QtGui.QPixmap())
            if self.is_running:
                self.icon_label.setText("▶")
                self.icon_label.setStyleSheet("background: transparent; border: none; font-size: 18pt; font-weight: 800; color: #35C89A;")
            elif self.slot_index % 2 == 0:
                self.icon_label.setText("🖱️")
                self.icon_label.setStyleSheet("background: transparent; border: none; font-size: 20pt;")
            else:
                self.icon_label.setText(self.macro_name)
                self.icon_label.setStyleSheet("""
                    QLabel {
                        background: transparent;
                        border: 2px solid #C084FC;
                        border-radius: 7px;
                        color: #E9D5FF;
                        font-size: 10pt;
                        font-weight: 800;
                        padding: 2px 8px;
                    }
                """)

        self.title_label.setText(self.display_title)
        if not text_show or text_pos == "hidden":
            self.title_label.setVisible(False)
        else:
            self.title_label.setVisible(True)
            if full_stretch and has_custom:
                self.title_label.setStyleSheet(
                    f"background: rgba(12, 16, 26, 0.75); border: 1px solid rgba(255, 255, 255, 0.15); "
                    f"border-radius: 6px; padding: 2px 8px; font-size: {font_size}pt; font-weight: 800; color: #FFFFFF;"
                )
            else:
                self.title_label.setStyleSheet(
                    f"background: transparent; border: none; font-size: {font_size}pt; font-weight: 800; color: #FFFFFF;"
                )

        self._reposition_title_label()

        palette = list(theme.get("card_palette") or [])
        border_width = float(theme.get("grid_border_width", 2.0))
        if palette:
            card_bg = palette[self.slot_index % len(palette)]
            card_border = str(theme.get("grid_border") or theme.get("palette_border", "#454A55"))
            glow_color = str(theme.get("glow_1", card_border))
        elif self.slot_index % 2 == 0:
            card_bg, card_border, glow_color = theme["card_bg_1"], theme["card_border_1"], theme["glow_1"]
        else:
            card_bg, card_border, glow_color = theme["card_bg_2"], theme["card_border_2"], theme["glow_2"]

        if self.is_running:
            self.glow_bar.setStyleSheet("background: #35C89A; border-radius: 2px;")
            self.setStyleSheet(f"""
                #SlotCard {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #102B21, stop:1 #0A1C16);
                    border: {border_width + 0.5}px solid #35C89A;
                    border-radius: {tile_radius}px;
                }}
            """)
        else:
            self.glow_bar.setStyleSheet(f"background: {glow_color}; border-radius: 2px;")
            if hover_glow:
                self.setStyleSheet(f"""
                    #SlotCard {{
                        background: {card_bg};
                        border: {border_width}px solid {card_border};
                        border-radius: {tile_radius}px;
                    }}
                    #SlotCard:hover {{
                        border: {border_width + 0.5}px solid #FFFFFF;
                    }}
                """)
            else:
                self.setStyleSheet(f"""
                    #SlotCard {{
                        background: {card_bg};
                        border: {border_width}px solid {card_border};
                        border-radius: {tile_radius}px;
                    }}
                """)

        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            win = self.window()
            if hasattr(win, "_start_window_drag_candidate") and win._start_window_drag_candidate(event):
                event.accept()
                return

            self._press_pos = event.globalPos()
            if hasattr(win, "_start_mouse_hold_check"):
                win._start_mouse_hold_check(event.globalPos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        win = self.window()
        if hasattr(win, "_handle_window_drag_move") and win._handle_window_drag_move(event):
            event.accept()
            return

        if hasattr(self, "_press_pos") and self._press_pos and (event.buttons() & QtCore.Qt.LeftButton):
            if (event.globalPos() - self._press_pos).manhattanLength() > 15:
                if hasattr(win, "_cancel_mouse_hold_check"):
                    win._cancel_mouse_hold_check()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        win = self.window()
        if hasattr(win, "_handle_window_drag_release") and win._handle_window_drag_release(event):
            event.accept()
            return

        is_hold_triggered = False
        if hasattr(win, "_hold_triggered"):
            is_hold_triggered = bool(win._hold_triggered)
        if hasattr(win, "_cancel_mouse_hold_check"):
            win._cancel_mouse_hold_check()

        if event.button() == QtCore.Qt.LeftButton and self._suppress_next_release:
            self._suppress_next_release = False
        elif not is_hold_triggered and event.button() == QtCore.Qt.LeftButton:
            self._emit_primary_action()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            # The first release keeps the primary action feeling immediate.
            # Suppress only the second release and open the sticky deck picker.
            self._suppress_next_release = True
            win = self.window()
            if hasattr(win, "_cancel_mouse_hold_check"):
                win._cancel_mouse_hold_check()
            if hasattr(win, "_show_preset_radial"):
                win._show_preset_radial(event.globalPosition().toPoint(), sticky=True)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _emit_primary_action(self) -> None:
        if not self.macro_name:
            return
        if self.is_running:
            self.slot_stopped.emit(self.slot_index, self.macro_name)
        else:
            self.slot_triggered.emit(self.slot_index, self.macro_name)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        self._show_options_menu(pos=event.globalPos())

    def _show_options_menu(self, pos: Optional[QtCore.QPoint] = None) -> None:
        menu = self._build_options_menu()
        popup_pos = pos or self.opt_btn.mapToGlobal(QtCore.QPoint(0, self.opt_btn.height()))
        menu.exec_(popup_pos)

    def _build_options_menu(self) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu(self)
        if self.macro_name:
            if self.is_running:
                stop_act = menu.addAction("🛑 매크로 중지")
                stop_act.triggered.connect(lambda: self.slot_stopped.emit(self.slot_index, self.macro_name))
            else:
                run_act = menu.addAction("▶ 매크로 실행")
                run_act.triggered.connect(lambda: self.slot_triggered.emit(self.slot_index, self.macro_name))
            menu.addSeparator()

        edit_icon_act = menu.addAction("🖼️ 아이콘 불러오기 / 편집")
        edit_icon_act.triggered.connect(lambda: self.edit_icon_requested.emit(self.slot_index))
        if self.macro_name:
            menu.addSeparator()
            remove_act = menu.addAction("🗑 슬롯 제거")
            remove_act.triggered.connect(lambda: self.remove_slot_requested.emit(self.slot_index))
        return menu

RADIAL_ACTION_PRESETS: Dict[str, tuple[str, str, str]] = {
    "prev_page": ("◀ 이전 페이지", "◀", "#3B82F6"),
    "next_page": ("▶ 다음 페이지", "▶", "#3B82F6"),
    "settings": ("🛠️ 환경 설정", "⚙️", "#6A55FF"),
    "deck_dock": ("▦ Deck Dock", "▦", "#14B8A6"),
    "emergency": ("🔴 전체 중지", "🛑", "#E85566"),
    "refresh": ("🔄 슬롯 갱신", "🔄", "#00A6FF"),
    "studio": ("⚙️ Studio 실행", "🖥️", "#35C89A"),
    "topmost": ("📌 최상위 고정", "📌", "#C026D3"),
    "minimize": ("📥 트레이 최소화", "—", "#94A3B8"),
    "close_app": ("❌ 프로그램 종료", "✕", "#FF4D4D"),
    "opacity": ("💧 투명도 조절", "💧", "#38BDF8"),
    "grid": ("▦ 그리드 변경", "▦", "#F59E0B"),
    "add_slot": ("＋ 슬롯 추가", "＋", "#22C55E"),
    "preset_settings": ("★ 덱 프리셋 관리", "★", "#FBBF24"),
}

THEMES: Dict[int, Dict[str, Any]] = {
    0: {
        "name": "🌌 Cyber Dark (네온 파플 & 블루)",
        "card_bg_1": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0F1729, stop:1 #090F1C)",
        "card_border_1": "#00A6FF",
        "card_bg_2": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1A112C, stop:1 #110B1E)",
        "card_border_2": "#A000FF",
        "glow_1": "#00A6FF",
        "glow_2": "#C026D3",
        "central_bg": "rgba(11, 14, 20, 0.45)",
    },
    1: {
        "name": "⬛ OLED Pure Black (심플 블랙)",
        "card_bg_1": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #141414, stop:1 #080808)",
        "card_border_1": "#333333",
        "card_bg_2": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1A1A1A, stop:1 #0D0D0D)",
        "card_border_2": "#555555",
        "glow_1": "#666666",
        "glow_2": "#888888",
        "central_bg": "rgba(0, 0, 0, 0.50)",
    },
    2: {
        "name": "💗 Synthwave Pink (분홍/보라 네온)",
        "card_bg_1": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2E0B25, stop:1 #1A0517)",
        "card_border_1": "#EC4899",
        "card_bg_2": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #220A38, stop:1 #120421)",
        "card_border_2": "#8B5CF6",
        "glow_1": "#F472B6",
        "glow_2": "#A78BFA",
        "central_bg": "rgba(20, 9, 31, 0.45)",
    },
    3: {
        "name": "🧊 Steel Slate (차분한 스틸 블루)",
        "card_bg_1": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1E293B, stop:1 #0F172A)",
        "card_border_1": "#38BDF8",
        "card_bg_2": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1E2B3E, stop:1 #111A29)",
        "card_border_2": "#64748B",
        "glow_1": "#38BDF8",
        "glow_2": "#94A3B8",
        "central_bg": "rgba(15, 23, 42, 0.45)",
    },
    4: {
        "name": "▦ Compact Icon Dark (아이콘 전용)",
        "card_bg_1": "#25282D", "card_border_1": "#4B5058",
        "card_bg_2": "#202328", "card_border_2": "#3F444C",
        "glow_1": "#6B7280", "glow_2": "#4B5563",
        "central_bg": "rgba(20, 21, 24, 0.96)",
        "icon_only": True, "recommended_scale": 0.50, "recommended_gap": 3, "recommended_radius": 6,
        "grid_border": "#5B616B", "grid_empty_bg": "#181A1E", "grid_border_width": 2.0,
    },
    5: {
        "name": "▦ Color Icon Grid (컬러 아이콘 전용)",
        "card_bg_1": "#315CF3", "card_border_1": "#4A4F59",
        "card_bg_2": "#7C3AED", "card_border_2": "#4A4F59",
        "glow_1": "#38BDF8", "glow_2": "#C084FC",
        "central_bg": "rgba(18, 19, 23, 0.98)",
        "icon_only": True, "recommended_scale": 0.45, "recommended_gap": 3, "recommended_radius": 6,
        "grid_border": "#5A606A", "grid_empty_bg": "#191B20", "grid_border_width": 2.0,
        "palette_border": "#454A55",
        "card_palette": ["#315CF3", "#392B83", "#F43F5E", "#A3E635", "#F97316", "#16A34A", "#14B8A6", "#D946EF", "#374151", "#6D28D9"],
    },
}


class RadialPieMenuItem:
    def __init__(self, key: str, title: str, icon_text: str, color: str, callback: Optional[Any] = None):
        self.key = key
        self.title = title
        self.icon_text = icon_text
        self.color = color
        self.callback = callback


class RadialPieMenuWidget(QtWidgets.QWidget):
    """Glass-style radial wheel supporting hold gestures and sticky double-click mode."""

    action_triggered = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.ToolTip | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFixedSize(400, 400)

        self.center_pt = QtCore.QPoint(200, 200)
        self.radius = 172.0
        self.inner_radius = 72.0
        self.center_radius = 64.0

        self.hovered_idx: int = -1
        self.items: List[RadialPieMenuItem] = []
        self.sticky = False
        self.center_title = "Quick Actions"
        self.center_subtitle = "관리 메뉴"
        self._sticky_timer = QtCore.QElapsedTimer()
        self._init_default_items()

    def _init_default_items(self) -> None:
        default_keys = ["prev_page", "next_page", "settings", "deck_dock", "preset_settings", "emergency", "studio", "topmost"]
        self.items = []
        for k in default_keys:
            title, icon_text, color = RADIAL_ACTION_PRESETS[k]
            self.items.append(RadialPieMenuItem(k, title, icon_text, color))

    def load_custom_items(self, custom_keys: Optional[List[str]]) -> None:
        self.center_title = "Quick Actions"
        self.center_subtitle = "관리 메뉴"
        if not custom_keys or len(custom_keys) < 8:
            self._init_default_items()
            return
        new_items = []
        for k in custom_keys[:8]:
            if k in RADIAL_ACTION_PRESETS:
                title, icon_text, color = RADIAL_ACTION_PRESETS[k]
                new_items.append(RadialPieMenuItem(k, title, icon_text, color))
        if len(new_items) == 8:
            self.items = new_items
        else:
            self._init_default_items()

    def load_deck_presets(self, presets: List[tuple[str, str]]) -> None:
        self.center_title = "Preset"
        self.center_subtitle = "모드 전환"
        colors = ["#38BDF8", "#A78BFA", "#34D399", "#F59E0B", "#F472B6", "#22D3EE", "#818CF8", "#FB7185"]
        self.items = [
            RadialPieMenuItem(f"deck:{preset_id}", preset_name, str(position + 1), colors[position % len(colors)])
            for position, (preset_id, preset_name) in enumerate(presets[:8])
        ]
        if not self.items:
            title, icon_text, color = RADIAL_ACTION_PRESETS["preset_settings"]
            self.items = [RadialPieMenuItem("preset_settings", title, icon_text, color)]
        self.update()

    def popup_at(self, global_pos: QtCore.QPoint, sticky: bool = False) -> None:
        top_left = global_pos - self.center_pt
        self.move(top_left)
        self.hovered_idx = -1
        self.sticky = sticky
        self.show()
        self.raise_()
        self.activateWindow()
        if sticky:
            self._sticky_timer.start()
            app = QtWidgets.QApplication.instance()
            if app:
                app.installEventFilter(self)
        else:
            self.grabMouse()

    def _get_item_center(self, idx: int) -> QtCore.QPointF:
        count = len(self.items) or 8
        angle_deg = (idx * (360.0 / count)) - 90.0
        angle_rad = math.radians(angle_deg)
        label_radius = (self.inner_radius + self.radius) / 2.0
        cx = self.center_pt.x() + label_radius * math.cos(angle_rad)
        cy = self.center_pt.y() + label_radius * math.sin(angle_rad)
        return QtCore.QPointF(cx, cy)

    def _update_hover_from_pos(self, pos: QtCore.QPoint) -> None:
        dx = pos.x() - self.center_pt.x()
        dy = pos.y() - self.center_pt.y()
        dist = math.hypot(dx, dy)

        if dist <= self.inner_radius:
            if self.hovered_idx != -2:
                self.hovered_idx = -2
                self.update()
            return

        if dist > self.radius:
            if self.hovered_idx != -1:
                self.hovered_idx = -1
                self.update()
            return

        angle_deg = math.degrees(math.atan2(dy, dx)) + 90.0
        if angle_deg < 0:
            angle_deg += 360.0

        count = len(self.items) or 8
        step = 360.0 / count
        idx = int((angle_deg + (step / 2.0)) % 360.0 // step)
        idx = max(0, min(count - 1, idx))

        if self.hovered_idx != idx:
            self.hovered_idx = idx
            self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._update_hover_from_pos(event.pos())
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self._update_hover_from_pos(event.pos())
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.sticky and self._sticky_timer.isValid() and self._sticky_timer.elapsed() < 250:
            event.accept()
            return
        if not self.sticky:
            self.releaseMouse()
        pos = event.pos()
        self._update_hover_from_pos(pos)

        if 0 <= self.hovered_idx < len(self.items):
            item = self.items[self.hovered_idx]
            self.hide()
            # Release the popup and its mouse grab before opening modal
            # actions such as Settings. Dispatching on the next event-loop
            # turn also avoids nesting a dialog inside this release handler.
            QtCore.QTimer.singleShot(0, lambda selected=item: self._trigger_item(selected))
        elif self.hovered_idx == -2:
            self.hide()
        elif not self.sticky:
            self.hide()
        super().mouseReleaseEvent(event)

    def _trigger_item(self, item: RadialPieMenuItem) -> None:
        self.action_triggered.emit(item.key)
        if item.callback:
            item.callback()

    def eventFilter(self, watched: object, event: QtCore.QEvent) -> bool:
        if self.sticky and event.type() == QtCore.QEvent.MouseButtonPress and isinstance(event, QtGui.QMouseEvent):
            global_pos = event.globalPosition().toPoint()
            if not self.geometry().contains(global_pos):
                self.hide()
        elif self.sticky and event.type() == QtCore.QEvent.ApplicationDeactivate:
            self.hide()
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        app = QtWidgets.QApplication.instance()
        if app:
            app.removeEventFilter(self)
        self.sticky = False
        if QtWidgets.QWidget.mouseGrabber() is self:
            self.releaseMouse()
        super().hideEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        # Soft floating shadow and frosted base.
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(15, 23, 42, 42))
        painter.drawEllipse(self.center_pt + QtCore.QPoint(0, 8), self.radius + 8, self.radius + 8)

        count = max(1, len(self.items))
        step = 360.0 / count
        outer_rect = QtCore.QRectF(
            self.center_pt.x() - self.radius,
            self.center_pt.y() - self.radius,
            self.radius * 2,
            self.radius * 2,
        )
        inner_rect = QtCore.QRectF(
            self.center_pt.x() - self.inner_radius,
            self.center_pt.y() - self.inner_radius,
            self.inner_radius * 2,
            self.inner_radius * 2,
        )

        for index, item in enumerate(self.items):
            # Qt painter angles start at 3 o'clock and increase counter-
            # clockwise. Begin half a sector to the left of the item's
            # centre so the painted wedge matches cursor hit-testing.
            start = 90.0 - (index * step) + (step / 2.0)
            path = QtGui.QPainterPath()
            path.arcMoveTo(outer_rect, start)
            path.arcTo(outer_rect, start, -step)
            inner_end = start - step
            inner_point = QtCore.QPointF(
                self.center_pt.x() + self.inner_radius * math.cos(math.radians(inner_end)),
                self.center_pt.y() - self.inner_radius * math.sin(math.radians(inner_end)),
            )
            path.lineTo(inner_point)
            path.arcTo(inner_rect, inner_end, step)
            path.closeSubpath()

            hovered = index == self.hovered_idx
            if hovered:
                accent = QtGui.QColor(item.color)
                fill = QtGui.QColor(accent.red(), accent.green(), accent.blue(), 88)
                border = QtGui.QColor(accent.red(), accent.green(), accent.blue(), 190)
            else:
                fill = QtGui.QColor(247, 249, 252, 246)
                border = QtGui.QColor(190, 198, 211, 190)
            painter.setBrush(fill)
            painter.setPen(QtGui.QPen(border, 1.3))
            painter.drawPath(path)

            center = self._get_item_center(index)
            icon_rect = QtCore.QRectF(center.x() - 34, center.y() - 34, 68, 34)
            title_rect = QtCore.QRectF(center.x() - 54, center.y() + 1, 108, 32)
            painter.setPen(QtGui.QColor("#172033"))
            font = painter.font()
            font.setPointSize(14 if hovered else 13)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(icon_rect, QtCore.Qt.AlignCenter, item.icon_text)
            font.setPointSize(8 if count > 6 else 9)
            font.setBold(False)
            painter.setFont(font)
            title = QtGui.QFontMetrics(font).elidedText(item.title.replace("● ", ""), QtCore.Qt.ElideRight, 104)
            painter.drawText(title_rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, title)

        center_hover = self.hovered_idx == -2
        painter.setBrush(QtGui.QColor("#FFFFFF") if not center_hover else QtGui.QColor("#EEF6FF"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#CBD5E1"), 1.5))
        painter.drawEllipse(self.center_pt, self.center_radius, self.center_radius)
        font = painter.font()
        font.setPointSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#172033"))
        painter.drawText(QtCore.QRectF(130, 174, 140, 28), QtCore.Qt.AlignCenter, self.center_title)
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#7C879A"))
        painter.drawText(QtCore.QRectF(130, 200, 140, 24), QtCore.Qt.AlignCenter, self.center_subtitle)

        painter.end()


class DraggableActionChip(QtWidgets.QLabel):
    """Draggable Action Chip for Radial Menu Editor."""

    def __init__(self, action_key: str, title: str, icon_text: str, color: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(f"{icon_text} {title}", parent)
        self.action_key = action_key
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setToolTip(f"이 항목을 클릭하거나 라디얼 원형 메뉴 슬롯으로 끌어다 놓으세요: {title}")
        self.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(255, 255, 255, 0.92);
                border: 1.5px solid {color};
                color: #263449;
                border-radius: 8px;
                padding: 5px 10px;
                font-size: 9pt;
                font-weight: 700;
            }}
            QLabel:hover {{
                background-color: {color};
                color: #FFFFFF;
            }}
        """)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            drag = QtGui.QDrag(self)
            mime = QtCore.QMimeData()
            mime.setText(self.action_key)
            drag.setMimeData(mime)
            drag.exec_(QtCore.Qt.CopyAction)
        super().mousePressEvent(event)


class RadialWheelPreviewWidget(QtWidgets.QWidget):
    """Interactive Circular Radial Wheel Editor supporting Drag & Drop and click selection."""

    slot_changed = QtCore.Signal(int, str)  # slot_index, new_action_key

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(300, 300)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.items_keys: List[str] = ["prev_page", "next_page", "settings", "emergency", "refresh", "studio", "topmost", "add_slot"]
        self.hovered_slot: int = -1
        self.selected_slot: int = -1

    def set_radial_keys(self, keys: List[str]) -> None:
        if len(keys) >= 8:
            self.items_keys = list(keys[:8])
            self.update()

    def _get_slot_center(self, idx: int) -> QtCore.QPointF:
        count = 8
        angle_deg = (idx * (360.0 / count)) - 90.0
        angle_rad = math.radians(angle_deg)
        radius = 92.0
        cx = 150.0 + radius * math.cos(angle_rad)
        cy = 150.0 + radius * math.sin(angle_rad)
        return QtCore.QPointF(cx, cy)

    def _get_slot_at_pos(self, pos: QtCore.QPoint) -> int:
        for i in range(8):
            c_pt = self._get_slot_center(i)
            dx = pos.x() - c_pt.x()
            dy = pos.y() - c_pt.y()
            if math.hypot(dx, dy) <= 29.0:
                return i
        return -1

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        slot = self._get_slot_at_pos(event.pos())
        if self.hovered_slot != slot:
            self.hovered_slot = slot
            self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            slot = self._get_slot_at_pos(event.pos())
            if 0 <= slot < 8:
                self.selected_slot = slot
                self.update()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasText() and event.mimeData().text() in RADIAL_ACTION_PRESETS:
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        slot = self._get_slot_at_pos(event.pos())
        if self.hovered_slot != slot:
            self.hovered_slot = slot
            self.update()
        if slot >= 0:
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        slot = self._get_slot_at_pos(event.pos())
        action_key = event.mimeData().text()
        if 0 <= slot < 8 and action_key in RADIAL_ACTION_PRESETS:
            self.items_keys[slot] = action_key
            self.slot_changed.emit(slot, action_key)
            self.update()
            event.acceptProposedAction()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        center_pt = QtCore.QPointF(150.0, 150.0)

        # Halo ring
        painter.setBrush(QtGui.QColor(10, 14, 23, 210))
        painter.setPen(QtGui.QPen(QtGui.QColor(32, 42, 64, 180), 1.5))
        painter.drawEllipse(center_pt, 125.0, 125.0)

        # Center close circle
        painter.setBrush(QtGui.QColor(130, 20, 35, 235))
        painter.setPen(QtGui.QPen(QtGui.QColor("#E85566"), 1.5))
        painter.drawEllipse(center_pt, 22.0, 22.0)
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF")))
        painter.drawText(QtCore.QRectF(128.0, 128.0, 44.0, 44.0), QtCore.Qt.AlignCenter, "MENU")

        # Render 8 slots
        for i in range(8):
            c_pt = self._get_slot_center(i)
            key = self.items_keys[i] if i < len(self.items_keys) else "settings"
            title, icon_text, color = RADIAL_ACTION_PRESETS.get(key, ("환경 설정", "⚙️", "#6A55FF"))
            is_hover = (i == self.hovered_slot)
            is_selected = (i == self.selected_slot)

            r_val = 26.0 + (3.0 if is_hover else 0.0)
            if is_hover or is_selected:
                glow_col = QtGui.QColor(color)
                painter.setBrush(QtGui.QColor(glow_col.red(), glow_col.green(), glow_col.blue(), 230))
                painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 2.5))
            else:
                painter.setBrush(QtGui.QColor(30, 38, 56, 235))
                painter.setPen(QtGui.QPen(QtGui.QColor(color), 1.5))

            painter.drawEllipse(c_pt, r_val, r_val)

            font.setPointSize(12 if not is_hover else 14)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF")))
            painter.drawText(QtCore.QRectF(c_pt.x() - r_val, c_pt.y() - r_val, r_val * 2, r_val * 2), QtCore.Qt.AlignCenter, icon_text)

        painter.end()


class SlotPresetDialog(QtWidgets.QDialog):
    """Create, rename, save, delete, and select complete QuickSlot deck presets."""

    def __init__(self, main_window: "QuickSlotDeckWindow", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.main_window = main_window
        self.presets: Dict[str, Dict[str, Any]] = copy.deepcopy(main_window.config.get("slot_presets") or {})
        self.active_id = str(main_window.config.get("active_slot_preset") or "default")
        self.setWindowTitle("QuickSlot 덱 프리셋 관리")
        self.setMinimumSize(620, 460)
        self.setStyleSheet(glass_dialog_stylesheet())

        root = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            "프리셋마다 슬롯, 연결된 매크로, 아이콘, 그리드와 테마를 통째로 저장합니다. "
            "좌클릭을 1초간 유지하면 이 목록을 라디얼 메뉴에서 바로 전환할 수 있습니다."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #536278; font-weight: 700;")
        root.addWidget(info)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept())
        root.addWidget(self.list_widget, 1)

        tools = QtWidgets.QHBoxLayout()
        add_button = QtWidgets.QPushButton("＋ 빈 프리셋")
        rename_button = QtWidgets.QPushButton("이름 변경")
        save_button = QtWidgets.QPushButton("현재 UI로 저장")
        delete_button = QtWidgets.QPushButton("삭제")
        add_button.clicked.connect(self._add_preset)
        rename_button.clicked.connect(self._rename_preset)
        save_button.clicked.connect(self._save_current_ui)
        delete_button.clicked.connect(self._delete_preset)
        for button in (add_button, rename_button, save_button, delete_button):
            tools.addWidget(button)
        root.addLayout(tools)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        switch_button = buttons.addButton("선택 프리셋으로 전환", QtWidgets.QDialogButtonBox.AcceptRole)
        switch_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._reload_list(self.active_id)

    def _selected_id(self) -> str:
        item = self.list_widget.currentItem()
        return str(item.data(QtCore.Qt.UserRole)) if item else ""

    def _reload_list(self, selected_id: str = "") -> None:
        self.list_widget.clear()
        for preset_id, preset in self.presets.items():
            name = str(preset.get("name") or preset_id)
            marker = "● " if preset_id == self.active_id else ""
            item = QtWidgets.QListWidgetItem(f"{marker}{name}")
            item.setData(QtCore.Qt.UserRole, preset_id)
            self.list_widget.addItem(item)
            if preset_id == selected_id:
                self.list_widget.setCurrentItem(item)
        if self.list_widget.currentRow() < 0 and self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _new_id(self) -> str:
        number = 1
        while f"preset-{number}" in self.presets:
            number += 1
        return f"preset-{number}"

    def _add_preset(self) -> None:
        if len(self.presets) >= 8:
            QtWidgets.QMessageBox.information(self, "새 프리셋", "좌클릭 라디얼에는 최대 8개 프리셋을 등록할 수 있습니다.")
            return
        name, accepted = QtWidgets.QInputDialog.getText(self, "새 프리셋", "프리셋 이름:")
        name = str(name).strip()
        if not accepted or not name:
            return
        preset_id = self._new_id()
        preset = self.main_window._build_slot_preset(name)
        preset["slots"] = []
        preset["custom_icons"] = {}
        preset["deck_page_count"] = 1
        preset["deck_page_names"] = ["페이지 1"]
        self.presets[preset_id] = preset
        self._reload_list(preset_id)

    def _rename_preset(self) -> None:
        preset_id = self._selected_id()
        if not preset_id:
            return
        current = str(self.presets[preset_id].get("name") or preset_id)
        name, accepted = QtWidgets.QInputDialog.getText(self, "프리셋 이름 변경", "새 이름:", text=current)
        name = str(name).strip()
        if accepted and name:
            self.presets[preset_id]["name"] = name
            self._reload_list(preset_id)

    def _save_current_ui(self) -> None:
        preset_id = self._selected_id()
        if not preset_id:
            return
        name = str(self.presets[preset_id].get("name") or preset_id)
        self.presets[preset_id] = self.main_window._build_slot_preset(name)
        self._reload_list(preset_id)

    def _delete_preset(self) -> None:
        preset_id = self._selected_id()
        if not preset_id or len(self.presets) <= 1:
            QtWidgets.QMessageBox.information(self, "프리셋 삭제", "최소 한 개의 프리셋은 남아 있어야 합니다.")
            return
        self.presets.pop(preset_id, None)
        if self.active_id == preset_id:
            self.active_id = next(iter(self.presets))
        self._reload_list(self.active_id)

    def selected_preset_id(self) -> str:
        return self._selected_id() or self.active_id


class QuickSlotDeckSettingsDialog(QtWidgets.QDialog):
    """Rich Multi-Tab Settings Dialog for QuickSlot Deck."""

    def __init__(self, main_window: "QuickSlotDeckWindow", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("QuickSlot Deck ⚙️ 환경 설정")
        self.setMinimumSize(780, 720)
        self.setStyleSheet(glass_dialog_stylesheet())

        self._init_ui()
        self._load_settings()

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        # Header
        header = QtWidgets.QLabel("⚙️ QuickSlot Deck 시스템 및 환경 설정")
        header.setStyleSheet("font-size: 14pt; font-weight: 800; color: #172033;")
        layout.addWidget(header)

        # Tab Widget
        self.tabs = QtWidgets.QTabWidget()

        # Tab 1: System & Autostart
        self.tab_sys = QtWidgets.QWidget()
        self._init_sys_tab()
        self.tabs.addTab(self.tab_sys, "🚀 시작 & 시스템")

        # Tab 2: Grid & Layout
        self.tab_grid = QtWidgets.QWidget()
        self._init_grid_tab()
        self.tabs.addTab(self.tab_grid, "▦ 그리드 & 타일")

        # Tab 3: Appearance & Theme
        self.tab_theme = QtWidgets.QWidget()
        self._init_theme_tab()
        self.tabs.addTab(self.tab_theme, "🎨 테마 & 스타일")

        # Tab 4: Radial Menu Customization
        self.tab_radial = QtWidgets.QWidget()
        self._init_radial_tab()
        self.tabs.addTab(self.tab_radial, "🎯 라디얼 메뉴")

        # Tab 5: Backup & Import/Export
        self.tab_backup = QtWidgets.QWidget()
        self._init_backup_tab()
        self.tabs.addTab(self.tab_backup, "💾 백업 & 복원")

        layout.addWidget(self.tabs, 1)

        # Buttons
        btn_box = QtWidgets.QHBoxLayout()
        btn_cancel = QtWidgets.QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)

        btn_apply = QtWidgets.QPushButton("적용 및 저장")
        btn_apply.setStyleSheet("background: #DCEEFF; border: 1.5px solid #72B2EE; color: #173A5E; font-weight: 800; padding: 8px 22px; border-radius: 9px;")
        btn_apply.clicked.connect(self._apply_settings)

        btn_box.addStretch(1)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_apply)

        layout.addLayout(btn_box)

    def _init_sys_tab(self) -> None:
        form = QtWidgets.QFormLayout(self.tab_sys)
        form.setSpacing(14)

        self.autostart_check = QtWidgets.QCheckBox("윈도우 부팅 시 QuickSlot Deck 자동 실행")
        self.autostart_check.setStyleSheet("font-weight: 700; color: #168566;")

        self.start_min_check = QtWidgets.QCheckBox("시작 시 트레이(최소화) 모드로 실행")
        self.topmost_check = QtWidgets.QCheckBox("프로그램 실행 시 항상 위(TopMost) 고정")

        # Opacity slider
        opac_box = QtWidgets.QHBoxLayout()
        self.opac_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.opac_slider.setRange(20, 100)
        self.opac_spin = QtWidgets.QSpinBox()
        self.opac_spin.setRange(20, 100)
        self.opac_spin.setSuffix(" %")

        self.opac_slider.valueChanged.connect(self.opac_spin.setValue)
        self.opac_spin.valueChanged.connect(self.opac_slider.setValue)

        opac_box.addWidget(self.opac_slider, 1)
        opac_box.addWidget(self.opac_spin)

        form.addRow("윈도우 시작:", self.autostart_check)
        form.addRow("트레이 시작:", self.start_min_check)
        form.addRow("항상 위 고정:", self.topmost_check)
        form.addRow("창 기본 투명도:", opac_box)

    def _init_grid_tab(self) -> None:
        form = QtWidgets.QFormLayout(self.tab_grid)
        form.setSpacing(14)

        self.grid_preset_combo = QtWidgets.QComboBox()
        self.grid_preset_combo.addItems([
            "3 x 5 그리드 (15 슬롯)",
            "4 x 4 그리드 (16 슬롯)",
            "3 x 3 그리드 (9 슬롯)",
            "2 x 4 그리드 (8 슬롯)",
            "4 x 5 그리드 (20 슬롯)",
            "2 x 6 그리드 (12 슬롯)",
        ])
        self.grid_preset_combo.currentIndexChanged.connect(self._on_grid_live_changed)

        self.tile_scale_combo = QtWidgets.QComboBox()
        self.tile_scale_combo.addItem("기본 크기 (100%)", 1.0)
        self.tile_scale_combo.addItem("절반 크기 (1/2)", 0.5)
        self.tile_scale_combo.addItem("초소형 (1/4)", 0.25)
        self.tile_scale_combo.currentIndexChanged.connect(self._on_tile_scale_changed)

        # Border radius
        radius_box = QtWidgets.QHBoxLayout()
        self.radius_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.radius_slider.setRange(0, 24)
        self.radius_spin = QtWidgets.QSpinBox()
        self.radius_spin.setRange(0, 24)
        self.radius_spin.setSuffix(" px")
        self.radius_slider.valueChanged.connect(self.radius_spin.setValue)
        self.radius_spin.valueChanged.connect(self.radius_slider.setValue)
        self.radius_spin.valueChanged.connect(self._on_radius_live_changed)

        radius_box.addWidget(self.radius_slider, 1)
        radius_box.addWidget(self.radius_spin)

        # Spacing
        gap_box = QtWidgets.QHBoxLayout()
        self.gap_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.gap_slider.setRange(4, 20)
        self.gap_spin = QtWidgets.QSpinBox()
        self.gap_spin.setRange(4, 20)
        self.gap_spin.setSuffix(" px")
        self.gap_slider.valueChanged.connect(self.gap_spin.setValue)
        self.gap_spin.valueChanged.connect(self.gap_slider.setValue)
        self.gap_spin.valueChanged.connect(self._on_gap_live_changed)

        gap_box.addWidget(self.gap_slider, 1)
        gap_box.addWidget(self.gap_spin)

        self.hover_glow_check = QtWidgets.QCheckBox("마우스 호버 시 카드 테두리 글로우 효과")
        self.hover_glow_check.setChecked(True)
        self.hover_glow_check.toggled.connect(self._on_hover_glow_live_changed)

        self.compact_fit_check = QtWidgets.QCheckBox("등록된 슬롯만 표시하고 프로그램 창 크기 자동 맞춤")
        self.compact_fit_check.setStyleSheet("font-weight: 700; color: #2879B9;")
        self.compact_fit_check.setChecked(True)
        self.compact_fit_check.setEnabled(False)

        # Window Size (Width x Height) controls
        win_size_box = QtWidgets.QHBoxLayout()
        self.win_w_spin = QtWidgets.QSpinBox()
        self.win_w_spin.setRange(60, 3840)
        self.win_w_spin.setSuffix(" px")

        self.win_h_spin = QtWidgets.QSpinBox()
        self.win_h_spin.setRange(60, 2160)
        self.win_h_spin.setSuffix(" px")

        self.win_w_spin.valueChanged.connect(self._on_win_size_changed)
        self.win_h_spin.valueChanged.connect(self._on_win_size_changed)

        btn_size_sm = QtWidgets.QPushButton("1/4")
        btn_size_sm.clicked.connect(lambda: self._set_tile_scale(0.25))
        btn_size_md = QtWidgets.QPushButton("1/2")
        btn_size_md.clicked.connect(lambda: self._set_tile_scale(0.5))
        btn_size_lg = QtWidgets.QPushButton("기본")
        btn_size_lg.clicked.connect(lambda: self._set_tile_scale(1.0))

        win_size_box.addWidget(QtWidgets.QLabel("가로:"))
        win_size_box.addWidget(self.win_w_spin)
        win_size_box.addWidget(QtWidgets.QLabel("세로:"))
        win_size_box.addWidget(self.win_h_spin)
        win_size_box.addWidget(btn_size_sm)
        win_size_box.addWidget(btn_size_md)
        win_size_box.addWidget(btn_size_lg)

        # Empty Slot Opacity slider
        empty_opac_box = QtWidgets.QHBoxLayout()
        self.empty_opac_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.empty_opac_slider.setRange(0, 100)
        self.empty_opac_spin = QtWidgets.QSpinBox()
        self.empty_opac_spin.setRange(0, 100)
        self.empty_opac_spin.setSuffix(" %")

        self.empty_opac_slider.valueChanged.connect(self.empty_opac_spin.setValue)
        self.empty_opac_spin.valueChanged.connect(self.empty_opac_slider.setValue)
        self.empty_opac_spin.valueChanged.connect(self._on_empty_opacity_live_changed)

        empty_opac_box.addWidget(self.empty_opac_slider, 1)
        empty_opac_box.addWidget(self.empty_opac_spin)

        self.show_empty_slots_check = QtWidgets.QCheckBox("빈 슬롯도 그리드에 표시")
        self.show_empty_slots_check.toggled.connect(self._on_show_empty_slots_toggled)

        form.addRow("슬롯 창 자동 축소:", self.compact_fit_check)
        form.addRow("슬롯 크기 프리셋:", self.tile_scale_combo)
        form.addRow("창 수동 크기:", win_size_box)
        form.addRow("그리드 레이아웃:", self.grid_preset_combo)
        form.addRow("빈 슬롯:", self.show_empty_slots_check)
        form.addRow("빈 슬롯 투명도:", empty_opac_box)
        form.addRow("카드 모서리 둥글기:", radius_box)
        form.addRow("타일 간격 (Gap):", gap_box)
        form.addRow("시각 효과:", self.hover_glow_check)

    def _set_win_size(self, w: int, h: int) -> None:
        self.win_w_spin.setValue(w)
        self.win_h_spin.setValue(h)
        self.main_window.resize_grid_from_width(w)

    def _set_tile_scale(self, scale: float) -> None:
        for index in range(self.tile_scale_combo.count()):
            if abs(float(self.tile_scale_combo.itemData(index)) - scale) < 0.001:
                self.tile_scale_combo.setCurrentIndex(index)
                return

    def _on_compact_fit_toggled(self, checked: bool) -> None:
        if getattr(self, "_loading_settings", False):
            return
        self.main_window.config["compact_auto_fit"] = checked
        self.main_window.refresh_slots()

    def _on_win_size_changed(self) -> None:
        if getattr(self, "_loading_settings", False):
            return
        if self.sender() is self.win_h_spin:
            self.main_window.resize_grid_from_height(self.win_h_spin.value())
        else:
            self.main_window.resize_grid_from_width(self.win_w_spin.value())

    def _on_tile_scale_changed(self, _index: int) -> None:
        if getattr(self, "_loading_settings", False):
            return
        self.main_window.config["tile_scale"] = float(self.tile_scale_combo.currentData())
        self.main_window.refresh_slots()
        with QtCore.QSignalBlocker(self.win_w_spin), QtCore.QSignalBlocker(self.win_h_spin):
            self.win_w_spin.setValue(self.main_window.width())
            self.win_h_spin.setValue(self.main_window.height())
        self.main_window._capture_active_slot_preset()
        self.main_window._save_config()

    def _on_empty_opacity_live_changed(self, val: int) -> None:
        if getattr(self, "_loading_settings", False):
            return
        self.main_window.config["empty_slot_opacity"] = val
        self.main_window.refresh_slots()

    def _on_show_empty_slots_toggled(self, checked: bool) -> None:
        if getattr(self, "_loading_settings", False):
            return
        self.main_window.config["show_empty_slots"] = checked
        if checked and self.empty_opac_spin.value() <= 0:
            self.empty_opac_spin.setValue(38)
        self.empty_opac_slider.setEnabled(checked)
        self.empty_opac_spin.setEnabled(checked)
        self.main_window.refresh_slots()

    def _on_radius_live_changed(self, val: int) -> None:
        if getattr(self, "_loading_settings", False):
            return
        self.main_window.config["tile_radius"] = val
        self.main_window.refresh_slots()

    def _on_gap_live_changed(self, val: int) -> None:
        if getattr(self, "_loading_settings", False):
            return
        self.main_window.config["tile_gap"] = val
        self.main_window.refresh_slots()

    def _on_hover_glow_live_changed(self, checked: bool) -> None:
        if getattr(self, "_loading_settings", False):
            return
        self.main_window.config["hover_glow"] = checked
        self.main_window.refresh_slots()

    def _on_grid_live_changed(self, index: int) -> None:
        if getattr(self, "_loading_settings", False):
            return
        presets = [(3, 5), (4, 4), (3, 3), (2, 4), (4, 5), (2, 6)]
        if 0 <= index < len(presets):
            r, c = presets[index]
            self.main_window.rows = r
            self.main_window.cols = c
            self.main_window.current_page = 0
            self.main_window._save_config()
            self.main_window.refresh_slots()

    def _on_theme_live_changed(self, index: int) -> None:
        if getattr(self, "_loading_settings", False):
            return
        self.main_window.config["theme_index"] = index
        theme = THEMES.get(index, THEMES[0])
        if theme.get("icon_only"):
            scale = float(theme.get("recommended_scale", 0.5))
            self.main_window.config["tile_scale"] = scale
            self.main_window.config["tile_gap"] = int(theme.get("recommended_gap", 5))
            self.main_window.config["tile_radius"] = int(theme.get("recommended_radius", 8))
            with QtCore.QSignalBlocker(self.tile_scale_combo), QtCore.QSignalBlocker(self.gap_spin), QtCore.QSignalBlocker(self.radius_spin):
                scale_index = min(range(self.tile_scale_combo.count()), key=lambda idx: abs(float(self.tile_scale_combo.itemData(idx)) - scale))
                self.tile_scale_combo.setCurrentIndex(scale_index)
                self.gap_spin.setValue(self.main_window.config["tile_gap"])
                self.radius_spin.setValue(self.main_window.config["tile_radius"])
        self.main_window._apply_theme()
        if theme.get("icon_only"):
            self.main_window.refresh_slots()
        else:
            for button in self.main_window.buttons:
                button._update_appearance()
                button.update()
            self.main_window.update()

    def _init_theme_tab(self) -> None:
        form = QtWidgets.QFormLayout(self.tab_theme)
        form.setSpacing(14)

        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItems([THEMES[index]["name"] for index in sorted(THEMES)])
        self.theme_combo.currentIndexChanged.connect(self._on_theme_live_changed)

        self.auto_stretch_default = QtWidgets.QCheckBox("커스텀 이미지 로드 시 100% 가득 채우기 자동 기본 설정")
        self.auto_stretch_default.setChecked(True)

        form.addRow("컬러 테마 픽커:", self.theme_combo)
        form.addRow("이미지 옵션:", self.auto_stretch_default)

    def _init_radial_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.tab_radial)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        info = QtWidgets.QLabel("원하는 기능을 아래 팔레트에서 원형 슬롯으로 끌어다 놓으세요.")
        info.setAlignment(QtCore.Qt.AlignCenter)
        info.setStyleSheet("color: #536278; font-size: 10pt; font-weight: 700;")
        layout.addWidget(info)

        self.radial_preview = RadialWheelPreviewWidget(self)
        self.radial_preview.slot_changed.connect(self._on_preview_slot_changed)
        layout.addWidget(self.radial_preview, 0, QtCore.Qt.AlignHCenter)

        palette_label = QtWidgets.QLabel("기능 팔레트")
        palette_label.setStyleSheet("color: #172033; font-size: 10pt; font-weight: 800;")
        layout.addWidget(palette_label)

        palette_card = QtWidgets.QFrame()
        palette_card.setStyleSheet("QFrame { background: rgba(255,255,255,0.82); border: 1px solid #CBD5E1; border-radius: 12px; }")
        palette_grid = QtWidgets.QGridLayout(palette_card)
        palette_grid.setContentsMargins(12, 12, 12, 12)
        palette_grid.setHorizontalSpacing(8)
        palette_grid.setVerticalSpacing(8)

        for index, (k, (title, icon_text, color)) in enumerate(RADIAL_ACTION_PRESETS.items()):
            chip = DraggableActionChip(k, title, icon_text, color, self)
            chip.setMinimumHeight(34)
            palette_grid.addWidget(chip, index // 4, index % 4)

        layout.addWidget(palette_card)
        layout.addStretch(1)

    def _on_preview_slot_changed(self, slot_idx: int, new_key: str) -> None:
        self.radial_preview.selected_slot = slot_idx

    def _init_backup_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.tab_backup)
        layout.setSpacing(12)

        info_label = QtWidgets.QLabel(
            "💾 QuickSlot Deck의 모든 매크로 슬롯 설정과 내장 커스텀 아이콘(Base64) 데이터를 백업 파일(.json)로 내보내거나 복원할 수 있습니다."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #637187; font-size: 9.5pt;")
        layout.addWidget(info_label)

        btn_export = QtWidgets.QPushButton("📤 백업 파일 내보내기 (Export .json)")
        btn_export.setStyleSheet("background: #E8F3FF; border: 1.5px solid #7DB8EE; color: #205A8C; font-weight: 700; padding: 10px; border-radius: 9px;")
        btn_export.clicked.connect(self._export_config)

        btn_import = QtWidgets.QPushButton("📥 백업 파일 가져오기 (Import .json)")
        btn_import.setStyleSheet("background: #EAFBF4; border: 1.5px solid #74C9A9; color: #176B50; font-weight: 700; padding: 10px; border-radius: 9px;")
        btn_import.clicked.connect(self._import_config)

        btn_reset_all = QtWidgets.QPushButton("⚠️ 모든 슬롯 및 설정 초기화")
        btn_reset_all.setStyleSheet("background: #FFF1F2; border: 1.5px solid #FDA4AF; color: #BE123C; font-weight: 700; padding: 10px; border-radius: 9px;")
        btn_reset_all.clicked.connect(self._reset_all_config)

        layout.addWidget(btn_export)
        layout.addWidget(btn_import)
        layout.addWidget(btn_reset_all)
        layout.addStretch(1)

    def _load_settings(self) -> None:
        self._loading_settings = True
        try:
            self.autostart_check.setChecked(is_autostart_enabled())
            self.start_min_check.setChecked(bool(self.main_window.config.get("start_minimized", False)))
            self.topmost_check.setChecked(self.main_window.always_on_top)
            self.opac_spin.setValue(self.main_window.opacity_val)

            self.win_w_spin.setValue(self.main_window.width())
            self.win_h_spin.setValue(self.main_window.height())

            grid_map = {(3, 5): 0, (4, 4): 1, (3, 3): 2, (2, 4): 3, (4, 5): 4, (2, 6): 5}
            self.grid_preset_combo.setCurrentIndex(grid_map.get((self.main_window.rows, self.main_window.cols), 0))
            tile_scale = float(self.main_window.config.get("tile_scale", 1.0))
            scale_index = min(
                range(self.tile_scale_combo.count()),
                key=lambda idx: abs(float(self.tile_scale_combo.itemData(idx)) - tile_scale),
            )
            self.tile_scale_combo.setCurrentIndex(scale_index)

            self.compact_fit_check.setChecked(True)
            show_empty = bool(self.main_window.config.get("show_empty_slots", False))
            self.show_empty_slots_check.setChecked(show_empty)
            self.empty_opac_spin.setValue(int(self.main_window.config.get("empty_slot_opacity", 0)))
            self.empty_opac_slider.setEnabled(show_empty)
            self.empty_opac_spin.setEnabled(show_empty)
            self.radius_spin.setValue(int(self.main_window.config.get("tile_radius", 14)))
            self.gap_spin.setValue(int(self.main_window.config.get("tile_gap", 10)))
            self.hover_glow_check.setChecked(bool(self.main_window.config.get("hover_glow", True)))
            self.theme_combo.setCurrentIndex(int(self.main_window.config.get("theme_index", 0)))
            self.auto_stretch_default.setChecked(bool(self.main_window.config.get("auto_stretch_default", True)))

            radial_keys = list(self.main_window.config.get("radial_items") or ["prev_page", "next_page", "settings", "preset_settings", "emergency", "refresh", "studio", "topmost"])
            self.radial_preview.set_radial_keys(radial_keys)
        finally:
            self._loading_settings = False

    def _apply_settings(self) -> None:
        autostart = self.autostart_check.isChecked()
        set_autostart_enabled(autostart, self.main_window.repository.root)

        self.main_window.config["start_minimized"] = self.start_min_check.isChecked()
        self.main_window.always_on_top = self.topmost_check.isChecked()
        self.main_window._apply_always_on_top()

        self.main_window.opacity_val = self.opac_spin.value()
        self.main_window.setWindowOpacity(self.main_window.opacity_val / 100.0)

        grid_presets = [(3, 5), (4, 4), (3, 3), (2, 4), (4, 5), (2, 6)]
        r, c = grid_presets[self.grid_preset_combo.currentIndex()]
        self.main_window.rows = r
        self.main_window.cols = c

        self.main_window.config["compact_auto_fit"] = True
        self.main_window.config["show_empty_slots"] = self.show_empty_slots_check.isChecked()
        self.main_window.config["empty_slot_opacity"] = self.empty_opac_spin.value()
        self.main_window.config["tile_radius"] = self.radius_spin.value()
        self.main_window.config["tile_gap"] = self.gap_spin.value()
        self.main_window.config["tile_scale"] = float(self.tile_scale_combo.currentData())
        self.main_window.config["hover_glow"] = self.hover_glow_check.isChecked()
        self.main_window.config["theme_index"] = self.theme_combo.currentIndex()
        self.main_window.config["auto_stretch_default"] = self.auto_stretch_default.isChecked()

        radial_items = list(self.radial_preview.items_keys)
        self.main_window.config["radial_items"] = radial_items
        self.main_window.radial_menu.load_custom_items(radial_items)

        self.main_window._capture_active_slot_preset()
        self.main_window._save_config()
        self.main_window._apply_theme()
        for button in self.main_window.buttons:
            button._update_appearance()
            button.update()

        self.accept()

    def _export_config(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "QuickSlot 덱 백업 내보내기", "quickslot_deck_backup.json", "JSON 파일 (*.json)"
        )
        if path:
            self.main_window._save_config()
            content = self.main_window.config_path.read_text(encoding="utf-8")
            Path(path).write_text(content, encoding="utf-8")
            QtWidgets.QMessageBox.information(self, "백업 완료", f"설정 및 백업 아이콘이 내보내졌습니다.\n{path}")

    def _import_config(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "QuickSlot 덱 백업 불러오기", "", "JSON 파일 (*.json)"
        )
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.main_window.config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                self.main_window._load_config()
                self.main_window._ensure_slot_presets()
                self.main_window._refresh_preset_radial_menu()
                self.main_window.refresh_slots()
                QtWidgets.QMessageBox.information(self, "복원 완료", "성공적으로 설정을 복원했습니다!")
                self.accept()
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "오류", f"백업 파일 로드 실패: {e}")

    def _reset_all_config(self) -> None:
        ans = QtWidgets.QMessageBox.warning(
            self,
            "전체 초기화 경고",
            "정말로 모든 슬롯의 커스텀 아이콘과 레이아웃 설정을 초기화하시겠습니까?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if ans == QtWidgets.QMessageBox.Yes:
            self.main_window.custom_icons.clear()
            self.main_window._capture_active_slot_preset()
            self.main_window._save_config()
            self.main_window.refresh_slots()
            QtWidgets.QMessageBox.information(self, "초기화 완료", "모든 커스텀 아이콘 설정이 초기화되었습니다.")
            self.accept()


def load_transparent_radial_icon(file_path: str) -> QtGui.QPixmap:
    """Load an image file and convert white background pixels to transparent RGBA QPixmap."""
    if not os.path.exists(file_path):
        return QtGui.QPixmap()
    try:
        from PIL import Image
        img = Image.open(file_path).convert('RGBA')
        datas = img.getdata()
        new_data = []
        for item in datas:
            r, g, b, a = item
            if r > 225 and g > 225 and b > 225:
                new_data.append((r, g, b, 0))
            else:
                new_data.append((r, g, b, a))
        img.putdata(new_data)

        import io
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        raw_bytes = buffer.getvalue()

        pix = QtGui.QPixmap()
        if pix.loadFromData(raw_bytes) and not pix.isNull():
            return pix
    except Exception:
        pass

    try:
        pix = QtGui.QPixmap(file_path)
        if not pix.isNull():
            return pix
    except Exception:
        pass

    return QtGui.QPixmap()


class RadialMenuIconButton(QtWidgets.QPushButton):
    """Circular Menu Button displaying transparent custom 레디얼.png icon."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__("", parent)
        self.setFixedSize(36, 36)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 18px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(106, 85, 255, 0.2);
                border: 1.5px solid #7C6CFF;
            }
            QPushButton:pressed {
                background-color: rgba(106, 85, 255, 0.4);
            }
        """)

        app_root = Path(__file__).resolve().parents[1]
        icon_paths = [
            str(app_root / "radial-icon-transparent.png"),
            str(app_root / "radial-icon.png"),
        ]
        pix = QtGui.QPixmap()
        for p in icon_paths:
            if os.path.exists(p):
                loaded = load_transparent_radial_icon(p)
                if not loaded.isNull():
                    pix = loaded
                    break

        if not pix.isNull():
            self.setIcon(QtGui.QIcon(pix))
            self.setIconSize(QtCore.QSize(32, 32))
        else:
            self.setText("🎯")


class QuickSlotDeckWindow(QtWidgets.QMainWindow):
    """Stream Deck Style Standalone QuickSlot Window with full fill and text position controls."""

    def __init__(self, repository: Optional[MacroRepository] = None, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setDoubleClickInterval(QUICKSLOT_DOUBLE_CLICK_INTERVAL_MS)
        self.repository = repository or MacroRepository()
        self.config_path = self.repository.root / ".quickslot_deck_config.json"
        self.active_processes: Dict[int, tuple[str, subprocess.Popen[Any]]] = {}

        self.always_on_top = True
        self.opacity_val = 100
        self.rows = 3
        self.cols = 5
        self.current_page = 0
        self.config: Dict[str, Any] = {
            "start_minimized": False,
            "tile_radius": 14,
            "tile_gap": 10,
            "tile_scale": 1.0,
            "hover_glow": True,
            "theme_index": 0,
            "auto_stretch_default": True,
            "empty_slot_opacity": 0,
            "show_empty_slots": False,
            "radial_items": ["prev_page", "next_page", "settings", "deck_dock", "preset_settings", "emergency", "studio", "topmost"],
            "slot_presets": {},
            "active_slot_preset": "default",
        }
        self.custom_icons: Dict[str, Dict[str, Any]] = {}
        self.buttons: List[StreamDeckButton] = []
        self._drag_press_global: Optional[QtCore.QPoint] = None
        self._drag_window_origin: Optional[QtCore.QPoint] = None
        self._drag_threshold = 12
        self._is_dragging_window = False
        self._hold_timer = QtCore.QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_mouse_hold_timeout)
        self._hold_start_pos: Optional[QtCore.QPoint] = None
        self._hold_popup_pos: Optional[QtCore.QPoint] = None
        self._hold_triggered = False
        self._visible_cols = 1
        self._visible_rows = 1
        self._applying_slot_preset = False

        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

        self.setWindowTitle("MacroRelay QuickSlot Deck")
        self.setObjectName("AppRoot")
        self.setStyleSheet(stylesheet())

        icon_path = self.repository.root / "branding" / "macrorelay-quickslot.ico"
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        self._load_config()
        self._ensure_slot_presets()
        self.radial_menu = RadialPieMenuWidget(self)
        self.radial_menu.load_custom_items(self.config.get("radial_items"))
        self.radial_menu.action_triggered.connect(self._handle_radial_action)
        self.preset_radial_menu = RadialPieMenuWidget(self)
        self.preset_radial_menu.action_triggered.connect(self._handle_preset_radial_action)
        self._refresh_preset_radial_menu()

        self._init_ui()
        self._apply_theme()
        self._setup_watcher()

        # Polling timer for active macro states
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(800)
        self.poll_timer.timeout.connect(self.refresh_states)
        self.poll_timer.start()

        self.refresh_slots()

    def _load_config(self) -> None:
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.always_on_top = bool(data.get("always_on_top", True))
                self.opacity_val = int(data.get("opacity", 100))
                self.rows = int(data.get("rows", 3))
                self.cols = int(data.get("cols", 5))
                self.custom_icons = dict(data.get("custom_icons") or {})
                if "config" in data and isinstance(data["config"], dict):
                    self.config.update(data["config"])
                self.config["compact_auto_fit"] = True
                radial_items = list(self.config.get("radial_items") or [])
                if "deck_dock" not in radial_items:
                    if "refresh" in radial_items:
                        radial_items[radial_items.index("refresh")] = "deck_dock"
                    elif len(radial_items) < 8:
                        radial_items.append("deck_dock")
                    elif radial_items:
                        radial_items[3] = "deck_dock"
                if "preset_settings" not in radial_items:
                    if "add_slot" in radial_items:
                        radial_items[radial_items.index("add_slot")] = "preset_settings"
                    elif len(radial_items) < 8:
                        radial_items.append("preset_settings")
                    elif radial_items:
                        radial_items[-1] = "preset_settings"
                    self.config["radial_items"] = radial_items
                geom = data.get("geometry")
                if geom:
                    self.restoreGeometry(QtCore.QByteArray.fromHex(geom.encode("ascii")))

                # Auto-migrate local image_path to embedded Base64 image_data
                updated_any = False
                for slot_key, icon_cfg in self.custom_icons.items():
                    if isinstance(icon_cfg, dict):
                        img_path = str(icon_cfg.get("image_path", "")).strip()
                        b64_data = str(icon_cfg.get("image_data", "")).strip()
                        if img_path and not b64_data and os.path.exists(img_path):
                            new_b64 = encode_image_file_to_base64(img_path)
                            if new_b64:
                                icon_cfg["image_data"] = new_b64
                                updated_any = True
                if updated_any:
                    self._save_config()
            except Exception:
                pass

    def _save_config(self) -> None:
        try:
            data = {
                "always_on_top": self.always_on_top,
                "opacity": self.opacity_val,
                "rows": self.rows,
                "cols": self.cols,
                "custom_icons": self.custom_icons,
                "geometry": bytes(self.saveGeometry().toHex()).decode("ascii"),
                "config": self.config,
            }
            temp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
            temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp_path, self.config_path)
        except Exception:
            pass

    def _build_slot_preset(self, name: str) -> Dict[str, Any]:
        hotkeys = self.repository.load_hotkeys()
        return {
            "name": str(name).strip() or "이름 없는 모드",
            "slots": copy.deepcopy(list(hotkeys.get("slots") or [])),
            "deck_page_count": max(1, int(hotkeys.get("deck_page_count") or 1)),
            "deck_page_names": copy.deepcopy(list(hotkeys.get("deck_page_names") or [])),
            "custom_icons": copy.deepcopy(self.custom_icons),
            "rows": int(self.rows),
            "cols": int(self.cols),
            "style": {key: copy.deepcopy(self.config.get(key)) for key in PRESET_STYLE_KEYS},
        }

    def _ensure_slot_presets(self) -> None:
        presets = self.config.get("slot_presets")
        if not isinstance(presets, dict) or not presets:
            presets = {"default": self._build_slot_preset("기본 모드")}
            self.config["slot_presets"] = presets
            self.config["active_slot_preset"] = "default"
        active_id = str(self.config.get("active_slot_preset") or "")
        if active_id not in presets:
            self.config["active_slot_preset"] = next(iter(presets))

    def _capture_active_slot_preset(self) -> None:
        if self._applying_slot_preset:
            return
        self._ensure_slot_presets()
        presets = self.config["slot_presets"]
        active_id = str(self.config.get("active_slot_preset"))
        current = presets.get(active_id, {})
        name = str(current.get("name") or active_id)
        presets[active_id] = self._build_slot_preset(name)

    def _save_preset_hotkeys(self, hotkeys: Dict[str, Any]) -> None:
        path = str(self.repository.hotkeys_path)
        watcher = getattr(self, "watcher", None)
        was_watched = bool(watcher and path in watcher.files())
        if was_watched:
            watcher.removePath(path)
        try:
            self.repository.save_hotkeys(hotkeys)
        finally:
            if was_watched and os.path.exists(path):
                watcher.addPath(path)

    def _switch_slot_preset(self, preset_id: str) -> None:
        self._ensure_slot_presets()
        presets = self.config["slot_presets"]
        if preset_id not in presets:
            return
        if preset_id == str(self.config.get("active_slot_preset")):
            return

        self._capture_active_slot_preset()
        preset = copy.deepcopy(presets[preset_id])
        self._applying_slot_preset = True
        try:
            self.stop_all_macros()
            hotkeys = self.repository.load_hotkeys()
            hotkeys["slots"] = copy.deepcopy(list(preset.get("slots") or []))
            hotkeys["deck_page_count"] = max(1, int(preset.get("deck_page_count") or 1))
            hotkeys["deck_page_names"] = copy.deepcopy(list(preset.get("deck_page_names") or []))
            self._save_preset_hotkeys(hotkeys)
            self.custom_icons = copy.deepcopy(dict(preset.get("custom_icons") or {}))
            self.rows = max(1, int(preset.get("rows", self.rows)))
            self.cols = max(1, int(preset.get("cols", self.cols)))
            style = dict(preset.get("style") or {})
            for key in PRESET_STYLE_KEYS:
                if key in style and style[key] is not None:
                    self.config[key] = copy.deepcopy(style[key])
            self.config["active_slot_preset"] = preset_id
            self.current_page = 0
            self._apply_theme()
            self.refresh_slots()
        finally:
            self._applying_slot_preset = False
        self._save_config()

    def _init_ui(self) -> None:
        self.setMinimumSize(64, 64)
        if not self.geometry().isValid():
            self.resize(1060, 620)

        self._apply_always_on_top()
        self.setWindowOpacity(self.opacity_val / 100.0)

        central = QtWidgets.QFrame()
        central.setObjectName("CentralFrame")
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(0)

        # -------------------------------------------------------------------
        # Touch Swipe Grid Container Area (Fills space cleanly 100%)
        # -------------------------------------------------------------------
        self.swipe_container = TouchSwipeWidget()
        self.swipe_container.setStyleSheet("background: transparent; border: none;")
        self.grid_layout = QtWidgets.QGridLayout(self.swipe_container)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(10)

        main_layout.addWidget(self.swipe_container, 1)

    def _apply_theme(self) -> None:
        theme_idx = int(self.config.get("theme_index", 0))
        theme = THEMES.get(theme_idx, THEMES[0])
        central = self.centralWidget()
        if central:
            central.setStyleSheet(f"""
                #CentralFrame {{
                    background-color: {theme['central_bg']};
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 14px;
                }}
            """)

    def _start_window_drag_candidate(self, event: QtGui.QMouseEvent) -> bool:
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_press_global = event.globalPosition().toPoint()
            self._drag_window_origin = self.pos()
            self._is_dragging_window = False
        return False

    def _handle_window_drag_move(self, event: QtGui.QMouseEvent) -> bool:
        if not (event.buttons() & QtCore.Qt.LeftButton) or self._drag_press_global is None:
            return False
        if self._hold_triggered:
            return False

        delta = event.globalPosition().toPoint() - self._drag_press_global
        if not self._is_dragging_window:
            if delta.manhattanLength() < self._drag_threshold:
                return False
            self._is_dragging_window = True
            self._cancel_mouse_hold_check()
            self.setCursor(QtCore.Qt.SizeAllCursor)

        if self._drag_window_origin is not None:
            self.move(self._drag_window_origin + delta)
        return True

    def _handle_window_drag_release(self, event: QtGui.QMouseEvent) -> bool:
        was_dragging = self._is_dragging_window
        self._is_dragging_window = False
        self._drag_press_global = None
        self._drag_window_origin = None
        if was_dragging:
            self.unsetCursor()
            self._save_config()
            return True
        return False

    def _get_resize_edge(self, pos: QtCore.QPoint) -> str:
        margin = 8
        w = self.width()
        h = self.height()
        x, y = pos.x(), pos.y()

        edge = ""
        if y < margin:
            edge += "top"
        elif y > h - margin:
            edge += "bottom"

        if x < margin:
            edge += "_left" if edge else "left"
        elif x > w - margin:
            edge += "_right" if edge else "right"

        return edge

    def _update_resize_cursor(self, edge: str) -> None:
        if edge in ("left", "right"):
            self.setCursor(QtCore.Qt.SizeHorCursor)
        elif edge in ("top", "bottom"):
            self.setCursor(QtCore.Qt.SizeVerCursor)
        elif edge in ("top_left", "bottom_right"):
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        elif edge in ("top_right", "bottom_left"):
            self.setCursor(QtCore.Qt.SizeBDiagCursor)
        elif not getattr(self, "_is_dragging_window", False):
            self.unsetCursor()

    def _handle_border_resize(self, global_pos: QtCore.QPoint) -> None:
        if not hasattr(self, "_resize_start_geom") or not self._resize_start_geom:
            return
        orig = self._resize_start_geom
        dx = global_pos.x() - self._resize_start_pos.x()
        dy = global_pos.y() - self._resize_start_pos.y()

        edge = getattr(self, "_resize_edge", "")
        proposed_w = orig.width() + dx if "right" in edge else orig.width() - dx
        proposed_h = orig.height() + dy if "bottom" in edge else orig.height() - dy
        side_w = self._tile_side_from_width(proposed_w)
        side_h = self._tile_side_from_height(proposed_h)

        if ("left" in edge or "right" in edge) and ("top" in edge or "bottom" in edge):
            horizontal_change = abs(dx) / max(1, orig.width())
            vertical_change = abs(dy) / max(1, orig.height())
            tile_side = side_w if horizontal_change >= vertical_change else side_h
        elif "left" in edge or "right" in edge:
            tile_side = side_w
        else:
            tile_side = side_h

        target = self._size_for_tile_side(max(MIN_TILE_SIDE, tile_side))
        new_x = orig.right() - target.width() + 1 if "left" in edge else orig.x()
        new_y = orig.bottom() - target.height() + 1 if "top" in edge else orig.y()
        self.setGeometry(new_x, new_y, target.width(), target.height())

    def _grid_extras(self) -> tuple[int, int]:
        gap = int(self.config.get("tile_gap", 10))
        return (
            WINDOW_PADDING + max(0, self._visible_cols - 1) * gap,
            WINDOW_PADDING + max(0, self._visible_rows - 1) * gap,
        )

    def _size_for_tile_side(self, tile_side: float) -> QtCore.QSize:
        extra_w, extra_h = self._grid_extras()
        return QtCore.QSize(
            max(1, round(self._visible_cols * tile_side + extra_w)),
            max(1, round(self._visible_rows * tile_side + extra_h)),
        )

    def _tile_side_from_width(self, width: int) -> float:
        extra_w, _ = self._grid_extras()
        return max(MIN_TILE_SIDE, (width - extra_w) / max(1, self._visible_cols))

    def _tile_side_from_height(self, height: int) -> float:
        _, extra_h = self._grid_extras()
        return max(MIN_TILE_SIDE, (height - extra_h) / max(1, self._visible_rows))

    def resize_grid_from_width(self, width: int) -> None:
        self.resize(self._size_for_tile_side(self._tile_side_from_width(width)))

    def resize_grid_from_height(self, height: int) -> None:
        self.resize(self._size_for_tile_side(self._tile_side_from_height(height)))

    def _start_mouse_hold_check(self, global_pos: QtCore.QPoint) -> None:
        self._hold_start_pos = global_pos
        self._hold_popup_pos = global_pos
        self._hold_triggered = False
        self._hold_timer.stop()
        self._hold_timer.start(1000)

    def _cancel_mouse_hold_check(self) -> None:
        if hasattr(self, "_hold_timer") and self._hold_timer:
            self._hold_timer.stop()
        self._hold_start_pos = None
        self._hold_popup_pos = None

    def _on_mouse_hold_timeout(self) -> None:
        if hasattr(self, "_hold_timer") and self._hold_timer:
            self._hold_timer.stop()

        if self._hold_popup_pos is not None and QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton:
            self._hold_triggered = True
            self._hold_start_pos = None
            self._show_preset_radial(self._hold_popup_pos, sticky=False)

    def _show_preset_radial(self, global_pos: QtCore.QPoint, sticky: bool) -> None:
        self._cancel_mouse_hold_check()
        self._refresh_preset_radial_menu()
        self.preset_radial_menu.popup_at(global_pos, sticky=sticky)

    def _refresh_preset_radial_menu(self) -> None:
        self._ensure_slot_presets()
        items = [
            (
                preset_id,
                ("● " if preset_id == str(self.config.get("active_slot_preset")) else "")
                + str(preset.get("name") or preset_id),
            )
            for preset_id, preset in self.config["slot_presets"].items()
        ]
        self.preset_radial_menu.load_deck_presets(items)

    def _handle_preset_radial_action(self, key: str) -> None:
        if key == "preset_settings":
            self._open_hold_preset_dialog()
            return
        if not key.startswith("deck:"):
            return
        self._switch_slot_preset(key.split(":", 1)[1])

    def _open_hold_preset_dialog(self) -> None:
        self._capture_active_slot_preset()
        dialog = SlotPresetDialog(self, self)
        position_dialog_beside(self, dialog)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        target_id = dialog.selected_preset_id()
        self.config["slot_presets"] = copy.deepcopy(dialog.presets)
        if target_id not in self.config["slot_presets"]:
            target_id = next(iter(self.config["slot_presets"]))
        preset = copy.deepcopy(self.config["slot_presets"][target_id])
        self._applying_slot_preset = True
        try:
            self.stop_all_macros()
            hotkeys = self.repository.load_hotkeys()
            hotkeys["slots"] = copy.deepcopy(list(preset.get("slots") or []))
            hotkeys["deck_page_count"] = max(1, int(preset.get("deck_page_count") or 1))
            hotkeys["deck_page_names"] = copy.deepcopy(list(preset.get("deck_page_names") or []))
            self._save_preset_hotkeys(hotkeys)
            self.custom_icons = copy.deepcopy(dict(preset.get("custom_icons") or {}))
            self.rows = max(1, int(preset.get("rows", self.rows)))
            self.cols = max(1, int(preset.get("cols", self.cols)))
            style = dict(preset.get("style") or {})
            for style_key in PRESET_STYLE_KEYS:
                if style_key in style and style[style_key] is not None:
                    self.config[style_key] = copy.deepcopy(style[style_key])
            self.config["active_slot_preset"] = target_id
            self.current_page = 0
            self._apply_theme()
            self.refresh_slots()
        finally:
            self._applying_slot_preset = False
        self._refresh_preset_radial_menu()
        self._save_config()

    def _handle_radial_action(self, key: str) -> None:
        if key == "prev_page":
            self._prev_page()
        elif key == "next_page":
            self._next_page()
        elif key == "settings":
            self._open_settings_dialog(target_tab=0)
        elif key == "deck_dock":
            self._open_deck_dock()
        elif key == "emergency":
            self.stop_all_macros()
        elif key == "refresh":
            self.refresh_slots()
        elif key == "studio":
            self._open_studio()
        elif key == "topmost":
            self.always_on_top = not self.always_on_top
            self._apply_always_on_top()
            self._save_config()
        elif key == "opacity":
            self._open_settings_dialog(target_tab=0)
        elif key == "grid":
            self._open_settings_dialog(target_tab=1)
        elif key == "add_slot":
            self._add_slot_from_radial()
        elif key == "preset_settings":
            self._open_hold_preset_dialog()
        elif key in ("close_app", "close", "exit"):
            self.close()
        elif key == "minimize":
            self.showMinimized()

    def _open_settings_dialog(self, target_tab: int = 0) -> None:
        dlg = QuickSlotDeckSettingsDialog(self, self)
        if 0 <= target_tab < dlg.tabs.count():
            dlg.tabs.setCurrentIndex(target_tab)
        position_dialog_beside(self, dlg)
        dlg.exec_()

    def _open_deck_dock(self) -> None:
        existing = getattr(self, "_deck_dock_window", None)
        if existing is not None and existing.isVisible():
            existing.showNormal()
            existing.raise_()
            existing.activateWindow()
            return
        from macro_studio.deck_dock import DeckDockWindow

        self._deck_dock_window = DeckDockWindow(self)
        screen = QtGui.QGuiApplication.screenAt(self.frameGeometry().center()) or self.screen()
        if screen:
            available = screen.availableGeometry()
            x = max(available.left(), min(self.frameGeometry().right() + 18, available.right() - self._deck_dock_window.width() + 1))
            y = max(available.top(), min(self.frameGeometry().top(), available.bottom() - self._deck_dock_window.height() + 1))
            self._deck_dock_window.move(x, y)
        self._deck_dock_window.show()
        self._deck_dock_window.raise_()
        self._deck_dock_window.activateWindow()

    # Window Drag Support for Frameless Window & Long-press / Right-click Radial Menu
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            edge = self._get_resize_edge(event.pos())
            if edge:
                self._resize_edge = edge
                self._resize_start_geom = self.geometry()
                self._resize_start_pos = event.globalPos()
                self._cancel_mouse_hold_check()
                event.accept()
                return

            self._start_window_drag_candidate(event)
            self._start_mouse_hold_check(event.globalPos())
            event.accept()
        elif event.button() == QtCore.Qt.RightButton:
            self.radial_menu.popup_at(event.globalPos())
            event.accept()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._show_preset_radial(event.globalPos(), sticky=True)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if getattr(self, "_resize_edge", ""):
            self._handle_border_resize(event.globalPos())
            event.accept()
            return

        if self._handle_window_drag_move(event):
            event.accept()
            return

        if not (event.buttons() & QtCore.Qt.LeftButton):
            edge = self._get_resize_edge(event.pos())
            self._update_resize_cursor(edge)

        if self._hold_start_pos and (event.globalPos() - self._hold_start_pos).manhattanLength() > 15:
            self._cancel_mouse_hold_check()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._cancel_mouse_hold_check()
        if getattr(self, "_resize_edge", ""):
            self._resize_edge = ""
            self._drag_press_global = None
            self._drag_window_origin = None
            self.unsetCursor()
            self._save_config()
            event.accept()
            return

        if self._handle_window_drag_release(event):
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _apply_always_on_top(self) -> None:
        flags = QtCore.Qt.FramelessWindowHint | QtCore.Qt.Window
        if self.always_on_top:
            flags |= QtCore.Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if self.isVisible():
            self.show()

    def _toggle_always_on_top(self, checked: bool) -> None:
        self.always_on_top = checked
        self._update_topmost_btn_style()
        self._apply_always_on_top()
        self._save_config()

    def _update_topmost_btn_style(self) -> None:
        if self.always_on_top:
            self.btn_topmost.setText("📌 최상위 ON")
            self.btn_topmost.setStyleSheet("""
                QPushButton {
                    background-color: #1F193E;
                    border: 1.5px solid #6A55FF;
                    color: #B5A8FF;
                    font-weight: 700;
                    border-radius: 7px;
                    padding: 5px 9px;
                    font-size: 9.5pt;
                }
            """)
        else:
            self.btn_topmost.setText("📌 최상위 OFF")
            self.btn_topmost.setStyleSheet("""
                QPushButton {
                    background-color: #141824;
                    border: 1.5px solid #252D3F;
                    color: #808C9E;
                    font-weight: 600;
                    border-radius: 7px;
                    padding: 5px 9px;
                    font-size: 9.5pt;
                }
            """)

    def _show_opacity_menu(self) -> None:
        menu = QtWidgets.QMenu(self)
        for pct in [100, 90, 80, 70, 60, 50]:
            act = menu.addAction(f"{pct}% 투명도")
            act.triggered.connect(lambda _c, p=pct: self._set_opacity(p))
        menu.exec_(self.btn_opacity.mapToGlobal(QtCore.QPoint(0, self.btn_opacity.height())))

    def _set_opacity(self, pct: int) -> None:
        self.opacity_val = pct
        self.btn_opacity.setText(f"💧 {pct}%")
        self.setWindowOpacity(pct / 100.0)
        self._save_config()

    def _on_grid_changed(self, index: int) -> None:
        presets = [(3, 5), (4, 4), (3, 3), (2, 4)]
        if 0 <= index < len(presets):
            self.rows, self.cols = presets[index]
            self.current_page = 0
            self._capture_active_slot_preset()
            self._save_config()
            self.refresh_slots()

    def _setup_watcher(self) -> None:
        self.watcher = QtCore.QFileSystemWatcher(self)
        hotkeys_file = str(self.repository.hotkeys_path)
        if os.path.exists(hotkeys_file):
            self.watcher.addPath(hotkeys_file)
            self.watcher.fileChanged.connect(self._on_hotkeys_file_changed)

    def _on_hotkeys_file_changed(self, path: str) -> None:
        self.refresh_slots()
        if not self._applying_slot_preset:
            self._capture_active_slot_preset()
            self._save_config()
        if path and path not in self.watcher.files():
            QtCore.QTimer.singleShot(150, lambda p=path: self.watcher.addPath(p) if os.path.exists(p) else None)

    def _add_slot_from_radial(self) -> None:
        names = [summary.name for summary in self.repository.list_macros()]
        if not names:
            QtWidgets.QMessageBox.information(self, "슬롯 추가", "먼저 Studio에서 매크로를 만들어 주세요.")
            return
        macro_name, accepted = QtWidgets.QInputDialog.getItem(
            self, "퀵스트림 슬롯 추가", "추가할 매크로:", names, 0, False
        )
        if not accepted or not macro_name:
            return
        payload = self.repository.load_hotkeys()
        slots = list(payload.get("slots") or [])
        new_slot = {"macro": str(macro_name), "hotkey": "", "mode": "hybrid"}
        empty_index = next((i for i, slot in enumerate(slots) if not str(slot.get("macro") or "").strip()), None)
        if empty_index is None:
            slots.append(new_slot)
            inserted_index = len(slots) - 1
        else:
            slots[empty_index] = new_slot
            inserted_index = empty_index
        payload["slots"] = slots
        self.repository.save_hotkeys(payload)
        self.current_page = max(0, inserted_index // max(1, self.rows * self.cols))
        self.refresh_slots()
        self._capture_active_slot_preset()
        self._save_config()

    def _edit_slot_icon(self, slot_idx: int) -> None:
        if slot_idx < 0:
            # Reset icon request
            real_idx = -1 - slot_idx
            self.custom_icons.pop(str(real_idx), None)
            self._capture_active_slot_preset()
            self._save_config()
            self.refresh_slots()
            return

        hotkeys_data = self.repository.load_hotkeys()
        slots = list(hotkeys_data.get("slots") or [])
        macro_name = ""
        if slot_idx < len(slots):
            macro_name = str(slots[slot_idx].get("macro") or "").strip()

        current_cfg = self.custom_icons.get(str(slot_idx), {})
        dlg = SlotIconEditDialog(slot_idx, macro_name, current_cfg, self)
        dlg.live_config_changed.connect(self._preview_slot_icon)
        position_dialog_beside(self, dlg)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            new_cfg = dlg.get_config()
            self.custom_icons[str(slot_idx)] = new_cfg
            self._capture_active_slot_preset()
            self._save_config()
            self._preview_slot_icon(slot_idx, new_cfg)
        else:
            self._preview_slot_icon(slot_idx, current_cfg)

    def _preview_slot_icon(self, slot_idx: int, config: object) -> None:
        if not isinstance(config, dict):
            return
        for button in self.buttons:
            if button.slot_index == slot_idx:
                button.set_custom_icon_config(config)
                button.update()
                break

    def _remove_slot(self, slot_idx: int) -> None:
        payload = self.repository.load_hotkeys()
        slots = list(payload.get("slots") or [])
        if not (0 <= slot_idx < len(slots)):
            return
        macro_name = str(slots[slot_idx].get("macro") or "").strip()
        if macro_name and self._is_macro_running(macro_name):
            self._stop_slot_macro(slot_idx, macro_name)
        slots[slot_idx] = {"macro": "", "hotkey": "", "mode": "hybrid"}
        payload["slots"] = slots
        self.repository.save_hotkeys(payload)
        self.custom_icons.pop(str(slot_idx), None)
        self._capture_active_slot_preset()
        self._save_config()
        self.refresh_slots()

    def refresh_slots(self) -> None:
        gap = int(self.config.get("tile_gap", 10))
        self.grid_layout.setSpacing(gap)

        # Clear existing grid
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().setParent(None)
                item.widget().deleteLater()
        self.buttons.clear()

        hotkeys_data = self.repository.load_hotkeys()
        slots = list(hotkeys_data.get("slots") or [])
        pageSize = self.rows * self.cols
        totalPages = max(1, int(hotkeys_data.get("deck_page_count") or 1), math.ceil(len(slots) / pageSize))
        if self.current_page >= totalPages:
            self.current_page = totalPages - 1

        if hasattr(self, "page_label") and self.page_label:
            self.page_label.setText(f"{self.current_page + 1}/{totalPages}")
        if hasattr(self, "btn_prev_page") and self.btn_prev_page:
            self.btn_prev_page.setEnabled(self.current_page > 0)
        if hasattr(self, "btn_next_page") and self.btn_next_page:
            self.btn_next_page.setEnabled(self.current_page < totalPages - 1)

        # Update dots (● ● ●) if present
        if hasattr(self, "dots_label") and self.dots_label:
            dots_html = []
            for p in range(min(totalPages, 5)):
                if p == self.current_page:
                    dots_html.append("<span style='color: #1E90FF; font-size: 11pt;'>●</span>")
                else:
                    dots_html.append("<span style='color: #2B364A; font-size: 9pt;'>●</span>")
            self.dots_label.setText("   ".join(dots_html))

        start_idx = self.current_page * pageSize
        show_empty_slots = bool(self.config.get("show_empty_slots", False))
        current_page_slots = list(slots[start_idx:start_idx + pageSize])
        if show_empty_slots:
            current_page_slots.extend(
                {"macro": "", "hotkey": "", "mode": "hybrid"}
                for _ in range(max(0, pageSize - len(current_page_slots)))
            )
            page_slots = [(start_idx + offset, slot) for offset, slot in enumerate(current_page_slots)]
        else:
            page_slots = [
                (index, slot)
                for index, slot in enumerate(current_page_slots, start=start_idx)
                if str(slot.get("macro") or "").strip()
            ]

        visible_cols = min(self.cols, max(1, len(page_slots)))
        visible_rows = max(1, math.ceil(len(page_slots) / visible_cols))
        self._visible_cols = visible_cols
        self._visible_rows = visible_rows
        self.setMinimumSize(self._size_for_tile_side(MIN_TILE_SIDE))
        for r in range(20):
            self.grid_layout.setRowStretch(r, 1 if r < visible_rows else 0)
        for c in range(20):
            self.grid_layout.setColumnStretch(c, 1 if c < visible_cols else 0)

        for i, (slot_idx, slot_info) in enumerate(page_slots):
            btn = StreamDeckButton(slot_idx, self.swipe_container)
            btn.setMinimumSize(MIN_TILE_SIDE, MIN_TILE_SIDE)

            # Apply custom icon config if present
            icon_cfg = self.custom_icons.get(str(slot_idx), {})
            btn.set_custom_icon_config(icon_cfg)

            macro_name = str(slot_info.get("macro") or "").strip()
            action = slot_info.get("action") if isinstance(slot_info.get("action"), dict) else None
            display_title = str(action.get("label") or "") if action is not None else macro_name
            hotkey = str(slot_info.get("hotkey") or "").strip()
            mode = str(slot_info.get("mode") or "hybrid")
            is_running = self._is_macro_running(macro_name)
            btn.set_slot_data(macro_name, hotkey, mode, is_running, display_title=display_title)

            btn.slot_triggered.connect(self._run_slot_macro)
            btn.slot_stopped.connect(self._stop_slot_macro)
            btn.edit_icon_requested.connect(self._edit_slot_icon)
            btn.remove_slot_requested.connect(self._remove_slot)

            row = i // visible_cols
            col = i % visible_cols
            self.grid_layout.addWidget(btn, row, col)
            self.buttons.append(btn)
        self.refresh_states()

        # Dynamic Auto-Fit Window Size according to configured active slots
        if bool(self.config.get("compact_auto_fit", True)):
            active_count = len(page_slots)
            if active_count > 0:
                fit_cols = min(self.cols, active_count)
                fit_rows = math.ceil(active_count / fit_cols)

                self._visible_cols = fit_cols
                self._visible_rows = fit_rows
                tile_scale = float(self.config.get("tile_scale", 1.0))
                tile_side = max(MIN_TILE_SIDE, BASE_TILE_SIDE * tile_scale)
                self.setMinimumSize(self._size_for_tile_side(MIN_TILE_SIDE))
                self.resize(self._size_for_tile_side(tile_side))
            else:
                self._visible_cols = 1
                self._visible_rows = 1
                self.setMinimumSize(self._size_for_tile_side(MIN_TILE_SIDE))
                self.resize(self._size_for_tile_side(BASE_TILE_SIDE * float(self.config.get("tile_scale", 1.0))))
        if hasattr(self, "preset_radial_menu"):
            self._refresh_preset_radial_menu()

    def _prev_page(self) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_slots()

    def _next_page(self) -> None:
        hotkeys_data = self.repository.load_hotkeys()
        slots = list(hotkeys_data.get("slots") or [])
        pageSize = self.rows * self.cols
        totalPages = max(1, int(hotkeys_data.get("deck_page_count") or 1), math.ceil(len(slots) / pageSize))
        if self.current_page < totalPages - 1:
            self.current_page += 1
            self.refresh_slots()

    def _is_macro_running(self, macro_name: str) -> bool:
        if not macro_name:
            return False
        return any(name == macro_name and proc.poll() is None for name, proc in self.active_processes.values())

    def _run_slot_macro(self, slot_index: int, macro_name: str) -> None:
        if not macro_name:
            return
        slots = list(self.repository.load_hotkeys().get("slots") or [])
        if 0 <= slot_index < len(slots):
            action = slots[slot_index].get("action")
            if isinstance(action, dict) and action.get("kind"):
                self._execute_deck_action(action)
                return
        try:
            proc = self.repository.run_macro(macro_name)
            self.active_processes[int(proc.pid)] = (macro_name, proc)
            if hasattr(self, "status_title") and self.status_title:
                self.status_title.setText("▶ 실행 중")
                self.status_title.setStyleSheet("font-size: 10pt; font-weight: 700; color: #26D07C;")
            if hasattr(self, "status_sub") and self.status_sub:
                self.status_sub.setText(f"'{macro_name}' 실행 중...")
            self.refresh_states()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "실행 오류", f"매크로 '{macro_name}' 실행 중 오류 발생:\n{exc}")

    def _set_deck_status(self, title: str, detail: str = "", error: bool = False) -> None:
        color = "#E85566" if error else "#26D07C"
        if hasattr(self, "status_title") and self.status_title:
            self.status_title.setText(title)
            self.status_title.setStyleSheet(f"font-size: 10pt; font-weight: 700; color: {color};")
        if hasattr(self, "status_sub") and self.status_sub:
            self.status_sub.setText(detail)

    def _send_windows_hotkey(self, sequence: str) -> None:
        if os.name != "nt":
            raise RuntimeError("단축키 전송은 Windows에서만 지원됩니다.")
        parts = [part.strip() for part in str(sequence).replace("-", "+").split("+") if part.strip()]
        if not parts:
            raise ValueError("단축키가 비어 있습니다.")
        aliases = {
            "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
            "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
            "space": 0x20, "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
            "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
        }
        user32 = ctypes.windll.user32
        virtual_keys: list[int] = []
        for token in parts:
            lowered = token.casefold()
            if lowered in aliases:
                virtual_keys.append(aliases[lowered])
            elif lowered.startswith("f") and lowered[1:].isdigit() and 1 <= int(lowered[1:]) <= 24:
                virtual_keys.append(0x6F + int(lowered[1:]))
            elif len(token) == 1:
                virtual_keys.append(int(user32.VkKeyScanW(ord(token))) & 0xFF)
            else:
                raise ValueError(f"지원하지 않는 키: {token}")
        for vk in virtual_keys:
            user32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(virtual_keys):
            user32.keybd_event(vk, 0, 0x0002, 0)

    def _activate_window_by_title(self, title_fragment: str) -> None:
        fragment = str(title_fragment or "").strip().casefold()
        if not fragment:
            return
        if os.name != "nt":
            raise RuntimeError("대상 창 선택은 Windows에서만 지원됩니다.")
        user32 = ctypes.windll.user32
        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def enum_callback(hwnd: int, _param: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if fragment in buffer.value.casefold():
                matches.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(callback_type(enum_callback), 0)
        if not matches:
            raise ValueError(f"대상 창을 찾지 못했습니다: {title_fragment}")
        user32.ShowWindow(matches[0], 9)
        user32.SetForegroundWindow(matches[0])
        QtCore.QThread.msleep(80)

    def _resolve_action_window(self, action: Dict[str, Any]) -> int:
        """Resolve a saved Deck action target without changing foreground focus."""
        if os.name != "nt":
            raise RuntimeError("대상 창 지정은 Windows에서만 지원됩니다.")
        user32 = ctypes.windll.user32
        token = str(action.get("target_window") or "").strip()
        if token.casefold().startswith("ahk_id"):
            try:
                hwnd = int(token.split()[-1], 0)
                if hwnd and user32.IsWindow(hwnd):
                    return hwnd
            except (TypeError, ValueError):
                pass

        title_fragment = str(action.get("target_title") or "").strip().casefold()
        exe_name = str(action.get("target_exe") or "").strip().casefold()
        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def process_name(hwnd: int) -> str:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
            if not handle:
                return ""
            try:
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return Path(buffer.value).name.casefold()
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
            return ""

        def enum_callback(hwnd: int, _param: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(max(1, length + 1))
            if length:
                user32.GetWindowTextW(hwnd, buffer, length + 1)
            title_ok = not title_fragment or title_fragment in buffer.value.casefold()
            exe_ok = not exe_name or process_name(hwnd) == exe_name
            if title_ok and exe_ok:
                matches.append(int(hwnd))
                return False
            return True

        if title_fragment or exe_name:
            user32.EnumWindows(callback_type(enum_callback), 0)
        if matches:
            return matches[0]
        if token or title_fragment or exe_name:
            raise ValueError(f"대상 창을 찾지 못했습니다: {action.get('target_exe') or action.get('target_title') or token}")
        return int(user32.GetForegroundWindow() or 0)

    @staticmethod
    def _focused_child_window(hwnd: int) -> int:
        if os.name != "nt" or not hwnd:
            return hwnd

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        user32 = ctypes.windll.user32
        thread_id = int(user32.GetWindowThreadProcessId(hwnd, None) or 0)
        info = GUITHREADINFO(); info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if thread_id and user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)) and info.hwndFocus:
            return int(info.hwndFocus)
        return hwnd

    def _activate_action_window(self, action: Dict[str, Any]) -> int:
        hwnd = self._resolve_action_window(action)
        if hwnd:
            user32 = ctypes.windll.user32
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            QtCore.QThread.msleep(80)
        return hwnd

    def _send_inactive_hotkey(self, hwnd: int, sequence: str) -> None:
        parts = [part.strip() for part in str(sequence).replace("-", "+").split("+") if part.strip()]
        if not parts:
            raise ValueError("단축키가 비어 있습니다.")
        aliases = {
            "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
            "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
            "space": 0x20, "backspace": 0x08, "delete": 0x2E,
        }
        user32 = ctypes.windll.user32
        keys: list[int] = []
        for token in parts:
            lowered = token.casefold()
            if lowered in aliases: keys.append(aliases[lowered])
            elif lowered.startswith("f") and lowered[1:].isdigit() and 1 <= int(lowered[1:]) <= 24: keys.append(0x6F + int(lowered[1:]))
            elif len(token) == 1: keys.append(int(user32.VkKeyScanW(ord(token))) & 0xFF)
            else: raise ValueError(f"지원하지 않는 키: {token}")
        target = self._focused_child_window(hwnd)
        for vk in keys: user32.PostMessageW(target, 0x0100, vk, 0)
        for vk in reversed(keys): user32.PostMessageW(target, 0x0101, vk, 0)

    def _send_active_unicode_text(self, text: str, delay_ms: int = 0) -> None:
        if os.name != "nt":
            raise RuntimeError("글자별 키 입력은 Windows에서만 지원됩니다.")
        pointer_int = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", pointer_int),
            ]

        class INPUTUNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("union",)
            _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

        encoded = text.encode("utf-16-le")
        for offset in range(0, len(encoded), 2):
            code_unit = int.from_bytes(encoded[offset:offset + 2], "little")
            events = (INPUT * 2)(
                INPUT(1, INPUTUNION(ki=KEYBDINPUT(0, code_unit, 0x0004, 0, 0))),
                INPUT(1, INPUTUNION(ki=KEYBDINPUT(0, code_unit, 0x0004 | 0x0002, 0, 0))),
            )
            if ctypes.windll.user32.SendInput(2, events, ctypes.sizeof(INPUT)) != 2:
                raise RuntimeError("Windows 키 입력 전송에 실패했습니다.")
            if delay_ms > 0:
                QtCore.QThread.msleep(delay_ms)

    def _send_inactive_text(self, hwnd: int, text: str, method: str = "clipboard", delay_ms: int = 0) -> None:
        target = self._focused_child_window(hwnd)
        if method == "set_text":
            ctypes.windll.user32.SendMessageW(target, 0x000C, 0, str(text))
            return
        if method in {"type", "wm_char"}:
            encoded = str(text).encode("utf-16-le")
            for offset in range(0, len(encoded), 2):
                code_unit = int.from_bytes(encoded[offset:offset + 2], "little")
                ctypes.windll.user32.PostMessageW(target, 0x0102, code_unit, 0)
                if delay_ms > 0:
                    QtCore.QThread.msleep(delay_ms)
            return
        QtWidgets.QApplication.clipboard().setText(text)
        self._send_inactive_hotkey(hwnd, "Ctrl+V")

    def _click_action_target(self, action: Dict[str, Any], *, inactive: bool) -> None:
        hwnd = self._resolve_action_window(action) if inactive else self._activate_action_window(action)
        x, y = int(action.get("x") or 0), int(action.get("y") or 0)
        button = str(action.get("button") or "left")
        clicks = max(1, min(3, int(action.get("clicks") or 1)))
        user32 = ctypes.windll.user32
        if inactive:
            message_map = {"left": (0x0201, 0x0202, 1), "right": (0x0204, 0x0205, 2), "middle": (0x0207, 0x0208, 0x10)}
            down, up, wparam = message_map.get(button, message_map["left"])
            lparam = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
            for _ in range(clicks):
                user32.PostMessageW(hwnd, 0x0200, 0, lparam)
                user32.PostMessageW(hwnd, down, wparam, lparam)
                user32.PostMessageW(hwnd, up, 0, lparam)
            return
        point = wintypes.POINT(x, y)
        if hwnd:
            user32.ClientToScreen(hwnd, ctypes.byref(point))
        user32.SetCursorPos(point.x, point.y)
        flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
        down, up = flags.get(button, flags["left"])
        for _ in range(clicks):
            user32.mouse_event(down, 0, 0, 0, 0); user32.mouse_event(up, 0, 0, 0, 0)

    def _start_registered_macro(self, macro_name: str) -> subprocess.Popen[Any]:
        proc = self.repository.run_macro(macro_name)
        self.active_processes[int(proc.pid)] = (macro_name, proc)
        return proc

    def _run_macro_batch(self, action: Dict[str, Any]) -> None:
        names = [str(value).strip() for value in list(action.get("macros") or []) if str(value).strip()]
        if not names:
            raise ValueError("실행할 매크로가 선택되지 않았습니다.")
        mode = str(action.get("mode") or "sequential")
        delay = max(0, int(action.get("delay_ms") or 0))
        continue_on_error = bool(action.get("continue_on_error", True))
        if mode == "parallel":
            failures = []
            for name in names:
                try:
                    self._start_registered_macro(name)
                except Exception as exc:
                    failures.append(f"{name}: {exc}")
                    if not continue_on_error:
                        break
            if failures:
                self._set_deck_status("일부 작업 실패", failures[0], error=True)
            return

        def run_at(index: int) -> None:
            if index >= len(names):
                self.refresh_states()
                return
            try:
                proc = self._start_registered_macro(names[index])
            except Exception as exc:
                self._set_deck_status("다중 작업 실패", f"{names[index]}: {exc}", error=True)
                if not continue_on_error:
                    return
                QtCore.QTimer.singleShot(delay, lambda: run_at(index + 1))
                return

            def wait_for_finish() -> None:
                return_code = proc.poll()
                if return_code is None:
                    QtCore.QTimer.singleShot(100, wait_for_finish)
                    return
                if return_code != 0 and not continue_on_error:
                    self._set_deck_status("다중 작업 중단", f"{names[index]} 종료 코드 {return_code}", error=True)
                    return
                QtCore.QTimer.singleShot(delay, lambda: run_at(index + 1))

            wait_for_finish()

        run_at(0)

    def _execute_deck_action(self, action: Dict[str, Any]) -> None:
        kind = str(action.get("kind") or "")
        label = str(action.get("label") or kind)
        try:
            if kind == "open_target":
                target = str(action.get("target") or "").strip()
                if not target:
                    raise ValueError("열기 대상이 비어 있습니다.")
                if target.lower().startswith(("http://", "https://")):
                    webbrowser.open(target)
                elif os.name == "nt":
                    os.startfile(target)
                else:
                    subprocess.Popen([target])
            elif kind == "terminate_program":
                process_name = str(action.get("process") or "").strip()
                if not process_name:
                    raise ValueError("종료할 프로세스 이름이 비어 있습니다.")
                subprocess.Popen(
                    ["taskkill", "/IM", process_name, "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            elif kind == "hotkey":
                if str(action.get("input_mode") or "active") == "inactive":
                    self._send_inactive_hotkey(self._resolve_action_window(action), str(action.get("keys") or ""))
                else:
                    if action.get("target_window") or action.get("target_exe"):
                        self._activate_action_window(action)
                    else:
                        self._activate_window_by_title(str(action.get("target_title") or ""))
                    self._send_windows_hotkey(str(action.get("keys") or ""))
            elif kind == "text":
                text_value = str(action.get("text") or "")
                input_mode = str(action.get("input_mode") or "active")
                method = str(action.get("text_method") or "auto")
                delay_ms = max(0, int(action.get("key_interval_ms") or 0))
                press_enter = bool(action.get("press_enter", False))
                if input_mode == "inactive":
                    resolved_method = "wm_char" if method == "auto" else method
                    hwnd = self._resolve_action_window(action)
                    self._send_inactive_text(hwnd, text_value, resolved_method, delay_ms)
                    if press_enter:
                        self._send_inactive_hotkey(hwnd, "Enter")
                else:
                    if action.get("target_window") or action.get("target_exe"):
                        self._activate_action_window(action)
                    else:
                        self._activate_window_by_title(str(action.get("target_title") or ""))
                    if method in {"type", "wm_char"}:
                        self._send_active_unicode_text(text_value, delay_ms)
                    elif method == "set_text":
                        self._send_inactive_text(self._resolve_action_window(action), text_value, "set_text", delay_ms)
                    else:
                        QtWidgets.QApplication.clipboard().setText(text_value)
                        self._send_windows_hotkey("Ctrl+V")
                    if press_enter:
                        self._send_windows_hotkey("Enter")
            elif kind == "mouse_click":
                self._click_action_target(action, inactive=str(action.get("input_mode") or "inactive") == "inactive")
            elif kind == "wait":
                wait_ms = max(10, int(action.get("ms") or 1000))
                self._set_deck_status("대기 중", f"{wait_ms}ms")
                QtCore.QTimer.singleShot(wait_ms, lambda: self._set_deck_status("준비 완료", "Deck 액션 대기 완료"))
                return
            elif kind == "studio":
                self._open_studio()
            elif kind == "stop_all":
                self.stop_all_macros()
            elif kind == "run_macro":
                macro_name = str(action.get("macro") or "").strip()
                if not macro_name:
                    raise ValueError("실행할 매크로가 선택되지 않았습니다.")
                self._start_registered_macro(macro_name)
            elif kind == "multi_macros":
                self._run_macro_batch(action)
            elif kind == "switch_preset":
                self._switch_slot_preset(str(action.get("preset_id") or ""))
            elif kind == "page_prev":
                self._prev_page()
            elif kind == "page_next":
                self._next_page()
            elif kind == "page_first":
                self.current_page = 0
                self.refresh_slots()
            elif kind == "page_goto":
                payload = self.repository.load_hotkeys()
                count = max(1, int(payload.get("deck_page_count") or 1))
                self.current_page = max(0, min(count - 1, int(action.get("page") or 1) - 1))
                self.refresh_slots()
            else:
                raise ValueError(f"지원하지 않는 Deck 액션: {kind}")
            self._set_deck_status("Deck 액션 실행", label)
            self.refresh_states()
        except Exception as exc:
            self._set_deck_status("Deck 액션 실패", str(exc), error=True)
            QtWidgets.QMessageBox.warning(self, "Deck 액션 오류", f"'{label}' 실행 중 오류가 발생했습니다.\n{exc}")

    def _stop_slot_macro(self, slot_index: int, macro_name: str) -> None:
        for pid, (name, proc) in list(self.active_processes.items()):
            if name == macro_name:
                self._terminate_process_tree(proc)
                self.active_processes.pop(pid, None)
        if hasattr(self, "status_title") and self.status_title:
            self.status_title.setText("🛑 중지됨")
            self.status_title.setStyleSheet("font-size: 10pt; font-weight: 700; color: #E85566;")
        if hasattr(self, "status_sub") and self.status_sub:
            self.status_sub.setText(f"'{macro_name}' 중지됨")
        self.refresh_states()

    def stop_all_macros(self) -> None:
        count = 0
        for _pid, (_name, proc) in list(self.active_processes.items()):
            self._terminate_process_tree(proc)
            count += 1
        self.active_processes.clear()
        if hasattr(self, "status_title") and self.status_title:
            self.status_title.setText("🛑 전체 중지")
            self.status_title.setStyleSheet("font-size: 10pt; font-weight: 700; color: #E85566;")
        if hasattr(self, "status_sub") and self.status_sub:
            self.status_sub.setText(f"{count}개 매크로 중지됨")
        self.refresh_states()

    def refresh_states(self) -> None:
        # Clean up dead processes
        finished = [pid for pid, (_name, proc) in self.active_processes.items() if proc.poll() is not None]
        for pid in finished:
            _name, proc = self.active_processes.pop(pid)
            self.repository.release_macro_process(proc)

        running_count = len(self.active_processes)
        if running_count > 0:
            names = ", ".join(name for name, _proc in self.active_processes.values())
            if hasattr(self, "status_title") and self.status_title:
                self.status_title.setText("▶ 매크로 실행 중")
                self.status_title.setStyleSheet("font-size: 10pt; font-weight: 700; color: #26D07C;")
            if hasattr(self, "status_sub") and self.status_sub:
                self.status_sub.setText(f"{running_count}개 실행 중: {names}")
            if hasattr(self, "dot_label") and self.dot_label:
                self.dot_label.setStyleSheet("font-size: 10pt; color: #26D07C;")
        elif hasattr(self, "status_title") and self.status_title and not self.status_title.text().startswith("🛑"):
            self.status_title.setText("준비 완료")
            self.status_title.setStyleSheet("font-size: 10pt; font-weight: 700; color: #26D07C;")
            if hasattr(self, "status_sub") and self.status_sub:
                self.status_sub.setText("매크로 대기 중")
            if hasattr(self, "dot_label") and self.dot_label:
                self.dot_label.setStyleSheet("font-size: 10pt; color: #26D07C;")

        for btn in self.buttons:
            if btn.macro_name:
                is_running = self._is_macro_running(btn.macro_name)
                btn.set_running(is_running)

    def _open_studio(self) -> None:
        script = self.repository.root / "run_studio.ps1"
        if script.exists():
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                cwd=str(self.repository.root),
                creationflags=creationflags,
            )

    def _terminate_process_tree(self, proc: subprocess.Popen[Any]) -> None:
        if proc.poll() is not None:
            self.repository.release_macro_process(proc)
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            self.repository.release_macro_process(proc)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.stop_all_macros()
        self._capture_active_slot_preset()
        self._save_config()
        super().closeEvent(event)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    window = QuickSlotDeckWindow()
    app.setWindowIcon(window.windowIcon())
    if bool(window.config.get("start_minimized", False)):
        window.showMinimized()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
