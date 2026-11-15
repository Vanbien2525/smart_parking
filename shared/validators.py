"""Các hàm chuẩn hóa và kiểm tra hợp lệ dùng chung giữa Client và Server."""

import base64
import io
import re
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple, Union

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_PHONE_RE = re.compile(r"^(0|\+84)(3|5|7|8|9)[0-9]{8}$")
_CCCD_RE = re.compile(r"^(\d{9}|\d{12})$")

# Định dạng biển số VN chuẩn sau normalize:
# - Ô tô: 2 số + 1-2 chữ cái + '-' + (4 số hoặc 3 số + '.' + 2 số) -> VD: 51H-888.99, 30A-1234, 51LD-123.45
# - Xe máy: 2 số + 1-2 chữ cái + 1 chữ số/chữ cái + '-' + (4 số hoặc 3 số + '.' + 2 số) -> VD: 59A1-123.45, 29B1-1234
_CAR_PLATE_RE = re.compile(r"^\d{2}[A-Z]{1,2}-(\d{4}|\d{3}\.\d{2})$")
_MOTO_PLATE_RE = re.compile(r"^\d{2}[A-Z]{1,2}[A-Z0-9]-(\d{4}|\d{3}\.\d{2})$")


def normalize_plate(plate: str) -> str:
    """Chuẩn hóa biển số xe Việt Nam về định dạng thống nhất (VD: 51H-888.99, 59A1-123.45)."""
    if not plate:
        return ""
    upper = plate.strip().upper()

    # Nếu người dùng đã có dấu gạch ngang hoặc khoảng trắng ngăn cách prefix và số cuối
    sep_match = re.match(r"^\s*([0-9]{2}[A-Z0-9]{1,3})[\s-]+([0-9.\s]+)\s*$", upper)
    if sep_match:
        prefix = re.sub(r"[^A-Z0-9]", "", sep_match.group(1))
        digits = re.sub(r"\D", "", sep_match.group(2))
        if len(digits) == 5:
            return f"{prefix}-{digits[:3]}.{digits[3:]}"
        if len(digits) == 4:
            return f"{prefix}-{digits}"

    cleaned = re.sub(r"[^A-Z0-9]", "", upper)
    if len(cleaned) < 7:
        return cleaned

    # Ưu tiên khớp theo thứ tự:
    # 1. Xe máy 5 số (2 số + 1-2 chữ + 1 chữ/số + 5 số, VD: 59A112345)
    # 2. Ô tô 5 số (2 số + 1-2 chữ + 5 số, VD: 51H88899, 51LD12345)
    # 3. Ô tô 4 số (2 số + 1-2 chữ + 4 số, VD: 30A1234)
    # 4. Xe máy 4 số (2 số + 1-2 chữ + 1 chữ/số + 4 số, VD: 59P11234)
    patterns = (
        r"^(\d{2}[A-Z]{1,2}[A-Z0-9])(\d{5})$",
        r"^(\d{2}[A-Z]{1,2})(\d{5})$",
        r"^(\d{2}[A-Z]{1,2})(\d{4})$",
        r"^(\d{2}[A-Z]{1,2}[A-Z0-9])(\d{4})$",
    )
    for pat in patterns:
        match = re.match(pat, cleaned)
        if match:
            prefix, digits = match.group(1), match.group(2)
            formatted_digits = f"{digits[:3]}.{digits[3:]}" if len(digits) == 5 else digits
            return f"{prefix}-{formatted_digits}"

    return cleaned


def validate_plate(plate: str, vehicle_type: Optional[str] = None) -> Tuple[bool, str]:
    """Kiểm tra biển số xe VN hợp lệ; trả về (True, biển_số_chuẩn) hoặc (False, thông_báo_lỗi)."""
    normalized = normalize_plate(plate)
    if not normalized:
        return False, "Biển số xe không được để trống."

    vtype = (vehicle_type or "").strip().upper()
    if vtype == "O_TO":
        if _CAR_PLATE_RE.match(normalized):
            return True, normalized
        return False, f"Biển số ô tô không hợp lệ: '{plate}' (VD đúng: 51H-888.99)."
    if vtype == "XE_MAY":
        if _MOTO_PLATE_RE.match(normalized):
            return True, normalized
        return False, f"Biển số xe máy không hợp lệ: '{plate}' (VD đúng: 59A1-123.45)."

    if _CAR_PLATE_RE.match(normalized) or _MOTO_PLATE_RE.match(normalized):
        return True, normalized
    return False, f"Định dạng biển số xe không hợp lệ: '{plate}'."


def validate_license_plate(plate: str, vehicle_type: Optional[str] = None) -> Tuple[bool, str]:
    """Alias tương thích cho các module gọi tên validate_license_plate."""
    return validate_plate(plate, vehicle_type)


def validate_email(email: str) -> bool:
    """Kiểm tra định dạng email hợp lệ."""
    if not email or len(email.strip()) > 100:
        return False
    return bool(_EMAIL_RE.match(email.strip()))


def validate_phone(phone: str) -> bool:
    """Kiểm tra số điện thoại Việt Nam (10 chữ số đầu 0 hoặc +84)."""
    if not phone:
        return False
    compact = re.sub(r"[\s.-]", "", phone.strip())
    return bool(_PHONE_RE.match(compact))


def validate_cccd(cccd: str) -> bool:
    """Kiểm tra CCCD (12 số) hoặc CMND cũ (9 số)."""
    if not cccd:
        return False
    return bool(_CCCD_RE.match(cccd.strip()))


def encode_image_base64(file_path: Union[str, Path], max_bytes: int = 400_000) -> str:
    """Đọc ảnh và mã hóa Base64; tự động nén JPEG nếu vượt quá max_bytes."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file ảnh: {path}")

    raw_bytes = path.read_bytes()
    if len(raw_bytes) <= max_bytes:
        return base64.b64encode(raw_bytes).decode("ascii")

    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw_bytes)) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            quality = 85
            while quality >= 40:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                compressed = buf.getvalue()
                if len(compressed) <= max_bytes:
                    return base64.b64encode(compressed).decode("ascii")
                quality -= 15

            # Giảm kích thước nếu vẫn còn lớn hơn max_bytes
            img.thumbnail((1280, 1280))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("ascii")
    except ImportError as exc:
        raise ValueError(
            f"Ảnh vượt quá {max_bytes} bytes và thiếu Pillow để nén tự động."
        ) from exc


def _strip_data_url_prefix(b64_str: str) -> Tuple[str, str]:
    """Tách tiền tố data:image/...;base64, nếu có; trả về (ext, clean_b64)."""
    text = b64_str.strip()
    if text.startswith("data:") and ";base64," in text:
        header, body = text.split(";base64,", 1)
        mime = header[5:].lower()
        ext = ".png" if "png" in mime else ".jpg"
        return ext, body
    return ".jpg", text


def save_base64_image(
    b64_str: str,
    dest_dir: Union[str, Path],
    prefix: str = "img",
    max_bytes: int = 5 * 1024 * 1024,
) -> str:
    """Giải mã ảnh Base64 và lưu vào dest_dir, trả về đường dẫn file đã lưu."""
    if not b64_str or not b64_str.strip():
        raise ValueError("Dữ liệu ảnh Base64 trống.")

    ext, clean_b64 = _strip_data_url_prefix(b64_str)
    try:
        raw = base64.b64decode(clean_b64, validate=True)
    except Exception as exc:
        raise ValueError("Chuỗi ảnh Base64 không hợp lệ.") from exc

    if len(raw) == 0 or len(raw) > max_bytes:
        raise ValueError(f"Dung lượng ảnh không hợp lệ ({len(raw)} bytes, tối đa {max_bytes} bytes).")

    # Nhận diện chữ ký PNG nếu không có data URI
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"

    target_dir = Path(dest_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", prefix).strip("_") or "img"
    filename = f"{safe_prefix}_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    target_path = target_dir / filename
    target_path.write_bytes(raw)
    return str(target_path)
