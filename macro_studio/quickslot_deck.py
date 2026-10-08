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
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
import webbrowser
from ctypes import wintypes
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional

try:
    import winreg
except ImportError:
    winreg = None

from PySide6 import QtCore, QtGui, QtWidgets

try:
    from PySide6.QtMultimedia import QSoundEffect
except ImportError:
    QSoundEffect = None

from macro_studio.repository import MacroRepository
from macro_studio.deck_edge_panel import (
    EDGE_PANEL_HEADER, EDGE_PANEL_SIDES, EDGE_PANEL_MIN_TILE, EDGE_PANEL_MAX_TILE,
    DeckLayoutEditButton, EdgePanelSizeDialog, draw_edge_direction, edge_panel_layout,
    nearest_screen_edge, slot_visual_positions,
)
from macro_studio.deck_github_sync import publish_bundled_deck
from macro_studio.portable_deck import adapt_backup_for_host, host_fingerprint
from macro_studio.deck_starter_actions import (
    browser_site_slot, compact_starter_caption, default_home_slot, starter_action_pack,
)
from macro_studio.deck_browser_bridge import (
    DeckBrowserBridge, foreground_program, normalize_program_rule,
    normalize_site_rule, preset_for_program, preset_for_url,
)
from macro_studio.theme import stylesheet


DECK_BACKUP_VERSION = 2
BUNDLED_DECK_BACKUP = Path(__file__).resolve().parent.parent / "deck_presets" / "macrorelay_bundled_deck.json"
GITHUB_BUNDLED_DECK_URL = (
    "https://raw.githubusercontent.com/shinhuigwan/macrorelay-studio/main/"
    "deck_presets/macrorelay_bundled_deck.json"
)
MAX_BUNDLED_DECK_BYTES = 100 * 1024 * 1024


def is_plain_whale_launch(target: str) -> bool:
    """Match a bare Whale executable, but not a command with URL or flags."""
    value = str(target or "").strip()
    executable = value[1:-1] if value.startswith('"') and value.endswith('"') else value
    return PureWindowsPath(executable).name.casefold() == "whale.exe"


def bundled_deck_path(app_root: Path) -> Path:
    local = Path(app_root) / "deck_presets" / "macrorelay_bundled_deck.json"
    return local if local.is_file() else BUNDLED_DECK_BACKUP


def download_github_bundled_deck() -> Dict[str, Any]:
    request = urllib.request.Request(
        f"{GITHUB_BUNDLED_DECK_URL}?v={int(time.time())}",
        headers={"User-Agent": "MacroRelay-Deck", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        content = response.read(MAX_BUNDLED_DECK_BYTES + 1)
    if len(content) > MAX_BUNDLED_DECK_BYTES:
        raise ValueError("GitHub 내장 구성 파일이 허용 크기(100MB)를 넘습니다.")
    payload = json.loads(content.decode("utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("format") != "macrorelay-deck-backup":
        raise ValueError("GitHub 파일이 MacroRelay Deck 백업 형식이 아닙니다.")
    if not isinstance(payload.get("hotkeys"), dict) or not isinstance(payload.get("macros"), dict):
        raise ValueError("GitHub 내장 구성에 슬롯 또는 매크로 정보가 없습니다.")
    if not isinstance(payload.get("deck_config"), dict) or not isinstance(payload["hotkeys"].get("slots"), list):
        raise ValueError("GitHub 내장 구성에 프리셋 또는 슬롯 데이터가 없습니다.")
    return payload


def save_bundled_deck_payload(app_root: Path, payload: Dict[str, Any]) -> Path:
    if payload.get("format") != "macrorelay-deck-backup":
        raise ValueError("내장 구성으로 저장할 수 있는 Deck 백업 형식이 아닙니다.")
    path = Path(app_root) / "deck_presets" / "macrorelay_bundled_deck.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        backup = path.with_name(f"{path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S%f}.json")
        shutil.copy2(path, backup)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp_path, path)
    return path


def save_deck_restore_point(app_root: Path, payload: Dict[str, Any]) -> Path:
    """Preserve the current full Deck before applying a downloaded backup."""
    if payload.get("format") != "macrorelay-deck-backup":
        raise ValueError("현재 Deck 구성을 백업할 수 없습니다.")
    directory = Path(app_root) / "deck_presets"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"deck_before_github_restore-{datetime.now():%Y%m%d-%H%M%S%f}.json"
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp_path, path)
    return path


REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "MacroRelayQuickSlot"
PRESET_ICON_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".ico"}
BASE_TILE_SIDE = 190
MIN_TILE_SIDE = 48
WINDOW_PADDING = 16
PRESET_STYLE_KEYS = ("theme_index", "tile_scale", "tile_gap", "tile_radius", "hover_glow", "empty_slot_opacity", "show_empty_slots", "bottom_blank_space", "crop_trailing_empty_rows")
QUICKSLOT_DOUBLE_CLICK_INTERVAL_MS = 240
SLOT_ACCIDENTAL_REPEAT_MS = 320


def deck_slot_has_content(slot: object) -> bool:
    """A slot can run a Deck action even without a macro name."""
    if not isinstance(slot, dict):
        return False
    action = slot.get("action")
    return bool(str(slot.get("macro") or "").strip()
                or (isinstance(action, dict) and action))


# Starter modes add no actions and never enable automatic switching on their own.
# The first-run marker prevents a deleted starter mode from reappearing later.
STARTER_PRESETS = (
    ("youtube", "유튜브", "▶", "#FF4545", ("youtube.com",), ()),
    ("naver", "네이버", "N", "#26C76D", ("naver.com",), ()),
    ("ldplayer", "LDPlayer", "LD", "#4F9DFF", (), ("dnplayer.exe", "ldplayer.exe")),
    ("explorer", "파일 탐색기", "▣", "#F2BF50", (), ("explorer.exe",)),
    ("notepad", "메모장", "✎", "#67B7F7", (), ("notepad.exe",)),
    ("calculator", "계산기", "∑", "#AD95F8", (), ("calculatorapp.exe", "calculator.exe", "calc.exe")),
    ("settings", "Windows 설정", "⚙", "#94A3B8", (), ("systemsettings.exe",)),
)
DEFAULT_PRESET_VISUAL = ("⌂", "#7C6CFF")
BROWSER_SHORTCUTS = (
    ("네이버", "https://www.naver.com/", "starter-naver", "N", "#03C75A", "domain"),
    ("유튜브", "https://www.youtube.com/", "starter-youtube", "▶", "#E83945", "domain"),
    ("트레이딩", "https://kr.tradingview.com/chart/oT6trVdU/", "preset-1", "↗", "#1976D2", "origin_path"),
    ("테트리스", "https://jstris.jezevec10.com/#", "starter-jstris", "▦", "#7858D7", "domain"),
    ("ChatGPT", "https://chatgpt.com/c/e58e9cf9-eaad-4024-a714-3d0462ce8717", "starter-chatgpt", "✦", "#16826C", "origin_path"),
    ("구글", "https://www.google.com/", "starter-google", "G", "#4285F4", "domain"),
)


def is_default_home_slot(slot: object) -> bool:
    action = slot.get("action") if isinstance(slot, dict) else None
    return isinstance(action, dict) and action.get("kind") == "switch_preset" and action.get("preset_id") == "default"


def starter_icon_action_key(action: Dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(action.get(key) or "") for key in ("label", "kind", "target", "keys", "target_exe"))


def insert_default_home_slot(preset: Dict[str, Any]) -> bool:
    """Put Home first without replacing user actions or their icon positions."""
    slots = list(preset.get("slots") or [])
    if slots and is_default_home_slot(slots[0]):
        return False
    rows = max(1, int(preset.get("rows") or 3))
    cols = max(1, int(preset.get("cols") or 5))
    page_size = rows * cols
    home_index = next((index for index, slot in enumerate(slots) if is_default_home_slot(slot)), None)
    pins = dict(preset.get("pinned_slots") or {})
    shifted_pins: Dict[str, Any] = {}
    for position, entry in pins.items():
        try:
            old_position = int(position)
        except (TypeError, ValueError):
            return False
        if home_index is None or old_position < home_index:
            new_position = old_position + 1
        elif old_position == home_index:
            new_position = 0
        else:
            new_position = old_position
        if new_position >= page_size or (new_position == 0 and not is_default_home_slot(entry.get("slot") if isinstance(entry, dict) else None)):
            return False  # Keep pinned data intact when there is no safe local position.
        shifted_pins[str(new_position)] = entry

    home_slot, home_icon = default_home_slot()
    if home_index is None:
        slots.insert(0, home_slot)
    else:
        slots.insert(0, slots.pop(home_index))

    old_icons = dict(preset.get("custom_icons") or {})
    icons: Dict[str, Any] = {}
    for position, icon in old_icons.items():
        try:
            old_index = int(position)
        except (TypeError, ValueError):
            icons[position] = icon
            continue
        if home_index is None:
            new_index = old_index + 1
        elif old_index == home_index:
            new_index = 0
        elif old_index < home_index:
            new_index = old_index + 1
        else:
            new_index = old_index
        icons[str(new_index)] = icon
    icons.setdefault("0", home_icon)
    preset["slots"] = slots
    preset["custom_icons"] = icons
    preset["pinned_slots"] = shifted_pins
    page_count = max(int(preset.get("deck_page_count") or 1), math.ceil(len(slots) / page_size))
    preset["deck_page_count"] = page_count
    page_names = list(preset.get("deck_page_names") or [])
    page_names.extend(f"페이지 {index + 1}" for index in range(len(page_names), page_count))
    preset["deck_page_names"] = page_names
    return True


def preset_visual(preset_id: str, preset: Dict[str, Any]) -> tuple[str, str]:
    icon = str(preset.get("icon") or "").strip()
    color = str(preset.get("color") or "").strip()
    if not icon or not QtGui.QColor(color).isValid():
        if preset_id == "default":
            fallback = DEFAULT_PRESET_VISUAL
        else:
            fallback = next(((glyph, accent) for key, _name, glyph, accent, _sites, _programs
                             in STARTER_PRESETS if preset_id == f"starter-{key}"),
                            ((str(preset.get("name") or preset_id).strip() or "?")[:1].upper(), "#818CF8"))
        icon = icon or fallback[0]
        color = color if QtGui.QColor(color).isValid() else fallback[1]
    return icon[:3], color


def preset_qicon(icon_text: str, color: str) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(36, 36)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(color))
    painter.drawRoundedRect(QtCore.QRectF(2, 2, 32, 32), 9, 9)
    font = painter.font()
    font.setBold(True)
    font.setPointSize(10 if len(icon_text) > 1 else 14)
    painter.setFont(font)
    painter.setPen(QtGui.QColor("#FFFFFF"))
    painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, icon_text)
    painter.end()
    return QtGui.QIcon(pixmap)


def _quickslot_touch_sound_path() -> Path:
    """Create a tiny, pre-loadable click sound in the user's temp folder."""
    sound_dir = Path(tempfile.gettempdir()) / "MacroRelay"
    sound_path = sound_dir / "quickslot-touch-v1.wav"
    if sound_path.exists():
        return sound_path
    try:
        sound_dir.mkdir(parents=True, exist_ok=True)
        sample_rate = 44100
        duration = 0.032
        frames = bytearray()
        for index in range(int(sample_rate * duration)):
            progress = index / max(1, int(sample_rate * duration) - 1)
            envelope = (1.0 - progress) ** 3
            tone = math.sin(2.0 * math.pi * 1250.0 * index / sample_rate)
            value = int(32767 * 0.42 * envelope * tone)
            frames.extend(struct.pack("<h", value))
        with wave.open(str(sound_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(bytes(frames))
    except Exception:
        return Path()
    return sound_path


_ULONG_PTR = wintypes.WPARAM


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    # INPUT must be large enough for every documented input variant.  Defining
    # only KEYBDINPUT shrinks this union on 64-bit Windows and makes SendInput
    # reject cbSize with ERROR_INVALID_PARAMETER.
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


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


def icon_config_has_visual(config: Dict[str, Any]) -> bool:
    return bool(
        str(config.get("image_data") or "").strip()
        or str(config.get("image_path") or "").strip()
        or str(config.get("emoji") or "").strip()
    )


def extract_program_icon_config(target: str) -> Dict[str, Any]:
    """Extract a launch target's native Windows icon into a portable config."""
    path = str(target or "").strip().strip('"')
    icon_index = 0
    if path.lower().endswith(".lnk") and Path(path).is_file():
        # QFileIconProvider returns the small shortcut thumbnail (including its
        # arrow overlay). Resolve the link before extracting the actual icon.
        script = (
            "$OutputEncoding=[Console]::OutputEncoding=[Console]::InputEncoding="
            "[System.Text.Encoding]::UTF8;"
            "$link=(New-Object -ComObject WScript.Shell).CreateShortcut([Console]::In.ReadToEnd());"
            "[Console]::Out.WriteLine($link.IconLocation);"
            "[Console]::Out.WriteLine($link.TargetPath)"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                 base64.b64encode(script.encode("utf-16le")).decode("ascii")],
                input=path, text=True, encoding="utf-8", capture_output=True,
                timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                icon_location, _, shortcut_target = result.stdout.partition("\n")
                icon_location = icon_location.strip().strip('"')
                if "," in icon_location and icon_location.rsplit(",", 1)[1].lstrip("-").isdigit():
                    icon_location, index_text = icon_location.rsplit(",", 1)
                    icon_index = int(index_text)
                icon_location = os.path.expandvars(icon_location)
                shortcut_target = os.path.expandvars(shortcut_target.strip())
                if Path(icon_location).is_file():
                    path = icon_location
                elif Path(shortcut_target).is_file():
                    path = shortcut_target
                    icon_index = 0
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            pass
    info = QtCore.QFileInfo(path)
    if not path or not info.exists():
        return {}
    pixmap = QtGui.QPixmap()
    native_icon = False
    if os.name == "nt":
        extractor = ctypes.windll.user32.PrivateExtractIconsW
        extractor.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
                              wintypes.UINT, wintypes.UINT]
        extractor.restype = wintypes.UINT
        handle = wintypes.HICON()
        icon_id = wintypes.UINT()
        try:
            count = extractor(path, icon_index, 256, 256, ctypes.byref(handle), ctypes.byref(icon_id), 1, 0)
            if count and handle.value:
                image = QtGui.QImage.fromHICON(int(handle.value))
                if not image.isNull():
                    pixmap = QtGui.QPixmap.fromImage(image)
                    native_icon = True
        finally:
            if handle.value:
                ctypes.windll.user32.DestroyIcon(handle)
    if pixmap.isNull():
        provider = QtWidgets.QFileIconProvider()
        pixmap = provider.icon(info).pixmap(256, 256)
    if pixmap.isNull():
        return {}
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.WriteOnly)
    if not pixmap.save(buffer, "PNG"):
        return {}
    buffer.close()
    return {
        "image_path": "",
        "image_data": bytes(data.toBase64()).decode("ascii"),
        "full_stretch": True,
        "text_show": False,
        "emoji": "",
        "icon_size": 64,
        "text_position": "hidden",
        "font_size": 12,
        "spacing": 0,
        "text_x_percent": 50,
        "text_y_percent": 85,
        "icon_source": "program_auto",
        "icon_extractor_version": 2,
        "icon_quality": "native" if native_icon else "fallback",
    }


def macro_program_icon_target(repository: MacroRepository, macro_name: str, entry_name: str = "") -> str:
    """Find the first executable launched from a Deck macro's selected entry."""
    if not macro_name:
        return ""
    try:
        macro = repository.load_macro(macro_name)
    except (OSError, ValueError, KeyError):
        return ""
    steps = macro.get("steps") or []
    index = 1
    if entry_name:
        from macro_studio.repository import resolve_macro_entry
        try:
            index = resolve_macro_entry(macro, entry_name)
        except ValueError:
            return ""
    visited: set[int] = set()
    while 0 < index <= len(steps) and index not in visited:
        visited.add(index)
        step = steps[index - 1]
        if not isinstance(step, dict):
            break
        if step.get("action") == "run_program":
            command = os.path.expandvars(str(step.get("command") or "").strip())
            if command.startswith('"'):
                command = command[1:].split('"', 1)[0]
            elif ".exe" in command.lower():
                command = command[:command.lower().index(".exe") + 4]
            target = shutil.which(command) or command
            if Path(target).is_file():
                return target
        index = int(step.get("on_success") or (index + 1))
    return ""


def deck_action_needs_program_icon(action: Dict[str, Any] | None, icon: Dict[str, Any]) -> bool:
    if not action or str(icon.get("icon_source") or "") == "manual":
        return False
    kind = str(action.get("kind") or "")
    if kind not in {"open_target", "run_macro"}:
        return False
    if str(icon.get("icon_source") or "") == "program_auto" and int(icon.get("icon_extractor_version") or 0) < 2:
        return True
    if str(icon.get("image_data") or "") or str(icon.get("image_path") or ""):
        return False
    return not icon_config_has_visual(icon) or (kind == "run_macro" and icon.get("emoji") in {"▶", "M"})


def manual_executable_icon_needs_upgrade(icon: Dict[str, Any]) -> bool:
    return (
        str(icon.get("icon_source") or "") == "manual"
        and Path(str(icon.get("image_path") or "")).suffix.lower() in {".exe", ".lnk"}
        and int(icon.get("icon_extractor_version") or 0) < 2
    )


def upgrade_manual_executable_icon(icon: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh only the bitmap, preserving a user's tile layout and text."""
    if not manual_executable_icon_needs_upgrade(icon):
        return icon
    extracted = extract_program_icon_config(str(icon.get("image_path") or ""))
    if not extracted.get("image_data"):
        return icon
    upgraded = dict(icon)
    for key in ("image_data", "icon_extractor_version", "icon_quality"):
        upgraded[key] = extracted[key]
    return upgraded


def deck_action_program_icon(repository: MacroRepository, action: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(action.get("kind") or "")
    if kind == "open_target":
        target = str(action.get("target") or "")
    elif kind == "run_macro":
        target = macro_program_icon_target(
            repository, str(action.get("macro") or ""), str(action.get("entry_name") or "")
        )
    else:
        return {}
    return extract_program_icon_config(target)


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
        self._current_icon_meta = {
            key: self.icon_config[key]
            for key in ("icon_extractor_version", "icon_quality") if key in self.icon_config
        }
        repository = getattr(parent, "repository", None)
        self.app_root = Path(getattr(repository, "root", Path(__file__).resolve().parents[1]))
        owner_config = getattr(parent, "config", None)
        if not isinstance(owner_config, dict):
            owner_config = getattr(getattr(parent, "main_window", None), "config", {})
        self.default_full_stretch = bool(owner_config.get("auto_stretch_default", True)) if isinstance(owner_config, dict) else True
        self.config = {key: owner_config[key] for key in ("theme_index", "tile_radius", "hover_glow")
                       if isinstance(owner_config, dict) and key in owner_config}
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
        self.file_edit.textEdited.connect(self._on_image_path_edited)
        self.file_edit.installEventFilter(self)

        btn_browse = QtWidgets.QPushButton("📁 파일 선택...")
        btn_browse.setStyleSheet("background: #FFFFFF; border: 1px solid #B9C6D6; color: #263449; font-weight: 700; padding: 6px 12px; border-radius: 8px;")
        btn_browse.clicked.connect(self._browse_image)
        btn_program_icon = QtWidgets.QPushButton("💻 실행 파일 아이콘")
        btn_program_icon.setToolTip("슬롯에서 실행하는 매크로와 관계없이 EXE 파일의 아이콘을 직접 가져옵니다.")
        btn_program_icon.clicked.connect(self._browse_program_icon)
        btn_paste_image = QtWidgets.QPushButton("📋 붙여넣기")
        btn_paste_image.setToolTip("복사한 이미지 또는 탐색기에서 복사한 이미지 파일을 아이콘으로 적용합니다. 경로 입력칸에서도 Ctrl+V를 사용할 수 있습니다.")
        btn_paste_image.clicked.connect(self._paste_image_from_clipboard)

        file_box.addWidget(self.file_edit, 1)
        file_box.addWidget(btn_browse)
        file_box.addWidget(btn_program_icon)
        file_box.addWidget(btn_paste_image)
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

        self.text_override_edit = QtWidgets.QLineEdit()
        self.text_override_edit.setPlaceholderText("비워두면 슬롯 또는 매크로 이름을 사용합니다")
        self.text_override_edit.textChanged.connect(self._update_preview)
        form_layout.addRow("슬롯 표시 문구:", self.text_override_edit)

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

        zoom_box = QtWidgets.QHBoxLayout()
        self.zoom_spin = QtWidgets.QSpinBox()
        self.zoom_spin.setRange(50, 300)
        self.zoom_spin.setSingleStep(5)
        self.zoom_spin.setSuffix(" %")
        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.zoom_slider.setRange(50, 300)
        self.zoom_spin.valueChanged.connect(self.zoom_slider.setValue)
        self.zoom_slider.valueChanged.connect(self.zoom_spin.setValue)
        self.zoom_spin.valueChanged.connect(self._update_preview)
        zoom_box.addWidget(self.zoom_slider, 1)
        zoom_box.addWidget(self.zoom_spin)
        form_layout.addRow("전체 채움 이미지 확대 (미리보기 휠):", zoom_box)

        image_position_box = QtWidgets.QHBoxLayout()
        self.image_x_spin = QtWidgets.QSpinBox()
        self.image_y_spin = QtWidgets.QSpinBox()
        for control, axis in ((self.image_x_spin, "X"), (self.image_y_spin, "Y")):
            control.setRange(0, 100)
            control.setPrefix(f"{axis}: ")
            control.setSuffix(" %")
            control.valueChanged.connect(self._update_preview)
            image_position_box.addWidget(control)
        form_layout.addRow("확대 이미지 위치:", image_position_box)

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
        self.font_spin.setRange(6, 36)
        self.font_spin.setValue(12)
        self.font_spin.setSuffix(" pt")

        self.font_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.font_slider.setRange(6, 36)
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
        drag_hint = QtWidgets.QLabel("💡 미리보기 클릭: 텍스트 이동 · 휠: 이미지 확대/축소")
        drag_hint.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: #168566;")

        preview_header.addWidget(preview_label)
        preview_header.addStretch(1)
        preview_header.addWidget(drag_hint)
        layout.addLayout(preview_header)

        self.preview_card = StreamDeckButton(self.slot_index, self)
        self.preview_card.setFixedSize(160, 160)
        self.preview_card.set_preview_mode(True)
        self.preview_card.text_position_changed.connect(self._on_text_dragged_in_preview)
        self.preview_card.icon_zoom_requested.connect(self._on_preview_zoom_requested)
        self.actual_preview_card = StreamDeckButton(self.slot_index, self)
        actual_button = next((button for button in getattr(self.parent(), "buttons", [])
                              if button.slot_index == self.slot_index), None)
        actual_size = actual_button.size() if actual_button is not None else QtCore.QSize(90, 90)
        self.actual_preview_card.setFixedSize(actual_size)
        self.actual_preview_card.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        prev_box = QtWidgets.QHBoxLayout()
        prev_box.addStretch(1)
        prev_box.addWidget(self.preview_card)
        actual_box = QtWidgets.QVBoxLayout()
        actual_caption = QtWidgets.QLabel("실제 슬롯 크기")
        actual_caption.setAlignment(QtCore.Qt.AlignCenter)
        actual_box.addWidget(actual_caption)
        actual_box.addWidget(self.actual_preview_card, 0, QtCore.Qt.AlignCenter)
        prev_box.addSpacing(20)
        prev_box.addLayout(actual_box)
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
        self._update_preview()

    def _on_preview_zoom_requested(self, steps: int) -> None:
        self.zoom_spin.setValue(self.zoom_spin.value() + steps * 5)

    def _load_current_values(self) -> None:
        img_path = self.icon_config.get("image_path", "")
        self._current_base64 = str(self.icon_config.get("image_data", "")).strip()
        full_stretch = bool(self.icon_config.get("full_stretch", self.default_full_stretch))
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
        if self._current_base64 and not img_path:
            self.file_edit.setPlaceholderText("내장 아이콘 이미지 (파일 경로 없음)")
        self._sync_preset_selection(str(img_path))
        self.stretch_check.setChecked(full_stretch)
        self.show_text_check.setChecked(text_show)
        self.emoji_edit.setText(emoji)
        self.text_override_edit.setText(str(self.icon_config.get("text_override", "")))
        self.size_spin.setValue(size_val)
        self.zoom_spin.setValue(max(50, min(300, int(self.icon_config.get("image_zoom_percent", 100)))))
        self.image_x_spin.setValue(int(self.icon_config.get("image_x_percent", 50)))
        self.image_y_spin.setValue(int(self.icon_config.get("image_y_percent", 50)))
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
            self._current_icon_meta = {}
            self._sync_preset_selection(path)
            self._update_preview()

    def _on_image_path_edited(self, _text: str) -> None:
        # A manually entered path should never keep an unrelated embedded icon.
        self._current_base64 = ""
        self._current_icon_meta = {}
        self._update_preview()

    @staticmethod
    def _clipboard_image_file(mime: QtCore.QMimeData) -> str:
        if not mime.hasUrls():
            return ""
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if Path(path).suffix.lower() in PRESET_ICON_EXTENSIONS | {".svg"} and Path(path).is_file():
                    return path
        return ""

    def eventFilter(self, watched: object, event: QtCore.QEvent) -> bool:
        if watched is self.file_edit and event.type() == QtCore.QEvent.KeyPress:
            if isinstance(event, QtGui.QKeyEvent) and event.matches(QtGui.QKeySequence.Paste):
                mime = QtWidgets.QApplication.clipboard().mimeData()
                if mime and (mime.hasImage() or self._clipboard_image_file(mime)):
                    self._paste_image_from_clipboard()
                    return True
        return super().eventFilter(watched, event)

    def _paste_image_from_clipboard(self, clipboard: Optional[QtGui.QClipboard] = None) -> None:
        clipboard = clipboard or QtWidgets.QApplication.clipboard()
        mime = clipboard.mimeData()
        image_data = ""
        image_path = self._clipboard_image_file(mime) if mime else ""
        if mime and mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                if max(image.width(), image.height()) > 1024:
                    image = image.scaled(1024, 1024, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                data = QtCore.QByteArray()
                buffer = QtCore.QBuffer(data)
                if buffer.open(QtCore.QIODevice.WriteOnly):
                    if image.save(buffer, "PNG") and data.size() <= 10 * 1024 * 1024:
                        image_data = "data:image/png;base64," + bytes(data.toBase64()).decode("ascii")
                    buffer.close()
        if not image_data and image_path:
            image_data = encode_image_file_to_base64(image_path)
        if not image_data:
            QtWidgets.QMessageBox.warning(
                self, "이미지 붙여넣기", "클립보드에 사용할 수 있는 이미지가 없습니다. 이미지나 이미지 파일을 복사해 주세요."
            )
            return
        self._current_base64 = image_data
        self._current_icon_meta = {}
        self.file_edit.setText(image_path)
        if not image_path:
            self.file_edit.setPlaceholderText("내장 아이콘 이미지 (파일 경로 없음)")
        self._sync_preset_selection(image_path)
        self.stretch_check.setChecked(True)
        self.emoji_edit.clear()
        self._update_preview()

    def _browse_program_icon(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "아이콘을 가져올 실행 파일 선택", "", "실행 파일 (*.exe *.lnk);;모든 파일 (*.*)"
        )
        if not path:
            return
        config = extract_program_icon_config(path)
        image_data = str(config.get("image_data") or "")
        if not image_data:
            QtWidgets.QMessageBox.warning(self, "아이콘 가져오기", "이 실행 파일에서 아이콘을 읽지 못했습니다.")
            return
        self._current_base64 = image_data
        self._current_icon_meta = {
            key: config[key] for key in ("icon_extractor_version", "icon_quality") if key in config
        }
        self.file_edit.setText(path)
        self._sync_preset_selection("")
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
        self._current_icon_meta = {}
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
        self.file_edit.setPlaceholderText("이미지 파일 경로 (.png, .jpg, .ico, .svg)")
        self._current_base64 = ""
        self._current_icon_meta = {}
        self._sync_preset_selection("")
        self.stretch_check.setChecked(self.default_full_stretch)
        self.show_text_check.setChecked(True)
        self.emoji_edit.clear()
        self.text_override_edit.clear()
        self.size_spin.setValue(40)
        self.zoom_spin.setValue(100)
        self.image_x_spin.setValue(50)
        self.image_y_spin.setValue(50)
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
        self.actual_preview_card.set_custom_icon_config(cfg)
        self.actual_preview_card.set_slot_data(self.macro_name, "", "hybrid", False)
        self.live_config_changed.emit(self.slot_index, cfg)

    def get_config(self) -> Dict[str, Any]:
        pos_keys = ["bottom", "top", "center", "custom", "hidden"]
        idx = max(0, min(4, self.pos_combo.currentIndex()))
        img_path = self.file_edit.text().strip()

        b64_data = getattr(self, "_current_base64", "")
        if not b64_data and img_path and os.path.exists(img_path):
            b64_data = encode_image_file_to_base64(img_path)
            self._current_base64 = b64_data

        config = {
            "image_path": img_path,
            "image_data": b64_data,
            "full_stretch": self.stretch_check.isChecked(),
            "text_show": self.show_text_check.isChecked() and idx != 4,
            "emoji": self.emoji_edit.text().strip(),
            "text_override": self.text_override_edit.text().strip(),
            "icon_size": self.size_spin.value(),
            "image_zoom_percent": self.zoom_spin.value(),
            "image_x_percent": self.image_x_spin.value(),
            "image_y_percent": self.image_y_spin.value(),
            "text_position": pos_keys[idx],
            "font_size": self.font_spin.value(),
            "spacing": self.spacing_spin.value(),
            "text_x_percent": self.x_spin.value(),
            "text_y_percent": self.y_spin.value(),
        }
        if Path(img_path).suffix.lower() in {".exe", ".lnk"}:
            config.update(self._current_icon_meta)
        return config


class StreamDeckButton(QtWidgets.QFrame):
    """Slot Card Widget supporting custom images, full fill, drag text position, font size, and spacing."""

    slot_triggered = QtCore.Signal(int, str)  # index, macro_name
    slot_stopped = QtCore.Signal(int, str)    # index, macro_name
    edit_icon_requested = QtCore.Signal(int)  # index
    remove_slot_requested = QtCore.Signal(int)  # index
    text_position_changed = QtCore.Signal(float, float)  # x_pct, y_pct
    icon_zoom_requested = QtCore.Signal(int)  # mouse-wheel steps in the editor

    def __init__(self, slot_index: int, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("SlotCard")
        self.slot_index = slot_index
        self.macro_name = ""
        self.display_title = ""
        self.base_display_title = ""
        self.hotkey = ""
        self.mode = "hybrid"
        self.is_running = False
        self.feedback_state = ""
        self.custom_icon_config: Dict[str, Any] = {}
        self._suppress_next_release = False
        self._last_activation_at = 0.0
        self.action_kind = ""
        self.has_slot_content = False
        self._preview_mode = False
        self._preview_press_pos: Optional[QtCore.QPoint] = None

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
        self.center_container.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
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
        self.title_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        # Bottom Glow Accent Bar
        glow_layout = QtWidgets.QHBoxLayout()
        glow_layout.setContentsMargins(0, 0, 0, 0)

        self.glow_bar = QtWidgets.QFrame()
        self.glow_bar.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.glow_bar.setFixedHeight(3.5)
        self.glow_bar.setFixedWidth(44)
        self.glow_bar.setStyleSheet("background: transparent; border-radius: 2px;")

        glow_layout.addStretch(1)
        glow_layout.addWidget(self.glow_bar)
        glow_layout.addStretch(1)

        layout.addLayout(glow_layout)

    def set_display_number(self, number: int) -> None:
        self.slot_number_label.setText(f"#{max(1, int(number))}")

    def set_preview_mode(self, enabled: bool) -> None:
        self._preview_mode = bool(enabled)
        self.title_label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, not enabled)
        self.title_label.setCursor(QtCore.Qt.SizeAllCursor if enabled else QtCore.Qt.ArrowCursor)

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

        if bool(theme.get("icon_only", False)) or min(self.width(), self.height()) < 45 or not self.display_title or not text_show or text_pos == "hidden":
            self.title_label.setVisible(False)
            return

        self.title_label.setVisible(True)
        # The editor stores points, not a fraction of the card size. Starting
        # from min(width, height) made short, wide tiles use a tiny caption box.
        requested_px = max(8, round(int(self.custom_icon_config.get("font_size", 12))
                                    * self.logicalDpiY() / 72))
        padding_x = 2 if self.custom_icon_config.get("full_stretch") else 1
        padding_y = 2 if self.custom_icon_config.get("full_stretch") else 1
        max_width = max(8, self.width() - 6)
        max_height = max(8, min(self.height() - 4, round(self.height() * 0.48)))
        text = self.display_title
        compact_caption = self.custom_icon_config.get("icon_source") == "starter_pack"
        self.title_label.setWordWrap(not compact_caption)
        safety_x = 6  # Font overhang and the QLabel style's border.
        safety_y = 4
        for pixel_size in range(requested_px, 5, -1):
            font = QtGui.QFont(self.title_label.font())
            font.setPixelSize(pixel_size)
            font.setWeight(QtGui.QFont.ExtraBold)
            metrics = QtGui.QFontMetrics(font)
            bounds = (QtCore.QRect(0, 0,
                                   max(metrics.horizontalAdvance(text), metrics.boundingRect(text).width()),
                                   metrics.height())
                      if compact_caption else metrics.boundingRect(
                          QtCore.QRect(0, 0, max(1, max_width - 2 * padding_x - safety_x), max_height),
                          QtCore.Qt.TextWordWrap | QtCore.Qt.AlignCenter, text))
            if (bounds.height() + 2 * padding_y + safety_y <= max_height
                    and bounds.width() + 2 * padding_x + safety_x <= max_width):
                break
        else:
            text = metrics.elidedText(text, QtCore.Qt.ElideRight, max_width - 2 * padding_x - safety_x)
            bounds = metrics.boundingRect(text)
        self.title_label.setFont(font)
        self.title_label.setText(text)
        label_width = min(max_width, max(20, bounds.width() + 2 * padding_x + safety_x))
        label_height = min(max_height, max(8, bounds.height() + 2 * padding_y + safety_y))
        self.title_label.setFixedSize(label_width, label_height)
        self.title_label.setToolTip(self.display_title)

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
        if has_custom and full_stretch:
            tile_radius = max(tile_radius, 10.0)
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
                zoom = max(50, min(300, int(self.custom_icon_config.get("image_zoom_percent", 100)))) / 100.0
                scaled_size = QtCore.QSize(max(1, round(target_size.width() * zoom)),
                                           max(1, round(target_size.height() * zoom)))
                scaled_pix = self._scale_full_stretch_pixmap(pix, scaled_size)
                image_x = max(0.0, min(100.0, float(self.custom_icon_config.get("image_x_percent", 50)))) / 100.0
                image_y = max(0.0, min(100.0, float(self.custom_icon_config.get("image_y_percent", 50)))) / 100.0
                left = round((target_size.width() - scaled_size.width()) * image_x)
                top = round((target_size.height() - scaled_size.height()) * image_y)
                if zoom < 1.0:
                    painter.fillPath(path, QtGui.QColor("#101820"))
                painter.drawPixmap(left, top, scaled_pix)
                painter.setClipping(False)
                painter.setBrush(QtCore.Qt.NoBrush)
                frame_width = 1.5
                frame_color = QtGui.QColor("#E4ECEF" if self.underMouse() and bool(win_cfg.get("hover_glow", True)) else "#B9C9CE")
                painter.setPen(QtGui.QPen(frame_color, frame_width))
                frame_inset = frame_width / 2.0 + 0.5
                border_rect = rect.adjusted(frame_inset, frame_inset, -frame_inset, -frame_inset)
                painter.drawRoundedRect(border_rect, tile_radius, tile_radius)
            feedback_color = {"started": "#56B8FF", "completed": "#32D7A0", "failed": "#F16B79"}.get(self.feedback_state)
            if feedback_color:
                painter.setClipping(False)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(QtGui.QPen(QtGui.QColor(feedback_color), max(3.0, border_width + 1.0)))
                highlight_rect = rect.adjusted(2, 2, -2, -2)
                painter.drawRoundedRect(highlight_rect, tile_radius, tile_radius)
            painter.end()

    def set_custom_icon_config(self, config: Dict[str, Any]) -> None:
        self.custom_icon_config = dict(config or {})
        self.display_title = self._resolved_display_title()
        self._update_appearance()

    def _resolved_display_title(self) -> str:
        override = str(self.custom_icon_config.get("text_override") or "").strip()
        if override:
            return override
        title = self.base_display_title
        if self.custom_icon_config.get("icon_source") == "starter_pack":
            title = compact_starter_caption(title)
        return title.strip()

    def set_slot_data(
        self,
        macro_name: str,
        hotkey: str = "",
        mode: str = "hybrid",
        is_running: bool = False,
        display_title: Optional[str] = None,
    ) -> None:
        self.macro_name = (macro_name or "").strip()
        self.base_display_title = self.macro_name if display_title is None else str(display_title).strip()
        self.display_title = self._resolved_display_title()
        self.hotkey = (hotkey or "").strip()
        self.mode = mode
        self.is_running = is_running
        self._update_appearance()

    def set_running(self, running: bool) -> None:
        if self.is_running != running:
            self.is_running = running
            self._update_appearance()

    def set_feedback(self, state: str) -> None:
        if self.feedback_state != state:
            self.feedback_state = state
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
        if has_custom and full_stretch:
            tile_radius = max(tile_radius, 10)
        text_show = bool(self.custom_icon_config.get("text_show", True))
        custom_emoji = self.custom_icon_config.get("emoji", "")
        icon_size = int(self.custom_icon_config.get("icon_size", 40))
        text_pos = str(self.custom_icon_config.get("text_position", "bottom"))
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
                    f"border-radius: 6px; padding: 0px; font-weight: 800; color: #FFFFFF;"
                )
            else:
                self.title_label.setStyleSheet(
                    f"background: transparent; border: none; padding: 0px; font-weight: 800; color: #FFFFFF;"
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

        if has_custom and full_stretch:
            # The image and its single outline are painted together above.
            # A QSS outline here creates a second, offset frame on Windows.
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.glow_bar.setStyleSheet("background: transparent;")
            self.setStyleSheet("#SlotCard { background: transparent; border: none; }")
            self.update()
            return

        if self.feedback_state == "failed":
            self.glow_bar.setStyleSheet("background:#F16B79;border-radius:2px;")
            self.setStyleSheet(f"#SlotCard {{ background:{card_bg}; border:{border_width + 1}px solid #F16B79; border-radius:{tile_radius}px; }}")
        elif self.feedback_state == "completed":
            self.glow_bar.setStyleSheet("background:#32D7A0;border-radius:2px;")
            self.setStyleSheet(f"#SlotCard {{ background:{card_bg}; border:{border_width + 1}px solid #32D7A0; border-radius:{tile_radius}px; }}")
        elif self.feedback_state == "started":
            self.glow_bar.setStyleSheet("background:#56B8FF;border-radius:2px;")
            self.setStyleSheet(f"#SlotCard {{ background:{card_bg}; border:{border_width + 1}px solid #56B8FF; border-radius:{tile_radius}px; }}")
        elif self.is_running:
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
            if self._preview_mode:
                self._preview_press_pos = event.position().toPoint()
                event.accept()
                return
            win = self.window()
            if getattr(win, "_slot_layout_editing", False):
                win._begin_slot_layout_drag(self, event)
                event.accept()
                return
            if hasattr(win, "_start_window_drag_candidate") and win._start_window_drag_candidate(event):
                event.accept()
                return

            self._press_pos = event.globalPos()
            if hasattr(win, "_start_mouse_hold_check"):
                win._start_mouse_hold_check(event.globalPos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        win = self.window()
        if getattr(win, "_slot_layout_editing", False) and not self._preview_mode:
            win._move_slot_layout_drag(event)
            event.accept()
            return
        if hasattr(win, "_handle_window_drag_move") and win._handle_window_drag_move(event):
            event.accept()
            return

        if hasattr(self, "_press_pos") and self._press_pos and (event.buttons() & QtCore.Qt.LeftButton):
            if (event.globalPos() - self._press_pos).manhattanLength() > 15:
                if hasattr(win, "_cancel_mouse_hold_check"):
                    win._cancel_mouse_hold_check()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._preview_mode and event.button() == QtCore.Qt.LeftButton:
            start = self._preview_press_pos
            self._preview_press_pos = None
            if start is not None and (event.position().toPoint() - start).manhattanLength() < 8:
                self._move_preview_text_to(event.position().toPoint())
            event.accept()
            return
        win = self.window()
        if getattr(win, "_slot_layout_editing", False):
            win._finish_slot_layout_drag(event)
            event.accept()
            return
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
            if getattr(self.window(), "_slot_layout_editing", False) and not self._preview_mode:
                event.accept()
                return
            if not self.macro_name:
                win = self.window()
                if hasattr(win, "_show_preset_radial"):
                    win._show_preset_radial(event.globalPosition().toPoint(), sticky=True)
                    event.accept()
                    return
            # Navigation remains responsive; only repeated action taps are suppressed.
            self._suppress_next_release = self.action_kind != "switch_preset"
            win = self.window()
            if hasattr(win, "_cancel_mouse_hold_check"):
                win._cancel_mouse_hold_check()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self._preview_mode and self.custom_icon_config.get("full_stretch"):
            steps = int(event.angleDelta().y() / 120)
            if steps:
                self.icon_zoom_requested.emit(steps)
                event.accept()
                return
        super().wheelEvent(event)

    def _move_preview_text_to(self, point: QtCore.QPoint) -> None:
        if not self.title_label.isVisible():
            return
        max_x = max(1, self.width() - self.title_label.width())
        max_y = max(1, self.height() - self.title_label.height())
        x = max(0, min(max_x, point.x() - self.title_label.width() // 2))
        y = max(0, min(max_y, point.y() - self.title_label.height() // 2))
        x_pct, y_pct = x / max_x * 100.0, y / max_y * 100.0
        self._on_title_label_dragged(x_pct, y_pct)
        self._reposition_title_label()

    def _emit_primary_action(self) -> None:
        if getattr(self.window(), "_slot_layout_editing", False) and not self._preview_mode:
            return
        if not self.macro_name:
            return
        now = time.monotonic()
        preset_switch = self.action_kind == "switch_preset"
        if not preset_switch and now - self._last_activation_at < SLOT_ACCIDENTAL_REPEAT_MS / 1000.0:
            return
        self._last_activation_at = now
        win = self.window()
        if hasattr(win, "_accept_slot_tap"):
            tap_pos = getattr(self, "_press_pos", None)
            if not isinstance(tap_pos, QtCore.QPoint):
                tap_pos = self.mapToGlobal(self.rect().center())
            if not win._accept_slot_tap(tap_pos, preset_switch=preset_switch):
                return
        if hasattr(win, "_play_touch_sound"):
            win._play_touch_sound()
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
    "close_app": ("❌ 덱 닫기", "✕", "#FF4D4D"),
    "opacity": ("💧 투명도 조절", "💧", "#38BDF8"),
    "grid": ("▦ 그리드 변경", "▦", "#F59E0B"),
    "add_slot": ("＋ 슬롯 추가", "＋", "#22C55E"),
    "preset_settings": ("★ 덱 프리셋 관리", "★", "#FBBF24"),
    "preset_mode": ("프리셋 자동/고정", "📌", "#20BFA8"),
    "edge_panel": ("화면 가장자리 패널", "▤", "#38BDF8"),
}

DEFAULT_MANAGEMENT_RADIAL_ITEMS = [
    "prev_page", "next_page", "settings", "deck_dock",
    "preset_settings", "preset_mode", "edge_panel", "close_app",
]


def normalize_management_radial_items(keys: Optional[List[str]]) -> List[str]:
    """Keep Deck Close in the final management-menu sector across saved layouts."""
    keys = ["preset_mode" if key == "auto_resume" else key for key in (keys or [])]
    actions = list(dict.fromkeys(key for key in keys if key in RADIAL_ACTION_PRESETS and key != "close_app"))[:7]
    for fallback in DEFAULT_MANAGEMENT_RADIAL_ITEMS:
        if len(actions) >= 7:
            break
        if fallback not in actions and fallback != "close_app":
            actions.append(fallback)
    for required, preferred, replace in (("deck_dock", 3, "refresh"),
                                         ("preset_settings", 4, "add_slot")):
        if required in actions:
            continue
        target = actions.index(replace) if replace in actions else preferred
        if actions[target] in {"deck_dock", "preset_settings"}:
            target = next((index for index, key in enumerate(actions)
                           if key not in {"deck_dock", "preset_settings"}), target)
        actions[target] = required
    return actions + ["close_app"]

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
        self.center_action_key = ""
        self.center_secondary_action_key = ""
        self.center_secondary_action_text = ""
        self._visual_scale = 1.0
        self._sticky_timer = QtCore.QElapsedTimer()
        self._init_default_items()

    def _init_default_items(self) -> None:
        self.items = []
        for k in DEFAULT_MANAGEMENT_RADIAL_ITEMS:
            title, icon_text, color = RADIAL_ACTION_PRESETS[k]
            self.items.append(RadialPieMenuItem(k, title, icon_text, color))

    def load_custom_items(self, custom_keys: Optional[List[str]]) -> None:
        self.center_title = "Quick Actions"
        self.center_subtitle = "관리 메뉴"
        self.items = []
        for key in normalize_management_radial_items(custom_keys):
            title, icon_text, color = RADIAL_ACTION_PRESETS[key]
            self.items.append(RadialPieMenuItem(key, title, icon_text, color))
        self.update()

    def load_deck_presets(self, presets: List[tuple[str, ...]]) -> None:
        self.center_title = "Preset"
        self.center_subtitle = "모드 전환"
        colors = ["#38BDF8", "#A78BFA", "#34D399", "#F59E0B", "#F472B6", "#22D3EE", "#818CF8", "#FB7185"]
        self.items = [
            RadialPieMenuItem(
                f"deck:{entry[0]}", entry[1], entry[2] if len(entry) > 2 else str(position + 1),
                entry[3] if len(entry) > 3 else colors[position % len(colors)],
            )
            for position, entry in enumerate(presets[:12])
        ]
        if not self.items:
            title, icon_text, color = RADIAL_ACTION_PRESETS["preset_settings"]
            self.items = [RadialPieMenuItem("preset_settings", title, icon_text, color)]
        self.update()

    def popup_at(self, global_pos: QtCore.QPoint, sticky: bool = False) -> None:
        screen = QtGui.QGuiApplication.screenAt(global_pos) or QtGui.QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1920, 1080)
        side = max(1, min(400, available.width(), available.height()))
        self._visual_scale = side / 400.0
        self.setFixedSize(side, side)
        top_left = global_pos - QtCore.QPoint(round(self.center_pt.x() * self._visual_scale),
                                             round(self.center_pt.y() * self._visual_scale))
        top_left.setX(max(available.left(), min(top_left.x(), available.right() - self.width() + 1)))
        top_left.setY(max(available.top(), min(top_left.y(), available.bottom() - self.height() + 1)))
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

    def _get_item_center(self, idx: int, *, scaled: bool = True) -> QtCore.QPointF:
        count = len(self.items) or 8
        angle_deg = (idx * (360.0 / count)) - 90.0
        angle_rad = math.radians(angle_deg)
        label_radius = (self.inner_radius + self.radius) / 2.0
        cx = self.center_pt.x() + label_radius * math.cos(angle_rad)
        cy = self.center_pt.y() + label_radius * math.sin(angle_rad)
        scale = self._visual_scale if scaled else 1.0
        return QtCore.QPointF(cx * scale, cy * scale)

    def _center_button_rect(self, *, scaled: bool = True) -> QtCore.QRectF:
        scale = self._visual_scale if scaled else 1.0
        return QtCore.QRectF(151 * scale, 220 * scale, 98 * scale, 24 * scale)

    def _update_hover_from_pos(self, pos: QtCore.QPoint) -> None:
        dx = pos.x() / self._visual_scale - self.center_pt.x()
        dy = pos.y() / self._visual_scale - self.center_pt.y()
        dist = math.hypot(dx, dy)

        if (self.center_secondary_action_key and self._center_button_rect(scaled=False).contains(
                QtCore.QPointF(pos.x() / self._visual_scale, pos.y() / self._visual_scale))):
            if self.hovered_idx != -3:
                self.hovered_idx = -3
                self.update()
            return

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
            center_action = self.center_action_key
            self.hide()
            if center_action:
                QtCore.QTimer.singleShot(0, lambda key=center_action: self.action_triggered.emit(key))
        elif self.hovered_idx == -3:
            key = self.center_secondary_action_key
            self.hide()
            if key:
                QtCore.QTimer.singleShot(0, lambda action=key: self.action_triggered.emit(action))
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
        painter.scale(self._visual_scale, self._visual_scale)

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

            center = self._get_item_center(index, scaled=False)
            icon_rect = QtCore.QRectF(center.x() - 34, center.y() - 34, 68, 34)
            title_rect = QtCore.QRectF(center.x() - 54, center.y() + 1, 108, 32)
            painter.setPen(QtGui.QColor("#172033"))
            font = painter.font()
            font.setPointSize(14 if hovered else 13)
            font.setBold(True)
            painter.setFont(font)
            if item.key.startswith("edge:") and item.key.split(":", 1)[1] in EDGE_PANEL_SIDES:
                draw_edge_direction(painter, icon_rect, item.key.split(":", 1)[1],
                                    selected=item.title.startswith("● "))
            else:
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
        subtitle_rect = QtCore.QRectF(130, 198, 140, 20) if self.center_secondary_action_key else QtCore.QRectF(130, 200, 140, 24)
        painter.drawText(subtitle_rect, QtCore.Qt.AlignCenter, self.center_subtitle)
        if self.center_secondary_action_key:
            button = self._center_button_rect(scaled=False)
            painter.setBrush(QtGui.QColor("#DBEAFE" if self.hovered_idx == -3 else "#EFF6FF"))
            painter.setPen(QtGui.QPen(QtGui.QColor("#93C5FD"), 1))
            painter.drawRoundedRect(button, 7, 7)
            painter.setPen(QtGui.QColor("#1D4ED8"))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(button, QtCore.Qt.AlignCenter, self.center_secondary_action_text)

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
        self.items_keys: List[str] = list(DEFAULT_MANAGEMENT_RADIAL_ITEMS)
        self.hovered_slot: int = -1
        self.selected_slot: int = -1

    def set_radial_keys(self, keys: List[str]) -> None:
        self.items_keys = normalize_management_radial_items(keys)
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
        if 0 <= slot < 7 and event.mimeData().text() != "close_app":
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        slot = self._get_slot_at_pos(event.pos())
        action_key = event.mimeData().text()
        if 0 <= slot < 7 and action_key in RADIAL_ACTION_PRESETS and action_key != "close_app":
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
        self.starter_rules: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
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
        starter_button = QtWidgets.QPushButton("＋ 기본 앱 모드")
        rename_button = QtWidgets.QPushButton("이름 변경")
        save_button = QtWidgets.QPushButton("현재 UI로 저장")
        delete_button = QtWidgets.QPushButton("삭제")
        add_button.clicked.connect(self._add_preset)
        starter_button.clicked.connect(lambda: self._show_starter_menu(starter_button))
        rename_button.clicked.connect(self._rename_preset)
        save_button.clicked.connect(self._save_current_ui)
        delete_button.clicked.connect(self._delete_preset)
        for button in (add_button, starter_button, rename_button, save_button, delete_button):
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
            icon_text, color = preset_visual(preset_id, preset)
            item.setIcon(preset_qicon(icon_text, color))
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
        if len(self.presets) >= 12:
            QtWidgets.QMessageBox.information(self, "새 프리셋", "프리셋은 최대 12개까지 등록할 수 있습니다.")
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
        insert_default_home_slot(preset)
        self.presets[preset_id] = preset
        self._reload_list(preset_id)

    def _show_starter_menu(self, button: QtWidgets.QPushButton) -> None:
        menu = QtWidgets.QMenu(self)
        for key, name, glyph, color, sites, programs in STARTER_PRESETS:
            action = menu.addAction(preset_qicon(glyph, color), name)
            action.setEnabled(not any(str(preset.get("name") or "").casefold() == name.casefold()
                                      for preset in self.presets.values()))
            action.triggered.connect(
                lambda _checked=False, data=(key, name, glyph, color, sites, programs): self._add_starter(data)
            )
        menu.exec(button.mapToGlobal(QtCore.QPoint(0, button.height())))

    def _add_starter(self, data: tuple[Any, ...]) -> None:
        if len(self.presets) >= 12:
            QtWidgets.QMessageBox.information(self, "기본 앱 모드", "프리셋은 최대 12개까지 등록할 수 있습니다.")
            return
        key, name, glyph, color, sites, programs = data
        preset_id = f"starter-{key}"
        if preset_id in self.presets:
            preset_id = self._new_id()
        preset = self.main_window._build_slot_preset(name)
        slots, icons = starter_action_pack(key)
        preset.update(slots=slots, custom_icons=icons, deck_page_count=1,
                      deck_page_names=["페이지 1"], pinned_slots={}, icon=glyph, color=color,
                      starter_actions_initialized=True)
        insert_default_home_slot(preset)
        self.presets[preset_id] = preset
        self.starter_rules.append((preset_id, sites, programs))
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
        if preset_id != self.active_id:
            source = str(self.presets.get(self.active_id, {}).get("name") or self.active_id)
            answer = QtWidgets.QMessageBox.question(
                self, "다른 프리셋에 현재 화면 저장",
                f"현재 화면 '{source}'의 슬롯과 아이콘으로 '{name}' 프리셋을 덮어쓸까요?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
        previous = self.presets[preset_id]
        self.presets[preset_id] = self.main_window._build_slot_preset(name)
        if preset_id != "default":
            insert_default_home_slot(self.presets[preset_id])
        for visual_key in ("icon", "color"):
            if visual_key in previous:
                self.presets[preset_id][visual_key] = previous[visual_key]
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


class BundledDeckDownloadWorker(QtCore.QThread):
    payload_ready = QtCore.Signal(object)
    download_failed = QtCore.Signal(str)

    def run(self) -> None:
        try:
            self.payload_ready.emit(download_github_bundled_deck())
        except Exception as exc:
            self.download_failed.emit(str(exc))


class BundledDeckUploadWorker(QtCore.QThread):
    upload_complete = QtCore.Signal(object)
    upload_failed = QtCore.Signal(str)

    def __init__(self, app_root: Path, parent: QtCore.QObject):
        super().__init__(parent)
        self.app_root = app_root

    def run(self) -> None:
        try:
            self.upload_complete.emit(publish_bundled_deck(self.app_root))
        except Exception as exc:
            self.upload_failed.emit(str(exc))


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

        self.tab_browser = QtWidgets.QWidget()
        self._init_browser_tab()
        self.tabs.addTab(self.tab_browser, "🌐 프리셋 자동 전환")

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

        self.touch_sound_check = QtWidgets.QCheckBox("슬롯을 누를 때 짧은 터치음 재생")
        self.touch_volume_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.touch_volume_slider.setRange(0, 100)
        self.touch_volume_spin = QtWidgets.QSpinBox()
        self.touch_volume_spin.setRange(0, 100)
        self.touch_volume_spin.setSuffix(" %")
        self.touch_volume_slider.valueChanged.connect(self.touch_volume_spin.setValue)
        self.touch_volume_spin.valueChanged.connect(self.touch_volume_slider.setValue)
        self.touch_sound_check.toggled.connect(self.touch_volume_slider.setEnabled)
        self.touch_sound_check.toggled.connect(self.touch_volume_spin.setEnabled)
        touch_test = QtWidgets.QPushButton("▶ 소리 테스트")
        touch_test.clicked.connect(self._preview_touch_sound)
        touch_box = QtWidgets.QHBoxLayout()
        touch_box.addWidget(self.touch_sound_check)
        touch_box.addWidget(self.touch_volume_slider, 1)
        touch_box.addWidget(self.touch_volume_spin)
        touch_box.addWidget(touch_test)

        form.addRow("윈도우 시작:", self.autostart_check)
        form.addRow("트레이 시작:", self.start_min_check)
        form.addRow("항상 위 고정:", self.topmost_check)
        form.addRow("창 기본 투명도:", opac_box)
        form.addRow("터치 피드백:", touch_box)

    def _preview_touch_sound(self) -> None:
        previous = self.main_window.config.get("touch_sound_volume", 22)
        try:
            self.main_window.config["touch_sound_volume"] = self.touch_volume_spin.value()
            self.main_window._play_touch_sound(force=True)
        finally:
            self.main_window.config["touch_sound_volume"] = previous

    def _init_grid_tab(self) -> None:
        form = QtWidgets.QFormLayout(self.tab_grid)
        form.setSpacing(14)

        self.grid_preset_combo = QtWidgets.QComboBox()
        for title, shape in [
            ("3 x 5 그리드 (15 슬롯)", (3, 5)),
            ("4 x 4 그리드 (16 슬롯)", (4, 4)),
            ("3 x 3 그리드 (9 슬롯)", (3, 3)),
            ("2 x 4 그리드 (8 슬롯)", (2, 4)),
            ("4 x 5 그리드 (20 슬롯)", (4, 5)),
            ("2 x 6 그리드 (12 슬롯)", (2, 6)),
        ]:
            self.grid_preset_combo.addItem(title, shape)
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
        scale = float(self.tile_scale_combo.currentData())
        self.main_window.config["tile_scale"] = scale
        self.main_window._remember_manual_tile_side(BASE_TILE_SIDE * scale)
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
        if checked:
            # Explicitly showing empty slots restores the full configured grid.
            self.main_window.config["crop_trailing_empty_rows"] = False
            self.main_window.config["bottom_blank_space"] = 0
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
        shape = self.grid_preset_combo.itemData(index)
        if shape:
            r, c = shape
            if not self.main_window._remap_active_pins_for_grid(r, c):
                QtWidgets.QMessageBox.information(self, "고정 슬롯", "새 그리드에 들어가지 않는 고정 슬롯이 있습니다. 고정을 해제한 후 변경해 주세요.")
                with QtCore.QSignalBlocker(self.grid_preset_combo):
                    self.grid_preset_combo.setCurrentIndex(self.grid_preset_combo.findData((self.main_window.rows, self.main_window.cols)))
                return
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

        info = QtWidgets.QLabel("원하는 기능을 원형 슬롯으로 끌어다 놓으세요. 마지막 '덱 닫기' 칸은 항상 유지됩니다.")
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

    def _init_browser_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.tab_browser)
        layout.setSpacing(12)
        self.browser_auto_check = QtWidgets.QCheckBox("활성 프로그램·브라우저 탭 주소에 따라 프리셋 자동 전환")
        layout.addWidget(self.browser_auto_check)
        hint = QtWidgets.QLabel("확실히 일치하는 주소만 전환합니다. 일치하지 않거나 연결이 끊기면 현재 프리셋을 유지합니다. 실행 중 매크로는 중지하지 않습니다.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.browser_rules_table = QtWidgets.QTableWidget(0, 2)
        self.browser_rules_table.setHorizontalHeaderLabels(["사이트 도메인 또는 경로", "전환할 프리셋"])
        self.browser_rules_table.horizontalHeader().setStretchLastSection(True)
        self.browser_rules_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.browser_rules_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        layout.addWidget(self.browser_rules_table, 1)
        buttons = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("＋ 규칙 추가")
        add.clicked.connect(lambda: self._add_browser_rule())
        remove = QtWidgets.QPushButton("－ 선택 규칙 삭제")
        remove.clicked.connect(self._remove_browser_rule)
        buttons.addWidget(add); buttons.addWidget(remove); buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(QtWidgets.QLabel("일반 프로그램 규칙 (실행 파일명 기준, 예: obs64.exe)"))
        self.program_rules_table = QtWidgets.QTableWidget(0, 2)
        self.program_rules_table.setHorizontalHeaderLabels(["실행 파일명", "전환할 프리셋"])
        self.program_rules_table.horizontalHeader().setStretchLastSection(True)
        self.program_rules_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.program_rules_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        layout.addWidget(self.program_rules_table, 1)
        program_buttons = QtWidgets.QHBoxLayout()
        add_program = QtWidgets.QPushButton("＋ 프로그램 규칙")
        add_program.clicked.connect(lambda: self._add_program_rule())
        remove_program = QtWidgets.QPushButton("－ 선택 규칙 삭제")
        remove_program.clicked.connect(self._remove_program_rule)
        program_buttons.addWidget(add_program); program_buttons.addWidget(remove_program); program_buttons.addStretch(1)
        layout.addLayout(program_buttons)
        token_row = QtWidgets.QHBoxLayout()
        self.browser_token_edit = QtWidgets.QLineEdit()
        self.browser_token_edit.setReadOnly(True)
        copy_button = QtWidgets.QPushButton("연결 코드 복사")
        copy_button.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.browser_token_edit.text()))
        token_row.addWidget(QtWidgets.QLabel("브라우저 확장앱 연결 코드"))
        token_row.addWidget(self.browser_token_edit, 1)
        token_row.addWidget(copy_button)
        layout.addLayout(token_row)
        self.browser_bridge_label = QtWidgets.QLabel()
        layout.addWidget(self.browser_bridge_label)
        extension_path = Path(__file__).resolve().parent.parent / "browser_extension"
        help_label = QtWidgets.QLabel(f"웨일·엣지·크롬에서 사용할 브라우저마다 확장앱 관리 → 개발자 모드 → 압축해제된 확장앱 로드: {extension_path}\n각 브라우저의 확장앱 옵션에 같은 연결 코드를 붙여넣은 뒤 이 설정을 저장하세요.")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        open_extension = QtWidgets.QPushButton("확장앱 폴더 열기")
        open_extension.clicked.connect(lambda: os.startfile(str(extension_path)) if os.name == "nt" else None)
        layout.addWidget(open_extension)
        self.browser_lock_label = QtWidgets.QLabel()
        resume = QtWidgets.QPushButton("수동 선택 해제 · 자동 전환 재개")
        resume.clicked.connect(self._resume_browser_automatic)
        layout.addWidget(self.browser_lock_label)
        layout.addWidget(resume)
        whale_executable = (self.main_window._whale_executable()
                            or r"C:\Program Files\Naver\Naver Whale\Application\whale.exe")
        self.whale_debugger_command = subprocess.list2cmdline(
            [whale_executable, "--silent-debugger-extension-api"])
        debugger_hint = QtWidgets.QLabel(
            "웨일 디버깅 알림 숨기기(선택): 덕댁이 웨일을 새로 실행할 때는 아래 옵션을 자동으로 사용합니다. "
            "Windows 시작 프로그램이나 다른 앱이 먼저 실행한 웨일에는 적용되지 않습니다. "
            "그 경우 웨일을 완전히 종료한 뒤 해당 실행 항목의 명령에 같은 옵션을 추가하세요. "
            "설치 경로가 다르면 기존 경로를 유지하세요.\n"
            f"{self.whale_debugger_command}\n"
            "이 옵션은 MacroRelay뿐 아니라 다른 디버깅 확장앱의 경고도 숨깁니다."
        )
        debugger_hint.setObjectName("whale_debugger_launch_hint")
        debugger_hint.setWordWrap(True)
        debugger_hint.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        debugger_hint.setStyleSheet("color: #637187; font-size: 9pt;")
        layout.addWidget(debugger_hint)
        self.copy_whale_command_button = QtWidgets.QPushButton("📋 웨일 실행 명령 복사")
        self.copy_whale_command_button.setObjectName("copy_whale_debugger_command")
        self.copy_whale_command_button.clicked.connect(self._copy_whale_debugger_command)
        layout.addWidget(self.copy_whale_command_button)

    def _copy_whale_debugger_command(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.whale_debugger_command)
        self.copy_whale_command_button.setText("✓ 웨일 실행 명령 복사됨")

    def _add_browser_rule(self, site: str = "", preset_id: str = "") -> None:
        row = self.browser_rules_table.rowCount()
        self.browser_rules_table.insertRow(row)
        self.browser_rules_table.setItem(row, 0, QtWidgets.QTableWidgetItem(site))
        combo = QtWidgets.QComboBox()
        for key, preset in self.main_window.config.get("slot_presets", {}).items():
            combo.addItem(str(preset.get("name") or key), key)
        if preset_id:
            combo.setCurrentIndex(max(0, combo.findData(preset_id)))
        self.browser_rules_table.setCellWidget(row, 1, combo)

    def _remove_browser_rule(self) -> None:
        for index in sorted({cell.row() for cell in self.browser_rules_table.selectedIndexes()}, reverse=True):
            self.browser_rules_table.removeRow(index)

    def _add_program_rule(self, executable: str = "", preset_id: str = "") -> None:
        row = self.program_rules_table.rowCount()
        self.program_rules_table.insertRow(row)
        self.program_rules_table.setItem(row, 0, QtWidgets.QTableWidgetItem(executable))
        combo = QtWidgets.QComboBox()
        for key, preset in self.main_window.config.get("slot_presets", {}).items():
            combo.addItem(str(preset.get("name") or key), key)
        if preset_id:
            combo.setCurrentIndex(max(0, combo.findData(preset_id)))
        self.program_rules_table.setCellWidget(row, 1, combo)

    def _remove_program_rule(self) -> None:
        for index in sorted({cell.row() for cell in self.program_rules_table.selectedIndexes()}, reverse=True):
            self.program_rules_table.removeRow(index)

    def _resume_browser_automatic(self) -> None:
        self.main_window._resume_auto_preset()
        self.browser_auto_check.setChecked(True)
        self.browser_lock_label.setText("자동 전환 사용 가능")

    def _init_backup_tab(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.tab_backup)
        layout.setSpacing(12)

        info_label = QtWidgets.QLabel(
            "💾 페이지·프리셋·슬롯 액션·아이콘과 슬롯이 참조하는 매크로·이미지를 JSON으로 내보내거나 복원할 수 있습니다."
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

        btn_bundled = QtWidgets.QPushButton("📦 내장 Deck Dock 구성 불러오기")
        btn_bundled.setStyleSheet("background: #F1ECFF; border: 1.5px solid #A78BFA; color: #5B21B6; font-weight: 700; padding: 10px; border-radius: 9px;")
        btn_bundled.setEnabled(bundled_deck_path(self.main_window.repository.root).is_file())
        btn_bundled.setToolTip(str(bundled_deck_path(self.main_window.repository.root)))
        btn_bundled.clicked.connect(self._import_bundled_config)
        self.btn_bundled = btn_bundled

        self.btn_save_upload_bundled = QtWidgets.QPushButton("☁️ 모든 프리셋 저장 후 GitHub 업로드")
        self.btn_save_upload_bundled.setToolTip("현재 모든 프리셋·슬롯·참조 매크로·이미지·입력값을 로컬에 저장한 뒤 해당 JSON 파일만 GitHub에 업로드합니다. 이 PC에 Git과 GitHub 인증이 필요합니다.")
        self.btn_save_upload_bundled.clicked.connect(self._save_and_upload_bundled)

        self.btn_save_bundled_local = QtWidgets.QPushButton("💾 로컬 내장 구성으로만 저장")
        self.btn_save_bundled_local.setToolTip("GitHub에 전송하지 않고 이 PC에만 모든 덕덱 프리셋을 저장합니다.")
        self.btn_save_bundled_local.clicked.connect(self._save_current_as_bundled)

        self.btn_download_bundled = QtWidgets.QPushButton("⬇️ GitHub 구성 내려받기 (적용 안 함)")
        self.btn_download_bundled.setToolTip("GitHub의 내장 Deck 백업을 이 PC의 로컬 내장 구성으로 내려받습니다. 현재 사용 중인 덱은 자동 교체하지 않습니다.")
        self.btn_download_bundled.clicked.connect(self._download_bundled_from_github)

        self.btn_download_apply = QtWidgets.QPushButton("⬇️ GitHub 구성 내려받아 지금 적용")
        self.btn_download_apply.setToolTip("다른 PC의 현재 덕덱 구성을 먼저 백업한 뒤 GitHub의 모든 프리셋·슬롯·매크로·이미지를 적용합니다.")
        self.btn_download_apply.clicked.connect(self._download_and_apply_bundled)

        btn_reset_all = QtWidgets.QPushButton("⚠️ 모든 슬롯 및 설정 초기화")
        btn_reset_all.setStyleSheet("background: #FFF1F2; border: 1.5px solid #FDA4AF; color: #BE123C; font-weight: 700; padding: 10px; border-radius: 9px;")
        btn_reset_all.clicked.connect(self._reset_all_config)

        layout.addWidget(btn_export)
        layout.addWidget(btn_import)
        layout.addWidget(self.btn_save_upload_bundled)
        layout.addWidget(self.btn_save_bundled_local)
        layout.addWidget(self.btn_download_bundled)
        layout.addWidget(self.btn_download_apply)
        layout.addWidget(btn_bundled)
        layout.addWidget(btn_reset_all)
        layout.addStretch(1)

    def _load_settings(self) -> None:
        self._loading_settings = True
        try:
            self.autostart_check.setChecked(is_autostart_enabled())
            self.start_min_check.setChecked(bool(self.main_window.config.get("start_minimized", False)))
            self.topmost_check.setChecked(self.main_window.always_on_top)
            self.opac_spin.setValue(self.main_window.opacity_val)
            touch_enabled = bool(self.main_window.config.get("touch_sound_enabled", True))
            self.touch_sound_check.setChecked(touch_enabled)
            self.touch_volume_spin.setValue(int(self.main_window.config.get("touch_sound_volume", 22)))
            self.touch_volume_slider.setEnabled(touch_enabled)
            self.touch_volume_spin.setEnabled(touch_enabled)

            self.win_w_spin.setValue(self.main_window.width())
            self.win_h_spin.setValue(self.main_window.height())

            grid_map = {(3, 5): 0, (4, 4): 1, (3, 3): 2, (2, 4): 3, (4, 5): 4, (2, 6): 5}
            current_shape = (self.main_window.rows, self.main_window.cols)
            if current_shape not in grid_map:
                self.grid_preset_combo.addItem(f"{current_shape[0]} x {current_shape[1]} 그리드 (현재)", current_shape)
            self.grid_preset_combo.setCurrentIndex(self.grid_preset_combo.findData(current_shape))
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

            radial_keys = list(self.main_window.config.get("radial_items") or DEFAULT_MANAGEMENT_RADIAL_ITEMS)
            self.radial_preview.set_radial_keys(radial_keys)
            self.browser_auto_check.setChecked(bool(self.main_window.config.get("auto_preset_enabled")))
            self.browser_token_edit.setText(str(self.main_window.config.get("auto_preset_token") or secrets.token_urlsafe(32)))
            self.browser_lock_label.setText(
                "프리셋 고정 중 · '자동 전환 재개'로 해제" if self.main_window.config.get("auto_preset_pinned") else
                "수동 선택 중 · 다음 창/탭 전환 때 재개" if self.main_window.config.get("auto_preset_manual_lock") else "자동 전환 사용 가능"
            )
            self.browser_bridge_label.setText(
                f"로컬 연결 실패: {self.main_window._browser_bridge_error}" if self.main_window._browser_bridge_error
                else ("브라우저 확장앱 연결 대기 중 (127.0.0.1:18773)" if self.main_window._browser_bridge
                      else ("프로그램 규칙만 사용 중" if self.main_window.config.get("auto_preset_enabled") else "자동 전환 꺼짐"))
            )
            self.browser_rules_table.setRowCount(0)
            for rule in self.main_window.config.get("auto_preset_rules") or []:
                if isinstance(rule, dict):
                    self._add_browser_rule(str(rule.get("site") or ""), str(rule.get("preset_id") or ""))
            self.program_rules_table.setRowCount(0)
            for rule in self.main_window.config.get("auto_program_rules") or []:
                if isinstance(rule, dict):
                    self._add_program_rule(str(rule.get("exe") or ""), str(rule.get("preset_id") or ""))
        finally:
            self._loading_settings = False

    def _apply_settings(self) -> None:
        browser_rules = []
        program_rules = []
        for row in range(self.browser_rules_table.rowCount()):
            item = self.browser_rules_table.item(row, 0)
            site = str(item.text() if item else "").strip()
            if not site:
                continue
            try:
                site = normalize_site_rule(site)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "사이트 규칙", str(exc))
                self.tabs.setCurrentWidget(self.tab_browser)
                return
            combo = self.browser_rules_table.cellWidget(row, 1)
            preset_id = str(combo.currentData() or "") if isinstance(combo, QtWidgets.QComboBox) else ""
            if not preset_id:
                QtWidgets.QMessageBox.warning(self, "사이트 규칙", "규칙에 사용할 프리셋을 선택해 주세요.")
                return
            browser_rules.append({"site": site, "preset_id": preset_id})
        by_site: dict[str, str] = {}
        for rule in browser_rules:
            old = by_site.setdefault(rule["site"], rule["preset_id"])
            if old != rule["preset_id"]:
                QtWidgets.QMessageBox.warning(self, "사이트 규칙", "같은 사이트를 서로 다른 프리셋에 연결할 수 없습니다.")
                return
        by_program: dict[str, str] = {}
        for row in range(self.program_rules_table.rowCount()):
            item = self.program_rules_table.item(row, 0)
            executable = str(item.text() if item else "").strip()
            if not executable:
                continue
            try:
                executable = normalize_program_rule(executable)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "프로그램 규칙", str(exc))
                self.tabs.setCurrentWidget(self.tab_browser)
                return
            combo = self.program_rules_table.cellWidget(row, 1)
            preset_id = str(combo.currentData() or "") if isinstance(combo, QtWidgets.QComboBox) else ""
            old = by_program.setdefault(executable, preset_id)
            if old != preset_id:
                QtWidgets.QMessageBox.warning(self, "프로그램 규칙", "같은 프로그램을 서로 다른 프리셋에 연결할 수 없습니다.")
                return
            program_rules.append({"exe": executable, "preset_id": preset_id})
        autostart = self.autostart_check.isChecked()
        set_autostart_enabled(autostart, self.main_window.repository.root)

        self.main_window.config["start_minimized"] = self.start_min_check.isChecked()
        self.main_window.always_on_top = self.topmost_check.isChecked()
        self.main_window._apply_always_on_top()

        self.main_window.opacity_val = self.opac_spin.value()
        self.main_window.setWindowOpacity(self.main_window.opacity_val / 100.0)
        self.main_window.config["touch_sound_enabled"] = self.touch_sound_check.isChecked()
        self.main_window.config["touch_sound_volume"] = self.touch_volume_spin.value()
        self.main_window._sync_touch_sound_volume()

        r, c = self.grid_preset_combo.currentData()
        if not self.main_window._remap_active_pins_for_grid(r, c):
            self.tabs.setCurrentWidget(self.tab_grid)
            QtWidgets.QMessageBox.information(self, "고정 슬롯", "새 그리드에 들어가지 않는 고정 슬롯이 있습니다. 고정을 해제한 후 변경해 주세요.")
            return
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
        self.main_window.config["auto_preset_enabled"] = self.browser_auto_check.isChecked()
        self.main_window.config["auto_preset_rules"] = browser_rules
        self.main_window.config["auto_program_rules"] = program_rules
        self.main_window._update_preset_badge()
        self.main_window.config["auto_preset_token"] = self.browser_token_edit.text().strip()
        self.main_window._sync_browser_bridge()
        if self.main_window._browser_bridge_error:
            self.browser_bridge_label.setText(f"로컬 연결 실패: {self.main_window._browser_bridge_error}")
            self.tabs.setCurrentWidget(self.tab_browser)
            self.main_window._save_config()
            return

        radial_items = normalize_management_radial_items(self.radial_preview.items_keys)
        self.main_window.config["radial_items"] = radial_items
        self.main_window.radial_menu.load_custom_items(radial_items)
        self.main_window._refresh_quick_action_labels()

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
            payload = self.main_window.build_deck_backup_payload()
            Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            QtWidgets.QMessageBox.information(self, "백업 완료", f"페이지·슬롯 액션·대상·딜레이·아이콘·그리드·프리셋이 모두 내보내졌습니다.\n{path}")

    def _import_config(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "QuickSlot 덱 백업 불러오기", "", "JSON 파일 (*.json)"
        )
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.main_window.restore_deck_backup_payload(data)
                QtWidgets.QMessageBox.information(self, "복원 완료", "페이지와 슬롯에 지정된 액션을 포함한 전체 Deck 구성을 복원했습니다!" + self._portability_report_text())
                self.accept()
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "오류", f"백업 파일 로드 실패: {e}")

    def _import_bundled_config(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self, "내장 Deck Dock 구성", "현재 Deck 구성을 내장 백업으로 교체할까요?"
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            data = json.loads(bundled_deck_path(self.main_window.repository.root).read_text(encoding="utf-8"))
            self.main_window.restore_deck_backup_payload(data, portable_mode=not bool(data.get("source_environment")))
            QtWidgets.QMessageBox.information(self, "복원 완료", "내장 Deck Dock 구성을 불러왔습니다." + self._portability_report_text())
            self.accept()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "오류", f"내장 Deck 구성 로드 실패: {e}")

    def _portability_report_text(self) -> str:
        report = getattr(self.main_window, "last_portability_report", {}) or {}
        if not any(report.values()):
            return ""
        return (
            f"\n\n다른 PC 적용: 프로그램 경로 {report.get('rebound_programs', 0)}개, "
            f"창 대상 {report.get('rebound_windows', 0)}개 자동 재연결."
            f"\n이미지 검색 영역 {report.get('fallback_search_regions', 0)}개는 지정 범위에서 못 찾으면 대상 창 전체를 확인합니다."
            f"\n재확인 필요: 찾지 못한 프로그램 {report.get('unresolved_programs', 0)}개, "
            f"화면 절대 좌표 {report.get('screen_coordinates', 0)}개."
            "\n이미지 크기가 다르면 OpenCV가 빠른 검색 후 배율 대응 검색을 시도합니다."
        )

    def _save_bundled_locally(self, *, upload: bool) -> Optional[Path]:
        destination = "로컬과 GitHub" if upload else "로컬"
        answer = QtWidgets.QMessageBox.warning(
            self, "내장 구성 저장",
            f"현재 모든 프리셋과 참조 매크로·이미지·텍스트 입력값을 {destination}에 저장합니다. "
            + ("GitHub 저장소 접근자는 입력값도 볼 수 있습니다. 계속할까요?"
               if upload else "계속할까요?"),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return None
        try:
            payload = self.main_window.build_deck_backup_payload()
            path = save_bundled_deck_payload(self.main_window.repository.root, payload)
            self.btn_bundled.setEnabled(True)
            self.btn_bundled.setToolTip(str(path))
            return path
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "내장 구성 저장 실패", str(exc))
            return None

    def _save_current_as_bundled(self) -> None:
        path = self._save_bundled_locally(upload=False)
        if path is not None:
            QtWidgets.QMessageBox.information(
                self, "내장 구성 저장 완료",
                f"현재 구성을 로컬 내장 파일로 저장했습니다. 이전 파일은 시간표시 백업으로 보존했습니다.\n{path}\n\nGitHub에는 아직 업로드되지 않았습니다.",
            )

    def _save_and_upload_bundled(self) -> None:
        path = self._save_bundled_locally(upload=True)
        if path is None:
            return
        self.btn_save_upload_bundled.setEnabled(False)
        self.btn_save_bundled_local.setEnabled(False)
        self.btn_save_upload_bundled.setText("☁️ GitHub 업로드 중…")
        worker = BundledDeckUploadWorker(self.main_window.repository.root, self.main_window)
        self.main_window._bundled_deck_upload_worker = worker
        worker.upload_complete.connect(self._github_bundle_uploaded)
        worker.upload_failed.connect(self._github_bundle_upload_failed)
        worker.finished.connect(lambda: setattr(self.main_window, "_bundled_deck_upload_worker", None))
        worker.start()

    def _finish_bundle_upload(self) -> None:
        self.btn_save_upload_bundled.setEnabled(True)
        self.btn_save_bundled_local.setEnabled(True)
        self.btn_save_upload_bundled.setText("☁️ 모든 프리셋 저장 후 GitHub 업로드")

    def _github_bundle_uploaded(self, commit: Optional[str]) -> None:
        self._finish_bundle_upload()
        detail = (f"업로드 완료: {commit[:12]}" if commit else "GitHub에 이미 같은 구성이 저장되어 있습니다.")
        QtWidgets.QMessageBox.information(
            self, "GitHub 내장 구성 저장 완료",
            f"모든 덕덱 프리셋을 로컬에 저장했습니다. {detail}\n"
            "다른 PC에서는 'GitHub 구성 내려받아 지금 적용'을 누르면 됩니다.",
        )

    def _github_bundle_upload_failed(self, message: str) -> None:
        self._finish_bundle_upload()
        QtWidgets.QMessageBox.warning(
            self, "GitHub 업로드 실패",
            "로컬 내장 구성은 저장되어 있습니다. GitHub에는 반영되지 않았습니다.\n\n" + message,
        )

    def _download_bundled_from_github(self) -> None:
        self._start_bundled_download(apply=False)

    def _download_and_apply_bundled(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self, "GitHub 구성 적용",
            "GitHub의 모든 덕덱 프리셋·슬롯·참조 매크로·이미지를 내려받아 현재 구성을 교체할까요? "
            "교체하기 전에 이 PC의 현재 구성을 별도 백업 파일로 보존합니다.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self._start_bundled_download(apply=True)

    def _start_bundled_download(self, *, apply: bool) -> None:
        self._github_download_apply = apply
        self.btn_download_bundled.setEnabled(False)
        self.btn_download_apply.setEnabled(False)
        self.btn_download_bundled.setText("⬇️ GitHub 구성 내려받는 중…")
        worker = BundledDeckDownloadWorker(self.main_window)
        self.main_window._bundled_deck_download_worker = worker
        worker.payload_ready.connect(self._github_bundle_downloaded)
        worker.download_failed.connect(self._github_bundle_download_failed)
        worker.finished.connect(lambda: setattr(self.main_window, "_bundled_deck_download_worker", None))
        worker.start()

    def _finish_bundled_download(self) -> None:
        self.btn_download_bundled.setEnabled(True)
        self.btn_download_apply.setEnabled(True)
        self.btn_download_bundled.setText("⬇️ GitHub 구성 내려받기 (적용 안 함)")

    def _github_bundle_downloaded(self, payload: Dict[str, Any]) -> None:
        self._finish_bundled_download()
        try:
            restore_point = None
            if self._github_download_apply:
                current = self.main_window.build_deck_backup_payload()
                restore_point = save_deck_restore_point(self.main_window.repository.root, current)
            path = save_bundled_deck_payload(self.main_window.repository.root, payload)
            self.btn_bundled.setEnabled(True)
            self.btn_bundled.setToolTip(str(path))
            if restore_point is not None:
                self.main_window.restore_deck_backup_payload(payload)
                QtWidgets.QMessageBox.information(
                    self, "GitHub 구성 적용 완료",
                    f"GitHub의 모든 덕덱 프리셋을 적용했습니다.\n이전 구성 백업: {restore_point}"
                    + self._portability_report_text(),
                )
                self.accept()
            else:
                QtWidgets.QMessageBox.information(
                    self, "내려받기 완료",
                    "GitHub 구성을 이 PC의 내장 파일로 저장했습니다. 현재 덱은 바뀌지 않았습니다. "
                    "적용하려면 '내장 Deck Dock 구성 불러오기'를 누르세요.",
                )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "내려받기/적용 실패", str(exc))
        finally:
            self._github_download_apply = False

    def _github_bundle_download_failed(self, message: str) -> None:
        self._finish_bundled_download()
        self._github_download_apply = False
        QtWidgets.QMessageBox.warning(self, "GitHub 내려받기 실패", message)

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


class DeckPresetBadge(QtWidgets.QPushButton):
    """A tap goes home; dragging a docked panel moves it without triggering the tap."""

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        deck = self.window()
        if event.button() == QtCore.Qt.LeftButton and deck._edge_panel_enabled():
            deck._start_window_drag_candidate(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        deck = self.window()
        if deck._edge_panel_enabled() and deck._handle_window_drag_move(event):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        deck = self.window()
        if deck._edge_panel_enabled() and deck._handle_window_drag_release(event):
            self.setDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DeckResizeGrip(QtWidgets.QWidget):
    """Corner handles for scaling tiles or trimming only the empty bottom area."""

    def __init__(self, deck: "QuickSlotDeckWindow", mode: str = "bottom_right") -> None:
        super().__init__(deck)
        self.deck = deck
        self.mode = mode
        self._drag_started = False
        self.setFixedSize(24, 24)
        self.setCursor(QtCore.Qt.SizeVerCursor if mode == "bottom_padding" else QtCore.Qt.SizeFDiagCursor)
        self.setAttribute(QtCore.Qt.WA_Hover, True)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(10, 17, 29, 190))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 7, 7)
        painter.setPen(QtGui.QPen(QtGui.QColor("#8EF0DA"), 2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        if self.mode == "bottom_padding":
            for y in (10, 14, 18):
                painter.drawLine(6, y, 18, y)
        else:
            for offset in (0, 5, 10):
                painter.drawLine(8 + offset, 19, 19, 8 + offset)
        painter.end()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_started = False
            self.deck._resize_edge = self.mode
            self.deck._resize_start_geom = self.deck.geometry()
            self.deck._resize_start_pos = event.globalPosition().toPoint()
            self.deck._cancel_mouse_hold_check()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.buttons() & QtCore.Qt.LeftButton and getattr(self.deck, "_resize_edge", "") == self.mode:
            global_pos = event.globalPosition().toPoint()
            if self.mode == "bottom_padding" and not self._drag_started:
                if abs(global_pos.y() - self.deck._resize_start_pos.y()) < QtWidgets.QApplication.startDragDistance():
                    event.accept()
                    return
                self.deck._prepare_bottom_padding_resize()
                self.deck._resize_start_geom = self.deck.geometry()
                self._drag_started = True
            self.deck._handle_border_resize(global_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and getattr(self.deck, "_resize_edge", "") == self.mode:
            self.deck._resize_edge = ""
            if self.mode == "bottom_padding":
                if self._drag_started:
                    self.deck._remember_bottom_blank_space()
                    self.deck._save_config()
            else:
                self.deck._save_config()
            self._drag_started = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
        self._process_slot_map: Dict[int, tuple[str, int]] = {}
        self._slot_feedback: Dict[tuple[str, int], str] = {}
        self._slot_layout_editing = False
        self._layout_drag = None
        self._layout_placeholders = []
        self._visual_slot_positions = {}
        self._feedback_sequence: Dict[tuple[str, int], int] = {}
        self.execution_history: List[Dict[str, str]] = []

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
            "touch_sound_enabled": True,
            "touch_sound_volume": 22,
            "empty_slot_opacity": 0,
            "show_empty_slots": False,
            "bottom_blank_space": 0,
            "crop_trailing_empty_rows": False,
            "radial_items": list(DEFAULT_MANAGEMENT_RADIAL_ITEMS),
            "slot_presets": {},
            "active_slot_preset": "default",
            "auto_preset_enabled": False,
            "auto_preset_rules": [],
            "auto_program_rules": [],
            "auto_preset_manual_lock": False,
            "auto_preset_pinned": False,
            "edge_panel_side": "free",
        }
        self.custom_icons: Dict[str, Dict[str, Any]] = {}
        self.buttons: List[StreamDeckButton] = []
        self._last_slot_tap_at = 0.0
        self._last_slot_tap_pos = QtCore.QPoint(-10000, -10000)
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
        self._restored_window_geometry = False
        self._browser_bridge: Optional[DeckBrowserBridge] = None
        self._browser_bridge_error = ""
        self._browser_bridge_retry_at = 0.0
        self._last_browser_url = ""
        self._last_browser_event_at = 0
        self._pending_browser_actions: Dict[str, tuple[str, int, str, float]] = {}
        self._browser_action_requested_at: Dict[str, float] = {}
        self._browser_action_hwnds: Dict[str, int] = {}
        self._browser_action_names: Dict[str, str] = {}
        self._browser_action_notice = ""
        self._browser_notice_sequence = 0
        self._browser_yielded_topmost = False
        self._last_foreground_identity = ("", 0)
        self._auto_config_save = QtCore.QTimer(self)
        self._auto_config_save.setSingleShot(True)
        self._auto_config_save.timeout.connect(self._save_config)

        # A persistent control panel, not an application-switching destination.
        # Qt.Tool maps to WS_EX_TOOLWINDOW on Windows (no taskbar / Alt+Tab entry).
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

        self.setWindowTitle("MacroRelay QuickSlot Deck")
        self.setObjectName("AppRoot")
        self.setStyleSheet(stylesheet())

        icon_path = self.repository.root / "branding" / "macrorelay-quickslot.ico"
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        self._load_config()
        upgraded_quick_actions = self._upgrade_quick_actions()
        # Manual preset selection is a session override, not a startup lock.
        stale_manual_lock = bool(self.config.get("auto_preset_manual_lock"))
        self.config["auto_preset_manual_lock"] = False
        self._touch_sound_effect = None
        self._init_touch_sound()
        self._ensure_slot_presets()
        seeded_starter_presets = self._seed_starter_presets()
        seeded_starter_actions = self._seed_starter_actions()
        seeded_browser_shortcuts = self._seed_browser_shortcuts()
        migrated_home_tiles = self._install_home_tiles()
        repaired_tetris_mix = self._repair_tetris_youtube_copy()
        migrated_youtube_home = self._upgrade_youtube_home_action()
        self.radial_menu = RadialPieMenuWidget(self)
        self.radial_menu.load_custom_items(self.config.get("radial_items"))
        self.radial_menu.action_triggered.connect(self._handle_radial_action)
        self.preset_radial_menu = RadialPieMenuWidget(self)
        self.preset_radial_menu.action_triggered.connect(self._handle_preset_radial_action)
        self._refresh_preset_radial_menu()
        self.edge_radial_menu = RadialPieMenuWidget(self)
        self.edge_radial_menu.action_triggered.connect(self._handle_edge_panel_action)
        self._edge_panel_screen_obj = None

        self._init_ui()
        self._apply_theme()
        self._setup_watcher()

        # Polling timer for active macro states
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(800)
        self.poll_timer.timeout.connect(self.refresh_states)
        self.poll_timer.start()

        self.refresh_slots()
        self.browser_event_timer = QtCore.QTimer(self)
        self.browser_event_timer.setInterval(50)
        self.browser_event_timer.timeout.connect(self._poll_browser_events)
        self.browser_event_timer.start()
        self.program_event_timer = QtCore.QTimer(self)
        self.program_event_timer.setInterval(250)
        self.program_event_timer.timeout.connect(self._poll_foreground_program)
        self.program_event_timer.start()
        self._sync_browser_bridge()
        if (upgraded_quick_actions or seeded_starter_presets or seeded_starter_actions or seeded_browser_shortcuts
                or migrated_home_tiles or repaired_tetris_mix or migrated_youtube_home or stale_manual_lock):
            self._save_config()

    def _upgrade_quick_actions(self) -> bool:
        if self.config.get("quick_action_controls_version"):
            return False
        keys = normalize_management_radial_items(self.config.get("radial_items"))
        protected = {"deck_dock", "preset_settings", "close_app", "preset_mode", "edge_panel"}
        for required, preferred in (("preset_mode", "studio"), ("edge_panel", "prev_page")):
            if required in keys:
                continue
            target = keys.index(preferred) if preferred in keys else next(
                index for index in range(6, -1, -1) if keys[index] not in protected)
            keys[target] = required
        self.config["radial_items"] = keys
        self.config["quick_action_controls_version"] = 1
        return True

    def _init_touch_sound(self) -> None:
        if QSoundEffect is None or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        sound_path = _quickslot_touch_sound_path()
        if not sound_path.is_file():
            return
        effect = QSoundEffect(self)
        effect.setSource(QtCore.QUrl.fromLocalFile(str(sound_path)))
        effect.setLoopCount(1)
        self._touch_sound_effect = effect
        self._sync_touch_sound_volume()

    def _sync_touch_sound_volume(self) -> None:
        effect = getattr(self, "_touch_sound_effect", None)
        if effect is None:
            return
        volume = max(0, min(100, int(self.config.get("touch_sound_volume", 22))))
        effect.setVolume(volume / 100.0)

    def _play_touch_sound(self, *, force: bool = False) -> None:
        if not force and not bool(self.config.get("touch_sound_enabled", True)):
            return
        effect = getattr(self, "_touch_sound_effect", None)
        if effect is None:
            self._init_touch_sound()
            effect = getattr(self, "_touch_sound_effect", None)
        if effect is None:
            return
        self._sync_touch_sound_volume()
        if effect.isPlaying():
            effect.stop()
        effect.play()

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
                self.config["radial_items"] = normalize_management_radial_items(
                    self.config.get("radial_items")
                )
                geom = data.get("geometry")
                if geom:
                    self._restored_window_geometry = bool(
                        self.restoreGeometry(QtCore.QByteArray.fromHex(geom.encode("ascii")))
                    )

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

    def build_deck_backup_payload(self) -> Dict[str, Any]:
        """Return a portable backup containing both visuals and executable slots."""
        self._capture_active_slot_preset()
        self._save_config()
        deck_config = json.loads(self.config_path.read_text(encoding="utf-8"))
        deck_config.get("config", {}).pop("auto_preset_token", None)
        # The active preset already owns an identical icon set. Avoid storing
        # large embedded images twice in one backup.
        deck_config["custom_icons"] = {}
        hotkeys = copy.deepcopy(self.repository.load_hotkeys())
        macro_names: set[str] = set()
        for slot_list in [hotkeys.get("slots") or []] + [
            preset.get("slots") or []
            for preset in self.config.get("slot_presets", {}).values()
            if isinstance(preset, dict)
        ]:
            for slot in slot_list:
                if not isinstance(slot, dict):
                    continue
                action = slot.get("action") or {}
                if isinstance(action, dict):
                    if action.get("kind") == "run_macro" and action.get("macro"):
                        macro_names.add(str(action["macro"]))
                    elif action.get("kind") == "multi_macros":
                        macro_names.update(str(name) for name in action.get("macros") or [] if name)
                if slot.get("macro") and slot.get("mode") != "deck_action":
                    macro_names.add(str(slot["macro"]))
        macros: Dict[str, Any] = {}
        assets: Dict[str, Any] = {}
        asset_index = self.repository.load_assets()
        pending_macros = sorted(macro_names)
        while pending_macros:
            name = pending_macros.pop()
            if name in macros:
                continue
            path = self.repository.macro_path(name)
            if not path.is_file():
                continue
            macro = json.loads(path.read_text(encoding="utf-8"))
            macros[name] = macro
            for step in macro.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                if step.get("action") == "call_submacro" and step.get("macro"):
                    child_name = str(step["macro"])
                    if child_name not in macros:
                        pending_macros.append(child_name)
                aliases = [step.get("asset")]
                aliases.extend(step.get("assets") or [] if isinstance(step.get("assets"), list) else [])
                for alias in aliases:
                    if not isinstance(alias, str) or not alias or alias in assets:
                        continue
                    path = self.repository.asset_path(alias, asset_index)
                    if path is not None and path.is_file():
                        assets[alias] = {
                            "file": path.name,
                            "image_data": base64.b64encode(path.read_bytes()).decode("ascii"),
                        }
        return {
            "format": "macrorelay-deck-backup",
            "version": DECK_BACKUP_VERSION,
            "source_environment": {"host_fingerprint": host_fingerprint()},
            "deck_config": deck_config,
            "hotkeys": hotkeys,
            "macros": macros,
            "assets": assets,
        }

    @staticmethod
    def _legacy_backup_hotkeys(deck_config: Dict[str, Any]) -> Dict[str, Any]:
        config = dict(deck_config.get("config") or {})
        presets = dict(config.get("slot_presets") or {})
        active_id = str(config.get("active_slot_preset") or "")
        preset = dict(presets.get(active_id) or {})
        if not preset:
            return {}
        return {
            "slots": copy.deepcopy(list(preset.get("slots") or [])),
            "deck_page_count": max(1, int(preset.get("deck_page_count") or 1)),
            "deck_page_names": copy.deepcopy(list(preset.get("deck_page_names") or [])),
        }

    def restore_deck_backup_payload(self, payload: Dict[str, Any], *, portable_mode: bool = False) -> None:
        """Restore new full backups and migrate config-only legacy exports."""
        if not isinstance(payload, dict):
            raise ValueError("올바른 Deck 백업 JSON이 아닙니다.")
        payload, self.last_portability_report = adapt_backup_for_host(payload, force=portable_mode)
        is_full = payload.get("format") == "macrorelay-deck-backup"
        deck_config = copy.deepcopy(payload.get("deck_config") if is_full else payload)
        if not isinstance(deck_config, dict):
            raise ValueError("Deck 설정 데이터가 없습니다.")
        hotkeys = copy.deepcopy(payload.get("hotkeys") if is_full else self._legacy_backup_hotkeys(deck_config))
        if not isinstance(hotkeys, dict) or not isinstance(hotkeys.get("slots"), list):
            raise ValueError("백업에 슬롯 구성 데이터가 없습니다.")
        backup_config = deck_config.get("config") if isinstance(deck_config.get("config"), dict) else {}
        backup_presets = backup_config.get("slot_presets") if isinstance(backup_config.get("slot_presets"), dict) else {}
        active_id = str(backup_config.get("active_slot_preset") or "")
        active_preset = backup_presets.get(active_id)
        if (isinstance(active_preset, dict) and isinstance(active_preset.get("slots"), list)
                and hotkeys["slots"] != active_preset["slots"]):
            raise ValueError("백업의 활성 프리셋과 실행 슬롯이 서로 다릅니다. 다른 프리셋에 잘못 복사되지 않도록 적용을 중단했습니다.")

        macros = payload.get("macros") or {}
        assets = payload.get("assets") or {}
        if not isinstance(macros, dict) or not isinstance(assets, dict):
            raise ValueError("매크로 또는 이미지 자산 형식이 올바르지 않습니다.")
        for name, macro in macros.items():
            if not isinstance(name, str) or not isinstance(macro, dict):
                raise ValueError("매크로 백업 형식이 올바르지 않습니다.")
            self.repository.save_macro(name, macro)
        if assets:
            asset_index = self.repository.load_assets()
            for alias, asset in assets.items():
                if not isinstance(alias, str) or not isinstance(asset, dict):
                    raise ValueError("이미지 자산 형식이 올바르지 않습니다.")
                filename = Path(str(asset.get("file") or "")).name
                if not filename or filename != str(asset.get("file") or ""):
                    raise ValueError("이미지 자산 파일명이 올바르지 않습니다.")
                image_bytes = base64.b64decode(str(asset.get("image_data") or ""), validate=True)
                if not image_bytes:
                    raise ValueError("이미지 자산 데이터가 비어 있습니다.")
                target = self.repository.assets_dir / filename
                target.write_bytes(image_bytes)
                asset_index[alias] = {"file": str(target.relative_to(self.repository.root)).replace("\\", "/"), "source": "deck-backup", "size": len(image_bytes)}
            self.repository._write_json(self.repository.assets_index_path, asset_index)

        temp_path = self.config_path.with_suffix(self.config_path.suffix + ".import.tmp")
        temp_path.write_text(json.dumps(deck_config, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_path, self.config_path)
        self.config["auto_preset_enabled"] = False
        self.config["auto_preset_rules"] = []
        self.config["auto_program_rules"] = []
        self.config["auto_preset_manual_lock"] = False
        self._load_config()
        self.config["auto_preset_token"] = secrets.token_urlsafe(32)
        self.config["auto_preset_manual_lock"] = False
        self._upgrade_quick_actions()
        self._ensure_slot_presets()
        active_id = str(self.config.get("active_slot_preset") or "")
        active = dict(self.config.get("slot_presets", {}).get(active_id) or {})
        if not self.custom_icons and active.get("custom_icons"):
            self.custom_icons = copy.deepcopy(dict(active["custom_icons"]))
        self._save_preset_hotkeys(hotkeys)
        self._capture_active_slot_preset()
        self._install_home_tiles()
        self.current_page = 0
        self._refresh_preset_radial_menu()
        self.radial_menu.load_custom_items(self.config.get("radial_items"))
        self._apply_theme()
        self.refresh_slots()
        self._save_config()
        self._sync_browser_bridge()

    def _sync_browser_bridge(self) -> None:
        enabled = bool((self.config.get("auto_preset_enabled", False) and self.config.get("auto_preset_rules"))
                       or self.config.get("browser_shortcuts_version"))
        token = str(self.config.get("auto_preset_token") or "")
        if self._browser_bridge is not None and (not enabled or self._browser_bridge.token != token):
            self._browser_bridge.close()
            self._browser_bridge = None
        if not enabled:
            self._browser_bridge_error = ""
            self._browser_bridge_retry_at = 0.0
            self._update_preset_badge()
            return
        if self._browser_bridge is not None:
            return
        if not token:
            token = secrets.token_urlsafe(32)
            self.config["auto_preset_token"] = token
            self._save_config()
        try:
            self._browser_bridge = DeckBrowserBridge(token)
            self._browser_bridge_error = ""
            self._browser_bridge_retry_at = 0.0
            self._last_browser_event_at = 0
            self._last_browser_url = ""
        except OSError as exc:
            self._browser_bridge_error = str(exc)
            self._browser_bridge_retry_at = time.monotonic() + 5.0
        self._update_preset_badge()

    def _poll_browser_events(self) -> None:
        bridge = self._browser_bridge
        if bridge is None and ((self.config.get("auto_preset_enabled") and self.config.get("auto_preset_rules"))
                               or self.config.get("browser_shortcuts_version")):
            if time.monotonic() >= self._browser_bridge_retry_at:
                self._sync_browser_bridge()
                bridge = self._browser_bridge
        if bridge is None:
            return
        suppress_auto_switch = bool(self._pending_browser_actions)
        while not bridge.command_results.empty():
            try:
                result = bridge.command_results.get_nowait()
            except Exception:
                break
            pending = self._pending_browser_actions.pop(str(result.get("id") or ""), None)
            if pending is None:
                continue
            self._browser_action_requested_at.pop(str(result.get("id") or ""), None)
            hwnd = self._browser_action_hwnds.pop(str(result.get("id") or ""), 0)
            browser_name = self._browser_action_names.pop(str(result.get("id") or ""), "웨일")
            preset_id, slot_index, label, _deadline = pending
            source_preset = str(self.config.get("active_slot_preset") or "default")
            if result.get("status") == "ok":
                self._switch_slot_preset(preset_id, automatic=True)
                self._set_deck_status("브라우저 전환", label)
                self._mark_slot_status(slot_index, "completed", label, preset_id=source_preset)
                if hwnd:
                    QtCore.QTimer.singleShot(100, lambda target=hwnd: self._yield_browser_foreground(target))
            else:
                message = str(result.get("error") or "탭 활성화 실패")
                self._set_deck_status("브라우저 전환 실패", message, error=True)
                self._mark_slot_status(slot_index, "failed", message, preset_id=source_preset)
                self._show_browser_action_notice(f"{browser_name} 탭 선택 실패")
        for command_id, (preset_id, slot_index, label, deadline) in list(self._pending_browser_actions.items()):
            requested_at = self._browser_action_requested_at.get(command_id, deadline - 10.0)
            browser_name = self._browser_action_names.get(command_id, "웨일")
            browser_kind = {"웨일": "whale", "엣지": "edge", "크롬": "chrome"}[browser_name]
            last_poll = bridge.last_command_poll_by_browser.get(browser_kind, bridge.last_command_poll_at if browser_kind == "whale" else 0.0)
            if time.monotonic() >= requested_at + 2.5 and last_poll < requested_at:
                self._pending_browser_actions.pop(command_id, None)
                bridge.cancel_command(command_id)
                self._browser_action_requested_at.pop(command_id, None)
                self._browser_action_hwnds.pop(command_id, None)
                self._browser_action_names.pop(command_id, None)
                stale_extension = bridge.last_active_tab_at >= requested_at
                message = (f"{browser_name} 확장앱을 다시 로드해 주세요." if stale_extension else
                           f"{browser_name} 확장앱 연결 코드와 실행 상태를 확인해 주세요.")
                self._set_deck_status("브라우저 연결 실패", message, error=True)
                self._mark_slot_status(slot_index, "failed", message, preset_id="default")
                self._show_browser_action_notice("확장앱 다시 로드 필요" if stale_extension else f"{browser_name} 연결 확인 필요")
                continue
            if time.monotonic() >= deadline:
                self._pending_browser_actions.pop(command_id, None)
                bridge.cancel_command(command_id)
                self._browser_action_requested_at.pop(command_id, None)
                self._browser_action_hwnds.pop(command_id, None)
                self._browser_action_names.pop(command_id, None)
                self._set_deck_status("브라우저 연결 대기 초과", f"{browser_name} 확장앱 연결을 확인해 주세요.", error=True)
                self._mark_slot_status(slot_index, "failed", f"{browser_name} 확장앱 응답 없음", preset_id="default")
                self._show_browser_action_notice(f"{browser_name} 탭 응답 없음")
        if suppress_auto_switch:
            while not bridge.events.empty():
                try:
                    observed_at, url = bridge.events.get_nowait()
                    if observed_at > self._last_browser_event_at:
                        self._last_browser_event_at, self._last_browser_url = observed_at, url
                except Exception:
                    break
            return
        if not bool(self.config.get("auto_preset_enabled")):
            return
        latest_url = ""
        latest_at = 0
        while not bridge.events.empty():
            try:
                observed_at, url = bridge.events.get_nowait()
                if observed_at >= latest_at:
                    latest_at, latest_url = observed_at, url
            except Exception:
                break
        if not latest_url or latest_at <= self._last_browser_event_at:
            return
        previous_url = self._last_browser_url
        self._last_browser_event_at = latest_at
        self._last_browser_url = latest_url
        if self.config.get("auto_preset_pinned"):
            return
        if bool(self.config.get("auto_preset_manual_lock")):
            if latest_url == previous_url:
                return
            self.config["auto_preset_manual_lock"] = False
            self._update_preset_badge()
        target = preset_for_url(latest_url, list(self.config.get("auto_preset_rules") or []))
        if target in self.config.get("slot_presets", {}):
            self._switch_slot_preset(target, automatic=True)

    def _poll_foreground_program(self) -> None:
        if not self.config.get("auto_preset_enabled"):
            return
        executable, pid = foreground_program()
        identity = (executable, pid)
        if not executable or pid == os.getpid() or identity == self._last_foreground_identity:
            return
        self._last_foreground_identity = identity
        if self.config.get("auto_preset_pinned"):
            return
        if self.config.get("auto_preset_manual_lock"):
            self.config["auto_preset_manual_lock"] = False
            self._update_preset_badge()
        if not self.config.get("auto_program_rules"):
            return
        # A focused browser tab's site rule wins over a generic program rule.
        if executable in {"whale.exe", "msedge.exe", "chrome.exe"} and self.config.get("auto_preset_rules"):
            return
        target = preset_for_program(executable, list(self.config.get("auto_program_rules") or []))
        if target in self.config.get("slot_presets", {}):
            self._switch_slot_preset(target, automatic=True)

    def _resume_auto_preset(self) -> None:
        previous_identity = self._last_foreground_identity
        self.config["auto_preset_enabled"] = True
        self.config["auto_preset_pinned"] = False
        self.config["auto_preset_manual_lock"] = False
        self._last_foreground_identity = ("", 0)
        self._sync_browser_bridge()
        executable, pid = foreground_program()
        if not executable or pid == os.getpid():
            executable, _pid = previous_identity
        if executable in {"whale.exe", "msedge.exe", "chrome.exe"}:
            target = preset_for_url(self._last_browser_url, list(self.config.get("auto_preset_rules") or []))
        else:
            target = preset_for_program(executable, list(self.config.get("auto_program_rules") or []))
        if target in self.config.get("slot_presets", {}):
            self._switch_slot_preset(target, automatic=True)
        self._update_preset_badge()
        self._save_config()

    def _toggle_preset_mode(self) -> None:
        if self.config.get("auto_preset_pinned") or not self.config.get("auto_preset_enabled"):
            self._resume_auto_preset()
        else:
            self.config["auto_preset_pinned"] = True
            self.config["auto_preset_manual_lock"] = False
            self._update_preset_badge()
            self._auto_config_save.start(700)

    def _refresh_quick_action_labels(self) -> None:
        menu = getattr(self, "radial_menu", None)
        if menu is None:
            return
        fixed = bool(self.config.get("auto_preset_pinned") or not self.config.get("auto_preset_enabled"))
        for item in menu.items:
            if item.key == "preset_mode":
                item.title = "프리셋 자동으로" if fixed else "현재 프리셋 고정"
                item.icon_text = "↺" if fixed else "📌"
        menu.update()

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
            "pinned_slots": {},
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

    def _seed_starter_presets(self) -> bool:
        """Install a small, non-destructive first-run mode pack and disabled rules."""
        if self.config.get("starter_presets_version"):
            return False
        self._ensure_slot_presets()
        presets = self.config["slot_presets"]
        default = presets.get("default")
        if isinstance(default, dict):
            default.setdefault("icon", DEFAULT_PRESET_VISUAL[0])
            default.setdefault("color", DEFAULT_PRESET_VISUAL[1])
        site_rules = self.config.setdefault("auto_preset_rules", [])
        program_rules = self.config.setdefault("auto_program_rules", [])
        for key, name, icon, color, sites, programs in STARTER_PRESETS[:3]:
            preset_id = next((existing_id for existing_id, existing in presets.items()
                              if str(existing.get("name") or "").casefold() == name.casefold()), "")
            if not preset_id and len(presets) < 12:
                preset_id = f"starter-{key}"
                if preset_id in presets:
                    continue
                preset = self._build_slot_preset(name)
                preset.update(slots=[], custom_icons={}, deck_page_count=1,
                              deck_page_names=["페이지 1"], pinned_slots={})
                presets[preset_id] = preset
            if not preset_id:
                continue
            preset = presets[preset_id]
            preset.setdefault("icon", icon)
            preset.setdefault("color", color)
            for site in sites:
                if not any(isinstance(rule, dict) and str(rule.get("site") or "").casefold() == site
                           for rule in site_rules):
                    site_rules.append({"site": site, "preset_id": preset_id})
            for executable in programs:
                if not any(isinstance(rule, dict) and str(rule.get("exe") or "").casefold() == executable
                           for rule in program_rules):
                    program_rules.append({"exe": executable, "preset_id": preset_id})
        self.config["starter_presets_version"] = 1
        return True

    def _seed_starter_actions(self) -> bool:
        """Fill only untouched starter modes; never replace a user's action."""
        changed = False
        active_id = str(self.config.get("active_slot_preset") or "")
        for key, _name, _glyph, _color, _sites, _programs in STARTER_PRESETS:
            preset_id = f"starter-{key}"
            preset = self.config.get("slot_presets", {}).get(preset_id)
            if not isinstance(preset, dict) or preset.get("starter_actions_initialized"):
                continue
            existing = list(preset.get("slots") or [])
            if not any(isinstance(slot, dict) and (slot.get("macro") or slot.get("action"))
                       for slot in existing):
                slots, icons = starter_action_pack(key)
                preset["slots"] = slots
                preset_icons = preset.setdefault("custom_icons", {})
                for position, icon in icons.items():
                    preset_icons.setdefault(position, icon)
                if preset_id == active_id:
                    hotkeys = self.repository.load_hotkeys()
                    live_slots = list(hotkeys.get("slots") or [])
                    if not any(isinstance(slot, dict) and (slot.get("macro") or slot.get("action"))
                               for slot in live_slots):
                        hotkeys["slots"] = copy.deepcopy(slots)
                        self._save_preset_hotkeys(hotkeys)
                        self.custom_icons = copy.deepcopy(preset_icons)
            preset["starter_actions_initialized"] = True
            changed = True
        return changed

    def _repair_tetris_youtube_copy(self) -> bool:
        """Remove the exact accidental YouTube copy from the Tetris starter mode once."""
        if self.config.get("tetris_youtube_mix_cleanup_version"):
            return False
        presets = self.config.get("slot_presets") or {}
        tetris = presets.get("starter-jstris")
        youtube = presets.get("starter-youtube")
        if not isinstance(tetris, dict) or not isinstance(youtube, dict):
            return False
        tetris_slots = tetris.get("slots")
        youtube_slots = youtube.get("slots")
        icons = tetris.get("custom_icons")
        if (not isinstance(tetris_slots, list) or len(tetris_slots) < 3
                or tetris_slots != youtube_slots or not is_default_home_slot(tetris_slots[0])
                or tetris.get("pinned_slots")
                or not isinstance(icons, dict) or set(icons) - {"0", "1"}):
            return False
        active = self.config.get("active_slot_preset") == "starter-jstris"
        hotkeys = self.repository.load_hotkeys() if active else None
        if active and hotkeys.get("slots") != tetris_slots:
            return False

        # Preserve the exact pre-repair files before changing either preset or
        # hotkeys. A failed backup leaves the user's configuration untouched.
        backup_dir = self.repository.root / "deck_presets" / f"tetris-cleanup-backup-{datetime.now():%Y%m%d-%H%M%S%f}"
        try:
            backup_dir.mkdir(parents=True, exist_ok=False)
            shutil.copy2(self.config_path, backup_dir / self.config_path.name)
            shutil.copy2(self.repository.hotkeys_path, backup_dir / self.repository.hotkeys_path.name)
        except OSError as exc:
            self._tetris_repair_error = str(exc)
            return False

        home_slot = copy.deepcopy(tetris_slots[0])
        home_icon = copy.deepcopy(icons.get("0"))
        tetris["slots"] = [home_slot]
        tetris["custom_icons"] = {"0": home_icon} if isinstance(home_icon, dict) else {}
        if active:
            hotkeys["slots"] = [copy.deepcopy(home_slot)]
            self._save_preset_hotkeys(hotkeys)
            self.custom_icons = copy.deepcopy(tetris["custom_icons"])
        self.config["tetris_youtube_mix_cleanup_version"] = 1
        self._tetris_repair_backup = backup_dir
        return True

    def _upgrade_youtube_home_action(self) -> bool:
        """Change only the untouched starter Home action, preserving custom tiles."""
        preset = self.config.get("slot_presets", {}).get("starter-youtube")
        if not isinstance(preset, dict):
            return False
        slots = list(preset.get("slots") or [])
        changed = False
        for slot in slots:
            if not isinstance(slot, dict) or slot.get("macro") != "YouTube 홈":
                continue
            action = slot.get("action")
            if (isinstance(action, dict) and action.get("kind") == "open_target"
                    and action.get("label") == "YouTube 홈"
                    and action.get("target") == "https://www.youtube.com/"):
                slot["action"] = {"kind": "navigate_browser_tab", "label": "YouTube 홈",
                                  "url": "https://www.youtube.com/", "match": "domain"}
                changed = True
        if changed:
            preset["slots"] = slots
            if self.config.get("active_slot_preset") == "starter-youtube":
                hotkeys = self.repository.load_hotkeys()
                hotkeys["slots"] = copy.deepcopy(slots)
                self._save_preset_hotkeys(hotkeys)
        return changed

    def _seed_browser_shortcuts(self) -> bool:
        """Install the six default-mode Whale shortcuts without replacing user tiles."""
        if self.config.get("browser_shortcuts_version"):
            return False
        self._ensure_slot_presets()
        presets = self.config["slot_presets"]
        changed = False
        site_rules = self.config.setdefault("auto_preset_rules", [])
        for preset_id, name, glyph, color, site in (
            ("preset-1", "트레이딩", "↗", "#1976D2", "tradingview.com"),
            ("starter-jstris", "테트리스", "▦", "#7858D7", "jstris.jezevec10.com"),
            ("starter-chatgpt", "ChatGPT", "✦", "#16826C", "chatgpt.com"),
            ("starter-google", "구글", "G", "#4285F4", "google.com"),
        ):
            if preset_id not in presets and len(presets) < 12:
                preset = self._build_slot_preset(name)
                preset.update(slots=[], custom_icons={}, deck_page_count=1,
                              deck_page_names=["페이지 1"], pinned_slots={}, icon=glyph, color=color)
                presets[preset_id] = preset
                changed = True
            if preset_id in presets and not any(isinstance(rule, dict) and rule.get("site") == site
                                                for rule in site_rules):
                site_rules.append({"site": site, "preset_id": preset_id})
                changed = True
        default = presets.get("default")
        if not isinstance(default, dict) or any(preset_id not in presets for _, _, preset_id, _, _, _ in BROWSER_SHORTCUTS):
            return changed
        rows, cols = int(default.get("rows") or 3), int(default.get("cols") or 5)
        if rows != 3 or cols not in {5, 6}:
            return changed
        slots = list(default.get("slots") or [])
        protected = range(12, 15 if cols == 5 else 18)
        if any(index < len(slots) and isinstance(slots[index], dict)
               and (slots[index].get("macro") or slots[index].get("action"))
               for index in protected):
            return changed
        if any(str(index) in dict(default.get("pinned_slots") or {}) for index in protected):
            return changed
        icons = dict(default.get("custom_icons") or {})
        if any(str(index) in icons for index in protected):
            return changed
        if cols == 5:
            # Preserve page two exactly; its first three slots would otherwise land
            # in the new shortcut row after expanding the first page to 18 tiles.
            if len(slots) > 15:
                slots[15:15] = [{}, {}, {}]
            icons = {str(int(position) + (3 if int(position) >= 15 else 0)): icon
                     for position, icon in icons.items() if str(position).isdigit()}
            default["cols"] = 6
            default["rows"] = 3
        while len(slots) < 18:
            slots.append({})
        for offset, (label, url, preset_id, glyph, color, match) in enumerate(BROWSER_SHORTCUTS):
            slot, icon = browser_site_slot(label, url, preset_id, glyph, color, match)
            slots[12 + offset] = slot
            icons[str(12 + offset)] = icon
        default["slots"] = slots
        default["custom_icons"] = icons
        default.setdefault("style", {})["show_empty_slots"] = True
        self.config["browser_shortcuts_version"] = 1
        if str(self.config.get("active_slot_preset")) == "default":
            hotkeys = self.repository.load_hotkeys()
            hotkeys["slots"] = copy.deepcopy(slots)
            self._save_preset_hotkeys(hotkeys)
            self.custom_icons = copy.deepcopy(icons)
            self.rows, self.cols = 3, 6
            self.config["show_empty_slots"] = True
        return True

    def _install_home_tiles(self, *, sync_active: bool = True) -> bool:
        """Migrate saved presets without overwriting user slots or edited icons."""
        self._ensure_slot_presets()
        legacy_icons: Dict[str, str] = {}
        action_icons: Dict[tuple[str, ...], str] = {}
        for key, _name, _glyph, _color, _sites, _programs in STARTER_PRESETS:
            actions, old = starter_action_pack(key, legacy_icons=True)
            _, new = starter_action_pack(key)
            for position, old_icon in old.items():
                new_data = str(new[position].get("image_data") or "")
                legacy_icons[str(old_icon.get("image_data") or "")] = new_data
                action = actions[int(position)]["action"]
                action_icons[starter_icon_action_key(action)] = new_data

        active_id = str(self.config.get("active_slot_preset") or "default")
        active_slots_changed = False
        active_icons_changed = False
        changed = False
        for preset_id, preset in self.config["slot_presets"].items():
            if not isinstance(preset, dict):
                continue
            slots_changed = preset_id != "default" and insert_default_home_slot(preset)
            icons_changed = False
            slots = list(preset.get("slots") or [])
            for position, icon in dict(preset.get("custom_icons") or {}).items():
                if not isinstance(icon, dict) or icon.get("icon_source") != "starter_pack":
                    continue
                replacement = legacy_icons.get(str(icon.get("image_data") or ""))
                if not replacement:
                    try:
                        slot = slots[int(position)]
                        action = slot.get("action") if isinstance(slot, dict) else None
                        replacement = action_icons.get(starter_icon_action_key(action)) if isinstance(action, dict) else None
                    except (TypeError, ValueError, IndexError):
                        pass
                if replacement and replacement != icon.get("image_data"):
                    icon["image_data"] = replacement
                    icons_changed = True
            changed = changed or slots_changed or icons_changed
            if preset_id == active_id:
                active_slots_changed = slots_changed
                active_icons_changed = slots_changed or icons_changed

        if sync_active and (active_slots_changed or active_icons_changed):
            active = self.config["slot_presets"][active_id]
            if active_slots_changed:
                hotkeys = self.repository.load_hotkeys()
                hotkeys["slots"] = copy.deepcopy(active["slots"])
                hotkeys["deck_page_count"] = active["deck_page_count"]
                hotkeys["deck_page_names"] = copy.deepcopy(active["deck_page_names"])
                self._save_preset_hotkeys(hotkeys)
            self.custom_icons = copy.deepcopy(dict(active.get("custom_icons") or {}))
        return changed

    def _capture_active_slot_preset(self) -> None:
        if self._applying_slot_preset:
            return
        self._ensure_slot_presets()
        presets = self.config["slot_presets"]
        active_id = str(self.config.get("active_slot_preset"))
        current = presets.get(active_id, {})
        name = str(current.get("name") or active_id)
        updated = self._build_slot_preset(name)
        updated["pinned_slots"] = copy.deepcopy(dict(current.get("pinned_slots") or {}))
        updated["slot_layouts"] = copy.deepcopy(dict(current.get("slot_layouts") or {}))
        if current.get("starter_actions_initialized"):
            updated["starter_actions_initialized"] = True
        for visual_key in ("icon", "color"):
            if visual_key in current:
                updated[visual_key] = current[visual_key]
        presets[active_id] = updated

    def _active_pinned_slots(self) -> Dict[str, Any]:
        self._ensure_slot_presets()
        active_id = str(self.config.get("active_slot_preset") or "")
        return self.config["slot_presets"][active_id].setdefault("pinned_slots", {})

    def _remap_active_pins_for_grid(self, new_rows: int, new_cols: int) -> bool:
        if (new_rows, new_cols) == (self.rows, self.cols):
            return True
        pins = self._active_pinned_slots()
        remapped: Dict[str, Any] = {}
        for local, entry in pins.items():
            row, col = divmod(int(local), max(1, self.cols))
            if row >= new_rows or col >= new_cols:
                return False
            remapped[str(row * new_cols + col)] = entry
        slots = list(self.repository.load_hotkeys().get("slots") or [])
        new_size = max(1, new_rows * new_cols)
        for index, slot in enumerate(slots):
            if str(index % new_size) in remapped and (slot.get("macro") or slot.get("action")):
                return False
        pins.clear(); pins.update(remapped)
        return True

    def _effective_slot(self, slot_index: int, slots: List[Dict[str, Any]]) -> Dict[str, Any]:
        pin = self._active_pinned_slots().get(str(slot_index % max(1, self.rows * self.cols)))
        if isinstance(pin, dict) and isinstance(pin.get("slot"), dict):
            return pin["slot"]
        return slots[slot_index] if 0 <= slot_index < len(slots) else {}

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

    def _switch_slot_preset(self, preset_id: str, *, automatic: bool = False) -> None:
        if automatic and self._slot_layout_editing:
            return
        self._ensure_slot_presets()
        presets = self.config["slot_presets"]
        if preset_id not in presets:
            return
        if not automatic and bool(self.config.get("auto_preset_enabled")):
            self.config["auto_preset_manual_lock"] = True
            # The same browser process may regain focus after a Deck click.
            self._last_foreground_identity = ("", 0)
        if preset_id == str(self.config.get("active_slot_preset")):
            if not automatic:
                self._update_preset_badge()
                self._auto_config_save.start(700)
            return

        if self._slot_layout_editing:
            self._set_slot_layout_editing(False)
        if preset_id != "default":
            insert_default_home_slot(presets[preset_id])
        self._capture_active_slot_preset()
        preset = copy.deepcopy(presets[preset_id])
        old_layout = (
            self.rows, self.cols, self.config.get("tile_scale"),
            self.config.get("tile_gap"), self.config.get("show_empty_slots"),
        )
        self._applying_slot_preset = True
        try:
            if not automatic:
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
            # Older presets have no saved crop settings; do not inherit the
            # previous preset's empty-area behavior.
            for key, default in (("bottom_blank_space", 0), ("crop_trailing_empty_rows", False)):
                if key not in style:
                    self.config[key] = default
            self.config["active_slot_preset"] = preset_id
            self.current_page = 0
            self._apply_theme()
            new_layout = (
                self.rows, self.cols, self.config.get("tile_scale"),
                self.config.get("tile_gap"), self.config.get("show_empty_slots"),
            )
            if not (automatic and old_layout == new_layout and self._refresh_slots_fast()):
                self.refresh_slots()
            else:
                tile_side = self._saved_manual_tile_side() or self._tile_side_from_width(self.width())
                self.resize(self._window_size_for_tile_side(tile_side))
        finally:
            self._applying_slot_preset = False
        self._update_preset_badge()
        # A preset may contain megabytes of embedded icons. Persist after the
        # click finishes so JSON serialization cannot stall the visible switch.
        self._auto_config_save.start(700)

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
        self.main_layout = main_layout
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
        self.grid_layout.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)

        main_layout.addWidget(self.swipe_container, 1)
        self.edge_scroll = QtWidgets.QScrollArea(central)
        self.edge_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.edge_scroll.setWidgetResizable(False)
        self.edge_scroll.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self.edge_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.edge_scroll.hide()
        self.preset_badge = DeckPresetBadge(central)
        self.preset_badge.setObjectName("PresetBadge")
        self.preset_badge.setCursor(QtCore.Qt.PointingHandCursor)
        self.preset_badge.setFocusPolicy(QtCore.Qt.NoFocus)
        self.preset_badge.setFixedHeight(27)
        self.preset_badge.clicked.connect(lambda: self._switch_slot_preset("default"))
        self.layout_edit_button = DeckLayoutEditButton(central)
        self.layout_edit_button.setToolTip("슬롯 배치 편집 · 켠 뒤 슬롯을 원하는 자리로 드래그하세요")
        self.layout_edit_button.toggled.connect(self._set_slot_layout_editing)
        self.layout_edit_button.show()
        self.layout_drop_marker = QtWidgets.QFrame(self.swipe_container)
        self.layout_drop_marker.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.layout_drop_marker.setStyleSheet("background: rgba(56,189,248,35); border: 3px solid #38BDF8; border-radius: 8px;")
        self.layout_drop_marker.hide()
        self._update_preset_badge()
        self.preset_badge.show()
        self.preset_badge.raise_()
        self.resize_grip = DeckResizeGrip(self)
        self.resize_grip.setToolTip("드래그하여 덱 크기 조절")
        self.resize_grip.move(self.width() - self.resize_grip.width(), self.height() - self.resize_grip.height())
        self.resize_grip.show()
        self.resize_grip.raise_()
        self.bottom_padding_grip = DeckResizeGrip(self, "bottom_padding")
        self.bottom_padding_grip.setToolTip("드래그하여 슬롯 크기는 그대로 두고 아래 여백만 조절")
        self.bottom_padding_grip.move(0, self.height() - self.bottom_padding_grip.height())
        self.bottom_padding_grip.show()
        self.bottom_padding_grip.raise_()

        app = QtGui.QGuiApplication.instance()
        if app:
            app.screenAdded.connect(self._reflow_edge_panel)
            app.screenRemoved.connect(self._reflow_edge_panel)

    def _edge_panel_enabled(self) -> bool:
        return self.config.get("edge_panel_side") in EDGE_PANEL_SIDES

    def _edge_panel_screen(self):
        screens = QtGui.QGuiApplication.screens()
        name = str(self.config.get("edge_panel_screen") or "")
        screen = next((screen for screen in screens if screen.name() == name), None)
        if screen is None:
            screen = QtGui.QGuiApplication.screenAt(self.frameGeometry().center()) or self.screen()
        if screen is not self._edge_panel_screen_obj:
            if self._edge_panel_screen_obj is not None:
                try:
                    self._edge_panel_screen_obj.availableGeometryChanged.disconnect(self._reflow_edge_panel)
                except (RuntimeError, TypeError):
                    pass
            self._edge_panel_screen_obj = screen
            if screen:
                screen.availableGeometryChanged.connect(self._reflow_edge_panel)
        return screen

    def _reflow_edge_panel(self, *_args) -> None:
        if self._edge_panel_enabled():
            QtCore.QTimer.singleShot(0, self.refresh_slots)

    def _show_edge_panel_menu(self, global_pos: QtCore.QPoint) -> None:
        menu = self.edge_radial_menu
        menu.center_title = "자유 배치"
        menu.center_subtitle = "눌러서 고정 해제"
        menu.center_action_key = "edge:free"
        menu.center_secondary_action_key = "edge_size"
        menu.center_secondary_action_text = "슬롯 크기"
        current = str(self.config.get("edge_panel_side") or "free")
        menu.items = [RadialPieMenuItem(
            f"edge:{edge}", ("● " if current == edge else "") + title, glyph, "#38BDF8")
            for edge, title, glyph in (
                ("top", "화면 위", ""), ("right", "화면 오른쪽", ""),
                ("bottom", "화면 아래", ""), ("left", "화면 왼쪽", ""),
            )]
        menu.popup_at(global_pos, sticky=True)

    def _handle_edge_panel_action(self, key: str) -> None:
        if key == "edge_size":
            self._show_edge_panel_size_dialog()
        elif key.startswith("edge:"):
            self._set_edge_panel_mode(key.split(":", 1)[1])

    def _edge_panel_tile_size(self) -> int:
        try:
            value = float(self.config.get("edge_panel_tile_side") or self._saved_manual_tile_side() or 68)
            return max(EDGE_PANEL_MIN_TILE, min(EDGE_PANEL_MAX_TILE, round(value)))
        except (TypeError, ValueError, OverflowError):
            return 68

    def _set_edge_panel_tile_size(self, size: int) -> None:
        self.config["edge_panel_tile_side"] = max(EDGE_PANEL_MIN_TILE, min(EDGE_PANEL_MAX_TILE, int(size)))
        if self._edge_panel_enabled():
            self.refresh_slots()
        self._auto_config_save.start(300)

    def _show_edge_panel_size_dialog(self) -> None:
        dialog = EdgePanelSizeDialog(self._edge_panel_tile_size(), self)
        dialog.setStyleSheet(stylesheet())
        dialog.size_changed.connect(self._set_edge_panel_tile_size)
        position_dialog_beside(self, dialog)
        dialog.exec()

    def _set_edge_panel_mode(self, edge: str, *, screen=None) -> None:
        if edge not in EDGE_PANEL_SIDES | {"free"}:
            return
        was_docked = self._edge_panel_enabled()
        if edge != "free":
            if not was_docked:
                self.config["edge_panel_free_geometry"] = bytes(self.saveGeometry().toHex()).decode("ascii")
                self.config["edge_panel_free_position"] = [self.x(), self.y()]
                if not self.config.get("edge_panel_tile_side"):
                    self.config["edge_panel_tile_side"] = self._saved_manual_tile_side() or self._tile_side_from_width(self.width())
            screen = screen or self._edge_panel_screen()
            if screen:
                self.config["edge_panel_screen"] = screen.name()
        self.config["edge_panel_side"] = edge
        if edge == "free" and was_docked:
            saved = str(self.config.get("edge_panel_free_geometry") or "")
            if saved:
                self.restoreGeometry(QtCore.QByteArray.fromHex(saved.encode("ascii")))
            self._restored_window_geometry = False
        self.refresh_slots()
        if edge == "free" and was_docked:
            position = self.config.get("edge_panel_free_position")
            if isinstance(position, list) and len(position) == 2:
                point = QtCore.QPoint(int(position[0]), int(position[1]))
                screen = QtGui.QGuiApplication.screenAt(point) or self.screen()
                if screen:
                    available = screen.availableGeometry()
                    point.setX(max(available.left(), min(point.x(), available.right() - self.width() + 1)))
                    point.setY(max(available.top(), min(point.y(), available.bottom() - self.height() + 1)))
                self.move(point)
        self._auto_config_save.start(700)

    def _snap_edge_panel_to(self, global_pos: QtCore.QPoint) -> None:
        screens = QtGui.QGuiApplication.screens()
        screen = QtGui.QGuiApplication.screenAt(global_pos)
        if screen is None and screens:
            def distance(candidate):
                bounds = candidate.availableGeometry()
                dx = max(bounds.left() - global_pos.x(), 0, global_pos.x() - bounds.right())
                dy = max(bounds.top() - global_pos.y(), 0, global_pos.y() - bounds.bottom())
                return dx * dx + dy * dy
            screen = min(screens, key=distance)
        if screen is None:
            screen = self._edge_panel_screen()
        if screen:
            edge = nearest_screen_edge(global_pos, screen.availableGeometry(),
                                       str(self.config.get("edge_panel_side") or ""))
            self._set_edge_panel_mode(edge, screen=screen)

    def _set_edge_panel_container(self) -> None:
        docked = self._edge_panel_enabled()
        if docked:
            if self.edge_scroll.widget() is None:
                self.main_layout.removeWidget(self.swipe_container)
                self.edge_scroll.setWidget(self.swipe_container)
                self.main_layout.addWidget(self.edge_scroll, 1)
            self.main_layout.setContentsMargins(8, 8 + EDGE_PANEL_HEADER, 8, 8)
            self.edge_scroll.show()
        else:
            if self.edge_scroll.widget() is not None:
                self.edge_scroll.takeWidget()
                self.main_layout.removeWidget(self.edge_scroll)
                self.main_layout.addWidget(self.swipe_container, 1)
                self.swipe_container.setMinimumSize(0, 0)
                self.swipe_container.setMaximumSize(16777215, 16777215)
                self.swipe_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
                self.swipe_container.show()
            self.edge_scroll.hide()
            self.main_layout.setContentsMargins(8, 8, 8, 8)
        self.resize_grip.setVisible(not docked)
        self.bottom_padding_grip.setVisible(not docked)

    def _edge_panel_slots(self, slots: List[Dict[str, Any]]) -> list[tuple[int, Dict[str, Any]]]:
        pins = self._active_pinned_slots()
        page_size = max(1, self.rows * self.cols)
        highest_pin = max((int(key) for key in pins if str(key).isdigit()), default=-1)
        visible = []
        for index in range(max(len(slots), highest_pin + 1)):
            # A pinned tile repeats on ordinary pages; the combined panel
            # shows it once while retaining each action's original index.
            if index >= page_size and str(index % page_size) in pins:
                continue
            slot = self._effective_slot(index, slots)
            if deck_slot_has_content(slot):
                visible.append((index, slot))
        return visible

    def _slot_layout_scope(self) -> str:
        return f"edge:{self.config['edge_panel_side']}" if self._edge_panel_enabled() else f"page:{self.current_page}"

    def _active_slot_layout(self) -> Dict[str, Any]:
        preset_id = str(self.config.get("active_slot_preset") or "default")
        preset = self.config["slot_presets"][preset_id]
        layouts = preset.get("slot_layouts")
        if not isinstance(layouts, dict):
            layouts = preset["slot_layouts"] = {}
        entry = layouts.get(self._slot_layout_scope())
        return entry if isinstance(entry, dict) else {}

    def _set_slot_layout_editing(self, enabled: bool) -> None:
        self._slot_layout_editing = bool(enabled)
        self._layout_drag = None
        self._cancel_mouse_hold_check()
        self._drag_press_global = None
        self._is_dragging_window = False
        self.layout_drop_marker.hide()
        with QtCore.QSignalBlocker(self.layout_edit_button):
            self.layout_edit_button.setChecked(self._slot_layout_editing)
        self.layout_edit_button.setToolTip(
            "배치 편집 완료 · 체크를 누르면 슬롯 실행을 다시 사용할 수 있습니다. 변경은 자동 저장됩니다."
            if enabled else "슬롯 배치 편집 · 켠 뒤 슬롯을 원하는 자리로 드래그하세요")
        self._last_foreground_identity = ("", 0)
        self.refresh_slots()
        self._update_preset_badge()
        if not enabled:
            self._save_config()

    def _begin_slot_layout_drag(self, button, event) -> None:
        self._cancel_mouse_hold_check()
        self._layout_drag = {
            "slot": button.slot_index, "preset": self.config.get("active_slot_preset"),
            "scope": self._slot_layout_scope(), "start": event.globalPosition().toPoint(),
            "target": None, "moved": False,
        }

    def _move_slot_layout_drag(self, event) -> None:
        drag = self._layout_drag
        if not drag or not event.buttons() & QtCore.Qt.LeftButton:
            return
        if (event.globalPosition().toPoint() - drag["start"]).manhattanLength() < QtWidgets.QApplication.startDragDistance():
            return
        drag["moved"] = True
        local = self.swipe_container.mapFromGlobal(event.globalPosition().toPoint())
        drag["target"] = None
        for widget in [*self.buttons, *self._layout_placeholders]:
            if widget.geometry().contains(local):
                drag["target"] = widget.property("deck_layout_position")
                self.layout_drop_marker.setGeometry(widget.geometry())
                self.layout_drop_marker.show()
                self.layout_drop_marker.raise_()
                return
        self.layout_drop_marker.hide()

    def _finish_slot_layout_drag(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        drag, self._layout_drag = self._layout_drag, None
        self.layout_drop_marker.hide()
        if drag:
            local = self.swipe_container.mapFromGlobal(event.globalPosition().toPoint())
            drag["target"] = next((widget.property("deck_layout_position")
                                   for widget in [*self.buttons, *self._layout_placeholders]
                                   if widget.geometry().contains(local)), None)
        if (drag and drag["moved"] and drag["preset"] == self.config.get("active_slot_preset")
                and drag["scope"] == self._slot_layout_scope() and drag["target"] is not None):
            self._place_visual_slot(drag["slot"], int(drag["target"]))

    def _place_visual_slot(self, slot_index: int, target: int) -> bool:
        positions = dict(self._visual_slot_positions)
        source = positions.get(slot_index)
        if (not self._slot_layout_editing or source is None or source == target
                or not 0 <= target < self._visible_rows * self._visible_cols):
            return False
        other = next((index for index, position in positions.items() if position == target), None)
        positions[slot_index] = target
        if other is not None:
            positions[other] = source
        preset = self.config["slot_presets"][str(self.config.get("active_slot_preset") or "default")]
        layout = {"positions": {str(index): position for index, position in positions.items()}}
        if self._edge_panel_enabled():
            panel = self._edge_panel_layout
            layout["axis_span"] = panel.rows if panel.edge in {"left", "right"} else panel.cols
        preset.setdefault("slot_layouts", {})[self._slot_layout_scope()] = layout
        self._visual_slot_positions = positions
        self._save_config()
        # Do not delete/rebuild the source widget inside its release handler.
        QtCore.QTimer.singleShot(0, self.refresh_slots)
        return True

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        grip = getattr(self, "resize_grip", None)
        if grip is not None:
            grip.move(self.width() - grip.width(), self.height() - grip.height())
            grip.raise_()
        padding_grip = getattr(self, "bottom_padding_grip", None)
        if padding_grip is not None:
            padding_grip.move(0, self.height() - padding_grip.height())
            padding_grip.raise_()
        self._fit_buttons_to_width()
        self._update_preset_badge()

    def _position_preset_badge(self) -> None:
        badge = getattr(self, "preset_badge", None)
        central = self.centralWidget()
        if badge is None or central is None:
            return
        button = getattr(self, "layout_edit_button", None)
        extra = button.width() + 3 if button else 0
        start = max(2, (central.width() - badge.width() - extra) // 2)
        badge.move(start, 2)
        if button:
            button.move(start + badge.width() + 3, 2)
            button.raise_()
        badge.raise_()

    def _update_preset_badge(self) -> None:
        self._refresh_quick_action_labels()
        badge = getattr(self, "preset_badge", None)
        if badge is None:
            return
        self._ensure_slot_presets()
        preset_id = str(self.config.get("active_slot_preset") or "default")
        preset = dict(self.config["slot_presets"].get(preset_id) or {})
        icon, color = preset_visual(preset_id, preset)
        name = str(preset.get("name") or preset_id)
        if self.config.get("auto_preset_pinned"):
            suffix = " · 고정"
        elif self.config.get("auto_preset_enabled") and self._browser_bridge_error and self.config.get("auto_preset_rules"):
            suffix = " · 연결 오류"
        else:
            suffix = (" · 고정" if self.config.get("auto_preset_manual_lock") else " · 자동") if self.config.get("auto_preset_enabled") else ""
        full_text = f"⚠ {self._browser_action_notice}" if self._browser_action_notice else f"{icon}  {name}{suffix}"
        width_limit = max(32, self.width() - 36)
        font = badge.font()
        font.setPointSize(8)
        font.setBold(True)
        badge.setFont(font)
        display_text = QtGui.QFontMetrics(font).elidedText(full_text, QtCore.Qt.ElideRight, width_limit - 16)
        badge.setText(display_text)
        badge.setToolTip(
            f"현재 프리셋: {name} · 눌러서 홈으로 이동" +
            (" · 드래그하여 다른 화면 가장자리로 이동" if self._edge_panel_enabled() else "") +
            (f" · {self._browser_action_notice}" if self._browser_action_notice else "") +
            (f" · 브라우저 연결 실패: {self._browser_bridge_error}" if suffix == " · 연결 오류" else
             " (퀵액션에서 자동으로 전환할 때까지 고정)" if self.config.get("auto_preset_pinned") else
             " (수동 선택 중)" if suffix == " · 고정" else "")
        )
        badge.setFixedWidth(min(width_limit, max(72, QtGui.QFontMetrics(font).horizontalAdvance(display_text) + 20)))
        badge.setStyleSheet(
            f"QPushButton#PresetBadge {{ color: #F8FAFC; background: rgba(18, 24, 36, 220); "
            f"border: 1px solid {color}; border-radius: 10px; padding: 0 4px; }} "
            f"QPushButton#PresetBadge:hover {{ background: rgba(38, 49, 70, 235); }}"
        )
        self._position_preset_badge()

    def _show_browser_action_notice(self, message: str) -> None:
        self._browser_action_notice = str(message)
        self._browser_notice_sequence += 1
        sequence = self._browser_notice_sequence
        self._update_preset_badge()

        def clear() -> None:
            if self._browser_notice_sequence == sequence:
                self._browser_action_notice = ""
                self._update_preset_badge()

        QtCore.QTimer.singleShot(6000, clear)

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
            self._hold_triggered = False
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
        if event.button() != QtCore.Qt.LeftButton:
            return False
        was_dragging = self._is_dragging_window
        self._is_dragging_window = False
        self._drag_press_global = None
        self._drag_window_origin = None
        if was_dragging:
            self.unsetCursor()
            if self._edge_panel_enabled():
                self._snap_edge_panel_to(event.globalPosition().toPoint())
            else:
                self._save_config()
            return True
        return False

    def _get_resize_edge(self, pos: QtCore.QPoint) -> str:
        if self._edge_panel_enabled():
            return ""
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
        if edge == "bottom_padding":
            # Only the blank area changes. Keep the top edge, width and tile
            # side fixed, and never let the grip cover an occupied row.
            tile_side = self._tile_side_from_width(orig.width())
            minimum_height = self._size_for_tile_side(tile_side).height()
            self.resize(orig.width(), max(minimum_height, orig.height() + dy))
            return
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

        target = self._window_size_for_tile_side(max(MIN_TILE_SIDE, tile_side))
        new_x = orig.right() - target.width() + 1 if "left" in edge else orig.x()
        new_y = orig.bottom() - target.height() + 1 if "top" in edge else orig.y()
        self.setGeometry(new_x, new_y, target.width(), target.height())

        self._remember_manual_tile_side(tile_side)

    def _remember_manual_tile_side(self, tile_side: float) -> None:
        self.config["manual_tile_side"] = round(max(MIN_TILE_SIDE, float(tile_side)), 3)

    def _remember_bottom_blank_space(self) -> None:
        tile_side = self._tile_side_from_width(self.width())
        self._remember_manual_tile_side(tile_side)
        self.config["bottom_blank_space"] = max(0, self.height() - self._size_for_tile_side(tile_side).height())
        self._capture_active_slot_preset()

    def _prepare_bottom_padding_resize(self) -> None:
        """Release empty trailing grid rows before Qt enforces their height."""
        if not self.config.get("show_empty_slots") or self.config.get("crop_trailing_empty_rows"):
            return
        columns = max(1, self._visible_cols)
        last_occupied = max(
            (index for index, button in enumerate(self.buttons)
             if button.has_slot_content),
            default=-1,
        )
        needed_rows = max(1, last_occupied // columns + 1)
        if needed_rows >= self._visible_rows:
            return

        original_size = self.size()
        for button in self.buttons[needed_rows * columns:]:
            self.grid_layout.removeWidget(button)
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self.buttons = self.buttons[:needed_rows * columns]
        self._visible_rows = needed_rows
        self.config["crop_trailing_empty_rows"] = True
        for row in range(20):
            self.grid_layout.setRowStretch(row, 1 if row < needed_rows else 0)
        self.setMinimumSize(self._size_for_tile_side(MIN_TILE_SIDE))
        tile_side = self._tile_side_from_width(original_size.width())
        self.config["bottom_blank_space"] = max(
            0, original_size.height() - self._size_for_tile_side(tile_side).height())
        self.resize(original_size)

    def _crop_empty_page_tail(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.config.get("show_empty_slots") or not self.config.get("crop_trailing_empty_rows"):
            return entries
        last_occupied = max(
            (index for index, slot in enumerate(entries)
             if deck_slot_has_content(slot)),
            default=-1,
        )
        columns = max(1, self.cols)
        keep = max(columns, (last_occupied // columns + 1) * columns)
        return entries[:keep]

    def _bottom_blank_space(self) -> int:
        try:
            return max(0, int(self.config.get("bottom_blank_space") or 0))
        except (TypeError, ValueError):
            return 0

    def _saved_manual_tile_side(self) -> float | None:
        try:
            value = float(self.config.get("manual_tile_side") or 0)
        except (TypeError, ValueError):
            return None
        return max(MIN_TILE_SIDE, value) if value > 0 else None

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

    def _window_size_for_tile_side(self, tile_side: float) -> QtCore.QSize:
        content = self._size_for_tile_side(tile_side)
        return QtCore.QSize(content.width(), content.height() + self._bottom_blank_space())

    def _fit_buttons_to_width(self) -> None:
        if not hasattr(self, "buttons"):
            return
        panel = getattr(self, "_edge_panel_layout", None)
        tile_side = panel.tile_side if self._edge_panel_enabled() and panel else max(MIN_TILE_SIDE, round(self._tile_side_from_width(self.width())))
        for button in [*self.buttons, *self._layout_placeholders]:
            if button.width() != tile_side or button.height() != tile_side:
                button.setFixedSize(tile_side, tile_side)

    def _tile_side_from_width(self, width: int) -> float:
        extra_w, _ = self._grid_extras()
        return max(MIN_TILE_SIDE, (width - extra_w) / max(1, self._visible_cols))

    def _tile_side_from_height(self, height: int) -> float:
        _, extra_h = self._grid_extras()
        return max(MIN_TILE_SIDE, (height - extra_h - self._bottom_blank_space()) / max(1, self._visible_rows))

    def resize_grid_from_width(self, width: int) -> None:
        tile_side = self._tile_side_from_width(width)
        self._remember_manual_tile_side(tile_side)
        self.resize(self._window_size_for_tile_side(tile_side))

    def resize_grid_from_height(self, height: int) -> None:
        tile_side = self._tile_side_from_height(height)
        self._remember_manual_tile_side(tile_side)
        self.resize(self._window_size_for_tile_side(tile_side))

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
                *preset_visual(preset_id, preset),
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
        if self.config.get("auto_preset_enabled"):
            self.config["auto_preset_manual_lock"] = True
            self._last_foreground_identity = ("", 0)
        target_id = dialog.selected_preset_id()
        self.config["slot_presets"] = copy.deepcopy(dialog.presets)
        self._install_home_tiles(sync_active=False)
        for starter_id, sites, programs in dialog.starter_rules:
            if starter_id not in self.config["slot_presets"]:
                continue
            site_rules = self.config.setdefault("auto_preset_rules", [])
            program_rules = self.config.setdefault("auto_program_rules", [])
            for site in sites:
                if not any(isinstance(rule, dict) and str(rule.get("site") or "").casefold() == site
                           for rule in site_rules):
                    site_rules.append({"site": site, "preset_id": starter_id})
            for executable in programs:
                if not any(isinstance(rule, dict) and str(rule.get("exe") or "").casefold() == executable
                           for rule in program_rules):
                    program_rules.append({"exe": executable, "preset_id": starter_id})
        valid_ids = set(self.config["slot_presets"])
        for rule_key in ("auto_preset_rules", "auto_program_rules"):
            self.config[rule_key] = [rule for rule in self.config.get(rule_key, [])
                                     if isinstance(rule, dict) and rule.get("preset_id") in valid_ids]
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
        self._update_preset_badge()
        self._sync_browser_bridge()
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
        elif key == "preset_mode":
            self._toggle_preset_mode()
        elif key == "edge_panel":
            self._show_edge_panel_menu(QtGui.QCursor.pos())
        elif key == "auto_resume":
            self._resume_auto_preset()
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
        was_visible = self.isVisible()
        geometry = self.geometry()
        flags = QtCore.Qt.FramelessWindowHint | QtCore.Qt.Tool
        if self.always_on_top:
            flags |= QtCore.Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        # Changing flags hides/recreates the native window. Preserve the panel
        # and its monitor-edge placement when toggling topmost.
        self.setGeometry(geometry)
        if was_visible:
            self.show()

    def event(self, event: QtCore.QEvent) -> bool:
        if (event.type() == QtCore.QEvent.WindowActivate and
                getattr(self, "_browser_yielded_topmost", False)):
            self._browser_yielded_topmost = False
            if self.always_on_top and os.name == "nt":
                ctypes.windll.user32.SetWindowPos(
                    int(self.winId()), -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        return super().event(event)

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
        pinned_positions = {int(local) for local in self._active_pinned_slots()}
        page_size = max(1, self.rows * self.cols)
        empty_index = next((i for i, slot in enumerate(slots)
                            if i % page_size not in pinned_positions and not str(slot.get("macro") or "").strip()), None)
        if empty_index is None:
            while len(slots) % page_size in pinned_positions:
                slots.append({"macro": "", "hotkey": "", "mode": "hybrid"})
            slots.append(new_slot)
            inserted_index = len(slots) - 1
        else:
            slots[empty_index] = new_slot
            inserted_index = empty_index
        payload["slots"] = slots
        self._save_preset_hotkeys(payload)
        self.current_page = max(0, inserted_index // max(1, self.rows * self.cols))
        self.refresh_slots()
        self._capture_active_slot_preset()
        self._save_config()

    def _edit_slot_icon(self, slot_idx: int) -> None:
        if slot_idx < 0:
            # Reset icon request
            real_idx = -1 - slot_idx
            pin = self._active_pinned_slots().get(str(real_idx % max(1, self.rows * self.cols)))
            if isinstance(pin, dict):
                pin["icon"] = {}
            else:
                self.custom_icons.pop(str(real_idx), None)
            self._capture_active_slot_preset()
            self._save_config()
            self.refresh_slots()
            return

        hotkeys_data = self.repository.load_hotkeys()
        slots = list(hotkeys_data.get("slots") or [])
        macro_name = ""
        if slot_idx >= 0:
            macro_name = str(self._effective_slot(slot_idx, slots).get("macro") or "").strip()

        pin = self._active_pinned_slots().get(str(slot_idx % max(1, self.rows * self.cols)))
        current_cfg = pin.get("icon", {}) if isinstance(pin, dict) else self.custom_icons.get(str(slot_idx), {})
        dlg = SlotIconEditDialog(slot_idx, macro_name, current_cfg, self)
        dlg.live_config_changed.connect(self._preview_slot_icon)
        position_dialog_beside(self, dlg)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            new_cfg = dlg.get_config()
            if isinstance(pin, dict):
                pin["icon"] = new_cfg
            else:
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
        pin_key = str(slot_idx % max(1, self.rows * self.cols))
        if pin_key in self._active_pinned_slots():
            self._active_pinned_slots().pop(pin_key, None)
            self._capture_active_slot_preset()
            self._save_config()
            self.refresh_slots()
            return
        if not (0 <= slot_idx < len(slots)):
            return
        macro_name = str(slots[slot_idx].get("macro") or "").strip()
        if macro_name and self._is_macro_running(macro_name):
            self._stop_slot_macro(slot_idx, macro_name)
        slots[slot_idx] = {"macro": "", "hotkey": "", "mode": "hybrid"}
        payload["slots"] = slots
        self._save_preset_hotkeys(payload)
        self.custom_icons.pop(str(slot_idx), None)
        self._capture_active_slot_preset()
        self._save_config()
        self.refresh_slots()

    def _refresh_slots_fast(self) -> bool:
        """Reuse visible tile widgets when auto-switching between equal grids."""
        if self._edge_panel_enabled() or self._slot_layout_editing or self._active_slot_layout().get("positions"):
            return False
        if not self.buttons:
            return False
        hotkeys = self.repository.load_hotkeys()
        slots = list(hotkeys.get("slots") or [])
        page_size = max(1, self.rows * self.cols)
        page_slots = list(slots[:page_size])
        page_slots.extend({"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(max(0, page_size - len(page_slots))))
        pins = self._active_pinned_slots()
        for local, entry in pins.items():
            try:
                position = int(local)
            except (TypeError, ValueError):
                continue
            if 0 <= position < page_size and isinstance(entry, dict) and isinstance(entry.get("slot"), dict):
                page_slots[position] = entry["slot"]
        page_slots = self._crop_empty_page_tail(page_slots)
        visible = [(index, slot) for index, slot in enumerate(page_slots)
                   if self.config.get("show_empty_slots") or deck_slot_has_content(slot)]
        visible_cols = min(self.cols, max(1, len(visible)))
        if (len(visible) != len(self.buttons) or visible_cols != self._visible_cols
                or any(button.slot_index != index for button, (index, _slot) in zip(self.buttons, visible))):
            return False
        total_pages = max(1, int(hotkeys.get("deck_page_count") or 1), math.ceil(len(slots) / page_size))
        if hasattr(self, "page_label") and self.page_label:
            self.page_label.setText(f"1/{total_pages}")
        if hasattr(self, "btn_prev_page") and self.btn_prev_page:
            self.btn_prev_page.setEnabled(False)
        if hasattr(self, "btn_next_page") and self.btn_next_page:
            self.btn_next_page.setEnabled(total_pages > 1)
        if hasattr(self, "dots_label") and self.dots_label:
            self.dots_label.setText("   ".join(
                "<span style='color: #1E90FF; font-size: 11pt;'>●</span>" if page == 0
                else "<span style='color: #2B364A; font-size: 9pt;'>●</span>"
                for page in range(min(total_pages, 5))
            ))
        self.grid_layout.setSpacing(int(self.config.get("tile_gap", 10)))
        for button, (index, slot) in zip(self.buttons, visible):
            action = slot.get("action") if isinstance(slot.get("action"), dict) else None
            pin = pins.get(str(index))
            icon = dict((pin.get("icon") if isinstance(pin, dict) else None)
                        or self.custom_icons.get(str(index), {}) or {})
            if deck_action_needs_program_icon(action, icon) or manual_executable_icon_needs_upgrade(icon):
                return False
            button.set_custom_icon_config(icon)
            macro_name = str(slot.get("macro") or "").strip()
            button.set_slot_data(
                macro_name, str(slot.get("hotkey") or ""), str(slot.get("mode") or "hybrid"),
                self._is_macro_running(macro_name),
                display_title=str(action.get("label") or "") if action else macro_name,
            )
            button.action_kind = str(action.get("kind") or "") if action else ""
            button.has_slot_content = deck_slot_has_content(slot)
            button.set_feedback(self._slot_feedback.get(self._feedback_key(index), ""))
        self.refresh_states()
        return True

    def refresh_slots(self) -> None:
        self._set_edge_panel_container()
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
        self._layout_placeholders.clear()
        self.layout_drop_marker.hide()

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
        current_page_slots.extend(
            {"macro": "", "hotkey": "", "mode": "hybrid"}
            for _ in range(max(0, pageSize - len(current_page_slots)))
        )
        pinned = self._active_pinned_slots()
        for local, entry in pinned.items():
            try:
                position = int(local)
            except (TypeError, ValueError):
                continue
            if 0 <= position < pageSize and isinstance(entry, dict) and isinstance(entry.get("slot"), dict):
                current_page_slots[position] = entry["slot"]
        panel = None
        saved_layout = self._active_slot_layout()
        if self._edge_panel_enabled():
            page_slots = self._edge_panel_slots(slots)
            screen = self._edge_panel_screen()
            available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1920, 1080)
        elif show_empty_slots:
            current_page_slots = self._crop_empty_page_tail(current_page_slots)
            page_slots = [(start_idx + offset, slot) for offset, slot in enumerate(current_page_slots)]
        else:
            page_slots = [
                (index, slot)
                for index, slot in enumerate(current_page_slots, start=start_idx)
                if deck_slot_has_content(slot)
            ]

        positions = slot_visual_positions([index for index, _ in page_slots], saved_layout.get("positions"))
        visual_cell_count = max(positions.values(), default=0) + 1
        self._visual_slot_positions = positions
        if self._edge_panel_enabled():
            span = saved_layout.get("axis_span")
            span = span if isinstance(span, int) and 0 < span < 4096 else None
            panel = edge_panel_layout(self.config["edge_panel_side"], visual_cell_count, available,
                                      self._edge_panel_tile_size(), gap, axis_span=span)
            if self._slot_layout_editing:
                axis = panel.rows if panel.edge in {"left", "right"} else panel.cols
                panel = edge_panel_layout(panel.edge, panel.rows * panel.cols + axis, available,
                                          panel.tile_side, gap, axis_span=axis)
        self._edge_panel_layout = panel
        visible_cols = panel.cols if panel else (self.cols if self._slot_layout_editing else min(self.cols, visual_cell_count))
        visible_rows = panel.rows if panel else max(1, math.ceil(visual_cell_count / visible_cols))
        if self._slot_layout_editing and not panel:
            visible_rows = max(self.rows, visible_rows)
        self._visible_cols = visible_cols
        self._visible_rows = visible_rows
        self.setMinimumSize(QtCore.QSize(64, 64) if panel else self._size_for_tile_side(MIN_TILE_SIDE))
        for r in range(max(20, visible_rows, self.grid_layout.rowCount())):
            self.grid_layout.setRowStretch(r, 1 if r < visible_rows else 0)
        for c in range(max(20, visible_cols, self.grid_layout.columnCount())):
            self.grid_layout.setColumnStretch(c, 1 if c < visible_cols else 0)

        repaired_program_icons = False
        for i, (slot_idx, slot_info) in enumerate(page_slots):
            btn = StreamDeckButton(slot_idx, self.swipe_container)
            btn.setMinimumSize(MIN_TILE_SIDE, MIN_TILE_SIDE)

            macro_name = str(slot_info.get("macro") or "").strip()
            action = slot_info.get("action") if isinstance(slot_info.get("action"), dict) else None
            pin = pinned.get(str(slot_idx % pageSize))
            icon_cfg = dict(
                (pin.get("icon") if isinstance(pin, dict) else None)
                or self.custom_icons.get(str(slot_idx), {}) or {}
            )
            upgraded_icon = upgrade_manual_executable_icon(icon_cfg)
            if upgraded_icon is not icon_cfg:
                icon_cfg = upgraded_icon
                if isinstance(pin, dict):
                    pin["icon"] = upgraded_icon
                else:
                    self.custom_icons[str(slot_idx)] = upgraded_icon
                repaired_program_icons = True
            if deck_action_needs_program_icon(action, icon_cfg):
                extracted = deck_action_program_icon(self.repository, action)
                if extracted:
                    icon_cfg = extracted
                    if isinstance(pin, dict):
                        pin["icon"] = extracted
                    else:
                        self.custom_icons[str(slot_idx)] = extracted
                    repaired_program_icons = True

            # Apply custom icon config if present
            btn.set_custom_icon_config(icon_cfg)

            display_title = str(action.get("label") or "") if action is not None else macro_name
            hotkey = str(slot_info.get("hotkey") or "").strip()
            mode = str(slot_info.get("mode") or "hybrid")
            is_running = self._is_macro_running(macro_name)
            btn.set_slot_data(macro_name, hotkey, mode, is_running, display_title=display_title)
            btn.action_kind = str(action.get("kind") or "") if action else ""
            btn.has_slot_content = deck_slot_has_content(slot_info)
            btn.setProperty("deck_layout_position", positions[slot_idx])
            if self._slot_layout_editing:
                btn.setCursor(QtCore.Qt.OpenHandCursor)
                btn.setToolTip("배치 편집 · 다른 슬롯 위로 드래그하면 자리 교환, 빈 칸에는 이동합니다.")
            btn.set_feedback(self._slot_feedback.get(self._feedback_key(slot_idx), ""))

            btn.slot_triggered.connect(self._run_slot_macro)
            btn.slot_stopped.connect(self._stop_slot_macro)
            btn.edit_icon_requested.connect(self._edit_slot_icon)
            btn.remove_slot_requested.connect(self._remove_slot)

            row, col = panel.cell(positions[slot_idx]) if panel else divmod(positions[slot_idx], visible_cols)
            self.grid_layout.addWidget(btn, row, col)
            btn.show()
            self.buttons.append(btn)
        if self._slot_layout_editing:
            occupied = set(positions.values())
            for position in range(visible_rows * visible_cols):
                if position in occupied:
                    continue
                placeholder = QtWidgets.QFrame(self.swipe_container)
                placeholder.setProperty("deck_layout_position", position)
                placeholder.setToolTip("여기로 슬롯을 드래그하여 배치")
                placeholder.setStyleSheet("background: rgba(56,189,248,12); border: 1px dashed #5485A3; border-radius: 8px;")
                placeholder.setMinimumSize(MIN_TILE_SIDE, MIN_TILE_SIDE)
                row, col = panel.cell(position) if panel else divmod(position, visible_cols)
                self.grid_layout.addWidget(placeholder, row, col)
                placeholder.show()
                self._layout_placeholders.append(placeholder)
        if repaired_program_icons:
            self._capture_active_slot_preset()
            self._save_config()
        self.refresh_states()

        # Dynamic Auto-Fit Window Size according to configured active slots
        manual_tile_side = self._saved_manual_tile_side()
        if panel:
            self.swipe_container.setFixedSize(panel.content_size)
            if not self._is_dragging_window:
                self.setGeometry(panel.geometry)
            self.edge_scroll.horizontalScrollBar().setValue(
                self.edge_scroll.horizontalScrollBar().maximum() if panel.edge == "right" else 0)
            self.edge_scroll.verticalScrollBar().setValue(
                self.edge_scroll.verticalScrollBar().maximum() if panel.edge == "bottom" else 0)
        elif manual_tile_side is not None:
            self.resize(self._window_size_for_tile_side(manual_tile_side))
        elif self._restored_window_geometry:
            # Existing configs already carry the size chosen by the user.
            # Adopt that size instead of overwriting it during first refresh.
            side_from_width = self._tile_side_from_width(self.width())
            side_from_height = self._tile_side_from_height(self.height())
            self._remember_manual_tile_side(min(side_from_width, side_from_height))
            self._restored_window_geometry = False
            self._save_config()
        elif bool(self.config.get("compact_auto_fit", True)):
            active_count = visual_cell_count if saved_layout.get("positions") or self._slot_layout_editing else len(page_slots)
            if active_count > 0:
                fit_cols = visible_cols
                fit_rows = visible_rows

                self._visible_cols = fit_cols
                self._visible_rows = fit_rows
                tile_scale = float(self.config.get("tile_scale", 1.0))
                tile_side = max(MIN_TILE_SIDE, BASE_TILE_SIDE * tile_scale)
                self.setMinimumSize(self._size_for_tile_side(MIN_TILE_SIDE))
                self.resize(self._window_size_for_tile_side(tile_side))
            else:
                self._visible_cols = 1
                self._visible_rows = 1
                self.setMinimumSize(self._size_for_tile_side(MIN_TILE_SIDE))
                self.resize(self._window_size_for_tile_side(BASE_TILE_SIDE * float(self.config.get("tile_scale", 1.0))))
        self._fit_buttons_to_width()
        # A second touch-drag can start immediately after a drop. Assign real
        # cell geometry now instead of leaving every rebuilt tile at (0, 0)
        # until a later layout event.
        self.grid_layout.invalidate()
        self.grid_layout.activate()
        if hasattr(self, "preset_radial_menu"):
            self._refresh_preset_radial_menu()

    def _prev_page(self) -> None:
        if self._edge_panel_enabled():
            return
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_slots()

    def _next_page(self) -> None:
        if self._edge_panel_enabled():
            return
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

    def _feedback_key(self, slot_index: int, preset_id: str = "") -> tuple[str, int]:
        preset_id = preset_id or str(self.config.get("active_slot_preset") or "")
        preset = self.config.get("slot_presets", {}).get(preset_id, {})
        page_size = max(1, int(preset.get("rows") or self.rows) * int(preset.get("cols") or self.cols))
        local = slot_index % page_size
        if str(local) in dict(preset.get("pinned_slots") or {}):
            return preset_id, local
        return preset_id, slot_index

    def _mark_slot_status(self, slot_index: int, state: str, detail: str = "", *, preset_id: str = "") -> None:
        if slot_index < 0:
            return
        key = self._feedback_key(slot_index, preset_id)
        self._slot_feedback[key] = state
        sequence = self._feedback_sequence.get(key, 0) + 1
        self._feedback_sequence[key] = sequence
        self.execution_history.append({
            "time": QtCore.QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss"),
            "preset": key[0], "slot": str(slot_index + 1), "state": state,
            "detail": str(detail)[:300],
        })
        self.execution_history = self.execution_history[-100:]
        for button in self.buttons:
            if self._feedback_key(button.slot_index) == key:
                button.set_feedback(state)
                button.setToolTip(detail)

        def clear() -> None:
            if self._feedback_sequence.get(key) != sequence:
                return
            self._slot_feedback.pop(key, None)
            for button in self.buttons:
                if self._feedback_key(button.slot_index) == key:
                    button.set_feedback("")

        if state != "started":
            QtCore.QTimer.singleShot(1800, clear)

    def _accept_slot_tap(self, global_pos: QtCore.QPoint, *, preset_switch: bool = False) -> bool:
        """Debounce repeated actions, but never block a deliberate preset switch."""
        now = time.monotonic()
        if (not preset_switch
                and now - self._last_slot_tap_at < SLOT_ACCIDENTAL_REPEAT_MS / 1000.0
                and (global_pos - self._last_slot_tap_pos).manhattanLength() < 32):
            return False
        self._last_slot_tap_at = now
        self._last_slot_tap_pos = QtCore.QPoint(global_pos)
        return True

    def _run_slot_macro(self, slot_index: int, macro_name: str) -> None:
        if not macro_name:
            return
        slots = list(self.repository.load_hotkeys().get("slots") or [])
        if slot_index >= 0:
            action = self._effective_slot(slot_index, slots).get("action")
            if isinstance(action, dict) and action.get("kind"):
                self._execute_deck_action(action, slot_index=slot_index)
                return
        try:
            proc = self.repository.run_macro(macro_name)
            self.active_processes[int(proc.pid)] = (macro_name, proc)
            self._process_slot_map[int(proc.pid)] = (str(self.config.get("active_slot_preset") or ""), slot_index)
            self._mark_slot_status(slot_index, "started", f"{macro_name} 실행 중")
            if hasattr(self, "status_title") and self.status_title:
                self.status_title.setText("▶ 실행 중")
                self.status_title.setStyleSheet("font-size: 10pt; font-weight: 700; color: #26D07C;")
            if hasattr(self, "status_sub") and self.status_sub:
                self.status_sub.setText(f"'{macro_name}' 실행 중...")
            self.refresh_states()
        except Exception as exc:
            self._mark_slot_status(slot_index, "failed", str(exc))
            self._set_deck_status("실행 실패", str(exc), error=True)

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
        if user32.IsIconic(matches[0]):
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
        candidates: list[tuple[int, str, str]] = []
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
            target_class = str(action.get("target_class") or "").strip()
            if target_class:
                class_buffer = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
                if class_buffer.value.casefold() != target_class.casefold():
                    return True
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(max(1, length + 1))
            if length:
                user32.GetWindowTextW(hwnd, buffer, length + 1)
            candidates.append((int(hwnd), buffer.value.casefold(), process_name(hwnd)))
            return True

        if title_fragment or exe_name:
            user32.EnumWindows(callback_type(enum_callback), 0)
        matched = self._select_action_window_candidate(
            candidates,
            title_fragment=title_fragment,
            exe_name=exe_name,
            foreground_hwnd=int(user32.GetForegroundWindow() or 0),
        )
        if matched:
            return matched
        if token or title_fragment or exe_name:
            raise ValueError(f"대상 창을 찾지 못했습니다: {action.get('target_exe') or action.get('target_title') or token}")
        return int(user32.GetForegroundWindow() or 0)

    @staticmethod
    def _select_action_window_candidate(
        candidates: list[tuple[int, str, str]],
        *,
        title_fragment: str,
        exe_name: str,
        foreground_hwnd: int = 0,
    ) -> int:
        """Prefer an exact title match, then fall back to the same program.

        Browser tab titles change frequently.  The saved executable therefore
        remains the stable identity while the title is only a preference.  If
        several windows belong to that executable, the foreground one wins.
        """
        title_fragment = str(title_fragment or "").strip().casefold()
        exe_name = str(exe_name or "").strip().casefold()

        def preferred(pool: list[tuple[int, str, str]]) -> int:
            if not pool:
                return 0
            foreground = next((hwnd for hwnd, _title, _exe in pool if hwnd == foreground_hwnd), 0)
            return foreground or pool[0][0]

        exact = [
            candidate
            for candidate in candidates
            if (not title_fragment or title_fragment in candidate[1])
            and (not exe_name or candidate[2] == exe_name)
        ]
        if exact:
            return preferred(exact)

        if exe_name:
            same_program = [candidate for candidate in candidates if candidate[2] == exe_name]
            if same_program:
                return preferred(same_program)

        if title_fragment:
            same_title = [candidate for candidate in candidates if title_fragment in candidate[1]]
            if same_title:
                return preferred(same_title)
        return 0

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
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            QtCore.QThread.msleep(80)
        return hwnd

    def _open_whale_tab_if_running(self) -> bool:
        """Reuse an existing browser window; never send Ctrl+T to another app."""
        try:
            hwnd = self._resolve_action_window({
                "target_exe": "whale.exe", "target_class": "Chrome_WidgetWin_1",
            })
        except ValueError:
            return False
        user32 = ctypes.windll.user32
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        QtCore.QThread.msleep(80)
        if int(user32.GetForegroundWindow() or 0) != hwnd:
            raise RuntimeError("열린 웨일 창을 활성화하지 못했습니다. 새 창은 열지 않았습니다.")
        self._send_windows_hotkey("Ctrl+T")
        return True

    def _find_whale_window(self) -> int:
        """Find a running Whale window even when process-image lookup is denied."""
        return self._find_browser_window("whale")

    def _find_browser_window(self, browser: str) -> int:
        targets = {"whale": ("whale.exe", "- Whale"),
                   "edge": ("msedge.exe", "- Microsoft Edge"),
                   "chrome": ("chrome.exe", "- Google Chrome")}
        if browser not in targets:
            raise ValueError("지원하지 않는 브라우저입니다.")
        executable, title = targets[browser]
        try:
            return self._resolve_action_window({
                "target_exe": executable, "target_class": "Chrome_WidgetWin_1",
            })
        except ValueError:
            try:
                return self._resolve_action_window({
                    "target_title": title, "target_class": "Chrome_WidgetWin_1",
                })
            except ValueError:
                return 0

    def _focus_whale_for_shortcut(self, hwnd: int) -> bool:
        """Raise Whale without blocking the Deck UI; the extension selects the tab."""
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return int(user32.GetForegroundWindow() or 0) == hwnd

    def _yield_browser_foreground(self, hwnd: int) -> None:
        """Let the selected browser appear above the always-on-top Deck."""
        if os.name != "nt" or not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
            return
        user32 = ctypes.windll.user32
        if self.always_on_top:
            user32.SetWindowPos(int(self.winId()), -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            self._browser_yielded_topmost = True
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)

    @staticmethod
    def _whale_executable() -> str:
        found = shutil.which("whale.exe")
        if found:
            return found
        if winreg is not None:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\whale.exe") as key:
                        path, _ = winreg.QueryValueEx(key, "")
                    if Path(path).is_file():
                        return path
                except (OSError, ValueError):
                    pass
        for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES"),
                     os.environ.get("PROGRAMFILES(X86)")):
            if base:
                path = Path(base) / "Naver" / "Naver Whale" / "Application" / "whale.exe"
                if path.is_file():
                    return str(path)
        return ""

    @staticmethod
    def _browser_executable(browser: str) -> str:
        if browser == "whale":
            return QuickSlotDeckWindow._whale_executable()
        names = {"edge": ("msedge.exe", "Microsoft", "Edge"),
                 "chrome": ("chrome.exe", "Google", "Chrome")}
        if browser not in names:
            raise ValueError("지원하지 않는 브라우저입니다.")
        executable, vendor, product = names[browser]
        found = shutil.which(executable)
        if found:
            return found
        if winreg is not None:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{executable}") as key:
                        path, _ = winreg.QueryValueEx(key, "")
                    if Path(path).is_file():
                        return path
                except (OSError, ValueError):
                    pass
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                path = Path(base) / vendor / product / "Application" / executable
                if path.is_file():
                    return str(path)
        return ""

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
        user32 = ctypes.windll.user32
        encoded = text.encode("utf-16-le")
        for offset in range(0, len(encoded), 2):
            code_unit = int.from_bytes(encoded[offset:offset + 2], "little")
            events = (_INPUT * 2)(
                _INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(0, code_unit, 0x0004, 0, 0))),
                _INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(0, code_unit, 0x0004 | 0x0002, 0, 0))),
            )
            sent = int(user32.SendInput(2, events, ctypes.sizeof(_INPUT)))
            if sent != 2:
                error_code = int(ctypes.windll.kernel32.GetLastError())
                raise RuntimeError(f"Windows 키 입력 전송에 실패했습니다. (오류 코드: {error_code})")
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

    def _start_registered_macro(self, macro_name: str, slot_index: int = -1, *, entry_name: str = "") -> subprocess.Popen[Any]:
        proc = (self.repository.run_macro_entry(macro_name, entry_name)
                if entry_name else self.repository.run_macro(macro_name))
        self.active_processes[int(proc.pid)] = (macro_name, proc)
        if slot_index >= 0:
            self._process_slot_map[int(proc.pid)] = (str(self.config.get("active_slot_preset") or ""), slot_index)
        return proc

    def _run_macro_batch(self, action: Dict[str, Any], slot_index: int = -1) -> None:
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
                    if slot_index >= 0:
                        self._start_registered_macro(name, slot_index)
                    else:
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
                proc = self._start_registered_macro(names[index], slot_index) if slot_index >= 0 else self._start_registered_macro(names[index])
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

    def _execute_deck_action(self, action: Dict[str, Any], *, slot_index: int = -1) -> None:
        kind = str(action.get("kind") or "")
        label = str(action.get("label") or kind)
        source_preset = str(self.config.get("active_slot_preset") or "")
        try:
            if kind in {"activate_browser_tab", "navigate_browser_tab"}:
                url = str(action.get("url") or "")
                browser = str(action.get("browser") or "whale").strip().casefold()
                browser_name = {"whale": "웨일", "edge": "엣지", "chrome": "크롬"}.get(browser)
                if not browser_name:
                    raise ValueError("지원하지 않는 브라우저입니다.")
                preset_id = str(action.get("preset_id") or source_preset)
                if preset_id not in self.config.get("slot_presets", {}):
                    raise ValueError("연결된 프리셋을 찾을 수 없습니다.")
                hwnd = self._find_whale_window() if browser == "whale" else self._find_browser_window(browser)
                if hwnd:
                    self._sync_browser_bridge()
                    if self._browser_bridge is None:
                        raise RuntimeError(f"{browser_name} 탭 연결을 열지 못했습니다. 확장앱 설정을 확인해 주세요.")
                    match = str(action.get("match") or "domain")
                    command_id = (self._browser_bridge.request_navigation(url, match, browser=browser)
                                  if kind == "navigate_browser_tab"
                                  else self._browser_bridge.request_tab(url, match, browser=browser))
                    requested_at = time.monotonic()
                    self._pending_browser_actions[command_id] = (
                        preset_id, slot_index, label, requested_at + 10.0)
                    self._browser_action_requested_at[command_id] = requested_at
                    self._browser_action_hwnds[command_id] = hwnd
                    self._browser_action_names[command_id] = browser_name
                    self._browser_action_notice = ""
                    self._update_preset_badge()
                    self._focus_whale_for_shortcut(hwnd)
                    self._set_deck_status("브라우저 탭 확인 중", label)
                    self._mark_slot_status(slot_index, "started", label, preset_id=source_preset)
                else:
                    if kind == "navigate_browser_tab":
                        raise RuntimeError(f"실행 중인 {browser_name} 창을 찾지 못했습니다. 새 탭은 열지 않았습니다.")
                    executable = self._browser_executable(browser)
                    if not executable:
                        raise RuntimeError(f"{browser_name} 실행 파일을 찾을 수 없습니다.")
                    arguments = [executable]
                    if browser == "whale":
                        arguments.append("--silent-debugger-extension-api")
                    subprocess.Popen([*arguments, url])
                    self._switch_slot_preset(preset_id, automatic=True)
                    self._set_deck_status("브라우저 실행", label)
                    self._mark_slot_status(slot_index, "completed", label, preset_id=source_preset)
                return
            if kind == "open_target":
                target = str(action.get("target") or "").strip()
                if not target:
                    raise ValueError("열기 대상이 비어 있습니다.")
                whale_executable = is_plain_whale_launch(target)
                if target.lower().startswith(("http://", "https://")):
                    webbrowser.open(target)
                elif whale_executable and self._open_whale_tab_if_running():
                    pass
                elif whale_executable:
                    executable = target[1:-1] if target.startswith('"') and target.endswith('"') else target
                    subprocess.Popen([executable, "--silent-debugger-extension-api"])
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
                self._mark_slot_status(slot_index, "started", f"{wait_ms}ms 대기")
                def finish_wait() -> None:
                    self._set_deck_status("준비 완료", "Deck 액션 대기 완료")
                    self._mark_slot_status(slot_index, "completed", "대기 완료", preset_id=source_preset)
                QtCore.QTimer.singleShot(wait_ms, finish_wait)
                return
            elif kind == "studio":
                self._open_studio()
            elif kind == "stop_all":
                self.stop_all_macros()
            elif kind == "run_macro":
                macro_name = str(action.get("macro") or "").strip()
                if not macro_name:
                    raise ValueError("실행할 매크로가 선택되지 않았습니다.")
                entry_name = str(action.get("entry_name") or "").strip()
                if slot_index >= 0:
                    self._start_registered_macro(macro_name, slot_index, entry_name=entry_name)
                else:
                    self._start_registered_macro(macro_name, entry_name=entry_name)
            elif kind == "multi_macros":
                self._run_macro_batch(action, slot_index)
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
            self._mark_slot_status(slot_index, "started" if kind in {"run_macro", "multi_macros"} else "completed", label, preset_id=source_preset)
            self.refresh_states()
        except Exception as exc:
            self._set_deck_status("Deck 액션 실패", str(exc), error=True)
            self._mark_slot_status(slot_index, "failed", str(exc), preset_id=source_preset)

    def _stop_slot_macro(self, slot_index: int, macro_name: str) -> None:
        for pid, (name, proc) in list(self.active_processes.items()):
            if name == macro_name:
                self._terminate_process_tree(proc)
                self.active_processes.pop(pid, None)
                self._process_slot_map.pop(pid, None)
        self._mark_slot_status(slot_index, "failed", "사용자가 매크로를 중지했습니다.")
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
        self._process_slot_map.clear()
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
            source = self._process_slot_map.pop(pid, None)
            if source is not None:
                preset_id, slot_index = source
                state = "completed" if proc.returncode == 0 else "failed"
                self._mark_slot_status(slot_index, state, f"{_name} 종료 코드 {proc.returncode}", preset_id=preset_id)
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
        self._auto_config_save.stop()
        if self._browser_bridge is not None:
            self._browser_bridge.close()
            self._browser_bridge = None
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
