"""Handler Thông báo & Cảnh báo Khẩn cấp — Workflow 01 (1.10, 2.7, 3.6)."""

import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession, SessionManager
from shared.constants import (
    NotifyLevels,
    NotifyRecipientGroups,
    NotifyTypes,
    Roles,
)
from shared.protocol import Actions

_VALID_TYPES = {e.value for e in NotifyTypes}
_VALID_LEVELS = {e.value for e in NotifyLevels}
_VALID_GROUPS = {e.value for e in NotifyRecipientGroups}


def _resolve_db(db_provider: Any = None) -> Any:
    if db_provider is not None:
        return db_provider
    from server import db as default_db

    return default_db


class NotifyHandler:
    """Xử lý nghiệp vụ soạn nháp, phát thông báo Push, retry, đọc và ACK (Workflow 01)."""

    def __init__(
        self,
        session_manager: SessionManager,
        file_logger: FileLogger,
        db_provider: Any = None,
    ) -> None:
        self.session_manager = session_manager
        self.file_logger = file_logger
        self._db_provider = db_provider
        self._draft_lock = threading.RLock()
        self._drafts: Dict[str, Dict[str, Any]] = {}

    def _db(self) -> Any:
        return _resolve_db(self._db_provider)

    @staticmethod
    def _validate_notify_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
        tieu_de = str(payload.get("tieu_de") or "").strip()
        noi_dung = str(payload.get("noi_dung") or "").strip()
        loai_thong_bao = str(payload.get("loai_thong_bao") or NotifyTypes.BAO_TRI_KY_THUAT.value).strip()
        muc_do = str(payload.get("muc_do") or NotifyLevels.BINH_THUONG.value).strip()
        nhom_nguoi_nhan = str(payload.get("nhom_nguoi_nhan") or NotifyRecipientGroups.TAT_CA.value).strip()
        ma_tai_khoan_ca_nhan = payload.get("ma_tai_khoan_ca_nhan")

        if not tieu_de:
            raise BusinessError("Vui lòng nhập tiêu đề thông báo.")
        if len(tieu_de) > 200:
            raise BusinessError("Tiêu đề thông báo tối đa 200 ký tự.")
        if not noi_dung:
            raise BusinessError("Vui lòng nhập nội dung thông báo.")
        if len(noi_dung) > 1000:
            raise BusinessError("Nội dung thông báo tối đa 1000 ký tự.")
        if loai_thong_bao not in _VALID_TYPES:
            raise BusinessError(f"Loại thông báo không hợp lệ: '{loai_thong_bao}'.")
        if muc_do not in _VALID_LEVELS:
            raise BusinessError(f"Mức độ thông báo không hợp lệ: '{muc_do}'.")
        if nhom_nguoi_nhan not in _VALID_GROUPS:
            raise BusinessError(f"Nhóm người nhận không hợp lệ: '{nhom_nguoi_nhan}'.")

        parsed_target_id: Optional[int] = None
        if nhom_nguoi_nhan == NotifyRecipientGroups.CA_NHAN.value:
            if ma_tai_khoan_ca_nhan in (None, ""):
                raise BusinessError("Vui lòng chọn hoặc nhập mã tài khoản khi gửi cá nhân.")
            try:
                parsed_target_id = int(ma_tai_khoan_ca_nhan)
            except (TypeError, ValueError) as exc:
                raise BusinessError("Mã tài khoản cá nhân phải là số nguyên.") from exc

        return {
            "tieu_de": tieu_de,
            "noi_dung": noi_dung,
            "loai_thong_bao": loai_thong_bao,
            "muc_do": muc_do,
            "nhom_nguoi_nhan": nhom_nguoi_nhan,
            "ma_tai_khoan_ca_nhan": parsed_target_id,
        }

    # ==========================================================================
    # 1.10.1: SOẠN & LƯU NHÁP THÔNG BÁO (RAM SERVER)
    # ==========================================================================
    def handle_save_draft(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.10.1: Lưu bản nháp thông báo tạm trên RAM Server để Quản lý kiểm tra trước khi phát."""
        fields = self._validate_notify_fields(payload)
        draft_id = str(payload.get("draft_id") or "").strip() or f"DRAFT-{uuid.uuid4().hex[:8].upper()}"
        now_iso = datetime.now().isoformat(timespec="seconds")

        draft_record: Dict[str, Any] = {
            "draft_id": draft_id,
            "ma_tai_khoan_tao": session.ma_tai_khoan,
            "ma_nguoi_gui": session.ma_nhan_vien,
            "ten_nguoi_tao": session.ho_ten or session.ten_dang_nhap or "",
            "updated_at": now_iso,
            **fields,
        }
        with self._draft_lock:
            self._drafts[draft_id] = draft_record
            total_drafts = len(self._drafts)

        return {
            "draft_id": draft_id,
            "draft": dict(draft_record),
            "total_drafts": total_drafts,
        }

    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        with self._draft_lock:
            item = self._drafts.get(draft_id)
            return dict(item) if item is not None else None

    def list_drafts(self, ma_tai_khoan: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._draft_lock:
            items = list(self._drafts.values())
        if ma_tai_khoan is not None:
            items = [d for d in items if d.get("ma_tai_khoan_tao") == ma_tai_khoan]
        items.sort(key=lambda d: str(d.get("updated_at") or ""), reverse=True)
        return [dict(d) for d in items]


def register_notify_routes(
    router: Router,
    session_manager: SessionManager,
    file_logger: FileLogger,
    db_provider: Any = None,
) -> NotifyHandler:
    """Đăng ký các route Thông báo (1.10, 2.7, 3.6) lên Router."""
    handler = NotifyHandler(
        session_manager=session_manager,
        file_logger=file_logger,
        db_provider=db_provider,
    )
    router.register(
        Actions.NOTIFY_SAVE_DRAFT,
        handler.handle_save_draft,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    return handler
