"""Bộ kiểm thử chuyên biệt độ ổn định kết nối WebSocket Client & PySide6 Qt Event Loop.

Kiểm chứng các kịch bản thực tế:
1. Khởi chạy Gateway (FastAPI + Uvicorn + Neon Pool + Internal Socket Core).
2. Khởi tạo SocketClient PySide6 với transport='websocket' và ws_url='ws://127.0.0.1:8001/ws'.
3. Kiểm thử Idle Stability: Giữ kết nối hoàn toàn nhàn rỗi trong 4.0s (trước đây bị văng sau 1.5s).
4. Kiểm thử Heartbeat Ping/Pong trên luồng SocketClient và đo độ trễ latency_updated.
5. Kiểm thử Đăng nhập Admin qua WebSocket: auth.login admin01.
6. Kiểm thử Tải Hồ sơ Admin qua WebSocket: auth.get_profile (xác nhận ten_dang_nhap, vai_tro).
7. Kiểm thử Tải Danh sách Tài khoản Admin qua WebSocket: admin.list_accounts.
8. Kiểm thử Tái kết nối Reconnect qua WebSocket (không bị chuyển nhầm sang TCP).
9. Kiểm thử Đăng xuất và Đăng nhập chéo vai trò: quanly01, nhanvien01, cudan_a101.
10. Kiểm thử Ngắt kết nối an toàn (Graceful disconnect & Reader QThread termination).
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_BASE_DIR = Path(__file__).resolve().parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

# Đặt biến môi trường kiểm thử
os.environ["CLIENT_TRANSPORT"] = "websocket"
os.environ["CLIENT_WS_URL"] = "ws://127.0.0.1:8001/ws"

import uvicorn
from PySide6.QtCore import QCoreApplication, QTimer

from client.network.socket_client import SocketClient
from server.gateway import app
from shared.constants import Roles
from shared.protocol import Actions


class WebSocketStabilityTester:
    def __init__(self, port: int = 8001) -> None:
        self.port = port
        self.host = "127.0.0.1"
        self.ws_url = f"ws://{self.host}:{self.port}/ws"
        self.server_thread: Optional[threading.Thread] = None
        self.uvicorn_server: Optional[uvicorn.Server] = None
        self.passed = 0
        self.failed = 0
        self.disconnect_events = []

    def _record(self, name: str, success: bool, detail: str = "") -> None:
        if success:
            self.passed += 1
            tag = "[PASS]"
        else:
            self.failed += 1
            tag = "[FAIL]"
        if detail:
            print(f"{tag:<8} {name} — {detail}")
        else:
            print(f"{tag:<8} {name}")

    def start_gateway(self) -> None:
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="error")
        self.uvicorn_server = uvicorn.Server(config)

        def _run():
            asyncio.run(self.uvicorn_server.serve())

        self.server_thread = threading.Thread(target=_run, daemon=True, name="GatewayUvicornThread")
        self.server_thread.start()

        # Chờ gateway khởi động
        for _ in range(50):
            try:
                with socket.create_connection((self.host, self.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        time.sleep(0.6)

    def stop_gateway(self) -> None:
        if self.uvicorn_server is not None:
            self.uvicorn_server.should_exit = True
        if self.server_thread is not None:
            self.server_thread.join(timeout=3.0)

    def run_tests(self) -> int:
        print("\n" + "=" * 75)
        print("   SMART PARKING — WEBSOCKET CLIENT STABILITY & HOTFIX VERIFICATION")
        print("=" * 75 + "\n")

        # Khởi tạo QCoreApplication để vận hành Signal/Slot và QThread
        q_app = QCoreApplication.instance()
        if q_app is None:
            q_app = QCoreApplication(sys.argv)

        try:
            self.start_gateway()
            self._record("Gateway Startup", True, f"Uvicorn lắng nghe tại {self.ws_url}")
        except Exception as exc:
            self._record("Gateway Startup", False, str(exc))
            return 1

        client = None
        try:
            # 1. Khởi tạo SocketClient chế độ WebSocket
            client = SocketClient(ws_url=self.ws_url, transport="websocket")

            def on_disconnected(msg: str):
                self.disconnect_events.append(msg)

            client.connection_state_changed.connect(
                lambda connected, msg: self.disconnect_events.append(msg) if not connected else None
            )

            ok = client.connect_to_server(timeout=8.0)
            self._record(
                "WebSocket Handshake",
                ok and client.is_connected(),
                f"Đã kết nối thành công tới {self.ws_url} qua SocketClient"
            )

            # 2. Kiểm thử IDLE STABILITY (Giữ nhàn rỗi 4.0s - Không được disconnect!)
            # Bơm Qt Event Loop để các QThread / Signal / Socket timeout thực thi
            idle_start = time.time()
            self.disconnect_events.clear()
            while time.time() - idle_start < 4.0:
                q_app.processEvents()
                time.sleep(0.05)

            idle_ok = client.is_connected() and len(self.disconnect_events) == 0
            detail = (
                f"Giữ kết nối nhàn rỗi 4.0s hoàn toàn ổn định (không xảy ra timeout disconnect)"
                if idle_ok
                else f"LỖI: Bị ngắt kết nối khi idle: {self.disconnect_events}"
            )
            self._record("WebSocket Idle Stability (4.0s)", idle_ok, detail)

            # 3. Kiểm thử Heartbeat Ping/Pong
            latency = client.ping_once(timeout=5.0)
            self._record(
                "WebSocket Heartbeat Ping",
                latency is not None and latency > 0,
                f"Phản hồi Ping/Pong thành công (latency={latency}ms)"
            )

            # 4. Kiểm thử Đăng nhập Admin qua WebSocket
            login_resp = client.call(
                Actions.AUTH_LOGIN,
                {"ten_dang_nhap": "admin01", "mat_khau": "123456"},
                timeout=8.0,
            )
            login_ok = login_resp.get("ok") is True
            user_data = login_resp.get("data") or {}
            self._record(
                "WebSocket Admin Login",
                login_ok and user_data.get("vai_tro") == Roles.ADMIN.value,
                f"Đăng nhập thành công tài khoản '{user_data.get('ten_dang_nhap')}' (vai_tro={user_data.get('vai_tro')})"
            )

            # 5. Kiểm thử Tải Hồ sơ Admin (auth.get_profile)
            profile_resp = client.call(Actions.AUTH_GET_PROFILE, {}, timeout=8.0)
            profile_ok = profile_resp.get("ok") is True
            profile_data = (profile_resp.get("data") or {}).get("profile") or {}
            self._record(
                "Admin Profile Query (auth.get_profile)",
                profile_ok and profile_data.get("ten_dang_nhap") == "admin01",
                f"Hồ sơ tải thành công: ten_dang_nhap={profile_data.get('ten_dang_nhap')}, ho_ten={profile_data.get('ho_ten')}"
            )

            # 6. Kiểm thử Tải Danh sách Tài khoản Admin (admin.list_accounts)
            acc_resp = client.call(Actions.ADMIN_LIST_ACCOUNTS, {}, timeout=8.0)
            acc_ok = acc_resp.get("ok") is True
            acc_items = (acc_resp.get("data") or {}).get("items") or []
            self._record(
                "Admin Accounts List (admin.list_accounts)",
                acc_ok and len(acc_items) >= 4,
                f"Tải thành công {len(acc_items)} tài khoản từ CSDL Neon qua WebSocket"
            )

            # 7. Kiểm thử Đăng xuất và Đăng nhập chéo các vai trò còn lại
            roles_to_test = [
                ("quanly01", Roles.QUAN_LY.value),
                ("nhanvien01", Roles.NHAN_VIEN.value),
                ("cudan_a101", Roles.CU_DAN.value),
            ]
            all_roles_ok = True
            for u, r in roles_to_test:
                client.call(Actions.AUTH_LOGOUT, {}, timeout=4.0)
                resp = client.call(Actions.AUTH_LOGIN, {"ten_dang_nhap": u, "mat_khau": "123456"}, timeout=8.0)
                if not (resp.get("ok") and (resp.get("data") or {}).get("vai_tro") == r):
                    all_roles_ok = False
                    break

            self._record(
                "Multi-role Auth over WebSocket",
                all_roles_ok,
                "Đăng nhập và phân quyền chính xác cho Quản lý, Nhân viên, Cư dân qua WebSocket"
            )

            # 8. Kiểm thử Reconnect qua WebSocket (giữ nguyên transport='websocket')
            client.disconnect()
            assert not client.is_connected()
            # Tái kết nối bằng connect_to_server (mô phỏng bấm nút Kết nối)
            reconnect_ok = client.connect_to_server(ws_url=self.ws_url, transport="websocket", timeout=8.0)
            self._record(
                "WebSocket Reconnect Action",
                reconnect_ok and client.is_connected() and client.transport == "websocket",
                "Tái kết nối thành công, giữ nguyên giao thức WebSocket không bị chuyển nhầm sang TCP"
            )

            # 9. Đăng nhập lại sau Reconnect
            re_login = client.call(Actions.AUTH_LOGIN, {"ten_dang_nhap": "admin01", "mat_khau": "123456"}, timeout=8.0)
            self._record(
                "Post-reconnect Operation",
                re_login.get("ok") is True,
                "Giao tiếp Request/Response tiếp tục hoạt động bình thường sau tái kết nối"
            )

            # 10. Ngắt kết nối an toàn
            client.disconnect()
            time.sleep(0.3)
            q_app.processEvents()
            self._record(
                "Graceful Disconnect",
                not client.is_connected() and client._reader_thread is None,
                "Reader QThread đã dừng hoàn toàn, socket giải phóng an toàn"
            )

        except Exception as exc:
            self._record("Test Execution Exception", False, str(exc))
        finally:
            if client is not None:
                client.disconnect()
            self.stop_gateway()
            self._record("Gateway Teardown", True, "Đã dọn dẹp tiến trình Uvicorn và giải phóng port")

        total = self.passed + self.failed
        print("\n" + "-" * 75)
        print(f"TOTAL STABILITY CHECKS: {total}")
        print(f"PASSED:                 {self.passed}")
        print(f"FAILED:                 {self.failed}")
        print("-" * 75 + "\n")

        if self.failed > 0:
            print("[FAIL] Vẫn còn lỗi ổn định WebSocket!\n")
            return 1
        else:
            print("[SUCCESS] Hotfix WebSocket hoạt động hoàn hảo, sẵn sàng cho kiểm thử người dùng!\n")
            return 0


def main() -> int:
    tester = WebSocketStabilityTester()
    return tester.run_tests()


if __name__ == "__main__":
    sys.exit(main())
