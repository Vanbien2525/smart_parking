"""Cấu hình trung tâm cho Server Smart Parking và khởi tạo cây thư mục storage/."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ServerConfig:
    """Thông số cấu hình Server nạp từ biến môi trường / .env."""

    # Mạng TCP Socket
    server_host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    server_port: int = _int_env("SERVER_PORT", 5555)
    client_connect_host: str = os.getenv("CLIENT_CONNECT_HOST", "127.0.0.1")
    heartbeat_timeout_seconds: int = _int_env("HEARTBEAT_TIMEOUT_SECONDS", 45)
    heartbeat_reap_interval_seconds: int = _int_env("HEARTBEAT_REAP_INTERVAL_SECONDS", 10)

    # PostgreSQL
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = _int_env("DB_PORT", 5432)
    db_name: str = os.getenv("DB_NAME", "smart_parking")
    db_user: str = os.getenv("DB_USER", "postgres")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_pool_min: int = _int_env("DB_POOL_MIN", 2)
    db_pool_max: int = _int_env("DB_POOL_MAX", 10)

    # SMTP OTP Email
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = _int_env("SMTP_PORT", 587)
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_app_password: str = os.getenv("SMTP_APP_PASSWORD", "")
    otp_expire_seconds: int = _int_env("OTP_EXPIRE_SECONDS", 300)
    otp_resend_cooldown_seconds: int = _int_env("OTP_RESEND_COOLDOWN_SECONDS", 60)

    # Đường dẫn thư mục lưu trữ
    base_dir: Path = BASE_DIR
    storage_dir: Path = BASE_DIR / "storage"
    logs_dir: Path = BASE_DIR / "storage" / "logs"
    state_dir: Path = BASE_DIR / "storage" / "state"
    uploads_dir: Path = BASE_DIR / "storage" / "uploads"
    cavet_dir: Path = BASE_DIR / "storage" / "uploads" / "cavet"
    gates_in_dir: Path = BASE_DIR / "storage" / "uploads" / "gates_in"
    gates_out_dir: Path = BASE_DIR / "storage" / "uploads" / "gates_out"


def ensure_storage_dirs(config: Optional[ServerConfig] = None) -> ServerConfig:
    """Đảm bảo toàn bộ các thư mục logs, state và uploads đều tồn tại."""
    cfg = config or ServerConfig()
    for folder in (
        cfg.storage_dir,
        cfg.logs_dir,
        cfg.state_dir,
        cfg.uploads_dir,
        cfg.cavet_dir,
        cfg.gates_in_dir,
        cfg.gates_out_dir,
    ):
        folder.mkdir(parents=True, exist_ok=True)
    return cfg


DEFAULT_CONFIG = ServerConfig()
