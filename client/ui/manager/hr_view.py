"""Giao diện Quản lý Nhân sự 1.3 (Cư dân & Nhân viên), hỗ trợ read_only cho Admin."""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
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
    ReadOnlyBanner,
    SectionCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
    style_table,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.protocol import Actions


class CreateResidentDialog(QDialog):
    """Hộp thoại 1.3.1: Tạo tài khoản cư dân (chọn căn hộ có sẵn hoặc nhập mã mới)."""

    def __init__(
        self,
        apartments: Optional[List[Dict[str, Any]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tạo tài khoản cư dân (1.3.1)")
        self.setMinimumWidth(460)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.txt_ho_ten = QLineEdit()
        self.txt_cccd = QLineEdit()
        self.txt_sdt = QLineEdit()
        self.txt_email = QLineEdit()
        self.txt_dia_chi = QLineEdit()

        self.cbo_can_ho = QComboBox()
        self.cbo_can_ho.setEditable(True)
        self.cbo_can_ho.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for apt in apartments or []:
            code = str(apt.get("ma_can_ho") or "")
            if code:
                self.cbo_can_ho.addItem(code, code)
        if self.cbo_can_ho.lineEdit() is not None:
            self.cbo_can_ho.lineEdit().setPlaceholderText("Chọn hoặc nhập mới VD: A12-05")

        self.txt_toa = QLineEdit()
        self.txt_toa.setPlaceholderText("Tùy chọn (tự tách nếu bỏ trống)")
        self.txt_tang = QLineEdit()
        self.txt_tang.setPlaceholderText("Tùy chọn (tự tách nếu bỏ trống)")

        self.txt_username = QLineEdit()
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setText("123456")

        form.addRow("Họ và tên (*):", self.txt_ho_ten)
        form.addRow("Số CCCD (*):", self.txt_cccd)
        form.addRow("Số điện thoại (*):", self.txt_sdt)
        form.addRow("Email (*):", self.txt_email)
        form.addRow("Mã căn hộ (*):", self.cbo_can_ho)
        form.addRow("Tòa nhà:", self.txt_toa)
        form.addRow("Số tầng:", self.txt_tang)
        form.addRow("Địa chỉ:", self.txt_dia_chi)
        form.addRow("Tên đăng nhập (*):", self.txt_username)
        form.addRow("Mật khẩu ban đầu (*):", self.txt_password)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Hủy")
        set_button_variant(btn_cancel, "default", "sm")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Tạo tài khoản")
        set_button_variant(btn_ok, "primary", "md")
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        root.addLayout(row)

    def get_payload(self) -> Dict[str, Any]:
        return {
            "ho_ten": self.txt_ho_ten.text().strip(),
            "so_cccd": self.txt_cccd.text().strip(),
            "so_dien_thoai": self.txt_sdt.text().strip(),
            "email": self.txt_email.text().strip(),
            "ma_can_ho": self.cbo_can_ho.currentText().strip().upper(),
            "toa_nha": self.txt_toa.text().strip() or None,
            "so_tang": self.txt_tang.text().strip() or None,
            "dia_chi": self.txt_dia_chi.text().strip(),
            "ten_dang_nhap": self.txt_username.text().strip(),
            "mat_khau_ban_dau": self.txt_password.text(),
        }


class ResidentDetailDialog(QDialog):
    """Hộp thoại 1.3.3: Xem chi tiết hồ sơ cư dân và định mức căn hộ."""

    def __init__(self, resident: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Chi tiết Cư dân — {resident.get('ho_ten', '')}")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        form = QFormLayout()
        for label, val in (
            ("Mã cư dân:", resident.get("ma_cu_dan")),
            ("Tên đăng nhập:", resident.get("ten_dang_nhap")),
            ("Họ và tên:", resident.get("ho_ten")),
            ("Căn hộ:", f"{resident.get('ma_can_ho', '-')} (Tòa {resident.get('toa_nha', '-')}, Tầng {resident.get('so_tang', '-')})"),
            ("Định mức ô tô:", f"{resident.get('so_cho_oto_mac_dinh', 1)} mặc định + {resident.get('so_cho_oto_mua_them', 0)} mua thêm"),
            ("Số CCCD:", resident.get("so_cccd")),
            ("Số điện thoại:", resident.get("so_dien_thoai")),
            ("Email:", resident.get("email")),
            ("Địa chỉ:", resident.get("dia_chi") or "-"),
            ("Trạng thái TK:", resident.get("trang_thai")),
            ("Lý do khóa:", resident.get("ly_do_khoa") or "Không có"),
        ):
            form.addRow(label, QLabel(str(val if val is not None else "-")))
        root.addLayout(form)

        btn_close = QPushButton("Đóng")
        set_button_variant(btn_close, "default", "sm")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)


class StaffFormDialog(QDialog):
    """Hộp thoại 1.3.6 / 1.3.8: Thêm mới hoặc Sửa hồ sơ nhân viên."""

    def __init__(
        self,
        staff: Optional[Dict[str, Any]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._is_edit = staff is not None
        self.setWindowTitle("Sửa hồ sơ nhân viên (1.3.8)" if self._is_edit else "Thêm nhân viên mới (1.3.6)")
        self.setMinimumWidth(450)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.txt_code = QLineEdit(str((staff or {}).get("ma_nhan_vien_code") or ""))
        self.txt_code.setReadOnly(self._is_edit)
        self.txt_ho_ten = QLineEdit(str((staff or {}).get("ho_ten") or ""))
        self.txt_cccd = QLineEdit(str((staff or {}).get("so_cccd") or ""))
        self.txt_sdt = QLineEdit(str((staff or {}).get("so_dien_thoai") or ""))
        self.txt_email = QLineEdit(str((staff or {}).get("email") or ""))

        self.cbo_chuc_vu = QComboBox()
        self.cbo_chuc_vu.addItem("Nhân viên bãi xe", "nhan_vien_bai_xe")
        self.cbo_chuc_vu.addItem("Quản lý chung cư", "quan_ly_chung_cu")
        if staff and staff.get("chuc_vu"):
            idx = self.cbo_chuc_vu.findData(staff["chuc_vu"])
            if idx >= 0:
                self.cbo_chuc_vu.setCurrentIndex(idx)

        self.txt_username = QLineEdit(str((staff or {}).get("ten_dang_nhap") or ""))
        self.txt_username.setReadOnly(self._is_edit)
        self.txt_password = QLineEdit("123456")
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Mã nhân viên (*):", self.txt_code)
        form.addRow("Họ và tên (*):", self.txt_ho_ten)
        form.addRow("Chức vụ (*):", self.cbo_chuc_vu)
        form.addRow("Số CCCD (*):", self.txt_cccd)
        form.addRow("Số điện thoại (*):", self.txt_sdt)
        form.addRow("Email (*):", self.txt_email)
        if not self._is_edit:
            form.addRow("Tên đăng nhập (*):", self.txt_username)
            form.addRow("Mật khẩu ban đầu (*):", self.txt_password)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Hủy")
        set_button_variant(btn_cancel, "default", "sm")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Lưu hồ sơ" if self._is_edit else "Thêm nhân viên")
        set_button_variant(btn_ok, "primary", "md")
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        root.addLayout(row)

    def get_payload(self, ma_nhan_vien: Optional[int] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ho_ten": self.txt_ho_ten.text().strip(),
            "chuc_vu": self.cbo_chuc_vu.currentData(),
            "so_cccd": self.txt_cccd.text().strip(),
            "so_dien_thoai": self.txt_sdt.text().strip(),
            "email": self.txt_email.text().strip(),
        }
        if self._is_edit:
            payload["ma_nhan_vien"] = ma_nhan_vien
        else:
            payload["ma_nhan_vien_code"] = self.txt_code.text().strip().upper()
            payload["ten_dang_nhap"] = self.txt_username.text().strip()
            payload["mat_khau_ban_dau"] = self.txt_password.text()
        return payload


class HRView(QWidget):
    """Màn hình 1.3 Quản lý Nhân sự (Cư dân & Nhân viên), hỗ trợ read_only cho Admin."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        read_only: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.read_only = read_only
        self._residents: List[Dict[str, Any]] = []
        self._apartments: List[Dict[str, Any]] = []
        self._staff_list: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        if self.read_only:
            root.addWidget(ReadOnlyBanner(self))

        title_lbl = QLabel("Quản lý Tài khoản Cư dân & Hồ sơ Nhân viên (1.3)")
        title_lbl.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        root.addWidget(title_lbl)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_resident_tab(), "Tài khoản Cư dân (1.3.1–1.3.5)")
        self.tabs.addTab(self._build_staff_tab(), "Hồ sơ Nhân viên (1.3.6–1.3.10)")
        root.addWidget(self.tabs, 1)

    def _mark_mutation(self, btn: QPushButton) -> QPushButton:
        btn.setProperty("is_mutation", True)
        if self.read_only:
            btn.setEnabled(False)
            btn.setVisible(False)
        return btn

    def _build_resident_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(12)

        card = SectionCard(
            title="Danh sách Tài khoản Cư dân",
            subtitle="Tạo tài khoản (tự động tạo căn hộ mới nếu chưa có), tra cứu và khóa/mở khóa",
        )

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.txt_res_search = QLineEdit()
        self.txt_res_search.setPlaceholderText("Tìm theo tên, mã căn hộ, SĐT, email...")
        self.cbo_res_status = QComboBox()
        self.cbo_res_status.addItem("Tất cả trạng thái", "")
        self.cbo_res_status.addItem("Hoạt động", "hoat_dong")
        self.cbo_res_status.addItem("Tạm khóa", "tam_khoa")

        self.btn_res_search = QPushButton("Tìm kiếm (1.3.2)")
        set_button_variant(self.btn_res_search, "default", "sm")
        self.btn_res_search.clicked.connect(self.refresh_residents)

        self.btn_res_detail = QPushButton("Xem chi tiết (1.3.3)")
        set_button_variant(self.btn_res_detail, "teal", "sm")
        self.btn_res_detail.clicked.connect(self._view_selected_resident)

        self.btn_res_create = self._mark_mutation(QPushButton("+ Tạo cư dân (1.3.1)"))
        set_button_variant(self.btn_res_create, "primary", "sm")
        self.btn_res_create.clicked.connect(self._create_resident)

        self.btn_res_deactivate = self._mark_mutation(QPushButton("Ngừng hoạt động (1.3.4)"))
        set_button_variant(self.btn_res_deactivate, "danger", "sm")
        self.btn_res_deactivate.clicked.connect(self._deactivate_resident)

        self.btn_res_activate = self._mark_mutation(QPushButton("Kích hoạt lại (1.3.5)"))
        set_button_variant(self.btn_res_activate, "success", "sm")
        self.btn_res_activate.clicked.connect(self._activate_resident)

        bar.addWidget(self.txt_res_search, 2)
        bar.addWidget(self.cbo_res_status, 1)
        bar.addWidget(self.btn_res_search)
        bar.addWidget(self.btn_res_detail)
        bar.addWidget(self.btn_res_create)
        bar.addWidget(self.btn_res_deactivate)
        bar.addWidget(self.btn_res_activate)
        card.body_layout.addLayout(bar)

        self.tbl_residents = QTableWidget(0, 7)
        self.tbl_residents.setHorizontalHeaderLabels(
            ["Mã CĐ", "Tên đăng nhập", "Họ tên", "Căn hộ", "SĐT", "Email", "Trạng thái"]
        )
        style_table(self.tbl_residents)
        self.tbl_residents.cellDoubleClicked.connect(lambda _r, _c: self._view_selected_resident())
        card.body_layout.addWidget(self.tbl_residents, 1)
        lay.addWidget(card, 1)
        return host

    def _build_staff_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(12)

        card = SectionCard(
            title="Danh sách Hồ sơ Nhân viên & Quản lý",
            subtitle="Quản lý mã nhân sự, chức vụ, cập nhật hồ sơ và khóa/mở khóa tài khoản",
        )

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.txt_staff_search = QLineEdit()
        self.txt_staff_search.setPlaceholderText("Tìm theo tên, mã NV, tên đăng nhập...")
        self.cbo_staff_pos = QComboBox()
        self.cbo_staff_pos.addItem("Tất cả chức vụ", "")
        self.cbo_staff_pos.addItem("Nhân viên bãi xe", "nhan_vien_bai_xe")
        self.cbo_staff_pos.addItem("Quản lý chung cư", "quan_ly_chung_cu")

        self.btn_staff_search = QPushButton("Tìm / Làm mới (1.3.7)")
        set_button_variant(self.btn_staff_search, "default", "sm")
        self.btn_staff_search.clicked.connect(self.refresh_staff)

        self.btn_staff_create = self._mark_mutation(QPushButton("+ Thêm nhân viên (1.3.6)"))
        set_button_variant(self.btn_staff_create, "primary", "sm")
        self.btn_staff_create.clicked.connect(self._create_staff)

        self.btn_staff_edit = self._mark_mutation(QPushButton("Sửa hồ sơ (1.3.8)"))
        set_button_variant(self.btn_staff_edit, "teal", "sm")
        self.btn_staff_edit.clicked.connect(self._update_staff)

        self.btn_staff_deactivate = self._mark_mutation(QPushButton("Ngừng hoạt động (1.3.9)"))
        set_button_variant(self.btn_staff_deactivate, "danger", "sm")
        self.btn_staff_deactivate.clicked.connect(self._deactivate_staff)

        self.btn_staff_activate = self._mark_mutation(QPushButton("Kích hoạt lại (1.3.10)"))
        set_button_variant(self.btn_staff_activate, "success", "sm")
        self.btn_staff_activate.clicked.connect(self._activate_staff)

        bar.addWidget(self.txt_staff_search, 2)
        bar.addWidget(self.cbo_staff_pos, 1)
        bar.addWidget(self.btn_staff_search)
        bar.addWidget(self.btn_staff_create)
        bar.addWidget(self.btn_staff_edit)
        bar.addWidget(self.btn_staff_deactivate)
        bar.addWidget(self.btn_staff_activate)
        card.body_layout.addLayout(bar)

        self.tbl_staff = QTableWidget(0, 7)
        self.tbl_staff.setHorizontalHeaderLabels(
            ["Mã NV", "Tên đăng nhập", "Họ tên", "Chức vụ", "SĐT", "Email", "Trạng thái"]
        )
        style_table(self.tbl_staff)
        card.body_layout.addWidget(self.tbl_staff, 1)
        lay.addWidget(card, 1)
        return host

    def refresh_data(self) -> None:
        self.refresh_residents()
        self.refresh_staff()

    def refresh_residents(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.HR_SEARCH_RESIDENTS,
            {
                "keyword": self.txt_res_search.text().strip(),
                "trang_thai": self.cbo_res_status.currentData(),
            },
        )
        if resp.get("ok") and resp.get("data"):
            data = resp["data"]
            self._apartments = data.get("apartments") or []
            self.set_residents(data.get("items") or [])

    def set_residents(self, items: List[Dict[str, Any]]) -> None:
        self._residents = items
        self.tbl_residents.setRowCount(len(items))
        for idx, r in enumerate(items):
            status = str(r.get("trang_thai") or "hoat_dong")
            self.tbl_residents.setItem(idx, 0, QTableWidgetItem(str(r.get("ma_cu_dan") or "")))
            self.tbl_residents.setItem(idx, 1, QTableWidgetItem(str(r.get("ten_dang_nhap") or "")))
            self.tbl_residents.setItem(idx, 2, QTableWidgetItem(str(r.get("ho_ten") or "")))
            self.tbl_residents.setItem(idx, 3, QTableWidgetItem(str(r.get("ma_can_ho") or "")))
            self.tbl_residents.setItem(idx, 4, QTableWidgetItem(str(r.get("so_dien_thoai") or "")))
            self.tbl_residents.setItem(idx, 5, QTableWidgetItem(str(r.get("email") or "")))
            self.tbl_residents.setCellWidget(
                idx, 6, StatusBadge(status, "success" if status == "hoat_dong" else "danger")
            )

    def refresh_staff(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.HR_LIST_STAFF,
            {
                "keyword": self.txt_staff_search.text().strip(),
                "chuc_vu": self.cbo_staff_pos.currentData(),
            },
        )
        if resp.get("ok") and resp.get("data"):
            self.set_staff_list(resp["data"].get("items") or [])

    def set_staff_list(self, items: List[Dict[str, Any]]) -> None:
        self._staff_list = items
        self.tbl_staff.setRowCount(len(items))
        for idx, s in enumerate(items):
            status = str(s.get("trang_thai") or "hoat_dong")
            self.tbl_staff.setItem(idx, 0, QTableWidgetItem(str(s.get("ma_nhan_vien_code") or "")))
            self.tbl_staff.setItem(idx, 1, QTableWidgetItem(str(s.get("ten_dang_nhap") or "")))
            self.tbl_staff.setItem(idx, 2, QTableWidgetItem(str(s.get("ho_ten") or "")))
            self.tbl_staff.setItem(idx, 3, QTableWidgetItem(str(s.get("chuc_vu") or "")))
            self.tbl_staff.setItem(idx, 4, QTableWidgetItem(str(s.get("so_dien_thoai") or "")))
            self.tbl_staff.setItem(idx, 5, QTableWidgetItem(str(s.get("email") or "")))
            self.tbl_staff.setCellWidget(
                idx, 6, StatusBadge(status, "success" if status == "hoat_dong" else "danger")
            )

    def _selected_resident(self) -> Optional[Dict[str, Any]]:
        row = self.tbl_residents.currentRow()
        if row < 0 or row >= len(self._residents):
            return None
        return self._residents[row]

    def _selected_staff(self) -> Optional[Dict[str, Any]]:
        row = self.tbl_staff.currentRow()
        if row < 0 or row >= len(self._staff_list):
            return None
        return self._staff_list[row]

    def _call_and_notify(self, action: str, payload: Dict[str, Any], success_msg: str) -> bool:
        if self.socket_client is None or not self.socket_client.is_connected():
            return False
        resp = self.socket_client.call(action, payload)
        if not resp.get("ok"):
            ToastNotification.show_toast(
                self.window(),
                str(resp.get("error") or "Thao tác thất bại."),
                variant="danger",
            )
            return False
        ToastNotification.show_toast(self.window(), success_msg, variant="success")
        return True

    def _create_resident(self) -> None:
        dlg = CreateResidentDialog(apartments=self._apartments, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self._call_and_notify(Actions.HR_CREATE_RESIDENT, dlg.get_payload(), "Đã tạo tài khoản cư dân mới."):
            self.refresh_residents()

    def _view_selected_resident(self) -> None:
        res = self._selected_resident()
        if res is None:
            return
        if self.socket_client is not None and self.socket_client.is_connected():
            resp = self.socket_client.call(
                Actions.HR_GET_RESIDENT, {"ma_cu_dan": res.get("ma_cu_dan")}
            )
            if resp.get("ok") and resp.get("data"):
                res = resp["data"].get("resident") or res
        ResidentDetailDialog(res, parent=self).exec()

    def _deactivate_resident(self) -> None:
        res = self._selected_resident()
        if res is None:
            return
        reason, ok = QInputDialog.getText(self, "Khóa tài khoản cư dân", "Nhập lý do ngừng hoạt động:")
        if not ok or not reason.strip():
            return
        if self._call_and_notify(
            Actions.HR_DEACTIVATE_RESIDENT,
            {"ma_tai_khoan": res["ma_tai_khoan"], "ly_do_khoa": reason.strip()},
            "Đã ngừng hoạt động tài khoản cư dân.",
        ):
            self.refresh_residents()

    def _activate_resident(self) -> None:
        res = self._selected_resident()
        if res is None:
            return
        if self._call_and_notify(
            Actions.HR_ACTIVATE_RESIDENT,
            {"ma_tai_khoan": res["ma_tai_khoan"]},
            "Đã kích hoạt lại tài khoản cư dân.",
        ):
            self.refresh_residents()

    def _create_staff(self) -> None:
        dlg = StaffFormDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self._call_and_notify(Actions.HR_CREATE_STAFF, dlg.get_payload(), "Đã thêm nhân viên mới."):
            self.refresh_staff()

    def _update_staff(self) -> None:
        staff = self._selected_staff()
        if staff is None:
            return
        dlg = StaffFormDialog(staff=staff, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self._call_and_notify(
            Actions.HR_UPDATE_STAFF,
            dlg.get_payload(ma_nhan_vien=staff["ma_nhan_vien"]),
            "Đã cập nhật hồ sơ nhân viên.",
        ):
            self.refresh_staff()

    def _deactivate_staff(self) -> None:
        staff = self._selected_staff()
        if staff is None:
            return
        reason, ok = QInputDialog.getText(self, "Khóa tài khoản nhân viên", "Nhập lý do ngừng hoạt động:")
        if not ok or not reason.strip():
            return
        if self._call_and_notify(
            Actions.HR_DEACTIVATE_STAFF,
            {"ma_tai_khoan": staff["ma_tai_khoan"], "ly_do_khoa": reason.strip()},
            "Đã ngừng hoạt động tài khoản nhân viên.",
        ):
            self.refresh_staff()

    def _activate_staff(self) -> None:
        staff = self._selected_staff()
        if staff is None:
            return
        if self._call_and_notify(
            Actions.HR_ACTIVATE_STAFF,
            {"ma_tai_khoan": staff["ma_tai_khoan"]},
            "Đã kích hoạt lại tài khoản nhân viên.",
        ):
            self.refresh_staff()
