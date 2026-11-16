"""Entrypoint khởi chạy Smart Parking TCP Socket Server."""

import signal
import threading
from dataclasses import dataclass
from typing import Optional

from server.config import ServerConfig, ensure_storage_dirs
from server.core.file_logger import FileLogger
from server.core.otp_service import OTPService
from server.core.router import Router
from server.core.session_manager import SessionManager
from server.core.socket_server import SocketServer
from server.core.state_store import StateStore
from shared.constants import LogLevels


@dataclass
class ServerContext:
    """Ngữ cảnh dùng chung chứa toàn bộ core service của Server."""

    config: ServerConfig
    file_logger: FileLogger
    state_store: StateStore
    session_manager: SessionManager
    otp_service: OTPService
    router: Router


def build_server_context(config: Optional[ServerConfig] = None) -> ServerContext:
    """Khởi tạo các service lõi của Server và đăng ký các handler sẵn có."""
    cfg = ensure_storage_dirs(config)
    file_logger = FileLogger(cfg.logs_dir)
    state_store = StateStore(cfg.state_dir / "active_vang_lai.json")
    session_manager = SessionManager(
        file_logger=file_logger,
        heartbeat_timeout=cfg.heartbeat_timeout_seconds,
        reap_interval=cfg.heartbeat_reap_interval_seconds,
    )
    otp_service = OTPService(config=cfg)
    router = Router(file_logger=file_logger)

    ctx = ServerContext(
        config=cfg,
        file_logger=file_logger,
        state_store=state_store,
        session_manager=session_manager,
        otp_service=otp_service,
        router=router,
    )
    _register_available_handlers(ctx)
    return ctx


def _register_available_handlers(ctx: ServerContext) -> None:
    """Đăng ký các module handler hiện diện trên nhánh hiện tại."""
    try:
        from server.handlers.monitor_admin_handler import register_monitor_admin_routes

        register_monitor_admin_routes(
            ctx.router,
            session_manager=ctx.session_manager,
            file_logger=ctx.file_logger,
            state_store=ctx.state_store,
        )
    except ImportError:
        pass


def create_socket_server(
    config: Optional[ServerConfig] = None,
    port_override: Optional[int] = None,
) -> tuple[SocketServer, ServerContext]:
    """Tạo thực thể SocketServer sẵn sàng lắng nghe."""
    ctx = build_server_context(config)
    port = ctx.config.server_port if port_override is None else port_override
    server = SocketServer(
        host=ctx.config.server_host,
        port=port,
        router=ctx.router,
        session_manager=ctx.session_manager,
        file_logger=ctx.file_logger,
    )
    return server, ctx


def main() -> int:
    server, ctx = create_socket_server()
    print(
        f"[SmartParking Server] Đang lắng nghe tại {ctx.config.server_host}:{server.bound_port} "
        f"(heartbeat timeout={ctx.config.heartbeat_timeout_seconds}s)"
    )
    ctx.file_logger.log_error(
        noi_dung=f"Khởi động TCP Server tại {ctx.config.server_host}:{server.bound_port}",
        muc_do=LogLevels.INFO,
        nguon="server.main",
        ma_loi="SERVER_STARTED",
    )

    stop_event = threading.Event()

    def _handle_sig(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sig)

    server.start_in_thread()
    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=0.25)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        print("\n[SmartParking Server] Đang dừng máy chủ an toàn...")
        server.stop()
        print("[SmartParking Server] Đã dừng hoàn toàn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
