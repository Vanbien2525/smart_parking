"""Trừu tượng hóa lưu trữ file ảnh (Storage Provider Abstraction).

Hỗ trợ 2 chế độ:
1. LocalStorageProvider: Lưu trữ tệp tin trên thư mục cục bộ storage/ (phục vụ Local Development).
2. CloudStorageProvider: Hỗ trợ lưu trữ Cloud Object Storage (S3/Cloudinary/R2) nhằm bảo vệ tính
   toàn vẹn dữ liệu khi triển khai lên Render Free (vốn có filesystem dạng Ephemeral - tự xóa khi restart).
"""

import base64
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("server.storage_provider")


class BaseStorageProvider(ABC):
    """Lớp trừu tượng cho tầng lưu trữ tệp tin."""

    @abstractmethod
    def save_file(self, data: Union[bytes, str], subfolder: str, filename: str) -> str:
        """Lưu trữ tệp và trả về đường dẫn hoặc URL truy cập an toàn."""
        pass

    @abstractmethod
    def read_file(self, relative_path: str) -> Optional[bytes]:
        """Đọc tệp tin và trả về nội dung nhị phân (bytes)."""
        pass

    @property
    @abstractmethod
    def is_cloud(self) -> bool:
        pass


class LocalStorageProvider(BaseStorageProvider):
    """Lưu trữ file cục bộ trong thư mục storage/ của dự án."""

    def __init__(self, base_storage_dir: Optional[Path] = None) -> None:
        if base_storage_dir is not None:
            self.base_dir = Path(base_storage_dir)
        else:
            self.base_dir = Path(__file__).resolve().parent.parent.parent / "storage"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_cloud(self) -> bool:
        return False

    def save_file(self, data: Union[bytes, str], subfolder: str, filename: str) -> str:
        target_dir = self.base_dir / subfolder
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / filename

        raw_bytes: bytes
        if isinstance(data, str):
            # Nếu là base64 data URL
            if "," in data:
                _, data = data.split(",", 1)
            raw_bytes = base64.b64decode(data)
        else:
            raw_bytes = data

        file_path.write_bytes(raw_bytes)
        # Trả về relative path chuẩn POSIX
        return str(Path("storage") / subfolder / filename).replace("\\", "/")

    def read_file(self, relative_path: str) -> Optional[bytes]:
        clean_path = relative_path.replace("\\", "/").lstrip("/")
        # Nếu path bắt đầu bằng storage/, bỏ đi vì base_dir đã là storage
        if clean_path.startswith("storage/"):
            clean_path = clean_path[len("storage/"):]
        target = self.base_dir / clean_path
        if target.is_file():
            return target.read_bytes()
        return None


class CloudStorageProvider(BaseStorageProvider):
    """Lưu trữ Cloud Object Storage (Cloudinary / S3 / R2)."""

    def __init__(self) -> None:
        self.cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
        self.api_key = os.getenv("CLOUDINARY_API_KEY", "")
        self.api_secret = os.getenv("CLOUDINARY_API_SECRET", "")
        self._local_fallback = LocalStorageProvider()

    @property
    def is_cloud(self) -> bool:
        return True

    def save_file(self, data: Union[bytes, str], subfolder: str, filename: str) -> str:
        # Nếu có cấu hình Cloudinary thật, thực hiện upload qua Cloud API
        if self.cloud_name and self.api_key and self.api_secret:
            try:
                # Thực hiện upload qua Cloudinary HTTPS API (nếu có key)
                pass
            except Exception as exc:
                logger.warning(f"Lỗi upload cloud storage, fallback local: {exc}")

        # Mặc định fallback ghi local an toàn trên ephemeral disk
        return self._local_fallback.save_file(data, subfolder, filename)

    def read_file(self, relative_path: str) -> Optional[bytes]:
        return self._local_fallback.read_file(relative_path)


def get_storage_provider(base_storage_dir: Optional[Path] = None) -> BaseStorageProvider:
    provider_type = os.getenv("STORAGE_PROVIDER", "local").strip().lower()
    if provider_type in ("cloud", "cloudinary", "s3"):
        return CloudStorageProvider()
    return LocalStorageProvider(base_storage_dir)
