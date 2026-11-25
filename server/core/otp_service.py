"""Dịch vụ quản lý OTP khôi phục mật khẩu trên RAM và gửi Email thật qua SMTP."""

import secrets
import smtplib
import threading
import time
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Dict, Optional

from server.config import ServerConfig
from server.core.email_provider import get_email_provider


@dataclass
class OTPRecord:
    request_id: str
    email: str
    ma_tai_khoan: int
    otp_code: str
    expires_at: float
    resend_available_at: float
    reset_token: Optional[str] = None
    reset_token_expires_at: float = 0.0
    verified: bool = False
    failed_attempts: int = 0


EmailSenderFn = Callable[[str, str, str], None]


class OTPService:
    """Quản lý vòng đời OTP 6 số và reset_token trên RAM (không lưu CSDL)."""

    MAX_VERIFY_ATTEMPTS = 5
    RESET_TOKEN_TTL_SECONDS = 600

    def __init__(
        self,
        config: Optional[ServerConfig] = None,
        email_sender: Optional[EmailSenderFn] = None,
    ) -> None:
        self._config = config or ServerConfig()
        if email_sender is not None:
            self._email_sender = email_sender
        else:
            provider = get_email_provider(self._config)
            self._email_sender = provider.send_email
        self._lock = threading.RLock()
        self._records: Dict[str, OTPRecord] = {}
        self._reset_tokens: Dict[str, str] = {}  # reset_token -> request_id

    def _generate_otp(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def _send_smtp_email(self, to_email: str, subject: str, body: str) -> None:
        """Gửi email OTP thật qua SMTP STARTTLS."""
        if not self._config.smtp_user or not self._config.smtp_app_password:
            raise RuntimeError(
                "Chưa cấu hình SMTP_USER / SMTP_APP_PASSWORD trong .env để gửi email OTP."
            )

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._config.smtp_user
        msg["To"] = to_email
        msg.set_content(body)

        with smtplib.SMTP(self._config.smtp_host, self._config.smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self._config.smtp_user, self._config.smtp_app_password)
            server.send_message(msg)

    def _build_otp_email_content(self, otp_code: str) -> tuple[str, str]:
        expire_minutes = max(1, self._config.otp_expire_seconds // 60)
        subject = "[Smart Parking Đào Tiên] Mã OTP khôi phục mật khẩu"
        body = (
            "Xin chào,\n\n"
            f"Mã OTP xác thực khôi phục mật khẩu của bạn là: {otp_code}\n"
            f"Mã có hiệu lực trong vòng {expire_minutes} phút ({self._config.otp_expire_seconds} giây).\n"
            "Tuyệt đối không chia sẻ mã này cho bất kỳ ai.\n\n"
            "Ban Quản Lý Chung Cư Đào Tiên"
        )
        return subject, body

    def _cleanup_expired_locked(self) -> None:
        now = time.monotonic()
        expired_ids = [
            req_id
            for req_id, rec in self._records.items()
            if (not rec.verified and now > rec.expires_at)
            or (rec.verified and rec.reset_token_expires_at > 0 and now > rec.reset_token_expires_at)
        ]
        for req_id in expired_ids:
            rec = self._records.pop(req_id, None)
            if rec and rec.reset_token:
                self._reset_tokens.pop(rec.reset_token, None)

    def request_otp(self, email: str, ma_tai_khoan: int) -> Dict[str, object]:
        """4.1.3: Tạo mã OTP 6 số mới trên RAM và gửi qua Email."""
        now = time.monotonic()
        otp_code = self._generate_otp()
        request_id = str(uuid.uuid4())
        ttl = self._config.otp_expire_seconds
        cooldown = self._config.otp_resend_cooldown_seconds

        record = OTPRecord(
            request_id=request_id,
            email=email.strip(),
            ma_tai_khoan=int(ma_tai_khoan),
            otp_code=otp_code,
            expires_at=now + ttl,
            resend_available_at=now + cooldown,
        )

        subject, body = self._build_otp_email_content(otp_code)
        self._email_sender(record.email, subject, body)

        with self._lock:
            self._cleanup_expired_locked()
            self._records[request_id] = record

        return {
            "ma_yeu_cau_otp": request_id,
            "email": record.email,
            "expires_in": ttl,
            "resend_cooldown": cooldown,
        }

    def resend_otp(self, ma_yeu_cau_otp: str) -> Dict[str, object]:
        """4.1.4: Kiểm tra cooldown 60s, vô hiệu mã OTP cũ và gửi lại mã mới."""
        now = time.monotonic()
        with self._lock:
            self._cleanup_expired_locked()
            record = self._records.get(ma_yeu_cau_otp)
            if record is None:
                raise ValueError("Yêu cầu OTP không tồn tại hoặc đã hết hạn.")
            if record.verified:
                raise ValueError("Yêu cầu OTP này đã được xác minh trước đó.")

            wait_seconds = int(record.resend_available_at - now)
            if wait_seconds > 0:
                raise ValueError(f"Vui lòng đợi {wait_seconds} giây trước khi gửi lại mã OTP.")

            new_otp = self._generate_otp()
            ttl = self._config.otp_expire_seconds
            cooldown = self._config.otp_resend_cooldown_seconds

        subject, body = self._build_otp_email_content(new_otp)
        self._email_sender(record.email, subject, body)

        with self._lock:
            record.otp_code = new_otp
            record.expires_at = time.monotonic() + ttl
            record.resend_available_at = time.monotonic() + cooldown
            record.failed_attempts = 0

        return {
            "ma_yeu_cau_otp": record.request_id,
            "email": record.email,
            "expires_in": ttl,
            "resend_cooldown": cooldown,
        }

    def verify_otp(self, ma_yeu_cau_otp: str, ma_otp: str) -> Dict[str, object]:
        """4.1.5: Xác minh OTP và cấp `reset_token` có thời hạn."""
        now = time.monotonic()
        code = (ma_otp or "").strip()
        with self._lock:
            self._cleanup_expired_locked()
            record = self._records.get(ma_yeu_cau_otp)
            if record is None:
                raise ValueError("Mã OTP đã hết hạn hoặc yêu cầu không tồn tại.")
            if now > record.expires_at:
                self._records.pop(ma_yeu_cau_otp, None)
                raise ValueError("Mã OTP đã hết hạn.")
            if record.verified and record.reset_token:
                return {
                    "reset_token": record.reset_token,
                    "ma_tai_khoan": record.ma_tai_khoan,
                    "expires_in": max(1, int(record.reset_token_expires_at - now)),
                }

            if record.otp_code != code:
                record.failed_attempts += 1
                remaining = self.MAX_VERIFY_ATTEMPTS - record.failed_attempts
                if remaining <= 0:
                    self._records.pop(ma_yeu_cau_otp, None)
                    raise ValueError("Bạn đã nhập sai OTP quá số lần cho phép. Vui lòng yêu cầu mã mới.")
                raise ValueError(f"Mã OTP không chính xác (còn {remaining} lần thử).")

            reset_token = secrets.token_urlsafe(32)
            record.verified = True
            record.reset_token = reset_token
            record.reset_token_expires_at = now + self.RESET_TOKEN_TTL_SECONDS
            self._reset_tokens[reset_token] = record.request_id

            return {
                "reset_token": reset_token,
                "ma_tai_khoan": record.ma_tai_khoan,
                "expires_in": self.RESET_TOKEN_TTL_SECONDS,
            }

    def consume_reset_token(self, reset_token: str) -> int:
        """4.1.6: Xác thực `reset_token` một lần và trả về `ma_tai_khoan`."""
        now = time.monotonic()
        with self._lock:
            self._cleanup_expired_locked()
            req_id = self._reset_tokens.pop(reset_token, None)
            if req_id is None:
                raise ValueError("Phiên đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.")
            record = self._records.pop(req_id, None)
            if record is None or now > record.reset_token_expires_at:
                raise ValueError("Phiên đặt lại mật khẩu đã hết hạn.")
            return record.ma_tai_khoan
