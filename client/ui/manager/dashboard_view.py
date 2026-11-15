"""Giao diện Mục 1.1 Dashboard tổng quan Quản lý chung cư (PySide6)."""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    ReadOnlyBanner,
    SectionCard,
    StatCard,
    StatusBadge,
    set_button_variant,
    style_table,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.protocol import Actions


def _format_vnd(amount: Any) -> str:
    try:
        return f"{int(round(float(amount or 0))):,} đ".replace(",", ".")
    except (TypeError, ValueError):
        return "0 đ"


class DashboardView(QWidget):
    """Màn hình 1.1 Dashboard tổng quan Quản lý (1.1.1 – 1.1.4)."""

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
        root.setSpacing(16)

        if self.read_only:
            root.addWidget(ReadOnlyBanner(self))

        # Thanh tiêu đề & nút làm mới
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_title = QLabel("Tổng quan vận hành bãi giữ xe")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_subtitle = QLabel("Cập nhật dữ liệu theo thời gian thực từ Socket Server")
        self.lbl_subtitle.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_box.addWidget(lbl_title)
        title_box.addWidget(self.lbl_subtitle)
        header.addLayout(title_box, 1)

        self.btn_refresh = QPushButton("Làm mới dữ liệu")
        set_button_variant(self.btn_refresh, "primary", "md")
        self.btn_refresh.clicked.connect(self.refresh_data)
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        # Hàng 4 thẻ KPI tương ứng 1.1.1 -> 1.1.4
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(14)

        self.card_pending = StatCard(
            label="1.1.1 Yêu cầu chờ duyệt",
            value="0",
            subtext="0 đăng ký xe · 0 mua thêm chỗ",
            accent_color=Colors.WARNING,
        )
        self.card_capacity = StatCard(
            label="1.1.2 Xe đang gửi / Chỗ trống",
            value="0 / 0",
            subtext="0 cư dân · 0 vãng lai",
            accent_color=Colors.ROYAL_BLUE,
        )
        self.card_revenue = StatCard(
            label="1.1.3 Doanh thu & Phí cư dân",
            value="0 đ",
            subtext="Còn nợ tháng: 0 đ · Vãng lai hôm nay: 0 đ",
            accent_color=Colors.SUCCESS,
        )
        self.card_alerts = StatCard(
            label="1.1.4 Cảnh báo hệ thống",
            value="0",
            subtext="Chưa ghi nhận cảnh báo bất thường",
            accent_color=Colors.ERROR,
        )

        for card in (
            self.card_pending,
            self.card_capacity,
            self.card_revenue,
            self.card_alerts,
        ):
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        # Khu vực thanh tiến độ sức chứa các khu vực
        self.zone_card = SectionCard(
            title="Tình trạng sức chứa theo khu vực (1.1.2)",
            subtitle="Theo dõi tỷ lệ lấp đầy các khu đỗ Ô tô & Xe máy",
        )
        self.zone_bars_layout = QHBoxLayout()
        self.zone_bars_layout.setSpacing(14)
        self.zone_card.body_layout.addLayout(self.zone_bars_layout)
        root.addWidget(self.zone_card)

        # Bảng đôi: Yêu cầu chờ duyệt (1.1.1) & Cảnh báo hệ thống (1.1.4)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)

        self.pending_section = SectionCard(
            title="Yêu cầu chờ duyệt gần nhất (1.1.1)",
            subtitle="Hồ sơ đăng ký xe và yêu cầu mua thêm chỗ đỗ ô tô",
        )
        self.tbl_pending = QTableWidget(0, 5)
        self.tbl_pending.setHorizontalHeaderLabels(
            ["Nhóm", "Loại yêu cầu", "Cư dân", "Căn hộ", "Biển số / Nhu cầu"]
        )
        style_table(self.tbl_pending)
        self.pending_section.body_layout.addWidget(self.tbl_pending)
        splitter.addWidget(self.pending_section)

        self.alerts_section = SectionCard(
            title="Cảnh báo hệ thống & Bãi xe (1.1.4)",
            subtitle="Tổng hợp sự cố Socket, Camera AI và vị trí ngừng dùng",
        )
        self.tbl_alerts = QTableWidget(0, 4)
        self.tbl_alerts.setHorizontalHeaderLabels(["Thời gian", "Mức độ", "Nguồn", "Nội dung"])
        style_table(self.tbl_alerts)
        self.alerts_section.body_layout.addWidget(self.tbl_alerts)
        splitter.addWidget(self.alerts_section)

        splitter.setSizes([500, 500])
        root.addWidget(splitter, 1)

    def refresh_data(self) -> None:
        """Gọi API `manager.dashboard_summary` từ Server và cập nhật giao diện."""
        if self.socket_client is None or not self.socket_client.is_connected():
            self.lbl_subtitle.setText("Chưa kết nối tới Server — hiển thị dữ liệu cục bộ.")
            return

        resp = self.socket_client.call(Actions.DASHBOARD_SUMMARY, {})
        if not resp.get("ok"):
            self.lbl_subtitle.setText(f"Lỗi tải Dashboard: {resp.get('error')}")
            return
        data = resp.get("data") or {}
        self.apply_dashboard_data(data)

    def apply_dashboard_data(self, data: Dict[str, Any]) -> None:
        """Đổ dữ liệu tổng hợp từ Server vào 4 KPI Card, thanh khu vực và 2 bảng."""
        generated_at = data.get("generated_at") or ""
        ky_thu = data.get("ky_thu_thang_nam") or ""
        self.lbl_subtitle.setText(f"Kỳ thu {ky_thu} · Cập nhật lúc {generated_at}")

        # 1.1.1 Yêu cầu chờ duyệt
        pending = data.get("pending_requests") or {}
        self.card_pending.set_value(
            str(pending.get("tong_cho_duyet", 0)),
            f"{pending.get('so_yeu_cau_xe', 0)} đăng ký xe · {pending.get('so_yeu_cau_mua_cho', 0)} mua thêm chỗ",
        )
        self._populate_pending_table(pending.get("items") or [])

        # 1.1.2 Xe đang gửi & chỗ trống
        cap = data.get("parking_capacity") or {}
        self.card_capacity.set_value(
            f"{cap.get('tong_xe_dang_gui', 0)} / {cap.get('cho_con_trong', 0)}",
            f"{cap.get('xe_cu_dan_dang_gui', 0)} cư dân · {cap.get('xe_vang_lai_dang_gui', 0)} vãng lai",
        )
        self._populate_zone_bars(cap.get("khu_vuc") or [])

        # 1.1.3 Doanh thu & phí cư dân
        rev = data.get("revenue_summary") or {}
        self.card_revenue.set_value(
            _format_vnd(rev.get("tong_doanh_thu_hien_thi", 0)),
            f"Đã thu cư dân: {_format_vnd(rev.get('da_thu_cu_dan', 0))} · "
            f"Còn nợ: {_format_vnd(rev.get('con_no_cu_dan', 0))} · "
            f"Vãng lai: {_format_vnd(rev.get('doanh_thu_vang_lai_hom_nay', 0))}",
        )

        # 1.1.4 Cảnh báo hệ thống
        alerts = data.get("system_alerts") or {}
        total_alerts = int(alerts.get("tong_canh_bao", 0))
        self.card_alerts.set_value(
            str(total_alerts),
            "Cần kiểm tra xử lý" if total_alerts > 0 else "Hệ thống vận hành ổn định",
        )
        self._populate_alerts_table(alerts.get("items") or [])

    def _populate_zone_bars(self, zones: List[Dict[str, Any]]) -> None:
        while self.zone_bars_layout.count():
            item = self.zone_bars_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not zones:
            lbl_empty = QLabel("Chưa có dữ liệu khu vực từ CSDL.")
            lbl_empty.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 12.5px;")
            self.zone_bars_layout.addWidget(lbl_empty)
            return

        for zone in zones:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)

            total = max(1, int(zone.get("tong_so_cho") or 0))
            used = int(zone.get("so_xe_dang_gui") or 0)
            pct = min(100, int((used / total) * 100))

            lbl = QLabel(f"{zone.get('ten_khu_vuc', zone.get('ma_khu_vuc'))}: {used}/{total} chỗ ({pct}%)")
            lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            fill_color = Colors.ERROR if pct >= 90 else (Colors.WARNING if pct >= 70 else Colors.ROYAL_BLUE)
            bar.setStyleSheet(
                f"QProgressBar {{ background: {Colors.BG_ELEVATED}; border-radius: 5px; border: none; }}"
                f"QProgressBar::chunk {{ background: {fill_color}; border-radius: 5px; }}"
            )
            v.addWidget(lbl)
            v.addWidget(bar)
            self.zone_bars_layout.addWidget(box, 1)

    def _populate_pending_table(self, items: List[Dict[str, Any]]) -> None:
        self.tbl_pending.setRowCount(len(items))
        for row_idx, item in enumerate(items):
            nhom = "Đăng ký xe" if item.get("nhom_yeu_cau") == "dang_ky_xe" else "Mua thêm chỗ"
            self.tbl_pending.setItem(row_idx, 0, QTableWidgetItem(nhom))
            self.tbl_pending.setItem(row_idx, 1, QTableWidgetItem(str(item.get("loai_yeu_cau") or "")))
            self.tbl_pending.setItem(row_idx, 2, QTableWidgetItem(str(item.get("ten_cu_dan") or "")))
            self.tbl_pending.setItem(row_idx, 3, QTableWidgetItem(str(item.get("ma_can_ho") or "")))
            self.tbl_pending.setItem(row_idx, 4, QTableWidgetItem(str(item.get("bien_so_xe") or "")))

    def _populate_alerts_table(self, items: List[Dict[str, Any]]) -> None:
        self.tbl_alerts.setRowCount(len(items))
        for row_idx, item in enumerate(items):
            level = str(item.get("muc_do") or "INFO")
            variant = "danger" if level == "ERROR" else ("warning" if level == "WARN" else "info")
            self.tbl_alerts.setItem(row_idx, 0, QTableWidgetItem(str(item.get("timestamp") or "")))
            self.tbl_alerts.setCellWidget(row_idx, 1, StatusBadge(level, variant))
            self.tbl_alerts.setItem(row_idx, 2, QTableWidgetItem(str(item.get("nguon") or "")))
            self.tbl_alerts.setItem(row_idx, 3, QTableWidgetItem(str(item.get("noi_dung") or "")))
