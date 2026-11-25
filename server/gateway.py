"""WebSocket Gateway & HTTP Service cho Render Cloud Deployment.

Chức năng:
1. Cung cấp HTTP GET /: Trang thông tin hệ thống (HTML) phục vụ giảng viên kiểm tra qua trình duyệt.
2. Cung cấp HTTP GET /health: Health check cho Render Web Service.
3. Cung cấp HTTP GET /ready: Kiểm tra sẵn sàng CSDL Neon và Socket Core (không lộ secrets).
4. Cung cấp WebSocket /ws: Cầu nối hai chiều giữa PySide6 WebSocket Client và TCP Socket Server Core.
5. Vòng đời Lifespan: Tự động khởi động và dừng internal TCP Server + Neon Connection Pool.
"""

import asyncio
import json
import logging
import os
import socket
import struct
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, JSONResponse

# Đảm bảo import đúng cấu trúc server & shared
_BASE_DIR = Path(__file__).resolve().parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from server.config import DEFAULT_CONFIG, ServerConfig
from server import db
from server.main import create_socket_server
from shared.protocol import HEADER_STRUCT, MAX_FRAME_SIZE, MsgType, make_response

logger = logging.getLogger("server.gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_SERVER_INSTANCE: Any = None
_SERVER_CTX: Any = None
_BOUND_INTERNAL_PORT: int = 5555
_ACTIVE_WEBSOCKETS: Set[WebSocket] = set()
_START_TIME: float = time.time()
MAX_CONCURRENT_WS: int = 50


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Quản lý vòng đời khởi chạy và tắt Server trên Render."""
    global _SERVER_INSTANCE, _SERVER_CTX, _BOUND_INTERNAL_PORT, _START_TIME
    _START_TIME = time.time()
    cfg = DEFAULT_CONFIG

    logger.info("Khởi động Smart Parking Cloud Gateway...")

    # 1. Khởi tạo Neon Database Pool
    try:
        db.init_pool(cfg)
        if db.is_pool_ready():
            logger.info("Kết nối Neon PostgreSQL Cloud thành công qua Connection Pool.")
        else:
            logger.warning("Cảnh báo: Neon DB Pool khởi tạo nhưng chưa sẵn sàng.")
    except Exception as exc:
        logger.error(f"Lỗi khởi tạo Neon DB Pool: {exc}")

    # 2. Khởi chạy internal TCP Server lắng nghe trên loopback (127.0.0.1)
    try:
        # Ưu tiên port 5555, nếu bận thì để OS tự cấp phát port ngẫu nhiên
        try:
            _SERVER_INSTANCE, _SERVER_CTX = create_socket_server(cfg, port_override=5555)
        except OSError:
            _SERVER_INSTANCE, _SERVER_CTX = create_socket_server(cfg, port_override=0)

        _SERVER_INSTANCE.start_in_thread()
        _BOUND_INTERNAL_PORT = _SERVER_INSTANCE.bound_port
        logger.info(f"Internal TCP Server Core đang chạy tại 127.0.0.1:{_BOUND_INTERNAL_PORT}")
    except Exception as exc:
        logger.error(f"Không thể khởi động Internal TCP Server Core: {exc}")
        raise

    yield

    # Shutdown
    logger.info("Đang tắt Smart Parking Cloud Gateway...")
    # Đóng toàn bộ WebSockets đang mở
    for ws in list(_ACTIVE_WEBSOCKETS):
        try:
            await ws.close(code=1001, reason="Server shutting down")
        except Exception:
            pass
    _ACTIVE_WEBSOCKETS.clear()

    # Dừng internal TCP Server
    if _SERVER_INSTANCE is not None:
        try:
            _SERVER_INSTANCE.stop()
        except Exception as exc:
            logger.warning(f"Lỗi dừng TCP server: {exc}")

    # Đóng connection pool
    try:
        db.close_pool(timeout=5.0)
    except Exception:
        pass

    logger.info("Smart Parking Cloud Gateway đã dừng hoàn toàn.")


app = FastAPI(
    title="Smart Parking Render Gateway",
    description="WebSocket & HTTP Gateway cho Hệ thống Quản lý Bãi xe Thông minh Chung cư Đào Tiên",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Trang thông tin hệ thống trực quan phục vụ kiểm tra và demo cho giảng viên."""
    uptime_sec = int(time.time() - _START_TIME)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours:02d}h {minutes:02d}m {seconds:02d}s"

    db_ready = db.is_pool_ready()
    db_status_color = "#10b981" if db_ready else "#ef4444"
    db_status_text = "Online (Connected)" if db_ready else "Offline (Reconnecting)"

    html_content = f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Parking — Render Cloud Gateway</title>
    <style>
        :root {{
            --bg: #0f172a;
            --card-bg: #1e293b;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --primary: #3b82f6;
            --success: #10b981;
            --border: #334155;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-primary);
            line-height: 1.6;
            padding: 40px 20px;
        }}
        .container {{
            max-width: 860px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            margin-bottom: 32px;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 600;
            background: rgba(59, 130, 246, 0.15);
            color: var(--primary);
            border: 1px solid rgba(59, 130, 246, 0.3);
            margin-bottom: 12px;
        }}
        h1 {{
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 8px;
            background: linear-gradient(135deg, #60a5fa 0%, #3b82f6 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{
            color: var(--text-secondary);
            font-size: 1.05rem;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 20px;
            margin-bottom: 32px;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }}
        .card h3 {{
            font-size: 0.95rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 8px;
        }}
        .card .value {{
            font-size: 1.4rem;
            font-weight: 700;
        }}
        .status-dot {{
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 6px;
            background-color: var(--success);
        }}
        .table-card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 32px;
        }}
        .table-card h2 {{
            font-size: 1.2rem;
            margin-bottom: 16px;
            color: var(--text-primary);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.95rem;
        }}
        th, td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            color: var(--text-secondary);
            font-weight: 600;
        }}
        tr:last-child td {{
            border-bottom: none;
        }}
        code {{
            background: rgba(0,0,0,0.3);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            color: #38bdf8;
        }}
        .instructions {{
            background: rgba(30, 41, 59, 0.7);
            border-left: 4px solid var(--primary);
            padding: 16px 20px;
            border-radius: 0 8px 8px 0;
            color: var(--text-secondary);
            font-size: 0.95rem;
        }}
        .footer {{
            text-align: center;
            margin-top: 40px;
            font-size: 0.85rem;
            color: var(--text-secondary);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <span class="badge">LẬP TRÌNH MẠNG · CLIENT-SERVER</span>
            <h1>BÃI XE THÔNG MINH CHUNG CƯ ĐÀO TIÊN</h1>
            <p class="subtitle">Render Cloud Gateway & TCP Core Service — MVC Architecture</p>
        </div>

        <div class="grid">
            <div class="card">
                <h3>Gateway Status</h3>
                <div class="value"><span class="status-dot"></span>Online</div>
                <small style="color: var(--text-secondary);">Uptime: {uptime_str}</small>
            </div>
            <div class="card">
                <h3>Neon PostgreSQL</h3>
                <div class="value" style="color: {db_status_color};">{db_status_text}</div>
                <small style="color: var(--text-secondary);">Region: AWS Singapore (v18.7)</small>
            </div>
            <div class="card">
                <h3>Active Connections</h3>
                <div class="value">{len(_ACTIVE_WEBSOCKETS)} WebSocket</div>
                <small style="color: var(--text-secondary);">Internal TCP: 127.0.0.1:{_BOUND_INTERNAL_PORT}</small>
            </div>
        </div>

        <div class="table-card">
            <h2>Giao diện lập trình cổng (API & WebSocket Endpoints)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Phương thức</th>
                        <th>Đường dẫn</th>
                        <th>Giao thức</th>
                        <th>Chức năng</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><code>GET</code></td>
                        <td><code>/</code></td>
                        <td>HTTP/1.1</td>
                        <td>Trang tổng quan trạng thái máy chủ (Dashboard)</td>
                    </tr>
                    <tr>
                        <td><code>GET</code></td>
                        <td><code>/health</code></td>
                        <td>JSON</td>
                        <td>Kiểm tra tiến trình dịch vụ Cloud còn sống (Healthcheck)</td>
                    </tr>
                    <tr>
                        <td><code>GET</code></td>
                        <td><code>/ready</code></td>
                        <td>JSON</td>
                        <td>Kiểm tra sẵn sàng CSDL Neon và Socket Core</td>
                    </tr>
                    <tr>
                        <td><code>WSS</code></td>
                        <td><code>/ws</code></td>
                        <td>WebSocket Secure</td>
                        <td>Đường truyền hai chiều cho PySide6 Desktop Client</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <div class="instructions">
            <strong>Hướng dẫn kết nối PySide6 Desktop Client:</strong><br>
            Cấu hình file <code>.env</code> ở Desktop Client:
            <code>CLIENT_TRANSPORT=websocket</code> và <code>CLIENT_WS_URL=wss://YOUR_SERVICE.onrender.com/ws</code>.
            Mọi khung tin <code>[4-byte Length-Prefix] + [JSON Payload]</code> được chuyển tiếp nguyên vẹn tới Router Core.
        </div>

        <div class="footer">
            Hệ thống Quản lý Bãi xe Thông minh Chung cư Đào Tiên &bull; Nhóm 5 thành viên &bull; 2026
        </div>
    </div>
</body>
</html>
"""
    return html_content


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Kiểm tra tiến trình Server còn hoạt động hay không."""
    return {
        "status": "ok",
        "service": "smart-parking-cloud-gateway",
        "version": "2.0.0",
        "uptime_seconds": int(time.time() - _START_TIME),
    }


@app.get("/ready")
async def readiness_check() -> JSONResponse:
    """Kiểm tra tính sẵn sàng của Server và Neon Database (không để lộ secrets)."""
    db_ok = False
    try:
        db_ok = db.is_pool_ready()
    except Exception:
        db_ok = False

    server_core_ok = _SERVER_INSTANCE is not None and not getattr(_SERVER_INSTANCE, "_stopped", True)

    is_ready = db_ok and server_core_ok
    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "ready": is_ready,
            "database_connected": db_ok,
            "socket_server_core": server_core_ok,
            "active_ws_clients": len(_ACTIVE_WEBSOCKETS),
            "max_concurrent_ws": MAX_CONCURRENT_WS,
        },
    )


async def _pipe_ws_to_tcp(
    websocket: WebSocket,
    writer: asyncio.StreamWriter,
    client_id: str,
) -> None:
    """Đọc dữ liệu từ WebSocket và ghi vào TCP socket nội bộ."""
    try:
        while True:
            # Nhận message từ WebSocket (hỗ trợ cả binary và text)
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            data_bytes: Optional[bytes] = None
            if "bytes" in msg and msg["bytes"]:
                raw = msg["bytes"]
                # Nếu client gửi kèm 4-byte length-prefix:
                if len(raw) >= 4:
                    prefix_len = struct.unpack("!I", raw[:4])[0]
                    if prefix_len == len(raw) - 4:
                        data_bytes = raw
                    else:
                        # Raw binary JSON mà không có length-prefix
                        if len(raw) > MAX_FRAME_SIZE:
                            raise ValueError(f"Khung tin vượt quá kích thước cho phép ({len(raw)} bytes)")
                        data_bytes = struct.pack("!I", len(raw)) + raw
                else:
                    data_bytes = struct.pack("!I", len(raw)) + raw

            elif "text" in msg and msg["text"]:
                payload_str = msg["text"]
                encoded = payload_str.encode("utf-8")
                if len(encoded) > MAX_FRAME_SIZE:
                    raise ValueError(f"Khung tin vượt quá kích thước cho phép ({len(encoded)} bytes)")
                data_bytes = struct.pack("!I", len(encoded)) + encoded

            if data_bytes:
                writer.write(data_bytes)
                await writer.drain()

    except WebSocketDisconnect:
        pass
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception as exc:
        logger.warning(f"[{client_id}] Lỗi đọc từ WebSocket: {exc}")


async def _pipe_tcp_to_ws(
    reader: asyncio.StreamReader,
    websocket: WebSocket,
    client_id: str,
) -> None:
    """Đọc dữ liệu length-prefixed từ TCP socket nội bộ và gửi về WebSocket."""
    try:
        while True:
            header = await reader.readexactly(4)
            length = struct.unpack("!I", header)[0]
            if length > MAX_FRAME_SIZE:
                logger.error(f"[{client_id}] Kích thước frame TCP quá lớn: {length} bytes")
                break

            payload_bytes = await reader.readexactly(length)
            # Gửi lại về WebSocket dạng binary frame đầy đủ (length-prefix + payload)
            full_frame = header + payload_bytes
            await websocket.send_bytes(full_frame)

    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    except WebSocketDisconnect:
        pass
    except (asyncio.CancelledError, RuntimeError):
        pass
    except Exception as exc:
        logger.warning(f"[{client_id}] Lỗi đọc từ TCP socket: {exc}")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Xử lý kết nối WebSocket hai chiều của PySide6 Desktop Client."""
    if len(_ACTIVE_WEBSOCKETS) >= MAX_CONCURRENT_WS:
        await websocket.close(code=1013, reason="Đạt giới hạn kết nối đồng thời trên Cloud Free Tier")
        return

    await websocket.accept()
    _ACTIVE_WEBSOCKETS.add(websocket)
    client_ip = websocket.client.host if websocket.client else "unknown"
    client_port = websocket.client.port if websocket.client else 0
    client_id = f"{client_ip}:{client_port}"
    logger.info(f"Client WebSocket đã kết nối: {client_id} (Tổng số: {len(_ACTIVE_WEBSOCKETS)})")

    # Mở kết nối TCP tới Internal Server Core (127.0.0.1:_BOUND_INTERNAL_PORT)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", _BOUND_INTERNAL_PORT),
            timeout=5.0,
        )
    except Exception as exc:
        logger.error(f"[{client_id}] Không thể kết nối tới Internal TCP Server Core: {exc}")
        await websocket.close(code=1011, reason="Không thể kết nối Server Core nội bộ")
        _ACTIVE_WEBSOCKETS.discard(websocket)
        return

    # Chạy song song 2 coroutine chuyển tiếp
    task_ws_to_tcp = asyncio.create_task(_pipe_ws_to_tcp(websocket, writer, client_id))
    task_tcp_to_ws = asyncio.create_task(_pipe_tcp_to_ws(reader, websocket, client_id))

    done, pending = await asyncio.wait(
        [task_ws_to_tcp, task_tcp_to_ws],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()

    # Dọn dẹp tài nguyên kết nối TCP
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass

    _ACTIVE_WEBSOCKETS.discard(websocket)
    logger.info(f"Client WebSocket đã ngắt kết nối: {client_id} (Còn lại: {len(_ACTIVE_WEBSOCKETS)})")


def run_standalone() -> None:
    """Khởi chạy trực tiếp Gateway bằng Uvicorn."""
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("server.gateway:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_standalone()
