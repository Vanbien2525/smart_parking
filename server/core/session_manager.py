"""Quản lý toàn bộ phiên kết nối TCP Socket trên RAM, Heartbeat Reaper và Server Push."""

import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from server.core.file_logger import FileLogger
from shared.constants import ConnectionStatus, LogLevels
from shared.protocol import PushEvents, make_push, send_frame


@dataclass
class ClientSession:
    """Thông tin một phiên kết nối TCP Client trên RAM Server."""

    session_id: str
    sock: socket.socket
    ip_address: str
    port: int
    send_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    ma_tai_khoan: Optional[int] = None
    ten_dang_nhap: Optional[str] = None
    vai_tro: Optional[str] = None
    ho_ten: Optional[str] = None
    ma_cu_dan: Optional[int] = None
    ma_nhan_vien: Optional[int] = None
    ma_can_ho: Optional[str] = None
    connected_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_heartbeat: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_heartbeat_mono: float = field(default_factory=time.monotonic)
    trang_thai_ket_noi: str = ConnectionStatus.ONLINE.value

    def send_message(self, message: Dict[str, Any]) -> bool:
        """Gửi gói tin thread-safe qua socket của phiên; trả về False nếu lỗi đường truyền."""
        with self.send_lock:
            try:
                send_frame(self.sock, message)
                return True
            except OSError:
                return False

    def close_socket(self) -> None:
        """Đóng socket an toàn không ném ngoại lệ."""
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def to_public_dict(self) -> Dict[str, Any]:
        """Xuất thông tin phiên an toàn phục vụ Admin (4.3.1, 4.3.2)."""
        idle_seconds = max(0, int(time.monotonic() - self.last_heartbeat_mono))
        return {
            "session_id": self.session_id,
            "ip_address": self.ip_address,
            "port": self.port,
            "endpoint": f"{self.ip_address}:{self.port}",
            "ma_tai_khoan": self.ma_tai_khoan,
            "ten_dang_nhap": self.ten_dang_nhap or "(Chưa đăng nhập)",
            "vai_tro": self.vai_tro or "anonymous",
            "ho_ten": self.ho_ten or "",
            "ma_cu_dan": self.ma_cu_dan,
            "ma_nhan_vien": self.ma_nhan_vien,
            "ma_can_ho": self.ma_can_ho,
            "connected_at": self.connected_at,
            "last_heartbeat": self.last_heartbeat,
            "idle_seconds": idle_seconds,
            "trang_thai_ket_noi": self.trang_thai_ket_noi,
        }


class SessionManager:
    """Quản lý tập trung toàn bộ ClientSession trên RAM, Server Push và dọn phiên timeout."""

    def __init__(
        self,
        file_logger: Optional[FileLogger] = None,
        heartbeat_timeout: int = 45,
        reap_interval: int = 10,
    ) -> None:
        self._sessions: Dict[str, ClientSession] = {}
        self._lock = threading.RLock()
        self._file_logger = file_logger
        self.heartbeat_timeout = heartbeat_timeout
        self.reap_interval = reap_interval
        self._stop_event = threading.Event()
        self._reaper_thread: Optional[threading.Thread] = None

    def register_connection(self, sock: socket.socket, addr: Tuple[str, int]) -> ClientSession:
        """Đăng ký một kết nối TCP mới vào RAM."""
        session = ClientSession(
            session_id=str(uuid.uuid4()),
            sock=sock,
            ip_address=addr[0],
            port=int(addr[1]),
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def bind_authenticated_user(
        self, session_id: str, account_dict: Dict[str, Any]
    ) -> Optional[ClientSession]:
        """Gắn thông tin tài khoản đã xác thực vào phiên kết nối hiện tại."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.ma_tai_khoan = account_dict.get("ma_tai_khoan")
            session.ten_dang_nhap = account_dict.get("ten_dang_nhap")
            session.vai_tro = account_dict.get("vai_tro")
            session.ho_ten = account_dict.get("ho_ten")
            session.ma_cu_dan = account_dict.get("ma_cu_dan")
            session.ma_nhan_vien = account_dict.get("ma_nhan_vien")
            session.ma_can_ho = account_dict.get("ma_can_ho")
            session.last_heartbeat = datetime.now().isoformat(timespec="seconds")
            session.last_heartbeat_mono = time.monotonic()
            session.trang_thai_ket_noi = ConnectionStatus.ONLINE.value
            return session

    def clear_authenticated_user(self, session_id: str) -> Optional[ClientSession]:
        """Gỡ thông tin người dùng khi đăng xuất nhưng vẫn giữ kết nối TCP."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.ma_tai_khoan = None
            session.ten_dang_nhap = None
            session.vai_tro = None
            session.ho_ten = None
            session.ma_cu_dan = None
            session.ma_nhan_vien = None
            session.ma_can_ho = None
            return session

    def touch(self, session_id: str) -> None:
        """Cập nhật thời điểm heartbeat mỗi khi nhận PING hoặc REQUEST."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_heartbeat = datetime.now().isoformat(timespec="seconds")
                session.last_heartbeat_mono = time.monotonic()

    def unregister_connection(self, session_id: str) -> Optional[ClientSession]:
        """Xóa phiên khỏi RAM khi Client ngắt kết nối."""
        with self._lock:
            return self._sessions.pop(session_id, None)

    def get_session(self, session_id: str) -> Optional[ClientSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(
        self,
        vai_tro: Optional[str] = None,
        ten_dang_nhap: Optional[str] = None,
        authenticated_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách phiên kết nối đang mở trên RAM (phục vụ Admin 4.3.1)."""
        with self._lock:
            snapshot = list(self._sessions.values())

        results: List[Dict[str, Any]] = []
        role_filter = (vai_tro or "").strip()
        user_filter = (ten_dang_nhap or "").strip().lower()

        for sess in snapshot:
            if authenticated_only and sess.ma_tai_khoan is None:
                continue
            if role_filter and (sess.vai_tro or "") != role_filter:
                continue
            if user_filter and user_filter not in (sess.ten_dang_nhap or "").lower():
                continue
            results.append(sess.to_public_dict())

        results.sort(key=lambda item: item["connected_at"], reverse=True)
        return results

    def revoke_user_sessions(
        self,
        ma_tai_khoan: int,
        reason: str = "Tài khoản đã bị khóa hoặc thay đổi mật khẩu.",
    ) -> int:
        """Đẩy gói SESSION_REVOKED và đóng mọi phiên đang mở của tài khoản."""
        with self._lock:
            targets = [
                sess for sess in self._sessions.values()
                if sess.ma_tai_khoan == ma_tai_khoan
            ]

        push_msg = make_push(
            PushEvents.SESSION_REVOKED,
            {"ma_tai_khoan": ma_tai_khoan, "reason": reason},
        )
        revoked_count = 0
        for sess in targets:
            sess.send_message(push_msg)
            sess.close_socket()
            self.unregister_connection(sess.session_id)
            revoked_count += 1
        return revoked_count

    def push_to_users(
        self,
        account_ids: Iterable[int],
        event: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[int]]:
        """Đẩy sự kiện PUSH tới danh sách ma_tai_khoan và báo cáo kết quả gửi."""
        target_ids = list(dict.fromkeys(int(uid) for uid in account_ids if uid is not None))
        with self._lock:
            sessions_by_user: Dict[int, List[ClientSession]] = {}
            for sess in self._sessions.values():
                if sess.ma_tai_khoan is not None:
                    sessions_by_user.setdefault(int(sess.ma_tai_khoan), []).append(sess)

        push_msg = make_push(event, data)
        gui_thanh_cong: List[int] = []
        gui_that_bai: List[int] = []

        for uid in target_ids:
            user_sessions = sessions_by_user.get(uid, [])
            if not user_sessions:
                gui_that_bai.append(uid)
                continue
            delivered = False
            for sess in user_sessions:
                if sess.send_message(push_msg):
                    delivered = True
            if delivered:
                gui_thanh_cong.append(uid)
            else:
                gui_that_bai.append(uid)

        return {"gui_thanh_cong": gui_thanh_cong, "gui_that_bai": gui_that_bai}

    def push_to_roles(
        self,
        roles: Iterable[str],
        event: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[int]]:
        """Đẩy sự kiện PUSH tới tất cả phiên đang online thuộc các vai trò chỉ định."""
        role_set = {str(r).strip() for r in roles if r}
        send_all = "tat_ca" in role_set or not role_set

        with self._lock:
            target_account_ids = [
                int(sess.ma_tai_khoan)
                for sess in self._sessions.values()
                if sess.ma_tai_khoan is not None and (send_all or sess.vai_tro in role_set)
            ]
        return self.push_to_users(target_account_ids, event, data)

    def reap_stale_sessions_once(self) -> List[str]:
        """Quét và đóng các phiên quá hạn heartbeat (> heartbeat_timeout giây)."""
        now_mono = time.monotonic()
        stale: List[ClientSession] = []
        with self._lock:
            for sess in list(self._sessions.values()):
                if now_mono - sess.last_heartbeat_mono > self.heartbeat_timeout:
                    self._sessions.pop(sess.session_id, None)
                    sess.trang_thai_ket_noi = ConnectionStatus.TIMEOUT.value
                    stale.append(sess)

        removed_ids: List[str] = []
        for sess in stale:
            sess.close_socket()
            removed_ids.append(sess.session_id)
            if self._file_logger is not None:
                self._file_logger.log_error(
                    noi_dung=f"Ngắt kết nối phiên quá hạn heartbeat: {sess.ip_address}:{sess.port}",
                    muc_do=LogLevels.WARN,
                    nguon="session_manager",
                    ma_loi="PING_TIMEOUT",
                    chi_tiet={
                        "session_id": sess.session_id,
                        "ma_tai_khoan": sess.ma_tai_khoan,
                        "ten_dang_nhap": sess.ten_dang_nhap,
                        "last_heartbeat": sess.last_heartbeat,
                    },
                )
        return removed_ids

    def _reaper_loop(self) -> None:
        while not self._stop_event.wait(timeout=self.reap_interval):
            try:
                self.reap_stale_sessions_once()
            except Exception as exc:
                if self._file_logger is not None:
                    self._file_logger.log_error(
                        noi_dung=f"Lỗi vòng quét phiên timeout: {exc}",
                        muc_do=LogLevels.ERROR,
                        nguon="session_manager",
                        ma_loi="REAPER_ERROR",
                    )

    def start_reaper(self) -> None:
        """Khởi chạy luồng nền dọn phiên mất kết nối đột ngột."""
        if self._reaper_thread is not None and self._reaper_thread.is_alive():
            return
        self._stop_event.clear()
        self._reaper_thread = threading.Thread(
            target=self._reaper_loop,
            name="SessionReaperThread",
            daemon=True,
        )
        self._reaper_thread.start()

    def stop_reaper(self) -> None:
        """Dừng luồng nền dọn phiên và đóng toàn bộ socket hiện tại."""
        self._stop_event.set()
        if self._reaper_thread is not None and self._reaper_thread.is_alive():
            self._reaper_thread.join(timeout=2.0)
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for sess in sessions:
            sess.close_socket()
