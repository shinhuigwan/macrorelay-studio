from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any
import uuid

from PySide6 import QtCore, QtGui, QtWidgets

from .theme import COLORS


ACTION_STYLES: dict[str, tuple[str, str]] = {
    "mouse_click": ("CLICK", "#7C6CFF"),
    "inactive_click": ("CLICK", "#7C6CFF"),
    "image_search": ("VISION", "#4D9FFF"),
    "screen_condition": ("IF", "#A879FF"),
    "datetime_condition": ("TIME", "#F5B942"),
    "ocr": ("OCR", "#4D9FFF"),
    "type_text": ("TEXT", "#C47CFF"),
    "wait": ("WAIT", "#F5B942"),
    "browser_action": ("WEB", "#35C89A"),
    "table_store": ("DATA", "#24B8C7"),
    "table_copy": ("DATA", "#24B8C7"),
    "table_paste": ("DATA", "#24B8C7"),
    "table_excel_read": ("EXCEL", "#24B8C7"),
    "table_excel_write": ("EXCEL", "#24B8C7"),
    "flow_control": ("FLOW", "#F06A78"),
    "text_condition": ("IF", "#F06A78"),
    "set_var": ("VAR", "#C47CFF"),
    "calc_var": ("CALC", "#C47CFF"),
    "coord_mode": ("COORD", "#9DA7BA"),
    "call_submacro": ("MACRO", "#7C6CFF"),
    "vault_get": ("VAULT", "#5ED3A6"),
    "run_program": ("RUN", "#35C89A"),
    "terminate_program": ("STOP", "#F06A78"),
    "pixel_search": ("COLOR", "#FF6B9D"),
    "ocr_tracking": ("TRACK", "#4D9FFF"),
    "multi_pixel_check": ("PIXELS", "#FF6B9D"),
    "wait_color": ("WAITCLR", "#F5B942"),
    "color_ratio": ("GAUGE", "#FF6B9D"),
}

ACTION_TITLES = {
    "mouse_click": "마우스 클릭", "inactive_click": "비활성 클릭", "image_search": "이미지 서치",
    "screen_condition": "화면 조건", "datetime_condition": "날짜·시간 조건",
    "ocr": "OCR 인식", "type_text": "텍스트 입력", "wait": "대기", "browser_action": "브라우저 요소",
    "table_store": "테이블 저장", "table_copy": "테이블 복사", "table_paste": "테이블 붙여넣기",
    "table_excel_read": "Excel 읽기", "table_excel_write": "Excel 쓰기", "flow_control": "반복 이동",
    "text_condition": "텍스트 조건", "set_var": "변수 설정", "calc_var": "변수 계산", "coord_mode": "좌표 기준",
    "call_submacro": "서브매크로", "vault_get": "보안 값", "run_program": "프로그램 실행", "terminate_program": "프로그램 종료",
    "pixel_search": "색상 서치", "ocr_tracking": "OCR 추적",
    "multi_pixel_check": "다중 픽셀 체크", "wait_color": "색상 변화 대기", "color_ratio": "색상 비율 게이지",
}


class NodeGraphScene(QtWidgets.QGraphicsScene):
    def drawBackground(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        painter.fillRect(rect, QtGui.QColor("#0D0F15"))
        painter.save()
        minor = 24
        major = minor * 5
        left = int(rect.left()) - (int(rect.left()) % minor)
        top = int(rect.top()) - (int(rect.top()) % minor)
        minor_pen = QtGui.QPen(QtGui.QColor(38, 43, 56, 95), 1)
        major_pen = QtGui.QPen(QtGui.QColor(48, 55, 72, 130), 1)
        x = left
        while x < rect.right():
            painter.setPen(major_pen if x % major == 0 else minor_pen)
            painter.drawLine(QtCore.QLineF(x, rect.top(), x, rect.bottom()))
            x += minor
        y = top
        while y < rect.bottom():
            painter.setPen(major_pen if y % major == 0 else minor_pen)
            painter.drawLine(QtCore.QLineF(rect.left(), y, rect.right(), y))
            y += minor
        painter.restore()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            selected_groups = [
                item for item in self.selectedItems()
                if hasattr(item, "node_indexes") or hasattr(item, "comment_id")
            ]
            if selected_groups:
                for group in selected_groups:
                    canvas = getattr(group, "canvas", None)
                    if canvas is not None:
                        canvas.remove_comment_box(group)
                event.accept()
                return

            selected = sorted(
                item.index for item in self.selectedItems() if isinstance(item, NodeItem)
            )
            canvas = None
            for item in self.selectedItems():
                if isinstance(item, NodeItem):
                    canvas = item.canvas
                    break
            if canvas is not None:
                for index in reversed(selected):
                    canvas.node_delete_requested.emit(index)
            event.accept()
            return
        super().keyPressEvent(event)


class NodeGraphView(QtWidgets.QGraphicsView):
    zoom_changed = QtCore.Signal(int)

    def __init__(self, scene: QtWidgets.QGraphicsScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(
            QtGui.QPainter.Antialiasing
            | QtGui.QPainter.TextAntialiasing
            | QtGui.QPainter.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QGraphicsView { background: #0D0F15; border: none; }\n"
            "QScrollBar:horizontal { height: 12px; background: #131722; border-top: 1px solid #1E2433; }\n"
            "QScrollBar::handle:horizontal { background: #2A3245; min-width: 30px; border-radius: 4px; margin: 2px; }\n"
            "QScrollBar::handle:horizontal:hover { background: #3E4A66; }\n"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }\n"
            "QScrollBar:vertical { width: 12px; background: #131722; border-left: 1px solid #1E2433; }\n"
            "QScrollBar::handle:vertical { background: #2A3245; min-height: 30px; border-radius: 4px; margin: 2px; }\n"
            "QScrollBar::handle:vertical:hover { background: #3E4A66; }\n"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )
        self._panning = False
        self._double_click_pan = False
        self._pan_start = QtCore.QPoint()
        self._zoom = 100

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        canvas = self.parent()
        if hasattr(canvas, "minimap") and canvas.minimap:
            canvas.minimap.update_position()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        factor = 1.14 if event.angleDelta().y() > 0 else 1 / 1.14
        next_zoom = max(35, min(220, int(self._zoom * factor)))
        factor = next_zoom / self._zoom
        self.scale(factor, factor)
        self._zoom = next_zoom
        self.zoom_changed.emit(self._zoom)
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton and not self.itemAt(event.position().toPoint()):
            canvas = self.parent()
            if isinstance(canvas, NodeCanvas):
                canvas.begin_rubber_selection()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and not self.itemAt(event.position().toPoint()):
            self._panning = True
            self._double_click_pan = True
            self._pan_start = event.position().toPoint()
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._panning:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._panning and (
            event.button() == QtCore.Qt.MiddleButton
            or (event.button() == QtCore.Qt.LeftButton and self._double_click_pan)
        ):
            self._panning = False
            self._double_click_pan = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == QtCore.Qt.LeftButton:
            canvas = self.parent()
            if isinstance(canvas, NodeCanvas):
                canvas.end_rubber_selection()

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        item = self.itemAt(event.pos())
        if item is None:
            canvas = self.parent()
            if isinstance(canvas, NodeCanvas):
                menu = QtWidgets.QMenu(self)
                selected_nodes = canvas.selected_indexes()
                act_branch = None
                act_align_top = None
                act_align_bottom = None
                act_align_left = None
                act_align_right = None
                act_align_dh = None
                act_align_dv = None
                if len(selected_nodes) >= 2:
                    act_branch = menu.addAction(f"🔀 선택 노드 {len(selected_nodes)}개를 순차 분기로 연결")
                    align_sub = menu.addMenu(f"📐 선택 노드 {len(selected_nodes)}개 일괄 정렬")
                    act_align_top = align_sub.addAction("📐 상단 일렬 맞춤 (Top)")
                    act_align_bottom = align_sub.addAction("📐 하단 일렬 맞춤 (Bottom)")
                    act_align_left = align_sub.addAction("📐 좌측 일렬 맞춤 (Left)")
                    act_align_right = align_sub.addAction("📐 우측 일렬 맞춤 (Right)")
                    align_sub.addSeparator()
                    act_align_dh = align_sub.addAction("↔ 가로 간격 균등 분배")
                    act_align_dv = align_sub.addAction("↕ 세로 간격 균등 분배")
                    menu.addSeparator()
                act_group = menu.addAction("🗂️ 여기에 새 노드 그룹 추가...")
                act_archive = menu.addAction("📦 노드 보관함 열기...")
                act_auto = menu.addAction("자동 정렬")
                act_fit = menu.addAction("전체 보기")
                chosen = menu.exec(event.globalPos())
                if not chosen:
                    event.accept()
                    return
                if act_branch is not None and chosen == act_branch:
                    canvas.branch_chain_requested.emit(selected_nodes)
                elif chosen == act_align_top:
                    canvas.align_selected_nodes("top")
                elif chosen == act_align_bottom:
                    canvas.align_selected_nodes("bottom")
                elif chosen == act_align_left:
                    canvas.align_selected_nodes("left")
                elif chosen == act_align_right:
                    canvas.align_selected_nodes("right")
                elif chosen == act_align_dh:
                    canvas.align_selected_nodes("distribute_h")
                elif chosen == act_align_dv:
                    canvas.align_selected_nodes("distribute_v")
                elif chosen == act_group:
                    canvas.add_comment_box(self.mapToScene(event.pos()), "새 노드 그룹", "yellow", 440.0, 260.0)
                elif chosen == act_archive:
                    canvas.archive_requested.emit()
                elif chosen == act_auto:
                    canvas.auto_layout()
                elif chosen == act_fit:
                    canvas.fit_all()
                event.accept()
                return
        super().contextMenuEvent(event)

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom = 100
        self.zoom_changed.emit(self._zoom)

    def fit_all(self) -> None:
        rect = self.scene().itemsBoundingRect()
        if rect.isEmpty():
            return
        self.fitInView(rect.adjusted(-70, -70, 70, 70), QtCore.Qt.KeepAspectRatio)
        self._zoom = max(35, min(220, int(self.transform().m11() * 100)))
        self.zoom_changed.emit(self._zoom)


class NodeToolTipPopup(QtWidgets.QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QFrame { background:#141724; border:2px solid #7C6CFF; border-radius:10px; }\n"
            "QLabel { color:#F2F4F8; background:transparent; font-family:'Malgun Gothic', 'Segoe UI'; }"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        self.header = QtWidgets.QLabel()
        self.header.setStyleSheet("font-weight:800; font-size:12px; color:#F2F4F8;")

        self.summary = QtWidgets.QLabel()
        self.summary.setStyleSheet("font-weight:700; font-size:11px; color:#38E7FF;")

        self.detail = QtWidgets.QLabel()
        self.detail.setStyleSheet("font-size:10px; color:#B8C0D0;")
        self.detail.setWordWrap(True)

        layout.addWidget(self.header)
        layout.addWidget(self.summary)
        layout.addWidget(self.detail)

    def set_step_info(self, index: int, action_title: str, summary: str, details: str) -> None:
        self.header.setText(f"[ {index} ]  {action_title}")
        self.summary.setText(summary)
        self.detail.setText(details if details else "설정 세부 정보 없음")
        self.adjustSize()


class NodePort(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, node: "NodeItem", kind: str, direction: str = "out") -> None:
        super().__init__(-7, -7, 14, 14, node)
        self.node = node
        self.kind = kind
        self.direction = direction
        color = COLORS["success"] if kind == "success" else COLORS["danger"]
        self.setBrush(QtGui.QColor(color))
        self.setPen(QtGui.QPen(QtGui.QColor("#101218"), 2.2))
        self.setZValue(6)
        if direction == "out":
            self.setCursor(QtCore.Qt.CrossCursor)
            self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)
            if kind == "success":
                self.setToolTip("<b>초록색 성공 출력 포트 (Success)</b><br>• 실행 성공 시 다음으로 실행할 노드로 드래그하여 연결합니다.<br>• 연결선을 빈 곳으로 드래그하면 즉시 연결이 해제됩니다.")
            else:
                self.setToolTip("<b>빨간색 실패 출력 포트 (Fail)</b><br>• 이미지 탐색 실패나 조건 불일치 시 실행할 노드로 드래그하여 연결합니다.<br>• 💡 <b>Zero-Wire 분기</b>: 순차 분기로 묶인 노드는 실패선을 잇지 않아도 다음 분기 첫 노드로 자동 전환됩니다!")
        else:
            self.setCursor(QtCore.Qt.ArrowCursor)
            self.setAcceptedMouseButtons(QtCore.Qt.NoButton)
            if kind == "success":
                self.setToolTip("<b>성공 입력 포트 (In)</b><br>이전 노드가 성공했을 때 이 노드가 실행됩니다.")
            else:
                self.setToolTip("<b>실패 입력 포트 (In)</b><br>이전 노드가 실패했을 때 이 노드가 실행됩니다.")

    def shape(self) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        path.addEllipse(self.rect().adjusted(-9, -9, 9, 9))
        return path

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self.direction == "out":
            self.node.canvas.begin_link(self.node, self.kind, self.scenePos())
            event.accept()

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self.direction == "out":
            self.node.canvas.update_link(event.scenePos())
            event.accept()

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self.direction == "out":
            self.node.canvas.end_link(self.node, self.kind, event.scenePos())
            event.accept()


class ImagePreviewPopup(QtWidgets.QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QFrame { background:#11151E; border:2px solid #42DDF5; border-radius:10px; }"
            "QLabel { color:#F2F4F8; border:none; background:transparent; }"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.title = QtWidgets.QLabel()
        self.title.setStyleSheet("font-weight:800;")
        self.image = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
        self.detail = QtWidgets.QLabel()
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.image)
        layout.addWidget(self.detail)

    def show_images(self, entries: list[tuple[str, Path]], screen_pos: QtCore.QPoint) -> None:
        loaded = [(alias, QtGui.QPixmap(str(path))) for alias, path in entries]
        loaded = [(alias, pixmap) for alias, pixmap in loaded if not pixmap.isNull()]
        if not loaded:
            return
        self._show_pixmaps(loaded, screen_pos)

    def show_pixmap_preview(self, label: str, pixmap: QtGui.QPixmap, screen_pos: QtCore.QPoint,
                            search_region: list[int] | None = None) -> None:
        if pixmap.isNull():
            return
        self._show_pixmaps([(label, pixmap)], screen_pos, search_region=search_region)

    def _show_pixmaps(self, loaded: list[tuple[str, QtGui.QPixmap]], screen_pos: QtCore.QPoint,
                      search_region: list[int] | None = None) -> None:
        if not loaded:
            return
        if len(loaded) == 1:
            alias, pixmap = loaded[0]
            shown = QtGui.QPixmap(pixmap)
            if shown.width() > 360 or shown.height() > 240:
                shown = shown.scaled(360, 240, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            if search_region and len(search_region) >= 4:
                scale_x = shown.width() / max(1, pixmap.width())
                scale_y = shown.height() / max(1, pixmap.height())
                painter = QtGui.QPainter(shown)
                painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
                pen = QtGui.QPen(QtGui.QColor("#FF9800"), 2, QtCore.Qt.DashLine)
                painter.setPen(pen)
                painter.setBrush(QtGui.QColor(255, 152, 0, 30))
                rx = int(search_region[0] * scale_x)
                ry = int(search_region[1] * scale_y)
                rw = int((search_region[2] - search_region[0]) * scale_x)
                rh = int((search_region[3] - search_region[1]) * scale_y)
                painter.drawRect(rx, ry, rw, rh)
                painter.setPen(QtGui.QColor("#FF9800"))
                painter.setFont(QtGui.QFont("Malgun Gothic", 7, QtGui.QFont.Bold))
                painter.drawText(rx + 3, ry - 3, "서치 영역")
                painter.end()
            self.title.setText(alias or "이미지 서치")
            region_text = f" · 서치 영역 {search_region[2]-search_region[0]}×{search_region[3]-search_region[1]}" if search_region and len(search_region) >= 4 else ""
            self.detail.setText(f"{pixmap.width()} × {pixmap.height()} · 이미지 서치 원본{region_text}")
        else:
            columns = min(4, max(2, math.ceil(math.sqrt(len(loaded)))))
            rows = math.ceil(len(loaded) / columns)
            cell_width = max(112, min(190, 680 // columns))
            cell_height = max(92, min(150, 470 // rows))
            shown = QtGui.QPixmap(columns * cell_width, rows * cell_height)
            shown.fill(QtGui.QColor("#0D1119"))
            painter = QtGui.QPainter(shown)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            font = QtGui.QFont("Malgun Gothic", 8)
            painter.setFont(font)
            for index, (alias, pixmap) in enumerate(loaded):
                row, column = divmod(index, columns)
                cell = QtCore.QRect(column * cell_width + 4, row * cell_height + 4, cell_width - 8, cell_height - 8)
                painter.setPen(QtGui.QPen(QtGui.QColor("#354056"), 1))
                painter.setBrush(QtGui.QColor("#151B25"))
                painter.drawRoundedRect(cell, 7, 7)
                image_rect = cell.adjusted(6, 6, -6, -27)
                scaled = pixmap.scaled(image_rect.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                x = image_rect.center().x() - scaled.width() // 2
                y = image_rect.center().y() - scaled.height() // 2
                painter.drawPixmap(x, y, scaled)
                painter.setPen(QtGui.QColor("#E9EDF5"))
                text = QtGui.QFontMetrics(font).elidedText(alias, QtCore.Qt.ElideRight, cell.width() - 12)
                painter.drawText(cell.adjusted(6, cell.height() - 24, -6, -3), QtCore.Qt.AlignCenter, text)
            painter.end()
            self.title.setText(f"멀티 이미지 서치 · {len(loaded)}개")
            self.detail.setText("정확도가 가장 높은 이미지가 선택됩니다.")
        self.image.setPixmap(shown)
        self.image.setFixedSize(shown.size())
        self.detail.setFixedWidth(max(180, shown.width()))
        self.adjustSize()
        target = screen_pos + QtCore.QPoint(18, 18)
        screen = QtGui.QGuiApplication.screenAt(screen_pos) or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            target.setX(max(area.left(), min(target.x(), area.right() - self.width())))
            target.setY(max(area.top(), min(target.y(), area.bottom() - self.height())))
        self.move(target)
        self.show()
        self.raise_()


class NodeImagePreviewBadge(QtWidgets.QGraphicsSimpleTextItem):
    def __init__(
        self,
        canvas: "NodeCanvas",
        entries: list[tuple[str, Path]],
        step_index: int | None = None,
        parent=None,
        pixmap: QtGui.QPixmap | None = None,
        search_region: list[int] | None = None,
    ) -> None:
        super().__init__("▦" if len(entries) > 1 else "▧", parent)
        self.canvas = canvas
        self.entries = entries
        self.step_index = step_index
        self._pixmap = pixmap
        self._search_region = search_region
        self.setAcceptHoverEvents(True)

    def hoverEnterEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        if self._pixmap is not None and not self._pixmap.isNull():
            label = self.entries[0][0] if self.entries else "캡처 이미지"
            self.canvas._preview_popup.show_pixmap_preview(
                label, self._pixmap, event.screenPos(), search_region=self._search_region
            )
        elif self.entries:
            self.canvas.show_image_preview(self.entries, event.screenPos())
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        self.canvas.hide_image_preview()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self.step_index is not None and (self.entries or self._pixmap):
            self.canvas.hide_image_preview()
            self.canvas.image_edit_requested.emit(self.step_index)
            event.accept()
            return
        super().mousePressEvent(event)


class TriggerNodeItem(QtWidgets.QGraphicsObject):
    """Visual-only card for a macro-level image trigger.

    Triggers are not executable steps, so keeping them outside ``nodes`` prevents
    graph editing and runtime highlighting from accidentally treating them as one.
    """

    WIDTH = 270.0
    HEIGHT = 104.0

    def __init__(self, trigger: dict[str, Any], canvas: "NodeCanvas") -> None:
        super().__init__()
        self.trigger = trigger
        self.canvas = canvas
        self.display_title = "화면 트리거"
        self.setZValue(2)
        self.setToolTip("이 이미지가 화면에 나타나면 매크로 실행을 시작합니다. 실행 단계가 아닌 시작 조건입니다.")
        alias = str(trigger.get("asset") or trigger.get("name") or "시작 이미지 선택 필요").strip()
        path = canvas.asset_preview_path(alias)
        self.preview_badge: NodeImagePreviewBadge | None = None
        if path is not None:
            badge = NodeImagePreviewBadge(canvas, [(alias, path)], None, self)
            font = QtGui.QFont("Segoe UI Symbol", 12)
            font.setBold(True)
            badge.setFont(font)
            badge.setBrush(QtGui.QColor("#C49BFF"))
            badge.setPos(243, 7)
            badge.setZValue(8)
            badge.setCursor(QtCore.Qt.PointingHandCursor)
            badge.setToolTip(f"<b>화면 트리거</b><br>{html.escape(alias)}<br>커서를 올리면 시작 조건 이미지를 확인합니다.")
            self.preview_badge = badge

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(-3, -3, self.WIDTH + 6, self.HEIGHT + 6)

    def paint(self, painter: QtGui.QPainter, _option: QtWidgets.QStyleOptionGraphicsItem, _widget=None) -> None:
        rect = QtCore.QRectF(0, 0, self.WIDTH, self.HEIGHT)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtGui.QPen(QtGui.QColor("#A879FF"), 2.2))
        painter.setBrush(QtGui.QColor("#1D1930"))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#392A59"))
        painter.drawRoundedRect(QtCore.QRectF(0, 0, self.WIDTH, 34), 10, 10)
        painter.drawRect(QtCore.QRectF(0, 23, self.WIDTH, 11))
        painter.setPen(QtGui.QColor("#EDE5FF"))
        title_font = QtGui.QFont("Malgun Gothic", 10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(QtCore.QRectF(12, 4, 220, 27), QtCore.Qt.AlignVCenter, "⚡  화면 트리거")
        alias = str(self.trigger.get("asset") or self.trigger.get("name") or "시작 이미지 선택 필요")
        body_font = QtGui.QFont("Malgun Gothic", 9)
        painter.setFont(body_font)
        painter.setPen(QtGui.QColor("#F2F4F8"))
        elided = QtGui.QFontMetrics(body_font).elidedText(alias, QtCore.Qt.ElideRight, 244)
        painter.drawText(QtCore.QRectF(13, 42, 244, 24), QtCore.Qt.AlignVCenter, elided)
        painter.setPen(QtGui.QColor("#C7B5E8"))
        painter.drawText(QtCore.QRectF(13, 70, 244, 22), QtCore.Qt.AlignVCenter, "화면에 나타나면 매크로 시작")


class WorkflowLaneItem(QtWidgets.QGraphicsObject):
    """Interactive workflow lane header — drag to move all nodes, double-click to fold."""

    HEADER_HEIGHT = 36.0
    PADDING = 26.0

    def __init__(
        self,
        canvas: "NodeCanvas",
        group_index: int,
        workflow_id: str,
        label_text: str,
        indexes: list[int],
        color: QtGui.QColor,
    ) -> None:
        super().__init__()
        self.canvas = canvas
        self.group_index = group_index
        self.workflow_id = workflow_id
        self.label_text = label_text
        self.indexes = indexes
        self.color = color
        self.folded = False

        self._rect = QtCore.QRectF()
        self._drag_origin = QtCore.QPointF()
        self._node_origins: dict[int, QtCore.QPointF] = {}
        self._dragging = False

        self.setZValue(-30)
        self.setAcceptHoverEvents(True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)

    def boundingRect(self) -> QtCore.QRectF:
        return self._rect.adjusted(-4, -4, 4, 4)

    def sync_rect(self) -> None:
        self.prepareGeometryChange()
        rect = QtCore.QRectF()
        for index in self.indexes:
            node = self.canvas.nodes.get(index)
            if node is not None and node.isVisible():
                rect = rect.united(node.sceneBoundingRect()) if not rect.isNull() else node.sceneBoundingRect()
        if rect.isNull():
            if not self._rect.isNull():
                self._rect = QtCore.QRectF(
                    self._rect.x(), self._rect.y(),
                    max(self._rect.width(), 160), self.HEADER_HEIGHT + 8,
                )
            return
        self._rect = rect.adjusted(-self.PADDING, -52, self.PADDING, 30)
        self.update()

    def paint(self, painter: QtGui.QPainter, _option: QtWidgets.QStyleOptionGraphicsItem, _widget=None) -> None:
        if self._rect.isNull():
            return
        transform = painter.worldTransform()
        zoom = transform.m11()
        font_boost = min(1.25, (1.0 / zoom) ** 0.35) if zoom < 0.85 else 1.0
        line_w = max(1.6, 1.4 / zoom) if zoom < 0.85 else 1.4

        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        bg = QtGui.QColor(self.color)
        bg.setAlpha(26 if zoom < 0.85 else 18)
        painter.setPen(QtGui.QPen(self.color, line_w, QtCore.Qt.DashLine))
        painter.setBrush(bg)
        painter.drawRoundedRect(self._rect, 14, 14)

        hr = QtCore.QRectF(self._rect.x(), self._rect.y(), self._rect.width(), self.HEADER_HEIGHT)
        hbg = QtGui.QColor(self.color)
        hbg.setAlpha(60 if zoom < 0.85 else 45)
        hp = QtGui.QPainterPath()
        hp.addRoundedRect(hr, 14, 14)
        hp.addRect(QtCore.QRectF(hr.x(), hr.y() + 20, hr.width(), 16))
        painter.setPen(QtCore.Qt.NoPen)
        painter.fillPath(hp, hbg)

        arrow_font = QtGui.QFont("Segoe UI Symbol", int(9.5 * font_boost), QtGui.QFont.Bold)
        painter.setFont(arrow_font)
        painter.setPen(self.color.lighter(150))
        painter.drawText(QtCore.QRectF(hr.x() + 8, hr.y(), 16, self.HEADER_HEIGHT), QtCore.Qt.AlignVCenter, "▸" if self.folded else "▾")

        label_font = QtGui.QFont("Malgun Gothic", int(10 * font_boost), QtGui.QFont.Bold)
        painter.setFont(label_font)
        painter.setPen(self.color.lighter(140))
        lx, lw = hr.x() + 26, hr.width() - 42
        metrics = QtGui.QFontMetrics(label_font)
        display_label = self.label_text if any(k in self.label_text for k in ("작업", "분기")) else f"작업 {self.group_index + 1} · {self.label_text}"
        painter.drawText(QtCore.QRectF(lx, hr.y(), lw, self.HEADER_HEIGHT), QtCore.Qt.AlignVCenter, metrics.elidedText(display_label, QtCore.Qt.ElideRight, int(lw)))

        ct = f"{len(self.indexes)}"
        cf = QtGui.QFont("Segoe UI", int(8 * font_boost), QtGui.QFont.Bold)
        painter.setFont(cf)
        cw = max(20, QtGui.QFontMetrics(cf).horizontalAdvance(ct) + 12)
        cr = QtCore.QRectF(hr.right() - cw - 10, hr.y() + 7, cw, 20)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(self.color)
        painter.drawRoundedRect(cr, 6, 6)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(cr, QtCore.Qt.AlignCenter, ct)

    def _header_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self._rect.x(), self._rect.y(), self._rect.width(), self.HEADER_HEIGHT)

    def mouseDoubleClickEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self._header_rect().contains(event.pos()):
            self.toggle_fold()
            event.accept()

    def toggle_fold(self) -> None:
        self.folded = not self.folded
        folded_set = set(self.indexes)
        for index in self.indexes:
            node = self.canvas.nodes.get(index)
            if node is not None:
                node.setVisible(not self.folded)
        for edge in self.canvas.edges:
            if edge.source in folded_set or edge.target in folded_set:
                edge.setVisible(not self.folded)
        self.sync_rect()
        self.canvas._route_edges()
        self.canvas._sync_node_groups()
        self.update()

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._header_rect().contains(event.pos()):
            self._drag_origin = event.scenePos()
            self._node_origins = {}
            for index in self.indexes:
                node = self.canvas.nodes.get(index)
                if node is not None:
                    self._node_origins[index] = QtCore.QPointF(node.pos())
            self._dragging = True
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self._dragging:
            delta = event.scenePos() - self._drag_origin
            for index, origin in self._node_origins.items():
                node = self.canvas.nodes.get(index)
                if node is not None:
                    node.setPos(origin + delta)
            self.canvas._route_edges()
            self.sync_rect()
            self.canvas._sync_node_groups()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self.canvas.node_moved()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class NodeItem(QtWidgets.QGraphicsObject):
    WIDTH = 196.0
    HEIGHT = 116.0
    COLLAPSED_WIDTH = 88.0
    COLLAPSED_HEIGHT = 76.0

    def __init__(self, index: int, step: dict[str, Any], canvas: "NodeCanvas") -> None:
        super().__init__()
        self.index = index
        self.step = step
        self.canvas = canvas
        self.collapsed = index in canvas.collapsed_nodes
        self.is_multi = (
            str(step.get("action") or "") == "image_search"
            and len(step.get("assets") or []) > 1
        )
        custom_label = str(step.get("label") or step.get("name") or "").strip()
        self.display_title = (
            custom_label
            if custom_label
            else "멀티 이미지 서치"
            if self.is_multi
            else "서브플로우"
            if str(step.get("action") or "") == "call_submacro"
            else ACTION_TITLES.get(str(step.get("action") or "step"), str(step.get("action") or "step"))
        )
        self.setFlags(
            QtWidgets.QGraphicsItem.ItemIsMovable
            | QtWidgets.QGraphicsItem.ItemIsSelectable
            | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setAcceptHoverEvents(True)

        self.in_success_port = NodePort(self, "success", "in")
        self.in_fail_port = NodePort(self, "fail", "in")
        self.out_success_port = NodePort(self, "success", "out")
        self.out_fail_port = NodePort(self, "fail", "out")

        self.success_port = self.out_success_port
        self.fail_port = self.out_fail_port

        self._sync_compact_geometry()

        self.preview_badge: NodeImagePreviewBadge | None = None
        if str(step.get("action") or "") in {"image_search", "screen_condition"}:
            aliases = [str(value) for value in step.get("assets") or [] if str(value).strip()] if isinstance(step.get("assets"), list) else []
            primary = str(step.get("asset") or "").strip()
            if primary and primary not in aliases:
                aliases.insert(0, primary)
            aliases = list(dict.fromkeys(aliases))
            entries = [(alias, path) for alias in aliases if (path := canvas.asset_preview_path(alias)) is not None]
            live_pixmap = canvas.asset_preview_pixmap(str(index)) if not entries else None
            live_region = canvas.asset_search_region(str(index)) if live_pixmap else None
            badge = NodeImagePreviewBadge(canvas, entries, index, self, pixmap=live_pixmap, search_region=live_region)
            font = QtGui.QFont("Segoe UI Symbol", 9)
            font.setBold(True)
            badge.setFont(font)
            badge.setBrush(QtGui.QColor("#5ED9FF"))
            badge.setPos(self.current_width() - 22, 6)
            badge.setZValue(8)
            badge.setCursor(QtCore.Qt.PointingHandCursor)
            if entries:
                names = ", ".join(html.escape(alias) for alias, _path in entries)
                preview_title = "화면 조건" if str(step.get("action") or "") == "screen_condition" else ("멀티 이미지 서치" if len(entries) > 1 else "이미지 서치")
                badge.setToolTip(
                    f"<b>{preview_title}</b><br>"
                    f"전체 이미지: {names}<br>커서를 올리면 미리보기 · 클릭하면 상세 편집"
                )
            elif live_pixmap is not None:
                region_hint = f"<br>서치 영역: {live_region[2]-live_region[0]}×{live_region[3]-live_region[1]}" if live_region and len(live_region) >= 4 else ""
                badge.setToolTip(f"<b>캡처 이미지</b><br>{live_pixmap.width()}×{live_pixmap.height()}{region_hint}<br>커서를 올리면 미리보기")
            else:
                badge.setToolTip("이미지 미선택")
            self.preview_badge = badge
            badge.setVisible(not self.collapsed)

    def current_width(self) -> float:
        return self.COLLAPSED_WIDTH if self.collapsed else self.WIDTH

    def current_height(self) -> float:
        return self.COLLAPSED_HEIGHT if self.collapsed else self.HEIGHT

    def _sync_compact_geometry(self) -> None:
        w = self.current_width()
        if self.collapsed:
            self.in_success_port.setPos(0, 24)
            self.in_fail_port.setPos(0, 52)
            self.out_success_port.setPos(w, 24)
            self.out_fail_port.setPos(w, 52)
        else:
            self.in_success_port.setPos(0, 32)
            self.in_fail_port.setPos(0, 72)
            self.out_success_port.setPos(w, 32)
            self.out_fail_port.setPos(w, 72)

    def set_collapsed(self, collapsed: bool, notify: bool = True) -> None:
        normalized = bool(collapsed)
        if normalized == self.collapsed:
            return
        self.prepareGeometryChange()
        self.collapsed = normalized
        self._sync_compact_geometry()
        if self.preview_badge is not None:
            self.preview_badge.setVisible(not normalized)
            self.preview_badge.setPos(self.current_width() - 22, 6)
        self.update()
        self.canvas._route_edges()
        self.canvas._sync_workflow_lanes()
        if notify:
            self.canvas.node_collapsed(self.index, normalized)

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(-3, -3, self.current_width() + 6, self.current_height() + 6)

    def hoverEnterEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        super().hoverEnterEvent(event)
        summary = self.canvas.step_summary(self.step)
        detail_lines = []
        if self.step.get("window"):
            detail_lines.append(f"<b>대상:</b> {html.escape(str(self.step['window']))}")
        if self.step.get("text"):
            detail_lines.append(f"<b>입력:</b> {html.escape(str(self.step['text']))}")
        if self.step.get("duration"):
            detail_lines.append(f"<b>대기:</b> {self.step['duration']} ms")
        if self.step.get("retry_count"):
            detail_lines.append(f"<b>재시도:</b> {self.step['retry_count']}회")
        details = "<br>".join(detail_lines)

        popup = getattr(self.canvas, "_tooltip_popup", None)
        if popup is not None:
            popup.set_step_info(self.index, self.display_title, summary, details)
            screen_pos = event.screenPos()
            popup.move(screen_pos.x() + 15, screen_pos.y() + 15)
            popup.show()

    def hoverLeaveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        super().hoverLeaveEvent(event)
        popup = getattr(self.canvas, "_tooltip_popup", None)
        if popup is not None:
            popup.hide()

    def _get_engine_and_preset(self) -> tuple[str, str]:
        step = self.step
        engine = str(step.get("engine") or "opencv").lower()
        preset = str(step.get("search_preset") or "").lower()
        if preset not in {"ultra_fast", "balanced", "precise"}:
            if engine == "ahk":
                var = int(step.get("variation", 16) or 16)
                poll = int(step.get("poll_delay", 35) or 35)
                timeout = int(step.get("timeout", 1200) or 1200)
                if var <= 12 or poll <= 20:
                    preset = "ultra_fast"
                elif var >= 20 or timeout >= 1500:
                    preset = "precise"
                else:
                    preset = "balanced"
            else:
                profile = str(step.get("search_profile") or "fast").lower()
                conf = int(step.get("confidence", 84) or 84)
                if profile == "precise" or conf >= 89:
                    preset = "precise"
                elif profile == "fast" or conf <= 83:
                    preset = "ultra_fast"
                else:
                    preset = "balanced"
        return engine, preset

    def _draw_action_icon(self, painter: QtGui.QPainter, action: str, cx: float, cy: float, zoom: float = 1.0) -> None:
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        badge, accent = ("MULTI", "#38E7FF") if getattr(self, "is_multi", False) else ACTION_STYLES.get(action, ("STEP", COLORS["muted"]))
        pen_color = QtGui.QColor(accent)
        pen_w = max(2.2, 1.8 / (zoom ** 0.45)) if zoom < 0.85 else 2.2
        painter.setPen(QtGui.QPen(pen_color, pen_w))
        painter.setBrush(QtCore.Qt.NoBrush)

        if action in {"mouse_click", "inactive_click"}:
            painter.drawRoundedRect(QtCore.QRectF(cx - 9, cy - 12, 18, 24), 8, 8)
            painter.fillRect(QtCore.QRectF(cx - 9, cy - 12, 9, 11), pen_color)
            painter.drawLine(QtCore.QLineF(cx - 13, cy - 14, cx - 17, cy - 18))
            painter.drawLine(QtCore.QLineF(cx - 15, cy - 6, cx - 20, cy - 6))
            painter.drawLine(QtCore.QLineF(cx - 6, cy - 15, cx - 6, cy - 20))
        elif getattr(self, "is_multi", False) or action in {"image_search", "screen_condition"}:
            is_multi = getattr(self, "is_multi", False)
            engine, preset = self._get_engine_and_preset()

            # Colors matching media_1788458837506.png
            if engine == "ahk":
                theme_color = QtGui.QColor("#4D9FFF")  # Bright blue
                theme_fill = QtGui.QColor("#0F1E33")
            else:
                theme_color = QtGui.QColor("#4ADE80")  # Bright green
                theme_fill = QtGui.QColor("#0D2619")

            # 1. Outer Photo Frame
            if is_multi:
                # Stacked back frame
                painter.setPen(QtGui.QPen(theme_color.darker(150), 1.6))
                painter.setBrush(theme_fill.darker(130))
                painter.drawRoundedRect(QtCore.QRectF(cx - 22, cy - 17, 31, 25), 3, 3)
                # Front frame
                painter.setPen(QtGui.QPen(theme_color, 2.2))
                painter.setBrush(theme_fill)
                painter.drawRoundedRect(QtCore.QRectF(cx - 16, cy - 11, 31, 25), 3, 3)
            else:
                # Single photo frame
                frame_rect = QtCore.QRectF(cx - 20, cy - 15, 33, 27)
                painter.setPen(QtGui.QPen(theme_color, 2.2))
                painter.setBrush(theme_fill)
                painter.drawRoundedRect(frame_rect, 4, 4)

            # Mountain peaks artwork inside frame
            painter.setPen(QtCore.Qt.NoPen)
            # Sun dot
            painter.setBrush(theme_color)
            painter.drawEllipse(QtCore.QPointF(cx - 9, cy - 7), 2.8, 2.8)
            # Mountain path
            m_path = QtGui.QPainterPath()
            m_path.moveTo(cx - 19, cy + 10)
            m_path.lineTo(cx - 11, cy - 2)
            m_path.lineTo(cx - 5, cy + 4)
            m_path.lineTo(cx + 2, cy - 4)
            m_path.lineTo(cx + 11, cy + 10)
            m_path.closeSubpath()
            m_color = QtGui.QColor(theme_color)
            m_color.setAlpha(130)
            painter.setBrush(m_color)
            painter.drawPath(m_path)

            # 2. Overlapping Foreground Element (lower-right of frame)
            if engine == "ahk":
                # AutoHotkey: Magnifying Glass (돋보기)
                lx = cx + 6
                ly = cy + 4
                painter.setPen(QtGui.QPen(theme_color, 2.4))
                painter.setBrush(QtGui.QColor("#0B1322"))
                painter.drawEllipse(QtCore.QPointF(lx, ly), 8.5, 8.5)
                # Reflection arc
                painter.setPen(QtGui.QPen(theme_color.lighter(130), 1.2))
                painter.drawArc(QtCore.QRectF(lx - 6, ly - 6, 12, 12), 45 * 16, 80 * 16)
                # Handle
                painter.setPen(QtGui.QPen(theme_color, 3.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
                painter.drawLine(QtCore.QPointF(lx + 6.0, ly + 6.0), QtCore.QPointF(lx + 11.5, ly + 11.5))
            else:
                # OpenCV: Camera Aperture / Shutter (조리개)
                ax = cx + 6
                ay = cy + 4
                painter.setPen(QtGui.QPen(theme_color, 2.2))
                painter.setBrush(QtGui.QColor("#081A12"))
                painter.drawEllipse(QtCore.QPointF(ax, ay), 9.0, 9.0)
                # 6 Aperture iris blades
                painter.setPen(QtGui.QPen(theme_color, 1.5))
                import math
                for i in range(6):
                    ang1 = math.radians(i * 60)
                    ang2 = math.radians(i * 60 + 55)
                    x1 = ax + 8.5 * math.cos(ang1)
                    y1 = ay + 8.5 * math.sin(ang1)
                    x2 = ax + 3.4 * math.cos(ang2)
                    y2 = ay + 3.4 * math.sin(ang2)
                    painter.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))

            # 3. Bottom-Right Preset Emblem Badge (⚡, ◐, 🎯)
            ex = cx + 16.5
            ey = cy + 12.0
            er = 7.5

            if preset == "ultra_fast":
                emblem_pen = QtGui.QColor("#22C55E")  # Green
                emblem_bg = QtGui.QColor("#0E2619")
            elif preset == "precise":
                emblem_pen = QtGui.QColor("#F59E0B")  # Amber
                emblem_bg = QtGui.QColor("#2B1E0A")
            else:  # balanced
                emblem_pen = QtGui.QColor("#A855F7")  # Purple
                emblem_bg = QtGui.QColor("#221430")

            painter.setPen(QtGui.QPen(emblem_pen, 1.8))
            painter.setBrush(emblem_bg)
            painter.drawEllipse(QtCore.QPointF(ex, ey), er, er)

            if preset == "ultra_fast":
                # Lightning bolt
                bolt = QtGui.QPainterPath()
                bolt.moveTo(ex + 0.4, ey - 4.8)
                bolt.lineTo(ex - 2.5, ey + 0.3)
                bolt.lineTo(ex + 0.6, ey + 0.3)
                bolt.lineTo(ex - 0.6, ey + 4.8)
                bolt.lineTo(ex + 3.0, ey - 0.6)
                bolt.lineTo(ex - 0.3, ey - 0.6)
                bolt.closeSubpath()
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(emblem_pen)
                painter.drawPath(bolt)
            elif preset == "precise":
                # Target crosshair
                painter.setPen(QtGui.QPen(emblem_pen, 1.4))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QPointF(ex, ey), 3.8, 3.8)
                painter.setBrush(emblem_pen)
                painter.drawEllipse(QtCore.QPointF(ex, ey), 1.2, 1.2)
                painter.drawLine(QtCore.QPointF(ex - 5.8, ey), QtCore.QPointF(ex - 2.4, ey))
                painter.drawLine(QtCore.QPointF(ex + 2.4, ey), QtCore.QPointF(ex + 5.8, ey))
                painter.drawLine(QtCore.QPointF(ex, ey - 5.8), QtCore.QPointF(ex, ey - 2.4))
                painter.drawLine(QtCore.QPointF(ex, ey + 2.4), QtCore.QPointF(ex, ey + 5.8))
            else:  # balanced
                # Split circle
                painter.setPen(QtGui.QPen(emblem_pen, 1.0))
                painter.setBrush(emblem_pen)
                painter.drawPie(QtCore.QRectF(ex - 3.8, ey - 3.8, 7.6, 7.6), 90 * 16, 180 * 16)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawLine(QtCore.QPointF(ex, ey - 3.8), QtCore.QPointF(ex, ey + 3.8))
        elif action == "wait":
            painter.drawEllipse(QtCore.QPointF(cx, cy), 12.0, 12.0)
            painter.drawLine(QtCore.QLineF(cx, cy, cx, cy - 7))
            painter.drawLine(QtCore.QLineF(cx, cy, cx + 5, cy))
        elif action == "type_text":
            painter.drawRoundedRect(QtCore.QRectF(cx - 15, cy - 11, 30, 22), 4, 4)
            painter.setFont(QtGui.QFont("Segoe UI", 11, QtGui.QFont.Bold))
            painter.setPen(pen_color)
            painter.drawText(QtCore.QRectF(cx - 15, cy - 11, 30, 22), QtCore.Qt.AlignCenter, "T")
        elif action == "datetime_condition":
            painter.drawRoundedRect(QtCore.QRectF(cx - 13, cy - 12, 26, 24), 3, 3)
            painter.drawLine(QtCore.QLineF(cx - 13, cy - 5, cx + 13, cy - 5))
            painter.fillRect(QtCore.QRectF(cx - 13, cy - 12, 26, 7), pen_color)
        elif action == "browser_action":
            painter.drawRoundedRect(QtCore.QRectF(cx - 15, cy - 12, 30, 24), 4, 4)
            painter.drawLine(QtCore.QLineF(cx - 15, cy - 6, cx + 15, cy - 6))
            painter.drawEllipse(QtCore.QPointF(cx, cy + 4), 5.5, 5.5)
        elif action == "ocr":
            painter.drawRoundedRect(QtCore.QRectF(cx - 16, cy - 11, 32, 22), 3, 3)
            painter.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold))
            painter.setPen(pen_color)
            painter.drawText(QtCore.QRectF(cx - 16, cy - 11, 32, 22), QtCore.Qt.AlignCenter, "OCR")
        elif action.startswith("table"):
            painter.drawRoundedRect(QtCore.QRectF(cx - 13, cy - 11, 26, 22), 3, 3)
            painter.drawLine(QtCore.QLineF(cx - 13, cy - 4, cx + 13, cy - 4))
            painter.drawLine(QtCore.QLineF(cx - 13, cy + 3, cx + 13, cy + 3))
            painter.drawLine(QtCore.QLineF(cx, cy - 11, cx, cy + 11))
        elif action in {"set_var", "calc_var"}:
            painter.setFont(QtGui.QFont("Segoe UI", 12, QtGui.QFont.Bold))
            painter.setPen(pen_color)
            painter.drawText(QtCore.QRectF(cx - 16, cy - 13, 32, 26), QtCore.Qt.AlignCenter, "{x}")
        elif action == "pixel_search":
            path = QtGui.QPainterPath()
            path.moveTo(cx, cy - 12)
            path.lineTo(cx + 12, cy)
            path.lineTo(cx, cy + 12)
            path.lineTo(cx - 12, cy)
            path.closeSubpath()
            painter.drawPath(path)
            painter.setBrush(pen_color)
            painter.drawEllipse(QtCore.QPointF(cx, cy), 3.5, 3.5)
        elif action == "multi_pixel_check":
            painter.setBrush(pen_color)
            painter.drawEllipse(QtCore.QPointF(cx - 8, cy - 7), 3.5, 3.5)
            painter.drawEllipse(QtCore.QPointF(cx + 8, cy - 7), 3.5, 3.5)
            painter.drawEllipse(QtCore.QPointF(cx, cy + 6), 4.0, 4.0)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(QtCore.QRectF(cx - 15, cy - 12, 30, 24), 5, 5)
        elif action == "wait_color":
            painter.drawEllipse(QtCore.QPointF(cx, cy), 11.0, 11.0)
            painter.drawLine(QtCore.QLineF(cx, cy, cx, cy - 6))
            painter.drawLine(QtCore.QLineF(cx, cy, cx + 5, cy))
            painter.setBrush(pen_color)
            painter.drawEllipse(QtCore.QPointF(cx + 8, cy + 8), 3.5, 3.5)
        elif action == "color_ratio":
            painter.drawRoundedRect(QtCore.QRectF(cx - 15, cy - 9, 30, 18), 3, 3)
            painter.fillRect(QtCore.QRectF(cx - 15, cy - 9, 18, 18), pen_color)
            painter.setFont(QtGui.QFont("Segoe UI", 7, QtGui.QFont.Bold))
            painter.setPen(QtGui.QColor("#FFFFFF"))
            painter.drawText(QtCore.QRectF(cx - 15, cy - 9, 30, 18), QtCore.Qt.AlignCenter, "%")
        elif action == "ocr_tracking":
            painter.drawEllipse(QtCore.QPointF(cx, cy), 11.0, 11.0)
            painter.drawLine(QtCore.QLineF(cx - 15, cy, cx + 15, cy))
            painter.drawLine(QtCore.QLineF(cx, cy - 15, cx, cy + 15))
            painter.setBrush(pen_color)
            painter.drawEllipse(QtCore.QPointF(cx, cy), 3.0, 3.0)
        elif action == "coord_mode":
            painter.drawEllipse(QtCore.QPointF(cx, cy), 10.0, 10.0)
            painter.drawLine(QtCore.QLineF(cx - 14, cy, cx + 14, cy))
            painter.drawLine(QtCore.QLineF(cx, cy - 14, cx, cy + 14))
        elif action == "remote_notify":
            path = QtGui.QPainterPath()
            path.arcMoveTo(cx - 8, cy - 9, 16, 16, 0)
            path.arcTo(cx - 8, cy - 9, 16, 16, 0, 180)
            path.lineTo(cx + 9, cy + 4)
            path.lineTo(cx - 9, cy + 4)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawEllipse(QtCore.QPointF(cx, cy + 6), 2.5, 2.5)
        elif action == "call_submacro":
            painter.drawEllipse(QtCore.QPointF(cx, cy), 10.0, 10.0)
            painter.drawLine(QtCore.QLineF(cx + 6, cy - 6, cx + 11, cy - 1))
        else:
            painter.drawRoundedRect(QtCore.QRectF(cx - 12, cy - 12, 24, 24), 4, 4)
            painter.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold))
            painter.setPen(pen_color)
            painter.drawText(QtCore.QRectF(cx - 12, cy - 12, 24, 24), QtCore.Qt.AlignCenter, "★")

        painter.restore()

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        _widget=None,
    ) -> None:
        action = str(self.step.get("action") or "step")
        action_title = self.display_title
        badge, accent = ("MULTI", "#38E7FF") if getattr(self, "is_multi", False) else ACTION_STYLES.get(action, ("STEP", COLORS["muted"]))
        selected = self.isSelected()
        running = self.index == self.canvas.active_step
        execution = self.canvas.execution_states.get(self.index, {})
        execution_status = str(execution.get("status") or "").upper()
        needs_setup = bool(self.step.get("needs_setup"))
        candidate_position = self.canvas.start_candidate_position(self.index)

        transform = painter.worldTransform()
        zoom = transform.m11()
        font_boost = min(1.28, (1.0 / zoom) ** 0.35) if zoom < 0.85 else 1.0
        line_boost = max(1.0, 1.0 / zoom) if zoom < 0.85 else 1.0

        w = self.current_width()
        h = self.current_height()
        rect = QtCore.QRectF(0, 0, w, h)

        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setBrush(QtGui.QColor("#151929"))
        border_color = (
            "#38E7FF"
            if running
            else COLORS["success"]
            if execution_status == "SUCCESS"
            else COLORS["danger"]
            if execution_status == "FAIL"
            else "#FF9D45"
            if needs_setup
            else COLORS["accent"]
            if selected
            else COLORS["warning"]
            if candidate_position
            else "#363E54"
        )
        border_width = 3.8 if running else (2.5 if needs_setup else 2.2 if selected else max(1.6, 1.2 * line_boost ** 0.5))
        if running:
            painter.setPen(QtGui.QPen(QtGui.QColor(56, 231, 255, 75), 9))
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 12, 12)
        painter.setPen(QtGui.QPen(QtGui.QColor(border_color), border_width))
        painter.drawRoundedRect(rect, 10, 10)

        # Header Bar (28px height, vibrant action color)
        header_path = QtGui.QPainterPath()
        header_path.addRoundedRect(QtCore.QRectF(0, 0, w, 28), 10, 10)
        header_path.addRect(QtCore.QRectF(0, 16, w, 12))
        header_color = QtGui.QColor(accent)
        header_alpha = min(115, int(75 / (zoom ** 0.4))) if zoom < 0.85 else (65 if not selected else 95)
        header_color.setAlpha(header_alpha)
        painter.fillPath(header_path, header_color)

        # Step Number Badge (Vibrant purple rounded rectangle)
        num_font_size = max(8, int(8.5 * font_boost))
        num_font = QtGui.QFont("Segoe UI", num_font_size, QtGui.QFont.Bold)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#6F62D9"))
        painter.drawRoundedRect(QtCore.QRectF(5, 4, 22, 20), 5, 5)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.setFont(num_font)
        painter.drawText(QtCore.QRectF(5, 4, 22, 20), QtCore.Qt.AlignCenter, str(self.index))

        # Action Title (Crisp white bold title)
        title_font_size = max(9, int(9.5 * font_boost))
        title_font = QtGui.QFont("Malgun Gothic", title_font_size, QtGui.QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        title_metrics = QtGui.QFontMetrics(title_font)
        raw_tw = title_metrics.horizontalAdvance(action_title)
        max_tw = w - 46
        title_w = min(raw_tw, max_tw)
        painter.drawText(
            QtCore.QRectF(31, 2, title_w, 24),
            QtCore.Qt.AlignVCenter,
            title_metrics.elidedText(action_title, QtCore.Qt.ElideRight, int(title_w)),
        )

        # Collapse Button Arrow
        if self.collapsed:
            expand_font = QtGui.QFont("Segoe UI Symbol", max(11, int(11 * font_boost)), QtGui.QFont.Bold)
            painter.setFont(expand_font)
            painter.setPen(QtGui.QColor("#5ED9FF"))
            painter.drawText(QtCore.QRectF(w - 22, 2, 18, 24), QtCore.Qt.AlignCenter, "▸")
        else:
            collapse_x = 31 + title_w + 3
            collapse_font = QtGui.QFont("Segoe UI Symbol", max(9, int(9.5 * font_boost)), QtGui.QFont.Bold)
            painter.setFont(collapse_font)
            painter.setPen(QtGui.QColor("#B8C0D0"))
            painter.drawText(QtCore.QRectF(collapse_x, 3, 16, 22), QtCore.Qt.AlignCenter, "▾")

        if self.collapsed:
            self._draw_action_icon(painter, action, w / 2, 44, zoom)
            return

        # Center Vector Icon
        self._draw_action_icon(painter, action, w / 2, 48, zoom)

        # Summary Text below icon
        summary = self.canvas.step_summary(self.step)
        summary_font_size = max(8, int(8.5 * font_boost))
        summary_font = QtGui.QFont("Malgun Gothic", summary_font_size)
        painter.setFont(summary_font)
        painter.setPen(QtGui.QColor("#DCE5F5"))
        metrics = QtGui.QFontMetrics(summary_font)
        painter.drawText(
            QtCore.QRectF(6, 70, w - 12, 18),
            QtCore.Qt.AlignCenter,
            metrics.elidedText(summary, QtCore.Qt.ElideRight, int(w - 12)),
        )

        # Bottom Badges (Center-aligned below text: Branch, Engine, Preset)
        automation = self.step.get("_automation") if isinstance(self.step.get("_automation"), dict) else {}
        has_branch_badge = bool(automation.get("branch_chain") or self.step.get("on_fail"))
        has_engine_badge = action in {"image_search", "screen_condition"} or getattr(self, "is_multi", False)

        badge_items = []
        badge_font = QtGui.QFont("Segoe UI", max(7, int(7.5 * font_boost)), QtGui.QFont.Bold)
        b_metrics = QtGui.QFontMetrics(badge_font)

        if has_branch_badge:
            pos = automation.get("candidate_position")
            total = automation.get("candidate_count")
            b_text = f"🔀 {pos}/{total}" if pos and total else "🔀 분기"
            bw = b_metrics.horizontalAdvance(b_text) + 10
            def draw_branch(bx, by, w=bw, txt=b_text):
                painter.setFont(badge_font)
                painter.setPen(QtGui.QPen(QtGui.QColor("#FF9E64"), 1))
                painter.setBrush(QtGui.QColor("#2B1E16"))
                painter.drawRoundedRect(QtCore.QRectF(bx, by, w, 17), 3, 3)
                painter.setPen(QtGui.QColor("#FF9E64"))
                painter.drawText(QtCore.QRectF(bx, by, w, 17), QtCore.Qt.AlignCenter, txt)
            badge_items.append((bw, draw_branch))

        if has_engine_badge:
            engine, preset = self._get_engine_and_preset()
            engine_text = "AHK" if engine == "ahk" else "OpenCV"
            e_color = QtGui.QColor("#60A5FA") if engine == "ahk" else QtGui.QColor("#34D399")
            e_bg = QtGui.QColor("#102030") if engine == "ahk" else QtGui.QColor("#102A1C")
            e_border = QtGui.QColor("#3B82F6") if engine == "ahk" else QtGui.QColor("#10B981")

            p_symbol, p_color, p_bg, p_border = {
                "ultra_fast": ("⚡", QtGui.QColor("#22C55E"), QtGui.QColor("#102A1C"), QtGui.QColor("#22C55E")),
                "balanced": ("◐", QtGui.QColor("#C084FC"), QtGui.QColor("#251838"), QtGui.QColor("#A855F7")),
                "precise": ("🎯", QtGui.QColor("#FBBF24"), QtGui.QColor("#2E210E"), QtGui.QColor("#F59E0B")),
            }.get(preset, ("⚡", QtGui.QColor("#22C55E"), QtGui.QColor("#102A1C"), QtGui.QColor("#22C55E")))

            ew = b_metrics.horizontalAdvance(engine_text) + 10
            def draw_engine(ex, ey, w=ew, txt=engine_text, c=e_color, bg=e_bg, bc=e_border):
                painter.setFont(badge_font)
                painter.setPen(QtGui.QPen(bc, 1.0))
                painter.setBrush(bg)
                painter.drawRoundedRect(QtCore.QRectF(ex, ey, w, 17), 3, 3)
                painter.setPen(c)
                painter.drawText(QtCore.QRectF(ex, ey, w, 17), QtCore.Qt.AlignCenter, txt)
            badge_items.append((ew, draw_engine))

            pw = 18
            def draw_preset(px, py, w=pw, sym=p_symbol, c=p_color, bg=p_bg, bc=p_border):
                painter.setFont(badge_font)
                painter.setPen(QtGui.QPen(bc, 1.0))
                painter.setBrush(bg)
                painter.drawRoundedRect(QtCore.QRectF(px, py, w, 17), 3, 3)
                painter.setPen(c)
                painter.drawText(QtCore.QRectF(px, py, w, 17), QtCore.Qt.AlignCenter, sym)
            badge_items.append((pw, draw_preset))

        if badge_items:
            spacing = 4
            total_bw = sum(item[0] for item in badge_items) + spacing * (len(badge_items) - 1)
            start_x = max(6.0, (w - total_bw) / 2)
            badge_y = 91
            curr_x = start_x
            for bw, draw_fn in badge_items:
                draw_fn(curr_x, badge_y)
                curr_x += bw + spacing

        if self.index == self.canvas.start_step:
            painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["success"]), 2.2))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)
        if self.index == self.canvas.end_step:
            painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["danger"]), 2.2))
            painter.drawLine(QtCore.QLineF(6, h - 3, w - 6, h - 3))
        if running:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#38E7FF"))
            badge_w = min(74, w - 12)
            painter.drawRoundedRect(QtCore.QRectF((w - badge_w) / 2, h - 18, badge_w, 15), 5, 5)
            running_font = QtGui.QFont("Malgun Gothic", max(7, int(7.5 * font_boost)), QtGui.QFont.Bold)
            painter.setFont(running_font)
            painter.setPen(QtGui.QColor("#061116"))
            painter.drawText(QtCore.QRectF((w - badge_w) / 2, h - 18, badge_w, 15), QtCore.Qt.AlignCenter, "실행 중")
        elif execution_status in {"SUCCESS", "FAIL"}:
            painter.setPen(QtCore.Qt.NoPen)
            result_color = QtGui.QColor(COLORS["success"] if execution_status == "SUCCESS" else COLORS["danger"])
            painter.setBrush(result_color)
            badge_w = min(78, w - 12)
            painter.drawRoundedRect(QtCore.QRectF((w - badge_w) / 2, h - 18, badge_w, 15), 5, 5)
            result_font = QtGui.QFont("Malgun Gothic", max(7, int(7.5 * font_boost)), QtGui.QFont.Bold)
            painter.setFont(result_font)
            painter.setPen(QtGui.QColor("#07110E" if execution_status == "SUCCESS" else "#18070B"))
            painter.drawText(
                QtCore.QRectF((w - badge_w) / 2, h - 18, badge_w, 15),
                QtCore.Qt.AlignCenter,
                f"{'성공' if execution_status == 'SUCCESS' else '실패'} {int(execution.get('duration_ms') or 0)}ms",
            )
        elif needs_setup:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#FF9D45"))
            badge_w = min(80, w - 12)
            painter.drawRoundedRect(QtCore.QRectF((w - badge_w) / 2, h - 18, badge_w, 15), 5, 5)
            warning_font = QtGui.QFont("Malgun Gothic", max(7, int(7.5 * font_boost)), QtGui.QFont.Bold)
            painter.setFont(warning_font)
            painter.setPen(QtGui.QColor("#1B0D02"))
            painter.drawText(QtCore.QRectF((w - badge_w) / 2, h - 18, badge_w, 15), QtCore.Qt.AlignCenter, "⚠ 설정 필요")

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged and not self.canvas.suspended:
            self.canvas.node_moved()
        if change == QtWidgets.QGraphicsItem.ItemSelectedHasChanged:
            self.update()
            if bool(value) and not self.canvas.suspended and not self.canvas.rubber_selecting:
                self.canvas.node_selected.emit(self.index)
        return result

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if not self.canvas.suspended and hasattr(self.canvas, "check_node_group_membership"):
            self.canvas.check_node_group_membership(self)

    def _edit_title_dialog(self, screen_pos: QtCore.QPointF | QtCore.QPoint) -> None:
        dialog = QtWidgets.QDialog(self.canvas.window())
        dialog.setWindowTitle(f"{self.index}번 노드 이름 변경")
        dialog.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.FramelessWindowHint)
        dialog.setAttribute(QtCore.Qt.WA_InputMethodEnabled, True)
        dialog.setStyleSheet("""
            QDialog { background: #171A24; border: 2px solid #5ED9FF; border-radius: 8px; }
            QLabel { color: #E2E8F0; font-weight: bold; font-size: 9.5pt; }
            QLineEdit { background: #0E1118; color: #FFFFFF; border: 1px solid #3B455C; border-radius: 5px; padding: 6px; font-size: 10pt; }
            QPushButton { background: #2B5A8A; color: white; border-radius: 5px; padding: 5px 12px; font-weight: bold; }
            QPushButton:hover { background: #3878B5; }
        """)
        vbox = QtWidgets.QVBoxLayout(dialog)
        vbox.setContentsMargins(12, 12, 12, 12)
        vbox.setSpacing(8)
        lbl = QtWidgets.QLabel(f"✏️ {self.index}번 노드 이름 수정")
        vbox.addWidget(lbl)
        edit = QtWidgets.QLineEdit()
        edit.setAttribute(QtCore.Qt.WA_InputMethodEnabled, True)
        edit.setInputMethodHints(QtCore.Qt.ImhNone)
        cur_text = str(self.step.get("label") or self.step.get("name") or self.display_title).strip()
        edit.setText(cur_text)
        edit.selectAll()
        vbox.addWidget(edit)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_save = QtWidgets.QPushButton("확인 (Enter)")
        btn_save.clicked.connect(dialog.accept)
        btn_cancel = QtWidgets.QPushButton("취소 (Esc)")
        btn_cancel.setStyleSheet("background: #202636; color: #9DA7BA;")
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        vbox.addLayout(btn_row)
        edit.returnPressed.connect(dialog.accept)

        pos = screen_pos.toPoint() if hasattr(screen_pos, "toPoint") else QtCore.QPoint(int(screen_pos.x()), int(screen_pos.y()))
        dialog.adjustSize()
        dialog.move(pos.x() - 20, pos.y() - 15)
        edit.setFocus()

        if dialog.exec() == QtWidgets.QDialog.Accepted:
            new_title = edit.text().strip()
            self.step["label"] = new_title
            self.display_title = new_title if new_title else ACTION_TITLES.get(str(self.step.get("action") or "step"), "step")
            self.update()
            if hasattr(self.canvas, "node_title_changed"):
                self.canvas.node_title_changed.emit(self.index, new_title)

    def mouseDoubleClickEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        point = event.pos()
        if 0 <= point.y() <= 28:
            self._edit_title_dialog(event.screenPos())
            event.accept()
            return
        self.canvas.node_selected.emit(self.index)
        self.canvas.inspector_requested.emit(self.index)
        event.accept()

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        point = event.pos()
        w = self.current_width()
        if event.button() == QtCore.Qt.LeftButton and 0 <= point.y() <= 28:
            should_toggle = False
            if self.collapsed:
                # In collapsed state, clicking anywhere in header (x >= 28) expands it
                should_toggle = point.x() >= 28
            else:
                # In expanded state, check click next to title (where ▾ is drawn)
                action_title = self.display_title
                title_font = QtGui.QFont("Malgun Gothic", 9, QtGui.QFont.Bold)
                title_metrics = QtGui.QFontMetrics(title_font)
                title_w = min(title_metrics.horizontalAdvance(action_title), w - 60)
                collapse_x = 31 + title_w + 3
                if collapse_x - 6 <= point.x() <= collapse_x + 22:
                    should_toggle = True
            if should_toggle:
                selected = self.canvas.selected_indexes()
                indexes = selected if self.index in selected and len(selected) > 1 else [self.index]
                self.canvas.set_nodes_collapsed(indexes, not self.collapsed)
                event.accept()
                return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event: QtWidgets.QGraphicsSceneContextMenuEvent) -> None:
        if not self.isSelected():
            self.canvas.select_node(self.index)
        menu = QtWidgets.QMenu()
        wait_indexes = [
            index
            for index in self.canvas.selected_indexes()
            if 0 < index <= len(self.canvas.steps) and self.canvas.steps[index - 1].get("action") == "wait"
        ]
        change_wait = None
        change_all_wait = None
        if self.step.get("action") == "wait":
            label = "대기시간 변경" if len(wait_indexes) <= 1 else f"선택한 대기 노드 {len(wait_indexes)}개 시간 변경"
            change_wait = menu.addAction(label)
            change_all_wait = menu.addAction("모든 대기 노드 시간 변경")
            menu.addSeparator()
        selected_indexes = self.canvas.selected_indexes() or [self.index]
        act_rename = menu.addAction("✏️ 노드 이름 수정")
        menu.addSeparator()
        act_expand = menu.addAction(f"⊞ 선택 노드 {len(selected_indexes)}개 펼치기")
        act_collapse = menu.addAction(f"⊟ 선택 노드 {len(selected_indexes)}개 접기")
        act_box = menu.addAction(f"🗂️ 선택 노드 {len(selected_indexes)}개로 노드 그룹 묶기")

        current_group = None
        for g in getattr(self.canvas, "comments", []):
            if hasattr(g, "node_indexes") and self.index in g.node_indexes:
                current_group = g
                break

        act_leave_group = None
        act_split_to_new = None
        if current_group is not None:
            act_leave_group = menu.addAction(f"📤 노드 그룹 '{current_group.title}'에서 제외")
            act_split_to_new = menu.addAction(f"📤 현재 그룹에서 분리 (새 그룹으로 독립)")

        add_group_menu = None
        group_actions = {}
        other_groups = [
            g for g in getattr(self.canvas, "comments", [])
            if hasattr(g, "node_indexes") and g is not current_group
        ]
        if other_groups:
            add_group_menu = menu.addMenu("📥 기존 노드 그룹에 포함...")
            for og in other_groups:
                act_og = add_group_menu.addAction(f"🗂️ {og.title}")
                group_actions[act_og] = og

        align_sub = None
        act_al_left = None
        act_al_right = None
        act_al_top = None
        act_al_bottom = None
        act_dist_h = None
        act_dist_v = None
        if len(selected_indexes) >= 2:
            align_sub = menu.addMenu(f"📐 선택 노드 {len(selected_indexes)}개 일괄 정렬")
            act_al_left = align_sub.addAction("← 좌측 일렬 맞춤")
            act_al_right = align_sub.addAction("→ 우측 일렬 맞춤")
            act_al_top = align_sub.addAction("↑ 상단 수평 맞춤")
            act_al_bottom = align_sub.addAction("↓ 하단 수평 맞춤")
            align_sub.addSeparator()
            act_dist_h = align_sub.addAction("↔ 가로 간격 균등 분배")
            act_dist_v = align_sub.addAction("↕ 세로 간격 균등 분배")

        selected_image_nodes = [
            index
            for index in selected_indexes
            if index in self.canvas.nodes
            and str(self.canvas.steps[index - 1].get("action") or "") == "image_search"
        ]
        merge_multi = menu.addAction("▦ 선택 이미지 서치를 멀티 서치로 묶기")
        merge_multi.setEnabled(len(selected_image_nodes) >= 2 and len(selected_image_nodes) == len(selected_indexes))
        menu.addSeparator()
        if len(selected_indexes) >= 2:
            act_branch = menu.addAction(f"🔀 선택 노드 {len(selected_indexes)}개를 순차 분기로 연결 (실패 시 다음 분기)")
            act_branch.setToolTip("1번 실패 시 2번 실행 → 2번 실패 시 3번 실행하는 순차 분기 체인을 생성합니다.")
        else:
            act_branch = menu.addAction("🔀 실패 시 다음 분기 노드 지정...")
            act_branch.setToolTip("이 노드가 실패했을 때 실행할 다음 분기 노드를 선택합니다.")
        has_branch = any(
            bool(self.canvas.steps[idx - 1].get("on_fail"))
            for idx in selected_indexes
            if 0 < idx <= len(self.canvas.steps)
        )
        act_unbranch = menu.addAction(f"⛓️ 선택 노드 {len(selected_indexes)}개 분기 해제 (실패선 제거)") if has_branch else None
        menu.addSeparator()
        duplicate = menu.addAction("노드 복제")
        archive = menu.addAction("노드 보관")
        open_archive = menu.addAction("📦 노드 보관함 열기...")
        chosen = menu.exec(event.screenPos())
        if not chosen:
            event.accept()
            return
        if chosen == act_rename:
            self._edit_title_dialog(event.screenPos())
        elif chosen == act_expand:
            self.canvas.set_nodes_collapsed(selected_indexes, False)
        elif chosen == act_collapse:
            self.canvas.set_nodes_collapsed(selected_indexes, True)
        elif chosen == act_box:
            self.canvas.add_comment_box_for_selection()
        elif act_leave_group and chosen == act_leave_group:
            if current_group:
                for idx in selected_indexes:
                    current_group.remove_node(idx)
        elif act_split_to_new and chosen == act_split_to_new:
            if current_group:
                for idx in selected_indexes:
                    current_group.remove_node(idx)
                first_step = self.canvas.steps[selected_indexes[0] - 1] if 0 < selected_indexes[0] <= len(self.canvas.steps) else {}
                wf_label = str(first_step.get("workflow_label") or first_step.get("workflow_id") or "").strip()
                new_title = f"{wf_label} 그룹" if wf_label else "독립 노드 그룹"
                other_colors = [c for c in NODE_GROUP_THEMES.keys() if c != getattr(current_group, "color_name", "yellow")]
                new_color = other_colors[0] if other_colors else "blue"
                self.canvas.add_comment_box_for_selection(title=new_title, color=new_color)
        elif add_group_menu and chosen in group_actions:
            target_group = group_actions[chosen]
            for idx in selected_indexes:
                target_group.add_node(idx)
        elif chosen == act_al_left:
            self.canvas.align_selected_nodes("left")
        elif chosen == act_al_right:
            self.canvas.align_selected_nodes("right")
        elif chosen == act_al_top:
            self.canvas.align_selected_nodes("top")
        elif chosen == act_al_bottom:
            self.canvas.align_selected_nodes("bottom")
        elif chosen == act_dist_h:
            self.canvas.align_selected_nodes("distribute_h")
        elif chosen == act_dist_v:
            self.canvas.align_selected_nodes("distribute_v")
        elif chosen == act_branch:
            if len(selected_indexes) >= 2:
                self.canvas.branch_chain_requested.emit(selected_indexes)
            else:
                self.canvas.single_branch_requested.emit(self.index)
        elif act_unbranch is not None and chosen == act_unbranch:
            self.canvas.unbranch_requested.emit(selected_indexes)
        elif change_wait is not None and chosen == change_wait:
            self.canvas.wait_duration_requested.emit(wait_indexes or [self.index])
        elif change_all_wait is not None and chosen == change_all_wait:
            self.canvas.all_wait_duration_requested.emit()
        elif chosen == merge_multi:
            self.canvas.multi_image_merge_requested.emit(selected_image_nodes)
        elif chosen == duplicate:
            self.canvas.node_duplicate_requested.emit(self.index)
        elif chosen == archive:
            self.canvas.node_delete_requested.emit(self.index)
        elif chosen == open_archive:
            self.canvas.archive_requested.emit()
        event.accept()


class EdgeWaypointHandle(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, edge: "EdgeItem", index: int, seed: bool = False) -> None:
        super().__init__(-7, -7, 14, 14, edge)
        self.edge = edge
        self.index = index
        self.seed = seed
        self.setBrush(QtGui.QColor("#151B27"))
        self.setPen(QtGui.QPen(edge.color.lighter(145), 2.2))
        self.setCursor(QtCore.Qt.SizeAllCursor)
        self.setZValue(80)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setToolTip("드래그해 연결선 경로를 조정합니다.")

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self.edge._handle_dragging = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        super().mouseMoveEvent(event)
        self.edge._waypoint_moved(self)

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        self.edge._waypoint_moved(self)
        self.edge._handle_dragging = False
        self.edge.canvas.commit_manual_route(self.edge)


class EdgeItem(QtWidgets.QGraphicsPathItem):
    def __init__(
        self,
        canvas: "NodeCanvas",
        source: int,
        target: int,
        kind: str,
        condition_index: int = -1,
        rule: dict[str, Any] | None = None,
        candidate_index: int = 0,
    ) -> None:
        super().__init__()
        self.canvas = canvas
        self.source = int(source)
        self.target = int(target)
        self.kind = kind
        self.condition_index = condition_index
        self.candidate_index = candidate_index
        self.is_secondary_candidate = candidate_index > 0
        self.rule = rule or {}
        self.is_condition = condition_index >= 0
        is_return_flow = self.is_return_link()
        self.edge_type = "return" if is_return_flow else ("condition" if self.is_condition else "normal")

        if self.edge_type == "return":
            self.color = QtGui.QColor("#2CDBB8" if kind == "success" else "#F06292")
            line_style = QtCore.Qt.DashLine
            pen_width = 2.4
            self.setOpacity(0.38 if kind == "success" else 0.35)
        elif self.edge_type == "condition":
            self.color = QtGui.QColor(COLORS["warning"])
            line_style = QtCore.Qt.DashLine
            pen_width = 3.0
            self.setOpacity(0.50)
        else:
            self.color = QtGui.QColor(COLORS["success"] if kind == "success" else COLORS["danger"])
            line_style = QtCore.Qt.SolidLine
            pen_width = 2.6
            self.setOpacity(0.45)
        self.setPen(QtGui.QPen(self.color, pen_width, line_style, QtCore.Qt.RoundCap))
        self.setZValue(-1)
        self.setAcceptHoverEvents(True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.arrow = QtWidgets.QGraphicsPolygonItem(self)
        self.arrow.setBrush(self.color)
        self.arrow.setPen(QtCore.Qt.NoPen)
        self.target_arrow = QtWidgets.QGraphicsPolygonItem(self)
        self.target_arrow.setBrush(self.color)
        self.target_arrow.setPen(QtCore.Qt.NoPen)
        self.target_arrow.setVisible(False)
        self.label = QtWidgets.QGraphicsSimpleTextItem("", self)
        self.label.setBrush(self.color.lighter(130))
        self.label.setFont(QtGui.QFont("Malgun Gothic", 7))
        self.label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setToolTip("선을 빈 공간으로 드래그해 즉시 끊거나, 다른 노드로 드래그해 다시 연결합니다.")
        self._drag_origin = QtCore.QPointF()
        self._dragging = False
        self.route_side = ""
        self.route_lane = 0
        self.target_offset_y = 0.0
        self.manual_points = self.canvas.route_points(self)
        self._waypoint_handles: list[EdgeWaypointHandle] = []
        self._handle_dragging = False
        self._disposed = False
        self.update_path()

    def is_return_link(self) -> bool:
        if getattr(self, "is_condition", False):
            return False
        source_node = self.canvas.nodes.get(self.source)
        target_node = self.canvas.nodes.get(self.target)
        if source_node is None or target_node is None:
            return False
        # Routing follows the visible layout, not the numeric node order.
        # A lower-numbered node placed to the right is still a normal forward link.
        return target_node.sceneBoundingRect().center().x() <= source_node.sceneBoundingRect().center().x() + 4.0

    def is_row_wrap_link(self) -> bool:
        if self.is_return_link() or getattr(self, "is_condition", False):
            return False
        source_node = self.canvas.nodes.get(self.source)
        target_node = self.canvas.nodes.get(self.target)
        if not source_node or not target_node:
            return False
        src_rect = source_node.sceneBoundingRect()
        tgt_rect = target_node.sceneBoundingRect()
        # Row wrap: target is on a lower row and to the left of source
        return tgt_rect.top() > src_rect.bottom() - 15.0 and tgt_rect.center().x() < src_rect.center().x() - 10.0

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        widget=None,
    ) -> None:
        was_selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        if was_selected:
            option.state &= ~QtWidgets.QStyle.State_Selected
        transform = painter.worldTransform()
        zoom = transform.m11()
        if zoom < 0.85:
            orig_pen = self.pen()
            boost_w = max(orig_pen.widthF(), 2.2 / (zoom ** 0.5))
            painter.save()
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setPen(QtGui.QPen(orig_pen.color(), boost_w, orig_pen.style(), QtCore.Qt.RoundCap))
            painter.drawPath(self.path())
            painter.restore()
        else:
            super().paint(painter, option, widget)
        if was_selected:
            option.state |= QtWidgets.QStyle.State_Selected

    def itemChange(self, change: QtWidgets.QGraphicsItem.GraphicsItemChange, value: Any) -> Any:
        result = super().itemChange(change, value)
        if change == QtWidgets.QGraphicsItem.ItemSelectedHasChanged and not self._disposed:
            QtCore.QTimer.singleShot(0, self._sync_waypoint_handles)
        return result

    def dispose(self) -> None:
        """Invalidate queued Qt callbacks before the C++ graphics item dies."""
        if self._disposed:
            return
        self._disposed = True
        self._handle_dragging = False
        self._clear_waypoint_handles()
        for child in (getattr(self, "arrow", None), getattr(self, "target_arrow", None), getattr(self, "label", None)):
            if child is not None:
                try:
                    child.setParentItem(None)
                except (RuntimeError, AttributeError):
                    pass

    def _clear_waypoint_handles(self) -> None:
        for handle in self._waypoint_handles:
            try:
                handle.setParentItem(None)
                scene = handle.scene()
                if scene is not None:
                    scene.removeItem(handle)
            except RuntimeError:
                continue
        self._waypoint_handles = []

    def _sync_waypoint_handles(self) -> None:
        if self._disposed or self._handle_dragging:
            return
        try:
            selected = self.isSelected()
            scene = self.scene()
        except RuntimeError:
            self._disposed = True
            self._waypoint_handles = []
            return
        if not selected or scene is None:
            self._clear_waypoint_handles()
            return
        points = self.manual_points or [self.path().pointAtPercent(0.5)]
        seed = not bool(self.manual_points)
        valid_handles: list[EdgeWaypointHandle] = []
        for handle in self._waypoint_handles:
            try:
                handle.scene()
                valid_handles.append(handle)
            except RuntimeError:
                continue
        self._waypoint_handles = valid_handles
        if len(self._waypoint_handles) == len(points):
            for index, (handle, point) in enumerate(zip(self._waypoint_handles, points)):
                handle.index = index
                handle.seed = seed and index == 0
                handle.setPos(point)
            return
        self._clear_waypoint_handles()
        for index, point in enumerate(points):
            handle = EdgeWaypointHandle(self, index, seed=seed and index == 0)
            handle.setPos(point)
            self._waypoint_handles.append(handle)

    def _waypoint_moved(self, handle: EdgeWaypointHandle) -> None:
        point = QtCore.QPointF(handle.pos())
        if handle.seed:
            self.manual_points = [point]
            handle.seed = False
            handle.index = 0
        elif 0 <= handle.index < len(self.manual_points):
            self.manual_points[handle.index] = point
        self.update_path()

    def add_manual_point(self, point: QtCore.QPointF) -> None:
        self.manual_points.append(QtCore.QPointF(point))
        self.update_path()
        self.canvas.commit_manual_route(self)

    def clear_manual_points(self) -> None:
        self.manual_points = []
        self.canvas.commit_manual_route(self)

    def shape(self) -> QtGui.QPainterPath:
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(14)
        return stroker.createStroke(self.path())

    def update_path(self) -> None:
        if self._disposed:
            return
        source_node = self.canvas.nodes.get(self.source)
        target_node = self.canvas.nodes.get(self.target)
        if not source_node or not target_node:
            return

        is_return = self.is_return_link()

        if is_return:
            # Return Edge: Starts at source right port (success or fail),
            # Ends at target node Body Top Center (if success) or Body Bottom Center (if fail)
            source_rect = source_node.sceneBoundingRect()
            target_rect = target_node.sceneBoundingRect()
            tw = target_node.current_width()
            th = target_node.current_height()

            if self.kind == "success":
                out_port = source_node.out_success_port
                start = out_port.mapToScene(out_port.rect().center())
                end = target_node.mapToScene(QtCore.QPointF(tw / 2.0, 0.0))
                top_y = min(source_rect.top(), target_rect.top()) - 52.0 - getattr(self, "route_lane", 0) * 26.0
                exit_x = start.x() + 14.0
                enter_x = end.x()
                r = 12.0

                path = QtGui.QPainterPath(start)
                path.lineTo(exit_x, start.y())
                path.lineTo(exit_x, top_y + r)
                if exit_x > enter_x:
                    path.quadTo(exit_x, top_y, exit_x - r, top_y)
                    path.lineTo(enter_x + r, top_y)
                    path.quadTo(enter_x, top_y, enter_x, top_y + r)
                else:
                    path.quadTo(exit_x, top_y, exit_x + r, top_y)
                    path.lineTo(enter_x - r, top_y)
                    path.quadTo(enter_x, top_y, enter_x, top_y + r)
                path.lineTo(end)
                self.setPath(path)

                # Downward entry arrow (▼) into target top center body
                self.target_arrow.setVisible(True)
                size = 7.5
                polygon = QtGui.QPolygonF(
                    [QtCore.QPointF(0, 0), QtCore.QPointF(-size * 0.75, -size * 1.25), QtCore.QPointF(size * 0.75, -size * 1.25)]
                )
                transform = QtGui.QTransform()
                transform.translate(end.x(), end.y())
                self.target_arrow.setPolygon(transform.map(polygon))

                # Mid-path direction arrow (◄) along top horizontal return line
                mid_point = path.pointAtPercent(0.50)
                mid_angle = -path.angleAtPercent(0.50)
                mid_size = 7.5
                mid_polygon = QtGui.QPolygonF(
                    [QtCore.QPointF(0, 0), QtCore.QPointF(-mid_size, mid_size * 0.55), QtCore.QPointF(-mid_size, -mid_size * 0.55)]
                )
                mid_transform = QtGui.QTransform()
                mid_transform.translate(mid_point.x(), mid_point.y())
                mid_transform.rotate(mid_angle)
                self.arrow.setPolygon(mid_transform.map(mid_polygon))

                step = self.canvas.steps[self.source - 1]
                delay = int(step.get("on_success_delay") or 0)
                success_candidates = step.get("success_candidates") or []
                if isinstance(success_candidates, list) and self.target in success_candidates:
                    text = f"성공 후보 {success_candidates.index(self.target) + 1}/{len(success_candidates)} ({self.target}번 복귀)"
                else:
                    text = f"성공 시 {self.target}번으로 복귀"
                if delay:
                    text += f" · {delay}ms"
                self.label.setText(text)
                mid_x = (exit_x + enter_x) / 2.0
                self.label.setPos(QtCore.QPointF(mid_x - 36, top_y - 18))
                self._sync_waypoint_handles()
                return
            else:
                out_port = source_node.out_fail_port
                start = out_port.mapToScene(out_port.rect().center())
                end = target_node.mapToScene(QtCore.QPointF(tw / 2.0, th))
                bottom_y = max(source_rect.bottom(), target_rect.bottom()) + 52.0 + getattr(self, "route_lane", 0) * 26.0
                exit_x = start.x() + 14.0
                enter_x = end.x()
                r = 12.0

                path = QtGui.QPainterPath(start)
                path.lineTo(exit_x, start.y())
                path.lineTo(exit_x, bottom_y - r)
                if exit_x > enter_x:
                    path.quadTo(exit_x, bottom_y, exit_x - r, bottom_y)
                    path.lineTo(enter_x + r, bottom_y)
                    path.quadTo(enter_x, bottom_y, enter_x, bottom_y - r)
                else:
                    path.quadTo(exit_x, bottom_y, exit_x + r, bottom_y)
                    path.lineTo(enter_x - r, bottom_y)
                    path.quadTo(enter_x, bottom_y, enter_x, bottom_y - r)
                path.lineTo(end)
                self.setPath(path)

                # Upward entry arrow (▲) into target bottom center body
                self.target_arrow.setVisible(True)
                size = 7.5
                polygon = QtGui.QPolygonF(
                    [QtCore.QPointF(0, 0), QtCore.QPointF(-size * 0.75, size * 1.25), QtCore.QPointF(size * 0.75, size * 1.25)]
                )
                transform = QtGui.QTransform()
                transform.translate(end.x(), end.y())
                self.target_arrow.setPolygon(transform.map(polygon))

                # Mid-path direction arrow (◄) along bottom horizontal return line
                mid_point = path.pointAtPercent(0.50)
                mid_angle = -path.angleAtPercent(0.50)
                mid_size = 7.5
                mid_polygon = QtGui.QPolygonF(
                    [QtCore.QPointF(0, 0), QtCore.QPointF(-mid_size, mid_size * 0.55), QtCore.QPointF(-mid_size, -mid_size * 0.55)]
                )
                mid_transform = QtGui.QTransform()
                mid_transform.translate(mid_point.x(), mid_point.y())
                mid_transform.rotate(mid_angle)
                self.arrow.setPolygon(mid_transform.map(mid_polygon))

                step = self.canvas.steps[self.source - 1]
                delay = int(step.get("on_fail_delay") or 0)
                fail_candidates = step.get("fail_candidates") or []
                if isinstance(fail_candidates, list) and self.target in fail_candidates:
                    text = f"실패 후보 {fail_candidates.index(self.target) + 1}/{len(fail_candidates)} ({self.target}번 복귀)"
                else:
                    text = f"실패 시 {self.target}번으로 복귀"
                if delay:
                    text += f" · {delay}ms"
                self.label.setText(text)
                mid_x = (exit_x + enter_x) / 2.0
                self.label.setPos(QtCore.QPointF(mid_x - 36, bottom_y + 4))
                self._sync_waypoint_handles()
                return
        elif self.is_row_wrap_link():
            in_port = target_node.in_success_port if self.kind == "success" else target_node.in_fail_port
            end = in_port.mapToScene(in_port.rect().center())
            out_port = source_node.out_success_port if self.kind == "success" else source_node.out_fail_port
            start = out_port.mapToScene(out_port.rect().center())

            source_rect = source_node.sceneBoundingRect()
            target_rect = target_node.sceneBoundingRect()
            center_gutter_y = (source_rect.bottom() + target_rect.top()) / 2.0

            lane = getattr(self, "route_lane", 0)
            if self.kind == "success":
                # Upper track for success lines
                gutter_y = center_gutter_y - 14.0 - (lane * 18.0)
            else:
                # Lower track for fail lines
                gutter_y = center_gutter_y + 14.0 + (lane * 18.0)

            # Staggered exit and entry X so vertical lines do not overlap
            exit_x = start.x() + 18.0 + (lane * 14.0)
            enter_x = end.x() - 20.0 - (lane * 14.0)
            corner = 12.0

            path = QtGui.QPainterPath(start)
            path.lineTo(exit_x, start.y())
            path.quadTo(exit_x, gutter_y, exit_x - corner, gutter_y)
            path.lineTo(enter_x + corner, gutter_y)
            path.quadTo(enter_x, gutter_y, enter_x, end.y())
            path.lineTo(end)
            self.setPath(path)

            self.target_arrow.setVisible(False)
            point = path.pointAtPercent(0.50)
            angle = -path.angleAtPercent(0.50)
            size = 8.0
            polygon = QtGui.QPolygonF(
                [QtCore.QPointF(0, 0), QtCore.QPointF(-size, size * 0.55), QtCore.QPointF(-size, -size * 0.55)]
            )
            transform = QtGui.QTransform()
            transform.translate(point.x(), point.y())
            transform.rotate(angle)
            self.arrow.setPolygon(transform.map(polygon))

            step = self.canvas.steps[self.source - 1]
            delay_key = "on_success_delay" if self.kind == "success" else "on_fail_delay"
            delay = int(step.get(delay_key) or 0)
            text = "성공" if self.kind == "success" else "실패"
            if delay:
                text += f" · {delay}ms"
            self.label.setText(text)

            # Non-colliding label position: success at 35% above, fail at 65% below
            span_len = abs(exit_x - enter_x)
            left_x = min(exit_x, enter_x)
            if self.kind == "success":
                lx = left_x + span_len * 0.35 - 20.0
                ly = gutter_y - 18.0
            else:
                lx = left_x + span_len * 0.65 - 20.0
                ly = gutter_y + 4.0
            self.label.setPos(QtCore.QPointF(lx, ly))
            self._sync_waypoint_handles()
            return
        else:
            in_port = target_node.in_success_port if self.kind == "success" else target_node.in_fail_port
            end = in_port.mapToScene(in_port.rect().center())
            primary_edge = None
            if self.is_secondary_candidate and not self.is_condition:
                for other in self.canvas.edges:
                    if (
                        other is not self
                        and other.source == self.source
                        and other.kind == self.kind
                        and getattr(other, "candidate_index", 0) == 0
                        and not other.is_condition
                    ):
                        if other.path() and not other.path().isEmpty() and other.path().length() > 5.0:
                            primary_edge = other
                            break

            if primary_edge is not None:
                start = primary_edge.path().pointAtPercent(0.48)
            else:
                out_port = source_node.out_success_port if self.kind == "success" else source_node.out_fail_port
                start = out_port.mapToScene(out_port.rect().center())

        distance = abs(end.x() - start.x())
        if self.manual_points:
            path = QtGui.QPainterPath(start)
            for control in self.manual_points:
                path.lineTo(control)
            path.lineTo(end)
        elif self.route_side:
            # Backward routing for other non-return links
            source_rect = source_node.sceneBoundingRect()
            target_rect = target_node.sceneBoundingRect()
            span_left = min(start.x(), end.x())
            span_right = max(start.x(), end.x())
            corridor_top = min(source_rect.top(), target_rect.top()) - NodeItem.HEIGHT * 0.45
            corridor_bottom = max(source_rect.bottom(), target_rect.bottom()) + NodeItem.HEIGHT * 0.45
            route_rects = [
                node.sceneBoundingRect()
                for node in self.canvas.nodes.values()
                if node.sceneBoundingRect().right() >= span_left
                and node.sceneBoundingRect().left() <= span_right
                and node.sceneBoundingRect().bottom() >= corridor_top
                and node.sceneBoundingRect().top() <= corridor_bottom
            ]
            if not route_rects:
                route_rects = [source_rect, target_rect]
            margin = 52.0 + self.route_lane * 26.0
            if self.route_side == "top":
                lane_y = min(rect.top() for rect in route_rects) - margin
            else:
                lane_y = max(rect.bottom() for rect in route_rects) + margin
            exit_x = start.x() + 16.0
            enter_x = end.x() - 16.0
            corner = 10.0
            path = QtGui.QPainterPath(start)
            path.lineTo(exit_x, start.y())
            path.quadTo(exit_x, lane_y, exit_x + corner, lane_y)
            path.lineTo(enter_x - corner, lane_y)
            path.quadTo(enter_x, lane_y, enter_x, end.y())
            path.lineTo(end)
        else:
            # Shortest-distance smooth Bezier curve for forward & secondary candidate lines
            vertical = end.y() - start.y()
            bend = min(100.0, max(25.0, distance * 0.40 + abs(vertical) * 0.12))
            cand_offset = ((self.candidate_index - 1) * 18.0) if self.is_secondary_candidate else 0.0
            lane_offset = ((self.condition_index + 1) * 28.0) if self.is_condition else cand_offset
            c1 = QtCore.QPointF(start.x() + bend, start.y() + vertical * 0.12 + lane_offset)
            c2 = QtCore.QPointF(end.x() - bend, end.y() - vertical * 0.12)
            path = QtGui.QPainterPath(start)
            path.cubicTo(c1, c2, end)
        self.setPath(path)

        self.target_arrow.setVisible(False)
        point = path.pointAtPercent(0.55)
        angle = -path.angleAtPercent(0.55)
        size = 8.0
        polygon = QtGui.QPolygonF(
            [QtCore.QPointF(0, 0), QtCore.QPointF(-size, size * 0.55), QtCore.QPointF(-size, -size * 0.55)]
        )
        transform = QtGui.QTransform()
        transform.translate(point.x(), point.y())
        transform.rotate(angle)
        self.arrow.setPolygon(transform.map(polygon))

        if self.is_condition:
            source_name = "횟수" if self.rule.get("source", "edge_count") == "edge_count" else str(self.rule.get("variable") or "변수")
            operator = str(self.rule.get("operator") or ">=")
            value = self.rule.get("value", 1)
            custom_label = str(self.rule.get("label") or "").strip()
            text = custom_label or f"{source_name} {operator} {value}"
            if self.rule.get("reset_on_match"):
                text += " · 리셋"
        else:
            step = self.canvas.steps[self.source - 1]
            delay_key = "on_success_delay" if self.kind == "success" else "on_fail_delay"
            delay = int(step.get(delay_key) or 0)
            text = "성공" if self.kind == "success" else "실패"
            success_candidates = step.get("success_candidates") or []
            fail_candidates = step.get("fail_candidates") or []

            if self.kind == "success" and isinstance(success_candidates, list) and self.target in success_candidates:
                text = f"성공 후보 {success_candidates.index(self.target) + 1}/{len(success_candidates)}"
            elif self.kind == "fail" and isinstance(fail_candidates, list) and self.target in fail_candidates:
                text = f"실패 후보 {fail_candidates.index(self.target) + 1}/{len(fail_candidates)}"

            candidate_position = self.canvas.start_candidate_position(self.source)
            automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
            if self.kind == "fail" and automation.get("branch_chain"):
                text = "실패 → 다음 분기"
            elif candidate_position and not is_return:
                text = "탐지 성공" if self.kind == "success" else "미탐지 → 다음 후보"
            if delay:
                text += f" · {delay}ms"
        self.label.setText(text)
        label_y = 5 if self.route_side == "bottom" else -17
        if not self.route_side:
            label_y += self.target_offset_y * 1.15
        self.label.setPos(point + QtCore.QPointF(8, label_y))
        self._sync_waypoint_handles()

    def hoverEnterEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        self.setZValue(25)
        self.setOpacity(1.0)
        source_node = self.canvas.nodes.get(self.source)
        target_node = self.canvas.nodes.get(self.target)
        src_name = source_node.step.get("label") or getattr(source_node, "display_title", "노드") if source_node else f"{self.source}번"
        tgt_name = target_node.step.get("label") or getattr(target_node, "display_title", "노드") if target_node else f"{self.target}번"
        kind_str = "성공" if self.kind == "success" else "실패"
        self.setToolTip(
            f"<b>{self.source}번 [{src_name}] {kind_str}</b> → <b>{self.target}번 [{tgt_name}]</b><br>"
            "💡 <b>더블클릭 / 우클릭</b>: 딜레이 시간 및 분기 조건 추가<br>"
            "💡 <b>빈 곳으로 드래그</b>: 연결선 즉시 삭제"
        )

        highlight = QtGui.QColor("#38E7FF" if self.kind == "success" else "#FF5252" if self.kind == "fail" else COLORS["warning"])
        style = QtCore.Qt.DashLine if getattr(self, "edge_type", "") == "return" or self.is_condition else QtCore.Qt.SolidLine
        self.setPen(QtGui.QPen(highlight, 4.2, style, QtCore.Qt.RoundCap))
        self.arrow.setBrush(highlight)
        if hasattr(self, "target_arrow"):
            self.target_arrow.setBrush(highlight)
        self.label.setBrush(highlight)
        if source_node:
            source_node.update()
        if target_node:
            target_node.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        selected = self.canvas.selected_indexes()
        if not selected:
            base_opacity = 0.38 if getattr(self, "edge_type", "") == "return" and self.kind == "success" else (0.35 if getattr(self, "edge_type", "") == "return" else (0.50 if self.is_condition else 0.45))
            self.setZValue(-1)
        elif self.source in selected or self.target in selected:
            base_opacity = 1.0
            self.setZValue(15)
        else:
            base_opacity = 0.18
            self.setZValue(-2)
        self.setOpacity(base_opacity)
        style = QtCore.Qt.DashLine if getattr(self, "edge_type", "") == "return" or self.is_condition else QtCore.Qt.SolidLine
        pen_width = 2.4 if getattr(self, "edge_type", "") == "return" else (3.0 if self.is_condition else 2.6)
        self.setPen(QtGui.QPen(self.color, pen_width, style, QtCore.Qt.RoundCap))
        self.arrow.setBrush(self.color)
        if hasattr(self, "target_arrow"):
            self.target_arrow.setBrush(self.color)
        self.label.setBrush(self.color.lighter(130))
        source_node = self.canvas.nodes.get(self.source)
        target_node = self.canvas.nodes.get(self.target)
        if source_node:
            source_node.update()
        if target_node:
            target_node.update()
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self.canvas.edge_delay_requested.emit(self.source, self.target, self.kind)
        event.accept()

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_origin = event.scenePos()
            self._dragging = False
            self.setCursor(QtCore.Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.buttons() & QtCore.Qt.LeftButton:
            distance = (event.scenePos() - self._drag_origin).manhattanLength()
            if not self._dragging and distance >= 8:
                self._dragging = True
                self.canvas.begin_edge_drag(self)
            if self._dragging:
                self.canvas.update_link(event.scenePos())
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if self._dragging and event.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            scene_pos = QtCore.QPointF(event.scenePos())
            canvas = self.canvas
            edge = self
            event.accept()
            QtCore.QTimer.singleShot(0, lambda: canvas.end_edge_drag(edge, scene_pos))
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QtWidgets.QGraphicsSceneContextMenuEvent) -> None:
        menu = QtWidgets.QMenu()
        delay = menu.addAction("연결 설정 · 딜레이와 조건 분기")
        add_point = menu.addAction("＋ 이 위치에 경유점 추가")
        reset_route = menu.addAction("수동 경로 초기화")
        reset_route.setEnabled(bool(self.manual_points))
        remove = menu.addAction("연결 끊기")
        chosen = menu.exec(event.screenPos())
        if not chosen:
            event.accept()
            return
        if chosen == delay:
            self.canvas.edge_delay_requested.emit(self.source, self.target, self.kind)
        elif chosen == add_point:
            self.add_manual_point(event.scenePos())
        elif chosen == reset_route:
            self.clear_manual_points()
        elif chosen == remove:
            if self.is_condition:
                self.canvas.edge_condition_delete_requested.emit(self.source, self.condition_index)
            else:
                self.canvas.remove_edge(self.source, self.target, self.kind)
                self.canvas.edge_delete_requested.emit(self.source, self.target, self.kind)
        event.accept()


class _MinimapPreviewArea(QtWidgets.QWidget):
    def __init__(self, minimap: "NodeMinimapWidget") -> None:
        super().__init__(minimap)
        self.minimap = minimap
        self.setMouseTracking(True)
        self._dragging = False
        self._transform_params: tuple[float, float, float, float, float] | None = None
        self.setToolTip("클릭하거나 드래그하여 해당 화면 위치로 즉시 이동합니다.")

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        canvas = self.minimap.canvas
        if not canvas or not canvas.scene:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QtGui.QColor("#0D111A"))

        nodes = canvas.nodes
        if not nodes:
            painter.setPen(QtGui.QColor("#55607A"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "노드 없음")
            painter.end()
            return

        min_x = min(node.sceneBoundingRect().left() for node in nodes.values())
        min_y = min(node.sceneBoundingRect().top() for node in nodes.values())
        max_x = max(node.sceneBoundingRect().right() for node in nodes.values())
        max_y = max(node.sceneBoundingRect().bottom() for node in nodes.values())

        if canvas.trigger_node:
            t_br = canvas.trigger_node.sceneBoundingRect()
            min_x = min(min_x, t_br.left())
            min_y = min(min_y, t_br.top())
            max_x = max(max_x, t_br.right())
            max_y = max(max_y, t_br.bottom())

        for comment in list(getattr(canvas, "comments", [])):
            try:
                c_br = comment.sceneBoundingRect()
                min_x = min(min_x, c_br.left())
                min_y = min(min_y, c_br.top())
                max_x = max(max_x, c_br.right())
                max_y = max(max_y, c_br.bottom())
            except (RuntimeError, AttributeError):
                pass

        # Anchor scene bounds strictly to graph nodes/comments (do NOT include view_rect)
        # This keeps the nodes static on the minimap while scrolling!
        margin = 140.0
        scene_left = min_x - margin
        scene_top = min_y - margin
        scene_w = max(200.0, (max_x - min_x) + margin * 2)
        scene_h = max(140.0, (max_y - min_y) + margin * 2)

        pad = 6.0
        avail_w = max(10.0, w - pad * 2)
        avail_h = max(10.0, h - pad * 2)
        scale = min(avail_w / scene_w, avail_h / scene_h)
        off_x = pad + (avail_w - scene_w * scale) / 2.0
        off_y = pad + (avail_h - scene_h * scale) / 2.0

        self._transform_params = (scene_left, scene_top, scale, off_x, off_y)

        # Border
        painter.setPen(QtGui.QPen(QtGui.QColor("#1A2234"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Draw comment boxes
        for c in list(getattr(canvas, "comments", [])):
            try:
                cr = c.sceneBoundingRect()
                cx = off_x + (cr.left() - scene_left) * scale
                cy = off_y + (cr.top() - scene_top) * scale
                cw = max(6.0, cr.width() * scale)
                ch = max(4.0, cr.height() * scale)
                theme = COMMENT_THEMES.get(c.color_name, COMMENT_THEMES.get("yellow", {}))
                fill_c = QtGui.QColor(theme.get("bg", QtGui.QColor(100, 116, 139, 28)))
                fill_c.setAlpha(55)
                painter.setBrush(QtGui.QBrush(fill_c))
                painter.setPen(QtGui.QPen(QtGui.QColor(theme.get("border", "#64748B")), 1, QtCore.Qt.DashLine))
                painter.drawRoundedRect(QtCore.QRectF(cx, cy, cw, ch), 2, 2)
            except (RuntimeError, AttributeError):
                pass

        # Draw edges
        painter.setPen(QtGui.QPen(QtGui.QColor("#243048"), 1))
        for edge in canvas.edges:
            src_node = nodes.get(edge.source)
            tgt_node = nodes.get(edge.target)
            if src_node and tgt_node:
                sp = src_node.sceneBoundingRect().center()
                tp = tgt_node.sceneBoundingRect().center()
                sx = off_x + (sp.x() - scene_left) * scale
                sy = off_y + (sp.y() - scene_top) * scale
                tx = off_x + (tp.x() - scene_left) * scale
                ty = off_y + (tp.y() - scene_top) * scale
                painter.drawLine(QtCore.QPointF(sx, sy), QtCore.QPointF(tx, ty))

        # Draw nodes
        active_step = canvas.active_step
        for idx, node in nodes.items():
            br = node.sceneBoundingRect()
            nx = off_x + (br.left() - scene_left) * scale
            ny = off_y + (br.top() - scene_top) * scale
            nw = max(4.0, br.width() * scale)
            nh = max(3.0, br.height() * scale)

            if idx == active_step:
                fill_col = QtGui.QColor("#4ADE80")
                border_col = QtGui.QColor("#22C55E")
            elif node.isSelected():
                fill_col = QtGui.QColor("#5ED9FF")
                border_col = QtGui.QColor("#38BDF8")
            else:
                fill_col = QtGui.QColor("#1F2738")
                border_col = QtGui.QColor("#384561")

            painter.setBrush(QtGui.QBrush(fill_col))
            painter.setPen(QtGui.QPen(border_col, 1))
            painter.drawRoundedRect(QtCore.QRectF(nx, ny, nw, nh), 1.5, 1.5)

        # Draw viewport rect (camera rectangle smoothly moving over the static minimap)
        view_poly = canvas.view.mapToScene(canvas.view.viewport().rect())
        view_rect = view_poly.boundingRect()
        vx = off_x + (view_rect.left() - scene_left) * scale
        vy = off_y + (view_rect.top() - scene_top) * scale
        vw = max(6.0, view_rect.width() * scale)
        vh = max(4.0, view_rect.height() * scale)

        painter.setBrush(QtGui.QBrush(QtGui.QColor(0, 229, 255, 35)))
        painter.setPen(QtGui.QPen(QtGui.QColor("#00E5FF"), 1.5))
        painter.drawRect(QtCore.QRectF(vx, vy, vw, vh))

        painter.end()

    def _nav_to_pos(self, pt: QtCore.QPoint) -> None:
        if not self._transform_params:
            return
        scene_left, scene_top, scale, off_x, off_y = self._transform_params
        if scale <= 0:
            return
        scene_x = scene_left + (pt.x() - off_x) / scale
        scene_y = scene_top + (pt.y() - off_y) / scale
        self.minimap.canvas.view.centerOn(scene_x, scene_y)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self._nav_to_pos(event.pos())
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging and (event.buttons() & QtCore.Qt.LeftButton):
            self._nav_to_pos(event.pos())
            event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            event.accept()


class _MinimapHeader(QtWidgets.QWidget):
    def __init__(self, minimap: "NodeMinimapWidget") -> None:
        super().__init__(minimap)
        self.minimap = minimap
        self._dragging = False
        self._drag_start = QtCore.QPoint()
        self._pos_start = QtCore.QPoint()
        self.setCursor(QtCore.Qt.SizeAllCursor)

        h_layout = QtWidgets.QHBoxLayout(self)
        h_layout.setContentsMargins(8, 3, 6, 3)
        h_layout.setSpacing(4)
        title = QtWidgets.QLabel("⋮⋮ 🗺️ 미니맵")
        title.setStyleSheet("font-size: 11px; font-weight: 700; color: #94A3B8;")
        title.setToolTip("마우스로 잡고 드래그하여 화면 어디든 자유롭게 배치합니다.")

        self.btn_dock = QtWidgets.QToolButton(self)
        self.btn_dock.setText("⤢")
        self.btn_dock.setToolTip("노드 플로우 캔버스 모서리로 정렬")
        self.btn_dock.setStyleSheet(
            "QToolButton { font-size: 11px; color: #64748B; background: transparent; border: none; padding: 2px 4px; }"
            "QToolButton:hover { color: #5ED9FF; background: #1E2738; border-radius: 4px; }"
        )
        self.btn_dock.clicked.connect(self.minimap.reset_to_default_dock)

        self.btn_collapse = QtWidgets.QToolButton(self)
        self.btn_collapse.setText("▾ 접기")
        self.btn_collapse.setToolTip("미니맵 미리보기를 접어 작게 유지합니다.")
        self.btn_collapse.setStyleSheet(
            "QToolButton { font-size: 10px; color: #5ED9FF; background: transparent; border: none; padding: 2px 4px; }"
            "QToolButton:hover { color: #A5F3FC; background: #1E2738; border-radius: 4px; }"
        )
        self.btn_collapse.clicked.connect(self.minimap.toggle_collapse)

        self.btn_close = QtWidgets.QToolButton(self)
        self.btn_close.setText("✕")
        self.btn_close.setToolTip("미니맵 창 닫기 (상단 툴바의 [🗺️ 미니맵] 버튼으로 다시 열기)")
        self.btn_close.setStyleSheet(
            "QToolButton { font-size: 10px; color: #64748B; background: transparent; border: none; padding: 2px 4px; }"
            "QToolButton:hover { color: #EF4444; background: #1E2738; border-radius: 4px; }"
        )
        self.btn_close.clicked.connect(self.minimap.close_minimap)

        h_layout.addWidget(title)
        h_layout.addStretch(1)
        h_layout.addWidget(self.btn_dock)
        h_layout.addWidget(self.btn_collapse)
        h_layout.addWidget(self.btn_close)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self._drag_start = event.globalPosition().toPoint()
            self._pos_start = self.minimap.pos()
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging and (event.buttons() & QtCore.Qt.LeftButton):
            delta = event.globalPosition().toPoint() - self._drag_start
            new_pos = self._pos_start + delta
            self.minimap.set_custom_position(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            event.accept()


class NodeMinimapWidget(QtWidgets.QFrame):
    def __init__(self, canvas: "NodeCanvas") -> None:
        super().__init__(canvas, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        self.canvas = canvas
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setObjectName("NodeMinimapFloating")

        self.expanded = QtCore.QSettings("MacroRelay", "Studio").value("minimap_expanded", True, type=bool)
        saved_x = QtCore.QSettings("MacroRelay", "Studio").value("minimap_floating_x", None)
        saved_y = QtCore.QSettings("MacroRelay", "Studio").value("minimap_floating_y", None)
        self._custom_pos: QtCore.QPoint | None = None
        if saved_x is not None and saved_y is not None:
            try:
                self._custom_pos = QtCore.QPoint(int(saved_x), int(saved_y))
            except (ValueError, TypeError):
                self._custom_pos = None

        # Draggable header bar
        self.header = _MinimapHeader(self)

        # Preview area
        self.preview = _MinimapPreviewArea(self)

        # Layout
        self.layout_box = QtWidgets.QVBoxLayout(self)
        self.layout_box.setContentsMargins(4, 4, 4, 4)
        self.layout_box.setSpacing(2)
        self.layout_box.addWidget(self.header)
        self.layout_box.addWidget(self.preview, 1)

        self._apply_state()

    def set_custom_position(self, pos: QtCore.QPoint) -> None:
        self._custom_pos = pos
        self.move(pos)
        QtCore.QSettings("MacroRelay", "Studio").setValue("minimap_floating_x", pos.x())
        QtCore.QSettings("MacroRelay", "Studio").setValue("minimap_floating_y", pos.y())

    def reset_to_default_dock(self) -> None:
        self._custom_pos = None
        QtCore.QSettings("MacroRelay", "Studio").remove("minimap_floating_x")
        QtCore.QSettings("MacroRelay", "Studio").remove("minimap_floating_y")
        self.update_position()

    def toggle_collapse(self) -> None:
        self.expanded = not self.expanded
        QtCore.QSettings("MacroRelay", "Studio").setValue("minimap_expanded", self.expanded)
        self._apply_state()

    def close_minimap(self) -> None:
        self.hide()
        if hasattr(self.canvas, "minimap_btn"):
            self.canvas.minimap_btn.setChecked(False)
        QtCore.QSettings("MacroRelay", "Studio").setValue("minimap_visible", False)

    def _apply_state(self) -> None:
        if self.expanded:
            self.header.btn_collapse.setText("▾ 접기")
            self.preview.show()
            self.setStyleSheet(
                "QFrame#NodeMinimapFloating {"
                "  background: rgba(13, 17, 26, 0.96);"
                "  border: 1px solid #334155;"
                "  border-radius: 8px;"
                "}"
            )
            self.resize(220, 145)
        else:
            self.header.btn_collapse.setText("▲ 펼치기")
            self.preview.hide()
            self.setStyleSheet(
                "QFrame#NodeMinimapFloating {"
                "  background: rgba(13, 17, 26, 0.92);"
                "  border: 1px solid #334155;"
                "  border-radius: 6px;"
                "}"
            )
            self.resize(175, 32)
        self.update_position()
        if self.expanded:
            self.preview.update()

    def update_position(self) -> None:
        if self._custom_pos is not None:
            self.move(self._custom_pos)
            return
        if not self.canvas or not self.canvas.view:
            return
        try:
            view_global = self.canvas.view.mapToGlobal(QtCore.QPoint(0, 0))
            vw = self.canvas.view.width()
            vh = self.canvas.view.height()
            target_x = view_global.x() + vw - self.width() - 24
            target_y = view_global.y() + vh - self.height() - 24
            self.move(max(0, target_x), max(0, target_y))
        except Exception:
            pass


NODE_GROUP_THEMES: dict[str, dict[str, Any]] = {
    "yellow": {"name": "🟨 노랑 (골드)", "border": "#F59E0B", "header": "#D97706", "bg": QtGui.QColor(245, 158, 11, 28), "text": "#FFFBEB"},
    "blue": {"name": "🟦 파랑 (사파이어)", "border": "#3B82F6", "header": "#2563EB", "bg": QtGui.QColor(59, 130, 246, 28), "text": "#EFF6FF"},
    "green": {"name": "🟩 초록 (에메랄드)", "border": "#10B981", "header": "#059669", "bg": QtGui.QColor(16, 185, 129, 28), "text": "#ECFDF5"},
    "purple": {"name": "🟪 보라 (아메시스트)", "border": "#8B5CF6", "header": "#7C3AED", "bg": QtGui.QColor(139, 92, 246, 28), "text": "#F5F3FF"},
    "coral": {"name": "🟥 코랄 (루비)", "border": "#F43F5E", "header": "#E11D48", "bg": QtGui.QColor(244, 63, 94, 28), "text": "#FFF1F2"},
    "slate": {"name": "⬜ 그레이 (슬레이트)", "border": "#64748B", "header": "#475569", "bg": QtGui.QColor(100, 116, 139, 28), "text": "#F8FAFC"},
}
COMMENT_THEMES = NODE_GROUP_THEMES


class NodeGroupItem(QtWidgets.QGraphicsItem):
    HEADER_HEIGHT = 30.0
    MIN_WIDTH = 220.0
    MIN_HEIGHT = 140.0
    RESIZE_HANDLE_SIZE = 16.0

    def __init__(
        self,
        comment_id: str,
        title: str,
        color: str,
        rect: QtCore.QRectF,
        canvas: "NodeCanvas",
        node_indexes: list[int] | None = None,
    ) -> None:
        super().__init__()
        self.comment_id = comment_id or uuid.uuid4().hex[:8]
        self.title = title or "노드 그룹"
        self.color_name = color if color in NODE_GROUP_THEMES else "yellow"
        self.node_indexes: list[int] = [int(i) for i in (node_indexes or [])]
        self.box_rect = QtCore.QRectF(0, 0, max(self.MIN_WIDTH, rect.width()), max(self.MIN_HEIGHT, rect.height()))
        self.canvas = canvas

        self.setPos(rect.topLeft())
        self.setZValue(-40)  # Behind workflow lanes (-30), edges (-10) & nodes (0)
        self.setFlags(
            QtWidgets.QGraphicsItem.ItemIsMovable
            | QtWidgets.QGraphicsItem.ItemIsSelectable
            | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self._resizing = False
        self._resize_start = QtCore.QPointF()
        self._rect_start = QtCore.QRectF()
        self._dragging_group = False
        self._drag_start_scene = QtCore.QPointF()
        self._node_origins: dict[int, QtCore.QPointF] = {}
        self._hover_close = False
        self._hover_color = False

    def boundingRect(self) -> QtCore.QRectF:
        return self.box_rect.adjusted(-3, -3, 3, 3)

    def _close_btn_rect(self) -> QtCore.QRectF:
        r = self.box_rect
        return QtCore.QRectF(r.right() - 26, r.top() + 4, 20, 22)

    def _color_btn_rect(self) -> QtCore.QRectF:
        r = self.box_rect
        return QtCore.QRectF(r.right() - 50, r.top() + 4, 20, 22)

    def _is_in_close_btn(self, pos: QtCore.QPointF) -> bool:
        return self._close_btn_rect().contains(pos)

    def _is_in_color_btn(self, pos: QtCore.QPointF) -> bool:
        return self._color_btn_rect().contains(pos)

    def _is_in_resize_handle(self, pos: QtCore.QPointF) -> bool:
        if self.node_indexes:
            return False
        r = self.box_rect
        hr = QtCore.QRectF(r.right() - self.RESIZE_HANDLE_SIZE - 2, r.bottom() - self.RESIZE_HANDLE_SIZE - 2, self.RESIZE_HANDLE_SIZE + 4, self.RESIZE_HANDLE_SIZE + 4)
        return hr.contains(pos)

    def contains_node(self, node: NodeItem) -> bool:
        return self.sceneBoundingRect().intersects(node.sceneBoundingRect())

    def sync_rect(self) -> None:
        """Dynamically expand/contract box bounding rect to fit all member nodes and their workflow lanes."""
        if not self.node_indexes:
            return
        node_union = QtCore.QRectF()
        valid_nodes = []
        for idx in list(self.node_indexes):
            node = self.canvas.nodes.get(idx)
            if node is not None and node.isVisible():
                valid_nodes.append(node)
                node_union = node_union.united(node.sceneBoundingRect()) if not node_union.isNull() else node.sceneBoundingRect()
        if not valid_nodes or node_union.isNull():
            return

        # Check if any member nodes belong to workflow lanes (분기)
        member_idx_set = set(self.node_indexes)
        has_lane = False
        lane_union = QtCore.QRectF()
        for lane in getattr(self.canvas, "workflow_items", []):
            if any(i in member_idx_set for i in getattr(lane, "indexes", [])):
                if lane.isVisible() and hasattr(lane, "_rect") and not lane._rect.isNull():
                    has_lane = True
                    lane_union = lane_union.united(lane._rect) if not lane_union.isNull() else QtCore.QRectF(lane._rect)

        if has_lane and not lane_union.isNull():
            # Combine node bounds and branch lane bounds
            combined = node_union.united(lane_union)
            # Give comfortable outer padding around the branch lanes
            # Top: 44px (header 30px + 14px gap above branch header)
            # Bottom: 18px (clean margin below branch bottom dashed line)
            # Horizontal: 20px (clean margin left and right of branch dashed lines)
            pad_x = 20.0
            pad_top = 44.0
            pad_bottom = 18.0
            target = combined.adjusted(-pad_x, -pad_top, pad_x, pad_bottom)
        else:
            pad_x = 34.0
            pad_top = 46.0
            pad_bottom = 28.0
            target = node_union.adjusted(-pad_x, -pad_top, pad_x, pad_bottom)

        min_w = max(self.MIN_WIDTH, target.width())
        min_h = max(self.MIN_HEIGHT, target.height())

        self.prepareGeometryChange()
        self.setPos(target.topLeft())
        self.box_rect = QtCore.QRectF(0, 0, min_w, min_h)
        self.update()

    def add_node(self, node_index: int) -> None:
        if node_index not in self.node_indexes:
            self.node_indexes.append(node_index)
            self.sync_rect()
            self.canvas.comments_changed.emit(self.canvas.dump_comments())

    def remove_node(self, node_index: int) -> None:
        if node_index in self.node_indexes:
            self.node_indexes.remove(node_index)
            self.sync_rect()
            self.canvas.comments_changed.emit(self.canvas.dump_comments())

    def set_color(self, color_name: str) -> None:
        if color_name in NODE_GROUP_THEMES:
            self.color_name = color_name
            self.update()
            self.canvas.comments_changed.emit(self.canvas.dump_comments())

    def _show_color_popup(self, screen_pos: QtCore.QPointF | QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu()
        actions = {}
        for ck, cinfo in NODE_GROUP_THEMES.items():
            act = menu.addAction(cinfo["name"])
            actions[act] = ck
        chosen = menu.exec(screen_pos.toPoint() if isinstance(screen_pos, QtCore.QPointF) else screen_pos)
        if chosen in actions:
            self.set_color(actions[chosen])

    def paint(self, painter: QtGui.QPainter, option, widget=None) -> None:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        theme = NODE_GROUP_THEMES.get(self.color_name, NODE_GROUP_THEMES["yellow"])
        r = self.box_rect

        # 1. Body background
        painter.setBrush(QtGui.QBrush(theme["bg"]))
        border_pen = QtGui.QPen(QtGui.QColor(theme["border"]), 1.8 if self.isSelected() else 1.2, QtCore.Qt.SolidLine if self.isSelected() else QtCore.Qt.DashLine)
        painter.setPen(border_pen)
        painter.drawRoundedRect(r, 10.0, 10.0)

        # 2. Header bar
        header_rect = QtCore.QRectF(r.left(), r.top(), r.width(), self.HEADER_HEIGHT)
        header_path = QtGui.QPainterPath()
        header_path.addRoundedRect(header_rect, 10.0, 10.0)
        bottom_fill = QtCore.QRectF(r.left(), r.top() + self.HEADER_HEIGHT - 6.0, r.width(), 6.0)
        header_path.addRect(bottom_fill)
        painter.fillPath(header_path, QtGui.QColor(theme["header"]))

        # 3. Title text and node count badge
        painter.setPen(QtGui.QColor(theme["text"]))
        font = QtGui.QFont("Pretendard", 10, QtGui.QFont.Bold)
        painter.setFont(font)
        text_rect = header_rect.adjusted(10, 0, -60, 0)
        metrics = QtGui.QFontMetrics(font)
        title_str = f"🗂️ {self.title}"
        if self.node_indexes:
            title_str += f" ({len(self.node_indexes)}개)"
        painter.drawText(text_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, metrics.elidedText(title_str, QtCore.Qt.ElideRight, int(text_rect.width())))

        # 4. Color button [ 🎨 ]
        col_btn_r = self._color_btn_rect()
        if self._hover_color:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, 50))
            painter.drawRoundedRect(col_btn_r, 4, 4)
        btn_font = QtGui.QFont("Segoe UI Emoji", 9)
        painter.setFont(btn_font)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(col_btn_r, QtCore.Qt.AlignCenter, "🎨")

        # 5. Close button [ ✕ ]
        close_btn_r = self._close_btn_rect()
        if self._hover_close:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(239, 68, 68, 180))
            painter.drawRoundedRect(close_btn_r, 4, 4)
        close_font = QtGui.QFont("Pretendard", 10, QtGui.QFont.Bold)
        painter.setFont(close_font)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(close_btn_r, QtCore.Qt.AlignCenter, "✕")

        # 6. Resize handle (only when manual / no member nodes)
        if not self.node_indexes:
            hr = QtCore.QRectF(r.right() - self.RESIZE_HANDLE_SIZE, r.bottom() - self.RESIZE_HANDLE_SIZE, self.RESIZE_HANDLE_SIZE, self.RESIZE_HANDLE_SIZE)
            painter.setPen(QtGui.QPen(QtGui.QColor(theme["border"]), 1.5))
            painter.drawLine(QtCore.QPointF(hr.right() - 4, hr.bottom() - 11), QtCore.QPointF(hr.right() - 11, hr.bottom() - 4))
            painter.drawLine(QtCore.QPointF(hr.right() - 4, hr.bottom() - 6), QtCore.QPointF(hr.right() - 6, hr.bottom() - 4))

    def hoverMoveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        pos = event.pos()
        in_close = self._is_in_close_btn(pos)
        in_color = self._is_in_color_btn(pos)
        in_resize = self._is_in_resize_handle(pos)

        if in_close != self._hover_close or in_color != self._hover_color:
            self._hover_close = in_close
            self._hover_color = in_color
            self.update()

        if in_close or in_color:
            self.setCursor(QtCore.Qt.PointingHandCursor)
        elif in_resize:
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        elif pos.y() <= self.HEADER_HEIGHT:
            self.setCursor(QtCore.Qt.OpenHandCursor)
        else:
            self.setCursor(QtCore.Qt.ArrowCursor)
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        if self._hover_close or self._hover_color:
            self._hover_close = False
            self._hover_color = False
            self.update()
        super().hoverLeaveEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.canvas.remove_comment_box(self)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            pos = event.pos()
            # 1. Close button clicked
            if self._is_in_close_btn(pos):
                self.canvas.remove_comment_box(self)
                event.accept()
                return
            # 2. Color button clicked
            if self._is_in_color_btn(pos):
                self._show_color_popup(event.screenPos())
                event.accept()
                return
            # 3. Resize handle
            if self._is_in_resize_handle(pos):
                self._resizing = True
                self._resize_start = event.scenePos()
                self._rect_start = QtCore.QRectF(self.box_rect)
                event.accept()
                return
            # 4. Header dragged -> Move all member nodes together
            if pos.y() <= self.HEADER_HEIGHT:
                self._dragging_group = True
                self._drag_start_scene = event.scenePos()
                self._node_origins = {}
                for idx in self.node_indexes:
                    n = self.canvas.nodes.get(idx)
                    if n is not None:
                        self._node_origins[idx] = QtCore.QPointF(n.pos())
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                event.accept()
                return
            # 5. Body clicked -> Just select group without moving contained nodes
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self._resizing:
            delta = event.scenePos() - self._resize_start
            new_w = max(self.MIN_WIDTH, self._rect_start.width() + delta.x())
            new_h = max(self.MIN_HEIGHT, self._rect_start.height() + delta.y())
            self.prepareGeometryChange()
            self.box_rect = QtCore.QRectF(0, 0, new_w, new_h)
            self.update()
            event.accept()
            return

        if self._dragging_group:
            delta = event.scenePos() - self._drag_start_scene
            for idx, origin in self._node_origins.items():
                node = self.canvas.nodes.get(idx)
                if node:
                    node.setPos(origin + delta)
            self.canvas._route_edges()
            self.canvas._sync_workflow_lanes()
            self.sync_rect()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if self._resizing:
            self._resizing = False
            self.canvas.comments_changed.emit(self.canvas.dump_comments())
            event.accept()
            return

        if self._dragging_group:
            self._dragging_group = False
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self.canvas.node_moved()
            self.canvas.positions_changed.emit(self.canvas.positions())
            self.canvas.comments_changed.emit(self.canvas.dump_comments())
            event.accept()
            return

        super().mouseReleaseEvent(event)
        self.canvas.comments_changed.emit(self.canvas.dump_comments())

    def mouseDoubleClickEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.pos().y() <= self.HEADER_HEIGHT and not self._is_in_close_btn(event.pos()) and not self._is_in_color_btn(event.pos()):
            new_title, ok = QtWidgets.QInputDialog.getText(
                None, "노드 그룹 이름 수정", "새 노드 그룹 이름을 입력하세요:", text=self.title
            )
            if ok and new_title.strip():
                self.title = new_title.strip()
                self.update()
                self.canvas.comments_changed.emit(self.canvas.dump_comments())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: QtWidgets.QGraphicsSceneContextMenuEvent) -> None:
        menu = QtWidgets.QMenu()
        act_edit = menu.addAction("✏️ 그룹 이름 수정...")
        color_menu = menu.addMenu("🎨 색상 변경")
        color_actions = {}
        for c_key, c_info in NODE_GROUP_THEMES.items():
            color_actions[color_menu.addAction(c_info["name"])] = c_key

        selected_on_canvas = self.canvas.selected_indexes()
        uncontained = [idx for idx in selected_on_canvas if idx not in self.node_indexes]
        act_add_sel = None
        if uncontained:
            act_add_sel = menu.addAction(f"➕ 선택한 노드 {len(uncontained)}개를 이 그룹에 추가")

        # Check if group contains multiple branches
        member_wfs: dict[str, list[int]] = {}
        for idx in self.node_indexes:
            if 0 < idx <= len(self.canvas.steps):
                step = self.canvas.steps[idx - 1]
                wf_id = str(step.get("workflow_id") or "").strip()
                member_wfs.setdefault(wf_id, []).append(idx)

        act_split_branches = None
        if len(member_wfs) > 1:
            act_split_branches = menu.addAction(f"🔀 분기별로 그룹 분리 ({len(member_wfs)}개 분기로 분할)")
            act_split_branches.setToolTip("이 그룹에 섞여 있는 분기들을 각각 별도의 독립 그룹으로 자동 분할합니다.")

        menu.addSeparator()
        act_del = menu.addAction("🗑️ 노드 그룹 삭제 (Delete)")

        chosen = menu.exec(event.screenPos())
        if not chosen:
            event.accept()
            return
        if chosen == act_edit:
            new_title, ok = QtWidgets.QInputDialog.getText(
                None, "노드 그룹 이름 수정", "새 노드 그룹 이름을 입력하세요:", text=self.title
            )
            if ok and new_title.strip():
                self.title = new_title.strip()
                self.update()
                self.canvas.comments_changed.emit(self.canvas.dump_comments())
        elif chosen in color_actions:
            self.set_color(color_actions[chosen])
        elif act_add_sel is not None and chosen == act_add_sel:
            for idx in uncontained:
                self.add_node(idx)
        elif act_split_branches and chosen == act_split_branches:
            self.canvas.split_group_by_branches(self)
        elif chosen == act_del:
            self.canvas.remove_comment_box(self)
        event.accept()


GroupCommentBoxItem = NodeGroupItem


class NodeCanvas(QtWidgets.QWidget):
    node_selected = QtCore.Signal(int)
    inspector_requested = QtCore.Signal(int)
    positions_changed = QtCore.Signal(dict)
    routes_changed = QtCore.Signal(dict)
    link_requested = QtCore.Signal(int, int, str)
    edge_delete_requested = QtCore.Signal(int, int, str)
    edge_delay_requested = QtCore.Signal(int, int, str)
    edge_condition_delete_requested = QtCore.Signal(int, int)
    edge_condition_retarget_requested = QtCore.Signal(int, int, int)
    node_delete_requested = QtCore.Signal(int)
    node_duplicate_requested = QtCore.Signal(int)
    wait_duration_requested = QtCore.Signal(list)
    all_wait_duration_requested = QtCore.Signal()
    start_search_group_requested = QtCore.Signal(list)
    image_edit_requested = QtCore.Signal(int)
    collapsed_changed = QtCore.Signal(list)
    multi_image_merge_requested = QtCore.Signal(list)
    log_requested = QtCore.Signal()
    node_title_changed = QtCore.Signal(int, str)
    archive_requested = QtCore.Signal()
    branch_chain_requested = QtCore.Signal(list)
    single_branch_requested = QtCore.Signal(int)
    unbranch_requested = QtCore.Signal(list)
    help_requested = QtCore.Signal()
    comments_changed = QtCore.Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.suspended = False
        self.macro: dict[str, Any] = {}
        self.steps: list[dict[str, Any]] = []
        self.nodes: dict[int, NodeItem] = {}
        self.edges: list[EdgeItem] = []
        self.comments: list[GroupCommentBoxItem] = []
        self.workflow_items: list[WorkflowLaneItem] = []
        self.trigger_node: TriggerNodeItem | None = None
        self.trigger_edge: QtWidgets.QGraphicsPathItem | None = None
        self.manual_routes: dict[str, list[list[float]]] = {}
        self.start_step = 0
        self.start_candidates: list[int] = []
        self.end_step = 0
        self.active_step = 0
        self.execution_states: dict[int, dict[str, Any]] = {}
        self.collapsed_nodes: set[int] = set()
        self._temp_edge: QtWidgets.QGraphicsPathItem | None = None
        self._temp_start = QtCore.QPointF()
        self._retargeting_edge: tuple[int, int, str] | None = None
        self.rubber_selecting = False
        self._asset_preview_paths: dict[str, Path] = {}
        self._preview_popup = ImagePreviewPopup(self)
        self._tooltip_popup = NodeToolTipPopup(self)
        self._positions_timer = QtCore.QTimer(self)
        self._positions_timer.setSingleShot(True)
        self._positions_timer.setInterval(350)
        self._positions_timer.timeout.connect(lambda: self.positions_changed.emit(self.positions()))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        toolbar = QtWidgets.QWidget()
        toolbar.setObjectName("CanvasToolbar")
        toolbar_layout = QtWidgets.QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 8, 12, 8)
        toolbar_layout.setSpacing(7)
        self.flow_label = QtWidgets.QLabel("FLOW CANVAS")
        self.flow_label.setStyleSheet(f"font-weight:800; color:{COLORS['accent']}; letter-spacing:1px;")
        auto = QtWidgets.QPushButton("자동 정렬")
        auto.setToolTip("<b>자동 정렬</b><br>모든 노드를 100px 최적 간격으로 깔끔하게 자동 재배치합니다.")
        auto.clicked.connect(self.auto_layout)
        start_group = QtWidgets.QPushButton("시작 검색 묶기")
        start_group.setToolTip("<b>시작 검색 그룹 묶기</b><br>선택한 이미지 서치/OCR 노드 중 화면에 먼저 발견된 대상부터 자동 시작합니다.")
        start_group.clicked.connect(lambda: self.start_search_group_requested.emit(self.selected_indexes()))
        fit = QtWidgets.QPushButton("전체 보기")
        fit.setToolTip("<b>전체 보기</b><br>모든 노드가 한 화면에 쏙 들어오도록 시점과 배율을 맞춥니다.")
        fit.clicked.connect(self.fit_all)
        reset = QtWidgets.QPushButton("100%")
        reset.setToolTip("<b>줌 100% 초기화</b><br>캔버스 확대/축소 배율을 100% 표준 크기로 되돌립니다.")
        reset.clicked.connect(self.view_reset_zoom)
        self.zoom_label = QtWidgets.QLabel("100%")
        self.zoom_label.setObjectName("Muted")
        canvas_log_btn = QtWidgets.QPushButton("📋 성공·실패 로그")
        canvas_log_btn.setToolTip("<b>성공·실패 실행 로그</b><br>각 노드의 성공/실패 여부, 실패 시 쉬운 원인 분석과 개선 가이드를 실시간 확인합니다.")
        canvas_log_btn.clicked.connect(self.log_requested.emit)
        canvas_help_btn = QtWidgets.QPushButton("❓ 도움말")
        canvas_help_btn.setToolTip("<b>전체 사용 가이드 & 도움말 (F1)</b><br>노드 조작, 이미지 서치, 분기, 비활성 클릭, 단축키 등 모든 기능의 상세 설명서를 엽니다.")
        canvas_help_btn.setStyleSheet("font-weight: 700; color: #4ADE80; border-color: #2F4D3C;")
        canvas_help_btn.clicked.connect(self.help_requested.emit)
        legend = QtWidgets.QLabel(
            f"<span style='color:{COLORS['success']}'>● 성공</span>  "
            f"<span style='color:{COLORS['danger']}'>● 실패</span>  "
            "<span style='color:#9DA7BA'>· 선 선택→손잡이 드래그=경로 · 선 바깥 드롭=제거 · 바닥 더블클릭 유지=이동</span>"
        )
        legend.setObjectName("Muted")
        legend.setMinimumWidth(0)
        legend.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        toolbar_layout.addWidget(self.flow_label)
        toolbar_layout.addWidget(legend)
        toolbar_layout.addStretch(1)
        self.archive_btn = QtWidgets.QPushButton("📦 보관함")
        self.archive_btn.setToolTip("<b>노드 보관함 (📦)</b><br>삭제하거나 보관 처리한 노드 목록을 확인하고, 캔버스로 즉시 복원합니다.")
        self.archive_btn.clicked.connect(self.archive_requested.emit)
        toolbar_layout.addWidget(self.archive_btn)
        toolbar_layout.addWidget(canvas_log_btn)
        toolbar_layout.addWidget(canvas_help_btn)
        self.branch_btn = QtWidgets.QPushButton("🔀 순차 분기 묶기")
        self.branch_btn.setToolTip("<b>순차 분기 묶기 (스마트 분기)</b><br>선택한 노드들을 순차 분기 체인으로 연결합니다.<br>1번 실패 시 2번 실행 → 2번 실패 시 3번 실행<br>💡 <b>Zero-Wire</b>: 분기끼리는 실패선을 잇지 않아도 자동으로 다음 분기 첫 노드로 전환됩니다!")
        self.branch_btn.clicked.connect(lambda: self.branch_chain_requested.emit(self.selected_indexes()))
        toolbar_layout.addWidget(self.branch_btn)
        toolbar_layout.addWidget(start_group)

        self.align_btn = QtWidgets.QPushButton("📐 정렬 ▼")
        self.align_btn.setToolTip("<b>선택 노드 일괄 정렬</b><br>선택한 2개 이상의 노드를 상단/하단/좌측/우측/가로/세로로 단번에 정렬합니다.")
        align_menu = QtWidgets.QMenu(self.align_btn)
        act_a_top = align_menu.addAction("📐 상단 일렬 맞춤")
        act_a_bottom = align_menu.addAction("📐 하단 일렬 맞춤")
        act_a_left = align_menu.addAction("📐 좌측 일렬 맞춤")
        act_a_right = align_menu.addAction("📐 우측 일렬 맞춤")
        align_menu.addSeparator()
        act_dist_h = align_menu.addAction("↔ 가로 간격 균등 분배")
        act_dist_v = align_menu.addAction("↕ 세로 간격 균등 분배")
        act_a_top.triggered.connect(lambda: self.align_selected_nodes("top"))
        act_a_bottom.triggered.connect(lambda: self.align_selected_nodes("bottom"))
        act_a_left.triggered.connect(lambda: self.align_selected_nodes("left"))
        act_a_right.triggered.connect(lambda: self.align_selected_nodes("right"))
        act_dist_h.triggered.connect(lambda: self.align_selected_nodes("distribute_h"))
        act_dist_v.triggered.connect(lambda: self.align_selected_nodes("distribute_v"))
        self.align_btn.setMenu(align_menu)
        toolbar_layout.addWidget(self.align_btn)

        self.node_group_btn = QtWidgets.QPushButton("🗂️ 노드 그룹 ▼")
        self.node_group_btn.setToolTip("<b>노드 그룹 관리</b><br>선택한 노드들을 그룹으로 묶어 함께 이동하고 시각적으로 구분합니다.<br>노드가 이동하거나 추가되면 그룹 영역이 자동으로 늘어나고 줄어듭니다.")
        group_menu = QtWidgets.QMenu(self.node_group_btn)
        act_add_grp = group_menu.addAction("➕ 노드 그룹 생성 (노랑)")
        act_add_grp.triggered.connect(lambda: self._add_default_comment_box("yellow"))
        group_menu.addSeparator()
        color_sub = group_menu.addMenu("🎨 색상 지정 그룹 생성")
        for color_key, info in NODE_GROUP_THEMES.items():
            act_col = color_sub.addAction(info["name"])
            act_col.triggered.connect(lambda checked=False, ck=color_key: self._add_default_comment_box(ck))
        group_menu.addSeparator()
        act_split_by_branch = group_menu.addAction("🔀 모든 분기별 개별 그룹화 (1-클릭 분할)")
        act_split_by_branch.setToolTip("캔버스의 분기(Workflow Lane)들을 각각 독립된 노드 그룹으로 깔끔하게 분리 생성합니다.")
        act_split_by_branch.triggered.connect(self.auto_group_by_branches)
        group_menu.addSeparator()
        act_del_grp = group_menu.addAction("🗑️ 선택한 노드 그룹 삭제 (Delete)")
        act_del_grp.triggered.connect(self._delete_selected_group)
        self.node_group_btn.setMenu(group_menu)
        toolbar_layout.addWidget(self.node_group_btn)
        self.comment_box_btn = self.node_group_btn

        self.minimap_btn = QtWidgets.QPushButton("🗺️ 미니맵")
        self.minimap_btn.setCheckable(True)
        self.minimap_btn.setToolTip("<b>🗺️ 플로팅 미니맵 열기/닫기</b><br>캔버스 밖 독립 플로팅 창으로 전체 노드 맵을 표시합니다. 원하는 위치로 자유롭게 이동 및 접기가 가능합니다.")
        self.minimap_btn.toggled.connect(self._toggle_minimap)
        toolbar_layout.addWidget(self.minimap_btn)

        toolbar_layout.addWidget(auto)
        toolbar_layout.addWidget(fit)
        toolbar_layout.addWidget(reset)
        toolbar_layout.addWidget(self.zoom_label)
        self.scene = NodeGraphScene(self)
        self.scene.selectionChanged.connect(self._on_scene_selection_changed)
        self.view = NodeGraphView(self.scene, self)
        self.view.zoom_changed.connect(lambda value: self.zoom_label.setText(f"{value}%"))
        layout.addWidget(toolbar)
        layout.addWidget(self.view, 1)
        self.minimap = NodeMinimapWidget(self)
        is_minimap_on = QtCore.QSettings("MacroRelay", "Studio").value("minimap_visible", False, type=bool)
        self.minimap_btn.setChecked(is_minimap_on)
        if is_minimap_on:
            self.minimap.show()
        else:
            self.minimap.hide()

        self.view.horizontalScrollBar().valueChanged.connect(self._update_minimap_preview)
        self.view.verticalScrollBar().valueChanged.connect(self._update_minimap_preview)
        self.view.zoom_changed.connect(self._update_minimap_preview)

    def _update_minimap_preview(self) -> None:
        try:
            if hasattr(self, "minimap") and self.minimap:
                if self.minimap.isVisible() and getattr(self.minimap, "expanded", False):
                    self.minimap.preview.update()
        except (RuntimeError, AttributeError):
            pass

    def _toggle_minimap(self, visible: bool) -> None:
        try:
            if not hasattr(self, "minimap") or not self.minimap:
                return
            QtCore.QSettings("MacroRelay", "Studio").setValue("minimap_visible", visible)
            if visible:
                self.minimap.show()
                self.minimap.update_position()
                self._update_minimap_preview()
            else:
                self.minimap.hide()
        except (RuntimeError, AttributeError):
            pass

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        try:
            if hasattr(self, "minimap") and self.minimap and hasattr(self, "minimap_btn") and self.minimap_btn.isChecked():
                QtCore.QTimer.singleShot(60, self.minimap.show)
                QtCore.QTimer.singleShot(70, self.minimap.update_position)
        except (RuntimeError, AttributeError):
            pass

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        super().hideEvent(event)
        try:
            if hasattr(self, "minimap") and self.minimap:
                self.minimap.hide()
        except (RuntimeError, AttributeError):
            pass

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        super().closeEvent(event)
        try:
            if hasattr(self, "minimap") and self.minimap:
                self.minimap.hide()
        except (RuntimeError, AttributeError):
            pass

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        try:
            if hasattr(self, "minimap") and self.minimap and self.minimap.isVisible():
                if self.minimap._custom_pos is None:
                    self.minimap.update_position()
        except (RuntimeError, AttributeError):
            pass

    def update_archive_count(self, count: int) -> None:
        if hasattr(self, "archive_btn"):
            self.archive_btn.setText(f"📦 보관함 ({count})" if count > 0 else "📦 보관함")
            if count > 0:
                self.archive_btn.setStyleSheet("font-weight: 700; color: #5ED9FF; border: 1px solid #2B5A8A; background: #1B293A;")
            else:
                self.archive_btn.setStyleSheet("")

    @staticmethod
    def step_summary(step: dict[str, Any]) -> str:
        action = str(step.get("action") or "")
        label = str(step.get("label") or "").strip()
        if label:
            return label
        if action in {"image_search", "screen_condition"}:
            assets = step.get("assets") if isinstance(step.get("assets"), list) else []
            return f"멀티 이미지 {len(assets)}개" if len(assets) > 1 else str(step.get("asset") or "이미지 선택 필요")
        if action == "datetime_condition":
            if "weekday_enabled" in step:
                day_text = str(step.get("custom_days") or "요일 지정") if step.get("weekday_enabled") else "매일"
            else:
                legacy_days = {"everyday": "매일", "weekdays": "평일", "weekend": "주말", "custom": str(step.get("custom_days") or "요일 지정")}
                day_text = legacy_days.get(str(step.get("day_mode") or "everyday"), "매일")
            time_text = str(step.get("time_start") or "00:00")
            if bool(step.get("time_end_enabled", "time_end" in step)):
                time_text += f"~{step.get('time_end') or '23:59'}"
            if step.get("wait_until"):
                time_text += " · 대기"
            return f"{day_text} · {time_text}"
        if action == "ocr":
            ocr_action = str(step.get("ocr_action") or "extract")
            if ocr_action in {"find_text", "find_click", "find_click_offset"}:
                verb = "찾아 클릭" if "click" in ocr_action else "텍스트 찾기"
                return f"{verb} · {step.get('find_text') or '찾을 글자 필요'}"
            if ocr_action == "extract_number":
                return "숫자 추출"
            if ocr_action == "number_condition":
                labels = {"gte": "≥", "lte": "≤", "gt": ">", "lt": "<", "eq": "=", "neq": "≠"}
                return f"숫자 조건 · {labels.get(str(step.get('number_condition') or 'gte'), '?')} {step.get('number_value', 0)}"
            return f"텍스트 추출 · {step.get('profile') or '자동'}"
        if action == "type_text":
            return str(step.get("text") or "텍스트 입력")
        if action == "browser_action":
            return str(step.get("selector") or step.get("title") or "브라우저 액션")
        if action.startswith("table_"):
            return str(step.get("table") or step.get("path") or "데이터 작업")
        if action == "wait":
            return f"{int(step.get('duration') or 0)} ms 대기"
        if action in {"run_program", "terminate_program"}:
            return str(step.get("path") or step.get("process") or action)
        if action == "call_submacro":
            inputs = step.get("inputs") if isinstance(step.get("inputs"), dict) else {}
            result = str(step.get("result_var") or "").strip()
            signature = ", ".join(inputs) if inputs else "입력 없음"
            return f"{step.get('macro') or '선택 필요'}({signature})" + (f" → {result}" if result else "")
        return action or "단계"

    def set_macro(self, macro: dict[str, Any] | None, selected: int = 0) -> None:
        self.suspended = True
        self.hide_image_preview()
        self.active_step = 0
        self.execution_states = {}
        for edge in self.edges:
            try:
                edge.dispose()
            except RuntimeError:
                pass
        self.edges = []
        self.nodes = {}
        self.workflow_items = []
        self.comments = []
        self.trigger_node = None
        self.trigger_edge = None
        self.scene.clear()
        self.macro = macro or {}
        self.steps = list(self.macro.get("steps") or [])
        archived = (self.macro.get("meta") or {}).get("archived_steps") or []
        self.update_archive_count(len(archived))
        raw_routes = self.macro.get("graph_routes") or {}
        self.manual_routes = dict(raw_routes) if isinstance(raw_routes, dict) else {}
        raw_collapsed = self.macro.get("graph_collapsed") or []
        self.collapsed_nodes = {
            int(value)
            for value in raw_collapsed
            if str(value).lstrip("-").isdigit() and 0 < int(value) <= len(self.steps)
        } if isinstance(raw_collapsed, list) else set()
        self.start_step = int(self.macro.get("graph_start_step") or 0)
        raw_candidates = self.macro.get("start_search_candidates") or []
        self.start_candidates = []
        if isinstance(raw_candidates, list):
            for value in raw_candidates:
                try:
                    index = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 < index <= len(self.steps) and index not in self.start_candidates:
                    self.start_candidates.append(index)
        self.end_step = int(self.macro.get("graph_end_step") or 0)
        raw_positions = self.macro.get("graph_positions") or {}
        positions: dict[int, QtCore.QPointF] = {}
        if isinstance(raw_positions, dict):
            for key, value in raw_positions.items():
                try:
                    if isinstance(value, (list, tuple)) and len(value) >= 2:
                        positions[int(key)] = QtCore.QPointF(float(value[0]), float(value[1]))
                except (TypeError, ValueError):
                    continue
        suggested = self._suggest_positions()
        for index, step in enumerate(self.steps, start=1):
            node = NodeItem(index, step, self)
            node.setPos(positions.get(index, suggested[index]))
            self.scene.addItem(node)
            self.nodes[index] = node
        self._add_workflow_lanes()
        self.rebuild_edges()
        self._add_trigger_visual()
        self._load_comment_boxes()
        rect = self.scene.itemsBoundingRect()
        if rect.isEmpty():
            rect = QtCore.QRectF(-500, -350, 1000, 700)
        # Keep generous space around small graphs so double-click panning works
        # vertically as well as horizontally even when no scrollbars are shown.
        self.scene.setSceneRect(rect.adjusted(-2200, -1800, 2200, 1800))
        self.suspended = False
        if selected in self.nodes:
            self.select_node(selected)
        if self.nodes and not positions:
            QtCore.QTimer.singleShot(0, self.view.fit_all)

    def _add_trigger_visual(self) -> None:
        raw_triggers = self.macro.get("triggers") or []
        if not isinstance(raw_triggers, list) or not self.nodes:
            return
        trigger = next(
            (
                item
                for item in raw_triggers
                if isinstance(item, dict)
                and item.get("enabled", True)
                and str(item.get("type") or "").lower() in {"image_appear", "image_appears"}
            ),
            None,
        )
        if trigger is None:
            return
        target_index = self.start_step if self.start_step in self.nodes else (self.start_candidates[0] if self.start_candidates else 1)
        target = self.nodes.get(target_index) or self.nodes[min(self.nodes)]
        card = TriggerNodeItem(trigger, self)
        card.setPos(target.pos() + QtCore.QPointF(-TriggerNodeItem.WIDTH - 120, 8))
        self.scene.addItem(card)
        start = card.scenePos() + QtCore.QPointF(card.WIDTH, card.HEIGHT / 2)
        end = target.scenePos() + QtCore.QPointF(0, target.HEIGHT / 2)
        path = QtGui.QPainterPath(start)
        midpoint = (start.x() + end.x()) / 2
        path.cubicTo(midpoint, start.y(), midpoint, end.y(), end.x(), end.y())
        edge = QtWidgets.QGraphicsPathItem(path)
        pen = QtGui.QPen(QtGui.QColor("#A879FF"), 2.2, QtCore.Qt.DashLine)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        edge.setPen(pen)
        edge.setZValue(-3)
        edge.setToolTip("화면 트리거가 감지되면 이 시작 노드부터 실행합니다.")
        self.scene.addItem(edge)
        self.trigger_node = card
        self.trigger_edge = edge

    def set_asset_previews(self, previews: dict[Any, Any]) -> None:
        self._asset_preview_paths = {}
        self._asset_pixmaps = {}
        self._asset_search_regions: dict[str, list[int]] = {}
        if not isinstance(previews, dict):
            return
        for alias, item in previews.items():
            key = str(alias).strip()
            if not key:
                continue
            if isinstance(item, tuple) and len(item) == 2:
                pixmap_or_image, region = item
                if isinstance(pixmap_or_image, (QtGui.QPixmap, QtGui.QImage)):
                    pixmap = pixmap_or_image if isinstance(pixmap_or_image, QtGui.QPixmap) else QtGui.QPixmap.fromImage(pixmap_or_image)
                    if not pixmap.isNull():
                        self._asset_pixmaps[key] = pixmap
                    if isinstance(region, list) and len(region) >= 4:
                        self._asset_search_regions[key] = region
                continue
            if isinstance(item, (QtGui.QPixmap, QtGui.QImage)):
                pixmap = item if isinstance(item, QtGui.QPixmap) else QtGui.QPixmap.fromImage(item)
                if not pixmap.isNull():
                    self._asset_pixmaps[key] = pixmap
                continue
            if isinstance(item, (str, Path)):
                try:
                    p = Path(item).resolve()
                    if p.is_file():
                        self._asset_preview_paths[key] = p
                except Exception:
                    pass

    def start_candidate_position(self, index: int) -> tuple[int, int] | None:
        try:
            return self.start_candidates.index(int(index)) + 1, len(self.start_candidates)
        except ValueError:
            return None

    def asset_preview_path(self, alias: Any) -> Path | None:
        key = str(alias).strip()
        path = getattr(self, "_asset_preview_paths", {}).get(key)
        return path if path is not None and isinstance(path, Path) and path.is_file() else None

    def asset_preview_pixmap(self, alias: Any) -> QtGui.QPixmap | None:
        key = str(alias).strip()
        pixmap = getattr(self, "_asset_pixmaps", {}).get(key)
        if pixmap is not None and isinstance(pixmap, QtGui.QPixmap) and not pixmap.isNull():
            return pixmap
        path = self.asset_preview_path(alias)
        if path is not None:
            pm = QtGui.QPixmap(str(path))
            if not pm.isNull():
                return pm
        return None

    def asset_search_region(self, alias: Any) -> list[int] | None:
        key = str(alias).strip()
        return getattr(self, "_asset_search_regions", {}).get(key)

    def show_image_preview(self, entries: list[tuple[str, Path]], screen_pos: QtCore.QPoint) -> None:
        self._preview_popup.show_images(entries, screen_pos)

    def hide_image_preview(self) -> None:
        self._preview_popup.hide()

    def set_active_step(self, index: int) -> None:
        normalized = int(index) if int(index) in self.nodes else 0
        if normalized == self.active_step:
            return
        previous = self.nodes.get(self.active_step)
        self.active_step = normalized
        if previous is not None:
            previous.setZValue(0)
            previous.update()
        current = self.nodes.get(self.active_step)
        if current is not None:
            current.setZValue(20)
            current.update()

    def set_execution_states(self, states: dict[int, dict[str, Any]]) -> None:
        previous = set(self.execution_states)
        self.execution_states = {
            int(index): dict(value)
            for index, value in states.items()
            if int(index) in self.nodes and isinstance(value, dict)
        }
        for index in previous | set(self.execution_states):
            node = self.nodes.get(index)
            if node is not None:
                node.update()

    def clear_execution_states(self) -> None:
        self.set_execution_states({})

    def begin_rubber_selection(self) -> None:
        self.rubber_selecting = True

    def end_rubber_selection(self) -> None:
        if not self.rubber_selecting:
            return
        self.rubber_selecting = False
        selected = self.selected_indexes()
        if selected:
            # Update the inspector only once, after QGraphicsView has finalized
            # the complete rubber-band selection.
            self.node_selected.emit(selected[-1])

    def _suggest_positions(self) -> dict[int, QtCore.QPointF]:
        total = len(self.steps)
        if not total:
            return {}
        workflow_groups: list[tuple[str, list[int]]] = []
        workflow_lookup: dict[str, int] = {}
        for index, step in enumerate(self.steps, start=1):
            workflow_id = str(step.get("workflow_id") or "").strip()
            if not workflow_id:
                continue
            if workflow_id not in workflow_lookup:
                workflow_lookup[workflow_id] = len(workflow_groups)
                workflow_groups.append((workflow_id, []))
            workflow_groups[workflow_lookup[workflow_id]][1].append(index)
        if workflow_groups and sum(len(indexes) for _identifier, indexes in workflow_groups) == total:
            positions: dict[int, QtCore.QPointF] = {}
            MAX_PER_ROW = 10
            current_row = 0
            for _identifier, indexes in workflow_groups:
                for i in range(0, len(indexes), MAX_PER_ROW):
                    chunk = indexes[i : i + MAX_PER_ROW]
                    current_x = 0.0
                    for index in chunk:
                        col_width = 88.0 if index in self.collapsed_nodes else 196.0
                        positions[index] = QtCore.QPointF(current_x, current_row * 240.0)
                        current_x += col_width + 100.0
                    current_row += 1
            return positions
        outgoing: dict[int, list[int]] = {index: [] for index in range(1, total + 1)}
        indegree = {index: 0 for index in range(1, total + 1)}
        for index, step in enumerate(self.steps, start=1):
            targets: list[int] = []
            success_candidates = step.get("success_candidates") or []
            if isinstance(success_candidates, list) and success_candidates:
                targets.extend(int(value) for value in success_candidates if str(value).lstrip("-").isdigit())
            elif index < total and not bool(step.get("stop_on_success")):
                success = int(step.get("on_success") or 0)
                targets.append(success if success else index + 1)
            else:
                success = int(step.get("on_success") or 0)
                if success:
                    targets.append(success)
            failure = int(step.get("on_fail") or 0)
            if failure:
                targets.append(failure)
            for rule in step.get("edge_conditions") or []:
                if isinstance(rule, dict):
                    target = int(rule.get("target") or 0)
                    if target:
                        targets.append(target)
            for target in targets:
                if 0 < target <= total and target not in outgoing[index]:
                    outgoing[index].append(target)
                    indegree[target] += 1

        preferred_start = int(self.macro.get("graph_start_step") or 0)
        roots = ([preferred_start] if 0 < preferred_start <= total else []) + [
            index for index in range(1, total + 1) if indegree[index] == 0 and index != preferred_start
        ]
        ranks: dict[int, int] = {}
        pending: list[int] = []
        for root in roots:
            if root not in ranks:
                ranks[root] = 0
                pending.append(root)
        while pending:
            source = pending.pop(0)
            for target in outgoing[source]:
                if target not in ranks:
                    ranks[target] = ranks[source] + 1
                    pending.append(target)
        for index in range(1, total + 1):
            if index not in ranks:
                ranks[index] = 0

        columns: dict[int, list[int]] = {}
        for index in range(1, total + 1):
            columns.setdefault(ranks[index], []).append(index)
        positions: dict[int, QtCore.QPointF] = {}
        MAX_PER_ROW = 10
        all_wraps = sorted(set(rank // MAX_PER_ROW for rank in columns.keys()))
        for row_wrap in all_wraps:
            ranks_in_wrap = [r for r in columns.keys() if r // MAX_PER_ROW == row_wrap]
            current_x = 0.0
            for rank in sorted(ranks_in_wrap):
                indexes = columns[rank]
                center = (len(indexes) - 1) / 2.0
                col_width = max(
                    (88.0 if idx in self.collapsed_nodes else 196.0) for idx in indexes
                )
                for row, index in enumerate(indexes):
                    positions[index] = QtCore.QPointF(current_x, (row - center) * 190.0 + row_wrap * 260.0)
                current_x += col_width + 100.0
        return positions

    def positions(self) -> dict[str, list[float]]:
        return {
            str(index): [round(node.pos().x(), 2), round(node.pos().y(), 2)]
            for index, node in self.nodes.items()
        }

    def node_collapsed(self, index: int, collapsed: bool) -> None:
        if collapsed:
            self.collapsed_nodes.add(int(index))
        else:
            self.collapsed_nodes.discard(int(index))
        self._compact_row_spacing()
        self._route_edges()
        self._sync_workflow_lanes()
        self.collapsed_changed.emit(sorted(self.collapsed_nodes))
        self.positions_changed.emit(self.positions())

    def _compact_row_spacing(self) -> None:
        """Adjust horizontal spacing between adjacent nodes in each row so gaps stay consistent."""
        if not self.nodes:
            return
        rows: dict[int, list[NodeItem]] = {}
        for node in self.nodes.values():
            row_key = int(round(node.pos().y() / 150.0))
            rows.setdefault(row_key, []).append(node)
        for row_nodes in rows.values():
            if len(row_nodes) <= 1:
                continue
            row_nodes.sort(key=lambda n: n.pos().x())
            for i in range(1, len(row_nodes)):
                prev = row_nodes[i - 1]
                curr = row_nodes[i]
                desired_x = prev.pos().x() + prev.current_width() + 100.0
                curr.setPos(desired_x, curr.pos().y())

    def set_nodes_collapsed(self, indexes: list[int], collapsed: bool) -> None:
        changed = False
        self.suspended = True
        try:
            for index in indexes:
                node = self.nodes.get(int(index))
                if node is None or node.collapsed == bool(collapsed):
                    continue
                node.set_collapsed(collapsed, notify=False)
                if collapsed:
                    self.collapsed_nodes.add(int(index))
                else:
                    self.collapsed_nodes.discard(int(index))
                changed = True
        finally:
            self.suspended = False
        if changed:
            self._compact_row_spacing()
            self._route_edges()
            self._sync_workflow_lanes()
            self.collapsed_changed.emit(sorted(self.collapsed_nodes))
            self.positions_changed.emit(self.positions())

    @staticmethod
    def edge_route_key(edge: EdgeItem) -> str:
        return f"{edge.source}:{edge.kind}:{edge.target}:{edge.condition_index}"

    def route_points(self, edge: EdgeItem) -> list[QtCore.QPointF]:
        raw = self.manual_routes.get(self.edge_route_key(edge)) or []
        points: list[QtCore.QPointF] = []
        for value in raw if isinstance(raw, list) else []:
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                try:
                    points.append(QtCore.QPointF(float(value[0]), float(value[1])))
                except (TypeError, ValueError):
                    continue
        return points

    def commit_manual_route(self, edge: EdgeItem) -> None:
        key = self.edge_route_key(edge)
        if edge.manual_points:
            self.manual_routes[key] = [[round(point.x(), 2), round(point.y(), 2)] for point in edge.manual_points]
        else:
            self.manual_routes.pop(key, None)
        edge.update_path()
        self.routes_changed.emit(dict(self.manual_routes))

    def node_moved(self) -> None:
        self._route_edges()
        self._sync_workflow_lanes()
        self._sync_node_groups()
        self._positions_timer.start()
        if hasattr(self, "minimap") and self.minimap and self.minimap.expanded and self.minimap.isVisible():
            self.minimap.preview.update()

    def _sync_node_groups(self) -> None:
        for group in list(getattr(self, "comments", [])):
            try:
                if hasattr(group, "sync_rect"):
                    group.sync_rect()
            except Exception:
                pass

    def _add_workflow_lanes(self) -> None:
        groups: dict[str, list[int]] = {}
        labels: dict[str, str] = {}
        for index, step in enumerate(self.steps, start=1):
            workflow_id = str(step.get("workflow_id") or "").strip()
            if not workflow_id or index not in self.nodes:
                continue
            groups.setdefault(workflow_id, []).append(index)
            labels.setdefault(workflow_id, str(step.get("workflow_label") or workflow_id).strip())
        colors = ("#6F62D9", "#2C9E88", "#B37A39", "#A85586")
        for group_index, (workflow_id, indexes) in enumerate(groups.items()):
            color = QtGui.QColor(colors[group_index % len(colors)])
            lane = WorkflowLaneItem(self, group_index, workflow_id, labels[workflow_id], indexes, color)
            self.scene.addItem(lane)
            self.workflow_items.append(lane)
        self._sync_workflow_lanes()

    def _sync_workflow_lanes(self) -> None:
        for lane in self.workflow_items:
            lane.sync_rect()

    def rebuild_edges(self) -> None:
        for edge in self.edges:
            edge.dispose()
            try:
                if edge.scene() is self.scene:
                    self.scene.removeItem(edge)
            except RuntimeError:
                pass
        self.edges = []
        for index, step in enumerate(self.steps, start=1):
            # Success candidate edges
            candidates = step.get("success_candidates") or []
            candidate_targets = []
            if isinstance(candidates, list):
                candidate_targets = [int(value) for value in candidates if str(value).lstrip("-").isdigit()]
            for cand_idx, target in enumerate(candidate_targets):
                if index in self.nodes and target in self.nodes:
                    edge = EdgeItem(self, index, target, "success", candidate_index=cand_idx)
                    self.scene.addItem(edge)
                    self.edges.append(edge)

            # Fail candidate edges (실패 후보군)
            fail_cands = step.get("fail_candidates") or []
            fail_candidate_targets = []
            if isinstance(fail_cands, list):
                fail_candidate_targets = [int(value) for value in fail_cands if str(value).lstrip("-").isdigit()]
            for cand_idx, target in enumerate(fail_candidate_targets):
                if index in self.nodes and target in self.nodes:
                    edge = EdgeItem(self, index, target, "fail", candidate_index=cand_idx)
                    self.scene.addItem(edge)
                    self.edges.append(edge)

            for field, kind in (("on_success", "success"), ("on_fail", "fail")):
                target = int(step.get(field) or 0)
                automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
                if kind == "success" and candidate_targets:
                    continue
                if kind == "fail" and fail_candidate_targets:
                    continue
                if kind == "fail":
                    if bool(automation.get("hide_candidate_fail_edge")) or bool(automation.get("branch_chain")):
                        continue
                    source_wf = str(step.get("workflow_id") or "").strip()
                    target_step = self.steps[target - 1] if 0 < target <= len(self.steps) else {}
                    target_wf = str(target_step.get("workflow_id") or "").strip()
                    if source_wf and target_wf and source_wf != target_wf:
                        continue
                if index in self.nodes and target in self.nodes:
                    edge = EdgeItem(self, index, target, kind)
                    self.scene.addItem(edge)
                    self.edges.append(edge)
            conditions = step.get("edge_conditions") or []
            if isinstance(conditions, list):
                for condition_index, rule in enumerate(conditions):
                    if not isinstance(rule, dict):
                        continue
                    target = int(rule.get("target") or 0)
                    kind = str(rule.get("kind") or "success")
                    if index in self.nodes and target in self.nodes and kind in {"success", "fail"}:
                        edge = EdgeItem(self, index, target, kind, condition_index, rule)
                        self.scene.addItem(edge)
                        self.edges.append(edge)
        self._route_edges()

    def _route_edges(self) -> None:
        """Assign non-overlapping outer lanes to backward links, and route forward links via direct curves."""
        candidates: dict[str, list[tuple[float, float, EdgeItem]]] = {
            "top": [],
            "bottom": [],
            "gutter_success": [],
            "gutter_fail": [],
        }
        for edge in self.edges:
            edge.route_side = ""
            edge.route_lane = 0
            edge.target_offset_y = 0.0

        for edge in self.edges:
            if edge.manual_points:
                continue
            source = self.nodes.get(edge.source)
            target = self.nodes.get(edge.target)
            if source is None or target is None:
                continue
            out_port = source.out_success_port if edge.kind == "success" else source.out_fail_port
            start = out_port.mapToScene(out_port.rect().center())
            in_port = target.in_success_port if edge.kind == "success" else target.in_fail_port
            end = in_port.mapToScene(in_port.rect().center())
            if edge.is_return_link():
                side = "top" if edge.kind == "success" else "bottom"
                candidates[side].append((min(start.x(), end.x()), max(start.x(), end.x()), edge))
            elif edge.is_row_wrap_link():
                side = "gutter_success" if edge.kind == "success" else "gutter_fail"
                candidates[side].append((min(start.x(), end.x()), max(start.x(), end.x()), edge))
            else:
                needs_corridor = False
                x_min = min(start.x(), end.x())
                x_max = max(start.x(), end.x())
                y_min = min(start.y(), end.y()) - 25.0
                y_max = max(start.y(), end.y()) + 25.0
                for idx, node in self.nodes.items():
                    if idx == edge.source or idx == edge.target:
                        continue
                    nr = node.sceneBoundingRect()
                    if nr.right() > x_min + 15.0 and nr.left() < x_max - 15.0:
                        if nr.bottom() > y_min and nr.top() < y_max:
                            needs_corridor = True
                            break
                if needs_corridor:
                    side = "bottom" if edge.kind == "fail" else "top"
                    candidates[side].append((x_min, x_max, edge))

        for side, routed in candidates.items():
            lanes: list[list[tuple[float, float]]] = []
            for span_start, span_end, edge in sorted(
                routed,
                key=lambda item: (item[0], item[1], item[2].source, item[2].target, item[2].condition_index),
            ):
                lane = 0
                while lane < len(lanes):
                    overlaps = any(
                        not (span_end + 36.0 < used_start or span_start > used_end + 36.0)
                        for used_start, used_end in lanes[lane]
                    )
                    if not overlaps:
                        break
                    lane += 1
                if lane == len(lanes):
                    lanes.append([])
                lanes[lane].append((span_start, span_end))
                edge.route_side = side
                edge.route_lane = lane

        # Offset converging forward edges so their labels/paths do not overlap
        incoming_targets: dict[tuple[int, str], list[EdgeItem]] = {}
        for edge in self.edges:
            if not edge.route_side and not edge.is_return_link() and not edge.is_row_wrap_link():
                incoming_targets.setdefault((edge.target, edge.kind), []).append(edge)
        for (tgt, kind), in_edges in incoming_targets.items():
            if len(in_edges) > 1:
                for idx, e in enumerate(sorted(in_edges, key=lambda x: (x.source, x.condition_index))):
                    e.target_offset_y = (idx - (len(in_edges) - 1) / 2.0) * 16.0

        # 2-Pass update: Pass 1 primary edges, Pass 2 secondary candidate edges (so they branch from primary edge midpoint)
        for edge in self.edges:
            if not getattr(edge, "is_secondary_candidate", False):
                edge.update_path()
        for edge in self.edges:
            if getattr(edge, "is_secondary_candidate", False):
                edge.update_path()

    def remove_edge(self, source: int, target: int, kind: str, condition_index: int = -1) -> None:
        for edge in list(self.edges):
            try:
                if edge.source == source and edge.target == target and edge.kind == kind and edge.condition_index == condition_index:
                    if edge in self.edges:
                        self.edges.remove(edge)
                    edge.dispose()
                    try:
                        if edge.scene() is self.scene:
                            self.scene.removeItem(edge)
                    except (RuntimeError, AttributeError):
                        pass
                    break
            except (RuntimeError, AttributeError):
                if edge in self.edges:
                    self.edges.remove(edge)
                continue
        self.scene.update()
        self.view.viewport().update()

    def select_node(self, index: int) -> None:
        if index not in self.nodes:
            return
        current_selection = self.selected_indexes()
        # Rubber-band selection emits node_selected for every node.  The
        # inspector follows the latest node, but must not collapse the other
        # selected nodes before Delete can archive the full batch.
        if index in current_selection and len(current_selection) > 1:
            self.nodes[index].update()
            return
        previous_suspend = self.suspended
        self.suspended = True
        self.scene.clearSelection()
        self.nodes[index].setSelected(True)
        self.suspended = previous_suspend
        self.nodes[index].update()

    def selected_index(self) -> int:
        selected = self.selected_indexes()
        return selected[0] if selected else 0

    def selected_indexes(self) -> list[int]:
        return sorted(item.index for item in self.scene.selectedItems() if isinstance(item, NodeItem))

    def _highlight_connected_edges(self, selected_index: int) -> None:
        if not self.edges:
            return
        alive_edges = []
        for edge in self.edges:
            try:
                if edge.scene() is not None:
                    alive_edges.append(edge)
            except RuntimeError:
                pass
        self.edges = alive_edges

        if not selected_index:
            for edge in self.edges:
                try:
                    base_op = 0.38 if getattr(edge, "edge_type", "") == "return" and edge.kind == "success" else (0.35 if getattr(edge, "edge_type", "") == "return" else (0.50 if getattr(edge, "is_condition", False) else 0.45))
                    edge.setOpacity(base_op)
                    edge.setZValue(-1)
                except RuntimeError:
                    pass
            return

        for edge in self.edges:
            try:
                if edge.source == selected_index or edge.target == selected_index:
                    edge.setOpacity(1.0)
                    edge.setZValue(15)
                else:
                    edge.setOpacity(0.18)
                    edge.setZValue(-2)
            except RuntimeError:
                pass

    def _on_scene_selection_changed(self) -> None:
        if self.suspended:
            return
        selected = self.selected_indexes()
        if len(selected) == 1:
            self._highlight_connected_edges(selected[0])
        else:
            self._highlight_connected_edges(0)

    def auto_layout(self) -> None:
        if not self.nodes:
            return
        self.suspended = True
        positions = self._suggest_positions()
        for index, node in self.nodes.items():
            node.setPos(positions[index])
        self.suspended = False
        self._route_edges()
        self._sync_workflow_lanes()
        self.positions_changed.emit(self.positions())
        self.fit_all()

    def fit_all(self) -> None:
        self.view.fit_all()

    def view_reset_zoom(self) -> None:
        self.view.reset_zoom()

    def align_selected_nodes(self, mode: str) -> None:
        selected_indexes = self.selected_indexes()
        if len(selected_indexes) < 2:
            return
        selected_nodes = [self.nodes[i] for i in selected_indexes if i in self.nodes]
        if len(selected_nodes) < 2:
            return

        gap_y = 50.0
        gap_x = 100.0

        if mode == "left":
            min_x = min(n.pos().x() for n in selected_nodes)
            sorted_nodes = sorted(selected_nodes, key=lambda n: (n.pos().y(), n.index))
            for idx, n in enumerate(sorted_nodes):
                if idx == 0:
                    n.setPos(min_x, n.pos().y())
                else:
                    prev = sorted_nodes[idx - 1]
                    prev_bottom = prev.pos().y() + prev.boundingRect().height()
                    curr_y = max(n.pos().y(), prev_bottom + gap_y)
                    n.setPos(min_x, curr_y)
        elif mode == "right":
            max_right = max(n.pos().x() + n.boundingRect().width() for n in selected_nodes)
            sorted_nodes = sorted(selected_nodes, key=lambda n: (n.pos().y(), n.index))
            for idx, n in enumerate(sorted_nodes):
                w = n.boundingRect().width()
                target_x = max_right - w
                if idx == 0:
                    n.setPos(target_x, n.pos().y())
                else:
                    prev = sorted_nodes[idx - 1]
                    prev_bottom = prev.pos().y() + prev.boundingRect().height()
                    curr_y = max(n.pos().y(), prev_bottom + gap_y)
                    n.setPos(target_x, curr_y)
        elif mode == "top":
            min_y = min(n.pos().y() for n in selected_nodes)
            sorted_nodes = sorted(selected_nodes, key=lambda n: (n.pos().x(), n.index))
            for idx, n in enumerate(sorted_nodes):
                if idx == 0:
                    n.setPos(n.pos().x(), min_y)
                else:
                    prev = sorted_nodes[idx - 1]
                    prev_right = prev.pos().x() + prev.boundingRect().width()
                    curr_x = max(n.pos().x(), prev_right + gap_x)
                    n.setPos(curr_x, min_y)
        elif mode == "bottom":
            max_bottom = max(n.pos().y() + n.boundingRect().height() for n in selected_nodes)
            sorted_nodes = sorted(selected_nodes, key=lambda n: (n.pos().x(), n.index))
            for idx, n in enumerate(sorted_nodes):
                h = n.boundingRect().height()
                target_y = max_bottom - h
                if idx == 0:
                    n.setPos(n.pos().x(), target_y)
                else:
                    prev = sorted_nodes[idx - 1]
                    prev_right = prev.pos().x() + prev.boundingRect().width()
                    curr_x = max(n.pos().x(), prev_right + gap_x)
                    n.setPos(curr_x, target_y)
        elif mode == "distribute_h":
            sorted_nodes = sorted(selected_nodes, key=lambda n: n.pos().x())
            first_x = sorted_nodes[0].pos().x()
            last_x = sorted_nodes[-1].pos().x()
            if len(sorted_nodes) > 2 and last_x > first_x:
                total_span = last_x - first_x
                step = total_span / (len(sorted_nodes) - 1)
                for idx, n in enumerate(sorted_nodes):
                    n.setPos(first_x + idx * step, n.pos().y())
            else:
                for idx, n in enumerate(sorted_nodes):
                    if idx > 0:
                        prev = sorted_nodes[idx - 1]
                        n.setPos(prev.pos().x() + prev.boundingRect().width() + gap_x, n.pos().y())
        elif mode == "distribute_v":
            sorted_nodes = sorted(selected_nodes, key=lambda n: n.pos().y())
            first_y = sorted_nodes[0].pos().y()
            last_y = sorted_nodes[-1].pos().y()
            if len(sorted_nodes) > 2 and last_y > first_y:
                total_span = last_y - first_y
                step = total_span / (len(sorted_nodes) - 1)
                for idx, n in enumerate(sorted_nodes):
                    n.setPos(n.pos().x(), first_y + idx * step)
            else:
                for idx, n in enumerate(sorted_nodes):
                    if idx > 0:
                        prev = sorted_nodes[idx - 1]
                        n.setPos(n.pos().x(), prev.pos().y() + prev.boundingRect().height() + gap_y)

        self._route_edges()
        self.positions_changed.emit(self.positions())
        if hasattr(self, "minimap") and self.minimap.expanded:
            self.minimap.preview.update()

    def _load_comment_boxes(self) -> None:
        for c in list(self.comments):
            try:
                if c.scene() is self.scene:
                    self.scene.removeItem(c)
            except (RuntimeError, AttributeError):
                pass
        self.comments = []
        raw_comments = self.macro.get("graph_comments") or []
        for c in raw_comments if isinstance(raw_comments, list) else []:
            if isinstance(c, dict):
                x = float(c.get("x", 0.0))
                y = float(c.get("y", 0.0))
                w = float(c.get("w", 440.0))
                h = float(c.get("h", 260.0))
                loaded_indexes = c.get("node_indexes")
                if loaded_indexes is None or not isinstance(loaded_indexes, list):
                    # Backward compatibility: adopt nodes intersecting this box
                    box_rect = QtCore.QRectF(x, y, w, h)
                    loaded_indexes = [
                        idx for idx, node in self.nodes.items()
                        if box_rect.intersects(node.sceneBoundingRect())
                    ]
                box = NodeGroupItem(
                    str(c.get("id") or ""),
                    str(c.get("title") or "노드 그룹"),
                    str(c.get("color") or "yellow"),
                    QtCore.QRectF(x, y, w, h),
                    self,
                    node_indexes=[int(i) for i in loaded_indexes if str(i).isdigit()],
                )
                self.comments.append(box)
                self.scene.addItem(box)
                box.sync_rect()

    def _add_default_comment_box(self, color: str = "yellow") -> None:
        if self.selected_indexes():
            self.add_comment_box_for_selection(color=color)
            return
        vp_rect = self.view.viewport().rect()
        center_scene = self.view.mapToScene(vp_rect.center())
        box_pos = center_scene - QtCore.QPointF(220, 130)
        self.add_comment_box(box_pos, "새 노드 그룹", color, 440.0, 260.0)

    def _delete_selected_group(self) -> None:
        selected_groups = [
            item for item in self.scene.selectedItems()
            if hasattr(item, "node_indexes") or hasattr(item, "comment_id")
        ]
        if not selected_groups and self.comments:
            selected_groups = [self.comments[-1]]
        for g in selected_groups:
            self.remove_comment_box(g)

    def add_comment_box_for_selection(self, title: str = "", color: str = "yellow") -> NodeGroupItem | None:
        indexes = self.selected_indexes()
        selected_nodes = [self.nodes[i] for i in indexes if i in self.nodes]
        if not selected_nodes:
            return None
        min_x = min(node.sceneBoundingRect().left() for node in selected_nodes)
        min_y = min(node.sceneBoundingRect().top() for node in selected_nodes)
        max_x = max(node.sceneBoundingRect().right() for node in selected_nodes)
        max_y = max(node.sceneBoundingRect().bottom() for node in selected_nodes)

        pad_x = 34.0
        pad_top = 46.0
        pad_bottom = 28.0

        box_x = min_x - pad_x
        box_y = min_y - pad_top
        box_w = max(440.0, (max_x - min_x) + (pad_x * 2.0))
        box_h = max(260.0, (max_y - min_y) + pad_top + pad_bottom)

        if not title:
            if len(selected_nodes) == 1:
                title = f"{selected_nodes[0].index}번 노드 그룹"
            else:
                title = f"노드 {selected_nodes[0].index}~{selected_nodes[-1].index}번 그룹"

        return self.add_comment_box(QtCore.QPointF(box_x, box_y), title, color, box_w, box_h, node_indexes=indexes)

    def check_node_group_membership(self, node: NodeItem) -> None:
        # If node belongs to an existing group, sync that group's bounds
        for g in self.comments:
            if hasattr(g, "node_indexes") and node.index in g.node_indexes:
                g.sync_rect()
                return
        # If dropped inside an existing group, join that group
        node_rect = node.sceneBoundingRect()
        for g in self.comments:
            if hasattr(g, "sceneBoundingRect") and g.sceneBoundingRect().intersects(node_rect):
                g.add_node(node.index)
                return

    def include_connected_nodes_in_comment(self, source: int, target: int) -> None:
        """Add target node to comment group only if it's a forward edge within the same workflow branch."""
        if target <= source:
            return  # Backward / loop edges (e.g. return to 1) never merge groups
        src_step = self.steps[source - 1] if 0 < source <= len(self.steps) else {}
        tgt_step = self.steps[target - 1] if 0 < target <= len(self.steps) else {}
        src_wf = str(src_step.get("workflow_id") or "").strip()
        tgt_wf = str(tgt_step.get("workflow_id") or "").strip()
        if src_wf != tgt_wf:
            return  # Nodes from different branches or branch-to-main must NEVER merge!
        for box in self.comments:
            if hasattr(box, "node_indexes") and target in box.node_indexes:
                return  # Target already belongs to a node group
        for box in list(self.comments):
            if hasattr(box, "node_indexes") and source in box.node_indexes:
                box.add_node(target)
                break

    def split_group_by_branches(self, group: NodeGroupItem) -> list[NodeGroupItem]:
        """Split a node group that contains nodes from multiple branches into distinct groups per branch."""
        if not hasattr(group, "node_indexes") or not group.node_indexes:
            return [group]

        branches: dict[str, list[int]] = {}
        branch_labels: dict[str, str] = {}
        for idx in group.node_indexes:
            step = self.steps[idx - 1] if 0 < idx <= len(self.steps) else {}
            wf_id = str(step.get("workflow_id") or "").strip()
            wf_lbl = str(step.get("workflow_label") or "").strip()
            branches.setdefault(wf_id, []).append(idx)
            if wf_lbl and wf_id not in branch_labels:
                branch_labels[wf_id] = wf_lbl

        if len(branches) <= 1:
            return [group]

        available_colors = list(NODE_GROUP_THEMES.keys())
        branch_items = list(branches.items())
        first_wf, first_indexes = branch_items[0]
        group.node_indexes = list(first_indexes)
        first_name = branch_labels.get(first_wf) or (f"{first_wf} 분기" if first_wf else "기본 그룹")
        group.title = f"{first_name} 그룹"
        group.sync_rect()

        result_groups = [group]
        for i, (wf_id, indexes) in enumerate(branch_items[1:], start=1):
            wf_name = branch_labels.get(wf_id) or (f"{wf_id} 분기" if wf_id else f"분기 {i} 그룹")
            title = f"{wf_name} 그룹"
            color = available_colors[i % len(available_colors)]
            new_group = self.add_comment_box(
                QtCore.QPointF(0, 0),
                title=title,
                color=color,
                node_indexes=indexes,
            )
            result_groups.append(new_group)

        self.comments_changed.emit(self.dump_comments())
        return result_groups

    def auto_group_by_branches(self) -> None:
        """Automatically group nodes by their workflow branches, splitting merged groups or creating missing groups."""
        for g in list(self.comments):
            if hasattr(g, "node_indexes") and len(g.node_indexes) > 1:
                self.split_group_by_branches(g)

        existing_grouped_nodes = set()
        for g in self.comments:
            if hasattr(g, "node_indexes"):
                existing_grouped_nodes.update(g.node_indexes)

        available_colors = list(NODE_GROUP_THEMES.keys())
        color_idx = len(self.comments)

        for lane in getattr(self, "workflow_items", []):
            lane_indexes = getattr(lane, "indexes", [])
            if not lane_indexes:
                continue
            ungrouped = [i for i in lane_indexes if i not in existing_grouped_nodes]
            if ungrouped:
                title = f"{lane.label_text} 그룹"
                color = available_colors[color_idx % len(available_colors)]
                color_idx += 1
                new_group = self.add_comment_box(
                    QtCore.QPointF(0, 0),
                    title=title,
                    color=color,
                    node_indexes=lane_indexes,
                )
                existing_grouped_nodes.update(lane_indexes)

        self._sync_node_groups()
        self.comments_changed.emit(self.dump_comments())

    def add_comment_box(
        self,
        pos: QtCore.QPointF,
        title: str = "새 노드 그룹",
        color: str = "yellow",
        width: float = 440.0,
        height: float = 260.0,
        node_indexes: list[int] | None = None,
    ) -> NodeGroupItem:
        box = NodeGroupItem(
            uuid.uuid4().hex[:8],
            title,
            color,
            QtCore.QRectF(pos.x(), pos.y(), width, height),
            self,
            node_indexes=node_indexes or [],
        )
        self.comments.append(box)
        self.scene.addItem(box)
        box.sync_rect()
        self.comments_changed.emit(self.dump_comments())
        return box

    def remove_comment_box(self, box: NodeGroupItem) -> None:
        if box in self.comments:
            self.comments.remove(box)
        try:
            if box.scene() is self.scene:
                self.scene.removeItem(box)
        except (RuntimeError, AttributeError):
            pass
        self.comments_changed.emit(self.dump_comments())

    def dump_comments(self) -> list[dict[str, Any]]:
        result = []
        for b in list(self.comments):
            try:
                if b.scene() is not None:
                    result.append({
                        "id": b.comment_id,
                        "title": b.title,
                        "color": b.color_name,
                        "x": round(b.pos().x(), 1),
                        "y": round(b.pos().y(), 1),
                        "w": round(b.box_rect.width(), 1),
                        "h": round(b.box_rect.height(), 1),
                        "node_indexes": list(getattr(b, "node_indexes", [])),
                    })
            except (RuntimeError, AttributeError):
                pass
        return result

    def begin_link(self, node: NodeItem, kind: str, scene_pos: QtCore.QPointF) -> None:
        if self._temp_edge and self._temp_edge.scene() is self.scene:
            self.scene.removeItem(self._temp_edge)
        self._temp_start = scene_pos
        self._temp_edge = QtWidgets.QGraphicsPathItem()
        color = QtGui.QColor(COLORS["success"] if kind == "success" else COLORS["danger"])
        self._temp_edge.setPen(QtGui.QPen(color, 2.2, QtCore.Qt.DashLine, QtCore.Qt.RoundCap))
        self._temp_edge.setZValue(10)
        self.scene.addItem(self._temp_edge)

    def update_link(self, scene_pos: QtCore.QPointF) -> None:
        if not self._temp_edge:
            return
        start = self._temp_start
        bend = max(80.0, abs(scene_pos.x() - start.x()) * 0.42)
        path = QtGui.QPainterPath(start)
        path.cubicTo(
            QtCore.QPointF(start.x() + bend, start.y()),
            QtCore.QPointF(scene_pos.x() - bend, scene_pos.y()),
            scene_pos,
        )
        self._temp_edge.setPath(path)

    def begin_edge_drag(self, edge: EdgeItem) -> None:
        source = self.nodes.get(edge.source)
        if not source:
            return
        port = source.out_success_port if edge.kind == "success" else source.out_fail_port
        self.begin_link(source, edge.kind, port.mapToScene(port.rect().center()))
        edge.setOpacity(0.22)

    def retargeting_target(self, source: int, kind: str) -> int:
        value = self._retargeting_edge
        if value and value[0] == source and value[2] == kind:
            return value[1]
        return 0

    def _target_at(self, scene_pos: QtCore.QPointF, source: NodeItem) -> NodeItem | None:
        for item in self.scene.items(scene_pos):
            candidate = item
            while candidate and not isinstance(candidate, NodeItem):
                candidate = candidate.parentItem()
            if isinstance(candidate, NodeItem) and candidate is not source:
                return candidate
        best: NodeItem | None = None
        best_distance = 90.0
        for node in self.nodes.values():
            if node is source:
                continue
            distance = (node.sceneBoundingRect().center() - scene_pos).manhattanLength()
            if distance < best_distance:
                best_distance = distance
                best = node
        return best

    def end_edge_drag(self, edge: EdgeItem, scene_pos: QtCore.QPointF) -> None:
        try:
            if self._temp_edge and self._temp_edge.scene() is self.scene:
                self.scene.removeItem(self._temp_edge)
        except Exception:
            pass
        self._temp_edge = None
        try:
            edge.setOpacity(1.0)
        except Exception:
            return

        source = self.nodes.get(edge.source)
        if not source:
            return
        target = self._target_at(scene_pos, source)
        if target:
            self.include_connected_nodes_in_comment(source.index, target.index)
            if edge.is_condition:
                self.edge_condition_retarget_requested.emit(edge.source, edge.condition_index, target.index)
            else:
                self._retargeting_edge = (edge.source, edge.target, edge.kind)
                try:
                    self.link_requested.emit(edge.source, target.index, edge.kind)
                finally:
                    self._retargeting_edge = None
        else:
            source_idx = edge.source
            target_idx = edge.target
            kind = edge.kind
            cond_idx = edge.condition_index
            is_cond = edge.is_condition
            self.remove_edge(source_idx, target_idx, kind, cond_idx)
            if is_cond:
                self.edge_condition_delete_requested.emit(source_idx, cond_idx)
            else:
                self.edge_delete_requested.emit(source_idx, target_idx, kind)

    def end_link(self, source: NodeItem, kind: str, scene_pos: QtCore.QPointF) -> None:
        if self._temp_edge and self._temp_edge.scene() is self.scene:
            self.scene.removeItem(self._temp_edge)
        self._temp_edge = None
        target = self._target_at(scene_pos, source)
        if target:
            self.include_connected_nodes_in_comment(source.index, target.index)
            self.link_requested.emit(source.index, target.index, kind)
