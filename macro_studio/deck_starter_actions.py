"""Small, non-destructive starter action packs for QuickSlot Deck presets.

The actions follow familiar Stream Deck patterns: launch a destination, send a
targeted shortcut, or navigate to a page. They never run automatically just
because a preset becomes active; a tile must be pressed.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui


@lru_cache(maxsize=128)
def _tile_png_data(glyph: str, background: str) -> str:
    """Render each portable starter image only once per process."""
    pixmap = QtGui.QPixmap(128, 128)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    rect = QtCore.QRectF(4, 4, 120, 120)
    painter.setBrush(QtGui.QColor(background))
    painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 3))
    painter.drawRoundedRect(rect, 24, 24)
    painter.setBrush(QtGui.QColor(255, 255, 255, 32))
    painter.setPen(QtCore.Qt.NoPen)
    painter.drawRoundedRect(QtCore.QRectF(13, 13, 102, 48), 18, 18)
    font = QtGui.QFont("Segoe UI Symbol")
    font.setBold(True)
    font.setPixelSize(52 if len(glyph) <= 1 else 38)
    painter.setFont(font)
    painter.setPen(QtGui.QColor("#FFFFFF"))
    painter.drawText(rect, QtCore.Qt.AlignCenter, glyph)
    painter.end()
    encoded = QtCore.QByteArray()
    buffer = QtCore.QBuffer(encoded)
    buffer.open(QtCore.QIODevice.WriteOnly)
    if not pixmap.save(buffer, "PNG"):
        raise RuntimeError("기본 슬롯 아이콘을 만들지 못했습니다.")
    buffer.close()
    return bytes(encoded.toBase64()).decode("ascii")


def _tile_icon(glyph: str, background: str) -> dict[str, Any]:
    return {
        "image_data": _tile_png_data(glyph, background),
        "image_path": "", "emoji": "", "icon_size": 56,
        "full_stretch": True, "text_show": True,
        "text_position": "bottom", "font_size": 10, "spacing": 3,
        "icon_source": "starter_pack",
    }


def _action(label: str, kind: str, glyph: str, color: str, **fields: Any) -> tuple[dict[str, Any], str, str]:
    return {"kind": kind, "label": label, **fields}, glyph, color


def _open(label: str, target: str, glyph: str, color: str) -> tuple[dict[str, Any], str, str]:
    return _action(label, "open_target", glyph, color, target=target)


def _key(label: str, keys: str, executable: str, glyph: str, color: str) -> tuple[dict[str, Any], str, str]:
    return _action(label, "hotkey", glyph, color, keys=keys,
                   target_exe=executable, input_mode="active")


def _installed_ldplayer() -> str:
    candidates = (
        Path("D:/LDPlayer/LDPlayer9/dnplayer.exe"),
        Path("C:/LDPlayer/LDPlayer9/dnplayer.exe"),
        Path("C:/LDPlayer/LDPlayer4.0/dnplayer.exe"),
        Path("C:/Program Files/LDPlayer/LDPlayer9/dnplayer.exe"),
        Path.home() / "Desktop" / "LDPlayer 9.lnk",
    )
    return next((str(path) for path in candidates if path.is_file()), "")


def starter_action_pack(key: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return editable Deck actions and their generated icons for one mode."""
    if key == "youtube":
        entries = [
            _open("YouTube 홈", "https://www.youtube.com/", "▶", "#EF3340"),
            _open("구독", "https://www.youtube.com/feed/subscriptions", "★", "#DE3D48"),
            _open("시청 기록", "https://www.youtube.com/feed/history", "◷", "#C83F52"),
            _key("재생·일시정지", "K", "whale.exe", "Ⅱ", "#E34952"),
            _key("음소거", "M", "whale.exe", "♪", "#B8324B"),
            _key("10초 뒤로", "J", "whale.exe", "↶", "#B83746"),
            _key("10초 앞으로", "L", "whale.exe", "↷", "#B83746"),
        ]
    elif key == "naver":
        entries = [
            _open("네이버 홈", "https://www.naver.com/", "N", "#03C75A"),
            _open("뉴스", "https://news.naver.com/", "▤", "#10AE61"),
            _open("지도", "https://map.naver.com/", "⌖", "#17B978"),
            _key("새로고침", "Ctrl+R", "whale.exe", "↻", "#1C9E63"),
            _key("뒤로", "Alt+Left", "whale.exe", "←", "#178D67"),
            _key("앞으로", "Alt+Right", "whale.exe", "→", "#178D67"),
        ]
    elif key == "ldplayer":
        entries = []
        installed = _installed_ldplayer()
        if installed:
            entries.append(_open("LDPlayer 실행", installed, "LD", "#3C83ED"))
        entries.extend([
            _key("뒤로", "Esc", "dnplayer.exe", "←", "#447EF0"),
            _key("전체 화면", "F11", "dnplayer.exe", "⛶", "#536FE0"),
            _key("미니 모드", "Ctrl+F1", "dnplayer.exe", "▣", "#6672D7"),
        ])
    elif key == "explorer":
        entries = [_open("파일 탐색기", "C:/Windows/explorer.exe", "▣", "#E0AD3D")]
        for label, path, glyph in (("다운로드", Path.home() / "Downloads", "↓"),
                                   ("문서", Path.home() / "Documents", "▤")):
            if path.is_dir():
                entries.append(_open(label, str(path), glyph, "#CAA140"))
    elif key == "notepad":
        entries = [
            _open("메모장 실행", "C:/Windows/System32/notepad.exe", "✎", "#4B9CCF"),
            _key("새 문서", "Ctrl+N", "notepad.exe", "+", "#4AADD2"),
            _key("저장", "Ctrl+S", "notepad.exe", "▣", "#3A8EBF"),
        ]
    elif key == "calculator":
        entries = [_open("계산기 실행", "C:/Windows/System32/calc.exe", "∑", "#9A7CD8")]
    elif key == "settings":
        entries = [
            _open("Windows 설정", "ms-settings:", "⚙", "#78869B"),
            _open("디스플레이", "ms-settings:display", "▣", "#697F9D"),
            _open("사운드", "ms-settings:sound", "♪", "#657997"),
        ]
    else:
        return [], {}

    slots: list[dict[str, Any]] = []
    icons: dict[str, dict[str, Any]] = {}
    for index, (action, glyph, color) in enumerate(entries):
        slots.append({"macro": action["label"], "hotkey": "", "mode": "deck_action",
                      "action": copy.deepcopy(action)})
        icons[str(index)] = _tile_icon(glyph, color)
    return slots, icons
