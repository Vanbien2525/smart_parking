"""Bộ kiểm thử tự động toàn bộ Core Network, Protocol, Session, Logs, State, OTP, Handler & UI của Leader Biên."""

import base64
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from client.network.socket_client import SocketClient
from client.ui.admin.admin_views import (
    AdminAccountsView,
    AdminLogsView,
    AdminReadOnlyWrapper,
    AdminSessionsView,
)
from client.ui.manager.dashboard_view import DashboardView
from server.config import ServerConfig
from server.core.file_logger import FileLogger
from server.core.otp_service import OTPService
from server.core.router import Router
from server.core.session_manager import ClientSession, SessionManager
from server.core.socket_server import SocketServer
from server.core.state_store import StateStore
from server.handlers.monitor_admin_handler import register_monitor_admin_routes
from shared.constants import AuthResults, LogLevels, Roles
from shared.protocol import Actions, PushEvents, make_request, recv_frame, send_frame
from shared.validators import (
    normalize_plate,
    save_base64_image,
    validate_cccd,
    validate_email,
    validate_phone,
    validate_plate,
)


class _FakeDB:
    """Mock DB provider đơn giản phục vụ kiểm thử handler 1.1 và 4.2 không cần PostgreSQL."""

    def fetch_one(self, sql: str, params: tuple = ()) -> Dict[str, Any]:
        q = sql.lower()
        if "from hoa_don_thang" in q:
            return {
                "tong_phai_thu": 5_000_000,
                "da_thu_cu_dan": 3_600_000,
                "con_no_cu_dan": 1_400_000,
                "so_hoa_don_da_thu": 3,
                "so_hoa_don_chua_thu": 1,
            }
        if "from bao_cao_luot_va_thu_phi" in q:
            return {"doanh_thu_vang_lai": 450_000}
        if "from giao_dich_thanh_toan" in q:
            return {"tong_giao_dich_thanh_cong": 3_600_000}
        if "from quan_ly_phuong_tien_ra_vao" in q:
            return {"so_luong": 7}
        if "from tai_khoan tk" in q:
            uid = params[0] if params else 1
            return {
                "ma_tai_khoan": uid,
                "ten_dang_nhap": "quanly01",
                "vai_tro": Roles.QUAN_LY.value,
                "trang_thai": "hoat_dong",
                "ly_do_khoa": None,
                "ho_ten": "Nguyễn Văn Quản Lý",
                "email": "ql@daotien.vn",
                "so_dien_thoai": "0909123456",
            }
        return {}

    def fetch_all(self, sql: str, _params: tuple = ()) -> List[Dict[str, Any]]:
        q = sql.lower()
        if "from yeu_cau_dang_ky_xe" in q:
            return [
                {
                    "ma_yeu_cau": 10,
                    "nhom_yeu_cau": "dang_ky_xe",
                    "loai_yeu_cau": "dang_ky_moi",
                    "thoi_gian_gui": "2026-11-16T08:00:00",
                    "ten_cu_dan": "Trần Cư Dân",
                    "ma_can_ho": "A101",
                    "bien_so_xe": "51H-888.99",
                }
            ]
        if "from yeu_cau_mua_them_cho" in q:
            return []
        if "from khu_vuc" in q and "ngung_su_dung" not in q:
            return [
                {
                    "ma_khu_vuc": "HAM_B1",
                    "ten_khu_vuc": "Hầm B1 Ô tô",
                    "ma_loai_xe": "O_TO",
                    "tong_so_cho": 40,
                    "so_xe_dang_gui": 9,
                    "trang_thai_khu_vuc": "hoat_dong",
                }
            ]
        if "from vi_tri_do" in q and "group by" in q:
            return [
                {"trang_thai_vi_tri": "trong", "so_luong": 30},
                {"trang_thai_vi_tri": "dang_co_xe", "so_luong": 9},
                {"trang_thai_vi_tri": "ngung_su_dung", "so_luong": 1},
            ]
        if "from tai_khoan tk" in q:
            return [
                {
                    "ma_tai_khoan": 2,
                    "ten_dang_nhap": "quanly01",
                    "vai_tro": "quan_ly",
                    "trang_thai": "hoat_dong",
                    "ly_do_khoa": None,
                    "ho_ten": "Nguyễn Văn Quản Lý",
                    "email": "ql@daotien.vn",
                }
            ]
        return []


class TestLeaderCore(unittest.TestCase):
    """Kiểm thử tích hợp lõi mạng, session, log, state, OTP, RBAC, handler & UI."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.file_logger = FileLogger(self.tmp_path / "logs")
        self.state_store = StateStore(self.tmp_path / "state" / "active_vang_lai.json")
        self.session_manager = SessionManager(
            file_logger=self.file_logger,
            heartbeat_timeout=45,
            reap_interval=10,
        )
        self.router = Router(file_logger=self.file_logger)

    def tearDown(self) -> None:
        self.session_manager.stop_reaper()
        self.tmp_dir.cleanup()

    def test_01_framing_20_large_base64_packets(self) -> None:
        """Gửi liên tiếp 20 gói JSON chứa 200KB Base64 qua socketpair không dính/cắt gói."""
        sock_a, sock_b = socket.socketpair()
        try:
            blob_200kb = base64.b64encode(os.urandom(150_000)).decode("ascii")
            received: List[Dict[str, Any]] = []

            def _reader() -> None:
                for _ in range(20):
                    frame = recv_frame(sock_b)
                    if frame is not None:
                        received.append(frame)

            t = threading.Thread(target=_reader, daemon=True)
            t.start()

            for i in range(20):
                req = make_request("test.upload", {"seq": i, "image_b64": blob_200kb})
                send_frame(sock_a, req)

            t.join(timeout=10.0)
            self.assertEqual(len(received), 20)
            for idx, item in enumerate(received):
                self.assertEqual(item["payload"]["seq"], idx)
                self.assertEqual(len(item["payload"]["image_b64"]), len(blob_200kb))
        finally:
            sock_a.close()
            sock_b.close()

    def test_02_validators_and_base64_save(self) -> None:
        """Kiểm tra chuẩn hóa biển số VN, validate thông tin và lưu ảnh Base64."""
        self.assertEqual(normalize_plate("51h88899"), "51H-888.99")
        self.assertEqual(normalize_plate("59a112345"), "59A1-123.45")
        self.assertTrue(validate_plate("51H-888.99", "O_TO")[0])
        self.assertTrue(validate_plate("59A1-123.45", "XE_MAY")[0])
        self.assertFalse(validate_plate("ABC", "O_TO")[0])
        self.assertTrue(validate_email("bien.leader@daotien.vn"))
        self.assertTrue(validate_phone("0909123456"))
        self.assertTrue(validate_cccd("079200012345"))

        b64 = base64.b64encode(b"\xff\xd8\xff\xe0FAKE_JPEG_DATA").decode("ascii")
        saved = save_base64_image(b64, self.tmp_path / "uploads", prefix="cavet_51H")
        self.assertTrue(Path(saved).is_file())

    def test_03_concurrent_request_response_and_server_push(self) -> None:
        """Client gửi 10 request liên tiếp trong khi Server bắn xen kẽ 3 gói PUSH."""
        _app = QApplication.instance() or QApplication([])

        def _echo(_sess: ClientSession, payload: Dict[str, Any]) -> Dict[str, Any]:
            return {"seq": payload.get("seq")}

        self.router.register("test.echo", _echo, is_mutation=False, require_auth=False)

        srv = SocketServer(
            "127.0.0.1",
            0,
            router=self.router,
            session_manager=self.session_manager,
            file_logger=self.file_logger,
        )
        srv.start_in_thread()
        client = SocketClient("127.0.0.1", srv.bound_port)
        pushes: List[tuple[str, Dict[str, Any]]] = []
        client.push_received.connect(lambda ev, d: pushes.append((ev, d)))

        try:
            self.assertTrue(client.connect_to_server())

            # Gắn phiên vừa kết nối với một user để push_to_users định tuyến tới
            time.sleep(0.05)
            sessions = self.session_manager.list_sessions()
            self.assertEqual(len(sessions), 1)
            self.session_manager.bind_authenticated_user(
                sessions[0]["session_id"],
                {"ma_tai_khoan": 42, "ten_dang_nhap": "cudan42", "vai_tro": Roles.CU_DAN.value},
            )

            for i in range(10):
                if i in (2, 5, 8):
                    res_push = self.session_manager.push_to_users(
                        [42],
                        PushEvents.EMERGENCY_ALERT,
                        {"alert_no": i, "title": "Cảnh báo thử nghiệm"},
                    )
                    self.assertEqual(res_push["gui_thanh_cong"], [42])
                resp = client.call("test.echo", {"seq": i}, timeout=5.0)
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["data"]["seq"], i)

            # Đẩy event loop xử lý signal queued từ reader thread
            for _ in range(10):
                _app.processEvents()
                if len(pushes) >= 3:
                    break
                time.sleep(0.02)

            self.assertEqual(len(pushes), 3)
            self.assertEqual([p[1]["alert_no"] for p in pushes], [2, 5, 8])
        finally:
            client.disconnect()
            srv.stop()

    def test_04_session_manager_admin_rbac_and_reaper(self) -> None:
        """Kiểm tra chặn quyền ghi Admin, revoke session và reaper quét timeout."""
        self.router.register(
            "test.mutate",
            lambda _s, _p: {"updated": True},
            allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
            is_mutation=True,
            require_auth=True,
        )
        sock_a, sock_b = socket.socketpair()
        try:
            sess = self.session_manager.register_connection(sock_a, ("127.0.0.1", 55000))
            self.session_manager.bind_authenticated_user(
                sess.session_id,
                {"ma_tai_khoan": 1, "ten_dang_nhap": "admin01", "vai_tro": Roles.ADMIN.value},
            )

            resp = self.router.dispatch(sess, "test.mutate", {}, "req-admin-write")
            self.assertFalse(resp["ok"])
            self.assertEqual(resp["error_code"], "ADMIN_READ_ONLY")

            err_logs = self.file_logger.query_logs("error", filters={"ma_loi": "ADMIN_READ_ONLY"})
            self.assertEqual(len(err_logs), 1)

            # Giả lập timeout heartbeat quá 45s -> reaper phải dọn phiên và ghi PING_TIMEOUT
            sess.last_heartbeat_mono = time.monotonic() - 60
            removed = self.session_manager.reap_stale_sessions_once()
            self.assertEqual(removed, [sess.session_id])
            self.assertIsNone(self.session_manager.get_session(sess.session_id))
            timeout_logs = self.file_logger.query_logs("error", filters={"ma_loi": "PING_TIMEOUT"})
            self.assertEqual(len(timeout_logs), 1)
        finally:
            sock_a.close()
            sock_b.close()

    def test_05_state_store_persistence_and_file_logger(self) -> None:
        """Ghi 5 lượt xe vãng lai vào StateStore, nạp lại từ JSON và kiểm tra 4 loại FileLogger."""
        plates = ["51H-100.01", "51H-100.02", "51H-100.03", "59A1-200.04", "59A1-200.05"]
        for idx, p in enumerate(plates):
            self.state_store.add_visitor_session(
                bien_so_xe=p,
                ma_loai_xe="O_TO" if idx < 3 else "XE_MAY",
                ma_khu_vuc="KHU_C",
                ma_vi_tri=f"C-0{idx + 1}" if idx < 3 else None,
            )
        self.assertEqual(self.state_store.count_open_visitors(), 5)

        reloaded = StateStore(self.tmp_path / "state" / "active_vang_lai.json")
        self.assertEqual(reloaded.count_open_visitors(), 5)
        self.assertIsNotNone(reloaded.get_open_visitor_by_plate("51h10002"))
        popped = reloaded.pop_visitor_session("51H-100.02")
        self.assertIsNotNone(popped)
        self.assertEqual(reloaded.count_open_visitors(), 4)

        self.file_logger.log_auth("admin01", AuthResults.THANH_CONG, 1, Roles.ADMIN.value, "127.0.0.1")
        self.file_logger.log_audit(2, "quanly01", Roles.QUAN_LY.value, "KHOA_TK", "tai_khoan", 8, "hoat_dong", "tam_khoa")
        self.file_logger.log_error("Lỗi test", LogLevels.ERROR, "unit_test", "E_TEST")
        self.file_logger.log_parking_history("LG-1", "vang_lai", "51H-100.01", "O_TO", "KHU_C", "C-01")

        self.assertEqual(len(self.file_logger.query_logs("auth")), 1)
        self.assertEqual(len(self.file_logger.query_logs("audit", filters={"ma_doi_tuong": 8})), 1)
        self.assertEqual(len(self.file_logger.query_logs("error", filters={"muc_do": "ERROR"})), 1)
        self.assertEqual(len(self.file_logger.query_logs("parking_history", filters={"bien_so_xe": "51H-100.01"})), 1)

    def test_06_otp_service_4_steps_and_cooldown(self) -> None:
        """Kiểm tra đủ 4 bước OTP (request, resend cooldown, verify, consume reset_token)."""
        sent_emails: List[tuple[str, str]] = []
        cfg = ServerConfig(otp_expire_seconds=300, otp_resend_cooldown_seconds=60)
        otp_svc = OTPService(config=cfg, email_sender=lambda to, _s, body: sent_emails.append((to, body)))

        req = otp_svc.request_otp("resident@daotien.vn", ma_tai_khoan=15)
        req_id = str(req["ma_yeu_cau_otp"])
        self.assertEqual(len(sent_emails), 1)

        with self.assertRaises(ValueError):
            otp_svc.resend_otp(req_id)

        # Cho phép qua cooldown để kiểm tra resend vô hiệu mã cũ
        otp_svc._records[req_id].resend_available_at = time.monotonic() - 1
        old_code = otp_svc._records[req_id].otp_code
        otp_svc.resend_otp(req_id)
        new_code = otp_svc._records[req_id].otp_code
        self.assertEqual(len(sent_emails), 2)

        if old_code != new_code:
            with self.assertRaises(ValueError):
                otp_svc.verify_otp(req_id, old_code)

        verified = otp_svc.verify_otp(req_id, new_code)
        reset_token = str(verified["reset_token"])
        self.assertEqual(otp_svc.consume_reset_token(reset_token), 15)
        with self.assertRaises(ValueError):
            otp_svc.consume_reset_token(reset_token)

    def test_07_monitor_admin_handler_1_1_and_4_2_to_4_4(self) -> None:
        """Kiểm tra handler 1.1 Dashboard và 4.2, 4.3, 4.4 Admin với FakeDB."""
        register_monitor_admin_routes(
            self.router,
            session_manager=self.session_manager,
            file_logger=self.file_logger,
            state_store=self.state_store,
            db_provider=_FakeDB(),
        )
        self.state_store.add_visitor_session("51H-999.88", "O_TO", "HAM_B1", "B1-05")
        self.file_logger.log_error("Camera cổng vào mất kết nối", LogLevels.WARN, "gate_in", "CAM_OFFLINE")
        self.file_logger.log_audit(2, "quanly01", Roles.QUAN_LY.value, "KHOA_TK", "tai_khoan", 2, "hoat_dong", "tam_khoa")
        self.file_logger.log_auth("quanly01", AuthResults.THANH_CONG, 2, Roles.QUAN_LY.value, "10.0.0.5")

        sock_a, sock_b = socket.socketpair()
        try:
            sess = self.session_manager.register_connection(sock_a, ("10.0.0.10", 51000))
            self.session_manager.bind_authenticated_user(
                sess.session_id,
                {"ma_tai_khoan": 1, "ten_dang_nhap": "admin01", "vai_tro": Roles.ADMIN.value},
            )

            r_dash = self.router.dispatch(sess, Actions.DASHBOARD_SUMMARY, {}, "d1")
            self.assertTrue(r_dash["ok"])
            self.assertEqual(r_dash["data"]["pending_requests"]["tong_cho_duyet"], 1)
            self.assertEqual(r_dash["data"]["parking_capacity"]["xe_vang_lai_dang_gui"], 1)
            self.assertGreaterEqual(r_dash["data"]["system_alerts"]["tong_canh_bao"], 1)

            r_accs = self.router.dispatch(sess, Actions.ADMIN_LIST_ACCOUNTS, {"only_managers": True}, "a1")
            self.assertTrue(r_accs["ok"])
            self.assertEqual(r_accs["data"]["total"], 1)

            r_acc_det = self.router.dispatch(sess, Actions.ADMIN_GET_ACCOUNT, {"ma_tai_khoan": 2}, "a2")
            self.assertTrue(r_acc_det["ok"])
            self.assertEqual(r_acc_det["data"]["last_login"]["ip_address"], "10.0.0.5")

            r_acc_hist = self.router.dispatch(sess, Actions.ADMIN_ACCOUNT_HISTORY, {"ma_tai_khoan": 2}, "a3")
            self.assertTrue(r_acc_hist["ok"])
            self.assertEqual(r_acc_hist["data"]["total"], 1)

            r_sess = self.router.dispatch(sess, Actions.ADMIN_LIST_SESSIONS, {}, "s1")
            self.assertTrue(r_sess["ok"])
            self.assertEqual(r_sess["data"]["total"], 1)

            r_logs = self.router.dispatch(sess, Actions.ADMIN_QUERY_LOGS, {"log_type": "error"}, "l1")
            self.assertTrue(r_logs["ok"])
            self.assertEqual(r_logs["data"]["total"], 1)
        finally:
            sock_a.close()
            sock_b.close()

    def test_08_ui_dashboard_and_admin_read_only_views(self) -> None:
        """Kiểm tra render offscreen DashboardView (1.1) và cụm Admin Read-Only (4.2-4.8)."""
        _app = QApplication.instance() or QApplication([])

        dash = DashboardView()
        dash.apply_dashboard_data(
            {
                "generated_at": "2026-11-16T09:00:00",
                "ky_thu_thang_nam": "11/2026",
                "pending_requests": {"tong_cho_duyet": 2, "so_yeu_cau_xe": 1, "so_yeu_cau_mua_cho": 1, "items": []},
                "parking_capacity": {"tong_xe_dang_gui": 5, "cho_con_trong": 35, "xe_cu_dan_dang_gui": 4, "xe_vang_lai_dang_gui": 1, "khu_vuc": []},
                "revenue_summary": {"tong_doanh_thu_hien_thi": 2_000_000, "da_thu_cu_dan": 1_800_000, "con_no_cu_dan": 200_000, "doanh_thu_vang_lai_hom_nay": 200_000},
                "system_alerts": {"tong_canh_bao": 0, "items": []},
            }
        )
        self.assertEqual(dash.card_pending.lbl_value.text(), "2")

        acc_view = AdminAccountsView()
        acc_view.set_accounts(
            [{"ma_tai_khoan": 2, "ten_dang_nhap": "quanly01", "ho_ten": "QL", "vai_tro": "quan_ly", "trang_thai": "hoat_dong"}]
        )
        self.assertEqual(acc_view.tbl_accounts.rowCount(), 1)

        sess_view = AdminSessionsView()
        sess_view.set_sessions(
            [{"session_id": "s1", "endpoint": "127.0.0.1:5000", "ten_dang_nhap": "quanly01", "vai_tro": "quan_ly", "trang_thai_ket_noi": "online"}]
        )
        self.assertEqual(sess_view.tbl_sessions.rowCount(), 1)

        logs_view = AdminLogsView()
        logs_view.set_logs("audit", [{"timestamp": "2026-11-16", "ten_dang_nhap": "ql", "vai_tro": "quan_ly", "loai_thao_tac": "DUYET", "doi_tuong": "xe", "ma_doi_tuong": 1}])
        self.assertEqual(logs_view.tbl_audit.rowCount(), 1)

        child = QWidget()
        lay = QVBoxLayout(child)
        btn_edit = QPushButton("Sửa bảng giá")
        btn_view = QPushButton("Xem chi tiết")
        lay.addWidget(btn_edit)
        lay.addWidget(btn_view)
        _wrapper = AdminReadOnlyWrapper("4.6.4 Bảng giá (Chỉ xem)", child_widget=child)
        self.assertFalse(btn_edit.isEnabled())
        self.assertTrue(btn_view.isEnabled())

    def test_09_socket_server_graceful_stop_no_deadlock(self) -> None:
        """Kiểm tra dừng SocketServer an toàn, nhanh chóng và idempotent (fix Ctrl+C)."""
        with tempfile.TemporaryDirectory() as tmp:
            logger = FileLogger(Path(tmp) / "logs")
            sess_mgr = SessionManager(file_logger=logger)
            srv = SocketServer(
                host="127.0.0.1",
                port=0,
                router=Router(file_logger=logger),
                session_manager=sess_mgr,
                file_logger=logger,
            )
            thread = srv.start_in_thread()
            client = SocketClient(host="127.0.0.1", port=srv.bound_port)
            self.assertTrue(client.connect_to_server(timeout=2.0))
            t0 = time.monotonic()
            srv.stop()
            srv.stop()  # gọi lại lần 2 phải idempotent
            elapsed = time.monotonic() - t0
            client.disconnect()
            self.assertLess(elapsed, 2.0)
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main(verbosity=2)
