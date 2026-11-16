"""Giao diện Quản lý Phương tiện & Upload Ảnh Cà-vẹt của Cư dân (3.2.1–3.2.5 — Workflow 02)."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.manager.slot_picker_dialog import ZoomableCavetDialog
from client.ui.theme.components import (
    SectionCard,
    StatCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
    style_table,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.constants import VehicleRequestTypes, VehicleStatus, VehicleTypes
from shared.protocol import Actions
from shared.validators import encode_image_base64, validate_license_plate

_REQ_TYPE_LABELS = {
    "dang_ky_moi": "Đăng ký mới",
    "chinh_sua_thong_tin": "Chỉnh sửa thông tin",
    "ngung_dang_ky": "Ngừng đăng ký",
}


class VehicleRequestDialog(QDialog):
    """Hộp thoại gửi yêu cầu Đăng ký xe mới (3.2.1) hoặc Chỉnh sửa thông tin xe (3.2.4)."""

    def __init__(
        self,
        vehicle: Optional[Dict[str, Any]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._vehicle = vehicle
        self._is_edit = vehicle is not None
        self._cavet_b64: str = ""

        self.setWindowTitle(
            "Chỉnh sửa thông tin xe (3.2.4)" if self._is_edit else "Đăng ký phương tiện mới (3.2.1)"
        )
        self.setMinimumWidth(520)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.cbo_type = QComboBox()
        self.cbo_type.addItem("Ô tô (O_TO)", VehicleTypes.O_TO.value)
        self.cbo_type.addItem("Xe máy (XE_MAY)", VehicleTypes.XE_MAY.value)
        if self._is_edit and vehicle:
            idx = self.cbo_type.findData(vehicle.get("ma_loai_xe"))
            if idx >= 0:
                self.cbo_type.setCurrentIndex(idx)
            self.cbo_type.setEnabled(False)

        self.txt_plate = QLineEdit(str((vehicle or {}).get("bien_so_xe") or ""))
        self.txt_plate.setPlaceholderText("VD: 51H-888.99 (Ô tô) hoặc 59A1-123.45 (Xe máy)")
        self.lbl_plate_hint = QLabel("")
        self.lbl_plate_hint.setStyleSheet(f"font-size: 11.5px; color: {Colors.TEXT_MUTED};")

        self.txt_brand = QLineEdit(str((vehicle or {}).get("ten_hang_dong_xe") or ""))
        self.txt_brand.setPlaceholderText("VD: Toyota Camry 2.5Q / Honda SH 150i")

        self.txt_color = QLineEdit(str((vehicle or {}).get("mau_xe") or ""))
        self.txt_color.setPlaceholderText("VD: Đen, Trắng, Xám...")

        self.txt_note = QLineEdit()
        self.txt_note.setPlaceholderText(
            "Lý do chỉnh sửa hoặc vị trí mong muốn (VD: Gần thang máy A1)"
        )

        file_row = QHBoxLayout()
        self.lbl_file = QLabel(
            "Giữ ảnh cũ hoặc chọn ảnh mới (tùy chọn)"
            if self._is_edit
            else "Chưa chọn ảnh cà-vẹt (.jpg, .png)"
        )
        self.lbl_file.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_SECONDARY};")
        self.btn_pick_file = QPushButton("Chọn ảnh Cà-vẹt...")
        set_button_variant(self.btn_pick_file, "default", "sm")
        self.btn_pick_file.clicked.connect(self._pick_cavet_file)
        file_row.addWidget(self.lbl_file, 1)
        file_row.addWidget(self.btn_pick_file)

        self.lbl_preview = QLabel("Xem trước ảnh cà-vẹt")
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumHeight(140)
        self.lbl_preview.setStyleSheet(
            f"background-color: {Colors.BG_ELEVATED}; border: 1px dashed {Colors.BORDER_INPUT}; "
            f"border-radius: 8px; color: {Colors.TEXT_MUTED};"
        )

        form.addRow("Loại phương tiện (*):", self.cbo_type)
        form.addRow("Biển số xe (*):", self.txt_plate)
        form.addRow("", self.lbl_plate_hint)
        form.addRow("Hãng / Dòng xe (*):", self.txt_brand)
        form.addRow("Màu xe:", self.txt_color)
        form.addRow("Ghi chú / Mong muốn:", self.txt_note)
        form.addRow("Ảnh Cà-vẹt (*):" if not self._is_edit else "Ảnh Cà-vẹt mới:", file_row)
        root.addLayout(form)
        root.addWidget(self.lbl_preview)

        self.txt_plate.textChanged.connect(self._validate_plate_realtime)
        self.cbo_type.currentIndexChanged.connect(self._validate_plate_realtime)
        self._validate_plate_realtime()

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = QPushButton("Hủy")
        set_button_variant(btn_cancel, "default", "sm")
        btn_cancel.clicked.connect(self.reject)
        self.btn_submit = QPushButton("Gửi yêu cầu" if not self._is_edit else "Gửi yêu cầu sửa")
        set_button_variant(self.btn_submit, "primary", "md")
        self.btn_submit.clicked.connect(self._on_accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_submit)
        root.addLayout(btn_row)

    def _validate_plate_realtime(self) -> bool:
        raw = self.txt_plate.text().strip()
        if not raw:
            self.lbl_plate_hint.setText("Nhập biển số theo chuẩn Việt Nam (tự động chuẩn hóa).")
            self.lbl_plate_hint.setStyleSheet(f"font-size: 11.5px; color: {Colors.TEXT_MUTED};")
            return False
        ok, msg = validate_license_plate(raw, str(self.cbo_type.currentData() or ""))
        if ok:
            self.lbl_plate_hint.setText(f"Biển số hợp lệ: {msg}")
            self.lbl_plate_hint.setStyleSheet(f"font-size: 11.5px; font-weight: 600; color: {Colors.SUCCESS};")
            return True
        self.lbl_plate_hint.setText(msg)
        self.lbl_plate_hint.setStyleSheet(f"font-size: 11.5px; color: {Colors.ERROR};")
        return False

    def _pick_cavet_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh Giấy đăng ký xe (Cà-vẹt)",
            "",
            "Ảnh (*.jpg *.jpeg *.png)",
        )
        if not path:
            return
        self.set_cavet_file(path)

    def set_cavet_file(self, file_path: str) -> None:
        """Đọc file ảnh, mã hóa Base64 và hiển thị preview (tiện test & sử dụng)."""
        b64 = encode_image_base64(file_path, max_bytes=1_500_000)
        self._cavet_b64 = b64
        self.lbl_file.setText(Path(file_path).name)
        pix = QPixmap(file_path)
        if not pix.isNull():
            self.lbl_preview.setPixmap(
                pix.scaled(320, 140, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
            self.lbl_preview.setText("")

    def _on_accept(self) -> None:
        if not self._validate_plate_realtime():
            QMessageBox.warning(self, "Biển số không hợp lệ", self.lbl_plate_hint.text())
            return
        if not self.txt_brand.text().strip():
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập tên hãng / dòng xe.")
            return
        if not self._is_edit and not self._cavet_b64:
            QMessageBox.warning(self, "Thiếu ảnh cà-vẹt", "Vui lòng chọn ảnh cà-vẹt xe trước khi gửi.")
            return
        self.accept()

    def get_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "loai_yeu_cau": (
                VehicleRequestTypes.CHINH_SUA_THONG_TIN.value
                if self._is_edit
                else VehicleRequestTypes.DANG_KY_MOI.value
            ),
            "ma_loai_xe": self.cbo_type.currentData(),
            "bien_so_xe": self.txt_plate.text().strip(),
            "ten_hang_dong_xe": self.txt_brand.text().strip(),
            "mau_xe": self.txt_color.text().strip(),
        }
        if self._is_edit and self._vehicle:
            payload["ma_phuong_tien"] = self._vehicle["ma_phuong_tien"]
            payload["ghi_chu"] = self.txt_note.text().strip()
        else:
            payload["vi_tri_mong_muon"] = self.txt_note.text().strip()
        if self._cavet_b64:
            payload["cavet_image_base64"] = self._cavet_b64
        return payload


class ResidentVehicleView(QWidget):
    """Màn hình 3.2.1–3.2.5: Cư dân đăng ký xe mới, sửa thông tin, ngừng đăng ký & theo dõi duyệt."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._quota: Dict[str, Any] = {}
        self._vehicles: List[Dict[str, Any]] = []
        self._requests: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Quản lý Phương tiện Cư dân (3.2)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        lbl_sub = QLabel(
            "Đăng ký xe mới kèm ảnh cà-vẹt, chỉnh sửa thông tin, ngừng đăng ký và theo dõi trạng thái duyệt"
        )
        lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        header.addLayout(title_col, 1)

        self.btn_refresh = QPushButton("Làm mới")
        set_button_variant(self.btn_refresh, "default", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)

        self.btn_register = QPushButton("+ Đăng ký xe mới (3.2.1)")
        set_button_variant(self.btn_register, "primary", "md")
        self.btn_register.clicked.connect(self._open_register_dialog)

        header.addWidget(self.btn_refresh)
        header.addWidget(self.btn_register)
        root.addLayout(header)

        # Quota KPI cards
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self.kpi_default_oto = StatCard("Suất Ô tô mặc định", "1", "Theo căn hộ", Colors.ROYAL_BLUE)
        self.kpi_extra_oto = StatCard("Suất Ô tô mua thêm", "0", "Đã được duyệt", Colors.INDIGO)
        self.kpi_active_oto = StatCard("Ô tô đang hoạt động", "0", "Trên tổng suất", Colors.SUCCESS)
        self.kpi_active_moto = StatCard("Xe máy đang hoạt động", "0", "Phân khu vực", Colors.EMERALD_TEAL)
        for card in (self.kpi_default_oto, self.kpi_extra_oto, self.kpi_active_oto, self.kpi_active_moto):
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        self.tabs = QTabWidget()

        # Tab 1: Danh sách xe đã đăng ký (3.2.2, 3.2.4, 3.2.5)
        tab_v = QWidget()
        lay_v = QVBoxLayout(tab_v)
        lay_v.setContentsMargins(0, 10, 0, 0)
        card_v = SectionCard(
            title="Danh sách Phương tiện đã Đăng ký (3.2.2)",
            subtitle="Chọn xe để xem cà-vẹt, gửi yêu cầu chỉnh sửa hoặc ngừng đăng ký",
        )
        bar_v = QHBoxLayout()
        self.btn_view_cavet = QPushButton("Xem ảnh Cà-vẹt")
        set_button_variant(self.btn_view_cavet, "default", "sm")
        self.btn_view_cavet.clicked.connect(self._view_vehicle_cavet)

        self.btn_edit_vehicle = QPushButton("Chỉnh sửa thông tin (3.2.4)")
        set_button_variant(self.btn_edit_vehicle, "teal", "sm")
        self.btn_edit_vehicle.clicked.connect(self._open_edit_dialog)

        self.btn_stop_vehicle = QPushButton("Ngừng đăng ký xe (3.2.5)")
        set_button_variant(self.btn_stop_vehicle, "danger", "sm")
        self.btn_stop_vehicle.clicked.connect(self._submit_stop_vehicle)

        bar_v.addStretch(1)
        bar_v.addWidget(self.btn_view_cavet)
        bar_v.addWidget(self.btn_edit_vehicle)
        bar_v.addWidget(self.btn_stop_vehicle)
        card_v.body_layout.addLayout(bar_v)

        self.tbl_vehicles = QTableWidget(0, 7)
        self.tbl_vehicles.setHorizontalHeaderLabels(
            ["Biển số", "Loại xe", "Hãng / Dòng xe", "Màu", "Vị trí / Khu vực đỗ", "Trong bãi", "Trạng thái"]
        )
        style_table(self.tbl_vehicles)
        self.tbl_vehicles.itemSelectionChanged.connect(self._sync_vehicle_actions)
        card_v.body_layout.addWidget(self.tbl_vehicles, 1)
        lay_v.addWidget(card_v, 1)
        self.tabs.addTab(tab_v, "Xe đã đăng ký (3.2.2)")

        # Tab 2: Theo dõi trạng thái yêu cầu (3.2.3)
        tab_r = QWidget()
        lay_r = QVBoxLayout(tab_r)
        lay_r.setContentsMargins(0, 10, 0, 0)
        card_r = SectionCard(
            title="Theo dõi Trạng thái Hồ sơ Yêu cầu (3.2.3)",
            subtitle="Lịch sử đăng ký mới, chỉnh sửa thông tin và ngừng đăng ký kèm phản hồi của Quản lý",
        )
        self.tbl_requests = QTableWidget(0, 7)
        self.tbl_requests.setHorizontalHeaderLabels(
            ["Mã YC", "Loại yêu cầu", "Biển số", "Dòng xe", "Ngày gửi", "Trạng thái", "Phản hồi / Lý do"]
        )
        style_table(self.tbl_requests)
        card_r.body_layout.addWidget(self.tbl_requests, 1)
        lay_r.addWidget(card_r, 1)
        self.tabs.addTab(tab_r, "Trạng thái Yêu cầu (3.2.3)")

        root.addWidget(self.tabs, 1)
        self._sync_vehicle_actions()

    # --------------------------------------------------------------------------
    # NẠP & RENDER DỮ LIỆU
    # --------------------------------------------------------------------------
    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(Actions.VEHICLE_RESIDENT_LIST, {})
        if resp.get("ok") and isinstance(resp.get("data"), dict):
            self.set_data(resp["data"])

    def set_data(self, data: Dict[str, Any]) -> None:
        self._quota = dict(data.get("quota") or {})
        self._vehicles = list(data.get("vehicles") or [])
        self._requests = list(data.get("requests") or [])

        self.kpi_default_oto.set_value(str(self._quota.get("so_cho_oto_mac_dinh", 1)))
        self.kpi_extra_oto.set_value(str(self._quota.get("so_cho_oto_mua_them", 0)))
        self.kpi_active_oto.set_value(
            f"{self._quota.get('so_oto_dang_hoat_dong', 0)}/{self._quota.get('tong_suat_oto', 1)}"
        )
        self.kpi_active_moto.set_value(str(self._quota.get("so_xe_may_dang_hoat_dong", 0)))

        self.tbl_vehicles.setRowCount(len(self._vehicles))
        for idx, v in enumerate(self._vehicles):
            self.tbl_vehicles.setItem(idx, 0, QTableWidgetItem(str(v.get("bien_so_xe") or "")))
            self.tbl_vehicles.setItem(idx, 1, QTableWidgetItem(str(v.get("ten_loai_xe") or v.get("ma_loai_xe") or "")))
            self.tbl_vehicles.setItem(idx, 2, QTableWidgetItem(str(v.get("ten_hang_dong_xe") or "")))
            self.tbl_vehicles.setItem(idx, 3, QTableWidgetItem(str(v.get("mau_xe") or "-")))

            loc = v.get("ma_vi_tri_co_dinh") or v.get("ten_khu_vuc") or v.get("ma_khu_vuc_phan_cong") or "Chờ phân bổ"
            self.tbl_vehicles.setItem(idx, 4, QTableWidgetItem(str(loc)))
            in_yard = "Đang trong bãi" if v.get("dang_trong_bai") else "Ngoài bãi"
            self.tbl_vehicles.setItem(idx, 5, QTableWidgetItem(in_yard))

            st = str(v.get("trang_thai_xe") or "")
            badge_map = {
                "dang_hoat_dong": ("Đang hoạt động", "success"),
                "cho_duyet": ("Chờ duyệt", "warning"),
                "ngung_hoat_dong": ("Ngừng hoạt động", "neutral"),
                "ngung_hieu_luc": ("Từ chối / Hết hiệu lực", "danger"),
            }
            lbl, var = badge_map.get(st, (st, "neutral"))
            self.tbl_vehicles.setCellWidget(idx, 6, StatusBadge(lbl, var))

        self.tbl_requests.setRowCount(len(self._requests))
        for idx, r in enumerate(self._requests):
            self.tbl_requests.setItem(idx, 0, QTableWidgetItem(f"#{r.get('ma_yeu_cau_xe', '')}"))
            loai = _REQ_TYPE_LABELS.get(str(r.get("loai_yeu_cau") or ""), str(r.get("loai_yeu_cau") or ""))
            self.tbl_requests.setItem(idx, 1, QTableWidgetItem(loai))
            self.tbl_requests.setItem(idx, 2, QTableWidgetItem(str(r.get("bien_so_xe") or "")))
            self.tbl_requests.setItem(idx, 3, QTableWidgetItem(str(r.get("ten_hang_dong_xe") or "")))
            self.tbl_requests.setItem(idx, 4, QTableWidgetItem(str(r.get("ngay_gui_yeu_cau") or "")))
            st = str(r.get("trang_thai_duyet") or "")
            st_lbl, st_var = {
                "cho_duyet": ("Chờ duyệt", "warning"),
                "da_duyet": ("Đã duyệt", "success"),
                "tu_choi": ("Từ chối", "danger"),
            }.get(st, (st, "neutral"))
            self.tbl_requests.setCellWidget(idx, 5, StatusBadge(st_lbl, st_var))
            self.tbl_requests.setItem(idx, 6, QTableWidgetItem(str(r.get("ly_do_phan_hoi") or "-")))

        if self._vehicles:
            self.tbl_vehicles.selectRow(0)
        self._sync_vehicle_actions()

    def _current_vehicle(self) -> Optional[Dict[str, Any]]:
        row = self.tbl_vehicles.currentRow()
        if row < 0 or row >= len(self._vehicles):
            return None
        return self._vehicles[row]

    def _sync_vehicle_actions(self) -> None:
        v = self._current_vehicle()
        if v is None:
            self.btn_view_cavet.setEnabled(False)
            self.btn_edit_vehicle.setEnabled(False)
            self.btn_stop_vehicle.setEnabled(False)
            return
        self.btn_view_cavet.setEnabled(True)
        is_active = v.get("trang_thai_xe") == VehicleStatus.DANG_HOAT_DONG.value
        has_pending = bool(v.get("co_yeu_cau_cho_duyet"))
        in_yard = bool(v.get("dang_trong_bai"))
        self.btn_edit_vehicle.setEnabled(is_active and not has_pending)
        self.btn_stop_vehicle.setEnabled(is_active and not has_pending and not in_yard)
        if in_yard:
            self.btn_stop_vehicle.setToolTip("Xe đang đỗ trong bãi, vui lòng đưa xe ra trước khi ngừng đăng ký.")
        else:
            self.btn_stop_vehicle.setToolTip("")

    # --------------------------------------------------------------------------
    # ACTIONS
    # --------------------------------------------------------------------------
    def _open_register_dialog(self) -> None:
        dlg = VehicleRequestDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self.socket_client is None:
            return
        resp = self.socket_client.call(Actions.VEHICLE_SUBMIT_REQUEST, dlg.get_payload(), timeout=10.0)
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error") or "Gửi yêu cầu thất bại."), variant="danger")
            return
        ToastNotification.show_toast(self.window(), "Đã gửi yêu cầu đăng ký xe mới thành công.", variant="success")
        self.refresh_data()
        self.tabs.setCurrentIndex(1)

    def _open_edit_dialog(self) -> None:
        v = self._current_vehicle()
        if v is None or self.socket_client is None:
            return
        dlg = VehicleRequestDialog(vehicle=v, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        resp = self.socket_client.call(Actions.VEHICLE_SUBMIT_REQUEST, dlg.get_payload(), timeout=10.0)
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error") or "Gửi yêu cầu thất bại."), variant="danger")
            return
        ToastNotification.show_toast(self.window(), "Đã gửi yêu cầu chỉnh sửa thông tin xe.", variant="success")
        self.refresh_data()
        self.tabs.setCurrentIndex(1)

    def _submit_stop_vehicle(self) -> None:
        v = self._current_vehicle()
        if v is None or self.socket_client is None:
            return
        reason, ok = QInputDialog.getText(
            self,
            "Ngừng đăng ký xe (3.2.5)",
            f"Nhập lý do ngừng đăng ký xe {v.get('bien_so_xe')}:",
        )
        if not ok:
            return
        resp = self.socket_client.call(
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": VehicleRequestTypes.NGUNG_DANG_KY.value,
                "ma_phuong_tien": v["ma_phuong_tien"],
                "ly_do": reason.strip() or "Cư dân không còn nhu cầu sử dụng",
            },
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error") or "Thao tác thất bại."), variant="danger")
            return
        ToastNotification.show_toast(self.window(), "Đã gửi yêu cầu ngừng đăng ký xe.", variant="info")
        self.refresh_data()
        self.tabs.setCurrentIndex(1)

    def _view_vehicle_cavet(self) -> None:
        v = self._current_vehicle()
        if v is None:
            return
        dlg = ZoomableCavetDialog(
            title=f"Ảnh Cà-vẹt xe {v.get('bien_so_xe', '')}",
            socket_client=self.socket_client,
            ma_phuong_tien=v.get("ma_phuong_tien"),
            duong_dan_cavet=str(v.get("duong_dan_cavet") or ""),
            parent=self,
        )
        dlg.exec()
