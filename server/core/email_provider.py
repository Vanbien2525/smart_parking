"""Trừu tượng hóa dịch vụ gửi Email (Email Provider Abstraction).

Hỗ trợ 2 chế độ:
1. Local Development: SMTPEmailProvider (kết nối SMTP STARTTLS tới smtp.gmail.com).
2. Cloud Deployment (Render Free): HttpApiEmailProvider (gọi REST HTTPS API qua Resend hoặc Brevo,
   khắc phục việc Render Free chặn cổng outbound SMTP 25, 465, 587).
"""

import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from email.message import EmailMessage
from typing import Optional

from server.config import DEFAULT_CONFIG, ServerConfig

logger = logging.getLogger("server.email_provider")


class BaseEmailProvider(ABC):
    """Lớp cơ sở trừu tượng cho mọi nhà cung cấp Email."""

    @abstractmethod
    def send_email(self, to_email: str, subject: str, body: str) -> bool:
        """Gửi email tới địa chỉ `to_email`."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        pass


class SMTPEmailProvider(BaseEmailProvider):
    """Gửi email qua giao thức SMTP (dành cho môi trường Local Development)."""

    def __init__(self, config: Optional[ServerConfig] = None) -> None:
        self.config = config or DEFAULT_CONFIG

    @property
    def provider_name(self) -> str:
        return "SMTP"

    @property
    def is_configured(self) -> bool:
        return bool(self.config.smtp_user and self.config.smtp_app_password)

    def send_email(self, to_email: str, subject: str, body: str) -> bool:
        if not self.is_configured:
            raise RuntimeError("Chưa cấu hình SMTP_USER / SMTP_APP_PASSWORD trong .env.")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.config.smtp_user
        msg["To"] = to_email
        msg.set_content(body)

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.config.smtp_user, self.config.smtp_app_password)
            server.send_message(msg)
        return True


class ResendEmailProvider(BaseEmailProvider):
    """Gửi email qua Resend REST HTTPS API (tương thích 100% Render Cloud)."""

    def __init__(self, api_key: Optional[str] = None, from_email: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("RESEND_API_KEY", "").strip()
        self.from_email = from_email or os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev").strip()

    @property
    def provider_name(self) -> str:
        return "Resend (HTTPS API)"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def send_email(self, to_email: str, subject: str, body: str) -> bool:
        if not self.is_configured:
            raise RuntimeError(
                "Chưa cấu hình RESEND_API_KEY trong Environment Variables của Render. "
                "Đăng ký miễn phí tại https://resend.com để lấy API key (3000 emails/tháng)."
            )

        endpoint = "https://api.resend.com/emails"
        payload = json.dumps({
            "from": self.from_email,
            "to": [to_email],
            "subject": subject,
            "text": body,
        }).encode("utf-8")

        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "SmartParking-Render/2.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status_code = resp.getcode()
                return 200 <= status_code < 300
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            logger.error(f"Resend API error {err.code}: {err_body}")
            raise RuntimeError(f"Lỗi gửi email qua Resend: {err_body}") from err


class BrevoEmailProvider(BaseEmailProvider):
    """Gửi email qua Brevo (Sendinblue) REST HTTPS API (300 emails/ngày miễn phí)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        sender_email: Optional[str] = None,
        sender_name: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("BREVO_API_KEY", "").strip()
        self.sender_email = sender_email or os.getenv("BREVO_SENDER_EMAIL", "").strip()
        self.sender_name = sender_name or os.getenv("BREVO_SENDER_NAME", "Smart Parking Đào Tiên").strip()

    @property
    def provider_name(self) -> str:
        return "Brevo (HTTPS API)"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.sender_email)

    def send_email(self, to_email: str, subject: str, body: str) -> bool:
        if not self.is_configured:
            raise RuntimeError("Chưa cấu hình BREVO_API_KEY hoặc BREVO_SENDER_EMAIL.")

        endpoint = "https://api.brevo.com/v3/smtp/email"
        payload = json.dumps({
            "sender": {"name": self.sender_name, "email": self.sender_email},
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": body,
        }).encode("utf-8")

        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "api-key": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "SmartParking-Render/2.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return 200 <= resp.getcode() < 300
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            logger.error(f"Brevo API error {err.code}: {err_body}")
            raise RuntimeError(f"Lỗi gửi email qua Brevo: {err_body}") from err


def get_email_provider(config: Optional[ServerConfig] = None) -> BaseEmailProvider:
    """Factory hàm tạo provider dựa trên biến môi trường EMAIL_PROVIDER."""
    cfg = config or DEFAULT_CONFIG
    provider_type = os.getenv("EMAIL_PROVIDER", "smtp").strip().lower()

    if provider_type == "resend":
        return ResendEmailProvider()
    elif provider_type == "brevo":
        return BrevoEmailProvider()
    else:
        return SMTPEmailProvider(cfg)
