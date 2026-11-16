"""Wizard 3 bước khôi phục mật khẩu qua OTP Email (1.2.3, 2.1.3, 3.1.3, 4.1.3–4.1.6)."""

from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import SectionCard, StatusBadge, set_button_variant
from client.ui.theme.role_shell import apply_design_stylesheet
from client.ui.theme.tokens import Colors, Fonts
from shared.protocol import Actions
from shared.validators import validate_email


class ForgotPasswordOTPDialog(QDialog):
    """Hộp thoại Wizard 3 bước khôi phục mật khẩu bằng mã OTP gửi qua Email."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._ma_yeu_cau_otp: str = ""
        self._reset_token: str = ""
        self._cooldown_remaining: int = 0

        self.setWindowTitle("Khôi phục mật khẩu qua Email OTP")
        self.setMinimumSize(460, 380)
        self.setModal(True)
        apply_design_stylesheet(self)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_tick_cooldown)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        # Thanh chỉ báo bước
        step_row = QHBoxLayout()
        step_row.setSpacing(8)
        self.badge_step1 = StatusBadge("1. Nhập Email", "primary")
        self.badge_step2 = StatusBadge("2. Xác minh OTP", "neutral")
        self.badge_step3 = StatusBadge("3. Mật khẩu mới", "neutral")
        step_row.addWidget(self.badge_step1)
        step_row.addWidget(self.badge_step2)
        step_row.addWidget(self.badge_step3)
        step_row.addStretch(1)
        root.addLayout(step_row)

        self.lbl_feedback = QLabel("")
        self.lbl_feedback.setWordWrap(True)
        self.lbl_feedback.setVisible(False)
        root.addWidget(self.lbl_feedback)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1_email())
        self.stack.addWidget(self._build_step2_otp())
        self.stack.addWidget(self._build_step3_reset())
        root.addWidget(self.stack, 1)

    def _build_step1_email(self) -> QWidget:
        card = SectionCard(
            title="Bước 1: Nhập Email tài khoản",
            subtitle="Hệ thống sẽ gửi mã OTP 6 chữ số đến email đã đăng ký (4.1.3)",
        )
        lbl = QLabel("Địa chỉ Email")
        lbl.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT_PRIMARY}; border: none;")
        self.txt_email = QLineEdit()
        self.txt_email.setPlaceholderText("Ví dụ: quanly01@daotien.vn")
        self.txt_email.returnPressed.connect(self._on_send_otp)

        card.body_layout.addWidget(lbl)
        card.body_layout.addWidget(self.txt_email)
        card.body_layout.addStretch(1)

        row = QHBoxLayout()
        btn_cancel = QPushButton("Hủy")
        set_button_variant(btn_cancel, "default", "sm")
        btn_cancel.clicked.connect(self.reject)

        self.btn_send_otp = QPushButton("Gửi mã OTP")
        set_button_variant(self.btn_send_otp, "primary", "md")
        self.btn_send_otp.clicked.connect(self._on_send_otp)

        row.addWidget(btn_cancel)
        row.addStretch(1)
        row.addWidget(self.btn_send_otp)
        card.body_layout.addLayout(row)
        return card

    def _build_step2_otp(self) -> QWidget:
        card = SectionCard(
            title="Bước 2: Nhập mã OTP 6 chữ số",
            subtitle="Kiểm tra hộp thư đến và nhập mã xác thực (4.1.4 – 4.1.5)",
        )
        self.lbl_otp_target = QLabel("Đã gửi mã OTP tới email.")
        self.lbl_otp_target.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_SECONDARY}; border: none;")

        self.txt_otp = QLineEdit()
        self.txt_otp.setMaxLength(6)
        self.txt_otp.setPlaceholderText("Nhập 6 chữ số OTP...")
        self.txt_otp.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 18px; font-weight: 700; letter-spacing: 4px;"
        )
        self.txt_otp.returnPressed.connect(self._on_verify_otp)

        card.body_layout.addWidget(self.lbl_otp_target)
        card.body_layout.addWidget(self.txt_otp)
        card.body_layout.addStretch(1)

        row = QHBoxLayout()
        self.btn_resend = QPushButton("Gửi lại OTP")
        set_button_variant(self.btn_resend, "default", "sm")
        self.btn_resend.clicked.connect(self._on_resend_otp)

        self.btn_verify_otp = QPushButton("Xác minh OTP")
        set_button_variant(self.btn_verify_otp, "primary", "md")
        self.btn_verify_otp.clicked.connect(self._on_verify_otp)

        row.addWidget(self.btn_resend)
        row.addStretch(1)
        row.addWidget(self.btn_verify_otp)
        card.body_layout.addLayout(row)
        return card

    def _build_step3_reset(self) -> QWidget:
        card = SectionCard(
            title="Bước 3: Đặt lại mật khẩu mới",
            subtitle="Sau khi đổi thành công, mọi phiên đăng nhập cũ sẽ được thu hồi (4.1.6)",
        )
        lbl1 = QLabel("Mật khẩu mới (tối thiểu 6 ký tự)")
        lbl1.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT_PRIMARY}; border: none;")
        self.txt_new_pw = QLineEdit()
        self.txt_new_pw.setEchoMode(QLineEdit.EchoMode.Password)

        lbl2 = QLabel("Xác nhận mật khẩu mới")
        lbl2.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT_PRIMARY}; border: none;")
        self.txt_confirm_pw = QLineEdit()
        self.txt_confirm_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_confirm_pw.returnPressed.connect(self._on_reset_password)

        card.body_layout.addWidget(lbl1)
        card.body_layout.addWidget(self.txt_new_pw)
        card.body_layout.addWidget(lbl2)
        card.body_layout.addWidget(self.txt_confirm_pw)
        card.body_layout.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_reset_pw = QPushButton("Hoàn tất đặt lại mật khẩu")
        set_button_variant(self.btn_reset_pw, "success", "md")
        self.btn_reset_pw.clicked.connect(self._on_reset_password)
        row.addWidget(self.btn_reset_pw)
        card.body_layout.addLayout(row)
        return card

    def _set_step(self, step_index: int) -> None:
        self.stack.setCurrentIndex(step_index)
        badges = (self.badge_step1, self.badge_step2, self.badge_step3)
        for idx, badge in enumerate(badges):
            if idx < step_index:
                badge.set_variant("success")
            elif idx == step_index:
                badge.set_variant("primary")
            else:
                badge.set_variant("neutral")

    def _show_feedback(self, message: str, is_error: bool = True) -> None:
        if is_error:
            bg, text, border = Colors.ERROR_BG, Colors.ERROR_TEXT, Colors.ERROR_BORDER
        else:
            bg, text, border = Colors.SUCCESS_BG, Colors.SUCCESS_TEXT, Colors.SUCCESS_BORDER
        self.lbl_feedback.setStyleSheet(
            f"background-color: {bg}; color: {text}; border: 1px solid {border}; "
            "border-radius: 8px; padding: 8px 10px; font-size: 12px; font-weight: 600;"
        )
        self.lbl_feedback.setText(message)
        self.lbl_feedback.setVisible(True)

    def _clear_feedback(self) -> None:
        self.lbl_feedback.setVisible(False)
        self.lbl_feedback.clear()

    def _start_cooldown(self, seconds: int) -> None:
        self._cooldown_remaining = max(0, int(seconds))
        self._update_resend_button()
        if self._cooldown_remaining > 0:
            self._countdown_timer.start()
        else:
            self._countdown_timer.stop()

    def _on_tick_cooldown(self) -> None:
        self._cooldown_remaining = max(0, self._cooldown_remaining - 1)
        self._update_resend_button()
        if self._cooldown_remaining <= 0:
            self._countdown_timer.stop()

    def _update_resend_button(self) -> None:
        if self._cooldown_remaining > 0:
            self.btn_resend.setEnabled(False)
            self.btn_resend.setText(f"Gửi lại sau ({self._cooldown_remaining}s)")
        else:
            self.btn_resend.setEnabled(True)
            self.btn_resend.setText("Gửi lại OTP")

    def _require_connected_client(self) -> Optional[SocketClient]:
        if self.socket_client is None or not self.socket_client.is_connected():
            self._show_feedback("Chưa kết nối tới TCP Socket Server.", is_error=True)
            return None
        return self.socket_client

    def _on_send_otp(self) -> None:
        self._clear_feedback()
        email = self.txt_email.text().strip().lower()
        if not validate_email(email):
            self._show_feedback("Vui lòng nhập địa chỉ email hợp lệ.", is_error=True)
            return
        client = self._require_connected_client()
        if client is None:
            return

        self.btn_send_otp.setEnabled(False)
        try:
            resp = client.call(Actions.AUTH_REQUEST_OTP, {"email": email}, timeout=15.0)
        finally:
            self.btn_send_otp.setEnabled(True)

        if not resp.get("ok"):
            self._show_feedback(str(resp.get("error") or "Không gửi được mã OTP."), is_error=True)
            return

        data = resp.get("data") or {}
        self._ma_yeu_cau_otp = str(data.get("ma_yeu_cau_otp") or "")
        cooldown = int(data.get("resend_cooldown") or 60)
        self.lbl_otp_target.setText(f"Mã OTP đã được gửi tới: {data.get('email', email)}")
        self._set_step(1)
        self._start_cooldown(cooldown)
        self._show_feedback("Đã gửi mã OTP qua Email thành công.", is_error=False)

    def _on_resend_otp(self) -> None:
        self._clear_feedback()
        client = self._require_connected_client()
        if client is None:
            return
        resp = client.call(
            Actions.AUTH_RESEND_OTP,
            {"ma_yeu_cau_otp": self._ma_yeu_cau_otp},
            timeout=15.0,
        )
        if not resp.get("ok"):
            self._show_feedback(str(resp.get("error") or "Không thể gửi lại OTP."), is_error=True)
            return
        data = resp.get("data") or {}
        self._start_cooldown(int(data.get("resend_cooldown") or 60))
        self._show_feedback("Đã gửi lại mã OTP mới vào email của bạn.", is_error=False)

    def _on_verify_otp(self) -> None:
        self._clear_feedback()
        otp_code = self.txt_otp.text().strip()
        if len(otp_code) != 6 or not otp_code.isdigit():
            self._show_feedback("Mã OTP phải gồm đúng 6 chữ số.", is_error=True)
            return
        client = self._require_connected_client()
        if client is None:
            return

        resp = client.call(
            Actions.AUTH_VERIFY_OTP,
            {"ma_yeu_cau_otp": self._ma_yeu_cau_otp, "ma_otp": otp_code},
            timeout=8.0,
        )
        if not resp.get("ok"):
            self._show_feedback(str(resp.get("error") or "Mã OTP không đúng."), is_error=True)
            return

        data = resp.get("data") or {}
        self._reset_token = str(data.get("reset_token") or "")
        self._countdown_timer.stop()
        self._set_step(2)
        self._show_feedback("Xác minh OTP hợp lệ. Hãy nhập mật khẩu mới.", is_error=False)

    def _on_reset_password(self) -> None:
        self._clear_feedback()
        new_pw = self.txt_new_pw.text()
        confirm_pw = self.txt_confirm_pw.text()
        if len(new_pw) < 6:
            self._show_feedback("Mật khẩu mới phải có ít nhất 6 ký tự.", is_error=True)
            return
        if new_pw != confirm_pw:
            self._show_feedback("Mật khẩu xác nhận không khớp.", is_error=True)
            return
        client = self._require_connected_client()
        if client is None:
            return

        resp = client.call(
            Actions.AUTH_RESET_PASSWORD,
            {
                "reset_token": self._reset_token,
                "mat_khau_moi": new_pw,
                "xac_nhan": confirm_pw,
            },
            timeout=8.0,
        )
        if not resp.get("ok"):
            self._show_feedback(str(resp.get("error") or "Đặt lại mật khẩu thất bại."), is_error=True)
            return
        self.accept()

    def closeEvent(self, event: object) -> None:
        self._countdown_timer.stop()
        super().closeEvent(event)  # type: ignore[arg-type]
