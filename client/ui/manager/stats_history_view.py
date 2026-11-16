"""Cụm giao diện Quản lý của Thành viên 3 (Minh):
- ManagerMonitorView (1.6): Giám sát bãi xe 2D Grid & 3D Sa bàn tương tác
- ManagerStatsView (1.8): Thống kê lượt xe, cư dân vs vãng lai & doanh thu (Matplotlib)
- ManagerHistoryView (1.9): Tra cứu lịch sử xe vào/ra & ảnh đối chiếu
- ManagerNotifyView (1.10): Soạn nháp, phát thông báo Push khẩn cấp & theo dõi/Retry
"""

import base64
from typing import Any, Dict, List, Optional

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
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
    ToastNotification,
    set_button_variant,
    style_table,
)
from client.ui.theme.parking_3d_widget import Parking3DSceneWidget
from client.ui.theme.tokens import Colors, Fonts, SLOT_STATE_STYLES
from shared.constants import (
    NotifyLevels,
    NotifyRecipientGroups,
    NotifyTypes,
    SlotStatus,
)
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


def _fmt_vnd(val: Any) -> str:
    try:
        return f"{int(round(float(val or 0))):,} đ".replace(",", ".")
    except (TypeError, ValueError):
        return "0 đ"


class ManagerStatsView(QWidget):
    """Màn hình 1.8 (Quản lý) & 4.7 (Admin Read-Only): Thống kê lượt xe, cư dân vs vãng lai & doanh thu."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        read_only: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.read_only = read_only
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        if self.read_only:
            root.addWidget(ReadOnlyBanner(self))

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Thống kê Lượt xe & Doanh thu Bãi xe (1.8)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_sub = QLabel("Biểu đồ tổng hợp lượt vào/ra, cư dân vs vãng lai và cơ cấu doanh thu")
        self.lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        header.addLayout(title_col, 1)

        self.btn_refresh = QPushButton("Thống kê dữ liệu")
        set_button_variant(self.btn_refresh, "primary", "md")
        self.btn_refresh.clicked.connect(self.refresh_data)
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        # Bộ lọc khoảng ngày / khu vực / loại xe / nhóm theo kỳ
        filter_card = SectionCard(title="Bộ lọc kỳ thống kê")
        f_row = QHBoxLayout()
        f_row.setSpacing(10)

        today = QDate.currentDate()
        self.dt_start = QDateEdit(today.addDays(-30))
        self.dt_start.setCalendarPopup(True)
        self.dt_start.setDisplayFormat("yyyy-MM-dd")

        self.dt_end = QDateEdit(today)
        self.dt_end.setCalendarPopup(True)
        self.dt_end.setDisplayFormat("yyyy-MM-dd")

        self.cbo_zone = QComboBox()
        self.cbo_zone.addItem("Tất cả khu vực", "")
        self.cbo_zone.addItem("KHU_A (Xe máy)", "KHU_A")
        self.cbo_zone.addItem("HAM_B1 (Ô tô Cư dân)", "HAM_B1")
        self.cbo_zone.addItem("KHU_C (Ô tô Vãng lai)", "KHU_C")

        self.cbo_vehicle_type = QComboBox()
        self.cbo_vehicle_type.addItem("Tất cả loại xe", "")
        self.cbo_vehicle_type.addItem("Ô tô (O_TO)", "O_TO")
        self.cbo_vehicle_type.addItem("Xe máy (XE_MAY)", "XE_MAY")

        self.cbo_group = QComboBox()
        self.cbo_group.addItem("Theo ngày", "day")
        self.cbo_group.addItem("Theo tháng", "month")

        f_row.addWidget(QLabel("Từ ngày:"))
        f_row.addWidget(self.dt_start)
        f_row.addWidget(QLabel("Đến ngày:"))
        f_row.addWidget(self.dt_end)
        f_row.addWidget(QLabel("Khu vực:"))
        f_row.addWidget(self.cbo_zone)
        f_row.addWidget(QLabel("Loại xe:"))
        f_row.addWidget(self.cbo_vehicle_type)
        f_row.addWidget(QLabel("Doanh thu:"))
        f_row.addWidget(self.cbo_group)
        f_row.addStretch(1)
        filter_card.body_layout.addLayout(f_row)
        root.addWidget(filter_card)

        # 4 KPI Cards
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self.card_turns = StatCard("1.8.1 Tổng lượt xe (Vào / Ra)", "0 / 0", "Tổng lượt ghi nhận", Colors.ROYAL_BLUE)
        self.card_ratio = StatCard("1.8.2 Cư dân / Vãng lai", "0 / 0", "So sánh lượt gửi", Colors.INDIGO)
        self.card_rev_res = StatCard("1.8.3 Phí tháng Cư dân", "0 đ", "Giao dịch QR & Tiền mặt", Colors.EMERALD_TEAL)
        self.card_rev_total = StatCard("1.8.3 Tổng doanh thu", "0 đ", "Vãng lai: 0 đ", Colors.SUCCESS)
        for card in (self.card_turns, self.card_ratio, self.card_rev_res, self.card_rev_total):
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        # 3 Tab biểu đồ Matplotlib + Bảng đối chiếu
        self.tabs = QTabWidget()

        # Tab 1: 1.8.1 Lượt xe vào/ra
        tab1 = QWidget()
        t1_lay = QVBoxLayout(tab1)
        self.fig_turns = Figure(figsize=(7, 3.2), dpi=100, facecolor=Colors.BG_CARD)
        self.canvas_turns = FigureCanvas(self.fig_turns)
        self.tbl_turns = QTableWidget(0, 4)
        self.tbl_turns.setHorizontalHeaderLabels(["Ngày", "Lượt vào", "Lượt ra", "Báo cáo ca"])
        style_table(self.tbl_turns)
        t1_lay.addWidget(self.canvas_turns, 3)
        t1_lay.addWidget(self.tbl_turns, 2)
        self.tabs.addTab(tab1, "1.8.1 Thống kê lượt xe")

        # Tab 2: 1.8.2 Cư dân vs Vãng lai
        tab2 = QWidget()
        t2_lay = QVBoxLayout(tab2)
        self.fig_compare = Figure(figsize=(7, 3.2), dpi=100, facecolor=Colors.BG_CARD)
        self.canvas_compare = FigureCanvas(self.fig_compare)
        self.tbl_compare = QTableWidget(0, 7)
        self.tbl_compare.setHorizontalHeaderLabels(
            ["Ngày", "Cư dân Ô tô", "Cư dân Xe máy", "Tổng Cư dân", "Vãng lai Ô tô", "Vãng lai Xe máy", "Tổng Vãng lai"]
        )
        style_table(self.tbl_compare)
        t2_lay.addWidget(self.canvas_compare, 3)
        t2_lay.addWidget(self.tbl_compare, 2)
        self.tabs.addTab(tab2, "1.8.2 Cư dân vs Vãng lai")

        # Tab 3: 1.8.3 Doanh thu
        tab3 = QWidget()
        t3_lay = QVBoxLayout(tab3)
        self.fig_revenue = Figure(figsize=(7, 3.2), dpi=100, facecolor=Colors.BG_CARD)
        self.canvas_revenue = FigureCanvas(self.fig_revenue)
        self.tbl_revenue = QTableWidget(0, 4)
        self.tbl_revenue.setHorizontalHeaderLabels(
            ["Kỳ (Ngày/Tháng)", "Phí tháng Cư dân", "Thu khách Vãng lai", "Tổng doanh thu"]
        )
        style_table(self.tbl_revenue)
        t3_lay.addWidget(self.canvas_revenue, 3)
        t3_lay.addWidget(self.tbl_revenue, 2)
        self.tabs.addTab(tab3, "1.8.3 Thống kê doanh thu")

        root.addWidget(self.tabs, 1)

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            self.lbl_sub.setText("Chưa kết nối Server — không thể tải thống kê.")
            return

        payload = {
            "start_date": self.dt_start.date().toString("yyyy-MM-dd"),
            "end_date": self.dt_end.date().toString("yyyy-MM-dd"),
            "ma_khu_vuc": str(self.cbo_zone.currentData() or ""),
            "ma_loai_xe": str(self.cbo_vehicle_type.currentData() or ""),
            "group_by": str(self.cbo_group.currentData() or "day"),
        }
        turns_resp = self.socket_client.call(Actions.STATS_VEHICLE_TURNS, payload)
        cmp_resp = self.socket_client.call(Actions.STATS_RESIDENT_VS_VISITOR, payload)
        rev_resp = self.socket_client.call(Actions.STATS_REVENUE, payload)

        self.apply_stats_data(
            turns_data=(turns_resp.get("data") or {}) if turns_resp.get("ok") else {},
            compare_data=(cmp_resp.get("data") or {}) if cmp_resp.get("ok") else {},
            revenue_data=(rev_resp.get("data") or {}) if rev_resp.get("ok") else {},
        )

    def apply_stats_data(
        self,
        turns_data: Dict[str, Any],
        compare_data: Dict[str, Any],
        revenue_data: Dict[str, Any],
    ) -> None:
        """Đổ dữ liệu 1.8.1–1.8.3 vào KPI, 3 biểu đồ Matplotlib và 3 bảng đối chiếu."""
        # KPI
        self.card_turns.set_value(
            f"{turns_data.get('tong_luot_vao', 0)} / {turns_data.get('tong_luot_ra', 0)}",
            f"Tổng: {turns_data.get('tong_luot', 0)} lượt vào & ra",
        )
        self.card_ratio.set_value(
            f"{compare_data.get('tong_cu_dan', 0)} / {compare_data.get('tong_vang_lai', 0)}",
            f"Ô tô VL: {compare_data.get('vang_lai_oto', 0)} · Xe máy VL: {compare_data.get('vang_lai_xemay', 0)}",
        )
        self.card_rev_res.set_value(
            _fmt_vnd(revenue_data.get("doanh_thu_cu_dan", 0)),
            "Phí tháng cư dân đã thanh toán",
        )
        self.card_rev_total.set_value(
            _fmt_vnd(revenue_data.get("tong_doanh_thu", 0)),
            f"Vãng lai: {_fmt_vnd(revenue_data.get('doanh_thu_vang_lai', 0))}",
        )

        # 1.8.1 Chart & Table
        t_series = turns_data.get("series") or []
        self.tbl_turns.setRowCount(len(t_series))
        for i, row in enumerate(t_series):
            self.tbl_turns.setItem(i, 0, QTableWidgetItem(str(row.get("ngay") or "")))
            self.tbl_turns.setItem(i, 1, QTableWidgetItem(str(row.get("luot_vao") or 0)))
            self.tbl_turns.setItem(i, 2, QTableWidgetItem(str(row.get("luot_ra") or 0)))
            self.tbl_turns.setItem(i, 3, QTableWidgetItem(str(row.get("bao_cao_ca") or 0)))

        self.fig_turns.clear()
        ax1 = self.fig_turns.add_subplot(111)
        ax1.set_facecolor(Colors.BG_CARD)
        if t_series:
            labels = [str(x.get("ngay") or "") for x in t_series]
            xs = list(range(len(labels)))
            in_vals = [int(x.get("luot_vao") or 0) for x in t_series]
            out_vals = [int(x.get("luot_ra") or 0) for x in t_series]
            w = 0.36
            ax1.bar([x - w / 2 for x in xs], in_vals, width=w, label="Lượt vào", color=Colors.ROYAL_BLUE)
            ax1.bar([x + w / 2 for x in xs], out_vals, width=w, label="Lượt ra", color=Colors.EMERALD_TEAL)
            ax1.set_xticks(xs)
            ax1.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
            ax1.legend(frameon=False)
        else:
            ax1.text(0.5, 0.5, "Chưa có dữ liệu lượt xe trong kỳ", ha="center", va="center", color=Colors.TEXT_MUTED)
        ax1.set_title("Biểu đồ Lượt Xe Vào / Ra (1.8.1)", fontsize=11, fontweight="bold", color=Colors.TEXT_PRIMARY)
        ax1.grid(axis="y", linestyle="--", alpha=0.35)
        self.fig_turns.tight_layout()
        self.canvas_turns.draw()

        # 1.8.2 Chart & Table
        c_series = compare_data.get("series") or []
        self.tbl_compare.setRowCount(len(c_series))
        for i, row in enumerate(c_series):
            self.tbl_compare.setItem(i, 0, QTableWidgetItem(str(row.get("ngay") or "")))
            self.tbl_compare.setItem(i, 1, QTableWidgetItem(str(row.get("cu_dan_oto") or 0)))
            self.tbl_compare.setItem(i, 2, QTableWidgetItem(str(row.get("cu_dan_xemay") or 0)))
            self.tbl_compare.setItem(i, 3, QTableWidgetItem(str(row.get("cu_dan_tong") or 0)))
            self.tbl_compare.setItem(i, 4, QTableWidgetItem(str(row.get("vang_lai_oto") or 0)))
            self.tbl_compare.setItem(i, 5, QTableWidgetItem(str(row.get("vang_lai_xemay") or 0)))
            self.tbl_compare.setItem(i, 6, QTableWidgetItem(str(row.get("vang_lai_tong") or 0)))

        self.fig_compare.clear()
        ax2 = self.fig_compare.add_subplot(111)
        ax2.set_facecolor(Colors.BG_CARD)
        if c_series:
            labels = [str(x.get("ngay") or "") for x in c_series]
            xs = list(range(len(labels)))
            cd_vals = [int(x.get("cu_dan_tong") or 0) for x in c_series]
            vl_vals = [int(x.get("vang_lai_tong") or 0) for x in c_series]
            w = 0.36
            ax2.bar([x - w / 2 for x in xs], cd_vals, width=w, label="Cư dân", color=Colors.INDIGO)
            ax2.bar([x + w / 2 for x in xs], vl_vals, width=w, label="Khách vãng lai", color=Colors.WARNING)
            ax2.set_xticks(xs)
            ax2.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
            ax2.legend(frameon=False)
        else:
            ax2.text(0.5, 0.5, "Chưa có dữ liệu so sánh", ha="center", va="center", color=Colors.TEXT_MUTED)
        ax2.set_title("So sánh Xe Cư dân vs Khách Vãng lai (1.8.2)", fontsize=11, fontweight="bold", color=Colors.TEXT_PRIMARY)
        ax2.grid(axis="y", linestyle="--", alpha=0.35)
        self.fig_compare.tight_layout()
        self.canvas_compare.draw()

        # 1.8.3 Chart & Table
        r_series = revenue_data.get("series") or []
        self.tbl_revenue.setRowCount(len(r_series))
        for i, row in enumerate(r_series):
            self.tbl_revenue.setItem(i, 0, QTableWidgetItem(str(row.get("period") or "")))
            self.tbl_revenue.setItem(i, 1, QTableWidgetItem(_fmt_vnd(row.get("doanh_thu_cu_dan"))))
            self.tbl_revenue.setItem(i, 2, QTableWidgetItem(_fmt_vnd(row.get("doanh_thu_vang_lai"))))
            self.tbl_revenue.setItem(i, 3, QTableWidgetItem(_fmt_vnd(row.get("tong_doanh_thu"))))

        self.fig_revenue.clear()
        ax3 = self.fig_revenue.add_subplot(111)
        ax3.set_facecolor(Colors.BG_CARD)
        if r_series:
            labels = [str(x.get("period") or "") for x in r_series]
            xs = list(range(len(labels)))
            cd_rev = [float(x.get("doanh_thu_cu_dan") or 0) for x in r_series]
            vl_rev = [float(x.get("doanh_thu_vang_lai") or 0) for x in r_series]
            tot_rev = [float(x.get("tong_doanh_thu") or 0) for x in r_series]
            ax3.bar(xs, cd_rev, label="Phí tháng Cư dân", color=Colors.ROYAL_BLUE)
            ax3.bar(xs, vl_rev, bottom=cd_rev, label="Khách vãng lai", color=Colors.EMERALD_TEAL)
            ax3.plot(xs, tot_rev, color=Colors.SUCCESS, marker="o", linewidth=2, label="Tổng doanh thu")
            ax3.set_xticks(xs)
            ax3.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
            ax3.legend(frameon=False)
        else:
            ax3.text(0.5, 0.5, "Chưa có dữ liệu doanh thu", ha="center", va="center", color=Colors.TEXT_MUTED)
        ax3.set_title("Cơ cấu Doanh thu Theo Kỳ (1.8.3)", fontsize=11, fontweight="bold", color=Colors.TEXT_PRIMARY)
        ax3.grid(axis="y", linestyle="--", alpha=0.35)
        self.fig_revenue.tight_layout()
        self.canvas_revenue.draw()


def _pixmap_from_b64(b64_text: str) -> Optional[QPixmap]:
    if not b64_text:
        return None
    raw = str(b64_text).strip()
    if raw.startswith("data:image/") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw)
        pix = QPixmap()
        if pix.loadFromData(data):
            return pix
    except Exception:
        return None
    return None


class HistoryDetailDialog(QDialog):
    """Hộp thoại 1.9.3: Xem chi tiết lượt gửi kèm 2 ảnh đối chiếu cổng vào & cổng ra."""

    def __init__(self, session_data: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        code = str(session_data.get("ma_luot_gui") or "-")
        self.setWindowTitle(f"Chi tiết Lượt gửi {code} (1.9.3)")
        self.setMinimumSize(720, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        top_card = SectionCard(
            title=f"Lượt gửi: {code}",
            subtitle=f"Trạng thái: {session_data.get('trang_thai') or '-'}",
        )
        plate = str(session_data.get("bien_so_xe") or "---")
        top_card.add_header_widget(PlateBadge(plate))

        form = QFormLayout()
        form.setSpacing(6)
        nv_vao = session_data.get("ten_nhan_vien_vao") or session_data.get("ma_nhan_vien_vao") or "-"
        nv_ra = session_data.get("ten_nhan_vien_ra") or session_data.get("ma_nhan_vien_ra") or "-"
        for label, val in (
            ("Loại khách / Loại xe:", f"{session_data.get('loai_khach') or '-'} · {session_data.get('ma_loai_xe') or '-'}"),
            ("Khu vực / Vị trí:", f"{session_data.get('ma_khu_vuc') or '-'} / {session_data.get('ma_vi_tri') or '-'}"),
            ("Thời gian vào:", session_data.get("thoi_gian_vao") or "-"),
            ("Thời gian ra:", session_data.get("thoi_gian_ra") or "Chưa ra bãi"),
            ("Nhân viên vào / ra:", f"{nv_vao} / {nv_ra}"),
            ("Phí thu:", _fmt_vnd(session_data.get("tien_thu"))),
        ):
            form.addRow(label, QLabel(str(val)))
        top_card.body_layout.addLayout(form)
        root.addWidget(top_card)

        # Khung 2 ảnh đối chiếu cổng vào & cổng ra
        imgs_row = QHBoxLayout()
        imgs_row.setSpacing(12)
        for title, b64_key in (("Ảnh cổng VÀO", "anh_vao"), ("Ảnh cổng RA", "anh_ra")):
            img_card = SectionCard(title=title)
            lbl_img = QLabel("Chưa có ảnh ghi nhận")
            lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_img.setMinimumSize(280, 190)
            lbl_img.setStyleSheet(
                f"background-color: {Colors.BG_ELEVATED}; border: 1px dashed {Colors.BORDER_INPUT}; "
                f"border-radius: 8px; color: {Colors.TEXT_MUTED};"
            )
            pix = _pixmap_from_b64(str(session_data.get(b64_key) or ""))
            if pix is not None:
                lbl_img.setPixmap(
                    pix.scaled(
                        300,
                        200,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            img_card.body_layout.addWidget(lbl_img)
            imgs_row.addWidget(img_card, 1)
        root.addLayout(imgs_row, 1)

        btn_close = QPushButton("Đóng")
        set_button_variant(btn_close, "default", "sm")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)


class ManagerHistoryView(QWidget):
    """Màn hình 1.9 (Quản lý) & 4.8 (Admin Read-Only): Tra cứu lịch sử xe vào/ra."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        read_only: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.read_only = read_only
        self._items: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        if self.read_only:
            root.addWidget(ReadOnlyBanner(self))

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Tra cứu Lịch sử Xe Vào / Ra (1.9)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_sub = QLabel("Truy xuất nhật ký parking_history_*.jsonl kèm ảnh đối chiếu vào/ra")
        self.lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        header.addLayout(title_col, 1)

        self.btn_detail = QPushButton("Xem chi tiết & Ảnh (1.9.3)")
        set_button_variant(self.btn_detail, "primary", "sm")
        self.btn_detail.clicked.connect(self._open_selected_detail)
        header.addWidget(self.btn_detail)

        self.btn_refresh = QPushButton("Tra cứu")
        set_button_variant(self.btn_refresh, "teal", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        # Bộ lọc
        filter_card = SectionCard(title="Bộ lọc lịch sử vào / ra")
        f_row = QHBoxLayout()
        f_row.setSpacing(10)

        self.cbo_mode = QComboBox()
        self.cbo_mode.addItem("1.9.1 Lịch sử xe vào", "entries")
        self.cbo_mode.addItem("1.9.2 Lịch sử xe ra", "exits")
        self.cbo_mode.currentIndexChanged.connect(self.refresh_data)

        self.txt_plate = QLineEdit()
        self.txt_plate.setPlaceholderText("Biển số xe (VD: 51H-888.99)...")
        self.txt_plate.returnPressed.connect(self.refresh_data)

        self.cbo_guest = QComboBox()
        self.cbo_guest.addItem("Tất cả khách", "")
        self.cbo_guest.addItem("Cư dân", "cu_dan")
        self.cbo_guest.addItem("Vãng lai", "vang_lai")

        today = QDate.currentDate()
        self.dt_start = QDateEdit(today.addDays(-30))
        self.dt_start.setCalendarPopup(True)
        self.dt_start.setDisplayFormat("yyyy-MM-dd")
        self.dt_end = QDateEdit(today)
        self.dt_end.setCalendarPopup(True)
        self.dt_end.setDisplayFormat("yyyy-MM-dd")

        f_row.addWidget(self.cbo_mode)
        f_row.addWidget(self.txt_plate, 1)
        f_row.addWidget(self.cbo_guest)
        f_row.addWidget(QLabel("Từ:"))
        f_row.addWidget(self.dt_start)
        f_row.addWidget(QLabel("Đến:"))
        f_row.addWidget(self.dt_end)
        filter_card.body_layout.addLayout(f_row)
        root.addWidget(filter_card)

        list_card = SectionCard(
            title="Kết quả tra cứu lượt gửi",
            subtitle="Nhấp đúp vào một dòng để mở chi tiết & ảnh đối chiếu vào/ra",
        )
        self.tbl_history = QTableWidget(0, 8)
        self.tbl_history.setHorizontalHeaderLabels(
            ["Mã lượt", "Biển số", "Loại khách", "Loại xe", "Khu vực / Ô", "Giờ vào", "Giờ ra / Tiền thu", "Trạng thái"]
        )
        style_table(self.tbl_history)
        self.tbl_history.doubleClicked.connect(lambda _idx: self._open_selected_detail())
        list_card.body_layout.addWidget(self.tbl_history, 1)
        root.addWidget(list_card, 1)

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            self.lbl_sub.setText("Chưa kết nối Server — không thể tải lịch sử.")
            return

        mode = str(self.cbo_mode.currentData() or "entries")
        action = Actions.HISTORY_EXITS if mode == "exits" else Actions.HISTORY_ENTRIES
        payload = {
            "bien_so_xe": self.txt_plate.text().strip(),
            "loai_khach": str(self.cbo_guest.currentData() or ""),
            "start_date": self.dt_start.date().toString("yyyy-MM-dd"),
            "end_date": self.dt_end.date().toString("yyyy-MM-dd"),
            "limit": 200,
        }
        resp = self.socket_client.call(action, payload)
        if not resp.get("ok"):
            self.lbl_sub.setText(f"Lỗi tải lịch sử: {resp.get('error')}")
            return
        data = resp.get("data") or {}
        self.apply_history_items(data.get("items") or [], mode=mode)

    def apply_history_items(self, items: List[Dict[str, Any]], mode: str = "entries") -> None:
        self._items = list(items)
        self.lbl_sub.setText(f"Tìm thấy {len(self._items)} lượt gửi ({'Xe ra' if mode == 'exits' else 'Xe vào'})")
        self.tbl_history.setRowCount(len(self._items))
        for i, row in enumerate(self._items):
            st = str(row.get("trang_thai") or "dang_gui")
            col6 = (
                f"{row.get('thoi_gian_ra') or '-'} · {_fmt_vnd(row.get('tien_thu'))}"
                if mode == "exits" or row.get("thoi_gian_ra")
                else "-"
            )
            self.tbl_history.setItem(i, 0, QTableWidgetItem(str(row.get("ma_luot_gui") or "")))
            self.tbl_history.setItem(i, 1, QTableWidgetItem(str(row.get("bien_so_xe") or "")))
            self.tbl_history.setItem(i, 2, QTableWidgetItem("Cư dân" if row.get("loai_khach") == "cu_dan" else "Vãng lai"))
            self.tbl_history.setItem(i, 3, QTableWidgetItem(str(row.get("ma_loai_xe") or "")))
            self.tbl_history.setItem(i, 4, QTableWidgetItem(f"{row.get('ma_khu_vuc') or '-'} / {row.get('ma_vi_tri') or '-'}"))
            self.tbl_history.setItem(i, 5, QTableWidgetItem(str(row.get("thoi_gian_vao") or "")))
            self.tbl_history.setItem(i, 6, QTableWidgetItem(col6))
            self.tbl_history.setCellWidget(
                i, 7, StatusBadge("Đã ra bãi" if st == "da_ra_bai" else "Đang gửi", "success" if st == "da_ra_bai" else "info")
            )

    def _open_selected_detail(self) -> None:
        row_idx = self.tbl_history.currentRow()
        if row_idx < 0 or row_idx >= len(self._items):
            return
        selected = self._items[row_idx]
        detail = dict(selected)
        if self.socket_client is not None and self.socket_client.is_connected():
            resp = self.socket_client.call(
                Actions.HISTORY_SESSION_DETAIL,
                {"ma_luot_gui": selected.get("ma_luot_gui")},
            )
            if resp.get("ok"):
                detail = (resp.get("data") or {}).get("session") or detail
        dlg = HistoryDetailDialog(detail, parent=self)
        dlg.exec()


_EMERGENCY_PRESETS = {
    "chay": (
        "KHẨN CẤP: Cảnh báo cháy nổ tầng hầm",
        "Phát hiện cảnh báo khói/cháy tại khu vực hầm giữ xe. Đề nghị toàn bộ cư dân và nhân viên sơ tán theo lối thoát hiểm ngay lập tức!",
        NotifyTypes.KHAN_CAP.value,
        NotifyLevels.KHAN_CAP.value,
    ),
    "thien_tai": (
        "KHẨN CẤP: Cảnh báo mưa bão / ngập hầm",
        "Dự báo mưa lớn có nguy cơ ngập nước. Đề nghị chủ xe tại Hầm B1 nhanh chóng di chuyển phương tiện lên khu vực cao ráo.",
        NotifyTypes.KHAN_CAP.value,
        NotifyLevels.KHAN_CAP.value,
    ),
    "mat_dien": (
        "KHẨN CẤP: Sự cố mất điện toàn khu bãi xe",
        "Hệ thống điện lưới khu vực bãi giữ xe đang gặp sự cố, barie chuyển sang chế độ vận hành dự phòng. Nhân viên trực cổng hỗ trợ kiểm soát thủ công.",
        NotifyTypes.KHAN_CAP.value,
        NotifyLevels.KHAN_CAP.value,
    ),
    "bao_tri": (
        "Thông báo bảo trì định kỳ hệ thống bãi xe",
        "Ban Quản lý tiến hành bảo trì camera cổng vào/ra và sơn kẻ lại vạch ô đỗ từ 09:00 đến 11:30. Mong quý cư dân thông cảm.",
        NotifyTypes.BAO_TRI_KY_THUAT.value,
        NotifyLevels.QUAN_TRONG.value,
    ),
}


class ManagerNotifyView(QWidget):
    """Màn hình 1.10 Quản lý Thông báo & Cảnh báo Khẩn cấp (Workflow 01)."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        read_only: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.read_only = read_only
        self._draft_id: Optional[str] = None
        self._sent_items: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        if self.read_only:
            root.addWidget(ReadOnlyBanner(self))

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Quản lý Thông báo & Cảnh báo Khẩn cấp (1.10)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_sub = QLabel("Soạn nháp, phát thông báo Push real-time và theo dõi ACK / Retry")
        self.lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        header.addLayout(title_col, 1)

        self.btn_refresh = QPushButton("Làm mới danh sách")
        set_button_variant(self.btn_refresh, "default", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)

        # Cột trái: 1.10.1 Soạn & 1.10.2 Phát thông báo
        compose_card = SectionCard(
            title="1.10.1 – 1.10.2 Soạn & Phát thông báo",
            subtitle="Chọn mẫu nhanh sự cố hoặc soạn nội dung tùy chỉnh",
        )
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        for key, label in (
            ("chay", "Cháy"),
            ("thien_tai", "Thiên tai"),
            ("mat_dien", "Mất điện"),
            ("bao_tri", "Bảo trì"),
        ):
            btn_p = QPushButton(label)
            set_button_variant(btn_p, "danger" if key != "bao_tri" else "default", "sm")
            btn_p.clicked.connect(lambda _c=False, k=key: self.apply_preset(k))
            btn_p.setEnabled(not self.read_only)
            preset_row.addWidget(btn_p)
        preset_row.addStretch(1)
        compose_card.body_layout.addLayout(preset_row)

        form = QFormLayout()
        form.setSpacing(8)
        self.txt_title = QLineEdit()
        self.txt_title.setPlaceholderText("Nhập tiêu đề thông báo...")
        self.txt_content = QTextEdit()
        self.txt_content.setPlaceholderText("Nhập nội dung thông báo chi tiết...")
        self.txt_content.setMinimumHeight(96)

        self.cbo_type = QComboBox()
        self.cbo_type.addItem("Khẩn cấp (khan_cap)", NotifyTypes.KHAN_CAP.value)
        self.cbo_type.addItem("Bảo trì kỹ thuật", NotifyTypes.BAO_TRI_KY_THUAT.value)
        self.cbo_type.addItem("Kết quả duyệt hồ sơ", NotifyTypes.KET_QUA_DUYET_HO_SO.value)

        self.cbo_level = QComboBox()
        self.cbo_level.addItem("Khẩn cấp (khan_cap)", NotifyLevels.KHAN_CAP.value)
        self.cbo_level.addItem("Quan trọng", NotifyLevels.QUAN_TRONG.value)
        self.cbo_level.addItem("Bình thường", NotifyLevels.BINH_THUONG.value)

        self.cbo_group = QComboBox()
        self.cbo_group.addItem("Tất cả (Cư dân & Nhân viên)", NotifyRecipientGroups.TAT_CA.value)
        self.cbo_group.addItem("Chỉ Cư dân", NotifyRecipientGroups.CU_DAN.value)
        self.cbo_group.addItem("Chỉ Nhân viên", NotifyRecipientGroups.NHAN_VIEN.value)
        self.cbo_group.addItem("Cá nhân (theo mã tài khoản)", NotifyRecipientGroups.CA_NHAN.value)
        self.cbo_group.currentIndexChanged.connect(self._on_group_changed)

        self.txt_target_id = QLineEdit()
        self.txt_target_id.setPlaceholderText("Mã tài khoản cá nhân (VD: 4)")
        self.txt_target_id.setEnabled(False)

        form.addRow("Tiêu đề:", self.txt_title)
        form.addRow("Loại thông báo:", self.cbo_type)
        form.addRow("Mức độ:", self.cbo_level)
        form.addRow("Nhóm người nhận:", self.cbo_group)
        form.addRow("Mã TK cá nhân:", self.txt_target_id)
        form.addRow("Nội dung:", self.txt_content)
        compose_card.body_layout.addLayout(form)

        self.lbl_draft_status = QLabel("Chưa lưu nháp")
        self.lbl_draft_status.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_MUTED};")
        compose_card.body_layout.addWidget(self.lbl_draft_status)

        btn_row = QHBoxLayout()
        self.btn_save_draft = QPushButton("Lưu nháp (1.10.1)")
        set_button_variant(self.btn_save_draft, "default", "md")
        self.btn_save_draft.clicked.connect(self._on_save_draft)

        self.btn_publish = QPushButton("Gửi thông báo (1.10.2)")
        set_button_variant(self.btn_publish, "danger", "md")
        self.btn_publish.clicked.connect(self._on_publish)

        btn_row.addWidget(self.btn_save_draft)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_publish)
        compose_card.body_layout.addLayout(btn_row)

        if self.read_only:
            for w in (self.txt_title, self.txt_content, self.cbo_type, self.cbo_level, self.cbo_group, self.txt_target_id):
                w.setEnabled(False)
            self.btn_save_draft.setVisible(False)
            self.btn_publish.setVisible(False)

        splitter.addWidget(compose_card)

        # Cột phải: 1.10.3 Lịch sử thông báo đã gửi & chi tiết tiếp nhận / Retry
        right_col = QWidget()
        r_lay = QVBoxLayout(right_col)
        r_lay.setContentsMargins(0, 0, 0, 0)
        r_lay.setSpacing(10)

        sent_card = SectionCard(
            title="1.10.3 Thông báo đã gửi & Trạng thái tiếp nhận",
            subtitle="Chọn một thông báo để xem chi tiết từng người nhận hoặc bấm Thử gửi lại",
        )
        self.btn_retry = QPushButton("Thử gửi lại (Retry)")
        set_button_variant(self.btn_retry, "primary", "sm")
        self.btn_retry.clicked.connect(self._on_retry_selected)
        if self.read_only:
            self.btn_retry.setVisible(False)
        else:
            sent_card.add_header_widget(self.btn_retry)

        self.tbl_sent = QTableWidget(0, 7)
        self.tbl_sent.setHorizontalHeaderLabels(
            ["Mã", "Tiêu đề", "Loại / Mức độ", "Nhóm nhận", "Gửi OK / Lỗi", "Đã tiếp nhận / Đọc", "Thời gian gửi"]
        )
        style_table(self.tbl_sent)
        self.tbl_sent.itemSelectionChanged.connect(self._on_sent_selected)
        sent_card.body_layout.addWidget(self.tbl_sent, 1)
        r_lay.addWidget(sent_card, 3)

        rec_card = SectionCard(title="Chi tiết người nhận thông báo đang chọn")
        self.tbl_recipients = QTableWidget(0, 6)
        self.tbl_recipients.setHorizontalHeaderLabels(
            ["Tài khoản", "Họ tên", "Vai trò", "Trạng thái gửi", "Lần gửi lại", "Tiếp nhận / Đọc"]
        )
        style_table(self.tbl_recipients)
        rec_card.body_layout.addWidget(self.tbl_recipients, 1)
        r_lay.addWidget(rec_card, 2)

        splitter.addWidget(right_col)
        splitter.setSizes([440, 620])
        root.addWidget(splitter, 1)

    def _on_group_changed(self) -> None:
        is_personal = self.cbo_group.currentData() == NotifyRecipientGroups.CA_NHAN.value
        self.txt_target_id.setEnabled(is_personal and not self.read_only)

    def apply_preset(self, preset_key: str) -> None:
        preset = _EMERGENCY_PRESETS.get(preset_key)
        if not preset:
            return
        title, content, loai, muc_do = preset
        self.txt_title.setText(title)
        self.txt_content.setPlainText(content)
        idx_t = self.cbo_type.findData(loai)
        if idx_t >= 0:
            self.cbo_type.setCurrentIndex(idx_t)
        idx_l = self.cbo_level.findData(muc_do)
        if idx_l >= 0:
            self.cbo_level.setCurrentIndex(idx_l)

    def _collect_form_payload(self) -> Dict[str, Any]:
        return {
            "draft_id": self._draft_id,
            "tieu_de": self.txt_title.text().strip(),
            "noi_dung": self.txt_content.toPlainText().strip(),
            "loai_thong_bao": str(self.cbo_type.currentData() or NotifyTypes.KHAN_CAP.value),
            "muc_do": str(self.cbo_level.currentData() or NotifyLevels.KHAN_CAP.value),
            "nhom_nguoi_nhan": str(self.cbo_group.currentData() or NotifyRecipientGroups.TAT_CA.value),
            "ma_tai_khoan_ca_nhan": self.txt_target_id.text().strip() or None,
        }

    def _on_save_draft(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            ToastNotification.show_toast(self, "Chưa kết nối tới Server.", variant="warning")
            return
        resp = self.socket_client.call(Actions.NOTIFY_SAVE_DRAFT, self._collect_form_payload())
        if not resp.get("ok"):
            ToastNotification.show_toast(self, str(resp.get("error") or "Lỗi lưu nháp."), variant="danger")
            return
        data = resp.get("data") or {}
        self._draft_id = str(data.get("draft_id") or "")
        self.lbl_draft_status.setText(f"Đã lưu nháp: {self._draft_id}")
        ToastNotification.show_toast(self, f"Đã lưu nháp {self._draft_id}.", variant="success")

    def _on_publish(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            ToastNotification.show_toast(self, "Chưa kết nối tới Server.", variant="warning")
            return
        resp = self.socket_client.call(Actions.NOTIFY_PUBLISH, self._collect_form_payload())
        if not resp.get("ok"):
            ToastNotification.show_toast(self, str(resp.get("error") or "Gửi thông báo thất bại."), variant="danger")
            return
        data = resp.get("data") or {}
        self._draft_id = None
        self.lbl_draft_status.setText("Đã phát hành thông báo.")
        ToastNotification.show_toast(
            self,
            f"Đã phát thông báo tới {data.get('tong_nguoi_nhan', 0)} tài khoản "
            f"(OK: {data.get('so_gui_thanh_cong', 0)}, Lỗi: {data.get('so_gui_that_bai', 0)}).",
            variant="success" if int(data.get("so_gui_that_bai") or 0) == 0 else "warning",
        )
        self.refresh_data()

    def _on_retry_selected(self) -> None:
        row_idx = self.tbl_sent.currentRow()
        if row_idx < 0 or row_idx >= len(self._sent_items):
            ToastNotification.show_toast(self, "Vui lòng chọn thông báo cần thử gửi lại.", variant="warning")
            return
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        nid = self._sent_items[row_idx].get("ma_thong_bao")
        resp = self.socket_client.call(
            Actions.NOTIFY_RETRY_FAILED,
            {"ma_thong_bao": nid, "include_unacked": True},
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self, str(resp.get("error") or "Thử gửi lại thất bại."), variant="danger")
            return
        data = resp.get("data") or {}
        ToastNotification.show_toast(
            self,
            f"Đã thử gửi lại {data.get('retried_count', 0)} lượt (Thành công: {data.get('so_gui_thanh_cong', 0)}).",
            variant="success",
        )
        self.refresh_data()

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(Actions.NOTIFY_LIST_SENT, {})
        if not resp.get("ok"):
            return
        items = (resp.get("data") or {}).get("items") or []
        self.apply_sent_items(items)

    def apply_sent_items(self, items: List[Dict[str, Any]]) -> None:
        self._sent_items = list(items)
        self.tbl_sent.setRowCount(len(self._sent_items))
        for i, it in enumerate(self._sent_items):
            self.tbl_sent.setItem(i, 0, QTableWidgetItem(str(it.get("ma_thong_bao") or "")))
            self.tbl_sent.setItem(i, 1, QTableWidgetItem(str(it.get("tieu_de") or "")))
            self.tbl_sent.setItem(i, 2, QTableWidgetItem(f"{it.get('loai_thong_bao')} / {it.get('muc_do')}"))
            self.tbl_sent.setItem(i, 3, QTableWidgetItem(str(it.get("nhom_nguoi_nhan") or "")))
            self.tbl_sent.setItem(
                i, 4, QTableWidgetItem(f"{it.get('so_gui_thanh_cong', 0)} OK / {it.get('so_gui_that_bai', 0)} Lỗi")
            )
            self.tbl_sent.setItem(
                i, 5, QTableWidgetItem(f"{it.get('so_da_tiep_nhan', 0)} ACK / {it.get('so_da_doc', 0)} Đọc")
            )
            self.tbl_sent.setItem(i, 6, QTableWidgetItem(str(it.get("thoi_gian_gui") or "")))

        if self._sent_items:
            self.tbl_sent.selectRow(0)
            self._populate_recipients(self._sent_items[0].get("recipients") or [])
        else:
            self.tbl_recipients.setRowCount(0)

    def _on_sent_selected(self) -> None:
        idx = self.tbl_sent.currentRow()
        if 0 <= idx < len(self._sent_items):
            self._populate_recipients(self._sent_items[idx].get("recipients") or [])

    def _populate_recipients(self, recs: List[Dict[str, Any]]) -> None:
        self.tbl_recipients.setRowCount(len(recs))
        for i, r in enumerate(recs):
            send_st = str(r.get("trang_thai_gui") or "")
            ack_st = str(r.get("trang_thai_tiep_nhan") or "")
            read_txt = "Đã đọc" if r.get("da_doc") else "Chưa đọc"
            self.tbl_recipients.setItem(i, 0, QTableWidgetItem(str(r.get("ten_dang_nhap") or r.get("ma_tai_khoan_nhan"))))
            self.tbl_recipients.setItem(i, 1, QTableWidgetItem(str(r.get("ho_ten_nhan") or "")))
            self.tbl_recipients.setItem(i, 2, QTableWidgetItem(str(r.get("vai_tro") or "")))
            self.tbl_recipients.setCellWidget(
                i,
                3,
                StatusBadge(
                    "Gửi thành công" if send_st == "gui_thanh_cong" else "Gửi thất bại",
                    "success" if send_st == "gui_thanh_cong" else "danger",
                ),
            )
            self.tbl_recipients.setItem(i, 4, QTableWidgetItem(str(r.get("so_lan_gui_lai") or 0)))
            self.tbl_recipients.setItem(
                i,
                5,
                QTableWidgetItem(f"{'Đã tiếp nhận' if ack_st == 'da_tiep_nhan' else 'Chưa tiếp nhận'} · {read_txt}"),
            )
