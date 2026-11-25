"""Màn hình Đăng nhập hệ thống Smart Parking — Chung cư Đào Tiên (PySide6)."""

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import SectionCard, StatusBadge, set_button_variant
from client.ui.theme.role_shell import apply_design_stylesheet
from client.ui.theme.tokens import Colors, Fonts
from shared.protocol import Actions


class LoginDialog(QDialog):
    """Hộp thoại Đăng nhập dùng chung cho 4 vai trò (1.2.1, 2.1.1, 3.1.1, 4.1.1)."""

    login_succeeded = Signal(dict)

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.login_result: Optional[Dict[str, Any]] = None

        self.setWindowTitle("Đăng nhập — Smart Parking Chung cư Đào Tiên")
        self.setMinimumSize(460, 520)
        self.setModal(True)
        apply_design_stylesheet(self)

        self._build_ui()
        self._bind_socket_signals()
        self._sync_connection_badge()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        # Header thương hiệu Midnight Navy
        banner = QFrame()
        banner.setStyleSheet(
            f"QFrame {{ background-color: {Colors.SIDEBAR_BG}; border-radius: 12px; }}"
        )
        b_lay = QVBoxLayout(banner)
        b_lay.setContentsMargins(20, 16, 20, 16)
        b_lay.setSpacing(4)

        lbl_app = QLabel("SMART PARKING — CHUNG CƯ ĐÀO TIÊN")
        lbl_app.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 16px; font-weight: 800; "
            f"color: {Colors.TEXT_ON_DARK}; letter-spacing: 0.6px;"
        )
        lbl_desc = QLabel("Hệ thống quản lý bãi giữ xe thông minh qua TCP Socket")
        lbl_desc.setStyleSheet(f"font-size: 12px; color: {Colors.SIDEBAR_TEXT_MUTED};")
        b_lay.addWidget(lbl_app)
        b_lay.addWidget(lbl_desc)
        root.addWidget(banner)

        # Thanh trạng thái kết nối Socket Server
        conn_row = QHBoxLayout()
        conn_row.setSpacing(8)
        self.badge_conn = StatusBadge("Chưa kết nối Server", "warning")
        conn_row.addWidget(self.badge_conn)
        conn_row.addStretch(1)

        is_ws = (
            self.socket_client is not None
            and getattr(self.socket_client, "transport", "tcp") == "websocket"
        )
        initial_host = (
            self.socket_client.ws_url
            if (is_ws and self.socket_client.ws_url)
            else (self.socket_client.host if self.socket_client is not None else "127.0.0.1")
        )
        self.txt_host = QLineEdit(initial_host)
        self.txt_host.setFixedWidth(200 if is_ws else 120)
        self.txt_host.setPlaceholderText("URL WebSocket hoặc Host")
        self.txt_port = QLineEdit(
            str(self.socket_client.port if self.socket_client is not None else 5555)
        )
        self.txt_port.setFixedWidth(64)
        self.txt_port.setPlaceholderText("Port")
        if is_ws:
            self.txt_port.setVisible(False)

        self.btn_reconnect = QPushButton("Kết nối")
        set_button_variant(self.btn_reconnect, "default", "sm")
        self.btn_reconnect.clicked.connect(self._reconnect_server)

        conn_row.addWidget(self.txt_host)
        conn_row.addWidget(self.txt_port)
        conn_row.addWidget(self.btn_reconnect)
        root.addLayout(conn_row)

        # Card thông tin đăng nhập
        card = SectionCard(
            title="Đăng nhập hệ thống",
            subtitle="Hỗ trợ Quản lý, Nhân viên bãi xe, Cư dân và Admin",
        )

        lbl_user = QLabel("Tên đăng nhập")
        lbl_user.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT_PRIMARY}; border: none;")
        self.txt_username = QLineEdit()
        self.txt_username.setPlaceholderText("Ví dụ: quanly01, nhanvien01, cudan_a101, admin01")
        self.txt_username.returnPressed.connect(self._on_login_clicked)

        lbl_pw = QLabel("Mật khẩu")
        lbl_pw.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT_PRIMARY}; border: none;")
        pw_row = QHBoxLayout()
        pw_row.setSpacing(8)
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Nhập mật khẩu...")
        self.txt_password.returnPressed.connect(self._on_login_clicked)

        self.btn_toggle_pw = QPushButton("Hiện")
        set_button_variant(self.btn_toggle_pw, "ghost", "sm")
        self.btn_toggle_pw.setCheckable(True)
        self.btn_toggle_pw.toggled.connect(self._toggle_password_visibility)

        pw_row.addWidget(self.txt_password, 1)
        pw_row.addWidget(self.btn_toggle_pw)

        self.lbl_error = QLabel("")
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setVisible(False)
        self.lbl_error.setStyleSheet(
            f"background-color: {Colors.ERROR_BG}; color: {Colors.ERROR_TEXT}; "
            f"border: 1px solid {Colors.ERROR_BORDER}; border-radius: 8px; "
            "padding: 8px 10px; font-size: 12px; font-weight: 600;"
        )

        card.body_layout.addWidget(lbl_user)
        card.body_layout.addWidget(self.txt_username)
        card.body_layout.addWidget(lbl_pw)
        card.body_layout.addLayout(pw_row)
        card.body_layout.addWidget(self.lbl_error)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_forgot = QPushButton("Quên mật khẩu (OTP Email)?")
        set_button_variant(self.btn_forgot, "ghost", "sm")
        self.btn_forgot.clicked.connect(self._open_forgot_password)

        self.btn_login = QPushButton("Đăng nhập")
        set_button_variant(self.btn_login, "primary", "md")
        self.btn_login.clicked.connect(self._on_login_clicked)

        btn_row.addWidget(self.btn_forgot)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_login)
        card.body_layout.addLayout(btn_row)

        root.addWidget(card, 1)

    def _bind_socket_signals(self) -> None:
        if self.socket_client is not None:
            self.socket_client.connection_state_changed.connect(self._on_connection_changed)

    def _on_connection_changed(self, connected: bool, message: str) -> None:
        if connected:
            if self.socket_client is not None and getattr(self.socket_client, "transport", "tcp") == "websocket":
                url = self.socket_client.ws_url or f"ws://{self.socket_client.host}:{self.socket_client.port}/ws"
                proto = "WSS" if url.startswith("wss://") else "WS"
                self.badge_conn.setText(f"Online ({proto}) · {url}")
            else:
                host = self.socket_client.host if self.socket_client else "127.0.0.1"
                port = self.socket_client.port if self.socket_client else 5555
                self.badge_conn.setText(f"Online (TCP) · {host}:{port}")
            self.badge_conn.set_variant("success")
        else:
            self.badge_conn.setText(message or "Mất kết nối Server")
            self.badge_conn.set_variant("danger")

    def _sync_connection_badge(self) -> None:
        if self.socket_client is not None and self.socket_client.is_connected():
            if getattr(self.socket_client, "transport", "tcp") == "websocket":
                url = self.socket_client.ws_url or f"ws://{self.socket_client.host}:{self.socket_client.port}/ws"
                proto = "WSS" if url.startswith("wss://") else "WS"
                self.badge_conn.setText(f"Online ({proto}) · {url}")
            else:
                self.badge_conn.setText(f"Online (TCP) · {self.socket_client.host}:{self.socket_client.port}")
            self.badge_conn.set_variant("success")
        else:
            self.badge_conn.setText("Chưa kết nối Server")
            self.badge_conn.set_variant("warning")

    def _reconnect_server(self) -> None:
        if self.socket_client is None:
            self._show_error("Chưa khởi tạo SocketClient.")
            return

        raw_target = self.txt_host.text().strip()
        self._clear_error()

        # 1. Nếu người dùng nhập URL WebSocket (ws:// hoặc wss://):
        if raw_target.startswith("ws://") or raw_target.startswith("wss://"):
            ok = self.socket_client.connect_to_server(
                ws_url=raw_target,
                transport="websocket",
                timeout=8.0,
            )
            if not ok:
                self._show_error(f"Không thể kết nối WebSocket tới {raw_target}.")
            return

        # 2. Nếu client đang cấu hình transport là websocket:
        if getattr(self.socket_client, "transport", "tcp") == "websocket":
            target_url = self.socket_client.ws_url or f"ws://{raw_target or '127.0.0.1'}:8000/ws"
            ok = self.socket_client.connect_to_server(
                ws_url=target_url,
                transport="websocket",
                timeout=8.0,
            )
            if not ok:
                self._show_error(f"Không thể kết nối WebSocket tới {target_url}.")
            return

        # 3. Chế độ TCP Socket truyền thống:
        host = raw_target or "127.0.0.1"
        try:
            port = int(self.txt_port.text().strip() or "5555")
        except ValueError:
            self._show_error("Port kết nối phải là số nguyên.")
            return

        ok = self.socket_client.connect_to_server(
            host=host,
            port=port,
            transport="tcp",
            timeout=5.0,
        )
        if not ok:
            self._show_error(f"Không thể kết nối TCP tới {host}:{port}.")

    def _toggle_password_visibility(self, checked: bool) -> None:
        self.txt_password.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.btn_toggle_pw.setText("Ẩn" if checked else "Hiện")

    def _show_error(self, message: str) -> None:
        self.lbl_error.setText(message)
        self.lbl_error.setVisible(True)

    def _clear_error(self) -> None:
        self.lbl_error.clear()
        self.lbl_error.setVisible(False)

    def _on_login_clicked(self) -> None:
        self._clear_error()
        username = self.txt_username.text().strip()
        password = self.txt_password.text()
        if not username or not password:
            self._show_error("Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.")
            return
        if self.socket_client is None:
            self._show_error("Chưa cấu hình SocketClient.")
            return
        if not self.socket_client.is_connected():
            self._reconnect_server()
            if not self.socket_client.is_connected():
                return

        self.btn_login.setEnabled(False)
        try:
            resp = self.socket_client.call(
                Actions.AUTH_LOGIN,
                {"ten_dang_nhap": username, "mat_khau": password},
                timeout=8.0,
            )
        finally:
            self.btn_login.setEnabled(True)

        if not resp.get("ok"):
            self._show_error(str(resp.get("error") or "Đăng nhập thất bại."))
            return

        self.login_result = resp.get("data") or {}
        self.login_succeeded.emit(self.login_result)
        self.accept()

    def _open_forgot_password(self) -> None:
        from client.ui.auth.forgot_password_dialog import ForgotPasswordOTPDialog

        dlg = ForgotPasswordOTPDialog(socket_client=self.socket_client, parent=self)
        dlg.exec()
