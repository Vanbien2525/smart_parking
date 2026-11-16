"""Màn hình 2.2 Kiểm soát Cổng Vào Ticketless bằng Live Webcam & AI Nhận diện biển số (Thành viên 5: Ny)."""

import os
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
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
from shared.constants import VehicleTypes
from shared.protocol import Actions
from shared.validators import normalize_plate


class GateInView(QWidget):
    """Màn hình 2.2.1–2.2.3.2 (Workflow 03): Kiểm soát Cổng Vào Ticketless."""

    gate_entry_completed = Signal(dict)

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
        self._check_data: Optional[Dict[str, Any]] = None
        self._available_car_slots: List[Dict[str, Any]] = []
        self._available_moto_zones: List[Dict[str, Any]] = []

        self._build_ui()
        self._bind_signals()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # Header
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Kiểm soát Cổng Vào Ticketless — Camera AI (2.2)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_sub = QLabel("Định danh 100% bằng biển số xe · Tự động phân nhánh Cư dân / Khách vãng lai")
        self.lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        header.addLayout(title_col, 1)

        self.cbo_camera = QComboBox()
        self.cbo_camera.addItem("Webcam 0 (Mặc định)", 0)
        self.cbo_camera.addItem("Webcam 1 (Cổng phụ)", 1)
        header.addWidget(self.cbo_camera)

        self.btn_toggle_cam = QPushButton("Bật Camera")
        set_button_variant(self.btn_toggle_cam, "teal", "sm")
        header.addWidget(self.btn_toggle_cam)

        self.btn_load_sample = QPushButton("Nạp ảnh từ máy")
        set_button_variant(self.btn_load_sample, "default", "sm")
        header.addWidget(self.btn_load_sample)
        root.addLayout(header)

        # Split-Screen 2 cột: 55% trái — 45% phải
        body = QHBoxLayout()
        body.setSpacing(16)

        # ---------------- CỘT TRÁI: LIVE WEBCAM & AI OCR (2.2.1) ----------------
        left_card = SectionCard(
            title="2.2.1 Luồng Live Webcam & AI Đọc Biển Số",
            subtitle="Đưa biển số vào khung ngắm và nhấn Space hoặc bấm nút Chụp & Nhận Diện",
        )

        self.lbl_video = QLabel("Camera đang sẵn sàng — Bấm 'Bật Camera' hoặc 'Nạp ảnh từ máy'")
        self.lbl_video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_video.setMinimumSize(480, 310)
        self.lbl_video.setStyleSheet(
            f"background-color: {Colors.PRIMARY}; color: {Colors.SIDEBAR_TEXT}; "
            "border-radius: 10px; font-size: 13px; font-weight: 600;"
        )
        left_card.body_layout.addWidget(self.lbl_video, 1)

        self.btn_capture_ocr = QPushButton("CHỤP & NHẬN DIỆN BIỂN SỐ (Space)")
        set_button_variant(self.btn_capture_ocr, "primary", "lg")
        left_card.body_layout.addWidget(self.btn_capture_ocr)

        # Kết quả AI OCR & chỉnh sửa nhanh
        ocr_box = QFrame()
        ocr_box.setStyleSheet(
            f"QFrame {{ background-color: {Colors.BG_APP}; border: 1px solid {Colors.BORDER}; border-radius: 10px; }}"
        )
        ocr_lay = QHBoxLayout(ocr_box)
        ocr_lay.setContentsMargins(12, 10, 12, 10)
        ocr_lay.setSpacing(12)

        self.lbl_plate_crop = QLabel("Ảnh cắt\nbiển số")
        self.lbl_plate_crop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_plate_crop.setFixedSize(150, 78)
        self.lbl_plate_crop.setStyleSheet(
            f"background-color: #FFFFFF; color: {Colors.TEXT_MUTED}; "
            f"border: 1px dashed {Colors.BORDER_INPUT}; border-radius: 8px; font-size: 11px;"
        )
        ocr_lay.addWidget(self.lbl_plate_crop)

        plate_col = QVBoxLayout()
        plate_col.setSpacing(6)

        badge_row = QHBoxLayout()
        self.badge_engine = StatusBadge("Chưa nhận diện", "neutral")
        self.lbl_elapsed = QLabel("")
        self.lbl_elapsed.setStyleSheet(f"font-size: 11.5px; color: {Colors.TEXT_MUTED}; border: none;")
        badge_row.addWidget(self.badge_engine)
        badge_row.addStretch(1)
        badge_row.addWidget(self.lbl_elapsed)
        plate_col.addLayout(badge_row)

        edit_row = QHBoxLayout()
        self.txt_plate = QLineEdit()
        self.txt_plate.setPlaceholderText("VD: 51H-888.99")
        self.txt_plate.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.txt_plate.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 22px; font-weight: 800; "
            f"letter-spacing: 1px; background-color: #FFFFFF; color: {Colors.TEXT_PRIMARY}; "
            f"border: 2px solid {Colors.BORDER_INPUT}; border-radius: 8px; padding: 4px 10px;"
        )
        edit_row.addWidget(self.txt_plate, 1)

        self.btn_recheck = QPushButton("Tra cứu lại")
        set_button_variant(self.btn_recheck, "teal", "md")
        edit_row.addWidget(self.btn_recheck)
        plate_col.addLayout(edit_row)

        ocr_lay.addLayout(plate_col, 1)
        left_card.body_layout.addWidget(ocr_box)
        body.addWidget(left_card, 55)

        # ---------------- CỘT PHẢI: PHÂN LOẠI & MỞ CỔNG (2.2.2 - 2.2.3.2) ----------------
        right_card = SectionCard(
            title="2.2.2 – 2.2.3 Phân Loại Tự Động & Điều Khiển Barie",
            subtitle="Hệ thống tự động kiểm tra chống vào trùng (Anti-Passback) và gợi ý vị trí",
        )

        self.banner_frame = QFrame()
        self.banner_frame.setStyleSheet(
            f"background-color: {Colors.BG_ELEVATED}; border: 1px solid {Colors.BORDER}; border-radius: 10px;"
        )
        banner_lay = QVBoxLayout(self.banner_frame)
        banner_lay.setContentsMargins(14, 10, 14, 10)
        banner_lay.setSpacing(3)
        self.lbl_branch_title = QLabel("CHỜ NHẬN DIỆN BIỂN SỐ")
        self.lbl_branch_title.setStyleSheet(
            f"font-size: 14px; font-weight: 800; color: {Colors.TEXT_PRIMARY}; border: none;"
        )
        self.lbl_branch_desc = QLabel("Chụp từ Webcam hoặc nhập biển số để phân nhánh Cư dân / Vãng lai.")
        self.lbl_branch_desc.setWordWrap(True)
        self.lbl_branch_desc.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_SECONDARY}; border: none;")
        banner_lay.addWidget(self.lbl_branch_title)
        banner_lay.addWidget(self.lbl_branch_desc)
        right_card.body_layout.addWidget(self.banner_frame)

        # Stacked detail panel: 0=Waiting, 1=Resident (2.2.3.1), 2=Visitor (2.2.3.2)
        self.detail_stack = QStackedWidget()

        # Page 0: Waiting
        wait_page = QWidget()
        wait_lay = QVBoxLayout(wait_page)
        wait_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_wait = QLabel("Chưa có dữ liệu đối chiếu.\nHãy chụp ảnh biển số hoặc bấm 'Tra cứu lại'.")
        lbl_wait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_wait.setStyleSheet(f"font-size: 13px; color: {Colors.TEXT_MUTED};")
        wait_lay.addWidget(lbl_wait)
        self.detail_stack.addWidget(wait_page)

        # Page 1: Resident (2.2.3.1)
        res_page = QWidget()
        res_form = QFormLayout(res_page)
        res_form.setSpacing(10)
        self.lbl_res_owner = QLabel("-")
        self.lbl_res_owner.setStyleSheet("font-weight: 700; font-size: 13.5px;")
        self.lbl_res_apt = QLabel("-")
        self.lbl_res_phone = QLabel("-")
        self.lbl_res_type = QLabel("-")
        self.lbl_res_slot = QLabel("-")
        self.lbl_res_slot.setStyleSheet(f"font-family: {Fonts.MONO_FAMILY}; font-weight: 700; color: {Colors.ROYAL_BLUE};")
        self.lbl_res_zone = QLabel("-")
        res_form.addRow("Chủ phương tiện:", self.lbl_res_owner)
        res_form.addRow("Căn hộ:", self.lbl_res_apt)
        res_form.addRow("Số điện thoại:", self.lbl_res_phone)
        res_form.addRow("Loại xe:", self.lbl_res_type)
        res_form.addRow("Vị trí cố định:", self.lbl_res_slot)
        res_form.addRow("Khu vực:", self.lbl_res_zone)
        self.detail_stack.addWidget(res_page)

        # Page 2: Visitor Ticketless (2.2.3.2)
        vis_page = QWidget()
        vis_form = QFormLayout(vis_page)
        vis_form.setSpacing(10)
        self.cbo_vis_type = QComboBox()
        self.cbo_vis_type.addItem("Ô tô (O_TO)", VehicleTypes.O_TO.value)
        self.cbo_vis_type.addItem("Xe máy (XE_MAY)", VehicleTypes.XE_MAY.value)
        self.cbo_vis_assign = QComboBox()
        self.lbl_vis_ticketless = QLabel("Định danh trực tiếp bằng biển số (Không phát thẻ nhựa)")
        self.lbl_vis_ticketless.setWordWrap(True)
        self.lbl_vis_ticketless.setStyleSheet(f"color: {Colors.INFO_TEXT}; font-weight: 600;")
        vis_form.addRow("Loại phương tiện:", self.cbo_vis_type)
        vis_form.addRow("Gán ô đỗ / khu vực:", self.cbo_vis_assign)
        vis_form.addRow("Hình thức:", self.lbl_vis_ticketless)
        self.detail_stack.addWidget(vis_page)

        right_card.body_layout.addWidget(self.detail_stack, 1)

        # Trạng thái Barie & Nút mở cổng
        barie_box = QFrame()
        barie_box.setStyleSheet(
            f"background-color: {Colors.BG_APP}; border: 1px solid {Colors.BORDER}; border-radius: 10px;"
        )
        barie_lay = QHBoxLayout(barie_box)
        barie_lay.setContentsMargins(12, 10, 12, 10)
        lbl_b_title = QLabel("Trạng thái Barie Cổng Vào:")
        lbl_b_title.setStyleSheet("font-weight: 600; border: none;")
        self.badge_barrier = StatusBadge("ĐANG ĐÓNG", "neutral")
        barie_lay.addWidget(lbl_b_title)
        barie_lay.addStretch(1)
        barie_lay.addWidget(self.badge_barrier)
        right_card.body_layout.addWidget(barie_box)

        self.btn_open_gate = QPushButton("MỞ CỔNG CHO VÀO (Enter)")
        self.btn_open_gate.setEnabled(False)
        set_button_variant(self.btn_open_gate, "success", "lg")
        right_card.body_layout.addWidget(self.btn_open_gate)

        body.addWidget(right_card, 45)
        root.addLayout(body, 1)

        # Phím tắt Space (chụp) và Enter (mở cổng)
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
        self.btn_open_gate.clicked.connect(self.confirm_gate_in)
        self.cbo_vis_type.currentIndexChanged.connect(self._populate_visitor_targets)

        self._shortcut_space.activated.connect(self.capture_and_recognize)
        self._shortcut_enter.activated.connect(self._on_enter_shortcut)
        self.txt_plate.returnPressed.connect(self.check_current_plate)

    # --------------------------------------------------------------------------
    # QUẢN LÝ VÒNG ĐỜI CAMERA (TRÁNH XUNG ĐỘT CAMERA GIỮA CỔNG VÀO / CỔNG RA)
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
        self.lbl_sub.setText(f"Webcam {idx} đang hoạt động 30 FPS · Sẵn sàng chụp biển số")

    def _on_camera_stopped(self) -> None:
        self.btn_toggle_cam.setText("Bật Camera")
        set_button_variant(self.btn_toggle_cam, "teal", "sm")

    def _on_camera_error(self, message: str) -> None:
        self.lbl_video.setText(f"{message}\n\nBạn vẫn có thể bấm 'Nạp ảnh từ máy' hoặc nhập tay biển số.")
        self._on_camera_stopped()

    def _on_camera_frame(self, qimg: QImage, _frame_bgr: Any) -> None:
        if qimg.isNull():
            return
        # Vẽ khung ngắm Viewfinder ở trung tâm khung hình
        annotated = qimg.copy()
        painter = QPainter(annotated)
        w, h = annotated.width(), annotated.height()
        box_w, box_h = int(w * 0.52), int(h * 0.34)
        x1, y1 = (w - box_w) // 2, (h - box_h) // 2
        pen = QPen(QColor(Colors.SUCCESS), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
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
            "Chọn ảnh biển số xe",
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
        self._captured_bgr = img
        self._run_ocr_on_frame(img)

    # --------------------------------------------------------------------------
    # CHỤP FRAME & CHẠY AI OCR BẤT ĐỒNG BỘ
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
        self._captured_bgr = frame
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
        self.btn_capture_ocr.setText("CHỤP & NHẬN DIỆN BIỂN SỐ (Space)")

        crop_qimg = result.get("plate_crop_qimage")
        if isinstance(crop_qimg, QImage) and not crop_qimg.isNull():
            pix = QPixmap.fromImage(crop_qimg).scaled(
                self.lbl_plate_crop.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.lbl_plate_crop.setPixmap(pix)

        engine = str(result.get("engine_used") or "AI OCR")
        conf_pct = int(float(result.get("confidence") or 0.0) * 100)
        elapsed_ms = int(result.get("elapsed_ms") or 0)
        self.lbl_elapsed.setText(f"{elapsed_ms} ms")

        plate = str(result.get("bien_so") or "").strip()
        if result.get("success") and plate:
            self.badge_engine.setText(f"{engine} · {conf_pct}%")
            self.badge_engine.set_variant("success")
            self.txt_plate.setText(plate)
            self.check_current_plate(detected_type=str(result.get("loai_xe") or ""))
        else:
            self.badge_engine.setText("Không đọc rõ — Nhập tay")
            self.badge_engine.set_variant("warning")
            ToastNotification.show_toast(
                self,
                "AI chưa đọc rõ biển số, vui lòng nhập hoặc chỉnh sửa lại.",
                variant="warning",
            )
            self.txt_plate.setFocus()

    # --------------------------------------------------------------------------
    # ĐỐI CHIẾU SERVER & XÁC NHẬN MỞ CỔNG VÀO
    # --------------------------------------------------------------------------
    def check_current_plate(self, detected_type: str = "") -> None:
        plate = normalize_plate(self.txt_plate.text())
        if not plate:
            ToastNotification.show_toast(self, "Vui lòng nhập biển số xe.", variant="warning")
            return
        self.txt_plate.setText(plate)

        if self.socket_client is None or not self.socket_client.is_connected():
            ToastNotification.show_toast(self, "Chưa kết nối tới Server.", variant="danger")
            return

        payload: Dict[str, Any] = {"bien_so": plate}
        if detected_type:
            payload["loai_xe"] = detected_type

        resp = self.socket_client.call(Actions.GATE_IN_CHECK, payload)
        if not resp.get("ok"):
            self._apply_error_state(str(resp.get("error") or "Không thể kiểm tra biển số."))
            return
        self.apply_check_result(resp.get("data") or {})

    def _apply_error_state(self, error_msg: str) -> None:
        self._check_data = None
        self.btn_open_gate.setEnabled(False)
        self.detail_stack.setCurrentIndex(0)
        self.banner_frame.setStyleSheet(
            f"background-color: {Colors.ERROR_BG}; border: 1px solid {Colors.ERROR_BORDER}; border-radius: 10px;"
        )
        self.lbl_branch_title.setText("CẢNH BÁO KHÔNG CHO VÀO BÃI")
        self.lbl_branch_title.setStyleSheet(f"font-size: 14px; font-weight: 800; color: {Colors.ERROR_TEXT}; border: none;")
        self.lbl_branch_desc.setText(error_msg)
        ToastNotification.show_toast(self, error_msg, variant="danger", duration_ms=4500)

    def apply_check_result(self, data: Dict[str, Any]) -> None:
        """Hiển thị kết quả phân nhánh Cư dân (2.2.3.1) hoặc Vãng lai (2.2.3.2)."""
        self._check_data = dict(data)
        branch = str(data.get("phan_nhanh") or data.get("loai_khach") or "vang_lai")
        self.badge_barrier.setText("ĐANG ĐÓNG")
        self.badge_barrier.set_variant("neutral")

        if branch == "cu_dan":
            self.banner_frame.setStyleSheet(
                f"background-color: {Colors.SUCCESS_BG}; border: 1px solid {Colors.SUCCESS_BORDER}; border-radius: 10px;"
            )
            self.lbl_branch_title.setText("XE ĐÃ ĐĂNG KÝ CỦA CƯ DÂN (2.2.3.1)")
            self.lbl_branch_title.setStyleSheet(
                f"font-size: 14px; font-weight: 800; color: {Colors.SUCCESS_TEXT}; border: none;"
            )
            self.lbl_branch_desc.setText(str(data.get("ghi_chu") or "Miễn thu phí lượt tại cổng."))

            owner = data.get("chu_xe") or {}
            self.lbl_res_owner.setText(str(owner.get("ho_ten") or "-"))
            self.lbl_res_apt.setText(f"{owner.get('ma_can_ho', '-')} (Tòa {owner.get('toa_nha', '-')})")
            self.lbl_res_phone.setText(str(owner.get("so_dien_thoai") or "-"))
            vtype = str(data.get("ma_loai_xe") or VehicleTypes.O_TO.value)
            self.lbl_res_type.setText("Ô tô" if vtype == VehicleTypes.O_TO.value else "Xe máy")
            slot_txt = str(data.get("ten_vi_tri_co_dinh") or data.get("ma_vi_tri_co_dinh") or "Theo khu vực xe máy")
            self.lbl_res_slot.setText(slot_txt)
            self.lbl_res_zone.setText(str(data.get("ten_khu_vuc") or data.get("ma_khu_vuc") or "-"))

            self.detail_stack.setCurrentIndex(1)
            self.btn_open_gate.setEnabled(True)
        else:
            self.banner_frame.setStyleSheet(
                f"background-color: {Colors.INFO_BG}; border: 1px solid {Colors.INFO_BORDER}; border-radius: 10px;"
            )
            self.lbl_branch_title.setText("KHÁCH VÃNG LAI — TICKETLESS (2.2.3.2)")
            self.lbl_branch_title.setStyleSheet(
                f"font-size: 14px; font-weight: 800; color: {Colors.INFO_TEXT}; border: none;"
            )
            self.lbl_branch_desc.setText(str(data.get("ghi_chu") or "Định danh bằng biển số xe, tự động gợi ý vị trí trống."))

            self._available_car_slots = list(data.get("available_car_slots") or [])
            self._available_moto_zones = list(data.get("available_moto_zones") or [])

            vtype = str(data.get("ma_loai_xe") or VehicleTypes.O_TO.value)
            self.cbo_vis_type.blockSignals(True)
            idx = self.cbo_vis_type.findData(vtype)
            self.cbo_vis_type.setCurrentIndex(idx if idx >= 0 else 0)
            self.cbo_vis_type.blockSignals(False)
            self._populate_visitor_targets()

            self.detail_stack.setCurrentIndex(2)
            self.btn_open_gate.setEnabled(self.cbo_vis_assign.count() > 0)

    def _populate_visitor_targets(self) -> None:
        vtype = str(self.cbo_vis_type.currentData() or VehicleTypes.O_TO.value)
        self.cbo_vis_assign.clear()
        if vtype == VehicleTypes.O_TO.value:
            for slot in self._available_car_slots:
                code = str(slot.get("ma_vi_tri") or "")
                label = f"{code} — {slot.get('ten_khu_vuc', slot.get('ma_khu_vuc', ''))}"
                self.cbo_vis_assign.addItem(label, {"ma_vi_tri": code, "ma_khu_vuc": slot.get("ma_khu_vuc")})
        else:
            for zone in self._available_moto_zones:
                code = str(zone.get("ma_khu_vuc") or "")
                free = int(zone.get("so_cho_trong") or 0)
                label = f"{zone.get('ten_khu_vuc', code)} (Còn {free} chỗ)"
                self.cbo_vis_assign.addItem(label, {"ma_vi_tri": None, "ma_khu_vuc": code})

        if self._check_data and self._check_data.get("phan_nhanh") == "vang_lai":
            self.btn_open_gate.setEnabled(self.cbo_vis_assign.count() > 0)

    def _on_enter_shortcut(self) -> None:
        if self.btn_open_gate.isEnabled() and not self.txt_plate.hasFocus():
            self.confirm_gate_in()

    def confirm_gate_in(self) -> None:
        if not self._check_data:
            return
        if self.socket_client is None or not self.socket_client.is_connected():
            ToastNotification.show_toast(self, "Chưa kết nối tới Server.", variant="danger")
            return

        plate = normalize_plate(self.txt_plate.text() or str(self._check_data.get("bien_so_xe") or ""))
        branch = str(self._check_data.get("phan_nhanh") or "vang_lai")
        payload: Dict[str, Any] = {
            "bien_so": plate,
            "loai_khach": branch,
            "gate_image_base64": encode_bgr_to_base64(self._captured_bgr),
        }
        if branch == "cu_dan":
            payload["ma_phuong_tien"] = self._check_data.get("ma_phuong_tien")
        else:
            vtype = str(self.cbo_vis_type.currentData() or VehicleTypes.O_TO.value)
            target = self.cbo_vis_assign.currentData() or {}
            payload["ma_loai_xe"] = vtype
            payload["ma_vi_tri"] = target.get("ma_vi_tri")
            payload["ma_khu_vuc"] = target.get("ma_khu_vuc")

        self.btn_open_gate.setEnabled(False)
        self.badge_barrier.setText("ĐANG MỞ BARIE...")
        self.badge_barrier.set_variant("info")

        resp = self.socket_client.call(Actions.GATE_IN_CONFIRM, payload)
        if not resp.get("ok"):
            self.badge_barrier.setText("LỖI MỞ CỔNG")
            self.badge_barrier.set_variant("danger")
            self.btn_open_gate.setEnabled(True)
            ToastNotification.show_toast(self, str(resp.get("error") or "Không thể mở cổng."), variant="danger")
            return

        data = resp.get("data") or {}
        self.badge_barrier.setText("ĐÃ QUA CỔNG")
        self.badge_barrier.set_variant("success")
        ToastNotification.show_toast(
            self,
            str(data.get("message") or f"Đã mở cổng cho xe {plate} vào bãi."),
            variant="success",
            duration_ms=4000,
        )
        self.gate_entry_completed.emit(data.get("entry") or {})
        QTimer.singleShot(1500, self._reset_after_entry)

    def _reset_after_entry(self) -> None:
        self._check_data = None
        self._captured_bgr = None
        self.txt_plate.clear()
        self.lbl_plate_crop.clear()
        self.lbl_plate_crop.setText("Ảnh cắt\nbiển số")
        self.badge_engine.setText("Sẵn sàng lượt mới")
        self.badge_engine.set_variant("neutral")
        self.badge_barrier.setText("ĐANG ĐÓNG")
        self.badge_barrier.set_variant("neutral")
        self.detail_stack.setCurrentIndex(0)
        self.banner_frame.setStyleSheet(
            f"background-color: {Colors.BG_ELEVATED}; border: 1px solid {Colors.BORDER}; border-radius: 10px;"
        )
        self.lbl_branch_title.setText("CHỜ NHẬN DIỆN BIỂN SỐ")
        self.lbl_branch_title.setStyleSheet(
            f"font-size: 14px; font-weight: 800; color: {Colors.TEXT_PRIMARY}; border: none;"
        )
        self.lbl_branch_desc.setText("Chụp từ Webcam hoặc nhập biển số để phân nhánh Cư dân / Vãng lai.")
        self.btn_open_gate.setEnabled(False)
