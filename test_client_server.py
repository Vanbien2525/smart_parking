"""Kiểm thử tích hợp Client - Server qua TCP Socket (Port 5555) kết nối Neon PostgreSQL Cloud.

Xác nhận:
1. Server entrypoint (server/main.py) khởi chạy và lắng nghe port 5555.
2. Client entrypoint (client/network) kết nối TCP thành công.
3. Giao thức Ping / Pong / Heartbeat hoạt động.
4. SessionManager theo dõi trạng thái kết nối và phiên làm việc.
5. Xác thực tài khoản với bcrypt hash trên Neon DB:
   - admin01 (vai trò: admin)
   - quanly01 (vai trò: quan_ly)
   - nhanvien01 (vai trò: nhan_vien)
   - cudan_a101 (vai trò: cu_dan)
   - Kiểm tra từ chối mật khẩu sai.
6. Phân quyền vai trò (RBAC): Cư dân không được gọi API quản trị.
7. Nghiệp vụ đọc dữ liệu từ Neon:
   - Danh sách khu vực & 20 vị trí đỗ (infra.list_zones, infra.list_slots)
   - Bảng giá dịch vụ (pricing.list)
   - Hồ sơ cư dân (hr.search_residents)
   - Danh sách phương tiện (vehicle.resident_list)
   - Hóa đơn tháng (billing.my_invoices)
   - Hộp thư thông báo (notify.my_inbox)
8. Đóng kết nối an toàn (Graceful Shutdown).
"""

import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_BASE_DIR = Path(__file__).resolve().parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from server.config import DEFAULT_CONFIG, ServerConfig
from server import db
from server.main import create_socket_server
from shared.protocol import Actions, MsgType, make_request, recv_frame, send_frame


class ClientServerTester:
    def __init__(self) -> None:
        self.server = None
        self.ctx = None
        self.port = 5555
        self.passed = 0
        self.failed = 0

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

    def _send_req(self, sock: socket.socket, action: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        req = make_request(action, payload)
        send_frame(sock, req)
        res = recv_frame(sock)
        if res is None:
            raise ConnectionError(f"Server đã đóng kết nối khi gọi {action}")
        return res

    def start_server(self) -> None:
        # Khởi tạo DB pool trước
        db.init_pool(DEFAULT_CONFIG)
        self.server, self.ctx = create_socket_server(DEFAULT_CONFIG, port_override=self.port)
        self.server.start_in_thread()
        self.port = self.server.bound_port
        time.sleep(0.5)

    def stop_server(self) -> None:
        if self.server:
            self.server.stop()
        db.close_pool()

    def run_tests(self) -> int:
        print("\n" + "=" * 70)
        print("   SMART PARKING - CLIENT-SERVER & NEON INTEGRATION VERIFICATION")
        print("=" * 70 + "\n")

        try:
            self.start_server()
            self._record("TCP Server startup", True, f"Lắng nghe tại 0.0.0.0:{self.port} (Neon DB ready)")
        except Exception as exc:
            self._record("TCP Server startup", False, str(exc))
            return 1

        client_sock = None
        try:
            # 1. Kết nối TCP Client
            client_sock = socket.create_connection(("127.0.0.1", self.port), timeout=5.0)
            self._record("TCP Client connection", True, f"Kết nối thành công 127.0.0.1:{self.port}")

            # 2. Ping / Pong & Heartbeat
            res = self._send_req(client_sock, Actions.PING)
            if res.get("ok") and (res.get("data") or {}).get("pong"):
                self._record("Ping / Heartbeat", True, "Phản hồi pong=True qua TCP Socket")
            else:
                self._record("Ping / Heartbeat", False, f"Kết quả không như mong đợi: {res}")

            # 3. SessionManager check
            active_sessions = self.ctx.session_manager.list_sessions()
            if len(active_sessions) >= 1:
                self._record("SessionManager tracking", True, f"Theo dõi {len(active_sessions)} phiên TCP kết nối")
            else:
                self._record("SessionManager tracking", False, "Không tìm thấy session nào trong SessionManager")

            # 4. Xác thực mật khẩu sai -> Bị từ chối
            res_bad = self._send_req(
                client_sock,
                Actions.AUTH_LOGIN,
                {"ten_dang_nhap": "admin01", "mat_khau": "sai_mat_khau_123"},
            )
            if not res_bad.get("ok"):
                self._record("Auth invalid credentials rejection", True, f"Từ chối đúng: {res_bad.get('error')}")
            else:
                self._record("Auth invalid credentials rejection", False, "Mật khẩu sai nhưng lại đăng nhập thành công!")

            # 5. Xác thực admin01 thành công với bcrypt
            res_admin = self._send_req(
                client_sock,
                Actions.AUTH_LOGIN,
                {"ten_dang_nhap": "admin01", "mat_khau": "123456"},
            )
            if res_admin.get("ok") and (res_admin.get("data") or {}).get("vai_tro") == "admin":
                self._record("Auth admin01 login & bcrypt verify", True, "Đăng nhập admin thành công (vai trò: admin)")
            else:
                self._record("Auth admin01 login & bcrypt verify", False, f"Đăng nhập thất bại: {res_admin}")

            # 6. Admin truy vấn danh sách tài khoản
            res_accs = self._send_req(client_sock, Actions.ADMIN_LIST_ACCOUNTS)
            if res_accs.get("ok"):
                acc_data = res_accs.get("data") or {}
                items = acc_data.get("items") if isinstance(acc_data, dict) else acc_data
                self._record("Admin list accounts (Neon DB)", True, f"Đọc {len(items)} tài khoản từ Neon CSDL (total: {acc_data.get('total')})")
            else:
                self._record("Admin list accounts (Neon DB)", False, f"Lỗi: {res_accs}")

            # 7. Truy vấn hạ tầng khu vực & vị trí đỗ
            res_zones = self._send_req(client_sock, Actions.INFRA_LIST_ZONES)
            res_slots = self._send_req(client_sock, Actions.INFRA_LIST_SLOTS)
            if res_zones.get("ok") and res_slots.get("ok"):
                z_data = res_zones.get("data") or {}
                s_data = res_slots.get("data") or {}
                zones = z_data.get("items") if isinstance(z_data, dict) else z_data
                slots = s_data.get("items") if isinstance(s_data, dict) else s_data
                self._record("Infra zones & slots (Neon DB)", True, f"{len(zones)} khu vực, {len(slots)} vị trí đỗ")
            else:
                self._record("Infra zones & slots (Neon DB)", False, f"Zones: {res_zones}, Slots: {res_slots}")

            # 8. Truy vấn bảng giá dịch vụ
            res_pricing = self._send_req(client_sock, Actions.PRICING_LIST)
            if res_pricing.get("ok"):
                p_data = res_pricing.get("data") or {}
                tiers = p_data.get("items") if isinstance(p_data, dict) else p_data
                self._record("Pricing list (Neon DB)", True, f"{len(tiers)} mức phí đang áp dụng")
            else:
                self._record("Pricing list (Neon DB)", False, f"Lỗi bảng giá: {res_pricing}")

            # 9. Truy vấn danh sách cư dân (HR)
            res_res = self._send_req(client_sock, Actions.HR_SEARCH_RESIDENTS, {"keyword": ""})
            if res_res.get("ok"):
                r_data = res_res.get("data") or {}
                cd_list = r_data.get("items") if isinstance(r_data, dict) else r_data
                self._record("HR search residents (Neon DB)", True, f"Đọc {len(cd_list)} hồ sơ cư dân")
            else:
                self._record("HR search residents (Neon DB)", False, f"Lỗi cư dân: {res_res}")

            # 10. Đăng xuất admin và đăng nhập cư dân cudan_a101
            self._send_req(client_sock, Actions.AUTH_LOGOUT)
            res_cd = self._send_req(
                client_sock,
                Actions.AUTH_LOGIN,
                {"ten_dang_nhap": "cudan_a101", "mat_khau": "123456"},
            )
            if res_cd.get("ok") and (res_cd.get("data") or {}).get("vai_tro") == "cu_dan":
                self._record("Auth cudan_a101 login & bcrypt verify", True, "Đăng nhập cư dân thành công (vai trò: cu_dan)")
            else:
                self._record("Auth cudan_a101 login & bcrypt verify", False, f"Lỗi đăng nhập: {res_cd}")

            # 11. Kiểm tra RBAC: Cư dân gọi admin.list_accounts -> phải bị từ chối
            res_forbidden = self._send_req(client_sock, Actions.ADMIN_LIST_ACCOUNTS)
            if not res_forbidden.get("ok"):
                self._record("RBAC permission enforcement", True, "Chặn cư dân truy cập API quản trị viên (ROLE_FORBIDDEN)")
            else:
                self._record("RBAC permission enforcement", False, "Lỗi bảo mật: Cư dân truy cập được API quản trị!")

            # 12. Cư dân đọc danh sách phương tiện của mình
            res_veh = self._send_req(client_sock, Actions.VEHICLE_RESIDENT_LIST)
            if res_veh.get("ok"):
                v_data = res_veh.get("data") or {}
                vehs = v_data.get("vehicles") if isinstance(v_data, dict) else v_data
                self._record("Vehicle resident list (Neon DB)", True, f"Đọc {len(vehs)} phương tiện thuộc căn hộ A1-01")
            else:
                self._record("Vehicle resident list (Neon DB)", False, f"Lỗi phương tiện: {res_veh}")

            # 13. Cư dân đọc hóa đơn tiền gửi xe
            res_bill = self._send_req(client_sock, Actions.BILLING_MY_INVOICES)
            if res_bill.get("ok"):
                b_data = res_bill.get("data") or {}
                invs = b_data.get("items") if isinstance(b_data, dict) else b_data
                self._record("Billing my invoices (Neon DB)", True, f"Đọc {len(invs)} hóa đơn tiền giữ xe")
            else:
                self._record("Billing my invoices (Neon DB)", False, f"Lỗi hóa đơn: {res_bill}")

            # 14. Cư dân đọc hộp thư thông báo
            res_notif = self._send_req(client_sock, Actions.NOTIFY_MY_INBOX)
            if res_notif.get("ok"):
                n_data = res_notif.get("data") or {}
                notifs = n_data.get("items") if isinstance(n_data, dict) else n_data
                self._record("Notify my inbox (Neon DB)", True, f"Hộp thư cá nhân sẵn sàng ({len(notifs)} thông báo)")
            else:
                self._record("Notify my inbox (Neon DB)", False, f"Lỗi thông báo: {res_notif}")

            # 15. Kiểm tra tài khoản nhanvien01 và quanly01
            client_staff = socket.create_connection(("127.0.0.1", self.port), timeout=5.0)
            res_staff = self._send_req(
                client_staff,
                Actions.AUTH_LOGIN,
                {"ten_dang_nhap": "nhanvien01", "mat_khau": "123456"},
            )
            res_ql = self._send_req(
                client_staff,
                Actions.AUTH_LOGIN,
                {"ten_dang_nhap": "quanly01", "mat_khau": "123456"},
            )
            client_staff.close()
            if res_staff.get("ok") and res_ql.get("ok"):
                self._record("Staff & Manager authentication", True, "Xác thực thành công nhanvien01 và quanly01")
            else:
                self._record("Staff & Manager authentication", False, f"Staff: {res_staff}, QL: {res_ql}")

        except Exception as exc:
            self._record("Client-Server communication exception", False, str(exc))
        finally:
            if client_sock:
                try:
                    client_sock.close()
                except Exception:
                    pass
            self.stop_server()
            self._record("TCP Server & Pool shutdown", True, "Đã đóng kết nối TCP và Connection Pool an toàn")

        total = self.passed + self.failed
        print("\n" + "-" * 70)
        print(f"TOTAL CLIENT-SERVER CHECKS: {total}")
        print(f"PASSED:                     {self.passed}")
        print(f"FAILED:                     {self.failed}")
        print("-" * 70 + "\n")

        if self.failed > 0:
            print("[FAIL] Kiểm thử Client-Server có lỗi cần kiểm tra!\n")
            return 1
        else:
            print("[SUCCESS] Hệ thống Client-Server TCP Socket hoạt động hoàn hảo với Neon Cloud!\n")
            return 0


def main() -> int:
    tester = ClientServerTester()
    return tester.run_tests()


if __name__ == "__main__":
    sys.exit(main())
