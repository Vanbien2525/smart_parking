"""Cửa sổ giao diện độc lập cho System Admin Read-Only (tách biệt khỏi Quản lý)."""

from typing import Dict, Optional

from PySide6.QtWidgets import QWidget

from client.network.socket_client import SocketClient
from client.ui.admin.admin_views import (
    AdminAccountsView,
    AdminLogsView,
    AdminReadOnlyWrapper,
    AdminSessionsView,
)
from client.ui.manager.dashboard_view import DashboardView
from client.ui.theme.role_shell import RolePortalWindow


class AdminWindow(RolePortalWindow):
    """Cửa sổ làm việc riêng của System Admin (chỉ chứa nhóm 4.2 – 4.8 Read-Only)."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        extra_read_only_views: Optional[Dict[str, QWidget]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            window_title="Smart Parking Đào Tiên — Cổng System Admin (Chỉ Xem)",
            brand_subtitle="CỔNG SYSTEM ADMIN",
            role_badge_text="Vai trò: Admin Read-Only (admin)",
            parent=parent,
        )
        self.socket_client = socket_client

        self.accounts_view = AdminAccountsView(socket_client=socket_client)
        self.sessions_view = AdminSessionsView(socket_client=socket_client)
        self.logs_view = AdminLogsView(socket_client=socket_client)
        self.dashboard_readonly_view = AdminReadOnlyWrapper(
            title="4.5 Dashboard tổng quan Quản lý (Chế độ Chỉ Xem)",
            child_widget=DashboardView(socket_client=socket_client, read_only=True),
        )

        self.add_nav_page(
            key="4.2_accounts",
            nav_label="4.2 Tài khoản hệ thống",
            page_title="Giám sát Tài khoản Hệ thống (4.2)",
            page_subtitle="Chỉ xem danh sách tài khoản, bộ lọc Quản lý, trạng thái khóa và lịch sử",
            widget=self.accounts_view,
            group_label="GIÁM SÁT HỆ THỐNG",
        )
        self.add_nav_page(
            key="4.3_sessions",
            nav_label="4.3 Phiên kết nối Socket",
            page_title="Giám sát Phiên Kết nối Thực (4.3)",
            page_subtitle="Theo dõi các Client đang kết nối TCP Socket trên RAM Server",
            widget=self.sessions_view,
            group_label="GIÁM SÁT HỆ THỐNG",
        )
        self.add_nav_page(
            key="4.4_logs",
            nav_label="4.4 Nhật ký JSONL",
            page_title="Tra cứu Nhật ký Hệ thống (4.4)",
            page_subtitle="Nhật ký thao tác (audit), đăng nhập (auth) và lỗi hệ thống (error)",
            widget=self.logs_view,
            group_label="GIÁM SÁT HỆ THỐNG",
        )
        self.add_nav_page(
            key="4.5_dashboard_ro",
            nav_label="4.5 Dashboard (Chỉ xem)",
            page_title="Dashboard Quản lý — Chế độ Admin Chỉ Xem (4.5)",
            page_subtitle="Quan sát số liệu vận hành thực tế mà không thể chỉnh sửa dữ liệu",
            widget=self.dashboard_readonly_view,
            group_label="BÁO CÁO READ-ONLY (4.5–4.8)",
        )

        custom_ro = extra_read_only_views or {}
        readonly_sections = (
            ("4.6_infra_ro", "4.6 Hạ tầng & Giá (Chỉ xem)", "Xem khu vực đỗ, vị trí đỗ, xe cư dân và bảng giá"),
            ("4.7_finance_ro", "4.7 Thống kê & Phí (Chỉ xem)", "Xem biểu đồ lượt xe, doanh thu và công nợ cư dân"),
            ("4.8_parking_ro", "4.8 Bãi xe & Vào/Ra (Chỉ xem)", "Xem sơ đồ bãi xe đang gửi và lịch sử vào/ra"),
        )
        for key, label, subtitle in readonly_sections:
            wrapper = AdminReadOnlyWrapper(
                title=label,
                child_widget=custom_ro.get(key),
            )
            self.add_nav_page(
                key=key,
                nav_label=label,
                page_title=label,
                page_subtitle=subtitle,
                widget=wrapper,
                group_label="BÁO CÁO READ-ONLY (4.5–4.8)",
            )

    def load_initial_data(self) -> None:
        """Nạp toàn bộ dữ liệu giám sát Admin qua TCP Socket."""
        self.accounts_view.refresh_data()
        self.sessions_view.refresh_data()
        self.logs_view.refresh_log_type("audit")
        self.logs_view.refresh_log_type("auth")
        self.logs_view.refresh_log_type("error")
        if isinstance(self.dashboard_readonly_view.child_widget, DashboardView):
            self.dashboard_readonly_view.child_widget.refresh_data()
