"""Handler Xác thực (1.2, 2.1, 3.1, 4.1), OTP Email 4 bước & Quản lý Nhân sự (1.3)."""

from typing import Any, Dict, Optional

import bcrypt

from server.core.file_logger import FileLogger
from server.core.otp_service import OTPService
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession, SessionManager
from shared.constants import AccountStatus, AuthResults, Roles
from shared.protocol import Actions


def _resolve_db(db_provider: Any = None) -> Any:
    """Lấy module/provider DB mặc định nếu chưa truyền vào."""
    if db_provider is not None:
        return db_provider
    from server import db as default_db

    return default_db


def hash_password(raw_password: str) -> str:
    """Băm mật khẩu bằng bcrypt."""
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")


def verify_password(raw_password: str, hashed_password: str) -> bool:
    """Kiểm tra mật khẩu thô khớp với chuỗi bcrypt hash."""
    if not raw_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


class AuthHRHandler:
    """Xử lý các API Xác thực, Hồ sơ cá nhân, OTP khôi phục và Quản lý Nhân sự (1.3)."""

    def __init__(
        self,
        session_manager: SessionManager,
        file_logger: FileLogger,
        otp_service: Optional[OTPService] = None,
        db_provider: Any = None,
    ) -> None:
        self.session_manager = session_manager
        self.file_logger = file_logger
        self.otp_service = otp_service
        self._db_provider = db_provider

    def _db(self) -> Any:
        return _resolve_db(self._db_provider)

    @staticmethod
    def _build_public_user(row: Dict[str, Any]) -> Dict[str, Any]:
        """Chuẩn hóa thông tin tài khoản trả về Client (tuyệt đối ẩn mat_khau_bam)."""
        vai_tro = str(row.get("vai_tro") or "")
        ho_ten = row.get("cd_ho_ten") or row.get("nv_ho_ten") or row.get("ten_dang_nhap")
        return {
            "ma_tai_khoan": row.get("ma_tai_khoan"),
            "ten_dang_nhap": row.get("ten_dang_nhap"),
            "vai_tro": vai_tro,
            "trang_thai": row.get("trang_thai"),
            "ly_do_khoa": row.get("ly_do_khoa"),
            "ho_ten": ho_ten,
            "ma_cu_dan": row.get("ma_cu_dan"),
            "ma_can_ho": row.get("ma_can_ho"),
            "ma_nhan_vien": row.get("ma_nhan_vien"),
            "ma_nhan_vien_code": row.get("ma_nhan_vien_code"),
            "chuc_vu": row.get("chuc_vu"),
            "email": row.get("cd_email") or row.get("nv_email") or "",
            "so_dien_thoai": row.get("cd_sdt") or row.get("nv_sdt") or "",
            "so_cccd": row.get("cd_cccd") or row.get("nv_cccd") or "",
            "dia_chi": row.get("dia_chi") or "",
        }

    # ==========================================================================
    # CHỨC NĂNG 1.2.1 / 2.1.1 / 3.1.1 / 4.1.1: ĐĂNG NHẬP & ĐĂNG XUẤT
    # ==========================================================================
    def handle_login(self, session: ClientSession, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Xác thực tài khoản 4 vai trò, kiểm tra trạng thái khóa và bind vào SessionManager."""
        ten_dang_nhap = str(payload.get("ten_dang_nhap") or "").strip()
        mat_khau = str(payload.get("mat_khau") or "")
        if not ten_dang_nhap or not mat_khau:
            raise BusinessError("Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.")

        db = self._db()
        row = db.fetch_one(
            """
            SELECT tk.ma_tai_khoan,
                   tk.ten_dang_nhap,
                   tk.mat_khau_bam,
                   tk.vai_tro,
                   tk.trang_thai,
                   tk.ly_do_khoa,
                   cd.ma_cu_dan,
                   cd.ma_can_ho,
                   cd.ho_ten AS cd_ho_ten,
                   cd.so_cccd AS cd_cccd,
                   cd.so_dien_thoai AS cd_sdt,
                   cd.email AS cd_email,
                   cd.dia_chi,
                   nv.ma_nhan_vien,
                   nv.ma_nhan_vien_code,
                   nv.ho_ten AS nv_ho_ten,
                   nv.so_cccd AS nv_cccd,
                   nv.so_dien_thoai AS nv_sdt,
                   nv.email AS nv_email,
                   nv.chuc_vu
            FROM tai_khoan tk
            LEFT JOIN cu_dan cd ON cd.ma_tai_khoan = tk.ma_tai_khoan
            LEFT JOIN nhan_vien nv ON nv.ma_tai_khoan = tk.ma_tai_khoan
            WHERE tk.ten_dang_nhap = %s
            """,
            (ten_dang_nhap,),
        )
        if not row:
            self.file_logger.log_auth(
                ten_dang_nhap=ten_dang_nhap,
                ket_qua=AuthResults.THAT_BAI,
                ip_address=session.ip_address,
                ly_do="Tài khoản không tồn tại",
            )
            raise BusinessError("Tên đăng nhập hoặc mật khẩu không chính xác.")

        if not verify_password(mat_khau, str(row.get("mat_khau_bam") or "")):
            self.file_logger.log_auth(
                ten_dang_nhap=ten_dang_nhap,
                ket_qua=AuthResults.THAT_BAI,
                ma_tai_khoan=row.get("ma_tai_khoan"),
                vai_tro=row.get("vai_tro"),
                ip_address=session.ip_address,
                ly_do="Sai mật khẩu",
            )
            raise BusinessError("Tên đăng nhập hoặc mật khẩu không chính xác.")

        if row.get("trang_thai") != AccountStatus.HOAT_DONG.value:
            reason = str(row.get("ly_do_khoa") or "Tài khoản đang tạm khóa")
            self.file_logger.log_auth(
                ten_dang_nhap=ten_dang_nhap,
                ket_qua=AuthResults.BI_KHOA,
                ma_tai_khoan=row.get("ma_tai_khoan"),
                vai_tro=row.get("vai_tro"),
                ip_address=session.ip_address,
                ly_do=reason,
            )
            raise BusinessError(f"Tài khoản đang bị tạm khóa: {reason}")

        user_info = self._build_public_user(row)
        self.session_manager.bind_authenticated_user(session.session_id, user_info)
        self.file_logger.log_auth(
            ten_dang_nhap=ten_dang_nhap,
            ket_qua=AuthResults.THANH_CONG,
            ma_tai_khoan=user_info["ma_tai_khoan"],
            vai_tro=user_info["vai_tro"],
            ip_address=session.ip_address,
            ly_do="Đăng nhập thành công",
        )
        return {
            "session_id": session.session_id,
            "vai_tro": user_info["vai_tro"],
            "user": user_info,
        }

    def handle_logout(self, session: ClientSession, _payload: Dict[str, Any]) -> Dict[str, Any]:
        """1.2.5 / 2.1.5 / 3.1.5 / 4.1.8: Đăng xuất phiên hiện tại, giữ kết nối TCP."""
        ten_dang_nhap = session.ten_dang_nhap or ""
        ma_tai_khoan = session.ma_tai_khoan
        vai_tro = session.vai_tro
        self.session_manager.clear_authenticated_user(session.session_id)
        if ten_dang_nhap:
            self.file_logger.log_auth(
                ten_dang_nhap=ten_dang_nhap,
                ket_qua=AuthResults.THANH_CONG,
                ma_tai_khoan=ma_tai_khoan,
                vai_tro=vai_tro,
                ip_address=session.ip_address,
                ly_do="Đăng xuất",
            )
        return {"logged_out": True}


def register_auth_hr_routes(
    router: Router,
    session_manager: SessionManager,
    file_logger: FileLogger,
    otp_service: Optional[OTPService] = None,
    db_provider: Any = None,
) -> AuthHRHandler:
    """Đăng ký các route Xác thực, OTP và Quản lý Nhân sự lên Router."""
    handler = AuthHRHandler(
        session_manager=session_manager,
        file_logger=file_logger,
        otp_service=otp_service,
        db_provider=db_provider,
    )
    router.register(
        Actions.AUTH_LOGIN,
        handler.handle_login,
        allowed_roles=None,
        is_mutation=False,
        require_auth=False,
    )
    router.register(
        Actions.AUTH_LOGOUT,
        handler.handle_logout,
        allowed_roles=None,
        is_mutation=False,
        require_auth=True,
    )
    return handler
