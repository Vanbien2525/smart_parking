"""Cụm giao diện Admin Read-Only (4.2 – 4.8) gắn ReadOnlyBanner chuẩn Design DNA."""

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    EmptyStateWidget,
    ReadOnlyBanner,
    SectionCard,
    StatusBadge,
    set_button_variant,
    style_table,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.protocol import Actions

_MUTATION_KEYWORDS = (
    "thêm",
    "tạo",
    "sửa",
    "cập nhật",
    "xóa",
    "duyệt",
    "từ chối",
    "thu hồi",
    "phát hành",
    "khóa",
    "mở khóa",
    "ngừng",
    "kích hoạt",
    "gửi",
    "chốt",
    "lưu",
    "thanh toán",
)


def _pretty_json(val: Any) -> str:
    if val is None or val == "":
        return "-"
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


# ==============================================================================
# 4.2.1 - 4.2.5: ADMIN QUẢN LÝ TÀI KHOẢN HỆ THỐNG (CHỈ XEM)
# ==============================================================================
class AccountDetailDialog(QDialog):
    """Dialog chi tiết tài khoản (4.2.2), trạng thái khóa (4.2.4) & lịch sử (4.2.5)."""

    def __init__(
        self,
        detail_data: Dict[str, Any],
        history_items: List[Dict[str, Any]],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chi tiết Tài khoản & Lịch sử trạng thái (Chỉ xem)")
        self.setMinimumSize(680, 480)
        self._build_ui(detail_data, history_items)

    def _build_ui(
        self, detail_data: Dict[str, Any], history_items: List[Dict[str, Any]]
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        root.addWidget(ReadOnlyBanner(self))

        account = detail_data.get("account") or {}
        last_login = detail_data.get("last_login") or {}

        info_card = SectionCard(
            title=f"Tài khoản: {account.get('ten_dang_nhap', '-')}",
            subtitle=f"Vai trò: {account.get('vai_tro', '-')}",
        )
        form = QFormLayout()
        form.addRow("Họ tên:", QLabel(str(account.get("ho_ten") or "-")))
        form.addRow("Email / SĐT:", QLabel(f"{account.get('email') or '-'} · {account.get('so_dien_thoai') or '-'}"))
        form.addRow("Trạng thái (4.2.4):", QLabel(str(account.get("trang_thai") or "-")))
        form.addRow("Lý do khóa:", QLabel(str(account.get("ly_do_khoa") or "Không có")))
        form.addRow(
            "Đăng nhập gần nhất:",
            QLabel(
                f"{last_login.get('timestamp', 'Chưa ghi nhận')} "
                f"({last_login.get('ip_address', '-')}) — {last_login.get('ket_qua', '-')}"
            ),
        )
        info_card.body_layout.addLayout(form)
        root.addWidget(info_card)

        hist_card = SectionCard(
            title="Lịch sử thay đổi trạng thái tài khoản (4.2.5)",
            subtitle="Trích xuất từ nhật ký audit_*.jsonl",
        )
        tbl = QTableWidget(len(history_items), 5)
        tbl.setHorizontalHeaderLabels(
            ["Thời gian", "Người thực hiện", "Thao tác", "Trạng thái cũ", "Trạng thái mới"]
        )
        style_table(tbl)
        for idx, item in enumerate(history_items):
            tbl.setItem(idx, 0, QTableWidgetItem(str(item.get("timestamp") or "")))
            tbl.setItem(idx, 1, QTableWidgetItem(str(item.get("ten_dang_nhap") or "")))
            tbl.setItem(idx, 2, QTableWidgetItem(str(item.get("loai_thao_tac") or "")))
            tbl.setItem(idx, 3, QTableWidgetItem(_pretty_json(item.get("gia_tri_cu"))))
            tbl.setItem(idx, 4, QTableWidgetItem(_pretty_json(item.get("gia_tri_moi"))))
        hist_card.body_layout.addWidget(tbl)
        root.addWidget(hist_card, 1)

        btn_close = QPushButton("Đóng")
        set_button_variant(btn_close, "default", "sm")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)


class AdminAccountsView(QWidget):
    """Giao diện Admin 4.2.1 – 4.2.5: Xem danh sách, lọc Quản lý, xem khóa & lịch sử."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._accounts: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        root.addWidget(ReadOnlyBanner(self))

        card = SectionCard(
            title="Danh sách Tài khoản Hệ thống (4.2.1 – 4.2.5)",
            subtitle="Chế độ Admin chỉ xem — tra cứu hồ sơ, tài khoản Quản lý và lịch sử khóa/mở khóa",
        )

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Tìm theo tên đăng nhập, họ tên, email...")
        self.cbo_role = QComboBox()
        self.cbo_role.addItem("Tất cả vai trò", "")
        self.cbo_role.addItem("Quản lý chung cư", "quan_ly")
        self.cbo_role.addItem("Nhân viên bãi xe", "nhan_vien")
        self.cbo_role.addItem("Cư dân", "cu_dan")
        self.cbo_role.addItem("Admin", "admin")

        self.cbo_status = QComboBox()
        self.cbo_status.addItem("Tất cả trạng thái", "")
        self.cbo_status.addItem("Đang hoạt động", "hoat_dong")
        self.cbo_status.addItem("Tạm khóa", "tam_khoa")

        self.chk_only_managers = QCheckBox("Chỉ tài khoản Quản lý (4.2.3)")

        self.btn_refresh = QPushButton("Tìm kiếm / Làm mới")
        set_button_variant(self.btn_refresh, "primary", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)

        self.btn_detail = QPushButton("Xem chi tiết & Lịch sử")
        set_button_variant(self.btn_detail, "teal", "sm")
        self.btn_detail.clicked.connect(self.open_selected_detail)

        filter_row.addWidget(self.txt_search, 2)
        filter_row.addWidget(self.cbo_role, 1)
        filter_row.addWidget(self.cbo_status, 1)
        filter_row.addWidget(self.chk_only_managers)
        filter_row.addWidget(self.btn_refresh)
        filter_row.addWidget(self.btn_detail)
        card.body_layout.addLayout(filter_row)

        self.tbl_accounts = QTableWidget(0, 7)
        self.tbl_accounts.setHorizontalHeaderLabels(
            ["Mã TK", "Tên đăng nhập", "Họ tên", "Vai trò", "Trạng thái", "Lý do khóa", "Đơn vị / Căn hộ"]
        )
        style_table(self.tbl_accounts)
        self.tbl_accounts.cellDoubleClicked.connect(lambda _r, _c: self.open_selected_detail())
        card.body_layout.addWidget(self.tbl_accounts, 1)

        root.addWidget(card, 1)

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        payload = {
            "keyword": self.txt_search.text().strip(),
            "vai_tro": self.cbo_role.currentData(),
            "trang_thai": self.cbo_status.currentData(),
            "only_managers": self.chk_only_managers.isChecked(),
        }
        resp = self.socket_client.call(Actions.ADMIN_LIST_ACCOUNTS, payload)
        if resp.get("ok"):
            items = (resp.get("data") or {}).get("items") or []
            self.set_accounts(items)

    def set_accounts(self, items: List[Dict[str, Any]]) -> None:
        self._accounts = items
        self.tbl_accounts.setRowCount(len(items))
        for idx, acc in enumerate(items):
            status = str(acc.get("trang_thai") or "hoat_dong")
            variant = "success" if status == "hoat_dong" else "danger"
            unit = acc.get("ma_can_ho") or acc.get("chuc_vu") or "-"
            self.tbl_accounts.setItem(idx, 0, QTableWidgetItem(str(acc.get("ma_tai_khoan") or "")))
            self.tbl_accounts.setItem(idx, 1, QTableWidgetItem(str(acc.get("ten_dang_nhap") or "")))
            self.tbl_accounts.setItem(idx, 2, QTableWidgetItem(str(acc.get("ho_ten") or "")))
            self.tbl_accounts.setItem(idx, 3, QTableWidgetItem(str(acc.get("vai_tro") or "")))
            self.tbl_accounts.setCellWidget(idx, 4, StatusBadge(status, variant))
            self.tbl_accounts.setItem(idx, 5, QTableWidgetItem(str(acc.get("ly_do_khoa") or "-")))
            self.tbl_accounts.setItem(idx, 6, QTableWidgetItem(str(unit)))

    def open_selected_detail(self) -> None:
        row = self.tbl_accounts.currentRow()
        if row < 0 or row >= len(self._accounts):
            return
        account = self._accounts[row]
        ma_tk = account.get("ma_tai_khoan")
        detail_data: Dict[str, Any] = {"account": account, "last_login": {}}
        history_items: List[Dict[str, Any]] = []

        if self.socket_client is not None and self.socket_client.is_connected() and ma_tk is not None:
            r_det = self.socket_client.call(Actions.ADMIN_GET_ACCOUNT, {"ma_tai_khoan": ma_tk})
            if r_det.get("ok") and r_det.get("data"):
                detail_data = r_det["data"]
            r_hist = self.socket_client.call(Actions.ADMIN_ACCOUNT_HISTORY, {"ma_tai_khoan": ma_tk})
            if r_hist.get("ok") and r_hist.get("data"):
                history_items = r_hist["data"].get("items") or []

        dlg = AccountDetailDialog(detail_data, history_items, parent=self)
        dlg.exec()


# ==============================================================================
# 4.3.1 - 4.3.2: ADMIN XEM PHIÊN KẾT NỐI TRÊN RAM
# ==============================================================================
class SessionDetailDialog(QDialog):
    """Dialog 4.3.2: Chi tiết phiên kết nối TCP trên RAM Server."""

    def __init__(self, session_data: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chi tiết Phiên kết nối TCP (Chỉ xem)")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        root.addWidget(ReadOnlyBanner(self))

        form = QFormLayout()
        for label, key in (
            ("Session ID:", "session_id"),
            ("IP : Port:", "endpoint"),
            ("Tên đăng nhập:", "ten_dang_nhap"),
            ("Vai trò:", "vai_tro"),
            ("Họ tên:", "ho_ten"),
            ("Kết nối lúc:", "connected_at"),
            ("Heartbeat gần nhất:", "last_heartbeat"),
            ("Không hoạt động (s):", "idle_seconds"),
            ("Trạng thái:", "trang_thai_ket_noi"),
        ):
            form.addRow(label, QLabel(str(session_data.get(key) or "-")))
        root.addLayout(form)

        btn_close = QPushButton("Đóng")
        set_button_variant(btn_close, "default", "sm")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)


class AdminSessionsView(QWidget):
    """Giao diện Admin 4.3.1 – 4.3.2: Danh sách phiên kết nối real-time từ RAM Server."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._sessions: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        root.addWidget(ReadOnlyBanner(self))

        card = SectionCard(
            title="Phiên Kết Nối TCP Đang Mở (4.3.1 – 4.3.2)",
            subtitle="Dữ liệu trực tiếp từ SessionManager trên RAM Server",
        )
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self.txt_user = QLineEdit()
        self.txt_user.setPlaceholderText("Lọc theo tên đăng nhập...")
        self.cbo_role = QComboBox()
        self.cbo_role.addItem("Tất cả vai trò", "")
        self.cbo_role.addItem("Quản lý", "quan_ly")
        self.cbo_role.addItem("Nhân viên", "nhan_vien")
        self.cbo_role.addItem("Cư dân", "cu_dan")
        self.cbo_role.addItem("Admin", "admin")

        self.btn_refresh = QPushButton("Làm mới danh sách phiên")
        set_button_variant(self.btn_refresh, "primary", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)

        self.btn_detail = QPushButton("Xem chi tiết phiên (4.3.2)")
        set_button_variant(self.btn_detail, "teal", "sm")
        self.btn_detail.clicked.connect(self.open_selected_session)

        bar.addWidget(self.txt_user, 2)
        bar.addWidget(self.cbo_role, 1)
        bar.addWidget(self.btn_refresh)
        bar.addWidget(self.btn_detail)
        card.body_layout.addLayout(bar)

        self.tbl_sessions = QTableWidget(0, 7)
        self.tbl_sessions.setHorizontalHeaderLabels(
            ["Session ID", "Endpoint", "Tài khoản", "Vai trò", "Kết nối lúc", "Heartbeat cuối", "Trạng thái"]
        )
        style_table(self.tbl_sessions)
        self.tbl_sessions.cellDoubleClicked.connect(lambda _r, _c: self.open_selected_session())
        card.body_layout.addWidget(self.tbl_sessions, 1)
        root.addWidget(card, 1)

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.ADMIN_LIST_SESSIONS,
            {
                "ten_dang_nhap": self.txt_user.text().strip(),
                "vai_tro": self.cbo_role.currentData(),
            },
        )
        if resp.get("ok"):
            self.set_sessions((resp.get("data") or {}).get("items") or [])

    def set_sessions(self, items: List[Dict[str, Any]]) -> None:
        self._sessions = items
        self.tbl_sessions.setRowCount(len(items))
        for idx, sess in enumerate(items):
            status = str(sess.get("trang_thai_ket_noi") or "online")
            variant = "success" if status == "online" else "warning"
            self.tbl_sessions.setItem(idx, 0, QTableWidgetItem(str(sess.get("session_id") or "")[:12]))
            self.tbl_sessions.setItem(idx, 1, QTableWidgetItem(str(sess.get("endpoint") or "")))
            self.tbl_sessions.setItem(idx, 2, QTableWidgetItem(str(sess.get("ten_dang_nhap") or "")))
            self.tbl_sessions.setItem(idx, 3, QTableWidgetItem(str(sess.get("vai_tro") or "")))
            self.tbl_sessions.setItem(idx, 4, QTableWidgetItem(str(sess.get("connected_at") or "")))
            self.tbl_sessions.setItem(idx, 5, QTableWidgetItem(str(sess.get("last_heartbeat") or "")))
            self.tbl_sessions.setCellWidget(idx, 6, StatusBadge(status, variant))

    def open_selected_session(self) -> None:
        row = self.tbl_sessions.currentRow()
        if row < 0 or row >= len(self._sessions):
            return
        sess = self._sessions[row]
        if self.socket_client is not None and self.socket_client.is_connected():
            resp = self.socket_client.call(
                Actions.ADMIN_GET_SESSION,
                {"session_id": sess.get("session_id")},
            )
            if resp.get("ok") and resp.get("data"):
                sess = resp["data"].get("session") or sess
        dlg = SessionDetailDialog(sess, parent=self)
        dlg.exec()


# ==============================================================================
# 4.4.1 - 4.4.3: ADMIN XEM 3 LOẠI NHẬT KÝ HỆ THỐNG (JSONL)
# ==============================================================================
class AdminLogsView(QWidget):
    """Giao diện Admin 4.4.1 – 4.4.3: Nhật ký thao tác, đăng nhập và lỗi hệ thống."""

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
        root.addWidget(ReadOnlyBanner(self))

        card = SectionCard(
            title="Nhật Ký Hệ Thống JSONL (4.4.1 – 4.4.3)",
            subtitle="Tra cứu log theo ngày từ storage/logs/*.jsonl",
        )
        filters = QHBoxLayout()
        filters.setSpacing(10)

        self.txt_start_date = QLineEdit()
        self.txt_start_date.setPlaceholderText("Từ ngày (YYYY-MM-DD)")
        self.txt_end_date = QLineEdit()
        self.txt_end_date.setPlaceholderText("Đến ngày (YYYY-MM-DD)")
        self.txt_keyword = QLineEdit()
        self.txt_keyword.setPlaceholderText("Từ khóa / Tên đăng nhập / Mã lỗi...")

        self.btn_query = QPushButton("Tra cứu Nhật ký")
        set_button_variant(self.btn_query, "primary", "sm")
        self.btn_query.clicked.connect(self.refresh_current_tab)

        filters.addWidget(self.txt_start_date, 1)
        filters.addWidget(self.txt_end_date, 1)
        filters.addWidget(self.txt_keyword, 2)
        filters.addWidget(self.btn_query)
        card.body_layout.addLayout(filters)

        self.tabs = QTabWidget()

        # 4.4.1 Nhật ký thao tác (audit)
        self.tbl_audit = QTableWidget(0, 7)
        self.tbl_audit.setHorizontalHeaderLabels(
            ["Thời gian", "Tài khoản", "Vai trò", "Thao tác", "Đối tượng", "Giá trị cũ", "Giá trị mới"]
        )
        style_table(self.tbl_audit)
        self.tabs.addTab(self.tbl_audit, "4.4.1 Nhật ký thao tác")

        # 4.4.2 Nhật ký đăng nhập (auth)
        self.tbl_auth = QTableWidget(0, 6)
        self.tbl_auth.setHorizontalHeaderLabels(
            ["Thời gian", "Tên đăng nhập", "Vai trò", "Địa chỉ IP", "Kết quả", "Lý do"]
        )
        style_table(self.tbl_auth)
        self.tabs.addTab(self.tbl_auth, "4.4.2 Nhật ký đăng nhập")

        # 4.4.3 Nhật ký lỗi (error)
        self.tbl_error = QTableWidget(0, 5)
        self.tbl_error.setHorizontalHeaderLabels(
            ["Thời gian", "Mức độ", "Nguồn", "Mã lỗi", "Nội dung"]
        )
        style_table(self.tbl_error)
        self.tabs.addTab(self.tbl_error, "4.4.3 Nhật ký lỗi hệ thống")

        card.body_layout.addWidget(self.tabs, 1)
        root.addWidget(card, 1)

    def refresh_current_tab(self) -> None:
        tab_types = ("audit", "auth", "error")
        idx = self.tabs.currentIndex()
        log_type = tab_types[idx] if 0 <= idx < len(tab_types) else "audit"
        self.refresh_log_type(log_type)

    def refresh_log_type(self, log_type: str) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.ADMIN_QUERY_LOGS,
            {
                "log_type": log_type,
                "start_date": self.txt_start_date.text().strip() or None,
                "end_date": self.txt_end_date.text().strip() or None,
                "keyword": self.txt_keyword.text().strip() or None,
                "limit": 200,
            },
        )
        if resp.get("ok"):
            items = (resp.get("data") or {}).get("items") or []
            self.set_logs(log_type, items)

    def set_logs(self, log_type: str, items: List[Dict[str, Any]]) -> None:
        if log_type == "audit":
            self.tbl_audit.setRowCount(len(items))
            for idx, item in enumerate(items):
                self.tbl_audit.setItem(idx, 0, QTableWidgetItem(str(item.get("timestamp") or "")))
                self.tbl_audit.setItem(idx, 1, QTableWidgetItem(str(item.get("ten_dang_nhap") or "")))
                self.tbl_audit.setItem(idx, 2, QTableWidgetItem(str(item.get("vai_tro") or "")))
                self.tbl_audit.setItem(idx, 3, QTableWidgetItem(str(item.get("loai_thao_tac") or "")))
                self.tbl_audit.setItem(
                    idx, 4, QTableWidgetItem(f"{item.get('doi_tuong', '')} #{item.get('ma_doi_tuong', '')}")
                )
                self.tbl_audit.setItem(idx, 5, QTableWidgetItem(_pretty_json(item.get("gia_tri_cu"))))
                self.tbl_audit.setItem(idx, 6, QTableWidgetItem(_pretty_json(item.get("gia_tri_moi"))))
        elif log_type == "auth":
            self.tbl_auth.setRowCount(len(items))
            for idx, item in enumerate(items):
                res = str(item.get("ket_qua") or "")
                variant = "success" if res == "thanh_cong" else ("warning" if res == "that_bai" else "danger")
                self.tbl_auth.setItem(idx, 0, QTableWidgetItem(str(item.get("timestamp") or "")))
                self.tbl_auth.setItem(idx, 1, QTableWidgetItem(str(item.get("ten_dang_nhap") or "")))
                self.tbl_auth.setItem(idx, 2, QTableWidgetItem(str(item.get("vai_tro") or "")))
                self.tbl_auth.setItem(idx, 3, QTableWidgetItem(str(item.get("ip_address") or "")))
                self.tbl_auth.setCellWidget(idx, 4, StatusBadge(res, variant))
                self.tbl_auth.setItem(idx, 5, QTableWidgetItem(str(item.get("ly_do") or "-")))
        elif log_type == "error":
            self.tbl_error.setRowCount(len(items))
            for idx, item in enumerate(items):
                lvl = str(item.get("muc_do") or "INFO")
                variant = "danger" if lvl == "ERROR" else ("warning" if lvl == "WARN" else "info")
                self.tbl_error.setItem(idx, 0, QTableWidgetItem(str(item.get("timestamp") or "")))
                self.tbl_error.setCellWidget(idx, 1, StatusBadge(lvl, variant))
                self.tbl_error.setItem(idx, 2, QTableWidgetItem(str(item.get("nguon") or "")))
                self.tbl_error.setItem(idx, 3, QTableWidgetItem(str(item.get("ma_loi") or "")))
                self.tbl_error.setItem(idx, 4, QTableWidgetItem(str(item.get("noi_dung") or "")))


# ==============================================================================
# 4.5 - 4.8: KHUNG BỌC ADMIN READ-ONLY CHO CÁC MÀN HÌNH QUẢN LÝ
# ==============================================================================
class AdminReadOnlyWrapper(QWidget):
    """Khung bọc tái sử dụng các View nghiệp vụ ở chế độ Admin Read-Only (4.5–4.8)."""

    def __init__(
        self,
        title: str,
        child_widget: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.child_widget = child_widget

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        root.addWidget(ReadOnlyBanner(self))

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 19px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        root.addWidget(lbl_title)

        if child_widget is not None:
            self._enforce_read_only(child_widget)
            root.addWidget(child_widget, 1)
        else:
            root.addWidget(
                EmptyStateWidget(
                    title="Chưa ghép module giao diện chi tiết",
                    description="Màn hình sẽ tự động hiển thị ở chế độ chỉ xem sau khi ghép nhánh của các thành viên.",
                ),
                1,
            )

    @staticmethod
    def _enforce_read_only(widget: QWidget) -> None:
        """Vô hiệu hóa/ẩn các nút ghi trên widget con để đảm bảo an toàn tuyệt đối cho Admin."""
        if hasattr(widget, "read_only"):
            try:
                setattr(widget, "read_only", True)
            except Exception:
                pass

        for btn in widget.findChildren(QPushButton):
            text = (btn.text() or "").strip().lower()
            obj_name = (btn.objectName() or "").lower()
            if btn.property("is_mutation") is True or "mutation" in obj_name:
                btn.setEnabled(False)
                btn.setVisible(False)
                continue
            if any(kw in text for kw in _MUTATION_KEYWORDS):
                btn.setEnabled(False)
                btn.setVisible(False)
