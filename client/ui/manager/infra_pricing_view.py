"""Giao diện 3 Tab: Khu vực (1.4.5–9), Vị trí đỗ (1.4.10–14) & Bảng giá (1.5)."""

from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    ReadOnlyBanner,
    SectionCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
    style_table,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.protocol import Actions


def _fmt_vnd(val: Any) -> str:
    try:
        return f"{int(round(float(val or 0))):,} đ".replace(",", ".")
    except (TypeError, ValueError):
        return "0 đ"


class ZoneFormDialog(QDialog):
    """Hộp thoại 1.4.5 / 1.4.7: Thêm mới hoặc Cập nhật khu vực bãi xe."""

    def __init__(
        self, zone: Optional[Dict[str, Any]] = None, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._is_edit = zone is not None
        self.setWindowTitle("Sửa thông tin khu vực (1.4.7)" if self._is_edit else "Thêm khu vực mới (1.4.5)")
        self.setMinimumWidth(420)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.txt_code = QLineEdit(str((zone or {}).get("ma_khu_vuc") or ""))
        self.txt_code.setReadOnly(self._is_edit)
        self.txt_name = QLineEdit(str((zone or {}).get("ten_khu_vuc") or ""))

        self.cbo_type = QComboBox()
        self.cbo_type.addItem("Ô tô (O_TO)", "O_TO")
        self.cbo_type.addItem("Xe máy (XE_MAY)", "XE_MAY")
        if zone and zone.get("ma_loai_xe"):
            idx = self.cbo_type.findData(zone["ma_loai_xe"])
            if idx >= 0:
                self.cbo_type.setCurrentIndex(idx)
        self.cbo_type.setEnabled(not self._is_edit)

        self.spn_total = QSpinBox()
        self.spn_total.setRange(0, 5000)
        self.spn_total.setValue(int((zone or {}).get("tong_so_cho") or 20))

        form.addRow("Mã khu vực (*):", self.txt_code)
        form.addRow("Tên khu vực (*):", self.txt_name)
        form.addRow("Loại xe (*):", self.cbo_type)
        form.addRow("Tổng số chỗ:", self.spn_total)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Hủy")
        set_button_variant(btn_cancel, "default", "sm")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Lưu khu vực" if self._is_edit else "Thêm khu vực")
        set_button_variant(btn_ok, "primary", "md")
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        root.addLayout(row)

    def get_payload(self) -> Dict[str, Any]:
        return {
            "ma_khu_vuc": self.txt_code.text().strip().upper(),
            "ten_khu_vuc": self.txt_name.text().strip(),
            "ma_loai_xe": self.cbo_type.currentData(),
            "tong_so_cho": self.spn_total.value(),
        }


class SlotFormDialog(QDialog):
    """Hộp thoại 1.4.10 / 1.4.12: Thêm mới hoặc Sửa thông tin vị trí đỗ."""

    def __init__(
        self,
        zones: List[Dict[str, Any]],
        slot: Optional[Dict[str, Any]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._is_edit = slot is not None
        self.setWindowTitle("Sửa vị trí đỗ (1.4.12)" if self._is_edit else "Thêm vị trí đỗ mới (1.4.10)")
        self.setMinimumWidth(420)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.txt_code = QLineEdit(str((slot or {}).get("ma_vi_tri") or ""))
        self.txt_code.setReadOnly(self._is_edit)

        self.cbo_zone = QComboBox()
        for z in zones:
            self.cbo_zone.addItem(f"{z.get('ma_khu_vuc')} — {z.get('ten_khu_vuc', '')}", z.get("ma_khu_vuc"))
        if slot and slot.get("ma_khu_vuc"):
            idx = self.cbo_zone.findData(slot["ma_khu_vuc"])
            if idx >= 0:
                self.cbo_zone.setCurrentIndex(idx)
        self.cbo_zone.setEnabled(not self._is_edit)

        self.txt_name = QLineEdit(str((slot or {}).get("ten_vi_tri") or ""))
        self.cbo_group = QComboBox()
        self.cbo_group.addItem("Vị trí thường (vi_tri_thuong)", "vi_tri_thuong")
        self.cbo_group.addItem("Vị trí đầu bãi (vi_tri_dau_bai)", "vi_tri_dau_bai")
        self.cbo_group.addItem("Vị trí vãng lai (vi_tri_vang_lai)", "vi_tri_vang_lai")
        if slot and slot.get("nhom_vi_tri"):
            idx = self.cbo_group.findData(slot["nhom_vi_tri"])
            if idx >= 0:
                self.cbo_group.setCurrentIndex(idx)

        form.addRow("Mã vị trí (*):", self.txt_code)
        form.addRow("Khu vực (*):", self.cbo_zone)
        form.addRow("Tên vị trí (*):", self.txt_name)
        form.addRow("Nhóm vị trí:", self.cbo_group)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Hủy")
        set_button_variant(btn_cancel, "default", "sm")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Lưu vị trí" if self._is_edit else "Thêm vị trí")
        set_button_variant(btn_ok, "primary", "md")
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        root.addLayout(row)

    def get_payload(self) -> Dict[str, Any]:
        return {
            "ma_vi_tri": self.txt_code.text().strip().upper(),
            "ma_khu_vuc": self.cbo_zone.currentData() or "",
            "ten_vi_tri": self.txt_name.text().strip() or self.txt_code.text().strip().upper(),
            "nhom_vi_tri": self.cbo_group.currentData(),
        }


class PricingFormDialog(QDialog):
    """Hộp thoại 1.5.1 / 1.5.2: Tạo mới hoặc Sửa mức phí bảng giá."""

    def __init__(
        self, pricing: Optional[Dict[str, Any]] = None, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._is_edit = pricing is not None
        self.setWindowTitle("Sửa bảng giá (1.5.2)" if self._is_edit else "Tạo bảng giá mới (1.5.1)")
        self.setMinimumWidth(420)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.cbo_type = QComboBox()
        self.cbo_type.addItem("Ô tô (O_TO)", "O_TO")
        self.cbo_type.addItem("Xe máy (XE_MAY)", "XE_MAY")
        if pricing and pricing.get("ma_loai_xe"):
            idx = self.cbo_type.findData(pricing["ma_loai_xe"])
            if idx >= 0:
                self.cbo_type.setCurrentIndex(idx)
        self.cbo_type.setEnabled(not self._is_edit)

        self.cbo_group = QComboBox()
        self.cbo_group.addItem("Chỗ ô tô mặc định", "cho_oto_mac_dinh")
        self.cbo_group.addItem("Chỗ ô tô mua thêm", "cho_oto_mua_them")
        self.cbo_group.addItem("Xe máy cư dân", "xe_may_cu_dan")
        self.cbo_group.addItem("Khách vãng lai", "khach_vang_lai")
        if pricing and pricing.get("nhom_ap_dung"):
            idx = self.cbo_group.findData(pricing["nhom_ap_dung"])
            if idx >= 0:
                self.cbo_group.setCurrentIndex(idx)
        self.cbo_group.setEnabled(not self._is_edit)

        self.cbo_unit = QComboBox()
        self.cbo_unit.addItem("Tháng (thang)", "thang")
        self.cbo_unit.addItem("Giờ (gio)", "gio")
        self.cbo_unit.addItem("Lượt (luot)", "luot")
        if pricing and pricing.get("don_vi_tinh"):
            idx = self.cbo_unit.findData(pricing["don_vi_tinh"])
            if idx >= 0:
                self.cbo_unit.setCurrentIndex(idx)
        self.cbo_unit.setEnabled(not self._is_edit)

        self.spn_fee = QDoubleSpinBox()
        self.spn_fee.setRange(0, 100_000_000)
        self.spn_fee.setDecimals(0)
        self.spn_fee.setSingleStep(10_000)
        self.spn_fee.setValue(float((pricing or {}).get("muc_phi") or 100_000))

        form.addRow("Loại phương tiện:", self.cbo_type)
        form.addRow("Nhóm áp dụng:", self.cbo_group)
        form.addRow("Đơn vị tính:", self.cbo_unit)
        form.addRow("Mức phí (VNĐ):", self.spn_fee)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Hủy")
        set_button_variant(btn_cancel, "default", "sm")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Lưu mức giá" if self._is_edit else "Tạo bảng giá")
        set_button_variant(btn_ok, "primary", "md")
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        root.addLayout(row)

    def get_payload(self, ma_bang_gia: Optional[int] = None) -> Dict[str, Any]:
        if self._is_edit:
            return {"ma_bang_gia": ma_bang_gia, "muc_phi": self.spn_fee.value()}
        return {
            "ma_loai_xe": self.cbo_type.currentData(),
            "nhom_ap_dung": self.cbo_group.currentData(),
            "don_vi_tinh": self.cbo_unit.currentData(),
            "muc_phi": self.spn_fee.value(),
        }


class InfraPricingView(QWidget):
    """Màn hình 3 Tab: Khu vực (1.4.5–9), Vị trí (1.4.10–14) & Bảng giá (1.5)."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        read_only: bool = False,
        initial_tab: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.read_only = read_only
        self._zones: List[Dict[str, Any]] = []
        self._slots: List[Dict[str, Any]] = []
        self._pricings: List[Dict[str, Any]] = []
        self._build_ui()
        if 0 <= initial_tab < self.tabs.count():
            self.tabs.setCurrentIndex(initial_tab)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        if self.read_only:
            root.addWidget(ReadOnlyBanner(self))

        lbl_title = QLabel("Hạ tầng Bãi giữ xe & Cấu hình Bảng giá (1.4 & 1.5)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        root.addWidget(lbl_title)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_zones_tab(), "Khu vực đỗ xe (1.4.5–1.4.9)")
        self.tabs.addTab(self._build_slots_tab(), "Vị trí / Ô đỗ (1.4.10–1.4.14)")
        self.tabs.addTab(self._build_pricing_tab(), "Cấu hình Bảng giá (1.5.1–1.5.4)")
        root.addWidget(self.tabs, 1)

    def _mark_mutation(self, btn: QPushButton) -> QPushButton:
        btn.setProperty("is_mutation", True)
        if self.read_only:
            btn.setEnabled(False)
            btn.setVisible(False)
        return btn

    def _build_zones_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 10, 0, 0)
        card = SectionCard(
            title="Quản lý Khu vực đỗ xe",
            subtitle="Khu vực chỉ được ngừng sử dụng khi không còn xe đang gửi (so_xe_dang_gui == 0)",
        )
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.txt_zone_kw = QLineEdit()
        self.txt_zone_kw.setPlaceholderText("Tìm theo mã / tên khu vực...")
        self.btn_zone_refresh = QPushButton("Tìm / Làm mới (1.4.6)")
        set_button_variant(self.btn_zone_refresh, "default", "sm")
        self.btn_zone_refresh.clicked.connect(self.refresh_zones)

        self.btn_zone_add = self._mark_mutation(QPushButton("+ Thêm khu vực (1.4.5)"))
        set_button_variant(self.btn_zone_add, "primary", "sm")
        self.btn_zone_add.clicked.connect(self._create_zone)

        self.btn_zone_edit = self._mark_mutation(QPushButton("Sửa khu vực (1.4.7)"))
        set_button_variant(self.btn_zone_edit, "teal", "sm")
        self.btn_zone_edit.clicked.connect(self._update_zone)

        self.btn_zone_stop = self._mark_mutation(QPushButton("Ngừng sử dụng (1.4.8)"))
        set_button_variant(self.btn_zone_stop, "danger", "sm")
        self.btn_zone_stop.clicked.connect(self._deactivate_zone)

        self.btn_zone_start = self._mark_mutation(QPushButton("Kích hoạt lại (1.4.9)"))
        set_button_variant(self.btn_zone_start, "success", "sm")
        self.btn_zone_start.clicked.connect(self._activate_zone)

        bar.addWidget(self.txt_zone_kw, 2)
        bar.addWidget(self.btn_zone_refresh)
        bar.addWidget(self.btn_zone_add)
        bar.addWidget(self.btn_zone_edit)
        bar.addWidget(self.btn_zone_stop)
        bar.addWidget(self.btn_zone_start)
        card.body_layout.addLayout(bar)

        self.tbl_zones = QTableWidget(0, 7)
        self.tbl_zones.setHorizontalHeaderLabels(
            ["Mã khu", "Tên khu vực", "Loại xe", "Tổng chỗ", "Đang gửi", "Trạng thái", "Lý do ngừng"]
        )
        style_table(self.tbl_zones)
        card.body_layout.addWidget(self.tbl_zones, 1)
        lay.addWidget(card, 1)
        return host

    def _build_slots_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 10, 0, 0)
        card = SectionCard(
            title="Quản lý Vị trí / Ô đỗ xe cụ thể",
            subtitle="Vị trí đang có xe (dang_co_xe) không được phép ngừng sử dụng",
        )
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.txt_slot_kw = QLineEdit()
        self.txt_slot_kw.setPlaceholderText("Tìm theo mã / tên vị trí...")
        self.cbo_slot_zone = QComboBox()
        self.cbo_slot_zone.addItem("Tất cả khu vực", "")
        self.cbo_slot_status = QComboBox()
        self.cbo_slot_status.addItem("Tất cả trạng thái", "")
        self.cbo_slot_status.addItem("Trống", "trong")
        self.cbo_slot_status.addItem("Đã phân chỗ", "da_phan_cho")
        self.cbo_slot_status.addItem("Đang có xe", "dang_co_xe")
        self.cbo_slot_status.addItem("Ngừng sử dụng", "ngung_su_dung")

        self.btn_slot_refresh = QPushButton("Tìm / Làm mới (1.4.11)")
        set_button_variant(self.btn_slot_refresh, "default", "sm")
        self.btn_slot_refresh.clicked.connect(self.refresh_slots)

        self.btn_slot_add = self._mark_mutation(QPushButton("+ Thêm vị trí (1.4.10)"))
        set_button_variant(self.btn_slot_add, "primary", "sm")
        self.btn_slot_add.clicked.connect(self._create_slot)

        self.btn_slot_edit = self._mark_mutation(QPushButton("Sửa vị trí (1.4.12)"))
        set_button_variant(self.btn_slot_edit, "teal", "sm")
        self.btn_slot_edit.clicked.connect(self._update_slot)

        self.btn_slot_stop = self._mark_mutation(QPushButton("Ngừng vị trí (1.4.13)"))
        set_button_variant(self.btn_slot_stop, "danger", "sm")
        self.btn_slot_stop.clicked.connect(self._deactivate_slot)

        self.btn_slot_start = self._mark_mutation(QPushButton("Kích hoạt lại (1.4.14)"))
        set_button_variant(self.btn_slot_start, "success", "sm")
        self.btn_slot_start.clicked.connect(self._activate_slot)

        bar.addWidget(self.txt_slot_kw, 2)
        bar.addWidget(self.cbo_slot_zone, 1)
        bar.addWidget(self.cbo_slot_status, 1)
        bar.addWidget(self.btn_slot_refresh)
        bar.addWidget(self.btn_slot_add)
        bar.addWidget(self.btn_slot_edit)
        bar.addWidget(self.btn_slot_stop)
        bar.addWidget(self.btn_slot_start)
        card.body_layout.addLayout(bar)

        self.tbl_slots = QTableWidget(0, 7)
        self.tbl_slots.setHorizontalHeaderLabels(
            ["Mã ô", "Khu vực", "Tên vị trí", "Nhóm", "Căn hộ sở hữu", "Trạng thái", "Lý do ngừng"]
        )
        style_table(self.tbl_slots)
        card.body_layout.addWidget(self.tbl_slots, 1)
        lay.addWidget(card, 1)
        return host

    def _build_pricing_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 10, 0, 0)
        card = SectionCard(
            title="Cấu hình Bảng giá dịch vụ gửi xe (1.5)",
            subtitle="Thay đổi bảng giá chỉ áp dụng cho hóa đơn/giao dịch mới, không làm sai lệch hóa đơn cũ",
        )
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.cbo_price_group = QComboBox()
        self.cbo_price_group.addItem("Tất cả nhóm áp dụng", "")
        self.cbo_price_group.addItem("Chỗ ô tô mặc định", "cho_oto_mac_dinh")
        self.cbo_price_group.addItem("Chỗ ô tô mua thêm", "cho_oto_mua_them")
        self.cbo_price_group.addItem("Xe máy cư dân", "xe_may_cu_dan")
        self.cbo_price_group.addItem("Khách vãng lai", "khach_vang_lai")

        self.cbo_price_status = QComboBox()
        self.cbo_price_status.addItem("Tất cả trạng thái", "")
        self.cbo_price_status.addItem("Đang áp dụng", "dang_ap_dung")
        self.cbo_price_status.addItem("Ngừng áp dụng", "ngung_ap_dung")

        self.btn_price_refresh = QPushButton("Xem bảng giá (1.5.4)")
        set_button_variant(self.btn_price_refresh, "default", "sm")
        self.btn_price_refresh.clicked.connect(self.refresh_pricing)

        self.btn_price_add = self._mark_mutation(QPushButton("+ Tạo bảng giá (1.5.1)"))
        set_button_variant(self.btn_price_add, "primary", "sm")
        self.btn_price_add.clicked.connect(self._create_pricing)

        self.btn_price_edit = self._mark_mutation(QPushButton("Sửa bảng giá (1.5.2)"))
        set_button_variant(self.btn_price_edit, "teal", "sm")
        self.btn_price_edit.clicked.connect(self._update_pricing)

        self.btn_price_stop = self._mark_mutation(QPushButton("Ngừng áp dụng (1.5.3)"))
        set_button_variant(self.btn_price_stop, "danger", "sm")
        self.btn_price_stop.clicked.connect(self._deactivate_pricing)

        bar.addWidget(self.cbo_price_group, 1)
        bar.addWidget(self.cbo_price_status, 1)
        bar.addStretch(1)
        bar.addWidget(self.btn_price_refresh)
        bar.addWidget(self.btn_price_add)
        bar.addWidget(self.btn_price_edit)
        bar.addWidget(self.btn_price_stop)
        card.body_layout.addLayout(bar)

        self.tbl_pricing = QTableWidget(0, 7)
        self.tbl_pricing.setHorizontalHeaderLabels(
            ["Mã giá", "Loại xe", "Nhóm áp dụng", "Đơn vị", "Mức phí", "Bắt đầu áp dụng", "Trạng thái"]
        )
        style_table(self.tbl_pricing)
        card.body_layout.addWidget(self.tbl_pricing, 1)
        lay.addWidget(card, 1)
        return host

    def refresh_data(self) -> None:
        self.refresh_zones()
        self.refresh_slots()
        self.refresh_pricing()

    def refresh_zones(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.INFRA_LIST_ZONES, {"keyword": self.txt_zone_kw.text().strip()}
        )
        if resp.get("ok") and resp.get("data"):
            self.set_zones(resp["data"].get("items") or [])

    def set_zones(self, items: List[Dict[str, Any]]) -> None:
        self._zones = items
        current_zone = self.cbo_slot_zone.currentData()
        self.cbo_slot_zone.blockSignals(True)
        self.cbo_slot_zone.clear()
        self.cbo_slot_zone.addItem("Tất cả khu vực", "")
        for z in items:
            self.cbo_slot_zone.addItem(f"{z.get('ma_khu_vuc')} — {z.get('ten_khu_vuc', '')}", z.get("ma_khu_vuc"))
        if current_zone:
            idx = self.cbo_slot_zone.findData(current_zone)
            if idx >= 0:
                self.cbo_slot_zone.setCurrentIndex(idx)
        self.cbo_slot_zone.blockSignals(False)

        self.tbl_zones.setRowCount(len(items))
        for idx, z in enumerate(items):
            status = str(z.get("trang_thai_khu_vuc") or "hoat_dong")
            self.tbl_zones.setItem(idx, 0, QTableWidgetItem(str(z.get("ma_khu_vuc") or "")))
            self.tbl_zones.setItem(idx, 1, QTableWidgetItem(str(z.get("ten_khu_vuc") or "")))
            self.tbl_zones.setItem(idx, 2, QTableWidgetItem(str(z.get("ma_loai_xe") or "")))
            self.tbl_zones.setItem(idx, 3, QTableWidgetItem(str(z.get("tong_so_cho") or 0)))
            self.tbl_zones.setItem(idx, 4, QTableWidgetItem(str(z.get("so_xe_dang_gui") or 0)))
            self.tbl_zones.setCellWidget(
                idx, 5, StatusBadge(status, "success" if status == "hoat_dong" else "neutral")
            )
            self.tbl_zones.setItem(idx, 6, QTableWidgetItem(str(z.get("ly_do_ngung") or "-")))

    def refresh_slots(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.INFRA_LIST_SLOTS,
            {
                "keyword": self.txt_slot_kw.text().strip(),
                "ma_khu_vuc": self.cbo_slot_zone.currentData(),
                "trang_thai_vi_tri": self.cbo_slot_status.currentData(),
            },
        )
        if resp.get("ok") and resp.get("data"):
            self.set_slots(resp["data"].get("items") or [])

    def set_slots(self, items: List[Dict[str, Any]]) -> None:
        self._slots = items
        self.tbl_slots.setRowCount(len(items))
        badge_map = {
            "trong": "success",
            "da_phan_cho": "primary",
            "dang_co_xe": "danger",
            "ngung_su_dung": "neutral",
        }
        for idx, s in enumerate(items):
            status = str(s.get("trang_thai_vi_tri") or "trong")
            self.tbl_slots.setItem(idx, 0, QTableWidgetItem(str(s.get("ma_vi_tri") or "")))
            self.tbl_slots.setItem(idx, 1, QTableWidgetItem(str(s.get("ma_khu_vuc") or "")))
            self.tbl_slots.setItem(idx, 2, QTableWidgetItem(str(s.get("ten_vi_tri") or "")))
            self.tbl_slots.setItem(idx, 3, QTableWidgetItem(str(s.get("nhom_vi_tri") or "")))
            self.tbl_slots.setItem(idx, 4, QTableWidgetItem(str(s.get("can_ho_so_huu") or "-")))
            self.tbl_slots.setCellWidget(
                idx, 5, StatusBadge(status, badge_map.get(status, "neutral"))
            )
            self.tbl_slots.setItem(idx, 6, QTableWidgetItem(str(s.get("ly_do_ngung") or "-")))

    def refresh_pricing(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.PRICING_LIST,
            {
                "nhom_ap_dung": self.cbo_price_group.currentData(),
                "trang_thai_gia": self.cbo_price_status.currentData(),
            },
        )
        if resp.get("ok") and resp.get("data"):
            self.set_pricing_list(resp["data"].get("items") or [])

    def set_pricing_list(self, items: List[Dict[str, Any]]) -> None:
        self._pricings = items
        self.tbl_pricing.setRowCount(len(items))
        for idx, p in enumerate(items):
            status = str(p.get("trang_thai_gia") or "dang_ap_dung")
            self.tbl_pricing.setItem(idx, 0, QTableWidgetItem(str(p.get("ma_bang_gia") or "")))
            self.tbl_pricing.setItem(idx, 1, QTableWidgetItem(str(p.get("ma_loai_xe") or "")))
            self.tbl_pricing.setItem(idx, 2, QTableWidgetItem(str(p.get("nhom_ap_dung") or "")))
            self.tbl_pricing.setItem(idx, 3, QTableWidgetItem(str(p.get("don_vi_tinh") or "")))
            self.tbl_pricing.setItem(idx, 4, QTableWidgetItem(_fmt_vnd(p.get("muc_phi"))))
            self.tbl_pricing.setItem(idx, 5, QTableWidgetItem(str(p.get("thoi_gian_bat_dau") or "")))
            self.tbl_pricing.setCellWidget(
                idx, 6, StatusBadge(status, "success" if status == "dang_ap_dung" else "neutral")
            )

    def _call_action(self, action: str, payload: Dict[str, Any], ok_msg: str) -> bool:
        if self.socket_client is None or not self.socket_client.is_connected():
            return False
        resp = self.socket_client.call(action, payload)
        if not resp.get("ok"):
            ToastNotification.show_toast(
                self.window(),
                str(resp.get("error") or "Thao tác thất bại."),
                variant="danger",
            )
            return False
        ToastNotification.show_toast(self.window(), ok_msg, variant="success")
        return True

    def _create_zone(self) -> None:
        dlg = ZoneFormDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._call_action(Actions.INFRA_CREATE_ZONE, dlg.get_payload(), "Đã thêm khu vực mới."):
                self.refresh_zones()

    def _update_zone(self) -> None:
        row = self.tbl_zones.currentRow()
        if row < 0 or row >= len(self._zones):
            return
        dlg = ZoneFormDialog(zone=self._zones[row], parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._call_action(Actions.INFRA_UPDATE_ZONE, dlg.get_payload(), "Đã cập nhật khu vực."):
                self.refresh_zones()

    def _deactivate_zone(self) -> None:
        row = self.tbl_zones.currentRow()
        if row < 0 or row >= len(self._zones):
            return
        z = self._zones[row]
        reason, ok = QInputDialog.getText(self, "Ngừng khu vực", "Nhập lý do ngừng sử dụng khu vực:")
        if ok and reason.strip():
            if self._call_action(
                Actions.INFRA_DEACTIVATE_ZONE,
                {"ma_khu_vuc": z["ma_khu_vuc"], "ly_do_ngung": reason.strip()},
                "Đã ngừng sử dụng khu vực.",
            ):
                self.refresh_zones()

    def _activate_zone(self) -> None:
        row = self.tbl_zones.currentRow()
        if row < 0 or row >= len(self._zones):
            return
        if self._call_action(
            Actions.INFRA_ACTIVATE_ZONE,
            {"ma_khu_vuc": self._zones[row]["ma_khu_vuc"]},
            "Đã kích hoạt lại khu vực.",
        ):
            self.refresh_zones()

    def _create_slot(self) -> None:
        dlg = SlotFormDialog(zones=self._zones, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._call_action(Actions.INFRA_CREATE_SLOT, dlg.get_payload(), "Đã thêm vị trí đỗ mới."):
                self.refresh_zones()
                self.refresh_slots()

    def _update_slot(self) -> None:
        row = self.tbl_slots.currentRow()
        if row < 0 or row >= len(self._slots):
            return
        dlg = SlotFormDialog(zones=self._zones, slot=self._slots[row], parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._call_action(Actions.INFRA_UPDATE_SLOT, dlg.get_payload(), "Đã cập nhật vị trí đỗ."):
                self.refresh_slots()

    def _deactivate_slot(self) -> None:
        row = self.tbl_slots.currentRow()
        if row < 0 or row >= len(self._slots):
            return
        reason, ok = QInputDialog.getText(self, "Ngừng vị trí", "Nhập lý do ngừng sử dụng ô đỗ:")
        if ok and reason.strip():
            if self._call_action(
                Actions.INFRA_DEACTIVATE_SLOT,
                {"ma_vi_tri": self._slots[row]["ma_vi_tri"], "ly_do_ngung": reason.strip()},
                "Đã ngừng sử dụng vị trí.",
            ):
                self.refresh_slots()

    def _activate_slot(self) -> None:
        row = self.tbl_slots.currentRow()
        if row < 0 or row >= len(self._slots):
            return
        if self._call_action(
            Actions.INFRA_ACTIVATE_SLOT,
            {"ma_vi_tri": self._slots[row]["ma_vi_tri"]},
            "Đã kích hoạt lại vị trí.",
        ):
            self.refresh_slots()

    def _create_pricing(self) -> None:
        dlg = PricingFormDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._call_action(Actions.PRICING_CREATE, dlg.get_payload(), "Đã tạo bảng giá mới."):
                self.refresh_pricing()

    def _update_pricing(self) -> None:
        row = self.tbl_pricing.currentRow()
        if row < 0 or row >= len(self._pricings):
            return
        p = self._pricings[row]
        dlg = PricingFormDialog(pricing=p, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._call_action(
                Actions.PRICING_UPDATE,
                dlg.get_payload(ma_bang_gia=p["ma_bang_gia"]),
                "Đã cập nhật mức phí.",
            ):
                self.refresh_pricing()

    def _deactivate_pricing(self) -> None:
        row = self.tbl_pricing.currentRow()
        if row < 0 or row >= len(self._pricings):
            return
        if self._call_action(
            Actions.PRICING_DEACTIVATE,
            {"ma_bang_gia": self._pricings[row]["ma_bang_gia"]},
            "Đã ngừng áp dụng bảng giá.",
        ):
            self.refresh_pricing()
