"""Khung cửa sổ chính MainWindow dùng chung cho cả 4 vai trò (Quản lý, Nhân viên, Cư dân, Admin).

Tuân thủ 100% DESIGN_SYSTEM.md:
- Sidebar tối #0B132B hỗ trợ thu gọn/mở rộng (256px <-> 72px) nạp menu theo vai_tro.
- Topbar mảnh 56px: Tiêu đề màn hình, RoleBadge, trạng thái Socket Online/Ping,
  nút Chuông thông báo kèm badge chưa đọc và nút Đăng xuất.
- EmergencyBanner ghim ngay dưới Topbar + lắng nghe SocketClient.push_received
  (EMERGENCY_ALERT, NORMAL_NOTIFICATION, SESSION_REVOKED, PARKING_STATE_CHANGED).
"""

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.admin.admin_views import (
    AdminAccountsView,
    AdminLogsView,
    AdminReadOnlyWrapper,
    AdminSessionsView,
)
from client.ui.auth.profile_view import ProfileView
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
from client.ui.resident.bills_payment_view import BillsPaymentView
from client.ui.resident.extra_slot_view import ExtraSlotView
from client.ui.resident.resident_notify_view import ResidentNotifyView
from client.ui.resident.vehicle_lookup_view import VehicleLookupView
from client.ui.resident.vehicle_view import ResidentVehicleView
from client.ui.staff.staff_monitor_notify_view import (
    StaffMonitorView,
    StaffNotifyInboxView,
)
from client.ui.theme.components import (
    EmergencyBanner,
    EmergencyDialog,
    EmptyStateWidget,
    RoleBadge,
    ToastNotification,
    set_button_variant,
)
from client.ui.theme.role_shell import apply_design_stylesheet
from client.ui.theme.tokens import Colors, Layout
from shared.constants import Roles
from shared.protocol import Actions, PushEvents


class MainWindow(QMainWindow):
    """Cửa sổ làm việc trung tâm điều phối 4 vai trò và Push Notification thời gian thực."""

    logout_requested = Signal()
    session_revoked = Signal(str)
    parking_state_pushed = Signal(dict)
    notification_pushed = Signal(str, dict)

    def __init__(
        self,
        session_info: Optional[Dict[str, Any]] = None,
        socket_client: Optional[SocketClient] = None,
        extra_views: Optional[Dict[str, QWidget]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.session_info: Dict[str, Any] = dict(session_info or {})
        self.socket_client = socket_client
        self._extra_views: Dict[str, QWidget] = dict(extra_views or {})

        self.vai_tro: str = str(self.session_info.get("vai_tro") or Roles.CU_DAN.value)
        self.ho_ten: str = str(
            self.session_info.get("ho_ten")
            or self.session_info.get("ten_dang_nhap")
            or "Người dùng"
        )

        self._sidebar_collapsed = False
        self._unread_count = int(self.session_info.get("unread_notifications") or 0)
        self._nav_buttons: Dict[str, QPushButton] = {}
        self._nav_full_labels: Dict[str, str] = {}
        self._nav_short_labels: Dict[str, str] = {}
        self._group_labels: List[QLabel] = []
        self._pages: Dict[str, QWidget] = {}
        self._page_titles: Dict[str, Tuple[str, str]] = {}
        self._current_group: Optional[str] = None
        self._current_key: Optional[str] = None
        self._handling_revoked = False

        self.setWindowTitle(f"Smart Parking Chung cư Đào Tiên — {self.ho_ten}")
        self.resize(1380, 860)
        apply_design_stylesheet(self)

        self._build_shell()
        self._populate_role_pages()
        self._bind_socket_signals()
        self._sync_connection_indicator()
        self._refresh_unread_badge()

    # --------------------------------------------------------------------------
    # DỰNG KHUNG SHELL (SIDEBAR + TOPBAR + EMERGENCY BANNER + STACKED PAGES)
    # --------------------------------------------------------------------------
    def _build_shell(self) -> None:
        root_host = QWidget(self)
        root_host.setObjectName("AppWorkspace")
        root_layout = QHBoxLayout(root_host)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Sidebar Midnight Navy (#0B132B)
        self.sidebar = QWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(Layout.SIDEBAR_WIDTH)
        sidebar_lay = QVBoxLayout(self.sidebar)
        sidebar_lay.setContentsMargins(0, 14, 0, 14)
        sidebar_lay.setSpacing(6)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(14, 2, 12, 8)
        brand_col = QVBoxLayout()
        brand_col.setSpacing(2)
        self.lbl_brand = QLabel("SMART PARKING")
        self.lbl_brand.setObjectName("SidebarBrandTitle")
        self.lbl_sub = QLabel("CHUNG CƯ ĐÀO TIÊN")
        self.lbl_sub.setObjectName("SidebarBrandSubtitle")
        brand_col.addWidget(self.lbl_brand)
        brand_col.addWidget(self.lbl_sub)
        brand_row.addLayout(brand_col, 1)

        self.btn_collapse = QPushButton("«")
        self.btn_collapse.setObjectName("SidebarCollapseBtn")
        self.btn_collapse.setToolTip("Thu gọn / Mở rộng thanh điều hướng")
        self.btn_collapse.setFixedSize(32, 30)
        self.btn_collapse.clicked.connect(self.toggle_sidebar)
        brand_row.addWidget(self.btn_collapse)
        sidebar_lay.addLayout(brand_row)

        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        nav_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        nav_host = QWidget()
        self._nav_container = QVBoxLayout(nav_host)
        self._nav_container.setContentsMargins(0, 0, 0, 0)
        self._nav_container.setSpacing(2)
        self._nav_container.addStretch(1)
        nav_scroll.setWidget(nav_host)
        sidebar_lay.addWidget(nav_scroll, 1)

        self.lbl_sidebar_user = QLabel(f"Tài khoản: {self.ho_ten}")
        self.lbl_sidebar_user.setObjectName("SidebarBrandSubtitle")
        self.lbl_sidebar_user.setContentsMargins(16, 6, 16, 0)
        self.lbl_sidebar_user.setWordWrap(True)
        sidebar_lay.addWidget(self.lbl_sidebar_user)

        root_layout.addWidget(self.sidebar)

        # 2. Cột phải: Topbar + EmergencyBanner + StackedWidget
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)

        self.topbar = QWidget()
        self.topbar.setObjectName("Topbar")
        self.topbar.setFixedHeight(Layout.TOPBAR_HEIGHT)
        topbar_lay = QHBoxLayout(self.topbar)
        topbar_lay.setContentsMargins(20, 6, 20, 6)
        topbar_lay.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.lbl_topbar_title = QLabel("")
        self.lbl_topbar_title.setObjectName("TopbarTitle")
        self.lbl_topbar_subtitle = QLabel("")
        self.lbl_topbar_subtitle.setObjectName("TopbarSubtitle")
        title_col.addWidget(self.lbl_topbar_title)
        title_col.addWidget(self.lbl_topbar_subtitle)
        topbar_lay.addLayout(title_col, 1)

        self.role_badge = RoleBadge(self.vai_tro)
        topbar_lay.addWidget(self.role_badge)

        self.lbl_socket_dot = QLabel("●")
        self.lbl_socket_dot.setStyleSheet(f"color: {Colors.WARNING}; font-size: 13px;")
        self.lbl_socket_status = QLabel("Chưa kết nối")
        self.lbl_socket_status.setProperty("class", "MutedText")
        topbar_lay.addWidget(self.lbl_socket_dot)
        topbar_lay.addWidget(self.lbl_socket_status)

        self.btn_bell = QPushButton("Thông báo (0)")
        set_button_variant(self.btn_bell, "default", "sm")
        self.btn_bell.clicked.connect(self._open_notifications_page)
        topbar_lay.addWidget(self.btn_bell)

        self.btn_logout = QPushButton("Đăng xuất")
        set_button_variant(self.btn_logout, "ghost", "sm")
        self.btn_logout.clicked.connect(self._on_logout_clicked)
        topbar_lay.addWidget(self.btn_logout)

        right_col.addWidget(self.topbar)

        self.emergency_banner = EmergencyBanner(self)
        self.emergency_banner.acknowledged.connect(self._ack_emergency_by_id)
        right_col.addWidget(self.emergency_banner)

        self.stack = QStackedWidget()
        right_col.addWidget(self.stack, 1)

        right_host = QWidget()
        right_host.setLayout(right_col)
        root_layout.addWidget(right_host, 1)
        self.setCentralWidget(root_host)

    def toggle_sidebar(self) -> None:
        """Thu gọn (72px) hoặc mở rộng (256px) thanh Sidebar."""
        self._sidebar_collapsed = not self._sidebar_collapsed
        width = Layout.SIDEBAR_COLLAPSED_WIDTH if self._sidebar_collapsed else Layout.SIDEBAR_WIDTH
        self.sidebar.setFixedWidth(width)
        self.lbl_brand.setVisible(not self._sidebar_collapsed)
        self.lbl_sub.setVisible(not self._sidebar_collapsed)
        self.lbl_sidebar_user.setVisible(not self._sidebar_collapsed)
        self.btn_collapse.setText("»" if self._sidebar_collapsed else "«")

        for grp in self._group_labels:
            grp.setVisible(not self._sidebar_collapsed)

        for key, btn in self._nav_buttons.items():
            if self._sidebar_collapsed:
                btn.setText(self._nav_short_labels.get(key, key[:3]))
            else:
                btn.setText(self._nav_full_labels.get(key, key))

    def add_nav_page(
        self,
        key: str,
        nav_label: str,
        page_title: str,
        page_subtitle: str,
        widget: QWidget,
        group_label: Optional[str] = None,
    ) -> None:
        """Đăng ký một trang chức năng vào Sidebar và Stack."""
        insert_idx = max(0, self._nav_container.count() - 1)
        if group_label and group_label != self._current_group:
            self._current_group = group_label
            lbl_group = QLabel(group_label)
            lbl_group.setObjectName("SidebarGroupLabel")
            lbl_group.setVisible(not self._sidebar_collapsed)
            self._nav_container.insertWidget(insert_idx, lbl_group)
            self._group_labels.append(lbl_group)
            insert_idx += 1

        short_code = nav_label.split(" ", 1)[0] if " " in nav_label else nav_label[:3]
        btn = QPushButton(short_code if self._sidebar_collapsed else nav_label)
        btn.setToolTip(nav_label)
        btn.setProperty("class", "SidebarNavBtn")
        btn.setProperty("active", "false")
        btn.clicked.connect(lambda _checked=False, k=key: self.select_page(k))
        self._nav_container.insertWidget(insert_idx, btn)

        self._nav_buttons[key] = btn
        self._nav_full_labels[key] = nav_label
        self._nav_short_labels[key] = short_code
        self._pages[key] = widget
        self._page_titles[key] = (page_title, page_subtitle)
        self.stack.addWidget(widget)

        if len(self._pages) == 1:
            self.select_page(key)

    def select_page(self, key: str) -> None:
        """Chuyển màn hình hiển thị và cập nhật tiêu đề Topbar."""
        widget = self._pages.get(key)
        if widget is None:
            return
        self._current_key = key
        self.stack.setCurrentWidget(widget)
        title, subtitle = self._page_titles.get(key, ("", ""))
        self.lbl_topbar_title.setText(title)
        self.lbl_topbar_subtitle.setText(subtitle)

        for btn_key, btn in self._nav_buttons.items():
            btn.setProperty("active", "true" if btn_key == key else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def get_page(self, key: str) -> Optional[QWidget]:
        return self._pages.get(key)

    def page_keys(self) -> List[str]:
        return list(self._pages.keys())

    # --------------------------------------------------------------------------
    # NẠP MENU & MÀN HÌNH THEO TỪNG VAI TRÒ
    # --------------------------------------------------------------------------
    def _populate_role_pages(self) -> None:
        if self.vai_tro == Roles.QUAN_LY.value:
            self._populate_manager_pages()
        elif self.vai_tro == Roles.NHAN_VIEN.value:
            self._populate_staff_pages()
        elif self.vai_tro == Roles.ADMIN.value:
            self._populate_admin_pages()
        else:
            self._populate_resident_pages()

    def _make_profile_view(self) -> ProfileView:
        view = ProfileView(socket_client=self.socket_client, vai_tro=self.vai_tro)
        view.logout_requested.connect(self._on_logout_clicked)
        return view

    @staticmethod
    def _build_placeholder(title: str, description: str, owner: str) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(
            EmptyStateWidget(
                title=f"Chờ ghép module: {title}",
                description=f"{description}. Phụ trách: {owner}.",
            ),
            1,
        )
        return host

    def _populate_manager_pages(self) -> None:
        ex = self._extra_views
        finance_ro = ex.get("4.7_finance_ro")
        if finance_ro is None:
            fin_tabs = QTabWidget()
            fin_tabs.addTab(ManagerStatsView(socket_client=self.socket_client, read_only=True), "4.7.1–4.7.3 Thống kê & Doanh thu")
            fin_tabs.addTab(FinanceView(socket_client=self.socket_client, read_only=True), "4.7.4 Thu phí & Công nợ")
            finance_ro = fin_tabs

        parking_ro = ex.get("4.8_parking_ro")
        if parking_ro is None:
            park_tabs = QTabWidget()
            park_tabs.addTab(ManagerMonitorView(socket_client=self.socket_client, read_only=True), "4.8.1–4.8.2 Giám sát bãi 2D/3D")
            park_tabs.addTab(ManagerHistoryView(socket_client=self.socket_client, read_only=True), "4.8.3 Lịch sử xe vào/ra")
            parking_ro = park_tabs

        slots_manager_widget = ex.get("1.4_slots")
        if slots_manager_widget is None:
            slots_tabs = QTabWidget()
            slots_tabs.addTab(
                VehicleApprovalView(socket_client=self.socket_client, read_only=False),
                "1.4.1–4, 1.4.15–19 Duyệt xe & Mua thêm chỗ",
            )
            slots_tabs.addTab(
                InfraPricingView(socket_client=self.socket_client, read_only=False, initial_tab=0),
                "1.4.5–14 Khu vực & Ô đỗ",
            )
            slots_manager_widget = slots_tabs

        specs = [
            ("1.1_dashboard", "1.1 Dashboard tổng quan", "Dashboard Quản lý chung cư (1.1)",
             "Tổng quan yêu cầu chờ duyệt, sức chứa, doanh thu và cảnh báo",
             ex.get("1.1_dashboard") or DashboardView(socket_client=self.socket_client, read_only=False),
             "VẬN HÀNH & TỔNG QUAN"),
            ("1.2_profile", "1.2 Hồ sơ & Mật khẩu", "Tài khoản Quản lý (1.2)",
             "Cập nhật thông tin cá nhân và đổi mật khẩu bảo mật",
             ex.get("1.2_profile") or self._make_profile_view(),
             "VẬN HÀNH & TỔNG QUAN"),
            ("1.3_staff_resident", "1.3 Cư dân & Nhân sự", "Quản lý Cư dân & Nhân sự (1.3)",
             "Tạo, tìm kiếm, chỉnh sửa và khóa/mở khóa tài khoản",
             ex.get("1.3_staff_resident") or HRView(socket_client=self.socket_client, read_only=False),
             "NGHIỆP VỤ QUẢN LÝ"),
            ("1.4_slots", "1.4 Duyệt xe & Vị trí", "Duyệt xe, Khu vực & Vị trí đỗ (1.4)",
             "Duyệt đăng ký xe, mua thêm chỗ, thu hồi chỗ và quản lý khu vực/ô đỗ",
             slots_manager_widget,
             "NGHIỆP VỤ QUẢN LÝ"),
            ("1.5_pricing", "1.5 Bảng giá gửi xe", "Cấu hình Bảng giá gửi xe (1.5)",
             "Thiết lập đơn giá tháng và vãng lai cho Ô tô / Xe máy",
             ex.get("1.5_pricing") or InfraPricingView(socket_client=self.socket_client, read_only=False, initial_tab=2),
             "NGHIỆP VỤ QUẢN LÝ"),
            ("1.6_monitor", "1.6 Giám sát bãi 2D/3D", "Giám sát Bãi xe 2D & Sa bàn 3D (1.6)",
             "Theo dõi xe đang gửi, chỗ trống và tương tác sơ đồ 2D/3D",
             ex.get("1.6_monitor") or ManagerMonitorView(socket_client=self.socket_client, read_only=False),
             "GIÁM SÁT & THỐNG KÊ"),
            ("1.7_billing", "1.7 Theo dõi thu phí", "Theo dõi Thu phí & Công nợ (1.7)",
             "Phát hành hóa đơn tháng, đối soát phải thu và đã thu",
             ex.get("1.7_billing") or FinanceView(socket_client=self.socket_client, read_only=False),
             "GIÁM SÁT & THỐNG KÊ"),
            ("1.8_stats", "1.8 Thống kê & Doanh thu", "Thống kê Biểu đồ & Doanh thu (1.8)",
             "Phân tích lượt xe vào/ra, cư dân vs vãng lai và doanh thu",
             ex.get("1.8_stats") or ManagerStatsView(socket_client=self.socket_client, read_only=False),
             "GIÁM SÁT & THỐNG KÊ"),
            ("1.9_history", "1.9 Lịch sử vào / ra", "Tra cứu Lịch sử Xe Vào / Ra (1.9)",
             "Đối chiếu ảnh cổng vào/ra và lịch sử lượt gửi",
             ex.get("1.9_history") or ManagerHistoryView(socket_client=self.socket_client, read_only=False),
             "GIÁM SÁT & THỐNG KÊ"),
            ("1.10_notify", "1.10 Thông báo chung cư", "Phát Thông báo & Cảnh báo Khẩn (1.10)",
             "Soạn nháp, phát thông báo Push real-time và thử gửi lại",
             ex.get("1.10_notify") or ManagerNotifyView(socket_client=self.socket_client, read_only=False),
             "THÔNG BÁO KHẨN CẤP"),
        ]
        for key, nav, title, sub, widget, grp in specs:
            self.add_nav_page(key, nav, title, sub, widget, group_label=grp)

    def _populate_staff_pages(self) -> None:
        ex = self._extra_views
        staff_notify_view = ex.get("2.7_notify") or StaffNotifyInboxView(socket_client=self.socket_client)
        if hasattr(staff_notify_view, "unread_count_changed"):
            staff_notify_view.unread_count_changed.connect(self.set_unread_count)
        finance_ro = ex.get("4.7_finance_ro")
        if finance_ro is None:
            fin_tabs = QTabWidget()
            fin_tabs.addTab(ManagerStatsView(socket_client=self.socket_client, read_only=True), "4.7.1–4.7.3 Thống kê & Doanh thu")
            fin_tabs.addTab(FinanceView(socket_client=self.socket_client, read_only=True), "4.7.4 Thu phí & Công nợ")
            finance_ro = fin_tabs

        parking_ro = ex.get("4.8_parking_ro")
        if parking_ro is None:
            park_tabs = QTabWidget()
            park_tabs.addTab(ManagerMonitorView(socket_client=self.socket_client, read_only=True), "4.8.1–4.8.2 Giám sát bãi 2D/3D")
            park_tabs.addTab(ManagerHistoryView(socket_client=self.socket_client, read_only=True), "4.8.3 Lịch sử xe vào/ra")
            parking_ro = park_tabs

        specs = [
            ("2.1_profile", "2.1 Hồ sơ & Mật khẩu", "Tài khoản Nhân viên (2.1)",
             "Xem thông tin nhân viên trực bãi và đổi mật khẩu",
             ex.get("2.1_profile") or self._make_profile_view(),
             "TÀI KHOẢN & CA TRỰC"),
            ("2.2_gate_in", "2.2 Kiểm soát Xe vào", "Cổng Vào Ticketless & OCR (2.2)",
             "Quét biển số tự động và ghi nhận xe vào bãi",
             ex.get("2.2_gate_in") or self._build_placeholder("2.2 Kiểm soát Xe vào", "Nhận diện biển số AI và cho xe vào bãi", "Thành viên 5 (Ny)"),
             "KIỂM SOÁT CỔNG"),
            ("2.3_gate_out", "2.3 Kiểm soát Xe ra", "Cổng Ra & Tính phí Vãng lai (2.3)",
             "Đối chiếu biển số ra và thu phí khách vãng lai",
             ex.get("2.3_gate_out") or self._build_placeholder("2.3 Kiểm soát Xe ra", "Tính phí và xác nhận xe ra khỏi bãi", "Thành viên 5 (Ny)"),
             "KIỂM SOÁT CỔNG"),
            ("2.4_shift_revenue", "2.4 Tiền thu ca trực", "Đối chiếu Tiền thu Ca trực (2.4)",
             "Tổng hợp lượt xe và doanh thu tiền mặt trong ca",
             ex.get("2.4_shift_revenue") or self._build_placeholder("2.4 Tiền thu ca trực", "Thống kê tiền mặt thu trong ca trực", "Thành viên 5 (Ny)"),
             "KIỂM SOÁT CỔNG"),
            ("2.5_lookup", "2.5 Tra cứu xe bãi", "Tra cứu Phương tiện (2.5)",
             "Tìm xe đang gửi theo biển số và vị trí đỗ",
             ex.get("2.5_lookup") or self._build_placeholder("2.5 Tra cứu xe bãi", "Tìm kiếm nhanh biển số và vị trí xe", "Thành viên 5 (Ny)"),
             "GIÁM SÁT & THÔNG BÁO"),
            ("2.6_monitor", "2.6 Giám sát bãi 2D/3D", "Giám sát Bãi xe 2D/3D (2.6)",
             "Theo dõi chỗ trống và trạng thái các ô đỗ theo thời gian thực",
             ex.get("2.6_monitor") or StaffMonitorView(socket_client=self.socket_client),
             "GIÁM SÁT & THÔNG BÁO"),
            ("2.7_notify", "2.7 Thông báo khẩn cấp", "Hộp thư Thông báo Nhân viên (2.7)",
             "Nhận cảnh báo khẩn cấp, đọc chi tiết và xác nhận tiếp nhận",
             staff_notify_view,
             "GIÁM SÁT & THÔNG BÁO"),
        ]
        for key, nav, title, sub, widget, grp in specs:
            self.add_nav_page(key, nav, title, sub, widget, group_label=grp)

    def _populate_resident_pages(self) -> None:
        ex = self._extra_views
        res_notify_view = ex.get("3.6_notify") or ResidentNotifyView(socket_client=self.socket_client)
        if hasattr(res_notify_view, "unread_count_changed"):
            res_notify_view.unread_count_changed.connect(self.set_unread_count)

        specs = [
            ("3.1_profile", "3.1 Hồ sơ & Mật khẩu", "Hồ sơ Cư dân & Căn hộ (3.1)",
             "Quản lý thông tin liên hệ và bảo mật tài khoản",
             ex.get("3.1_profile") or self._make_profile_view(),
             "CĂN HỘ & TÀI KHOẢN"),
            ("3.2_my_vehicles", "3.2 Xe của tôi", "Đăng ký & Quản lý Phương tiện (3.2)",
             "Đăng ký xe mới kèm ảnh cà-vẹt và theo dõi duyệt",
             ex.get("3.2_my_vehicles") or ResidentVehicleView(socket_client=self.socket_client),
             "PHƯƠNG TIỆN & CHỖ ĐỖ"),
            ("3.3_buy_slots", "3.3 Mua thêm chỗ ô tô", "Yêu cầu Mua thêm Chỗ đỗ Ô tô (3.3)",
             "Gửi yêu cầu mua thêm chỗ đỗ và theo dõi chỗ được cấp",
             ex.get("3.3_buy_slots") or ExtraSlotView(socket_client=self.socket_client),
             "PHƯƠNG TIỆN & CHỖ ĐỖ"),
            ("3.4_track_vehicle", "3.4 Tra cứu & Định vị xe", "Tra cứu & Định vị Xe đang gửi (3.4)",
             "Xem xe đang ở trong bãi, vị trí đỗ 2D/3D và lịch sử gửi",
             ex.get("3.4_track_vehicle") or VehicleLookupView(socket_client=self.socket_client),
             "PHƯƠNG TIỆN & CHỖ ĐỖ"),
            ("3.5_bills", "3.5 Hóa đơn & Thanh toán", "Hóa đơn Tháng & Thanh toán QR (3.5)",
             "Xem hóa đơn hàng tháng, thanh toán QR hoặc xem biên nhận",
             ex.get("3.5_bills") or BillsPaymentView(socket_client=self.socket_client),
             "TÀI CHÍNH & THÔNG BÁO"),
            ("3.6_notify", "3.6 Hộp thư thông báo", "Thông báo từ Ban Quản Lý (3.6)",
             "Nhận cảnh báo khẩn cấp, kết quả duyệt hồ sơ và đánh dấu đã đọc",
             res_notify_view,
             "TÀI CHÍNH & THÔNG BÁO"),
        ]
        for key, nav, title, sub, widget, grp in specs:
            self.add_nav_page(key, nav, title, sub, widget, group_label=grp)

    def _populate_admin_pages(self) -> None:
        ex = self._extra_views

        infra_ro = ex.get("4.6_infra_ro")
        if infra_ro is None:
            tabs = QTabWidget()
            tabs.addTab(HRView(socket_client=self.socket_client, read_only=True), "4.6.1 Nhân sự & Cư dân")
            tabs.addTab(VehicleApprovalView(socket_client=self.socket_client, read_only=True), "4.6.2 Phương tiện & Chỗ đỗ")
            tabs.addTab(InfraPricingView(socket_client=self.socket_client, read_only=True), "4.6.3–4.6.4 Khu vực, Vị trí & Giá")
            infra_ro = tabs

        finance_ro = ex.get("4.7_finance_ro")
        if finance_ro is None:
            fin_tabs = QTabWidget()
            fin_tabs.addTab(ManagerStatsView(socket_client=self.socket_client, read_only=True), "4.7.1–4.7.3 Thống kê & Doanh thu")
            fin_tabs.addTab(FinanceView(socket_client=self.socket_client, read_only=True), "4.7.4 Thu phí & Công nợ")
            finance_ro = fin_tabs

        parking_ro = ex.get("4.8_parking_ro")
        if parking_ro is None:
            park_tabs = QTabWidget()
            park_tabs.addTab(ManagerMonitorView(socket_client=self.socket_client, read_only=True), "4.8.1–4.8.2 Giám sát bãi 2D/3D")
            park_tabs.addTab(ManagerHistoryView(socket_client=self.socket_client, read_only=True), "4.8.3 Lịch sử xe vào/ra")
            parking_ro = park_tabs

        specs = [
            ("4.1_profile", "4.1 Hồ sơ Admin", "Hồ sơ Quản trị Hệ thống (4.1)",
             "Xem hồ sơ cá nhân và đổi mật khẩu Admin",
             ex.get("4.1_profile") or self._make_profile_view(),
             "GIÁM SÁT HỆ THỐNG"),
            ("4.2_accounts", "4.2 Tài khoản hệ thống", "Giám sát Tài khoản Hệ thống (4.2)",
             "Chỉ xem danh sách tài khoản, trạng thái khóa và lịch sử",
             ex.get("4.2_accounts") or AdminAccountsView(socket_client=self.socket_client),
             "GIÁM SÁT HỆ THỐNG"),
            ("4.3_sessions", "4.3 Phiên kết nối Socket", "Giám sát Phiên Kết nối Thực (4.3)",
             "Theo dõi các Client đang kết nối TCP Socket trên RAM Server",
             ex.get("4.3_sessions") or AdminSessionsView(socket_client=self.socket_client),
             "GIÁM SÁT HỆ THỐNG"),
            ("4.4_logs", "4.4 Nhật ký JSONL", "Tra cứu Nhật ký Hệ thống (4.4)",
             "Nhật ký thao tác (audit), đăng nhập (auth) và lỗi hệ thống (error)",
             ex.get("4.4_logs") or AdminLogsView(socket_client=self.socket_client),
             "GIÁM SÁT HỆ THỐNG"),
            ("4.5_dashboard_ro", "4.5 Dashboard (Chỉ xem)", "Dashboard Quản lý — Chỉ Xem (4.5)",
             "Quan sát số liệu vận hành thực tế ở chế độ chỉ đọc",
             AdminReadOnlyWrapper("4.5 Dashboard tổng quan (Chỉ xem)", ex.get("4.5_dashboard_ro") or DashboardView(socket_client=self.socket_client, read_only=True)),
             "BÁO CÁO READ-ONLY (4.5–4.8)"),
            ("4.6_infra_ro", "4.6 Hạ tầng & Giá (Chỉ xem)", "Hạ tầng & Bảng giá — Chỉ Xem (4.6)",
             "Xem khu vực, vị trí đỗ, nhân sự và bảng giá",
             AdminReadOnlyWrapper("4.6 Hạ tầng & Giá (Chỉ xem)", infra_ro),
             "BÁO CÁO READ-ONLY (4.5–4.8)"),
            ("4.7_finance_ro", "4.7 Thống kê & Phí (Chỉ xem)", "Thống kê & Thu phí — Chỉ Xem (4.7)",
             "Xem biểu đồ lượt xe, doanh thu và công nợ cư dân",
             AdminReadOnlyWrapper("4.7 Thống kê & Phí (Chỉ xem)", finance_ro),
             "BÁO CÁO READ-ONLY (4.5–4.8)"),
            ("4.8_parking_ro", "4.8 Bãi xe & Vào/Ra (Chỉ xem)", "Bãi xe & Lịch sử Vào/Ra — Chỉ Xem (4.8)",
             "Xem sơ đồ bãi xe 2D/3D và lịch sử xe vào/ra",
             AdminReadOnlyWrapper("4.8 Bãi xe & Vào/Ra (Chỉ xem)", parking_ro),
             "BÁO CÁO READ-ONLY (4.5–4.8)"),
        ]
        for key, nav, title, sub, widget, grp in specs:
            self.add_nav_page(key, nav, title, sub, widget, group_label=grp)

    # --------------------------------------------------------------------------
    # KẾT NỐI SOCKET & XỬ LÝ SERVER PUSH REAL-TIME
    # --------------------------------------------------------------------------
    def _bind_socket_signals(self) -> None:
        if self.socket_client is None:
            return
        self.socket_client.connection_state_changed.connect(self._on_connection_changed)
        self.socket_client.latency_updated.connect(self._on_latency_updated)
        self.socket_client.push_received.connect(self._on_push_received)

    def _sync_connection_indicator(self) -> None:
        if self.socket_client is not None and self.socket_client.is_connected():
            latency = self.socket_client.last_latency_ms()
            ping_txt = f"Online · {latency}ms" if latency is not None else "Online"
            self.lbl_socket_dot.setStyleSheet(f"color: {Colors.SUCCESS}; font-size: 13px;")
            self.lbl_socket_status.setText(ping_txt)
        else:
            self.lbl_socket_dot.setStyleSheet(f"color: {Colors.ERROR}; font-size: 13px;")
            self.lbl_socket_status.setText("Mất kết nối")

    def _on_connection_changed(self, connected: bool, message: str) -> None:
        if connected:
            self.lbl_socket_dot.setStyleSheet(f"color: {Colors.SUCCESS}; font-size: 13px;")
            self.lbl_socket_status.setText("Online")
        else:
            self.lbl_socket_dot.setStyleSheet(f"color: {Colors.ERROR}; font-size: 13px;")
            self.lbl_socket_status.setText(message or "Mất kết nối")

    def _on_latency_updated(self, latency_ms: int) -> None:
        if self.socket_client is not None and self.socket_client.is_connected():
            self.lbl_socket_dot.setStyleSheet(f"color: {Colors.SUCCESS}; font-size: 13px;")
            self.lbl_socket_status.setText(f"Online · {latency_ms}ms")

    def set_unread_count(self, count: int) -> None:
        self._unread_count = max(0, int(count))
        self._refresh_unread_badge()

    def _refresh_unread_badge(self) -> None:
        self.btn_bell.setText(f"Thông báo ({self._unread_count})")
        if self._unread_count > 0:
            set_button_variant(self.btn_bell, "danger", "sm")
        else:
            set_button_variant(self.btn_bell, "default", "sm")

    def _open_notifications_page(self) -> None:
        target_map = {
            Roles.QUAN_LY.value: "1.10_notify",
            Roles.NHAN_VIEN.value: "2.7_notify",
            Roles.CU_DAN.value: "3.6_notify",
            Roles.ADMIN.value: "4.4_logs",
        }
        key = target_map.get(self.vai_tro)
        if key and key in self._pages:
            self.select_page(key)
            page = self._pages[key]
            if hasattr(page, "refresh_data"):
                page.refresh_data()

    def _on_push_received(self, event: str, data: Dict[str, Any]) -> None:
        self.notification_pushed.emit(event, data)
        if event == PushEvents.EMERGENCY_ALERT:
            notify_id = int(data.get("ma_thong_bao") or 0)
            title = str(data.get("tieu_de") or "Cảnh báo khẩn cấp")
            content = str(data.get("noi_dung") or "")
            sender = str(data.get("ten_nguoi_gui") or "Ban Quản Lý Chung Cư Đào Tiên")
            sent_at = str(data.get("thoi_gian_gui") or "")

            self.set_unread_count(self._unread_count + 1)
            self.emergency_banner.show_alert(notify_id, title, content)

            # Tự động mở hộp thoại khẩn cấp và gửi ACK khi người dùng xác nhận
            dlg = EmergencyDialog(
                title=title,
                content=content,
                sender_name=sender,
                sent_at=sent_at,
                parent=self,
            )
            if dlg.exec():
                self._ack_emergency_by_id(notify_id)
                self.emergency_banner.setVisible(False)
            self._refresh_notify_views()

        elif event == PushEvents.NORMAL_NOTIFICATION:
            title = str(data.get("tieu_de") or "Thông báo mới")
            content = str(data.get("noi_dung") or "")
            self.set_unread_count(self._unread_count + 1)
            ToastNotification.show_toast(self, f"{title}: {content}", variant="info", duration_ms=4500)
            self._refresh_notify_views()

        elif event == PushEvents.PARKING_STATE_CHANGED:
            self.parking_state_pushed.emit(data)
            for key in ("1.1_dashboard", "1.6_monitor", "2.6_monitor", "3.4_track_vehicle", "4.5_dashboard_ro", "4.8_parking_ro"):
                page = self._pages.get(key)
                if page is not None and hasattr(page, "refresh_data"):
                    try:
                        page.refresh_data()
                    except Exception:
                        pass

        elif event == PushEvents.SESSION_REVOKED:
            if self._handling_revoked:
                return
            self._handling_revoked = True
            reason = str(data.get("reason") or "Phiên đăng nhập đã bị thu hồi từ máy chủ.")
            QMessageBox.warning(self, "Phiên làm việc bị thu hồi", reason)
            self.session_revoked.emit(reason)

    def _ack_emergency_by_id(self, notify_id: int) -> None:
        if notify_id <= 0 or self.socket_client is None or not self.socket_client.is_connected():
            return
        try:
            self.socket_client.call(Actions.NOTIFY_ACK_EMERGENCY, {"ma_thong_bao": notify_id}, timeout=5.0)
            self._refresh_notify_views()
        except Exception:
            pass

    def _refresh_notify_views(self) -> None:
        for key in ("1.10_notify", "2.7_notify", "3.6_notify"):
            page = self._pages.get(key)
            if page is not None and hasattr(page, "refresh_data"):
                try:
                    page.refresh_data()
                except Exception:
                    pass

    def _on_logout_clicked(self) -> None:
        if self.socket_client is not None and self.socket_client.is_connected():
            try:
                self.socket_client.call(Actions.AUTH_LOGOUT, {}, timeout=4.0)
            except Exception:
                pass
        self.logout_requested.emit()

    def load_initial_data(self) -> None:
        """Nạp dữ liệu ban đầu cho trang đầu tiên và các module con hỗ trợ refresh_data."""
        for page in self._pages.values():
            target = page.child_widget if isinstance(page, AdminReadOnlyWrapper) else page
            if target is None:
                continue
            if hasattr(target, "refresh_data"):
                try:
                    target.refresh_data()
                except Exception:
                    pass
            elif isinstance(target, QTabWidget):
                for idx in range(target.count()):
                    sub = target.widget(idx)
                    if sub is not None and hasattr(sub, "refresh_data"):
                        try:
                            sub.refresh_data()
                        except Exception:
                            pass
