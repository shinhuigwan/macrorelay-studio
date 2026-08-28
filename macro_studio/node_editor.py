from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .theme import COLORS


ACTION_STYLES: dict[str, tuple[str, str]] = {
    "mouse_click": ("CLICK", "#7C6CFF"),
    "inactive_click": ("CLICK", "#7C6CFF"),
    "image_search": ("VISION", "#4D9FFF"),
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
    "run_program": ("RUN", "#35C89A"),
    "terminate_program": ("STOP", "#F06A78"),
}

ACTION_TITLES = {
    "mouse_click": "마우스 클릭", "inactive_click": "비활성 클릭", "image_search": "이미지 서치",
    "ocr": "OCR 인식", "type_text": "텍스트 입력", "wait": "대기", "browser_action": "브라우저 요소",
    "table_store": "테이블 저장", "table_copy": "테이블 복사", "table_paste": "테이블 붙여넣기",
    "table_excel_read": "Excel 읽기", "table_excel_write": "Excel 쓰기", "flow_control": "반복 이동",
    "text_condition": "텍스트 조건", "set_var": "변수 설정", "calc_var": "변수 계산", "coord_mode": "좌표 기준",
    "call_submacro": "서브매크로", "run_program": "프로그램 실행", "terminate_program": "프로그램 종료",
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
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._panning = False
        self._double_click_pan = False
        self._pan_start = QtCore.QPoint()
        self._zoom = 100

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)

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


class NodePort(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, node: "NodeItem", kind: str) -> None:
        super().__init__(-7, -7, 14, 14, node)
        self.node = node
        self.kind = kind
        color = COLORS["success"] if kind == "success" else COLORS["danger"]
        self.setBrush(QtGui.QColor(color))
        self.setPen(QtGui.QPen(QtGui.QColor("#101218"), 3))
        self.setZValue(5)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)

    def shape(self) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        path.addEllipse(self.rect().adjusted(-9, -9, 9, 9))
        return path

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self.node.canvas.begin_link(self.node, self.kind, self.scenePos())
        event.accept()

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self.node.canvas.update_link(event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
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
        self.image.setMinimumSize(260, 150)
        self.detail = QtWidgets.QLabel()
        self.detail.setObjectName("Muted")
        layout.addWidget(self.title)
        layout.addWidget(self.image)
        layout.addWidget(self.detail)

    def show_image(self, path: Path, alias: str, screen_pos: QtCore.QPoint) -> None:
        pixmap = QtGui.QPixmap(str(path))
        if pixmap.isNull():
            return
        self.title.setText(alias or path.stem)
        self.image.setPixmap(pixmap.scaled(360, 240, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        self.detail.setText(f"{pixmap.width()} × {pixmap.height()} · 이미지 서치 원본")
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
    def __init__(self, canvas: "NodeCanvas", alias: str, path: Path | None, parent=None) -> None:
        super().__init__("▧", parent)
        self.canvas = canvas
        self.alias = alias
        self.path = path
        self.setAcceptHoverEvents(True)

    def hoverEnterEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        if self.path is not None:
            self.canvas.show_image_preview(self.path, self.alias, event.screenPos())
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        self.canvas.hide_image_preview()
        super().hoverLeaveEvent(event)


class NodeItem(QtWidgets.QGraphicsObject):
    WIDTH = 270.0
    HEIGHT = 122.0

    def __init__(self, index: int, step: dict[str, Any], canvas: "NodeCanvas") -> None:
        super().__init__()
        self.index = index
        self.step = step
        self.canvas = canvas
        self.setFlags(
            QtWidgets.QGraphicsItem.ItemIsMovable
            | QtWidgets.QGraphicsItem.ItemIsSelectable
            | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.success_port = NodePort(self, "success")
        self.fail_port = NodePort(self, "fail")
        self.success_port.setPos(self.WIDTH, 72)
        self.fail_port.setPos(self.WIDTH, 101)
        self.preview_badge: NodeImagePreviewBadge | None = None
        if str(step.get("action") or "") == "image_search":
            alias = str(step.get("asset") or "").strip()
            preview_path = canvas.asset_preview_path(alias)
            badge = NodeImagePreviewBadge(canvas, alias, preview_path, self)
            font = QtGui.QFont("Segoe UI Symbol", 12)
            font.setBold(True)
            badge.setFont(font)
            badge.setBrush(QtGui.QColor("#5ED9FF"))
            badge.setPos(243, 7)
            badge.setZValue(8)
            badge.setCursor(QtCore.Qt.PointingHandCursor)
            if preview_path is not None:
                uri = preview_path.as_uri()
                badge.setToolTip(
                    f"<b>{html.escape(alias or '이미지 서치')}</b><br>"
                    f"<img src=\"{html.escape(uri)}\" width=\"240\"><br>"
                    "이미지 서치 원본 미리보기"
                )
            else:
                badge.setToolTip(f"{html.escape(alias or '이미지 미선택')} · 미리보기 파일을 찾을 수 없습니다.")
            self.preview_badge = badge

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(-3, -3, self.WIDTH + 12, self.HEIGHT + 6)

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        _widget=None,
    ) -> None:
        action = str(self.step.get("action") or "step")
        action_title = ACTION_TITLES.get(action, action)
        badge, accent = ACTION_STYLES.get(action, ("STEP", COLORS["muted"]))
        selected = self.isSelected()
        running = self.index == self.canvas.active_step
        candidate_position = self.canvas.start_candidate_position(self.index)
        lod = max(0.01, option.levelOfDetailFromTransform(painter.worldTransform()))
        font_boost = min(2.5, max(1.0, 0.82 / lod))
        compact = lod < 0.62
        rect = QtCore.QRectF(0, 0, self.WIDTH, self.HEIGHT)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setBrush(QtGui.QColor("#181C25"))
        border_color = (
            "#38E7FF"
            if running
            else COLORS["accent"]
            if selected
            else COLORS["warning"]
            if candidate_position
            else "#343B4D"
        )
        border_width = 4.5 if running else (2 if selected else 1.2)
        if running:
            painter.setPen(QtGui.QPen(QtGui.QColor(56, 231, 255, 70), 10))
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 14, 14)
        painter.setPen(QtGui.QPen(QtGui.QColor(border_color), border_width))
        painter.drawRoundedRect(rect, 12, 12)

        header_path = QtGui.QPainterPath()
        header_path.addRoundedRect(QtCore.QRectF(0, 0, self.WIDTH, 40), 12, 12)
        header_path.addRect(QtCore.QRectF(0, 22, self.WIDTH, 18))
        header_color = QtGui.QColor(accent)
        header_color.setAlpha(52 if not selected else 78)
        painter.fillPath(header_path, header_color)

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(accent))
        painter.drawRoundedRect(QtCore.QRectF(12, 9, 34, 23), 6, 6)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        number_font = QtGui.QFont("Segoe UI")
        number_font.setPointSizeF(9 * font_boost)
        number_font.setBold(True)
        painter.setFont(number_font)
        painter.drawText(QtCore.QRectF(12, 9, 34, 23), QtCore.Qt.AlignCenter, str(self.index))

        title_font = QtGui.QFont("Malgun Gothic")
        title_font.setPointSizeF((10.5 if not compact else 11.5) * font_boost)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QtGui.QColor("#F2F4F8"))
        title_metrics = QtGui.QFontMetrics(title_font)
        title_width = 194 if compact else 151
        painter.drawText(QtCore.QRectF(55, 6, title_width, 29), QtCore.Qt.AlignVCenter, title_metrics.elidedText(action_title, QtCore.Qt.ElideRight, title_width))
        if not compact:
            badge_font = QtGui.QFont("Segoe UI")
            badge_font.setPointSizeF(7.5 * font_boost)
            painter.setFont(badge_font)
            painter.setPen(QtGui.QColor(accent))
            painter.drawText(QtCore.QRectF(211, 8, 44, 24), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, badge)

        summary = self.canvas.step_summary(self.step)
        summary_font = QtGui.QFont("Malgun Gothic")
        summary_font.setPointSizeF((10 if not compact else 11) * font_boost)
        painter.setFont(summary_font)
        painter.setPen(QtGui.QColor("#DDE1E9"))
        metrics = QtGui.QFontMetrics(summary_font)
        painter.drawText(QtCore.QRectF(15, 48, 220, 31), QtCore.Qt.AlignVCenter, metrics.elidedText(summary, QtCore.Qt.ElideRight, 220))

        delay = int(self.step.get("sleep_after") or 0)
        success_delay = int(self.step.get("on_success_delay") or 0)
        detail = f"완료 {delay}ms" if delay else "완료 즉시"
        repeat_var = str(self.step.get("repeat_var") or "").strip().lstrip("$")
        if repeat_var:
            detail = f"반복 ${repeat_var}회  ·  " + detail
        elif int(self.step.get("repeat") or 1) > 1:
            detail = f"반복 {int(self.step.get('repeat') or 1)}회  ·  " + detail
        if action == "image_search" and bool(self.step.get("repeat_on_success")):
            detail = "탐지 중 재검색 · 미탐지 시 실패  ·  " + detail
        if success_delay:
            detail += f"  ·  연결 {success_delay}ms"
        if candidate_position:
            detail = f"시작 검색 {candidate_position[0]}/{candidate_position[1]} · 미탐지 시 다음 후보"
        if not compact:
            detail_font = QtGui.QFont("Malgun Gothic")
            detail_font.setPointSizeF(8 * font_boost)
            painter.setFont(detail_font)
            painter.setPen(QtGui.QColor(COLORS["muted"]))
            painter.drawText(15, 103, detail)

        port_font = QtGui.QFont("Segoe UI")
        port_font.setPointSizeF(7.5 * font_boost)
        painter.setFont(port_font)
        painter.setPen(QtGui.QColor(COLORS["success"]))
        painter.drawText(QtCore.QRectF(226, 62, 28, 18), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, "OK")
        painter.setPen(QtGui.QColor(COLORS["danger"]))
        painter.drawText(QtCore.QRectF(226, 91, 28, 18), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, "ERR")

        if self.index == self.canvas.start_step:
            painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["success"]), 2))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 9, 9)
        if self.index == self.canvas.end_step:
            painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["danger"]), 2))
            painter.drawLine(QtCore.QLineF(12, self.HEIGHT - 4, self.WIDTH - 12, self.HEIGHT - 4))
        if running:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#38E7FF"))
            painter.drawRoundedRect(QtCore.QRectF(168, 92, 84, 22), 7, 7)
            running_font = QtGui.QFont("Malgun Gothic", 8)
            running_font.setBold(True)
            painter.setFont(running_font)
            painter.setPen(QtGui.QColor("#061116"))
            painter.drawText(QtCore.QRectF(168, 92, 84, 22), QtCore.Qt.AlignCenter, "실행 중")

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged and not self.canvas.suspended:
            self.canvas.node_moved()
        if change == QtWidgets.QGraphicsItem.ItemSelectedHasChanged:
            self.update()
            if bool(value) and not self.canvas.suspended and not self.canvas.rubber_selecting:
                self.canvas.node_selected.emit(self.index)
        return result

    def mouseDoubleClickEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self.canvas.node_selected.emit(self.index)
        self.canvas.inspector_requested.emit(self.index)
        event.accept()

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
        duplicate = menu.addAction("노드 복제")
        archive = menu.addAction("노드 보관")
        chosen = menu.exec(event.screenPos())
        if change_wait is not None and chosen == change_wait:
            self.canvas.wait_duration_requested.emit(wait_indexes or [self.index])
        elif change_all_wait is not None and chosen == change_all_wait:
            self.canvas.all_wait_duration_requested.emit()
        elif chosen == duplicate:
            self.canvas.node_duplicate_requested.emit(self.index)
        elif chosen == archive:
            self.canvas.node_delete_requested.emit(self.index)
        event.accept()


class EdgeItem(QtWidgets.QGraphicsPathItem):
    def __init__(
        self,
        canvas: "NodeCanvas",
        source: int,
        target: int,
        kind: str,
        condition_index: int = -1,
        rule: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.canvas = canvas
        self.source = source
        self.target = target
        self.kind = kind
        self.condition_index = condition_index
        self.rule = rule or {}
        self.is_condition = condition_index >= 0
        self.color = QtGui.QColor(
            COLORS["warning"] if self.is_condition else COLORS["success"] if kind == "success" else COLORS["danger"]
        )
        line_style = QtCore.Qt.DashLine if self.is_condition else QtCore.Qt.SolidLine
        self.setPen(QtGui.QPen(self.color, 2.6 if self.is_condition else 2.4, line_style, QtCore.Qt.RoundCap))
        self.setZValue(-1)
        self.setAcceptHoverEvents(True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.arrow = QtWidgets.QGraphicsPolygonItem(self)
        self.arrow.setBrush(self.color)
        self.arrow.setPen(QtCore.Qt.NoPen)
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
        self.update_path()

    def shape(self) -> QtGui.QPainterPath:
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(14)
        return stroker.createStroke(self.path())

    def update_path(self) -> None:
        source_node = self.canvas.nodes.get(self.source)
        target_node = self.canvas.nodes.get(self.target)
        if not source_node or not target_node:
            return
        port = source_node.success_port if self.kind == "success" else source_node.fail_port
        start = port.mapToScene(port.rect().center())
        if self.route_side == "top":
            end = target_node.mapToScene(QtCore.QPointF(NodeItem.WIDTH / 2 + self.target_offset_y, 0))
        elif self.route_side == "bottom":
            end = target_node.mapToScene(
                QtCore.QPointF(NodeItem.WIDTH / 2 + self.target_offset_y, NodeItem.HEIGHT)
            )
        else:
            end = target_node.mapToScene(QtCore.QPointF(0, NodeItem.HEIGHT / 2 + self.target_offset_y))
        distance = abs(end.x() - start.x())
        if self.route_side:
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
            margin = 46.0 + self.route_lane * 30.0
            if self.route_side == "top":
                lane_y = min(rect.top() for rect in route_rects) - margin
                direction = -1.0
            else:
                lane_y = max(rect.bottom() for rect in route_rects) + margin
                direction = 1.0
            exit_x = start.x() + 26.0
            horizontal_direction = 1.0 if end.x() >= exit_x else -1.0
            corner = min(16.0, max(5.0, abs(end.x() - exit_x) / 3.0))
            near_lane_y = lane_y - direction * corner
            path = QtGui.QPainterPath(start)
            path.lineTo(exit_x, start.y())
            path.lineTo(exit_x, near_lane_y)
            path.quadTo(exit_x, lane_y, exit_x + horizontal_direction * corner, lane_y)
            path.lineTo(end.x() - horizontal_direction * corner, lane_y)
            path.quadTo(end.x(), lane_y, end.x(), near_lane_y)
            path.lineTo(end)
        else:
            # Keep close, forward links inside the gap between nodes.  A large
            # minimum bend makes their control points cross over each other and
            # produces the small loops seen when nodes are placed close by.
            bend = min(110.0, max(18.0, distance * 0.42))
            lane_offset = (self.condition_index + 1) * 34.0 if self.is_condition else 0.0
            c1 = QtCore.QPointF(start.x() + bend, start.y() + lane_offset)
            c2 = QtCore.QPointF(end.x() - bend, end.y() + lane_offset)
            path = QtGui.QPainterPath(start)
            path.cubicTo(c1, c2, end)
        self.setPath(path)

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
            candidate_position = self.canvas.start_candidate_position(self.source)
            if candidate_position:
                text = "탐지 성공" if self.kind == "success" else "미탐지 → 다음 후보"
            if delay:
                text += f" · {delay}ms"
        self.label.setText(text)
        label_y = 5 if self.route_side == "bottom" else -17
        if not self.route_side:
            label_y += self.target_offset_y * 1.15
        self.label.setPos(point + QtCore.QPointF(8, label_y))

    def hoverEnterEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        style = QtCore.Qt.DashLine if self.is_condition else QtCore.Qt.SolidLine
        self.setPen(QtGui.QPen(self.color.lighter(145), 3.5, style, QtCore.Qt.RoundCap))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        style = QtCore.Qt.DashLine if self.is_condition else QtCore.Qt.SolidLine
        self.setPen(QtGui.QPen(self.color, 2.6 if self.is_condition else 2.4, style, QtCore.Qt.RoundCap))
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
            self.canvas.end_edge_drag(self, event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QtWidgets.QGraphicsSceneContextMenuEvent) -> None:
        menu = QtWidgets.QMenu()
        delay = menu.addAction("연결 설정 · 딜레이와 조건 분기")
        remove = menu.addAction("연결 끊기")
        chosen = menu.exec(event.screenPos())
        if chosen == delay:
            self.canvas.edge_delay_requested.emit(self.source, self.target, self.kind)
        elif chosen == remove:
            if self.is_condition:
                self.canvas.edge_condition_delete_requested.emit(self.source, self.condition_index)
            else:
                self.canvas.remove_edge(self.source, self.target, self.kind)
                self.canvas.edge_delete_requested.emit(self.source, self.target, self.kind)
        event.accept()


class NodeCanvas(QtWidgets.QWidget):
    node_selected = QtCore.Signal(int)
    inspector_requested = QtCore.Signal(int)
    positions_changed = QtCore.Signal(dict)
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.suspended = False
        self.macro: dict[str, Any] = {}
        self.steps: list[dict[str, Any]] = []
        self.nodes: dict[int, NodeItem] = {}
        self.edges: list[EdgeItem] = []
        self.start_step = 0
        self.start_candidates: list[int] = []
        self.end_step = 0
        self.active_step = 0
        self._temp_edge: QtWidgets.QGraphicsPathItem | None = None
        self._temp_start = QtCore.QPointF()
        self.rubber_selecting = False
        self._asset_preview_paths: dict[str, Path] = {}
        self._preview_popup = ImagePreviewPopup(self)
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
        auto.clicked.connect(self.auto_layout)
        start_group = QtWidgets.QPushButton("시작 검색 묶기")
        start_group.setToolTip("선택한 이미지 서치·OCR 노드를 위 번호부터 검사하고, 실패 시 다음 후보로 이동합니다.")
        start_group.clicked.connect(lambda: self.start_search_group_requested.emit(self.selected_indexes()))
        fit = QtWidgets.QPushButton("전체 보기")
        fit.clicked.connect(self.fit_all)
        reset = QtWidgets.QPushButton("100%")
        reset.clicked.connect(self.view_reset_zoom)
        self.zoom_label = QtWidgets.QLabel("100%")
        self.zoom_label.setObjectName("Muted")
        legend = QtWidgets.QLabel(
            f"<span style='color:{COLORS['success']}'>● 성공</span>  "
            f"<span style='color:{COLORS['danger']}'>● 실패</span>  "
            "<span style='color:#9DA7BA'>· 선 바깥 드롭=제거 · 바닥 더블클릭 유지=이동</span>"
        )
        legend.setObjectName("Muted")
        toolbar_layout.addWidget(self.flow_label)
        toolbar_layout.addWidget(legend)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(start_group)
        toolbar_layout.addWidget(auto)
        toolbar_layout.addWidget(fit)
        toolbar_layout.addWidget(reset)
        toolbar_layout.addWidget(self.zoom_label)
        self.scene = NodeGraphScene(self)
        self.view = NodeGraphView(self.scene, self)
        self.view.zoom_changed.connect(lambda value: self.zoom_label.setText(f"{value}%"))
        layout.addWidget(toolbar)
        layout.addWidget(self.view, 1)

    @staticmethod
    def step_summary(step: dict[str, Any]) -> str:
        action = str(step.get("action") or "")
        label = str(step.get("label") or "").strip()
        if label:
            return label
        if action == "image_search":
            return str(step.get("asset") or "이미지 선택 필요")
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
        return action or "단계"

    def set_macro(self, macro: dict[str, Any] | None, selected: int = 0) -> None:
        self.suspended = True
        self.hide_image_preview()
        self.active_step = 0
        self.scene.clear()
        self.macro = macro or {}
        self.steps = list(self.macro.get("steps") or [])
        self.nodes = {}
        self.edges = []
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
        self.rebuild_edges()
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

    def set_asset_previews(self, paths: dict[str, str | Path]) -> None:
        self._asset_preview_paths = {
            str(alias): Path(path).resolve()
            for alias, path in paths.items()
            if str(alias).strip() and Path(path).is_file()
        }

    def start_candidate_position(self, index: int) -> tuple[int, int] | None:
        try:
            return self.start_candidates.index(int(index)) + 1, len(self.start_candidates)
        except ValueError:
            return None

    def asset_preview_path(self, alias: str) -> Path | None:
        path = self._asset_preview_paths.get(str(alias))
        return path if path is not None and path.is_file() else None

    def show_image_preview(self, path: Path, alias: str, screen_pos: QtCore.QPoint) -> None:
        self._preview_popup.show_image(path, alias, screen_pos)

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
        outgoing: dict[int, list[int]] = {index: [] for index in range(1, total + 1)}
        indegree = {index: 0 for index in range(1, total + 1)}
        for index, step in enumerate(self.steps, start=1):
            targets: list[int] = []
            success = int(step.get("on_success") or 0)
            if success:
                targets.append(success)
            elif index < total and not bool(step.get("stop_on_success")):
                targets.append(index + 1)
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
        for rank, indexes in columns.items():
            center = (len(indexes) - 1) / 2.0
            for row, index in enumerate(indexes):
                positions[index] = QtCore.QPointF(rank * 350.0, (row - center) * 172.0)
        return positions

    def positions(self) -> dict[str, list[float]]:
        return {
            str(index): [round(node.pos().x(), 2), round(node.pos().y(), 2)]
            for index, node in self.nodes.items()
        }

    def node_moved(self) -> None:
        self._route_edges()
        self._positions_timer.start()

    def rebuild_edges(self) -> None:
        for edge in self.edges:
            if edge.scene() is self.scene:
                self.scene.removeItem(edge)
        self.edges = []
        for index, step in enumerate(self.steps, start=1):
            for field, kind in (("on_success", "success"), ("on_fail", "fail")):
                target = int(step.get(field) or 0)
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
        """Assign non-overlapping outer lanes to backward/overlapping links."""
        candidates: dict[str, list[tuple[float, float, EdgeItem]]] = {"top": [], "bottom": []}
        for edge in self.edges:
            edge.route_side = ""
            edge.route_lane = 0
            edge.target_offset_y = 0.0

        # Fan multiple incoming links across a small part of the target's left
        # edge.  This keeps close forward links visually separate instead of
        # forcing every path through the exact same point.
        incoming: dict[int, list[EdgeItem]] = {}
        for edge in self.edges:
            if edge.target in self.nodes and edge.source in self.nodes:
                incoming.setdefault(edge.target, []).append(edge)
        for target_edges in incoming.values():
            if len(target_edges) < 2:
                continue
            ordered = sorted(
                target_edges,
                key=lambda edge: (
                    self.nodes[edge.source].sceneBoundingRect().center().y(),
                    edge.source,
                    edge.kind,
                    edge.condition_index,
                ),
            )
            spacing = min(18.0, 42.0 / max(1, len(ordered) - 1))
            center = (len(ordered) - 1) / 2.0
            for position, edge in enumerate(ordered):
                edge.target_offset_y = (position - center) * spacing

        for edge in self.edges:
            source = self.nodes.get(edge.source)
            target = self.nodes.get(edge.target)
            if source is None or target is None:
                continue
            port = source.success_port if edge.kind == "success" else source.fail_port
            start = port.mapToScene(port.rect().center())
            end = target.mapToScene(QtCore.QPointF(0, NodeItem.HEIGHT / 2 + edge.target_offset_y))
            forward = end.x() > start.x() + 4.0
            obstacle = False
            if forward and end.x() - start.x() > 90.0:
                direct_corridor = QtCore.QRectF(
                    start.x(),
                    min(start.y(), end.y()) - 22.0,
                    max(1.0, end.x() - start.x()),
                    abs(end.y() - start.y()) + 44.0,
                )
                obstacle = any(
                    index not in {edge.source, edge.target}
                    and direct_corridor.intersects(node.sceneBoundingRect().adjusted(-10.0, -8.0, 10.0, 8.0))
                    for index, node in self.nodes.items()
                )
            if forward and not obstacle:
                continue
            side = "top" if edge.kind == "success" else "bottom"
            candidates[side].append((min(start.x(), end.x()), max(start.x(), end.x()), edge))

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

        for edge in self.edges:
            edge.update_path()

    def remove_edge(self, source: int, target: int, kind: str, condition_index: int = -1) -> None:
        for edge in list(self.edges):
            if edge.source == source and edge.target == target and edge.kind == kind and edge.condition_index == condition_index:
                if edge.scene() is self.scene:
                    self.scene.removeItem(edge)
                self.edges.remove(edge)
                break
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

    def auto_layout(self) -> None:
        if not self.nodes:
            return
        self.suspended = True
        positions = self._suggest_positions()
        for index, node in self.nodes.items():
            node.setPos(positions[index])
        self.suspended = False
        self._route_edges()
        self.positions_changed.emit(self.positions())
        self.fit_all()

    def fit_all(self) -> None:
        self.view.fit_all()

    def view_reset_zoom(self) -> None:
        self.view.reset_zoom()

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
        port = source.success_port if edge.kind == "success" else source.fail_port
        self.begin_link(source, edge.kind, port.mapToScene(port.rect().center()))
        edge.setOpacity(0.22)

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
        if self._temp_edge and self._temp_edge.scene() is self.scene:
            self.scene.removeItem(self._temp_edge)
        self._temp_edge = None
        edge.setOpacity(1.0)
        source = self.nodes.get(edge.source)
        if not source:
            return
        target = self._target_at(scene_pos, source)
        if target:
            if edge.is_condition:
                self.edge_condition_retarget_requested.emit(edge.source, edge.condition_index, target.index)
            else:
                self.link_requested.emit(edge.source, target.index, edge.kind)
        else:
            self.remove_edge(edge.source, edge.target, edge.kind, edge.condition_index)
            if edge.is_condition:
                self.edge_condition_delete_requested.emit(edge.source, edge.condition_index)
            else:
                self.edge_delete_requested.emit(edge.source, edge.target, edge.kind)

    def end_link(self, source: NodeItem, kind: str, scene_pos: QtCore.QPointF) -> None:
        if self._temp_edge and self._temp_edge.scene() is self.scene:
            self.scene.removeItem(self._temp_edge)
        self._temp_edge = None
        target = self._target_at(scene_pos, source)
        if target:
            self.link_requested.emit(source.index, target.index, kind)
