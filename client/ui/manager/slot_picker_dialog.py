"""Hộp thoại chọn trực quan Ô đỗ Ô tô / Khu vực Xe máy & Xem ảnh Cà-vẹt phóng to/xoay.

Dùng chung cho:
- Quản lý duyệt yêu cầu đăng ký phương tiện (`1.4.2` — Workflow 05)
- Quản lý duyệt yêu cầu mua thêm chỗ đỗ ô tô (`1.4.16`)
- Xem ảnh cà-vẹt độ phân giải gốc có Zoom / Xoay (`ZoomableCavetDialog`)
"""

import base64
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QTransform
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    PlateBadge,
    SectionCard,
    StatusBadge,
    set_button_variant,
)
from client.ui.theme.tokens import Colors, Fonts, SLOT_STATE_STYLES
from shared.constants import VehicleTypes
from shared.protocol import Actions


class CavetPreviewWidget(QWidget):
    """Khung hiển thị ảnh cà-vẹt hỗ trợ Phóng to (+), Thu nhỏ (-), Xoay 90° & Reset."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._base_pixmap: Optional[QPixmap] = None
        self._zoom = 1.0
        self._angle = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self.btn_zoom_out = QPushButton("- Thu nhỏ")
        self.btn_zoom_in = QPushButton("+ Phóng to")
        self.btn_rotate = QPushButton("Xoay 90°")
        self.btn_reset = QPushButton("Mặc định")
        for btn in (self.btn_zoom_out, self.btn_zoom_in, self.btn_rotate, self.btn_reset):
            set_button_variant(btn, "default", "sm")

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 12px; color: {Colors.TEXT_MUTED};"
        )

        self.btn_zoom_out.clicked.connect(lambda: self._adjust_zoom(-0.2))
        self.btn_zoom_in.clicked.connect(lambda: self._adjust_zoom(0.2))
        self.btn_rotate.clicked.connect(self._rotate_90)
        self.btn_reset.clicked.connect(self._reset_transform)

        toolbar.addWidget(self.btn_zoom_out)
        toolbar.addWidget(self.btn_zoom_in)
        toolbar.addWidget(self.btn_rotate)
        toolbar.addWidget(self.btn_reset)
        toolbar.addStretch(1)
        toolbar.addWidget(self.lbl_zoom)
        root.addLayout(toolbar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(260)
        self.scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {Colors.BG_ELEVATED}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 10px; }}"
        )

        self.lbl_image = QLabel("Chưa có ảnh cà-vẹt")
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_image.setStyleSheet(f"color: {Colors.TEXT_MUTED}; padding: 16px;")
        self.scroll.setWidget(self.lbl_image)
        root.addWidget(self.scroll, 1)

    def set_base64_image(self, b64_str: str) -> bool:
        """Nạp ảnh từ chuỗi Base64."""
        if not b64_str:
            self.clear_image("Chưa có dữ liệu ảnh cà-vẹt")
            return False
        text = b64_str.strip()
        if text.startswith("data:") and ";base64," in text:
            text = text.split(";base64,", 1)[1]
        try:
            raw = base64.b64decode(text)
        except Exception:
            self.clear_image("Dữ liệu ảnh Base64 không hợp lệ")
            return False
        pix = QPixmap()
        if not pix.loadFromData(raw):
            self.clear_image("Không giải mã được định dạng ảnh")
            return False
        self.set_pixmap(pix)
        return True

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._base_pixmap = pixmap
        self._zoom = 1.0
        self._angle = 0
        self._render_pixmap()

    def clear_image(self, message: str = "Chưa có ảnh cà-vẹt") -> None:
        self._base_pixmap = None
        self.lbl_image.setPixmap(QPixmap())
        self.lbl_image.setText(message)
        self.lbl_zoom.setText("100%")

    def _adjust_zoom(self, delta: float) -> None:
        self._zoom = max(0.4, min(3.0, round(self._zoom + delta, 2)))
        self._render_pixmap()

    def _rotate_90(self) -> None:
        self._angle = (self._angle + 90) % 360
        self._render_pixmap()

    def _reset_transform(self) -> None:
        self._zoom = 1.0
        self._angle = 0
        self._render_pixmap()

    def _render_pixmap(self) -> None:
        self.lbl_zoom.setText(f"{int(round(self._zoom * 100))}%")
        if self._base_pixmap is None or self._base_pixmap.isNull():
            return
        transformed = self._base_pixmap
        if self._angle != 0:
            transformed = transformed.transformed(
                QTransform().rotate(self._angle),
                Qt.TransformationMode.SmoothTransformation,
            )
        target_w = max(80, int(min(transformed.width(), 540) * self._zoom))
        target_h = max(80, int(min(transformed.height(), 420) * self._zoom))
        scaled = transformed.scaled(
            target_w,
            target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.lbl_image.setText("")
        self.lbl_image.setPixmap(scaled)


class ZoomableCavetDialog(QDialog):
    """Hộp thoại phóng to & đối chiếu ảnh cà-vẹt (dùng cho Quản lý & Cư dân)."""

    def __init__(
        self,
        title: str = "Ảnh Giấy Đăng Ký Xe (Cà-vẹt)",
        image_base64: str = "",
        socket_client: Optional[SocketClient] = None,
        ma_phuong_tien: Optional[int] = None,
        duong_dan_cavet: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(680, 540)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        self.preview = CavetPreviewWidget(self)
        root.addWidget(self.preview, 1)

        if image_base64:
            self.preview.set_base64_image(image_base64)
        elif socket_client is not None and (ma_phuong_tien is not None or duong_dan_cavet):
            payload: Dict[str, Any] = {}
            if ma_phuong_tien is not None:
                payload["ma_phuong_tien"] = ma_phuong_tien
            if duong_dan_cavet:
                payload["duong_dan_cavet"] = duong_dan_cavet
            try:
                resp = socket_client.call(Actions.VEHICLE_GET_CAVET, payload, timeout=8.0)
                if resp.get("ok") and isinstance(resp.get("data"), dict):
                    self.preview.set_base64_image(str(resp["data"].get("image_base64") or ""))
                else:
                    self.preview.clear_image(str(resp.get("error") or "Không tải được ảnh cà-vẹt."))
            except Exception as exc:
                self.preview.clear_image(f"Lỗi kết nối khi tải ảnh: {exc}")

        btn_close = QPushButton("Đóng")
        set_button_variant(btn_close, "default", "sm")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)


class InteractiveSlotPicker(QWidget):
    """Widget chọn Ô đỗ ô tô trực quan hoặc Khu vực xe máy khả dụng."""

    selection_changed = Signal(str)

    def __init__(self, ma_loai_xe: str = VehicleTypes.O_TO.value, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ma_loai_xe = str(ma_loai_xe or VehicleTypes.O_TO.value).upper()
        self._selected_code: Optional[str] = None
        self._zones: List[Dict[str, Any]] = []
        self._slots: List[Dict[str, Any]] = []
        self._tile_buttons: Dict[str, QPushButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self.lbl_instruction = QLabel("")
        self.lbl_instruction.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_SECONDARY};")
        top_bar.addWidget(self.lbl_instruction, 1)

        self.cbo_zone = QComboBox()
        self.cbo_zone.currentIndexChanged.connect(self._render_items)
        top_bar.addWidget(self.cbo_zone)
        root.addLayout(top_bar)

        self.legend_host = QWidget()
        legend_lay = QHBoxLayout(self.legend_host)
        legend_lay.setContentsMargins(0, 0, 0, 0)
        legend_lay.setSpacing(8)
        legend_lay.addWidget(StatusBadge("Trống (Chọn được)", "success"))
        legend_lay.addWidget(StatusBadge("Suất đã cấp sẵn cho căn hộ", "primary"))
        legend_lay.addWidget(StatusBadge("Đang có xe / Đã cấp khác", "danger"))
        legend_lay.addWidget(StatusBadge("Ngừng dùng / Vãng lai", "neutral"))
        legend_lay.addStretch(1)
        root.addWidget(self.legend_host)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content_host = QWidget()
        self._grid_or_list = QVBoxLayout(self._content_host)
        self._grid_or_list.setContentsMargins(2, 2, 2, 2)
        self._grid_or_list.setSpacing(8)
        self.scroll.setWidget(self._content_host)
        root.addWidget(self.scroll, 1)

        self.lbl_selected = QLabel("Chưa chọn vị trí / khu vực")
        self.lbl_selected.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 13px; font-weight: 700; "
            f"color: {Colors.ROYAL_BLUE}; padding: 6px 10px; "
            f"background-color: {Colors.ROYAL_BLUE_SOFT}; border-radius: 8px;"
        )
        root.addWidget(self.lbl_selected)

    def set_data(self, assignable_data: Dict[str, Any]) -> None:
        self._ma_loai_xe = str(assignable_data.get("ma_loai_xe") or self._ma_loai_xe).upper()
        self._zones = list(assignable_data.get("zones") or [])
        self._slots = list(assignable_data.get("slots") or [])
        self._selected_code = None

        self.cbo_zone.blockSignals(True)
        self.cbo_zone.clear()
        if self._ma_loai_xe == VehicleTypes.O_TO.value:
            self.lbl_instruction.setText("Click vào ô đỗ trống (Xanh lá) hoặc suất sẵn của căn hộ (Xanh dương):")
            self.cbo_zone.setVisible(True)
            self.legend_host.setVisible(True)
            self.cbo_zone.addItem("Tất cả khu vực ô tô", "")
            for z in self._zones:
                self.cbo_zone.addItem(f"{z.get('ten_khu_vuc')} ({z.get('ma_khu_vuc')})", z.get("ma_khu_vuc"))
        else:
            self.lbl_instruction.setText("Chọn một khu vực đỗ Xe máy đang hoạt động để phân công:")
            self.cbo_zone.setVisible(False)
            self.legend_host.setVisible(False)
        self.cbo_zone.blockSignals(False)

        # Nếu căn hộ có sẵn ô mua thêm chưa gắn xe, tự gợi ý chọn ô đầu tiên
        if self._ma_loai_xe == VehicleTypes.O_TO.value:
            pre = assignable_data.get("preallocated_slots") or []
            if pre:
                self._selected_code = str(pre[0].get("ma_vi_tri") or "")
        elif self._zones:
            self._selected_code = str(self._zones[0].get("ma_khu_vuc") or "")

        self._render_items()
        self._update_selected_banner()

    def selected_code(self) -> Optional[str]:
        return self._selected_code

    def _clear_layout(self) -> None:
        self._tile_buttons.clear()
        while self._grid_or_list.count():
            item = self._grid_or_list.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render_items(self) -> None:
        self._clear_layout()
        if self._ma_loai_xe == VehicleTypes.XE_MAY.value:
            self._render_moto_zones()
        else:
            self._render_car_slots()

    def _render_car_slots(self) -> None:
        zone_filter = str(self.cbo_zone.currentData() or "").strip()
        filtered = [
            s for s in self._slots
            if not zone_filter or str(s.get("ma_khu_vuc") or "") == zone_filter
        ]
        if not filtered:
            self._grid_or_list.addWidget(QLabel("Không có ô đỗ ô tô khả dụng trong khu vực này."))
            self._grid_or_list.addStretch(1)
            return

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        cols = 4
        for idx, slot in enumerate(filtered):
            code = str(slot.get("ma_vi_tri") or "")
            status = str(slot.get("trang_thai_vi_tri") or "trong")
            is_pre = bool(slot.get("is_apartment_preallocated"))
            assignable = bool(slot.get("assignable"))
            plate = str(slot.get("bien_so_xe") or "").strip()

            btn = QPushButton()
            btn.setMinimumSize(112, 68)
            btn.setCursor(
                Qt.CursorShape.PointingHandCursor if assignable else Qt.CursorShape.ForbiddenCursor
            )
            btn.setEnabled(assignable)

            if is_pre:
                sub = "Suất sẵn CH"
                bg, border, fg = "#DBEAFE", Colors.ROYAL_BLUE, "#1E40AF"
            elif assignable:
                sub = "Trống"
                st = SLOT_STATE_STYLES["trong"]
                bg, border, fg = st["bg"], st["border"], st["text"]
            elif slot.get("nhom_vi_tri") == "vi_tri_vang_lai":
                sub = "Vãng lai"
                st = SLOT_STATE_STYLES["ngung_su_dung"]
                bg, border, fg = st["bg"], st["border"], st["text"]
            else:
                sub = plate or SLOT_STATE_STYLES.get(status, SLOT_STATE_STYLES["ngung_su_dung"])["label"]
                st = SLOT_STATE_STYLES.get(status, SLOT_STATE_STYLES["ngung_su_dung"])
                bg, border, fg = st["bg"], st["border"], st["text"]

            btn.setText(f"{code}\n{sub}")
            btn.setProperty("base_bg", bg)
            btn.setProperty("base_border", border)
            btn.setProperty("base_fg", fg)
            btn.clicked.connect(lambda _c=False, c=code: self._on_pick(c))
            self._tile_buttons[code] = btn
            self._style_tile(btn, selected=(code == self._selected_code))
            grid.addWidget(btn, idx // cols, idx % cols)

        self._grid_or_list.addWidget(grid_host)
        self._grid_or_list.addStretch(1)

    def _render_moto_zones(self) -> None:
        if not self._zones:
            self._grid_or_list.addWidget(QLabel("Không có khu vực xe máy đang hoạt động."))
            self._grid_or_list.addStretch(1)
            return

        for z in self._zones:
            code = str(z.get("ma_khu_vuc") or "")
            name = str(z.get("ten_khu_vuc") or code)
            total = max(1, int(z.get("tong_so_cho") or 1))
            used = int(z.get("so_xe_dang_gui") or 0)
            free = max(0, total - used)

            card = QFrame()
            is_sel = code == self._selected_code
            card.setStyleSheet(
                f"QFrame {{ background-color: {'#EFF6FF' if is_sel else Colors.BG_CARD}; "
                f"border: {'2px solid ' + Colors.ROYAL_BLUE if is_sel else '1px solid ' + Colors.BORDER}; "
                f"border-radius: 10px; }}"
            )
            lay = QVBoxLayout(card)
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(6)

            row = QHBoxLayout()
            lbl = QLabel(f"{name} ({code})")
            lbl.setStyleSheet(f"font-size: 13.5px; font-weight: 700; color: {Colors.TEXT_PRIMARY}; border: none;")
            btn_pick = QPushButton("Đang chọn" if is_sel else "Chọn khu vực này")
            set_button_variant(btn_pick, "primary" if is_sel else "default", "sm")
            btn_pick.clicked.connect(lambda _c=False, c=code: self._on_pick_zone(c))
            row.addWidget(lbl, 1)
            row.addWidget(btn_pick)
            lay.addLayout(row)

            bar = QProgressBar()
            bar.setRange(0, total)
            bar.setValue(min(used, total))
            bar.setFormat(f"Đang gửi: {used}/{total} chỗ (Còn trống: {free})")
            lay.addWidget(bar)
            self._grid_or_list.addWidget(card)

        self._grid_or_list.addStretch(1)

    def _style_tile(self, btn: QPushButton, selected: bool) -> None:
        bg = btn.property("base_bg")
        border = Colors.ROYAL_BLUE if selected else btn.property("base_border")
        fg = btn.property("base_fg")
        bw = "3px" if selected else "1.5px"
        btn.setStyleSheet(
            f"QPushButton {{ font-family: {Fonts.MONO_FAMILY}; font-size: 12px; font-weight: 700; "
            f"background-color: {bg}; color: {fg}; border: {bw} solid {border}; border-radius: 8px; padding: 6px; }}"
            f"QPushButton:hover:enabled {{ border: 2px solid {Colors.TEXT_PRIMARY}; }}"
        )

    def _on_pick(self, code: str) -> None:
        self._selected_code = code
        for tile_code, btn in self._tile_buttons.items():
            self._style_tile(btn, selected=(tile_code == code))
        self._update_selected_banner()
        self.selection_changed.emit(code)

    def _on_pick_zone(self, code: str) -> None:
        self._selected_code = code
        self._render_items()
        self._update_selected_banner()
        self.selection_changed.emit(code)

    def _update_selected_banner(self) -> None:
        if not self._selected_code:
            self.lbl_selected.setText("Chưa chọn vị trí / khu vực")
            return
        prefix = "Ô đỗ đã chọn:" if self._ma_loai_xe == VehicleTypes.O_TO.value else "Khu vực đã chọn:"
        self.lbl_selected.setText(f"{prefix} {self._selected_code}")


class SlotPickerDialog(QDialog):
    """Hộp thoại duyệt & gán chỗ đỗ trực quan (dùng cho 1.4.2 và 1.4.16)."""

    def __init__(
        self,
        title: str,
        ma_loai_xe: str = VehicleTypes.O_TO.value,
        ma_can_ho: str = "",
        summary_info: Optional[Dict[str, Any]] = None,
        socket_client: Optional[SocketClient] = None,
        assignable_data: Optional[Dict[str, Any]] = None,
        cavet_base64: str = "",
        ma_phuong_tien: Optional[int] = None,
        show_cavet_panel: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(960 if show_cavet_panel else 620, 620)
        self.setModal(True)

        self._ma_loai_xe = str(ma_loai_xe or VehicleTypes.O_TO.value).upper()
        self._socket_client = socket_client

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        info = summary_info or {}
        header_card = SectionCard(
            title=title,
            subtitle=(
                f"Căn hộ: {ma_can_ho or info.get('ma_can_ho', '-')} · "
                f"Chủ xe: {info.get('ten_cu_dan', '-')} · "
                f"Loại xe: {self._ma_loai_xe}"
            ),
        )
        if info.get("bien_so_xe"):
            header_card.add_header_widget(PlateBadge(str(info["bien_so_xe"])))
        root.addWidget(header_card)

        self.picker = InteractiveSlotPicker(ma_loai_xe=self._ma_loai_xe, parent=self)
        self.cavet_preview: Optional[CavetPreviewWidget] = None

        if show_cavet_panel:
            splitter = QSplitter(Qt.Orientation.Horizontal)
            left_card = SectionCard(
                title="Đối chiếu Giấy đăng ký xe (Cà-vẹt)",
                subtitle=f"Xe: {info.get('ten_hang_dong_xe', '-')} · Màu: {info.get('mau_xe', '-')}",
            )
            self.cavet_preview = CavetPreviewWidget(self)
            left_card.body_layout.addWidget(self.cavet_preview, 1)
            splitter.addWidget(left_card)

            right_card = SectionCard(
                title="Chọn Vị trí / Khu vực phân bổ",
                subtitle="Hỗ trợ chọn ô trống hoặc suất căn hộ đã mua sẵn",
            )
            right_card.body_layout.addWidget(self.picker, 1)
            splitter.addWidget(right_card)
            splitter.setSizes([430, 500])
            root.addWidget(splitter, 1)
        else:
            pick_card = SectionCard(
                title="Chọn Ô đỗ Ô tô trống để cấp cho Căn hộ",
                subtitle=f"Nhu cầu vị trí cư dân ghi chú: {info.get('nhu_cau_vi_tri', '-')}",
            )
            pick_card.body_layout.addWidget(self.picker, 1)
            root.addWidget(pick_card, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = QPushButton("Hủy")
        set_button_variant(btn_cancel, "default", "sm")
        btn_cancel.clicked.connect(self.reject)
        self.btn_confirm = QPushButton("Xác nhận duyệt & Gán chỗ")
        set_button_variant(self.btn_confirm, "success", "md")
        self.btn_confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_confirm)
        root.addLayout(btn_row)

        if assignable_data is not None:
            self.picker.set_data(assignable_data)
        elif socket_client is not None and socket_client.is_connected():
            try:
                resp = socket_client.call(
                    Actions.VEHICLE_GET_ASSIGNABLE,
                    {"ma_loai_xe": self._ma_loai_xe, "ma_can_ho": ma_can_ho},
                    timeout=6.0,
                )
                if resp.get("ok") and isinstance(resp.get("data"), dict):
                    self.picker.set_data(resp["data"])
            except Exception:
                pass

        if self.cavet_preview is not None:
            if cavet_base64:
                self.cavet_preview.set_base64_image(cavet_base64)
            elif socket_client is not None and socket_client.is_connected() and ma_phuong_tien is not None:
                try:
                    c_resp = socket_client.call(
                        Actions.VEHICLE_GET_CAVET,
                        {"ma_phuong_tien": ma_phuong_tien},
                        timeout=8.0,
                    )
                    if c_resp.get("ok") and isinstance(c_resp.get("data"), dict):
                        self.cavet_preview.set_base64_image(str(c_resp["data"].get("image_base64") or ""))
                except Exception:
                    pass

        self._sync_confirm_state()
        self.picker.selection_changed.connect(lambda _c: self._sync_confirm_state())

    def _sync_confirm_state(self) -> None:
        self.btn_confirm.setEnabled(bool(self.picker.selected_code()))

    def _on_confirm(self) -> None:
        if self.picker.selected_code():
            self.accept()

    def selected_slot_code(self) -> Optional[str]:
        if self._ma_loai_xe == VehicleTypes.O_TO.value:
            return self.picker.selected_code()
        return None

    def selected_zone_code(self) -> Optional[str]:
        if self._ma_loai_xe == VehicleTypes.XE_MAY.value:
            return self.picker.selected_code()
        return None
