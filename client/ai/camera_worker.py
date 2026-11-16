"""Luồng đọc Live Webcam tốc độ 30 FPS bằng OpenCV trên QThread cho Cổng Vào / Cổng Ra."""

import base64
import os
import threading
import time
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage


def bgr_to_qimage(frame_bgr: Optional[np.ndarray]) -> QImage:
    """Chuyển đổi frame OpenCV BGR sang QImage RGB888 độc lập bộ nhớ (thread-safe)."""
    if frame_bgr is None or frame_bgr.size == 0:
        return QImage()
    if frame_bgr.ndim == 2:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    height, width, channels = rgb.shape
    bytes_per_line = channels * width
    qimg = QImage(rgb.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)
    return qimg.copy()


def encode_bgr_to_base64(frame_bgr: Optional[np.ndarray], quality: int = 85) -> str:
    """Nén ảnh BGR sang JPEG và mã hóa Base64 để gửi qua TCP Socket."""
    if frame_bgr is None or frame_bgr.size == 0:
        return ""
    ok, encoded = cv2.imencode(
        ".jpg",
        frame_bgr,
        [int(cv2.IMWRITE_JPEG_QUALITY), max(30, min(95, int(quality)))],
    )
    if not ok:
        return ""
    return base64.b64encode(encoded.tobytes()).decode("ascii")


class CameraWorker(QThread):
    """QThread đọc Live Webcam 30 FPS (1280x720) không làm đơ giao diện PySide6."""

    frame_ready = Signal(QImage, object)
    camera_error = Signal(str)
    camera_started = Signal(int)
    camera_stopped = Signal()

    TARGET_WIDTH = 1280
    TARGET_HEIGHT = 720
    TARGET_FPS = 30

    def __init__(
        self,
        camera_index: int = 0,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._camera_index = int(camera_index)
        self._running = False
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None

    @property
    def camera_index(self) -> int:
        return self._camera_index

    def start_camera(self, camera_index: int = 0) -> None:
        """Khởi động luồng đọc Webcam theo chỉ số thiết bị (mặc định 0)."""
        target_index = int(camera_index)
        if self.isRunning():
            if target_index == self._camera_index:
                return
            self.stop_camera()

        self._camera_index = target_index
        self._running = True
        self.start()

    def stop_camera(self, wait_ms: int = 2500) -> None:
        """Dừng luồng Webcam và giải phóng thiết bị an toàn."""
        self._running = False
        if self.isRunning():
            self.wait(wait_ms)

    def capture_snapshot(self) -> Optional[np.ndarray]:
        """Trả về bản sao sắc nét của khung hình mới nhất khi bấm Chụp / Space."""
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def set_mock_frame(self, frame_bgr: Optional[np.ndarray]) -> None:
        """Nạp một frame BGR trực tiếp (phục vụ kiểm thử hoặc nạp ảnh mẫu khi không có Webcam)."""
        if frame_bgr is None or frame_bgr.size == 0:
            return
        frame_copy = frame_bgr.copy()
        with self._frame_lock:
            self._latest_frame = frame_copy
        qimg = bgr_to_qimage(frame_copy)
        if not qimg.isNull():
            self.frame_ready.emit(qimg, frame_copy)

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        """Mở thiết bị camera, ưu tiên CAP_DSHOW trên Windows để khởi động nhanh."""
        backends = [cv2.CAP_DSHOW, cv2.CAP_ANY] if os.name == "nt" else [cv2.CAP_ANY]
        for backend in backends:
            cap = cv2.VideoCapture(self._camera_index, backend)
            if cap is not None and cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.TARGET_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.TARGET_HEIGHT)
                cap.set(cv2.CAP_PROP_FPS, self.TARGET_FPS)
                return cap
            if cap is not None:
                cap.release()
        return None

    def run(self) -> None:
        cap: Optional[cv2.VideoCapture] = None
        frame_interval = 1.0 / float(self.TARGET_FPS)
        consecutive_failures = 0

        try:
            cap = self._open_capture()
            if cap is None:
                self.camera_error.emit(
                    f"Không tìm thấy hoặc không thể mở Webcam (chỉ số {self._camera_index}). "
                    "Vui lòng kiểm tra kết nối camera hoặc quyền truy cập thiết bị."
                )
                return

            self.camera_started.emit(self._camera_index)
            while self._running:
                loop_start = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None or frame.size == 0:
                    consecutive_failures += 1
                    if consecutive_failures >= 30:
                        self.camera_error.emit(
                            f"Mất tín hiệu hình ảnh từ Webcam (chỉ số {self._camera_index})."
                        )
                        break
                    time.sleep(0.03)
                    continue

                consecutive_failures = 0
                with self._frame_lock:
                    self._latest_frame = frame.copy()

                qimg = bgr_to_qimage(frame)
                if not qimg.isNull():
                    self.frame_ready.emit(qimg, frame.copy())

                elapsed = time.monotonic() - loop_start
                sleep_time = frame_interval - elapsed
                if sleep_time > 0.002:
                    time.sleep(sleep_time)
        except Exception as exc:
            self.camera_error.emit(f"Lỗi luồng Webcam: {exc}")
        finally:
            self._running = False
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            self.camera_stopped.emit()
