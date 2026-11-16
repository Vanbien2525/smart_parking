"""Màn hình Hồ sơ cá nhân & Đổi mật khẩu dùng chung 4 vai trò (1.2, 2.1, 3.1, 4.1)."""

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    ReadOnlyBanner,
    RoleBadge,
    SectionCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.constants import Roles
from shared.protocol import Actions


class ProfileView(QWidget):
    """Màn hình Xem/Cập nhật hồ sơ cá nhân & Đổi mật khẩu cho cả 4 vai trò."""

    logout_requested = Signal()

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        vai_tro: str = Roles.CU_DAN.value,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.vai_tro = vai_tro
        self._profile: Dict[str, Any] = {}
        self._build_ui()
        self.set_role_mode(vai_tro)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(16)

        self.admin_banner = ReadOnlyBanner(self)
        self.admin_banner.setVisible(False)
        root.addWidget(self.admin_banner)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Tài khoản & Hồ sơ cá nhân")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        lbl_sub = QLabel("Quản lý thông tin liên hệ, bảo mật mật khẩu và trạng thái tài khoản")
        lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        header.addLayout(title_col, 1)

        self.role_badge = RoleBadge(self.vai_tro)
        header.addWidget(self.role_badge)

        self.btn_refresh = QPushButton("Tải lại hồ sơ")
        set_button_variant(self.btn_refresh, "default", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)
        header.addWidget(self.btn_refresh)

        self.btn_logout = QPushButton("Đăng xuất")
        set_button_variant(self.btn_logout, "danger", "sm")
        self.btn_logout.clicked.connect(self._on_logout_clicked)
        header.addWidget(self.btn_logout)
        root.addLayout(header)

        cols = QHBoxLayout()
        cols.setSpacing(16)

        # Cột trái: Thông tin cá nhân
        self.profile_card = SectionCard(
            title="Thông tin hồ sơ cá nhân",
            subtitle="Cập nhật họ tên, số điện thoại, CCCD và địa chỉ email nhận OTP",
        )
        form = QFormLayout()
        form.setSpacing(10)

        self.lbl_username = QLabel("-")
        self.lbl_username.setStyleSheet(f"font-family: {Fonts.MONO_FAMILY}; font-weight: 700;")
        self.badge_status = StatusBadge("hoat_dong", "success")
        self.lbl_unit = QLabel("-")

        self.txt_ho_ten = QLineEdit()
        self.txt_cccd = QLineEdit()
        self.txt_phone = QLineEdit()
        self.txt_email = QLineEdit()
        self.txt_dia_chi = QLineEdit()

        form.addRow("Tên đăng nhập:", self.lbl_username)
        form.addRow("Trạng thái:", self.badge_status)
        form.addRow("Đơn vị / Căn hộ:", self.lbl_unit)
        form.addRow("Họ và tên:", self.txt_ho_ten)
        form.addRow("Số CCCD/CMND:", self.txt_cccd)
        form.addRow("Số điện thoại:", self.txt_phone)
        form.addRow("Email (nhận OTP):", self.txt_email)
        form.addRow("Địa chỉ:", self.txt_dia_chi)
        self.profile_card.body_layout.addLayout(form)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.btn_save_profile = QPushButton("Lưu thay đổi hồ sơ")
        set_button_variant(self.btn_save_profile, "primary", "md")
        self.btn_save_profile.clicked.connect(self._on_save_profile)
        save_row.addWidget(self.btn_save_profile)
        self.profile_card.body_layout.addLayout(save_row)

        # Cột phải: Đổi mật khẩu
        self.pw_card = SectionCard(
            title="Đổi mật khẩu đăng nhập",
            subtitle="Xác nhận mật khẩu hiện tại trước khi đặt mật khẩu mới",
        )
        pw_form = QFormLayout()
        pw_form.setSpacing(10)

        self.txt_old_pw = QLineEdit()
        self.txt_old_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_new_pw = QLineEdit()
        self.txt_new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_confirm_pw = QLineEdit()
        self.txt_confirm_pw.setEchoMode(QLineEdit.EchoMode.Password)

        pw_form.addRow("Mật khẩu hiện tại:", self.txt_old_pw)
        pw_form.addRow("Mật khẩu mới:", self.txt_new_pw)
        pw_form.addRow("Xác nhận mật khẩu:", self.txt_confirm_pw)
        self.pw_card.body_layout.addLayout(pw_form)

        self.lbl_pw_msg = QLabel("")
        self.lbl_pw_msg.setWordWrap(True)
        self.lbl_pw_msg.setVisible(False)
        self.pw_card.body_layout.addWidget(self.lbl_pw_msg)

        pw_btn_row = QHBoxLayout()
        pw_btn_row.addStretch(1)
        self.btn_change_pw = QPushButton("Cập nhật mật khẩu")
        set_button_variant(self.btn_change_pw, "teal", "md")
        self.btn_change_pw.clicked.connect(self._on_change_password)
        pw_btn_row.addWidget(self.btn_change_pw)
        self.pw_card.body_layout.addLayout(pw_btn_row)
        self.pw_card.body_layout.addStretch(1)

        cols.addWidget(self.profile_card, 3)
        cols.addWidget(self.pw_card, 2)
        root.addLayout(cols, 1)

    def set_role_mode(self, vai_tro: str) -> None:
        """Khóa chỉnh sửa hồ sơ nếu là Admin (4.1.7 chỉ xem)."""
        self.vai_tro = vai_tro
        is_admin = vai_tro == Roles.ADMIN.value
        self.admin_banner.setVisible(is_admin)
        for field in (
            self.txt_ho_ten,
            self.txt_cccd,
            self.txt_phone,
            self.txt_email,
            self.txt_dia_chi,
        ):
            field.setReadOnly(is_admin)
        self.btn_save_profile.setEnabled(not is_admin)
        self.btn_save_profile.setVisible(not is_admin)

    def apply_profile(self, profile: Dict[str, Any]) -> None:
        """Đổ dữ liệu hồ sơ từ Server lên các ô hiển thị."""
        self._profile = dict(profile)
        vai_tro = str(profile.get("vai_tro") or self.vai_tro)
        self.set_role_mode(vai_tro)

        self.lbl_username.setText(str(profile.get("ten_dang_nhap") or "-"))
        status = str(profile.get("trang_thai") or "hoat_dong")
        self.badge_status.setText(status)
        self.badge_status.set_variant("success" if status == "hoat_dong" else "danger")

        if profile.get("ma_can_ho"):
            unit = f"Căn hộ {profile['ma_can_ho']} (Ô tô: {profile.get('so_cho_oto_mac_dinh', 1)} mặc định + {profile.get('so_cho_oto_mua_them', 0)} mua thêm)"
        elif profile.get("ma_nhan_vien_code"):
            unit = f"{profile['ma_nhan_vien_code']} · {profile.get('chuc_vu') or ''}"
        else:
            unit = "Quản trị hệ thống (Read-Only)"
        self.lbl_unit.setText(unit)

        self.txt_ho_ten.setText(str(profile.get("ho_ten") or ""))
        self.txt_cccd.setText(str(profile.get("so_cccd") or ""))
        self.txt_phone.setText(str(profile.get("so_dien_thoai") or ""))
        self.txt_email.setText(str(profile.get("email") or ""))
        self.txt_dia_chi.setText(str(profile.get("dia_chi") or ""))

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(Actions.AUTH_GET_PROFILE, {})
        if resp.get("ok") and resp.get("data"):
            self.apply_profile(resp["data"].get("profile") or {})

    def _on_save_profile(self) -> None:
        if self.vai_tro == Roles.ADMIN.value:
            return
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.AUTH_UPDATE_PROFILE,
            {
                "ho_ten": self.txt_ho_ten.text().strip(),
                "so_cccd": self.txt_cccd.text().strip(),
                "so_dien_thoai": self.txt_phone.text().strip(),
                "email": self.txt_email.text().strip(),
                "dia_chi": self.txt_dia_chi.text().strip(),
            },
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(
                self.window(),
                str(resp.get("error") or "Không cập nhật được hồ sơ."),
                variant="danger",
            )
            return
        self.apply_profile((resp.get("data") or {}).get("profile") or {})
        ToastNotification.show_toast(self.window(), "Đã lưu thông tin hồ sơ cá nhân.", variant="success")

    def _on_change_password(self) -> None:
        self.lbl_pw_msg.setVisible(False)
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.AUTH_CHANGE_PASSWORD,
            {
                "mat_khau_hien_tai": self.txt_old_pw.text(),
                "mat_khau_moi": self.txt_new_pw.text(),
                "xac_nhan_mat_khau": self.txt_confirm_pw.text(),
            },
        )
        if not resp.get("ok"):
            self.lbl_pw_msg.setStyleSheet(f"color: {Colors.ERROR_TEXT}; font-size: 12px; font-weight: 600; border: none;")
            self.lbl_pw_msg.setText(str(resp.get("error") or "Đổi mật khẩu thất bại."))
            self.lbl_pw_msg.setVisible(True)
            return

        self.txt_old_pw.clear()
        self.txt_new_pw.clear()
        self.txt_confirm_pw.clear()
        self.lbl_pw_msg.setStyleSheet(f"color: {Colors.SUCCESS_TEXT}; font-size: 12px; font-weight: 600; border: none;")
        self.lbl_pw_msg.setText("Đổi mật khẩu thành công.")
        self.lbl_pw_msg.setVisible(True)

    def _on_logout_clicked(self) -> None:
        if self.socket_client is not None and self.socket_client.is_connected():
            self.socket_client.call(Actions.AUTH_LOGOUT, {})
        self.logout_requested.emit()
