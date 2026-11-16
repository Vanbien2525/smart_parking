"""Màn hình 2.5.1–2.5.3 Tra cứu Phương tiện tại chốt bảo vệ kèm lịch sử ảnh cổng vào/ra (Thành viên 5: Ny)."""

import base64
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    SectionCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
    style_table,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.constants import VehicleTypes
from shared.protocol import Actions


class StaffLookupView(QWidget):
    """Màn hình 2.5.1–2.5.3: Tra cứu biển số, trạng thái trong/ngoài bãi và ảnh vào/ra."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._items: List[Dict[str, Any]] = []
        self._current_history: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # Header & Bộ lọc 2.5.1
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Tra cứu Phương tiện & Ảnh Cổng Vào/Ra (2.5)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_sub = QLabel("Tìm tức thì xe Cư dân & Vãng lai theo biển số, vị trí đỗ và lịch sử vào/ra")
        self.lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        header.addLayout(title_col, 1)
        root.addLayout(header)

        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(10)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Nhập biển số cần tìm (VD: 51H-888.99 hoặc 88899)...")
        self.txt_search.returnPressed.connect(self.refresh_data)
        filter_bar.addWidget(self.txt_search, 2)

        self.cbo_status = QComboBox()
        self.cbo_status.addItem("Tất cả trạng thái", "all")
        self.cbo_status.addItem("Đang đỗ trong bãi", "dang_do_trong_bai")
        self.cbo_status.addItem("Ngoài bãi", "ngoai_bai")
        self.cbo_status.currentIndexChanged.connect(self.refresh_data)
        filter_bar.addWidget(self.cbo_status, 1)

        self.cbo_guest = QComboBox()
        self.cbo_guest.addItem("Tất cả loại khách", "all")
        self.cbo_guest.addItem("Xe cư dân", "cu_dan")
        self.cbo_guest.addItem("Xe vãng lai", "vang_lai")
        self.cbo_guest.currentIndexChanged.connect(self.refresh_data)
        filter_bar.addWidget(self.cbo_guest, 1)

        self.btn_search = QPushButton("Tìm kiếm")
        set_button_variant(self.btn_search, "primary", "md")
        self.btn_search.clicked.connect(self.refresh_data)
        filter_bar.addWidget(self.btn_search)
        root.addLayout(filter_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)

        # Cột trái: Bảng danh sách phương tiện (2.5.2)
        list_card = SectionCard(
            title="2.5.2 Danh sách phương tiện & Trạng thái hiện tại",
            subtitle="Chọn một phương tiện để xem thông tin liên hệ và lịch sử vào/ra kèm ảnh",
        )
        self.tbl_vehicles = QTableWidget(0, 6)
        self.tbl_vehicles.setHorizontalHeaderLabels(
            ["Biển số", "Loại xe", "Phân loại", "Trạng thái", "Vị trí / Khu vực", "Vào gần nhất"]
        )
        style_table(self.tbl_vehicles)
        self.tbl_vehicles.itemSelectionChanged.connect(self._on_vehicle_selected)
        list_card.body_layout.addWidget(self.tbl_vehicles, 1)
        splitter.addWidget(list_card)

        # Cột phải: Chi tiết + Lịch sử lượt gửi + Ảnh cổng vào/ra (2.5.3)
        detail_card = SectionCard(
            title="2.5.3 Chi tiết chủ xe & Lịch sử vào/ra kèm ảnh chụp cổng",
            subtitle="Click vào một lượt vào/ra bên dưới để xem lại ảnh chụp cổng vào và cổng ra",
        )
        form = QFormLayout()
        form.setSpacing(6)
        self.lbl_d_plate = QLabel("-")
        self.lbl_d_plate.setStyleSheet(f"font-family: {Fonts.MONO_FAMILY}; font-size: 16px; font-weight: 800;")
        self.lbl_d_owner = QLabel("-")
        self.lbl_d_phone = QLabel("-")
        self.lbl_d_slot = QLabel("-")
        form.addRow("Biển số:", self.lbl_d_plate)
        form.addRow("Chủ xe / Phân loại:", self.lbl_d_owner)
        form.addRow("Liên hệ:", self.lbl_d_phone)
        form.addRow("Vị trí / Khu vực:", self.lbl_d_slot)
        detail_card.body_layout.addLayout(form)

        self.tbl_history = QTableWidget(0, 5)
        self.tbl_history.setHorizontalHeaderLabels(
            ["Mã lượt", "Giờ vào", "Giờ ra", "Trạng thái", "Tiền thu"]
        )
        style_table(self.tbl_history)
        self.tbl_history.itemSelectionChanged.connect(self._on_history_selected)
        detail_card.body_layout.addWidget(self.tbl_history, 1)

        img_row = QHBoxLayout()
        img_row.setSpacing(10)

        in_col = QVBoxLayout()
        lbl_in_t = QLabel("Ảnh cổng vào")
        lbl_in_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_in_t.setStyleSheet(f"font-size: 11.5px; font-weight: 700; color: {Colors.TEXT_SECONDARY};")
        self.lbl_img_in = QLabel("Chưa có ảnh vào")
        self.lbl_img_in.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_img_in.setMinimumSize(180, 120)
        self.lbl_img_in.setStyleSheet(
            f"background-color: {Colors.BG_APP}; color: {Colors.TEXT_MUTED}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 8px; font-size: 11px;"
        )
        in_col.addWidget(lbl_in_t)
        in_col.addWidget(self.lbl_img_in)
        img_row.addLayout(in_col, 1)

        out_col = QVBoxLayout()
        lbl_out_t = QLabel("Ảnh cổng ra")
        lbl_out_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_out_t.setStyleSheet(f"font-size: 11.5px; font-weight: 700; color: {Colors.TEXT_SECONDARY};")
        self.lbl_img_out = QLabel("Chưa có ảnh ra")
        self.lbl_img_out.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_img_out.setMinimumSize(180, 120)
        self.lbl_img_out.setStyleSheet(
            f"background-color: {Colors.BG_APP}; color: {Colors.TEXT_MUTED}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 8px; font-size: 11px;"
        )
        out_col.addWidget(lbl_out_t)
        out_col.addWidget(self.lbl_img_out)
        img_row.addLayout(out_col, 1)

        detail_card.body_layout.addLayout(img_row)
        splitter.addWidget(detail_card)
        splitter.setSizes([580, 480])
        root.addWidget(splitter, 1)

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            self.lbl_sub.setText("Chưa kết nối tới Server — không thể tra cứu.")
            return
        resp = self.socket_client.call(
            Actions.GATE_LOOKUP_VEHICLE,
            {
                "search_plate": self.txt_search.text().strip(),
                "trang_thai_filter": str(self.cbo_status.currentData() or "all"),
                "loai_khach_filter": str(self.cbo_guest.currentData() or "all"),
                "include_images": True,
            },
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self, str(resp.get("error") or "Lỗi tra cứu xe."), variant="danger")
            return
        self.apply_lookup_data(resp.get("data") or {})

    def apply_lookup_data(self, data: Dict[str, Any]) -> None:
        items = list(data.get("items") or [])
        self._items = items
        self.lbl_sub.setText(f"Tìm thấy {len(items)} phương tiện phù hợp")

        self.tbl_vehicles.blockSignals(True)
        self.tbl_vehicles.setRowCount(len(items))
        for i, item in enumerate(items):
            vtype = "Ô tô" if item.get("ma_loai_xe") == VehicleTypes.O_TO.value else "Xe máy"
            in_yard = bool(item.get("dang_trong_bai"))
            slot_txt = str(item.get("ten_vi_tri") or item.get("ma_vi_tri") or item.get("ten_khu_vuc") or "-")
            self.tbl_vehicles.setItem(i, 0, QTableWidgetItem(str(item.get("bien_so_xe") or "")))
            self.tbl_vehicles.setItem(i, 1, QTableWidgetItem(vtype))
            self.tbl_vehicles.setItem(i, 2, QTableWidgetItem(str(item.get("phan_loai_text") or "")))
            self.tbl_vehicles.setCellWidget(
                i,
                3,
                StatusBadge(
                    "Đang trong bãi" if in_yard else "Ngoài bãi",
                    "danger" if in_yard else "neutral",
                ),
            )
            self.tbl_vehicles.setItem(i, 4, QTableWidgetItem(slot_txt))
            self.tbl_vehicles.setItem(i, 5, QTableWidgetItem(str(item.get("thoi_gian_vao_gan_nhat") or "-")))
        self.tbl_vehicles.blockSignals(False)

        if items:
            self.tbl_vehicles.selectRow(0)
            self._show_vehicle_detail(items[0])
        else:
            self._clear_detail()

    def _clear_detail(self) -> None:
        self._current_history = []
        self.lbl_d_plate.setText("-")
        self.lbl_d_owner.setText("-")
        self.lbl_d_phone.setText("-")
        self.lbl_d_slot.setText("-")
        self.tbl_history.setRowCount(0)
        self.lbl_img_in.clear()
        self.lbl_img_in.setText("Chưa có ảnh vào")
        self.lbl_img_out.clear()
        self.lbl_img_out.setText("Chưa có ảnh ra")

    def _on_vehicle_selected(self) -> None:
        row = self.tbl_vehicles.currentRow()
        if 0 <= row < len(self._items):
            self._show_vehicle_detail(self._items[row])

    def _show_vehicle_detail(self, item: Dict[str, Any]) -> None:
        self.lbl_d_plate.setText(str(item.get("bien_so_xe") or "-"))
        owner_txt = str(item.get("ho_ten_chu_xe") or "-")
        if item.get("ma_can_ho"):
            owner_txt += f" (Căn hộ {item.get('ma_can_ho')})"
        self.lbl_d_owner.setText(owner_txt)
        self.lbl_d_phone.setText(str(item.get("so_dien_thoai") or "Không có"))
        slot_txt = f"{item.get('ten_vi_tri') or item.get('ma_vi_tri') or ''} · {item.get('ten_khu_vuc') or ''}".strip(" ·")
        self.lbl_d_slot.setText(slot_txt or "-")

        turns = list(item.get("history") or [])
        self._current_history = turns
        self.tbl_history.blockSignals(True)
        self.tbl_history.setRowCount(len(turns))
        for i, t in enumerate(turns):
            st = str(t.get("trang_thai") or "")
            self.tbl_history.setItem(i, 0, QTableWidgetItem(str(t.get("ma_luot_gui") or "-")))
            self.tbl_history.setItem(i, 1, QTableWidgetItem(str(t.get("thoi_gian_vao") or "-")))
            self.tbl_history.setItem(i, 2, QTableWidgetItem(str(t.get("thoi_gian_ra") or "Đang gửi")))
            self.tbl_history.setCellWidget(
                i,
                3,
                StatusBadge("Đang gửi" if st == "dang_gui" else "Đã ra bãi", "warning" if st == "dang_gui" else "success"),
            )
            self.tbl_history.setItem(i, 4, QTableWidgetItem(f"{int(float(t.get('tien_thu') or 0)):,} đ"))
        self.tbl_history.blockSignals(False)

        if turns:
            self.tbl_history.selectRow(0)
            self._show_turn_images(turns[0])
        else:
            self.lbl_img_in.clear()
            self.lbl_img_in.setText("Chưa có lịch sử vào")
            self.lbl_img_out.clear()
            self.lbl_img_out.setText("Chưa có lịch sử ra")

    def _on_history_selected(self) -> None:
        row = self.tbl_history.currentRow()
        if 0 <= row < len(self._current_history):
            self._show_turn_images(self._current_history[row])

    @staticmethod
    def _render_base64_to_label(label: QLabel, b64_str: str, empty_text: str) -> None:
        if not b64_str:
            label.clear()
            label.setText(empty_text)
            return
        try:
            raw = base64.b64decode(b64_str)
            pix = QPixmap()
            if pix.loadFromData(raw):
                label.setPixmap(
                    pix.scaled(
                        label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        except Exception:
            pass
        label.clear()
        label.setText(empty_text)

    def _show_turn_images(self, turn: Dict[str, Any]) -> None:
        self._render_base64_to_label(
            self.lbl_img_in,
            str(turn.get("anh_vao_base64") or ""),
            "Không có ảnh cổng vào",
        )
        self._render_base64_to_label(
            self.lbl_img_out,
            str(turn.get("anh_ra_base64") or ""),
            "Không có ảnh cổng ra",
        )
