"""Lõi ThreadingTCPServer xử lý đồng thời nhiều Client qua 1 kết nối TCP/Client."""

import json
import socketserver
import threading
from typing import Optional, Tuple

from server.core.file_logger import FileLogger
from server.core.router import Router
from server.core.session_manager import SessionManager
from shared.constants import LogLevels
from shared.protocol import MsgType, make_response, recv_frame


class _ClientRequestHandler(socketserver.BaseRequestHandler):
    """Worker thread phục vụ vòng đời một kết nối TCP Client."""

    server: "ParkingTCPServer"  # type: ignore[assignment]

    def handle(self) -> None:
        session_mgr = self.server.session_manager
        router = self.server.router
        logger = self.server.file_logger
        session = session_mgr.register_connection(self.request, self.client_address)

        try:
            while not self.server.is_stopping:
                try:
                    frame = recv_frame(self.request)
                except (ValueError, json.JSONDecodeError) as exc:
                    if logger is not None:
                        logger.log_error(
                            noi_dung=f"Khung tin TCP không hợp lệ từ {session.ip_address}:{session.port}",
                            muc_do=LogLevels.WARN,
                            nguon="socket_server",
                            ma_loi="MALFORMED_FRAME",
                            chi_tiet=str(exc),
                        )
                    session.send_message(
                        make_response("", ok=False, error=f"Dữ liệu khung tin không hợp lệ: {exc}", error_code="MALFORMED_FRAME")
                    )
                    break
                except OSError:
                    break

                if frame is None:
                    break

                session_mgr.touch(session.session_id)

                if not isinstance(frame, dict):
                    continue

                msg_type = frame.get("msg_type", MsgType.REQUEST)
                if msg_type != MsgType.REQUEST:
                    continue

                request_id = str(frame.get("request_id") or "")
                action = str(frame.get("action") or "")
                payload = frame.get("payload")

                response = router.dispatch(session, action, payload, request_id)
                if not session.send_message(response):
                    break
        except Exception as exc:
            if logger is not None:
                logger.log_error(
                    noi_dung=f"Ngoại lệ luồng Client {session.ip_address}:{session.port}: {exc}",
                    muc_do=LogLevels.ERROR,
                    nguon="socket_server",
                    ma_loi="CLIENT_WORKER_CRASH",
                    chi_tiet=repr(exc),
                )
        finally:
            session_mgr.unregister_connection(session.session_id)
            session.close_socket()


class ParkingTCPServer(socketserver.ThreadingTCPServer):
    """ThreadingTCPServer gắn sẵn SessionManager, Router và FileLogger."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        router: Router,
        session_manager: SessionManager,
        file_logger: Optional[FileLogger] = None,
    ) -> None:
        self.router = router
        self.session_manager = session_manager
        self.file_logger = file_logger
        self.is_stopping = False
        super().__init__(server_address, _ClientRequestHandler)


class SocketServer:
    """Bộ điều phối vòng đời TCP Server (khởi động, chạy nền, dừng an toàn)."""

    def __init__(
        self,
        host: str,
        port: int,
        router: Router,
        session_manager: SessionManager,
        file_logger: Optional[FileLogger] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.router = router
        self.session_manager = session_manager
        self.file_logger = file_logger
        self._tcp_server = ParkingTCPServer(
            (host, port),
            router=router,
            session_manager=session_manager,
            file_logger=file_logger,
        )
        self.bound_port: int = int(self._tcp_server.server_address[1])
        self._thread: Optional[threading.Thread] = None
        self._serve_thread_id: Optional[int] = None
        self._stop_lock = threading.Lock()
        self._stopped = False

    def serve_forever(self, poll_interval: float = 0.2) -> None:
        """Chạy TCP Server đồng bộ và khởi động reaper quét phiên timeout."""
        self.session_manager.start_reaper()
        self._serve_thread_id = threading.get_ident()
        try:
            self._tcp_server.serve_forever(poll_interval=poll_interval)
        finally:
            self._serve_thread_id = None
            self.session_manager.stop_reaper()

    def start_in_thread(self) -> threading.Thread:
        """Khởi động TCP Server trên luồng nền (phục vụ kiểm thử hoặc tích hợp)."""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self.session_manager.start_reaper()
        self._thread = threading.Thread(
            target=self.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="ParkingTCPServerThread",
            daemon=True,
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        """Dừng TCP Server an toàn (tránh deadlock nếu gọi trên chính luồng serve)."""
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True

        self._tcp_server.is_stopping = True
        self.session_manager.stop_reaper()

        current_tid = threading.get_ident()
        if self._serve_thread_id is not None and self._serve_thread_id != current_tid:
            try:
                self._tcp_server.shutdown()
            except Exception:
                pass
        elif self._serve_thread_id == current_tid:
            # Tránh deadlock nếu stop() bị gọi chen ngang trên chính luồng đang chạy serve_forever
            setattr(self._tcp_server, "_BaseServer__shutdown_request", True)

        try:
            self._tcp_server.server_close()
        except OSError:
            pass

        if (
            self._thread is not None
            and self._thread is not threading.current_thread()
            and self._thread.is_alive()
        ):
            self._thread.join(timeout=2.0)
