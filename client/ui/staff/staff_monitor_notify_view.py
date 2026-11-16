"""Giao diện Nhân viên của Thành viên 3 (Minh):
- StaffMonitorView (2.6.1, 2.6.2): Giám sát bãi xe 2D Grid & 3D Sa bàn tương tác
- StaffNotifyInboxView (2.7.1–2.7.3): Hộp thư thông báo, xem chi tiết, ACK khẩn cấp & đánh dấu đã đọc
"""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.manager.stats_history_view import SlotDetailDialog
from client.ui.theme.components import (
    ParkingGridWidget,
    SectionCard,
    StatCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
    style_table,
)
from client.ui.theme.parking_3d_widget import Parking3DSceneWidget
from client.ui.theme.tokens import Colors, Fonts
from shared.constants import NotifyLevels, NotifyTypes, SlotStatus
from shared.protocol import Actions


class StaffMonitorView(QWidget):
    """Màn hình 2.6 Giám sát Bãi xe 2D/3D dành cho Nhân viên trực cổng."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Giám sát Bãi xe 2D / 3D — Nhân viên (2.6)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_sub = QLabel("Theo dõi chỗ trống, ô đang có xe và định vị nhanh trên sa bàn")
        self.lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        header.addLayout(title_col, 1)

        self.cbo_zone = QComboBox()
        self.cbo_zone.addItem("Tất cả khu vực", "")
        self.cbo_zone.currentIndexChanged.connect(self.refresh_data)
        header.addWidget(self.cbo_zone)

        self.btn_mode_2d = QPushButton("2D Lưới Ô Đỗ")
        self.btn_mode_3d = QPushButton("3D Sa Bàn Tương Tác")
        set_button_variant(self.btn_mode_2d, "primary", "sm")
        set_button_variant(self.btn_mode_3d, "default", "sm")
        self.btn_mode_2d.clicked.connect(lambda: self._switch_mode(0))
        self.btn_mode_3d.clicked.connect(lambda: self._switch_mode(1))
        header.addWidget(self.btn_mode_2d)
        header.addWidget(self.btn_mode_3d)

        self.btn_refresh = QPushButton("Làm mới")
        set_button_variant(self.btn_refresh, "teal", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        # KPI 2.6.1
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self.card_capacity = StatCard("Tổng sức chứa / Trống", "0 / 0", "Sẵn sàng đón xe", Colors.EMERALD_TEAL)
        self.card_in = StatCard("Xe đang gửi", "0", "0 cư dân · 0 vãng lai", Colors.ROYAL_BLUE)
        self.card_used = StatCard("Ô đang có xe / Phân chỗ", "0 / 0", "Cập nhật trực tiếp", Colors.ERROR)
        for card in (self.card_capacity, self.card_in, self.card_used):
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        self.zone_card = SectionCard(title="Sức chứa theo từng khu vực (2.6.1)")
        self.zone_bars_layout = QHBoxLayout()
        self.zone_bars_layout.setSpacing(12)
        self.zone_card.body_layout.addLayout(self.zone_bars_layout)
        root.addWidget(self.zone_card)

        map_card = SectionCard(
            title="Sơ đồ trạng thái bãi xe 2D / 3D (2.6.2)",
            subtitle="Click vào ô đỗ để xem chi tiết biển số, chủ xe và giờ vào",
        )
        self.map_stack = QStackedWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.grid_2d = ParkingGridWidget(columns=6)
        self.grid_2d.slot_selected.connect(self._on_slot_clicked)
        scroll.setWidget(self.grid_2d)

        self.scene_3d = Parking3DSceneWidget(columns=4)
        self.scene_3d.slot_clicked.connect(self._on_slot_clicked)
        self.map_stack.addWidget(scroll)
        self.map_stack.addWidget(self.scene_3d)
        map_card.body_layout.addWidget(self.map_stack, 1)
        root.addWidget(map_card, 1)

    def _switch_mode(self, idx: int) -> None:
        self.map_stack.setCurrentIndex(idx)
        set_button_variant(self.btn_mode_2d, "primary" if idx == 0 else "default", "sm")
        set_button_variant(self.btn_mode_3d, "primary" if idx == 1 else "default", "sm")

    def _on_slot_clicked(self, slot_data: Dict[str, Any]) -> None:
        SlotDetailDialog(slot_data, parent=self).exec()

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            self.lbl_sub.setText("Chưa kết nối Server — không thể tải sơ đồ.")
            return
        ma_khu_vuc = str(self.cbo_zone.currentData() or "")
        resp = self.socket_client.call(
            Actions.MONITOR_OVERVIEW_MAP,
            {"ma_khu_vuc": ma_khu_vuc} if ma_khu_vuc else {},
        )
        if resp.get("ok"):
            self.apply_overview_data(resp.get("data") or {})

    def apply_overview_data(self, data: Dict[str, Any]) -> None:
        summary = data.get("summary") or {}
        zones = data.get("zones") or []
        slots = data.get("slots") or []

        self.lbl_sub.setText(f"Cập nhật lúc: {data.get('updated_at') or '-'}")
        self.card_capacity.set_value(
            f"{summary.get('tong_suc_chua', 0)} / {summary.get('cho_con_trong', 0)}",
            f"Ô trống: {summary.get(SlotStatus.TRONG.value, 0)}",
        )
        self.card_in.set_value(
            str(summary.get("tong_xe_dang_gui", 0)),
            f"{summary.get('xe_cu_dan_dang_gui', 0)} cư dân · {summary.get('xe_vang_lai_dang_gui', 0)} vãng lai",
        )
        self.card_used.set_value(
            f"{summary.get(SlotStatus.DANG_CO_XE.value, 0)} / {summary.get(SlotStatus.DA_PHAN_CHO.value, 0)}",
            "Đang có xe / Đã phân chỗ",
        )

        cur_zone = self.cbo_zone.currentData()
        self.cbo_zone.blockSignals(True)
        self.cbo_zone.clear()
        self.cbo_zone.addItem("Tất cả khu vực", "")
        for z in zones:
            code = str(z.get("ma_khu_vuc") or "")
            self.cbo_zone.addItem(f"{code} — {z.get('ten_khu_vuc') or code}", code)
        idx = self.cbo_zone.findData(cur_zone)
        self.cbo_zone.setCurrentIndex(idx if idx >= 0 else 0)
        self.cbo_zone.blockSignals(False)

        while self.zone_bars_layout.count():
            item = self.zone_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
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
            v.addWidget(lbl)
            v.addWidget(bar)
            self.zone_bars_layout.addWidget(box, 1)

        self.grid_2d.set_slots(slots)
        self.scene_3d.set_slots(slots)


class StaffNotifyInboxView(QWidget):
    """Màn hình 2.7 Hộp thư Thông báo của Nhân viên (hoặc tái sử dụng cấu trúc inbox)."""

    unread_count_changed = Signal(int)

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        section_code: str = "2.7",
        role_title: str = "Nhân viên bãi xe",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.section_code = section_code
        self.role_title = role_title
        self._items: List[Dict[str, Any]] = []
        self._selected_item: Optional[Dict[str, Any]] = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel(f"Hộp thư Thông báo — {self.role_title} ({self.section_code})")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_sub = QLabel("Ưu tiên hiển thị thông báo khẩn cấp và tin chưa đọc lên đầu")
        self.lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        header.addLayout(title_col, 1)

        self.chk_only_unread = QCheckBox("Chỉ hiện chưa đọc")
        self.chk_only_unread.stateChanged.connect(self.refresh_data)
        header.addWidget(self.chk_only_unread)

        self.cbo_type = QComboBox()
        self.cbo_type.addItem("Tất cả loại", "")
        self.cbo_type.addItem("Khẩn cấp", NotifyTypes.KHAN_CAP.value)
        self.cbo_type.addItem("Bảo trì kỹ thuật", NotifyTypes.BAO_TRI_KY_THUAT.value)
        self.cbo_type.addItem("Kết quả duyệt hồ sơ", NotifyTypes.KET_QUA_DUYET_HO_SO.value)
        self.cbo_type.currentIndexChanged.connect(self.refresh_data)
        header.addWidget(self.cbo_type)

        self.btn_mark_all = QPushButton("Đánh dấu đọc tất cả")
        set_button_variant(self.btn_mark_all, "default", "sm")
        self.btn_mark_all.clicked.connect(self._on_mark_all_read)
        header.addWidget(self.btn_mark_all)

        self.btn_refresh = QPushButton("Tải lại")
        set_button_variant(self.btn_refresh, "teal", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)

        list_card = SectionCard(
            title=f"{self.section_code}.1 Danh sách thông báo",
            subtitle="Chọn một thông báo để xem nội dung chi tiết",
        )
        self.tbl_inbox = QTableWidget(0, 5)
        self.tbl_inbox.setHorizontalHeaderLabels(
            ["Mã", "Tiêu đề", "Mức độ", "Trạng thái đọc", "Thời gian gửi"]
        )
        style_table(self.tbl_inbox)
        self.tbl_inbox.itemSelectionChanged.connect(self._on_row_selected)
        list_card.body_layout.addWidget(self.tbl_inbox, 1)
        splitter.addWidget(list_card)

        detail_card = SectionCard(
            title=f"{self.section_code}.2 – {self.section_code}.3 Nội dung & Xác nhận",
            subtitle="Xác nhận đã tiếp nhận cảnh báo khẩn cấp và đánh dấu đã đọc",
        )
        form = QFormLayout()
        form.setSpacing(8)
        self.lbl_d_title = QLabel("-")
        self.lbl_d_title.setWordWrap(True)
        self.lbl_d_title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {Colors.TEXT_PRIMARY};")
        self.lbl_d_sender = QLabel("-")
        self.lbl_d_time = QLabel("-")
        self.lbl_d_ack = QLabel("-")
        form.addRow("Tiêu đề:", self.lbl_d_title)
        form.addRow("Người gửi:", self.lbl_d_sender)
        form.addRow("Thời gian gửi:", self.lbl_d_time)
        form.addRow("Tiếp nhận:", self.lbl_d_ack)
        detail_card.body_layout.addLayout(form)

        self.txt_d_content = QTextEdit()
        self.txt_d_content.setReadOnly(True)
        detail_card.body_layout.addWidget(self.txt_d_content, 1)

        action_row = QHBoxLayout()
        self.btn_ack = QPushButton("Xác nhận đã tiếp nhận")
        set_button_variant(self.btn_ack, "danger", "md")
        self.btn_ack.clicked.connect(self._on_ack_selected)

        self.btn_mark_read = QPushButton("Đánh dấu đã đọc")
        set_button_variant(self.btn_mark_read, "primary", "md")
        self.btn_mark_read.clicked.connect(self._on_mark_read_selected)

        action_row.addWidget(self.btn_ack)
        action_row.addStretch(1)
        action_row.addWidget(self.btn_mark_read)
        detail_card.body_layout.addLayout(action_row)

        splitter.addWidget(detail_card)
        splitter.setSizes([540, 480])
        root.addWidget(splitter, 1)

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            self.lbl_sub.setText("Chưa kết nối Server — không thể tải hộp thư.")
            return
        resp = self.socket_client.call(
            Actions.NOTIFY_MY_INBOX,
            {
                "only_unread": self.chk_only_unread.isChecked(),
                "loai_thong_bao": str(self.cbo_type.currentData() or ""),
            },
        )
        if not resp.get("ok"):
            self.lbl_sub.setText(f"Lỗi tải thông báo: {resp.get('error')}")
            return
        self.apply_inbox_data(resp.get("data") or {})

    def apply_inbox_data(self, data: Dict[str, Any]) -> None:
        items = list(data.get("items") or [])
        unread = int(data.get("unread_count") or 0)
        self._items = items
        self.lbl_sub.setText(f"Tổng {len(items)} thông báo · Chưa đọc: {unread}")
        self.unread_count_changed.emit(unread)

        self.tbl_inbox.setRowCount(len(items))
        for i, it in enumerate(items):
            level = str(it.get("muc_do") or NotifyLevels.BINH_THUONG.value)
            is_urgent = level == NotifyLevels.KHAN_CAP.value or it.get("loai_thong_bao") == NotifyTypes.KHAN_CAP.value
            is_read = bool(it.get("da_doc"))
            self.tbl_inbox.setItem(i, 0, QTableWidgetItem(str(it.get("ma_thong_bao") or "")))
            self.tbl_inbox.setItem(i, 1, QTableWidgetItem(str(it.get("tieu_de") or "")))
            self.tbl_inbox.setCellWidget(
                i, 2, StatusBadge("Khẩn cấp" if is_urgent else level, "danger" if is_urgent else "info")
            )
            self.tbl_inbox.setCellWidget(
                i, 3, StatusBadge("Đã đọc" if is_read else "Chưa đọc", "success" if is_read else "warning")
            )
            self.tbl_inbox.setItem(i, 4, QTableWidgetItem(str(it.get("thoi_gian_gui") or "")))

        if items:
            self.tbl_inbox.selectRow(0)
            self._show_detail(items[0])
        else:
            self._selected_item = None
            self.lbl_d_title.setText("-")
            self.lbl_d_sender.setText("-")
            self.lbl_d_time.setText("-")
            self.lbl_d_ack.setText("-")
            self.txt_d_content.clear()

    def _on_row_selected(self) -> None:
        row_idx = self.tbl_inbox.currentRow()
        if 0 <= row_idx < len(self._items):
            item = self._items[row_idx]
            if self.socket_client is not None and self.socket_client.is_connected():
                resp = self.socket_client.call(
                    Actions.NOTIFY_GET_DETAIL,
                    {"ma_thong_bao": item.get("ma_thong_bao")},
                )
                if resp.get("ok"):
                    item = (resp.get("data") or {}).get("notification") or item
            self._show_detail(item)

    def _show_detail(self, item: Dict[str, Any]) -> None:
        self._selected_item = item
        self.lbl_d_title.setText(str(item.get("tieu_de") or "-"))
        self.lbl_d_sender.setText(str(item.get("ten_nguoi_gui") or "Ban Quản Lý"))
        self.lbl_d_time.setText(str(item.get("thoi_gian_gui") or "-"))
        ack_txt = "Đã tiếp nhận" if item.get("trang_thai_tiep_nhan") == "da_tiep_nhan" else "Chưa tiếp nhận"
        if item.get("thoi_gian_tiep_nhan"):
            ack_txt += f" ({item.get('thoi_gian_tiep_nhan')})"
        self.lbl_d_ack.setText(ack_txt)
        self.txt_d_content.setPlainText(str(item.get("noi_dung") or ""))

    def _on_ack_selected(self) -> None:
        if not self._selected_item or self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.NOTIFY_ACK_EMERGENCY,
            {"ma_thong_bao": self._selected_item.get("ma_thong_bao")},
        )
        if resp.get("ok"):
            ToastNotification.show_toast(self, "Đã xác nhận tiếp nhận thông báo.", variant="success")
            self.refresh_data()

    def _on_mark_read_selected(self) -> None:
        if not self._selected_item or self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.NOTIFY_MARK_READ,
            {"ma_thong_bao": self._selected_item.get("ma_thong_bao")},
        )
        if resp.get("ok"):
            ToastNotification.show_toast(self, "Đã đánh dấu đã đọc.", variant="success")
            self.refresh_data()

    def _on_mark_all_read(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(Actions.NOTIFY_MARK_READ, {"mark_all": True})
        if resp.get("ok"):
            ToastNotification.show_toast(self, "Đã đánh dấu đọc toàn bộ thông báo.", variant="success")
            self.refresh_data()
