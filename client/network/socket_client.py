"""TCP SocketClient dùng chung cho toàn bộ ứng dụng PySide6.

Duy trì 1 kết nối TCP duy nhất mỗi Client:
- Reader QThread đọc frame liên tục, phân tách RESPONSE (đánh thức hàm call() đồng bộ)
  và PUSH (phát Signal push_received lên Qt Main Thread).
- QTimer mỗi 15.000ms gửi system.ping ngầm để duy trì heartbeat & đo độ trễ.
"""

import json
import os
import socket
import struct
import threading
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from shared.protocol import Actions, HEADER_STRUCT, MsgType, make_request, recv_frame, send_frame

try:
    import websocket
except ImportError:
    websocket = None


class _WebSocketReaderThread(QThread):
    """Luồng nền đọc tuần tự các khung tin WebSocket từ Render Cloud Gateway."""

    disconnected = Signal(str)

    def __init__(
        self,
        ws: Any,
        on_frame: Any,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._ws = ws
        self._on_frame = on_frame
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        reason = "Đã ngắt kết nối WebSocket."
        # Đặt timeout ngắn cho mỗi lần gọi recv() để định kỳ kiểm tra self._running
        # mà không bị chặn vĩnh viễn khi ứng dụng đóng hoặc ngắt kết nối.
        try:
            if hasattr(self._ws, "settimeout"):
                self._ws.settimeout(1.0)
        except Exception:
            pass

        ws_timeout_types = (socket.timeout, TimeoutError)
        ws_closed_types = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, EOFError)
        if websocket is not None:
            ws_timeout_types = (websocket.WebSocketTimeoutException, socket.timeout, TimeoutError)
            ws_closed_types = (websocket.WebSocketConnectionClosedException, *ws_closed_types)

        try:
            while self._running:
                try:
                    data = self._ws.recv()
                except ws_timeout_types:
                    # Timeout khi nhận dữ liệu khi kết nối nhàn rỗi (idle) KHÔNG PHẢI lỗi ngắt kết nối.
                    # Luồng nền tiếp tục vòng lặp để đón frame tiếp theo.
                    continue
                except ws_closed_types as exc:
                    reason = f"Máy chủ đã đóng kết nối WebSocket: {exc}"
                    break
                except OSError as exc:
                    if not self._running:
                        break
                    reason = f"Lỗi socket WebSocket: {exc}"
                    break

                if data is None or data == "" or data == b"":
                    reason = "Máy chủ đã đóng kết nối WebSocket."
                    break

                frame: Optional[Dict[str, Any]] = None
                if isinstance(data, bytes):
                    if len(data) >= 4:
                        prefix_len = struct.unpack("!I", data[:4])[0]
                        if prefix_len == len(data) - 4:
                            frame = json.loads(data[4:].decode("utf-8"))
                        else:
                            frame = json.loads(data.decode("utf-8"))
                    else:
                        frame = json.loads(data.decode("utf-8"))
                elif isinstance(data, str):
                    frame = json.loads(data)

                if isinstance(frame, dict):
                    self._on_frame(frame)
        except Exception as exc:
            if self._running:
                reason = f"Mất kết nối WebSocket: {exc}"
        finally:
            if self._running:
                self.disconnected.emit(reason)


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
        ws_url: Optional[str] = None,
        transport: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.host = host or os.getenv("CLIENT_CONNECT_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("SERVER_PORT", "5555"))
        self.ws_url = ws_url or os.getenv("CLIENT_WS_URL", "").strip()
        self.transport = (transport or os.getenv("CLIENT_TRANSPORT", "tcp")).strip().lower()

        self._sock: Optional[socket.socket] = None
        self._ws: Any = None
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending_events: Dict[str, threading.Event] = {}
        self._pending_responses: Dict[str, Dict[str, Any]] = {}

        self._reader_thread: Optional[Any] = None
        self._connected = False
        self._last_latency_ms: Optional[int] = None

        self._ping_timer = QTimer(self)
        self._ping_timer.setInterval(self.DEFAULT_PING_INTERVAL_MS)
        self._ping_timer.timeout.connect(self._send_heartbeat_async)

    def is_connected(self) -> bool:
        if self.transport == "websocket":
            return self._connected and self._ws is not None
        return self._connected and self._sock is not None

    def last_latency_ms(self) -> Optional[int]:
        return self._last_latency_ms

    def connect_to_server(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        ws_url: Optional[str] = None,
        transport: Optional[str] = None,
        timeout: float = 10.0,
    ) -> bool:
        """Thiết lập kết nối (TCP hoặc WebSocket Secure) tới Server."""
        self.disconnect()
        if transport is not None:
            self.transport = transport.strip().lower()
        if host:
            self.host = host
        if port is not None:
            self.port = int(port)
        if ws_url:
            self.ws_url = ws_url

        # Tự động chuyển mode nếu host hoặc ws_url có định dạng WebSocket
        if self.host.startswith("ws://") or self.host.startswith("wss://"):
            self.transport = "websocket"
            self.ws_url = self.host
        elif self.ws_url and self.transport == "websocket":
            pass

        if self.transport == "websocket":
            return self._connect_websocket(timeout=timeout)
        else:
            return self._connect_tcp(timeout=timeout)

    def _connect_tcp(self, timeout: float = 5.0) -> bool:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
            sock.settimeout(None)
        except OSError as exc:
            self._connected = False
            self.connection_state_changed.emit(False, f"Không kết nối được TCP {self.host}:{self.port} ({exc})")
            return False

        self._sock = sock
        self._connected = True

        self._reader_thread = _SocketReaderThread(sock, on_frame=self.handle_incoming_frame)
        self._reader_thread.disconnected.connect(self._on_reader_disconnected)
        self._reader_thread.start()

        if QThread.currentThread() == self.thread():
            self._ping_timer.start()

        self.connection_state_changed.emit(True, f"Đã kết nối TCP {self.host}:{self.port}")
        return True

    def _connect_websocket(self, timeout: float = 10.0) -> bool:
        if websocket is None:
            self._connected = False
            self.connection_state_changed.emit(False, "Chưa cài đặt thư viện 'websocket-client'. Chạy: pip install websocket-client")
            return False

        url = self.ws_url or f"ws://{self.host}:{self.port}/ws"
        try:
            ws = websocket.create_connection(url, timeout=timeout)
        except Exception as exc:
            self._connected = False
            self.connection_state_changed.emit(False, f"Không kết nối được WebSocket {url} ({exc})")
            return False

        self._ws = ws
        self._connected = True

        self._reader_thread = _WebSocketReaderThread(ws, on_frame=self.handle_incoming_frame)
        self._reader_thread.disconnected.connect(self._on_reader_disconnected)
        self._reader_thread.start()

        if QThread.currentThread() == self.thread():
            self._ping_timer.start()

        proto = "WSS" if url.startswith("wss://") else "WS"
        self.connection_state_changed.emit(True, f"Đã kết nối {proto} {url}")
        return True

    def disconnect(self) -> None:
        """Đóng kết nối (TCP hoặc WebSocket) và đánh thức mọi request đang chờ."""
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

        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
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
        self._ws = None
        self._fail_all_pending(reason)
        self.connection_state_changed.emit(False, reason)

    def call(
        self,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """Gửi REQUEST đồng bộ theo `request_id` và chờ RESPONSE từ Server."""
        if not self.is_connected():
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
                if self.transport == "websocket":
                    if self._ws is None:
                        raise OSError("WebSocket đã bị đóng.")
                    payload_bytes = json.dumps(req, ensure_ascii=False).encode("utf-8")
                    length_header = HEADER_STRUCT.pack(len(payload_bytes))
                    self._ws.send_binary(length_header + payload_bytes)
                else:
                    if self._sock is None:
                        raise OSError("Socket đã bị đóng.")
                    send_frame(self._sock, req)
        except (OSError, Exception) as exc:
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
