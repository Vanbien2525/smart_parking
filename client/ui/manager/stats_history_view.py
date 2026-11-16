"""Cụm giao diện Quản lý của Thành viên 3 (Minh):
- ManagerMonitorView (1.6): Giám sát bãi xe 2D Grid & 3D Sa bàn tương tác
- ManagerStatsView (1.8): Thống kê lượt xe, cư dân vs vãng lai & doanh thu (Matplotlib)
- ManagerHistoryView (1.9): Tra cứu lịch sử xe vào/ra & ảnh đối chiếu
- ManagerNotifyView (1.10): Soạn nháp, phát thông báo Push khẩn cấp & theo dõi/Retry
"""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    ParkingGridWidget,
    PlateBadge,
    ReadOnlyBanner,
    SectionCard,
    StatCard,
    StatusBadge,
    set_button_variant,
    style_table,
)
from client.ui.theme.parking_3d_widget import Parking3DSceneWidget
from client.ui.theme.tokens import Colors, Fonts, SLOT_STATE_STYLES
from shared.constants import SlotStatus
from shared.protocol import Actions


class SlotDetailDialog(QDialog):
    """Hộp thoại xem nhanh thông tin ô đỗ & xe đang đậu khi click trên sơ đồ 2D/3D."""

    def __init__(self, slot_data: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        code = str(slot_data.get("ma_vi_tri") or "---")
        self.setWindowTitle(f"Chi tiết Ô đỗ {code}")
        self.setMinimumWidth(400)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        status = str(slot_data.get("trang_thai_vi_tri") or "trong")
        st_info = SLOT_STATE_STYLES.get(status, SLOT_STATE_STYLES["trong"])
        badge_variant = {
            "trong": "success",
            "da_phan_cho": "primary",
            "dang_co_xe": "danger",
            "ngung_su_dung": "neutral",
        }.get(status, "neutral")

        card = SectionCard(
            title=f"Vị trí đỗ: {code}",
            subtitle=f"Khu vực: {slot_data.get('ten_khu_vuc') or slot_data.get('ma_khu_vuc') or '-'}",
        )
        card.add_header_widget(StatusBadge(st_info["label"], badge_variant))

        plate = str(slot_data.get("bien_so_xe") or "").strip()
        if plate:
            card.body_layout.addWidget(PlateBadge(plate), 0, Qt.AlignmentFlag.AlignLeft)

        form = QFormLayout()
        form.setSpacing(8)
        for label, val in (
            ("Mã vị trí:", code),
            ("Nhóm vị trí:", slot_data.get("nhom_vi_tri") or "-"),
            ("Loại khách:", slot_data.get("loai_khach") or "-"),
            ("Chủ xe:", slot_data.get("ten_chu_xe") or "-"),
            ("Căn hộ:", slot_data.get("ma_can_ho") or "-"),
            ("Giờ vào bãi:", slot_data.get("thoi_gian_vao") or "-"),
            ("Lý do ngừng:", slot_data.get("ly_do_ngung") or "-"),
        ):
            form.addRow(label, QLabel(str(val)))
        card.body_layout.addLayout(form)
        root.addWidget(card)

        btn_close = QPushButton("Đóng")
        set_button_variant(btn_close, "default", "sm")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)


class ManagerMonitorView(QWidget):
    """Màn hình 1.6 (Quản lý) & 4.8 (Admin Read-Only): Giám sát bãi xe 2D Grid / 3D Sa bàn."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        read_only: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.read_only = read_only
        self._overview_data: Dict[str, Any] = {}
        self._active_items: List[Dict[str, Any]] = []
        self._available_items: List[Dict[str, Any]] = []
        self._occupied_items: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        if self.read_only:
            root.addWidget(ReadOnlyBanner(self))

        # Header
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Giám sát Bãi xe Thời gian thực 2D / 3D (1.6)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_sub = QLabel("Theo dõi xe đang gửi, chỗ trống, chỗ đang dùng và tương tác sa bàn 3D")
        self.lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        header.addLayout(title_col, 1)

        self.btn_mode_2d = QPushButton("2D Lưới Ô Đỗ")
        self.btn_mode_3d = QPushButton("3D Sa Bàn Tương Tác")
        set_button_variant(self.btn_mode_2d, "primary", "sm")
        set_button_variant(self.btn_mode_3d, "default", "sm")
        self.btn_mode_2d.clicked.connect(lambda: self._switch_view_mode(0))
        self.btn_mode_3d.clicked.connect(lambda: self._switch_view_mode(1))
        header.addWidget(self.btn_mode_2d)
        header.addWidget(self.btn_mode_3d)

        self.btn_refresh = QPushButton("Làm mới")
        set_button_variant(self.btn_refresh, "teal", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        # 4 KPI Cards
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self.card_total_empty = StatCard("Tổng sức chứa / Chỗ trống", "0 / 0", "Toàn bãi xe", Colors.ROYAL_BLUE)
        self.card_active = StatCard("Xe đang gửi (1.6.1)", "0", "0 cư dân · 0 vãng lai", Colors.EMERALD_TEAL)
        self.card_occupied = StatCard("Ô đang có xe / Phân chỗ (1.6.3)", "0 / 0", "Ô đỗ cố định & vãng lai", Colors.ERROR)
        self.card_disabled = StatCard("Ô ngừng sử dụng", "0", "Đang bảo trì", Colors.WARNING)
        for card in (self.card_total_empty, self.card_active, self.card_occupied, self.card_disabled):
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        # Thanh sức chứa khu vực
        self.zone_card = SectionCard(
            title="Tình trạng sức chứa theo khu vực (1.6.4)",
            subtitle="Thanh tiến độ hiển thị số xe đang gửi trên tổng sức chứa mỗi khu vực",
        )
        self.zone_bars_layout = QHBoxLayout()
        self.zone_bars_layout.setSpacing(12)
        self.zone_card.body_layout.addLayout(self.zone_bars_layout)
        root.addWidget(self.zone_card)

        # Bộ lọc
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        self.cbo_zone = QComboBox()
        self.cbo_zone.addItem("Tất cả khu vực", "")
        self.cbo_zone.currentIndexChanged.connect(self._on_filter_changed)

        self.cbo_list_mode = QComboBox()
        self.cbo_list_mode.addItem("1.6.1 Xe đang gửi trong bãi", "active")
        self.cbo_list_mode.addItem("1.6.2 Vị trí chỗ trống", "available")
        self.cbo_list_mode.addItem("1.6.3 Vị trí đang có xe", "occupied")
        self.cbo_list_mode.currentIndexChanged.connect(self._render_table)

        self.txt_plate = QLineEdit()
        self.txt_plate.setPlaceholderText("Tìm theo biển số xe hoặc mã vị trí...")
        self.txt_plate.textChanged.connect(self._render_table)

        filter_row.addWidget(QLabel("Khu vực:"))
        filter_row.addWidget(self.cbo_zone)
        filter_row.addWidget(QLabel("Chế độ bảng:"))
        filter_row.addWidget(self.cbo_list_mode)
        filter_row.addWidget(self.txt_plate, 1)
        root.addLayout(filter_row)

        # Splitter: Sơ đồ 2D/3D bên trên/trái + Bảng chi tiết bên dưới/phải
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(10)

        map_card = SectionCard(
            title="Sơ đồ Trạng thái Ô đỗ",
            subtitle="Nhấp vào ô đỗ để xem biển số, chủ xe và thời gian vào bãi",
        )
        self.map_stack = QStackedWidget()

        scroll_2d = QScrollArea()
        scroll_2d.setWidgetResizable(True)
        scroll_2d.setFrameShape(QScrollArea.Shape.NoFrame)
        self.grid_2d = ParkingGridWidget(columns=5)
        self.grid_2d.slot_selected.connect(self._on_slot_clicked)
        scroll_2d.setWidget(self.grid_2d)

        self.scene_3d = Parking3DSceneWidget(columns=4)
        self.scene_3d.slot_clicked.connect(self._on_slot_clicked)

        self.map_stack.addWidget(scroll_2d)
        self.map_stack.addWidget(self.scene_3d)
        map_card.body_layout.addWidget(self.map_stack, 1)
        splitter.addWidget(map_card)

        list_card = SectionCard(
            title="Danh sách chi tiết (1.6.1 – 1.6.3)",
            subtitle="Tự động cập nhật khi có sự kiện xe vào/ra",
        )
        self.tbl_detail = QTableWidget(0, 6)
        self.tbl_detail.setHorizontalHeaderLabels(
            ["Mã / Lượt", "Khu vực", "Biển số / Tên ô", "Loại / Nhóm", "Chủ xe / Căn hộ", "Thời gian / Trạng thái"]
        )
        style_table(self.tbl_detail)
        list_card.body_layout.addWidget(self.tbl_detail, 1)
        splitter.addWidget(list_card)

        splitter.setSizes([580, 480])
        root.addWidget(splitter, 1)

    def _switch_view_mode(self, idx: int) -> None:
        self.map_stack.setCurrentIndex(idx)
        set_button_variant(self.btn_mode_2d, "primary" if idx == 0 else "default", "sm")
        set_button_variant(self.btn_mode_3d, "primary" if idx == 1 else "default", "sm")

    def _on_filter_changed(self) -> None:
        self.refresh_data()

    def _on_slot_clicked(self, slot_data: Dict[str, Any]) -> None:
        dlg = SlotDetailDialog(slot_data, parent=self)
        dlg.exec()

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            self.lbl_sub.setText("Chưa kết nối Server — không thể tải dữ liệu giám sát.")
            return

        ma_khu_vuc = str(self.cbo_zone.currentData() or "")
        req_payload = {"ma_khu_vuc": ma_khu_vuc} if ma_khu_vuc else {}

        ov_resp = self.socket_client.call(Actions.MONITOR_OVERVIEW_MAP, req_payload)
        if ov_resp.get("ok"):
            self.apply_overview_data(ov_resp.get("data") or {})

        act_resp = self.socket_client.call(Actions.MONITOR_ACTIVE_PARKINGS, req_payload)
        if act_resp.get("ok"):
            self._active_items = (act_resp.get("data") or {}).get("items") or []

        av_resp = self.socket_client.call(
            Actions.MONITOR_AVAILABLE_SLOTS, {**req_payload, "include_allocated": True}
        )
        if av_resp.get("ok"):
            self._available_items = (av_resp.get("data") or {}).get("items") or []

        occ_resp = self.socket_client.call(Actions.MONITOR_OCCUPIED_SLOTS, req_payload)
        if occ_resp.get("ok"):
            self._occupied_items = (occ_resp.get("data") or {}).get("items") or []

        self._render_table()

    def apply_overview_data(self, data: Dict[str, Any]) -> None:
        """Đổ dữ liệu tổng quan từ `monitor.overview_map` vào KPI, thanh khu vực, 2D và 3D."""
        self._overview_data = data
        summary = data.get("summary") or {}
        zones = data.get("zones") or []
        slots = data.get("slots") or []
        if not self._active_items and data.get("active_parkings"):
            self._active_items = list(data.get("active_parkings") or [])

        self.lbl_sub.setText(f"Cập nhật lúc: {data.get('updated_at') or '-'}")
        self.card_total_empty.set_value(
            f"{summary.get('tong_suc_chua', 0)} / {summary.get('cho_con_trong', 0)}",
            f"Trống: {summary.get(SlotStatus.TRONG.value, 0)} ô",
        )
        self.card_active.set_value(
            str(summary.get("tong_xe_dang_gui", 0)),
            f"{summary.get('xe_cu_dan_dang_gui', 0)} cư dân · {summary.get('xe_vang_lai_dang_gui', 0)} vãng lai",
        )
        self.card_occupied.set_value(
            f"{summary.get(SlotStatus.DANG_CO_XE.value, 0)} / {summary.get(SlotStatus.DA_PHAN_CHO.value, 0)}",
            "Đang có xe / Đã phân chỗ",
        )
        self.card_disabled.set_value(
            str(summary.get(SlotStatus.NGUNG_SU_DUNG.value, 0)),
            "Ô đang bảo trì",
        )

        # Đồng bộ combobox khu vực (giữ nguyên lựa chọn hiện tại)
        current_zone = self.cbo_zone.currentData()
        self.cbo_zone.blockSignals(True)
        self.cbo_zone.clear()
        self.cbo_zone.addItem("Tất cả khu vực", "")
        for z in zones:
            code = str(z.get("ma_khu_vuc") or "")
            name = str(z.get("ten_khu_vuc") or code)
            self.cbo_zone.addItem(f"{code} — {name}", code)
        idx = self.cbo_zone.findData(current_zone)
        self.cbo_zone.setCurrentIndex(idx if idx >= 0 else 0)
        self.cbo_zone.blockSignals(False)

        self._populate_zone_bars(zones)
        self.grid_2d.set_slots(slots)
        self.scene_3d.set_slots(slots)
        self._render_table()

    def _populate_zone_bars(self, zones: List[Dict[str, Any]]) -> None:
        while self.zone_bars_layout.count():
            item = self.zone_bars_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not zones:
            self.zone_bars_layout.addWidget(QLabel("Chưa có dữ liệu khu vực."))
            return

        for z in zones:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)
            total = max(1, int(z.get("tong_so_cho") or 0))
            used = int(z.get("so_xe_dang_gui") or 0)
            pct = min(100, int((used / total) * 100))

            lbl = QLabel(f"{z.get('ten_khu_vuc', z.get('ma_khu_vuc'))}: {used}/{total} ({pct}%)")
            lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            fill = Colors.ERROR if pct >= 90 else (Colors.WARNING if pct >= 70 else Colors.ROYAL_BLUE)
            bar.setStyleSheet(
                f"QProgressBar {{ background: {Colors.BG_ELEVATED}; border-radius: 5px; border: none; }}"
                f"QProgressBar::chunk {{ background: {fill}; border-radius: 5px; }}"
            )
            v.addWidget(lbl)
            v.addWidget(bar)
            self.zone_bars_layout.addWidget(box, 1)

    def _render_table(self) -> None:
        mode = str(self.cbo_list_mode.currentData() or "active")
        kw = self.txt_plate.text().strip().upper()

        if mode == "available":
            rows = [
                r
                for r in self._available_items
                if not kw
                or kw in str(r.get("ma_vi_tri") or "").upper()
                or kw in str(r.get("ten_vi_tri") or "").upper()
            ]
            self.tbl_detail.setRowCount(len(rows))
            for i, r in enumerate(rows):
                st = str(r.get("trang_thai_vi_tri") or "trong")
                self.tbl_detail.setItem(i, 0, QTableWidgetItem(str(r.get("ma_vi_tri") or "")))
                self.tbl_detail.setItem(i, 1, QTableWidgetItem(str(r.get("ma_khu_vuc") or "")))
                self.tbl_detail.setItem(i, 2, QTableWidgetItem(str(r.get("ten_vi_tri") or "")))
                self.tbl_detail.setItem(i, 3, QTableWidgetItem(str(r.get("nhom_vi_tri") or "")))
                self.tbl_detail.setItem(i, 4, QTableWidgetItem(str(r.get("ma_loai_xe") or "")))
                self.tbl_detail.setCellWidget(
                    i, 5, StatusBadge("Trống" if st == "trong" else "Đã phân chỗ", "success" if st == "trong" else "primary")
                )
        elif mode == "occupied":
            rows = [
                r
                for r in self._occupied_items
                if not kw
                or kw in str(r.get("bien_so_xe") or "").upper()
                or kw in str(r.get("ma_vi_tri") or "").upper()
            ]
            self.tbl_detail.setRowCount(len(rows))
            for i, r in enumerate(rows):
                self.tbl_detail.setItem(i, 0, QTableWidgetItem(str(r.get("ma_vi_tri") or "")))
                self.tbl_detail.setItem(i, 1, QTableWidgetItem(str(r.get("ma_khu_vuc") or "")))
                self.tbl_detail.setItem(i, 2, QTableWidgetItem(str(r.get("bien_so_xe") or "-")))
                self.tbl_detail.setItem(i, 3, QTableWidgetItem(str(r.get("loai_khach") or "-")))
                self.tbl_detail.setItem(i, 4, QTableWidgetItem(f"{r.get('ten_chu_xe') or '-'} ({r.get('ma_can_ho') or '-'})"))
                self.tbl_detail.setItem(i, 5, QTableWidgetItem(str(r.get("thoi_gian_vao") or "-")))
        else:
            rows = [
                r
                for r in self._active_items
                if not kw
                or kw in str(r.get("bien_so_xe") or "").upper()
                or kw in str(r.get("ma_vi_tri") or "").upper()
            ]
            self.tbl_detail.setRowCount(len(rows))
            for i, r in enumerate(rows):
                guest = "Cư dân" if r.get("loai_khach") == "cu_dan" else "Vãng lai"
                self.tbl_detail.setItem(i, 0, QTableWidgetItem(str(r.get("ma_vi_tri") or r.get("ma_luot_gui") or "-")))
                self.tbl_detail.setItem(i, 1, QTableWidgetItem(str(r.get("ma_khu_vuc") or "")))
                self.tbl_detail.setItem(i, 2, QTableWidgetItem(str(r.get("bien_so_xe") or "")))
                self.tbl_detail.setItem(i, 3, QTableWidgetItem(f"{guest} · {r.get('ma_loai_xe') or ''}"))
                self.tbl_detail.setItem(i, 4, QTableWidgetItem(f"{r.get('ten_chu_xe') or '-'} ({r.get('ma_can_ho') or '-'})"))
                self.tbl_detail.setItem(i, 5, QTableWidgetItem(str(r.get("thoi_gian_vao") or "")))
