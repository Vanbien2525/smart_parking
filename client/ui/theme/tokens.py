"""Design DNA Tokens cho toàn bộ ứng dụng PySide6 — Chung cư Đào Tiên.

File này đồng bộ 100% với `design_dna.json`. Tất cả 5 thành viên phải import
màu sắc, font chữ, kích thước và mapping trạng thái từ đây thay vì hardcode
màu/font rời rạc trong từng màn hình.
"""

from dataclasses import dataclass
from typing import Dict


# ==============================================================================
# 1. BẢNG MÀU CHỦ ĐẠO (COLOR DNA)
# ==============================================================================
class Colors:
    # Primary & Sidebar Midnight Navy
    SIDEBAR_BG = "#0B132B"
    SIDEBAR_SURFACE = "#111C3A"
    SIDEBAR_ACTIVE_BG = "#1E293B"
    SIDEBAR_HOVER_BG = "#162244"
    SIDEBAR_TEXT = "#E2E8F0"
    SIDEBAR_TEXT_MUTED = "#94A3B8"
    PRIMARY = "#0F172A"

    # Brand Accents
    ROYAL_BLUE = "#2563EB"
    ROYAL_BLUE_HOVER = "#1D4ED8"
    ROYAL_BLUE_SOFT = "#EFF6FF"

    EMERALD_TEAL = "#0D9488"
    EMERALD_TEAL_HOVER = "#0F766E"
    EMERALD_TEAL_SOFT = "#F0FDFA"

    INDIGO = "#4F46E5"
    INDIGO_SOFT = "#EEF2FF"

    # Surface & Neutrals
    BG_APP = "#F8FAFC"
    BG_CARD = "#FFFFFF"
    BG_ELEVATED = "#F1F5F9"
    BG_HOVER = "#F1F5F9"

    BORDER = "#E2E8F0"
    BORDER_INPUT = "#CBD5E1"
    BORDER_FOCUS = "#2563EB"

    TEXT_PRIMARY = "#0F172A"
    TEXT_SECONDARY = "#475569"
    TEXT_MUTED = "#64748B"
    TEXT_ON_DARK = "#FFFFFF"

    # Semantic Colors
    SUCCESS = "#16A34A"
    SUCCESS_HOVER = "#15803D"
    SUCCESS_BG = "#DCFCE7"
    SUCCESS_BORDER = "#86EFAC"
    SUCCESS_TEXT = "#166534"

    WARNING = "#D97706"
    WARNING_HOVER = "#B45309"
    WARNING_BG = "#FEF3C7"
    WARNING_BORDER = "#FDE68A"
    WARNING_TEXT = "#92400E"

    ERROR = "#DC2626"
    ERROR_HOVER = "#B91C1C"
    ERROR_BG = "#FEE2E2"
    ERROR_BORDER = "#FCA5A5"
    ERROR_TEXT = "#991B1B"

    INFO = "#0284C7"
    INFO_HOVER = "#0369A1"
    INFO_BG = "#E0F2FE"
    INFO_BORDER = "#BAE6FD"
    INFO_TEXT = "#075985"


# ==============================================================================
# 2. NHẬN DIỆN 4 VAI TRÒ (ROLE VISUAL IDENTITY)
# ==============================================================================
@dataclass(frozen=True)
class RoleTheme:
    code: str
    label: str
    accent: str
    bg_soft: str
    border: str
    is_read_only: bool = False


ROLE_THEMES: Dict[str, RoleTheme] = {
    "quan_ly": RoleTheme(
        code="quan_ly",
        label="Quản lý chung cư",
        accent=Colors.ROYAL_BLUE,
        bg_soft=Colors.ROYAL_BLUE_SOFT,
        border="#BFDBFE",
        is_read_only=False,
    ),
    "nhan_vien": RoleTheme(
        code="nhan_vien",
        label="Nhân viên bãi xe",
        accent=Colors.EMERALD_TEAL,
        bg_soft=Colors.EMERALD_TEAL_SOFT,
        border="#99F6E4",
        is_read_only=False,
    ),
    "cu_dan": RoleTheme(
        code="cu_dan",
        label="Cư dân",
        accent=Colors.INDIGO,
        bg_soft=Colors.INDIGO_SOFT,
        border="#C7D2FE",
        is_read_only=False,
    ),
    "admin": RoleTheme(
        code="admin",
        label="Admin (Chỉ xem)",
        accent=Colors.WARNING,
        bg_soft="#FFFBEB",
        border=Colors.WARNING_BORDER,
        is_read_only=True,
    ),
}


# ==============================================================================
# 3. TRẠNG THÁI Ô ĐỖ XE (KHỚP 100% BẢNG vi_tri_do)
# ==============================================================================
SLOT_STATE_STYLES: Dict[str, Dict[str, str]] = {
    "trong": {
        "label": "Trống",
        "bg": Colors.SUCCESS_BG,
        "border": Colors.SUCCESS_BORDER,
        "text": Colors.SUCCESS_TEXT,
    },
    "da_phan_cho": {
        "label": "Đã phân chỗ",
        "bg": "#DBEAFE",
        "border": "#93C5FD",
        "text": "#1E40AF",
    },
    "dang_co_xe": {
        "label": "Đang có xe",
        "bg": Colors.ERROR_BG,
        "border": Colors.ERROR_BORDER,
        "text": Colors.ERROR_TEXT,
    },
    "ngung_su_dung": {
        "label": "Ngừng dùng",
        "bg": Colors.BG_ELEVATED,
        "border": Colors.BORDER_INPUT,
        "text": Colors.TEXT_SECONDARY,
    },
}


# ==============================================================================
# 4. KIỂU BADGE NGHIỆP VỤ DÙNG CHUNG
# ==============================================================================
BADGE_VARIANTS: Dict[str, Dict[str, str]] = {
    "success": {"bg": Colors.SUCCESS_BG, "border": Colors.SUCCESS_BORDER, "text": Colors.SUCCESS_TEXT},
    "warning": {"bg": Colors.WARNING_BG, "border": Colors.WARNING_BORDER, "text": Colors.WARNING_TEXT},
    "danger": {"bg": Colors.ERROR_BG, "border": Colors.ERROR_BORDER, "text": Colors.ERROR_TEXT},
    "info": {"bg": Colors.INFO_BG, "border": Colors.INFO_BORDER, "text": Colors.INFO_TEXT},
    "neutral": {"bg": Colors.BG_ELEVATED, "border": Colors.BORDER_INPUT, "text": Colors.TEXT_SECONDARY},
    "primary": {"bg": Colors.ROYAL_BLUE_SOFT, "border": "#BFDBFE", "text": "#1D4ED8"},
}


# ==============================================================================
# 5. TYPOGRAPHY & KÍCH THƯỚC LAYOUT
# ==============================================================================
class Fonts:
    UI_FAMILY = "Segoe UI Variable Text, Inter, Segoe UI, Arial, sans-serif"
    HEADING_FAMILY = "Segoe UI Variable Display, Inter, Segoe UI, Arial, sans-serif"
    MONO_FAMILY = "JetBrains Mono, Cascadia Mono, Consolas, monospace"

    SIZE_DISPLAY = 26
    SIZE_H1 = 21
    SIZE_H2 = 16
    SIZE_H3 = 14
    SIZE_BODY = 13
    SIZE_SMALL = 12
    SIZE_CAPTION = 11


class Layout:
    SIDEBAR_WIDTH = 256
    SIDEBAR_COLLAPSED_WIDTH = 72
    TOPBAR_HEIGHT = 56
    SPACE_XS = 4
    SPACE_SM = 8
    SPACE_MD = 12
    SPACE_LG = 16
    SPACE_XL = 20
    SPACE_2XL = 24
    RADIUS_SM = 6
    RADIUS_MD = 10
    RADIUS_LG = 14
    CONTROL_HEIGHT = 36
    INPUT_HEIGHT = 38
