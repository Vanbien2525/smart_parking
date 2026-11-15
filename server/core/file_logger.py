"""Hệ thống ghi và truy vấn 4 loại File Log JSONL theo ngày (append-only, thread-safe)."""

import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from shared.constants import AuthResults, LogLevels

VALID_LOG_TYPES = ("auth", "audit", "error", "parking_history")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_date(value: Optional[Union[str, date, datetime]]) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


class FileLogger:
    """Quản lý ghi và truy vấn 4 loại log `.jsonl` theo ngày trong `storage/logs/`."""

    def __init__(self, logs_dir: Union[str, Path]) -> None:
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _get_log_file(self, log_type: str, log_date: Optional[date] = None) -> Path:
        if log_type not in VALID_LOG_TYPES:
            raise ValueError(f"Loại log không hợp lệ: {log_type}")
        day_str = (log_date or datetime.now().date()).isoformat()
        return self.logs_dir / f"{log_type}_{day_str}.jsonl"

    def _append_record(self, log_type: str, record: Dict[str, Any]) -> Dict[str, Any]:
        entry = {"timestamp": record.get("timestamp") or _now_iso(), **record}
        entry.setdefault("timestamp", _now_iso())
        line = json.dumps(entry, ensure_ascii=False, default=str)
        target_date = _to_date(entry["timestamp"]) or datetime.now().date()
        file_path = self._get_log_file(log_type, target_date)
        with self._lock:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with file_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return entry

    def log_auth(
        self,
        ten_dang_nhap: str,
        ket_qua: str = AuthResults.THANH_CONG,
        ma_tai_khoan: Optional[int] = None,
        vai_tro: Optional[str] = None,
        ip_address: str = "",
        ly_do: str = "",
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ghi log đăng nhập (auth_YYYY-MM-DD.jsonl) phục vụ 4.4.2 và 4.2.2."""
        return self._append_record(
            "auth",
            {
                "timestamp": timestamp or _now_iso(),
                "ma_tai_khoan": ma_tai_khoan,
                "ten_dang_nhap": ten_dang_nhap,
                "vai_tro": vai_tro,
                "ip_address": ip_address,
                "ket_qua": str(ket_qua),
                "ly_do": ly_do,
            },
        )

    def log_audit(
        self,
        ma_tai_khoan: Optional[int],
        ten_dang_nhap: str,
        vai_tro: str,
        loai_thao_tac: str,
        doi_tuong: str,
        ma_doi_tuong: Any = None,
        gia_tri_cu: Any = None,
        gia_tri_moi: Any = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ghi log thao tác nghiệp vụ (audit_YYYY-MM-DD.jsonl) phục vụ 4.4.1 và 4.2.5."""
        return self._append_record(
            "audit",
            {
                "timestamp": timestamp or _now_iso(),
                "ma_tai_khoan": ma_tai_khoan,
                "ten_dang_nhap": ten_dang_nhap,
                "vai_tro": vai_tro,
                "loai_thao_tac": loai_thao_tac,
                "doi_tuong": doi_tuong,
                "ma_doi_tuong": ma_doi_tuong,
                "gia_tri_cu": gia_tri_cu,
                "gia_tri_moi": gia_tri_moi,
            },
        )

    def log_error(
        self,
        noi_dung: str,
        muc_do: str = LogLevels.ERROR,
        nguon: str = "server",
        ma_loi: str = "ERR_INTERNAL",
        chi_tiet: Any = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ghi log lỗi & cảnh báo hệ thống (error_YYYY-MM-DD.jsonl) phục vụ 4.4.3 và 1.1.4."""
        return self._append_record(
            "error",
            {
                "timestamp": timestamp or _now_iso(),
                "muc_do": str(muc_do),
                "nguon": nguon,
                "ma_loi": ma_loi,
                "noi_dung": noi_dung,
                "chi_tiet": chi_tiet,
            },
        )

    def log_parking_history(
        self,
        ma_luot_gui: str,
        loai_khach: str,
        bien_so_xe: str,
        ma_loai_xe: str,
        ma_khu_vuc: Optional[str] = None,
        ma_vi_tri: Optional[str] = None,
        thoi_gian_vao: Optional[str] = None,
        thoi_gian_ra: Optional[str] = None,
        anh_vao: Optional[str] = None,
        anh_ra: Optional[str] = None,
        tien_thu: float = 0,
        ma_nhan_vien_vao: Optional[int] = None,
        ma_nhan_vien_ra: Optional[int] = None,
        trang_thai: str = "dang_gui",
        extra: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ghi lịch sử xe vào/ra bãi (parking_history_YYYY-MM-DD.jsonl)."""
        payload: Dict[str, Any] = {
            "timestamp": timestamp or _now_iso(),
            "ma_luot_gui": ma_luot_gui,
            "loai_khach": loai_khach,
            "bien_so_xe": bien_so_xe,
            "ma_loai_xe": ma_loai_xe,
            "ma_khu_vuc": ma_khu_vuc,
            "ma_vi_tri": ma_vi_tri,
            "thoi_gian_vao": thoi_gian_vao,
            "thoi_gian_ra": thoi_gian_ra,
            "anh_vao": anh_vao,
            "anh_ra": anh_ra,
            "tien_thu": tien_thu,
            "ma_nhan_vien_vao": ma_nhan_vien_vao,
            "ma_nhan_vien_ra": ma_nhan_vien_ra,
            "trang_thai": trang_thai,
        }
        if extra:
            payload.update(extra)
        return self._append_record("parking_history", payload)

    def _matches_filters(self, record: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        for key, expected in filters.items():
            if expected is None or expected == "":
                continue
            if key == "keyword":
                kw = str(expected).strip().lower()
                haystack = json.dumps(record, ensure_ascii=False, default=str).lower()
                if kw not in haystack:
                    return False
                continue

            actual = record.get(key)
            if actual is None:
                return False

            if key in ("ten_dang_nhap", "bien_so_xe", "loai_thao_tac", "nguon", "ma_loi"):
                if str(expected).strip().lower() not in str(actual).lower():
                    return False
            elif str(actual) != str(expected):
                return False
        return True

    def query_logs(
        self,
        log_type: str,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Truy vấn log `.jsonl` theo loại, khoảng ngày và bộ lọc; trả về mới nhất trước."""
        if log_type not in VALID_LOG_TYPES:
            raise ValueError(f"Loại log không hợp lệ: {log_type}")

        start_d = _to_date(start_date)
        end_d = _to_date(end_date)
        active_filters = filters or {}

        with self._lock:
            files = sorted(self.logs_dir.glob(f"{log_type}_*.jsonl"), reverse=True)

        results: List[Dict[str, Any]] = []
        for file_path in files:
            date_part = file_path.stem.replace(f"{log_type}_", "", 1)
            file_date = _to_date(date_part)
            if file_date is not None:
                if start_d is not None and file_date < start_d:
                    continue
                if end_d is not None and file_date > end_d:
                    continue

            with self._lock:
                if not file_path.exists():
                    continue
                lines = file_path.read_text(encoding="utf-8").splitlines()

            for raw_line in reversed(lines):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if active_filters and not self._matches_filters(record, active_filters):
                    continue
                results.append(record)
                if limit > 0 and len(results) >= limit:
                    return results

        return results

    def get_last_auth_for_account(self, ma_tai_khoan: int) -> Optional[Dict[str, Any]]:
        """Lấy bản ghi đăng nhập gần nhất của một tài khoản phục vụ màn hình 4.2.2."""
        rows = self.query_logs(
            "auth",
            filters={"ma_tai_khoan": ma_tai_khoan},
            limit=1,
        )
        return rows[0] if rows else None
