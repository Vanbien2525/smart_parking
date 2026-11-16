"""Cửa sổ giao diện độc lập cho Quản lý chung cư (phân tách khỏi giao diện Admin)."""

from typing import Dict, Optional

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from client.network.socket_client import SocketClient
from client.ui.manager.dashboard_view import DashboardView
from client.ui.manager.finance_view import FinanceView
from client.ui.manager.hr_view import HRView
from client.ui.manager.infra_pricing_view import InfraPricingView
from client.ui.manager.stats_history_view import (
    ManagerHistoryView,
    ManagerMonitorView,
    ManagerNotifyView,
    ManagerStatsView,
)
from client.ui.manager.vehicle_approval_view import VehicleApprovalView
from client.ui.theme.components import EmptyStateWidget
from client.ui.theme.role_shell import RolePortalWindow

_MANAGER_PLACEHOLDERS = (
    ("1.3_staff_resident", "1.3 Cư dân & Nhân sự", "Quản lý tài khoản cư dân và nhân viên trực cổng", "Thành viên 2 (Hậu)"),
    ("1.4_slots", "1.4 Duyệt xe & Vị trí", "Phê duyệt đăng ký xe, mua thêm chỗ và cấu hình khu vực/ô đỗ", "Thành viên 2 (Hậu) & Thành viên 4 (Long)"),
    ("1.5_pricing", "1.5 Bảng giá gửi xe", "Thiết lập đơn giá tháng và vãng lai cho Ô tô / Xe máy", "Thành viên 2 (Hậu)"),
    ("1.6_monitor", "1.6 Giám sát bãi 2D/3D", "Theo dõi vị trí đang gửi xe và chỗ trống theo thời gian thực", "Thành viên 3 (Minh)"),
    ("1.7_billing", "1.7 Theo dõi thu phí", "Kiểm tra công nợ, hóa đơn tháng và đối soát thanh toán", "Thành viên 2 (Hậu)"),
    ("1.8_stats", "1.8 Thống kê & Doanh thu", "Biểu đồ lượt vào/ra và cơ cấu doanh thu theo kỳ", "Thành viên 3 (Minh)"),
    ("1.9_history", "1.9 Lịch sử vào / ra", "Tra cứu lượt gửi xe cư dân và khách vãng lai", "Thành viên 3 (Minh)"),
    ("1.10_notify", "1.10 Thông báo chung cư", "Soạn và phát thông báo Push tới nhân viên, cư dân", "Thành viên 3 (Minh)"),
)


class ManagerWindow(RolePortalWindow):
    """Cửa sổ làm việc riêng của Quản lý chung cư (chỉ chứa nhóm màn hình 1.x)."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        extra_views: Optional[Dict[str, QWidget]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            window_title="Smart Parking Đào Tiên — Cổng Quản Lý Chung Cư",
            brand_subtitle="CỔNG QUẢN LÝ CHUNG CƯ",
            role_badge_text="Vai trò: Quản lý (quan_ly)",
            parent=parent,
        )
        self.socket_client = socket_client
        self.dashboard_view = DashboardView(socket_client=socket_client, read_only=False)
        self.add_nav_page(
            key="1.1_dashboard",
            nav_label="1.1 Dashboard tổng quan",
            page_title="Dashboard Quản lý chung cư (1.1)",
            page_subtitle="Tổng quan yêu cầu chờ duyệt, sức chứa, doanh thu và cảnh báo",
            widget=self.dashboard_view,
            group_label="VẬN HÀNH & TỔNG QUAN",
        )

        custom_views = dict(extra_views or {})
        self.hr_view = custom_views.get("1.3_staff_resident") or HRView(
            socket_client=socket_client, read_only=False
        )
        self.vehicle_approval_view = VehicleApprovalView(
            socket_client=socket_client, read_only=False
        )
        self.infra_view = InfraPricingView(
            socket_client=socket_client, read_only=False, initial_tab=0
        )
        if "1.4_slots" not in custom_views:
            slots_tabs = QTabWidget()
            slots_tabs.addTab(
                self.vehicle_approval_view,
                "1.4.1–4, 1.4.15–19 Duyệt xe & Mua thêm chỗ",
            )
            slots_tabs.addTab(
                self.infra_view,
                "1.4.5–14 Khu vực & Ô đỗ",
            )
            custom_views["1.4_slots"] = slots_tabs
        self.pricing_view = custom_views.get("1.5_pricing") or InfraPricingView(
            socket_client=socket_client, read_only=False, initial_tab=2
        )
        self.finance_view = custom_views.get("1.7_billing") or FinanceView(
            socket_client=socket_client, read_only=False
        )
        self.monitor_view = custom_views.get("1.6_monitor") or ManagerMonitorView(
            socket_client=socket_client, read_only=False
        )
        self.stats_view = custom_views.get("1.8_stats") or ManagerStatsView(
            socket_client=socket_client, read_only=False
        )
        self.history_view = custom_views.get("1.9_history") or ManagerHistoryView(
            socket_client=socket_client, read_only=False
        )
        self.notify_view = custom_views.get("1.10_notify") or ManagerNotifyView(
            socket_client=socket_client, read_only=False
        )
        custom_views.setdefault("1.3_staff_resident", self.hr_view)
        custom_views.setdefault("1.5_pricing", self.pricing_view)
        custom_views.setdefault("1.6_monitor", self.monitor_view)
        custom_views.setdefault("1.7_billing", self.finance_view)
        custom_views.setdefault("1.8_stats", self.stats_view)
        custom_views.setdefault("1.9_history", self.history_view)
        custom_views.setdefault("1.10_notify", self.notify_view)

        for key, label, desc, owner in _MANAGER_PLACEHOLDERS:
            widget = custom_views.get(key)
            if widget is None:
                widget = self._build_placeholder(label, desc, owner)
            self.add_nav_page(
                key=key,
                nav_label=label,
                page_title=label,
                page_subtitle=desc,
                widget=widget,
                group_label="NGHIỆP VỤ QUẢN LÝ",
            )

    @staticmethod
    def _build_placeholder(title: str, description: str, owner: str) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(
            EmptyStateWidget(
                title=f"Chờ ghép module: {title}",
                description=f"{description}. Chức năng này do {owner} triển khai và sẽ gắn trực tiếp vào đây.",
            ),
            1,
        )
        return host

    def load_initial_data(self) -> None:
        """Nạp dữ liệu Dashboard & nghiệp vụ Quản lý qua TCP Socket."""
        self.dashboard_view.refresh_data()
        for view in (
            self.hr_view,
            self.vehicle_approval_view,
            self.infra_view,
            self.pricing_view,
            self.monitor_view,
            self.finance_view,
            self.stats_view,
            self.history_view,
            self.notify_view,
        ):
            if hasattr(view, "refresh_data"):
                view.refresh_data()
