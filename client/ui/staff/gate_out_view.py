"""Màn hình 2.3 Kiểm soát Cổng Ra, Đối chiếu ảnh Vào/Ra & Thu phí Vãng lai (Thành viên 5: Ny)."""

import base64
import os
from typing import Any, Dict, Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from client.ai.camera_worker import CameraWorker, bgr_to_qimage, encode_bgr_to_base64
from client.ai.plate_recognizer import PlateRecognitionWorker, PlateRecognizer
from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    SectionCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.constants import PaymentMethods, VehicleTypes
from shared.protocol import Actions
from shared.validators import normalize_plate


class VisitorQRDialog(QDialog):
    """Hộp thoại hiển thị mã QR thu phí vãng lai kích thước lớn để khách quét ngay tại chốt."""

    def __init__(
        self,
        plate: str,
        amount: float,
        qr_base64: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Mã QR Thu Phí Vãng Lai — {plate}")
        self.setModal(True)
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        lbl_title = QLabel(f"QUÉT MÃ QR THANH TOÁN — {plate}")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {Colors.TEXT_PRIMARY};")
        root.addWidget(lbl_title)

        lbl_amount = QLabel(f"{int(amount):,} VNĐ")
        lbl_amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_amount.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 24px; font-weight: 800; color: {Colors.ROYAL_BLUE};"
        )
        root.addWidget(lbl_amount)

        lbl_qr = QLabel()
        lbl_qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if qr_base64:
            raw = base64.b64decode(qr_base64)
            pix = QPixmap()
            pix.loadFromData(raw)
            lbl_qr.setPixmap(pix.scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            lbl_qr.setText("Không có dữ liệu mã QR.")
        root.addWidget(lbl_qr)

        btn_ok = QPushButton("Đóng cửa sổ QR")
        set_button_variant(btn_ok, "primary", "md")
        btn_ok.clicked.connect(self.accept)
        root.addWidget(btn_ok)


class GateOutView(QWidget):
    """Màn hình 2.3.1–2.3.3 (Workflow 04): Kiểm soát Cổng Ra & Thu phí Vãng lai."""

    gate_exit_completed = Signal(dict)

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        recognizer: Optional[PlateRecognizer] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._recognizer = recognizer or PlateRecognizer()
        self._camera = CameraWorker(camera_index=0, parent=self)
        self._ocr_worker: Optional[PlateRecognitionWorker] = None

        self._captured_bgr: Optional[np.ndarray] = None
        self._out_data: Optional[Dict[str, Any]] = None

        self._build_ui()
        self._bind_signals()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Kiểm soát Cổng Ra & Thu Phí Vãng Lai (2.3)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_sub = QLabel("Đối chiếu trực quan ảnh Vào / Ra · Tự động tính phí theo giờ hoặc lượt")
        self.lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        header.addLayout(title_col, 1)

        self.cbo_camera = QComboBox()
        self.cbo_camera.addItem("Webcam 0 (Mặc định)", 0)
        self.cbo_camera.addItem("Webcam 1 (Cổng ra phụ)", 1)
        header.addWidget(self.cbo_camera)

        self.btn_toggle_cam = QPushButton("Bật Camera")
        set_button_variant(self.btn_toggle_cam, "teal", "sm")
        header.addWidget(self.btn_toggle_cam)

        self.btn_load_sample = QPushButton("Nạp ảnh từ máy")
        set_button_variant(self.btn_load_sample, "default", "sm")
        header.addWidget(self.btn_load_sample)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)

        # ---------------- CỘT TRÁI: WEBCAM CỔNG RA & ĐỐI CHIẾU ẢNH ----------------
        left_card = SectionCard(
            title="2.3.1 Live Webcam Cổng Ra & Đối Chiếu Ảnh Vào / Ra",
            subtitle="Chụp biển số ra và đối chiếu trực tiếp với ảnh lúc vào bãi",
        )

        self.lbl_video = QLabel("Camera Cổng Ra sẵn sàng — Bấm 'Bật Camera' hoặc 'Nạp ảnh từ máy'")
        self.lbl_video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_video.setMinimumSize(480, 240)
        self.lbl_video.setStyleSheet(
            f"background-color: {Colors.PRIMARY}; color: {Colors.SIDEBAR_TEXT}; "
            "border-radius: 10px; font-size: 13px; font-weight: 600;"
        )
        left_card.body_layout.addWidget(self.lbl_video, 1)

        ctrl_row = QHBoxLayout()
        self.btn_capture_ocr = QPushButton("CHỤP & NHẬN DIỆN CỔNG RA (Space)")
        set_button_variant(self.btn_capture_ocr, "primary", "md")
        ctrl_row.addWidget(self.btn_capture_ocr, 1)

        self.badge_engine = StatusBadge("Chưa nhận diện", "neutral")
        ctrl_row.addWidget(self.badge_engine)
        left_card.body_layout.addLayout(ctrl_row)

        plate_row = QHBoxLayout()
        self.txt_plate = QLineEdit()
        self.txt_plate.setPlaceholderText("Biển số xe ra (VD: 51H-888.99)")
        self.txt_plate.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.txt_plate.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 20px; font-weight: 800; "
            f"background-color: #FFFFFF; color: {Colors.TEXT_PRIMARY}; "
            f"border: 2px solid {Colors.BORDER_INPUT}; border-radius: 8px; padding: 4px 10px;"
        )
        plate_row.addWidget(self.txt_plate, 1)

        self.btn_recheck = QPushButton("Đối chiếu xe")
        set_button_variant(self.btn_recheck, "teal", "md")
        plate_row.addWidget(self.btn_recheck)
        left_card.body_layout.addLayout(plate_row)

        # Hàng 2 ảnh đối chiếu Vào / Ra
        compare_row = QHBoxLayout()
        compare_row.setSpacing(10)

        in_box = QVBoxLayout()
        lbl_in_cap = QLabel("Ảnh chụp lúc VÀO bãi")
        lbl_in_cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_in_cap.setStyleSheet(f"font-size: 11.5px; font-weight: 700; color: {Colors.TEXT_SECONDARY};")
        self.lbl_img_in = QLabel("Chưa có ảnh vào")
        self.lbl_img_in.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_img_in.setMinimumSize(210, 125)
        self.lbl_img_in.setStyleSheet(
            f"background-color: {Colors.BG_APP}; color: {Colors.TEXT_MUTED}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 8px; font-size: 11.5px;"
        )
        in_box.addWidget(lbl_in_cap)
        in_box.addWidget(self.lbl_img_in)
        compare_row.addLayout(in_box, 1)

        out_box = QVBoxLayout()
        lbl_out_cap = QLabel("Ảnh chụp lúc RA bãi")
        lbl_out_cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_out_cap.setStyleSheet(f"font-size: 11.5px; font-weight: 700; color: {Colors.TEXT_SECONDARY};")
        self.lbl_img_out = QLabel("Chưa có ảnh ra")
        self.lbl_img_out.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_img_out.setMinimumSize(210, 125)
        self.lbl_img_out.setStyleSheet(
            f"background-color: {Colors.BG_APP}; color: {Colors.TEXT_MUTED}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: 8px; font-size: 11.5px;"
        )
        out_box.addWidget(lbl_out_cap)
        out_box.addWidget(self.lbl_img_out)
        compare_row.addLayout(out_box, 1)

        left_card.body_layout.addLayout(compare_row)
        body.addWidget(left_card, 55)

        # ---------------- CỘT PHẢI: TÍNH PHÍ & XÁC NHẬN MỞ CỔNG ----------------
        right_card = SectionCard(
            title="2.3.2 – 2.3.3 Đối Chiếu Phiên Gửi & Thu Phí",
            subtitle="Tự động áp dụng miễn phí cho Cư dân hoặc tính tiền theo bảng giá Vãng lai",
        )

        self.banner_frame = QFrame()
        self.banner_frame.setStyleSheet(
            f"background-color: {Colors.BG_ELEVATED}; border: 1px solid {Colors.BORDER}; border-radius: 10px;"
        )
        b_lay = QVBoxLayout(self.banner_frame)
        b_lay.setContentsMargins(14, 10, 14, 10)
        b_lay.setSpacing(3)
        self.lbl_status_title = QLabel("CHỜ ĐỐI CHIẾU XE RA CỔNG")
        self.lbl_status_title.setStyleSheet(
            f"font-size: 14px; font-weight: 800; color: {Colors.TEXT_PRIMARY}; border: none;"
        )
        self.lbl_status_desc = QLabel("Chụp từ Webcam hoặc nhập biển số để tính thời lượng và cước phí.")
        self.lbl_status_desc.setWordWrap(True)
        self.lbl_status_desc.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_SECONDARY}; border: none;")
        b_lay.addWidget(self.lbl_status_title)
        b_lay.addWidget(self.lbl_status_desc)
        right_card.body_layout.addWidget(self.banner_frame)

        info_form = QFormLayout()
        info_form.setSpacing(8)
        self.lbl_guest_type = QLabel("-")
        self.lbl_owner_info = QLabel("-")
        self.lbl_slot_info = QLabel("-")
        self.lbl_time_in = QLabel("-")
        self.lbl_time_out = QLabel("-")
        self.lbl_duration = QLabel("-")
        self.lbl_duration.setStyleSheet("font-weight: 700;")
        info_form.addRow("Phân loại khách:", self.lbl_guest_type)
        info_form.addRow("Chủ xe / Căn hộ:", self.lbl_owner_info)
        info_form.addRow("Vị trí / Khu vực:", self.lbl_slot_info)
        info_form.addRow("Giờ vào bãi:", self.lbl_time_in)
        info_form.addRow("Giờ ra dự kiến:", self.lbl_time_out)
        info_form.addRow("Thời gian lưu bãi:", self.lbl_duration)
        right_card.body_layout.addLayout(info_form)

        # Khung Tổng tiền phải thu
        fee_box = QFrame()
        fee_box.setStyleSheet(
            f"background-color: {Colors.ROYAL_BLUE_SOFT}; border: 1px solid #BFDBFE; border-radius: 10px;"
        )
        fee_lay = QVBoxLayout(fee_box)
        fee_lay.setContentsMargins(14, 12, 14, 12)
        fee_lay.setSpacing(4)
        lbl_fee_cap = QLabel("TỔNG TIỀN CẦN THU TẠI CỔNG")
        lbl_fee_cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_fee_cap.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {Colors.ROYAL_BLUE}; border: none;")
        self.lbl_fee_amount = QLabel("0 VNĐ")
        self.lbl_fee_amount.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_fee_amount.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 28px; font-weight: 800; color: {Colors.TEXT_PRIMARY}; border: none;"
        )
        self.lbl_fee_formula = QLabel("Đơn giá áp dụng: -")
        self.lbl_fee_formula.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_fee_formula.setWordWrap(True)
        self.lbl_fee_formula.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_SECONDARY}; border: none;")
        fee_lay.addWidget(lbl_fee_cap)
        fee_lay.addWidget(self.lbl_fee_amount)
        fee_lay.addWidget(self.lbl_fee_formula)
        right_card.body_layout.addWidget(fee_box)

        # Chọn phương thức thanh toán cho khách vãng lai
        self.pay_box = QFrame()
        self.pay_box.setStyleSheet(
            f"background-color: {Colors.BG_APP}; border: 1px solid {Colors.BORDER}; border-radius: 10px;"
        )
        pay_lay = QHBoxLayout(self.pay_box)
        pay_lay.setContentsMargins(12, 10, 12, 10)
        pay_lay.setSpacing(10)

        self.rad_cash = QRadioButton("Tiền mặt")
        self.rad_cash.setChecked(True)
        self.rad_qr = QRadioButton("Chuyển khoản QR")
        self._pay_group = QButtonGroup(self)
        self._pay_group.addButton(self.rad_cash)
        self._pay_group.addButton(self.rad_qr)

        self.btn_show_qr = QPushButton("Hiện mã QR")
        set_button_variant(self.btn_show_qr, "primary", "sm")
        self.btn_show_qr.setEnabled(False)

        pay_lay.addWidget(QLabel("Thanh toán:"))
        pay_lay.addWidget(self.rad_cash)
        pay_lay.addWidget(self.rad_qr)
        pay_lay.addStretch(1)
        pay_lay.addWidget(self.btn_show_qr)
        right_card.body_layout.addWidget(self.pay_box)

        barie_row = QHBoxLayout()
        barie_row.addWidget(QLabel("Trạng thái Barie Cổng Ra:"))
        barie_row.addStretch(1)
        self.badge_barrier = StatusBadge("ĐANG ĐÓNG", "neutral")
        barie_row.addWidget(self.badge_barrier)
        right_card.body_layout.addLayout(barie_row)

        self.btn_confirm_exit = QPushButton("XÁC NHẬN & MỞ CỔNG RA (Enter)")
        self.btn_confirm_exit.setEnabled(False)
        set_button_variant(self.btn_confirm_exit, "success", "lg")
        right_card.body_layout.addWidget(self.btn_confirm_exit)

        body.addWidget(right_card, 45)
        root.addLayout(body, 1)

        self._shortcut_space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._shortcut_space.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        self._shortcut_enter.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _bind_signals(self) -> None:
        self._camera.frame_ready.connect(self._on_camera_frame)
        self._camera.camera_error.connect(self._on_camera_error)
        self._camera.camera_started.connect(self._on_camera_started)
        self._camera.camera_stopped.connect(self._on_camera_stopped)

        self.btn_toggle_cam.clicked.connect(self._toggle_camera)
        self.btn_load_sample.clicked.connect(self._on_load_sample_image)
        self.btn_capture_ocr.clicked.connect(self.capture_and_recognize)
        self.btn_recheck.clicked.connect(self.check_current_plate)
        self.btn_show_qr.clicked.connect(self._open_qr_dialog)
        self.rad_qr.toggled.connect(self._on_payment_method_changed)
        self.btn_confirm_exit.clicked.connect(self.confirm_gate_out)

        self._shortcut_space.activated.connect(self.capture_and_recognize)
        self._shortcut_enter.activated.connect(self._on_enter_shortcut)
        self.txt_plate.returnPressed.connect(self.check_current_plate)

    # --------------------------------------------------------------------------
    # QUẢN LÝ CAMERA
    # --------------------------------------------------------------------------
    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not self._camera.isRunning():
            self.start_camera()

    def hideEvent(self, event: Any) -> None:
        self.stop_camera()
        super().hideEvent(event)

    def start_camera(self) -> None:
        cam_idx = int(self.cbo_camera.currentData() or 0)
        self._camera.start_camera(cam_idx)

    def stop_camera(self) -> None:
        self._camera.stop_camera()

    def _toggle_camera(self) -> None:
        if self._camera.isRunning():
            self.stop_camera()
        else:
            self.start_camera()

    def _on_camera_started(self, idx: int) -> None:
        self.btn_toggle_cam.setText("Tắt Camera")
        set_button_variant(self.btn_toggle_cam, "danger", "sm")
        self.lbl_sub.setText(f"Webcam Cổng Ra {idx} đang hoạt động 30 FPS")

    def _on_camera_stopped(self) -> None:
        self.btn_toggle_cam.setText("Bật Camera")
        set_button_variant(self.btn_toggle_cam, "teal", "sm")

    def _on_camera_error(self, message: str) -> None:
        self.lbl_video.setText(f"{message}\n\nBạn vẫn có thể bấm 'Nạp ảnh từ máy' hoặc nhập biển số.")
        self._on_camera_stopped()

    def _on_camera_frame(self, qimg: QImage, _frame_bgr: Any) -> None:
        if qimg.isNull():
            return
        annotated = qimg.copy()
        painter = QPainter(annotated)
        w, h = annotated.width(), annotated.height()
        box_w, box_h = int(w * 0.52), int(h * 0.34)
        x1, y1 = (w - box_w) // 2, (h - box_h) // 2
        painter.setPen(QPen(QColor(Colors.ROYAL_BLUE), 2, Qt.PenStyle.DashLine))
        painter.drawRect(x1, y1, box_w, box_h)
        painter.end()

        pix = QPixmap.fromImage(annotated).scaled(
            self.lbl_video.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.lbl_video.setPixmap(pix)

    def _on_load_sample_image(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh biển số cổng ra",
            "",
            "Ảnh (*.jpg *.jpeg *.png *.bmp)",
        )
        if not file_path:
            return
        img = cv2.imread(file_path)
        if img is None:
            ToastNotification.show_toast(self, "Không thể đọc file ảnh đã chọn.", variant="danger")
            return
        self._camera.set_mock_frame(img)
        self._set_captured_out_frame(img)
        self._run_ocr_on_frame(img)

    def _set_captured_out_frame(self, frame_bgr: np.ndarray) -> None:
        self._captured_bgr = frame_bgr.copy()
        qimg = bgr_to_qimage(self._captured_bgr)
        if not qimg.isNull():
            pix = QPixmap.fromImage(qimg).scaled(
                self.lbl_img_out.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.lbl_img_out.setPixmap(pix)

    # --------------------------------------------------------------------------
    # CHỤP & NHẬN DIỆN AI CỔNG RA
    # --------------------------------------------------------------------------
    def capture_and_recognize(self) -> None:
        if self._ocr_worker is not None and self._ocr_worker.isRunning():
            return
        frame = self._camera.capture_snapshot()
        if frame is None:
            ToastNotification.show_toast(
                self,
                "Chưa có khung hình Webcam. Hãy bật Camera hoặc nạp ảnh từ máy.",
                variant="warning",
            )
            return
        self._set_captured_out_frame(frame)
        self._run_ocr_on_frame(frame)

    def _run_ocr_on_frame(self, frame_bgr: np.ndarray) -> None:
        self.btn_capture_ocr.setEnabled(False)
        self.btn_capture_ocr.setText("ĐANG NHẬN DIỆN AI...")
        self.badge_engine.setText("Đang phân tích...")
        self.badge_engine.set_variant("info")

        self._ocr_worker = PlateRecognitionWorker(frame_bgr, recognizer=self._recognizer, parent=self)
        self._ocr_worker.recognition_finished.connect(self._on_ocr_finished)
        self._ocr_worker.start()

    def _on_ocr_finished(self, result: Dict[str, Any]) -> None:
        self.btn_capture_ocr.setEnabled(True)
        self.btn_capture_ocr.setText("CHỤP & NHẬN DIỆN CỔNG RA (Space)")

        engine = str(result.get("engine_used") or "AI OCR")
        conf_pct = int(float(result.get("confidence") or 0.0) * 100)
        plate = str(result.get("bien_so") or "").strip()
        if result.get("success") and plate:
            self.badge_engine.setText(f"{engine} · {conf_pct}%")
            self.badge_engine.set_variant("success")
            self.txt_plate.setText(plate)
            self.check_current_plate()
        else:
            self.badge_engine.setText("Không đọc rõ — Nhập tay")
            self.badge_engine.set_variant("warning")
            ToastNotification.show_toast(self, "AI chưa đọc rõ biển số cổng ra, vui lòng nhập tay.", variant="warning")
            self.txt_plate.setFocus()

    # --------------------------------------------------------------------------
    # ĐỐI CHIẾU & TÍNH PHÍ CỔNG RA
    # --------------------------------------------------------------------------
    def check_current_plate(self) -> None:
        plate = normalize_plate(self.txt_plate.text())
        if not plate:
            ToastNotification.show_toast(self, "Vui lòng nhập biển số xe.", variant="warning")
            return
        self.txt_plate.setText(plate)

        if self.socket_client is None or not self.socket_client.is_connected():
            ToastNotification.show_toast(self, "Chưa kết nối tới Server.", variant="danger")
            return

        resp = self.socket_client.call(Actions.GATE_OUT_CHECK, {"bien_so": plate})
        if not resp.get("ok"):
            self._apply_error_state(str(resp.get("error") or "Không tìm thấy xe trong bãi."))
            return
        self.apply_out_check_data(resp.get("data") or {})

    def _apply_error_state(self, err_msg: str) -> None:
        self._out_data = None
        self.btn_confirm_exit.setEnabled(False)
        self.banner_frame.setStyleSheet(
            f"background-color: {Colors.ERROR_BG}; border: 1px solid {Colors.ERROR_BORDER}; border-radius: 10px;"
        )
        self.lbl_status_title.setText("KHÔNG TÌM THẤY PHIÊN GỬI XE")
        self.lbl_status_title.setStyleSheet(f"font-size: 14px; font-weight: 800; color: {Colors.ERROR_TEXT}; border: none;")
        self.lbl_status_desc.setText(err_msg)
        self.lbl_fee_amount.setText("0 VNĐ")
        self.lbl_fee_formula.setText("-")
        self.lbl_img_in.clear()
        self.lbl_img_in.setText("Chưa có ảnh vào")
        ToastNotification.show_toast(self, err_msg, variant="danger")

    def apply_out_check_data(self, data: Dict[str, Any]) -> None:
        self._out_data = dict(data)
        loai_khach = str(data.get("loai_khach") or "vang_lai")
        so_tien = float(data.get("so_tien_phai_thu") or 0)
        self.badge_barrier.setText("ĐANG ĐÓNG")
        self.badge_barrier.set_variant("neutral")

        # Hiển thị ảnh cổng vào để đối chiếu trực quan
        in_b64 = str(data.get("anh_cong_vao_base64") or "").strip()
        if in_b64:
            try:
                raw = base64.b64decode(in_b64)
                pix = QPixmap()
                if pix.loadFromData(raw):
                    self.lbl_img_in.setPixmap(
                        pix.scaled(
                            self.lbl_img_in.size(),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
            except Exception:
                self.lbl_img_in.setText("Lỗi hiển thị ảnh vào")
        else:
            self.lbl_img_in.clear()
            self.lbl_img_in.setText("Xe vào chưa lưu ảnh")

        vtype_txt = "Ô tô" if data.get("ma_loai_xe") == VehicleTypes.O_TO.value else "Xe máy"
        slot_txt = str(data.get("ten_vi_tri") or data.get("ma_vi_tri") or "")
        zone_txt = str(data.get("ten_khu_vuc") or data.get("ma_khu_vuc") or "")
        self.lbl_slot_info.setText(f"{slot_txt} · {zone_txt}".strip(" ·") or "-")
        self.lbl_time_in.setText(str(data.get("thoi_gian_vao") or "-"))
        self.lbl_time_out.setText(str(data.get("thoi_gian_ra") or "-"))
        self.lbl_duration.setText(str(data.get("thoi_luong_text") or "-"))
        self.lbl_fee_amount.setText(f"{int(so_tien):,} VNĐ")
        self.lbl_fee_formula.setText(str(data.get("cong_thuc_text") or "-"))

        if loai_khach == "cu_dan":
            owner = data.get("chu_xe") or {}
            self.lbl_guest_type.setText(f"Xe Cư dân ({vtype_txt})")
            self.lbl_owner_info.setText(f"{owner.get('ho_ten', '-')} · Căn hộ {owner.get('ma_can_ho', '-')}")
            self.banner_frame.setStyleSheet(
                f"background-color: {Colors.SUCCESS_BG}; border: 1px solid {Colors.SUCCESS_BORDER}; border-radius: 10px;"
            )
            self.lbl_status_title.setText("XE CƯ DÂN — MIỄN THU PHÍ LƯỢT (0 VNĐ)")
            self.lbl_status_title.setStyleSheet(
                f"font-size: 14px; font-weight: 800; color: {Colors.SUCCESS_TEXT}; border: none;"
            )
            self.lbl_status_desc.setText("Đã bao gồm trong hóa đơn phí gửi xe hàng tháng của căn hộ.")
            self.pay_box.setEnabled(False)
            self.btn_show_qr.setEnabled(False)
            self.btn_confirm_exit.setText("MỞ CỔNG CHO RA (Enter)")
        else:
            self.lbl_guest_type.setText(f"Khách Vãng lai ({vtype_txt})")
            self.lbl_owner_info.setText("Khách vãng lai (Ticketless)")
            self.banner_frame.setStyleSheet(
                f"background-color: {Colors.WARNING_BG}; border: 1px solid {Colors.WARNING_BORDER}; border-radius: 10px;"
            )
            self.lbl_status_title.setText("KHÁCH VÃNG LAI — THU PHÍ LƯỢT TRƯỚC KHI MỞ CỔNG")
            self.lbl_status_title.setStyleSheet(
                f"font-size: 14px; font-weight: 800; color: {Colors.WARNING_TEXT}; border: none;"
            )
            self.lbl_status_desc.setText("Chọn Tiền mặt hoặc Quét mã QR và xác nhận thu đủ tiền.")
            self.pay_box.setEnabled(True)
            self.btn_show_qr.setEnabled(bool(data.get("qr_image_base64")))
            self.btn_confirm_exit.setText("XÁC NHẬN THU PHÍ & MỞ CỔNG (Enter)")

        self.btn_confirm_exit.setEnabled(True)

    def _on_payment_method_changed(self, checked: bool) -> None:
        if checked and self._out_data and self._out_data.get("loai_khach") == "vang_lai":
            if self._out_data.get("qr_image_base64"):
                self._open_qr_dialog()

    def _open_qr_dialog(self) -> None:
        if not self._out_data:
            return
        VisitorQRDialog(
            plate=str(self._out_data.get("bien_so_xe") or ""),
            amount=float(self._out_data.get("so_tien_phai_thu") or 0),
            qr_base64=str(self._out_data.get("qr_image_base64") or ""),
            parent=self,
        ).exec()

    def _on_enter_shortcut(self) -> None:
        if self.btn_confirm_exit.isEnabled() and not self.txt_plate.hasFocus():
            self.confirm_gate_out()

    def confirm_gate_out(self) -> None:
        if not self._out_data:
            return
        if self.socket_client is None or not self.socket_client.is_connected():
            ToastNotification.show_toast(self, "Chưa kết nối tới Server.", variant="danger")
            return

        plate = normalize_plate(self.txt_plate.text() or str(self._out_data.get("bien_so_xe") or ""))
        method = (
            PaymentMethods.CHUYEN_KHOAN_QR.value
            if self.rad_qr.isChecked()
            else PaymentMethods.TIEN_MAT.value
        )
        payload = {
            "bien_so": plate,
            "loai_khach": self._out_data.get("loai_khach"),
            "phuong_thuc_thanh_toan": method,
            "gate_out_image_base64": encode_bgr_to_base64(self._captured_bgr),
        }

        self.btn_confirm_exit.setEnabled(False)
        self.badge_barrier.setText("ĐANG MỞ BARIE...")
        self.badge_barrier.set_variant("info")

        resp = self.socket_client.call(Actions.GATE_OUT_CONFIRM, payload)
        if not resp.get("ok"):
            self.badge_barrier.setText("LỖI MỞ CỔNG")
            self.badge_barrier.set_variant("danger")
            self.btn_confirm_exit.setEnabled(True)
            ToastNotification.show_toast(self, str(resp.get("error") or "Không thể xác nhận xe ra."), variant="danger")
            return

        data = resp.get("data") or {}
        self.badge_barrier.setText("ĐÃ QUA CỔNG")
        self.badge_barrier.set_variant("success")
        ToastNotification.show_toast(
            self,
            str(data.get("message") or f"Đã mở cổng ra cho xe {plate}."),
            variant="success",
            duration_ms=4000,
        )
        self.gate_exit_completed.emit(data.get("exit") or {})
        QTimer.singleShot(1500, self._reset_after_exit)

    def _reset_after_exit(self) -> None:
        self._out_data = None
        self._captured_bgr = None
        self.txt_plate.clear()
        self.lbl_img_in.clear()
        self.lbl_img_in.setText("Chưa có ảnh vào")
        self.lbl_img_out.clear()
        self.lbl_img_out.setText("Chưa có ảnh ra")
        self.badge_engine.setText("Sẵn sàng lượt mới")
        self.badge_engine.set_variant("neutral")
        self.badge_barrier.setText("ĐANG ĐÓNG")
        self.badge_barrier.set_variant("neutral")
        self.lbl_guest_type.setText("-")
        self.lbl_owner_info.setText("-")
        self.lbl_slot_info.setText("-")
        self.lbl_time_in.setText("-")
        self.lbl_time_out.setText("-")
        self.lbl_duration.setText("-")
        self.lbl_fee_amount.setText("0 VNĐ")
        self.lbl_fee_formula.setText("Đơn giá áp dụng: -")
        self.banner_frame.setStyleSheet(
            f"background-color: {Colors.BG_ELEVATED}; border: 1px solid {Colors.BORDER}; border-radius: 10px;"
        )
        self.lbl_status_title.setText("CHỜ ĐỐI CHIẾU XE RA CỔNG")
        self.lbl_status_title.setStyleSheet(
            f"font-size: 14px; font-weight: 800; color: {Colors.TEXT_PRIMARY}; border: none;"
        )
        self.lbl_status_desc.setText("Chụp từ Webcam hoặc nhập biển số để tính thời lượng và cước phí.")
        self.btn_confirm_exit.setEnabled(False)
        self.btn_show_qr.setEnabled(False)
