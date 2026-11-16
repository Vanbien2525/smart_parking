"""Widget Sa bàn Bãi giữ xe 3D tương tác bằng QPainter 3D Projection thuần PySide6.

Dùng chung cho Quản lý (1.6), Nhân viên (2.6) và Admin Read-Only (4.8):
- Không phụ thuộc driver OpenGL rời, render mượt 60fps trên mọi máy Windows/Linux.
- Hỗ trợ kéo chuột trái xoay góc nhìn (yaw/pitch), cuộn chuột Zoom (0.6x–2.2x),
  kéo chuột phải để dịch chuyển (pan), click vào ô/xe 3D để xem chi tiết.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QPushButton, QWidget

from client.ui.theme.components import set_button_variant
from client.ui.theme.tokens import Colors, Fonts, SLOT_STATE_STYLES


class Parking3DSceneWidget(QWidget):
    """Sa bàn 3D tương tác hiển thị các ô đỗ xe theo 4 trạng thái chuẩn CSDL."""

    slot_clicked = Signal(dict)

    DEFAULT_YAW_DEG = -34.0
    DEFAULT_PITCH_DEG = 40.0
    DEFAULT_ZOOM = 1.0

    def __init__(self, columns: int = 4, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._columns = max(1, int(columns))
        self._slots: List[Dict[str, Any]] = []

        self._yaw_deg = self.DEFAULT_YAW_DEG
        self._pitch_deg = self.DEFAULT_PITCH_DEG
        self._zoom = self.DEFAULT_ZOOM
        self._pan_x = 0.0
        self._pan_y = 0.0

        self._drag_mode: Optional[str] = None
        self._last_mouse_pos = QPointF()
        self._press_pos = QPointF()
        self._hovered_code: Optional[str] = None
        self._selected_code: Optional[str] = None

        # Danh sách đa giác 2D đã chiếu để hit-test click chuột theo thứ tự z-order
        self._hit_polygons: List[Tuple[QPolygonF, Dict[str, Any]]] = []

        self.setMinimumHeight(380)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.btn_reset_view = QPushButton("Đặt lại góc nhìn 3D", self)
        set_button_variant(self.btn_reset_view, "default", "sm")
        self.btn_reset_view.clicked.connect(self.reset_view)

    def set_slots(self, slots: Iterable[Dict[str, Any]], columns: Optional[int] = None) -> None:
        """Nạp danh sách ô đỗ `vi_tri_do` để dựng sa bàn 3D."""
        self._slots = [dict(s) for s in slots]
        if columns is not None:
            self._columns = max(1, int(columns))
        self.update()

    def set_selected_slot(self, ma_vi_tri: Optional[str]) -> None:
        self._selected_code = ma_vi_tri
        self.update()

    def reset_view(self) -> None:
        """Khôi phục góc nhìn 3D mặc định."""
        self._yaw_deg = self.DEFAULT_YAW_DEG
        self._pitch_deg = self.DEFAULT_PITCH_DEG
        self._zoom = self.DEFAULT_ZOOM
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.update()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.btn_reset_view.adjustSize()
        self.btn_reset_view.move(self.width() - self.btn_reset_view.width() - 14, 14)

    # --------------------------------------------------------------------------
    # PHÉP CHIẾU 3D -> 2D (EULER YAW / PITCH + WEAK PERSPECTIVE)
    # --------------------------------------------------------------------------
    def project_point(self, x: float, y: float, z: float) -> Tuple[float, float, float]:
        """Chiếu điểm 3D (x: ngang, y: sâu, z: cao) lên tọa độ màn hình (sx, sy, depth)."""
        yaw = math.radians(self._yaw_deg)
        pitch = math.radians(self._pitch_deg)

        # Xoay quanh trục Z (yaw)
        x1 = x * math.cos(yaw) - y * math.sin(yaw)
        y1 = x * math.sin(yaw) + y * math.cos(yaw)
        z1 = z

        # Xoay quanh trục X (pitch) — nghiêng camera nhìn từ trên xuống
        y2 = y1 * math.cos(pitch) - z1 * math.sin(pitch)
        z2 = y1 * math.sin(pitch) + z1 * math.cos(pitch)

        # Phối cảnh nhẹ để sa bàn trông có chiều sâu tự nhiên nhưng không bị méo quá mức
        cam_dist = 18.0
        perspective = cam_dist / max(4.0, cam_dist + y2 * 0.45)
        scale = 42.0 * self._zoom * perspective

        cx = self.width() * 0.5 + self._pan_x
        cy = self.height() * 0.54 + self._pan_y
        sx = cx + x1 * scale
        sy = cy - z2 * scale
        return sx, sy, y2

    def _poly_from_3d(self, points_3d: List[Tuple[float, float, float]]) -> Tuple[QPolygonF, float]:
        projected = [self.project_point(px, py, pz) for px, py, pz in points_3d]
        poly = QPolygonF([QPointF(sx, sy) for sx, sy, _ in projected])
        avg_depth = sum(d for _, _, d in projected) / max(1, len(projected))
        return poly, avg_depth

    def _draw_box_3d(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        z0: float,
        dx: float,
        dy: float,
        dz: float,
        top_color: QColor,
        side_color_a: QColor,
        side_color_b: QColor,
        border_color: QColor,
    ) -> None:
        """Vẽ khối hộp 3D theo thứ tự mặt từ xa tới gần (Painter's Algorithm)."""
        hx, hy = dx * 0.5, dy * 0.5
        p000 = (cx - hx, cy - hy, z0)
        p100 = (cx + hx, cy - hy, z0)
        p110 = (cx + hx, cy + hy, z0)
        p010 = (cx - hx, cy + hy, z0)
        p001 = (cx - hx, cy - hy, z0 + dz)
        p101 = (cx + hx, cy - hy, z0 + dz)
        p111 = (cx + hx, cy + hy, z0 + dz)
        p011 = (cx - hx, cy + hy, z0 + dz)

        faces = [
            ([p000, p100, p101, p001], side_color_a),
            ([p100, p110, p111, p101], side_color_b),
            ([p110, p010, p011, p111], side_color_a),
            ([p010, p000, p001, p011], side_color_b),
            ([p001, p101, p111, p011], top_color),
        ]
        projected_faces = []
        for pts, color in faces:
            poly, depth = self._poly_from_3d(pts)
            projected_faces.append((depth, poly, color))

        # Depth lớn hơn nằm xa hơn -> vẽ trước
        projected_faces.sort(key=lambda item: item[0], reverse=True)
        painter.setPen(QPen(border_color, 1.1))
        for _, poly, color in projected_faces:
            painter.setBrush(QBrush(color))
            painter.drawPolygon(poly)

    # --------------------------------------------------------------------------
    # VẼ SA BÀN & TỪNG Ô ĐỖ
    # --------------------------------------------------------------------------
    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Nền canvas sáng-xám nhẹ chuẩn Design DNA
        painter.fillRect(self.rect(), QColor(Colors.BG_APP))
        self._hit_polygons = []

        if not self._slots:
            painter.setPen(QColor(Colors.TEXT_MUTED))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Chưa có vị trí đỗ để dựng sa bàn 3D.",
            )
            self._draw_hud(painter)
            return

        cols = self._columns
        total = len(self._slots)
        rows = math.ceil(total / cols)
        slot_w = 1.65
        slot_d = 2.15
        gap_x = 0.42
        gap_y = 0.70  # Lối đi giữa các hàng ô đỗ

        grid_w = cols * slot_w + max(0, cols - 1) * gap_x
        grid_d = rows * slot_d + max(0, rows - 1) * gap_y

        # Vẽ tấm sàn bãi xe tổng thể
        pad = 0.9
        floor_pts = [
            (-grid_w * 0.5 - pad, -grid_d * 0.5 - pad, -0.06),
            (grid_w * 0.5 + pad, -grid_d * 0.5 - pad, -0.06),
            (grid_w * 0.5 + pad, grid_d * 0.5 + pad, -0.06),
            (-grid_w * 0.5 - pad, grid_d * 0.5 + pad, -0.06),
        ]
        floor_poly, _ = self._poly_from_3d(floor_pts)
        painter.setPen(QPen(QColor("#CBD5E1"), 1.5))
        painter.setBrush(QBrush(QColor("#E2E8F0")))
        painter.drawPolygon(floor_poly)

        # Vẽ các hàng ô đỗ theo Painter's Algorithm (sắp theo độ sâu camera)
        slot_items: List[Tuple[float, float, float, Dict[str, Any]]] = []
        for idx, slot in enumerate(self._slots):
            r = idx // cols
            c = idx % cols
            cx = -grid_w * 0.5 + slot_w * 0.5 + c * (slot_w + gap_x)
            cy = -grid_d * 0.5 + slot_d * 0.5 + r * (slot_d + gap_y)
            _, _, depth = self.project_point(cx, cy, 0.0)
            slot_items.append((depth, cx, cy, slot))

        slot_items.sort(key=lambda item: item[0], reverse=True)

        for _, cx, cy, slot in slot_items:
            self._draw_single_slot(painter, cx, cy, slot_w, slot_d, slot)

        self._draw_hud(painter)

    def _draw_single_slot(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        slot_w: float,
        slot_d: float,
        slot: Dict[str, Any],
    ) -> None:
        code = str(slot.get("ma_vi_tri") or "---")
        status = str(slot.get("trang_thai_vi_tri") or "trong")
        plate = str(slot.get("bien_so_xe") or "").strip()
        vehicle_type = str(slot.get("ma_loai_xe") or "O_TO")

        style = SLOT_STATE_STYLES.get(status, SLOT_STATE_STYLES["trong"])
        bg_color = QColor(style["bg"])
        border_color = QColor(style["border"])
        text_color = QColor(style["text"])

        is_hovered = self._hovered_code == code
        is_selected = self._selected_code == code

        hx, hy = slot_w * 0.5, slot_d * 0.5
        tile_pts = [
            (cx - hx, cy - hy, 0.0),
            (cx + hx, cy - hy, 0.0),
            (cx + hx, cy + hy, 0.0),
            (cx - hx, cy + hy, 0.0),
        ]
        tile_poly, _ = self._poly_from_3d(tile_pts)

        if is_selected:
            pen = QPen(QColor(Colors.ROYAL_BLUE), 2.8)
        elif is_hovered:
            pen = QPen(QColor(Colors.TEXT_PRIMARY), 2.4)
        else:
            pen = QPen(border_color, 1.6)

        painter.setPen(pen)
        painter.setBrush(QBrush(bg_color))
        painter.drawPolygon(tile_poly)
        self._hit_polygons.append((tile_poly, slot))

        # Nhãn mã vị trí ngay trên mặt sàn
        sx_lbl, sy_lbl, _ = self.project_point(cx, cy + hy * 0.55, 0.02)
        painter.setPen(text_color)
        painter.setFont(QFont("Consolas", max(7, int(8.5 * min(1.3, self._zoom))), QFont.Weight.Bold))
        painter.drawText(
            QRectF(sx_lbl - 46, sy_lbl - 11, 92, 22),
            Qt.AlignmentFlag.AlignCenter,
            code,
        )

        if status == "dang_co_xe":
            self._draw_vehicle_3d(painter, cx, cy - 0.08, vehicle_type, plate)
        elif status == "da_phan_cho":
            # Khối trụ giữ chỗ 3D màu xanh dương
            self._draw_box_3d(
                painter,
                cx=cx,
                cy=cy - 0.1,
                z0=0.0,
                dx=0.38,
                dy=0.38,
                dz=0.58,
                top_color=QColor("#60A5FA"),
                side_color_a=QColor("#2563EB"),
                side_color_b=QColor("#1D4ED8"),
                border_color=QColor("#1E3A8A"),
            )
            if plate:
                self._draw_floating_plate(painter, cx, cy - 0.1, 0.85, plate, is_reserved=True)
        elif status == "ngung_su_dung":
            # Khối rào chắn bảo trì 3D xám-cam
            self._draw_box_3d(
                painter,
                cx=cx,
                cy=cy - 0.05,
                z0=0.0,
                dx=slot_w * 0.72,
                dy=0.22,
                dz=0.45,
                top_color=QColor("#FDBA74"),
                side_color_a=QColor("#F97316"),
                side_color_b=QColor("#64748B"),
                border_color=QColor("#475569"),
            )

    def _draw_vehicle_3d(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        vehicle_type: str,
        plate: str,
    ) -> None:
        if vehicle_type == "XE_MAY":
            # Khối xe máy gọn hơn
            self._draw_box_3d(
                painter,
                cx=cx,
                cy=cy,
                z0=0.04,
                dx=0.48,
                dy=1.05,
                dz=0.52,
                top_color=QColor("#38BDF8"),
                side_color_a=QColor("#0284C7"),
                side_color_b=QColor("#0369A1"),
                border_color=QColor("#0F172A"),
            )
            label_z = 0.86
        else:
            # Thân xe ô tô
            self._draw_box_3d(
                painter,
                cx=cx,
                cy=cy,
                z0=0.05,
                dx=1.10,
                dy=1.45,
                dz=0.38,
                top_color=QColor("#F87171"),
                side_color_a=QColor("#DC2626"),
                side_color_b=QColor("#B91C1C"),
                border_color=QColor("#7F1D1D"),
            )
            # Mui xe (cabin)
            self._draw_box_3d(
                painter,
                cx=cx,
                cy=cy - 0.06,
                z0=0.43,
                dx=0.86,
                dy=0.85,
                dz=0.30,
                top_color=QColor("#FCA5A5"),
                side_color_a=QColor("#1E293B"),
                side_color_b=QColor("#334155"),
                border_color=QColor("#0F172A"),
            )
            label_z = 1.05

        if plate:
            self._draw_floating_plate(painter, cx, cy, label_z, plate, is_reserved=False)

    def _draw_floating_plate(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        z: float,
        plate: str,
        is_reserved: bool = False,
    ) -> None:
        sx, sy, _ = self.project_point(cx, cy, z)
        w = max(72.0, min(106.0, 82.0 * self._zoom))
        h = 20.0
        rect = QRectF(sx - w * 0.5, sy - h * 0.5, w, h)
        painter.setPen(QPen(QColor("#1E40AF" if is_reserved else Colors.TEXT_PRIMARY), 1.4))
        painter.setBrush(QBrush(QColor("#EFF6FF" if is_reserved else "#FFFFFF")))
        painter.drawRoundedRect(rect, 4.0, 4.0)

        painter.setPen(QColor("#1E3A8A" if is_reserved else Colors.TEXT_PRIMARY))
        painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, plate)

    def _draw_hud(self, painter: QPainter) -> None:
        # Góc trên trái: hướng dẫn thao tác 3D + góc hiện tại
        hud_rect = QRectF(14, 14, 380, 32)
        painter.setPen(QPen(QColor(Colors.BORDER), 1.0))
        painter.setBrush(QBrush(QColor(255, 255, 255, 225)))
        painter.drawRoundedRect(hud_rect, 8.0, 8.0)

        painter.setPen(QColor(Colors.TEXT_SECONDARY))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(
            hud_rect.adjusted(10, 0, -10, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            f"3D Sa bàn · Kéo trái: Xoay ({int(self._yaw_deg)}°/{int(self._pitch_deg)}°) · "
            f"Cuộn: Zoom ({self._zoom:.1f}x) · Click ô để xem",
        )

    # --------------------------------------------------------------------------
    # SỰ KIỆN CHUỘT XOAY / ZOOM / PAN / CLICK Ô 3D
    # --------------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._press_pos = event.position()
        self._last_mouse_pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = "rotate"
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._drag_mode = "pan"
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._drag_mode == "rotate":
            dx = pos.x() - self._last_mouse_pos.x()
            dy = pos.y() - self._last_mouse_pos.y()
            self._yaw_deg = (self._yaw_deg + dx * 0.45) % 360.0
            self._pitch_deg = max(20.0, min(75.0, self._pitch_deg + dy * 0.35))
            self._last_mouse_pos = pos
            self.update()
        elif self._drag_mode == "pan":
            dx = pos.x() - self._last_mouse_pos.x()
            dy = pos.y() - self._last_mouse_pos.y()
            self._pan_x += dx
            self._pan_y += dy
            self._last_mouse_pos = pos
            self.update()
        else:
            hit = self._hit_test(pos)
            new_code = str(hit.get("ma_vi_tri")) if hit else None
            if new_code != self._hovered_code:
                self._hovered_code = new_code
                self.setCursor(
                    Qt.CursorShape.PointingHandCursor
                    if hit is not None
                    else Qt.CursorShape.OpenHandCursor
                )
                self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode == "rotate" and event.button() == Qt.MouseButton.LeftButton:
            dist = (event.position() - self._press_pos).manhattanLength()
            if dist < 5.0:
                hit = self._hit_test(event.position())
                if hit is not None:
                    self._selected_code = str(hit.get("ma_vi_tri") or "")
                    self.slot_clicked.emit(dict(hit))
                    self.update()
        self._drag_mode = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta != 0:
            factor = 1.1 if delta > 0 else 0.9
            self._zoom = max(0.6, min(2.2, round(self._zoom * factor, 3)))
            self.update()
        event.accept()

    def _hit_test(self, pos: QPointF) -> Optional[Dict[str, Any]]:
        # Duyệt ngược danh sách vì phần tử vẽ sau (gần camera hơn) nằm ở cuối
        for poly, slot in reversed(self._hit_polygons):
            if poly.containsPoint(pos, Qt.FillRule.OddEvenFill):
                return slot
        return None
