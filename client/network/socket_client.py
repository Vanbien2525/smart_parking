"""TCP SocketClient dùng chung cho toàn bộ ứng dụng PySide6.

Duy trì 1 kết nối TCP duy nhất mỗi Client:
- Reader QThread đọc frame liên tục, phân tách RESPONSE (đánh thức hàm call() đồng bộ)
  và PUSH (phát Signal push_received lên Qt Main Thread).
- QTimer mỗi 15.000ms gửi system.ping ngầm để duy trì heartbeat & đo độ trễ.
"""

import os
import socket
import threading
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from shared.protocol import Actions, MsgType, make_request, recv_frame, send_frame


class _SocketReaderThread(QThread):
    """Luồng nền đọc tuần tự các khung tin từ Server."""

    disconnected = Signal(str)

    def __init__(
        self,
        sock: socket.socket,
        on_frame: Any,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._sock = sock
        self._on_frame = on_frame
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        reason = "Đã ngắt kết nối máy chủ."
        try:
            while self._running:
                frame = recv_frame(self._sock)
                if frame is None:
                    reason = "Máy chủ đã đóng kết nối."
                    break
                if isinstance(frame, dict):
                    # Xử lý RESPONSE ngay trên reader thread để call() trên Main Thread không bị deadlock Qt event loop
                    self._on_frame(frame)
        except OSError as exc:
            if self._running:
                reason = f"Mất kết nối mạng: {exc}"
        except Exception as exc:
            if self._running:
                reason = f"Lỗi đọc gói tin: {exc}"
        finally:
            if self._running:
                self.disconnected.emit(reason)


class SocketClient(QObject):
    """Client TCP đa nhiệm hỗ trợ đồng thời Request/Response và Server Push."""

    push_received = Signal(str, dict)
    connection_state_changed = Signal(bool, str)
    latency_updated = Signal(int)

    DEFAULT_PING_INTERVAL_MS = 15_000

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.host = host or os.getenv("CLIENT_CONNECT_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("SERVER_PORT", "5555"))

        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending_events: Dict[str, threading.Event] = {}
        self._pending_responses: Dict[str, Dict[str, Any]] = {}

        self._reader_thread: Optional[_SocketReaderThread] = None
        self._connected = False
        self._last_latency_ms: Optional[int] = None

        self._ping_timer = QTimer(self)
        self._ping_timer.setInterval(self.DEFAULT_PING_INTERVAL_MS)
        self._ping_timer.timeout.connect(self._send_heartbeat_async)

    def is_connected(self) -> bool:
        return self._connected and self._sock is not None

    def last_latency_ms(self) -> Optional[int]:
        return self._last_latency_ms

    def connect_to_server(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        timeout: float = 5.0,
    ) -> bool:
        """Thiết lập kết nối TCP tới Server và khởi chạy Reader QThread + Ping QTimer."""
        self.disconnect()
        if host:
            self.host = host
        if port is not None:
            self.port = int(port)

        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
            sock.settimeout(None)
        except OSError as exc:
            self._connected = False
            self.connection_state_changed.emit(False, f"Không kết nối được {self.host}:{self.port} ({exc})")
            return False

        self._sock = sock
        self._connected = True

        self._reader_thread = _SocketReaderThread(sock, on_frame=self.handle_incoming_frame)
        self._reader_thread.disconnected.connect(self._on_reader_disconnected)
        self._reader_thread.start()

        # Chỉ start QTimer khi đang ở trong thread có Qt event loop
        if QThread.currentThread() == self.thread():
            self._ping_timer.start()

        self.connection_state_changed.emit(True, f"Đã kết nối {self.host}:{self.port}")
        return True

    def disconnect(self) -> None:
        """Đóng kết nối TCP và đánh thức mọi request đang chờ."""
        self._ping_timer.stop()
        was_connected = self._connected
        self._connected = False

        if self._reader_thread is not None:
            self._reader_thread.stop()

        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

        if self._reader_thread is not None:
            self._reader_thread.wait(1500)
            self._reader_thread = None

        self._fail_all_pending("Đã đóng kết nối tới máy chủ.")
        if was_connected:
            self.connection_state_changed.emit(False, "Đã ngắt kết nối.")

    def _fail_all_pending(self, reason: str) -> None:
        with self._pending_lock:
            for req_id, evt in self._pending_events.items():
                self._pending_responses[req_id] = {
                    "msg_type": MsgType.RESPONSE,
                    "request_id": req_id,
                    "ok": False,
                    "data": None,
                    "error": reason,
                    "error_code": "DISCONNECTED",
                }
                evt.set()

    def handle_incoming_frame(self, frame: Dict[str, Any]) -> None:
        """Phân loại gói tin RESPONSE hoặc PUSH nhận từ Server."""
        msg_type = frame.get("msg_type")
        if msg_type == MsgType.RESPONSE:
            req_id = str(frame.get("request_id") or "")
            with self._pending_lock:
                evt = self._pending_events.get(req_id)
                if evt is not None:
                    self._pending_responses[req_id] = frame
                    evt.set()
        elif msg_type == MsgType.PUSH:
            event = str(frame.get("event") or "")
            data = frame.get("data")
            self.push_received.emit(event, data if isinstance(data, dict) else {})

    def _on_reader_disconnected(self, reason: str) -> None:
        self._ping_timer.stop()
        self._connected = False
        self._sock = None
        self._fail_all_pending(reason)
        self.connection_state_changed.emit(False, reason)

    def call(
        self,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """Gửi REQUEST đồng bộ theo `request_id` và chờ RESPONSE từ Server."""
        if not self.is_connected() or self._sock is None:
            return {
                "msg_type": MsgType.RESPONSE,
                "request_id": "",
                "ok": False,
                "data": None,
                "error": "Chưa kết nối tới máy chủ.",
                "error_code": "NOT_CONNECTED",
            }

        req = make_request(action, payload)
        req_id = req["request_id"]
        event = threading.Event()

        with self._pending_lock:
            self._pending_events[req_id] = event

        try:
            with self._send_lock:
                if self._sock is None:
                    raise OSError("Socket đã bị đóng.")
                send_frame(self._sock, req)
        except OSError as exc:
            with self._pending_lock:
                self._pending_events.pop(req_id, None)
                self._pending_responses.pop(req_id, None)
            return {
                "msg_type": MsgType.RESPONSE,
                "request_id": req_id,
                "ok": False,
                "data": None,
                "error": f"Lỗi gửi yêu cầu: {exc}",
                "error_code": "SEND_FAILED",
            }

        if not event.wait(timeout=timeout):
            with self._pending_lock:
                self._pending_events.pop(req_id, None)
                self._pending_responses.pop(req_id, None)
            return {
                "msg_type": MsgType.RESPONSE,
                "request_id": req_id,
                "ok": False,
                "data": None,
                "error": f"Hết thời gian chờ phản hồi ({timeout:.0f}s) cho '{action}'.",
                "error_code": "TIMEOUT",
            }

        with self._pending_lock:
            self._pending_events.pop(req_id, None)
            return self._pending_responses.pop(req_id, {"ok": False, "error": "Không có dữ liệu."})

    def request_data(
        self,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> Any:
        """Gọi `call()` và trả trực tiếp phần `data`, hoặc ném RuntimeError nếu `ok=False`."""
        resp = self.call(action, payload, timeout=timeout)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error") or f"Yêu cầu '{action}' thất bại.")
        return resp.get("data")

    def ping_once(self, timeout: float = 5.0) -> Optional[int]:
        """Gửi một gói system.ping và trả về độ trễ tính bằng mili-giây."""
        if not self.is_connected():
            return None
        started = time.perf_counter()
        resp = self.call(Actions.PING, {}, timeout=timeout)
        if not resp.get("ok"):
            return None
        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        self._last_latency_ms = elapsed_ms
        self.latency_updated.emit(elapsed_ms)
        return elapsed_ms

    def _send_heartbeat_async(self) -> None:
        if not self.is_connected():
            return
        threading.Thread(target=self.ping_once, daemon=True, name="SocketPingWorker").start()
