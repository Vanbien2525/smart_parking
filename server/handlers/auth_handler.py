"""Handler Xác thực (1.2, 2.1, 3.1, 4.1), OTP Email 4 bước & Quản lý Nhân sự (1.3)."""

from typing import Any, Dict, Optional

import bcrypt

from server.core.file_logger import FileLogger
from server.core.otp_service import OTPService
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession, SessionManager
from shared.constants import AccountStatus, AuthResults, Roles
from shared.protocol import Actions
from shared.validators import validate_cccd, validate_email, validate_phone


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


def validate_new_password(new_password: str, confirm_password: str) -> str:
    """Kiểm tra độ mạnh cơ bản và xác nhận mật khẩu mới."""
    if not new_password or len(new_password) < 6:
        raise BusinessError("Mật khẩu mới phải có ít nhất 6 ký tự.")
    if len(new_password) > 100:
        raise BusinessError("Mật khẩu mới không được vượt quá 100 ký tự.")
    if new_password != confirm_password:
        raise BusinessError("Mật khẩu xác nhận không khớp.")
    return new_password


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

    # ==========================================================================
    # CHỨC NĂNG 1.2.2 / 2.1.2 / 3.1.2 / 4.1.2: ĐỔI MẬT KHẨU CÁ NHÂN
    # ==========================================================================
    def handle_change_password(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Kiểm tra mật khẩu hiện tại, băm mật khẩu mới và ghi audit log."""
        if session.ma_tai_khoan is None:
            raise BusinessError("Phiên đăng nhập không hợp lệ.")

        mat_khau_hien_tai = str(payload.get("mat_khau_hien_tai") or "")
        mat_khau_moi = str(payload.get("mat_khau_moi") or "")
        xac_nhan = str(payload.get("xac_nhan_mat_khau") or "")

        if not mat_khau_hien_tai:
            raise BusinessError("Vui lòng nhập mật khẩu hiện tại.")
        validate_new_password(mat_khau_moi, xac_nhan)
        if mat_khau_moi == mat_khau_hien_tai:
            raise BusinessError("Mật khẩu mới phải khác mật khẩu hiện tại.")

        db = self._db()
        row = db.fetch_one(
            "SELECT ma_tai_khoan, mat_khau_bam FROM tai_khoan WHERE ma_tai_khoan = %s",
            (int(session.ma_tai_khoan),),
        )
        if not row or not verify_password(mat_khau_hien_tai, str(row.get("mat_khau_bam") or "")):
            raise BusinessError("Mật khẩu hiện tại không chính xác.")

        new_hash = hash_password(mat_khau_moi)
        db.execute(
            "UPDATE tai_khoan SET mat_khau_bam = %s WHERE ma_tai_khoan = %s",
            (new_hash, int(session.ma_tai_khoan)),
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="DOI_MAT_KHAU",
            doi_tuong="tai_khoan",
            ma_doi_tuong=session.ma_tai_khoan,
            gia_tri_cu={"password": "***"},
            gia_tri_moi={"password": "***"},
        )
        return {"updated": True, "message": "Đổi mật khẩu thành công."}

    # ==========================================================================
    # CHỨC NĂNG 4.1.3 - 4.1.6 & 1.2.3 / 2.1.3 / 3.1.3: QUÊN MẬT KHẨU OTP 4 BƯỚC
    # ==========================================================================
    def _require_otp_service(self) -> OTPService:
        if self.otp_service is None:
            raise BusinessError("Dịch vụ OTP chưa được khởi tạo trên Server.")
        return self.otp_service

    def handle_request_otp(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.1.3: Tra cứu email trong cu_dan/nhan_vien và gửi mã OTP 6 số qua SMTP."""
        email = str(payload.get("email") or "").strip().lower()
        if not validate_email(email):
            raise BusinessError("Địa chỉ email không hợp lệ.")

        db = self._db()
        account = db.fetch_one(
            """
            SELECT tk.ma_tai_khoan, tk.ten_dang_nhap, tk.vai_tro, tk.trang_thai
            FROM tai_khoan tk
            LEFT JOIN cu_dan cd ON cd.ma_tai_khoan = tk.ma_tai_khoan
            LEFT JOIN nhan_vien nv ON nv.ma_tai_khoan = tk.ma_tai_khoan
            WHERE LOWER(COALESCE(cd.email, nv.email, '')) = %s
            LIMIT 1
            """,
            (email,),
        )
        if not account:
            raise BusinessError("Không tìm thấy tài khoản nào gắn với địa chỉ email này.")
        if account.get("trang_thai") != AccountStatus.HOAT_DONG.value:
            raise BusinessError("Tài khoản gắn với email này đang tạm khóa, không thể khôi phục mật khẩu.")

        otp_svc = self._require_otp_service()
        try:
            return otp_svc.request_otp(email=email, ma_tai_khoan=int(account["ma_tai_khoan"]))
        except RuntimeError as exc:
            raise BusinessError(str(exc)) from exc

    def handle_resend_otp(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.1.4: Kiểm tra cooldown 60s, vô hiệu mã cũ và gửi lại OTP mới."""
        ma_yeu_cau_otp = str(payload.get("ma_yeu_cau_otp") or "").strip()
        if not ma_yeu_cau_otp:
            raise BusinessError("Thiếu mã yêu cầu OTP (`ma_yeu_cau_otp`).")
        otp_svc = self._require_otp_service()
        try:
            return otp_svc.resend_otp(ma_yeu_cau_otp)
        except RuntimeError as exc:
            raise BusinessError(str(exc)) from exc

    def handle_verify_otp(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.1.5: Xác minh mã OTP 6 số và cấp `reset_token` có thời hạn."""
        ma_yeu_cau_otp = str(payload.get("ma_yeu_cau_otp") or "").strip()
        ma_otp = str(payload.get("ma_otp") or "").strip()
        if not ma_yeu_cau_otp or not ma_otp:
            raise BusinessError("Vui lòng nhập đầy đủ mã yêu cầu và mã OTP 6 số.")
        otp_svc = self._require_otp_service()
        return otp_svc.verify_otp(ma_yeu_cau_otp, ma_otp)

    def handle_reset_password(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.1.6: Đặt lại mật khẩu mới bằng `reset_token` và thu hồi mọi phiên cũ."""
        reset_token = str(payload.get("reset_token") or "").strip()
        mat_khau_moi = str(payload.get("mat_khau_moi") or "")
        xac_nhan = str(payload.get("xac_nhan") or payload.get("xac_nhan_mat_khau") or "")
        if not reset_token:
            raise BusinessError("Thiếu `reset_token` xác thực OTP.")
        validate_new_password(mat_khau_moi, xac_nhan)

        otp_svc = self._require_otp_service()
        ma_tai_khoan = otp_svc.consume_reset_token(reset_token)
        new_hash = hash_password(mat_khau_moi)

        db = self._db()
        acc = db.fetch_one(
            "SELECT ma_tai_khoan, ten_dang_nhap, vai_tro FROM tai_khoan WHERE ma_tai_khoan = %s",
            (ma_tai_khoan,),
        )
        db.execute(
            "UPDATE tai_khoan SET mat_khau_bam = %s WHERE ma_tai_khoan = %s",
            (new_hash, ma_tai_khoan),
        )
        revoked = self.session_manager.revoke_user_sessions(
            ma_tai_khoan,
            "Đặt lại mật khẩu qua OTP",
        )
        self.file_logger.log_audit(
            ma_tai_khoan=ma_tai_khoan,
            ten_dang_nhap=(acc or {}).get("ten_dang_nhap") or "",
            vai_tro=(acc or {}).get("vai_tro") or "",
            loai_thao_tac="DAT_LAI_MAT_KHAU_OTP",
            doi_tuong="tai_khoan",
            ma_doi_tuong=ma_tai_khoan,
            gia_tri_cu={"password": "***"},
            gia_tri_moi={"password": "***", "revoked_sessions": revoked},
        )
        return {
            "reset": True,
            "ma_tai_khoan": ma_tai_khoan,
            "revoked_sessions": revoked,
            "message": "Đặt lại mật khẩu thành công. Vui lòng đăng nhập lại.",
        }

    # ==========================================================================
    # CHỨC NĂNG 1.2.4 / 2.1.4 / 3.1.4 / 4.1.7: XEM & CẬP NHẬT HỒ SƠ CÁ NHÂN
    # ==========================================================================
    def _fetch_account_row(self, db: Any, ma_tai_khoan: int) -> Optional[Dict[str, Any]]:
        return db.fetch_one(
            """
            SELECT tk.ma_tai_khoan,
                   tk.ten_dang_nhap,
                   tk.vai_tro,
                   tk.trang_thai,
                   tk.ly_do_khoa,
                   tk.ngay_tao,
                   cd.ma_cu_dan,
                   cd.ma_can_ho,
                   cd.ho_ten AS cd_ho_ten,
                   cd.so_cccd AS cd_cccd,
                   cd.so_dien_thoai AS cd_sdt,
                   cd.email AS cd_email,
                   cd.dia_chi,
                   ch.toa_nha,
                   ch.so_tang,
                   ch.so_cho_oto_mac_dinh,
                   ch.so_cho_oto_mua_them,
                   nv.ma_nhan_vien,
                   nv.ma_nhan_vien_code,
                   nv.ho_ten AS nv_ho_ten,
                   nv.so_cccd AS nv_cccd,
                   nv.so_dien_thoai AS nv_sdt,
                   nv.email AS nv_email,
                   nv.chuc_vu
            FROM tai_khoan tk
            LEFT JOIN cu_dan cd ON cd.ma_tai_khoan = tk.ma_tai_khoan
            LEFT JOIN can_ho ch ON ch.ma_can_ho = cd.ma_can_ho
            LEFT JOIN nhan_vien nv ON nv.ma_tai_khoan = tk.ma_tai_khoan
            WHERE tk.ma_tai_khoan = %s
            """,
            (int(ma_tai_khoan),),
        )

    def _check_unique_contact(
        self,
        db: Any,
        *,
        so_cccd: str,
        email: str,
        exclude_tai_khoan: Optional[int] = None,
    ) -> None:
        """Kiểm tra trùng CCCD hoặc Email trên toàn bộ bảng cu_dan & nhan_vien."""
        excl = int(exclude_tai_khoan) if exclude_tai_khoan is not None else -1
        dup = db.fetch_one(
            """
            SELECT 'cccd' AS field FROM cu_dan WHERE so_cccd = %s AND ma_tai_khoan <> %s
            UNION ALL
            SELECT 'cccd' AS field FROM nhan_vien WHERE so_cccd = %s AND ma_tai_khoan <> %s
            UNION ALL
            SELECT 'email' AS field FROM cu_dan WHERE LOWER(email) = LOWER(%s) AND ma_tai_khoan <> %s
            UNION ALL
            SELECT 'email' AS field FROM nhan_vien WHERE LOWER(email) = LOWER(%s) AND ma_tai_khoan <> %s
            LIMIT 1
            """,
            (so_cccd, excl, so_cccd, excl, email, excl, email, excl),
        )
        if dup:
            if dup.get("field") == "cccd":
                raise BusinessError(f"Số CCCD '{so_cccd}' đã được sử dụng bởi tài khoản khác.")
            raise BusinessError(f"Email '{email}' đã được sử dụng bởi tài khoản khác.")

    def handle_get_profile(
        self, session: ClientSession, _payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.2.4 / 2.1.4 / 3.1.4 / 4.1.7: Xem hồ sơ cá nhân của tài khoản đang đăng nhập."""
        if session.ma_tai_khoan is None:
            raise BusinessError("Phiên đăng nhập không hợp lệ.")
        db = self._db()
        row = self._fetch_account_row(db, int(session.ma_tai_khoan))
        if not row:
            raise BusinessError("Không tìm thấy hồ sơ tài khoản.")
        profile = self._build_public_user(row)
        profile["ngay_tao"] = row.get("ngay_tao")
        profile["toa_nha"] = row.get("toa_nha")
        profile["so_tang"] = row.get("so_tang")
        profile["so_cho_oto_mac_dinh"] = row.get("so_cho_oto_mac_dinh")
        profile["so_cho_oto_mua_them"] = row.get("so_cho_oto_mua_them")
        return {"profile": profile}

    def handle_update_profile(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.2.4 / 2.1.4 / 3.1.4: Cập nhật hồ sơ cá nhân (chặn Admin 4.1.7 chỉ xem)."""
        if session.ma_tai_khoan is None:
            raise BusinessError("Phiên đăng nhập không hợp lệ.")
        if session.vai_tro == Roles.ADMIN.value:
            raise PermissionError("Tài khoản Admin chỉ có quyền xem hồ sơ cá nhân.")

        db = self._db()
        current_row = self._fetch_account_row(db, int(session.ma_tai_khoan))
        if not current_row:
            raise BusinessError("Không tìm thấy hồ sơ tài khoản.")

        old_profile = self._build_public_user(current_row)
        ho_ten = str(payload.get("ho_ten") if payload.get("ho_ten") is not None else old_profile["ho_ten"]).strip()
        so_cccd = str(payload.get("so_cccd") if payload.get("so_cccd") is not None else old_profile["so_cccd"]).strip()
        so_dien_thoai = str(
            payload.get("so_dien_thoai") if payload.get("so_dien_thoai") is not None else old_profile["so_dien_thoai"]
        ).strip()
        email = str(payload.get("email") if payload.get("email") is not None else old_profile["email"]).strip().lower()
        dia_chi = str(payload.get("dia_chi") if payload.get("dia_chi") is not None else old_profile["dia_chi"]).strip()

        if not ho_ten:
            raise BusinessError("Họ và tên không được để trống.")
        if not validate_cccd(so_cccd):
            raise BusinessError("Số CCCD/CMND phải gồm 9 hoặc 12 chữ số.")
        if not validate_phone(so_dien_thoai):
            raise BusinessError("Số điện thoại không đúng định dạng Việt Nam.")
        if not validate_email(email):
            raise BusinessError("Địa chỉ email không hợp lệ.")

        self._check_unique_contact(
            db,
            so_cccd=so_cccd,
            email=email,
            exclude_tai_khoan=int(session.ma_tai_khoan),
        )

        if session.vai_tro == Roles.CU_DAN.value:
            db.execute(
                """
                UPDATE cu_dan
                SET ho_ten = %s, so_cccd = %s, so_dien_thoai = %s, email = %s, dia_chi = %s
                WHERE ma_tai_khoan = %s
                """,
                (ho_ten, so_cccd, so_dien_thoai, email, dia_chi or None, int(session.ma_tai_khoan)),
            )
        else:
            db.execute(
                """
                UPDATE nhan_vien
                SET ho_ten = %s, so_cccd = %s, so_dien_thoai = %s, email = %s
                WHERE ma_tai_khoan = %s
                """,
                (ho_ten, so_cccd, so_dien_thoai, email, int(session.ma_tai_khoan)),
            )

        session.ho_ten = ho_ten
        updated_row = self._fetch_account_row(db, int(session.ma_tai_khoan))
        new_profile = self._build_public_user(updated_row or current_row)
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="CAP_NHAT_HO_SO",
            doi_tuong="ho_so_ca_nhan",
            ma_doi_tuong=session.ma_tai_khoan,
            gia_tri_cu={
                "ho_ten": old_profile["ho_ten"],
                "so_cccd": old_profile["so_cccd"],
                "so_dien_thoai": old_profile["so_dien_thoai"],
                "email": old_profile["email"],
            },
            gia_tri_moi={
                "ho_ten": new_profile["ho_ten"],
                "so_cccd": new_profile["so_cccd"],
                "so_dien_thoai": new_profile["so_dien_thoai"],
                "email": new_profile["email"],
            },
        )
        return {"profile": new_profile}


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
    router.register(
        Actions.AUTH_CHANGE_PASSWORD,
        handler.handle_change_password,
        allowed_roles=None,
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.AUTH_REQUEST_OTP,
        handler.handle_request_otp,
        allowed_roles=None,
        is_mutation=False,
        require_auth=False,
    )
    router.register(
        Actions.AUTH_RESEND_OTP,
        handler.handle_resend_otp,
        allowed_roles=None,
        is_mutation=False,
        require_auth=False,
    )
    router.register(
        Actions.AUTH_VERIFY_OTP,
        handler.handle_verify_otp,
        allowed_roles=None,
        is_mutation=False,
        require_auth=False,
    )
    router.register(
        Actions.AUTH_RESET_PASSWORD,
        handler.handle_reset_password,
        allowed_roles=None,
        is_mutation=False,
        require_auth=False,
    )
    router.register(
        Actions.AUTH_GET_PROFILE,
        handler.handle_get_profile,
        allowed_roles=None,
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.AUTH_UPDATE_PROFILE,
        handler.handle_update_profile,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.CU_DAN.value],
        is_mutation=True,
        require_auth=True,
    )
    return handler
