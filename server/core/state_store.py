"""Lưu trữ trạng thái phiên xe vãng lai đang trong bãi trên RAM + active_vang_lai.json."""

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from shared.validators import normalize_plate


class StateStore:
    """Quản lý thread-safe danh sách lượt xe vãng lai đang gửi, đồng bộ atomic xuống JSON."""

    def __init__(self, state_file: Union[str, Path]) -> None:
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        with self._lock:
            if not self.state_file.exists():
                self._sessions = {}
                return
            raw_text = self.state_file.read_text(encoding="utf-8").strip()
            if not raw_text:
                self._sessions = {}
                return
            data = json.loads(raw_text)
            if isinstance(data, dict):
                self._sessions = {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
            elif isinstance(data, list):
                self._sessions = {
                    str(item["ma_luot_gui"]): dict(item)
                    for item in data
                    if isinstance(item, dict) and item.get("ma_luot_gui")
                }
            else:
                self._sessions = {}

    def _persist_locked(self) -> None:
        """Ghi nguyên tử (write temp + os.replace) để tránh hỏng file khi mất điện."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_file.with_suffix(".json.tmp")
        serialized = json.dumps(self._sessions, ensure_ascii=False, indent=2, default=str)
        tmp_path.write_text(serialized, encoding="utf-8")
        os.replace(tmp_path, self.state_file)

    def add_visitor_session(
        self,
        bien_so_xe: Optional[str] = None,
        ma_loai_xe: Optional[str] = None,
        ma_khu_vuc: Optional[str] = None,
        ma_vi_tri: Optional[str] = None,
        ma_nhan_vien_vao: Optional[int] = None,
        anh_vao: Optional[str] = None,
        ma_luot_gui: Optional[str] = None,
        thoi_gian_vao: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Thêm một lượt xe vãng lai đang gửi vào bãi và lưu ngay xuống đĩa."""
        plate_input = bien_so_xe or extra.pop("bien_so", "")
        normalized_plate = normalize_plate(str(plate_input))
        if not normalized_plate:
            raise ValueError("Biển số xe vãng lai không hợp lệ.")

        vehicle_type = ma_loai_xe or extra.pop("loai_xe", "O_TO")
        session_key = ma_luot_gui or f"VL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

        with self._lock:
            existing = self.get_open_visitor_by_plate(normalized_plate)
            if existing is not None:
                raise ValueError(f"Xe vãng lai biển số {normalized_plate} đang ở trong bãi.")

            record: Dict[str, Any] = {
                "ma_luot_gui": session_key,
                "loai_khach": "vang_lai",
                "bien_so_xe": normalized_plate,
                "ma_loai_xe": str(vehicle_type),
                "ma_khu_vuc": ma_khu_vuc,
                "ma_vi_tri": ma_vi_tri,
                "thoi_gian_vao": thoi_gian_vao or datetime.now().isoformat(timespec="seconds"),
                "ma_nhan_vien_vao": ma_nhan_vien_vao,
                "anh_vao": anh_vao,
            }
            record.update(extra)
            self._sessions[session_key] = record
            self._persist_locked()
            return dict(record)

    def get_open_visitor_by_plate(self, bien_so: str) -> Optional[Dict[str, Any]]:
        """Tìm lượt xe vãng lai đang mở theo biển số (đã chuẩn hóa)."""
        target = normalize_plate(bien_so)
        if not target:
            return None
        with self._lock:
            for item in self._sessions.values():
                if normalize_plate(str(item.get("bien_so_xe", ""))) == target:
                    return dict(item)
        return None

    def get_visitor_by_plate(self, bien_so: str) -> Optional[Dict[str, Any]]:
        """Alias tương thích cho module cổng vào/ra."""
        return self.get_open_visitor_by_plate(bien_so)

    def pop_visitor_session(self, key: str) -> Optional[Dict[str, Any]]:
        """Xóa và trả về phiên xe vãng lai theo `ma_luot_gui` hoặc `bien_so_xe`."""
        if not key:
            return None
        with self._lock:
            if key in self._sessions:
                popped = self._sessions.pop(key)
                self._persist_locked()
                return dict(popped)

            target_plate = normalize_plate(key)
            matched_id = None
            for sid, item in self._sessions.items():
                if normalize_plate(str(item.get("bien_so_xe", ""))) == target_plate:
                    matched_id = sid
                    break
            if matched_id is not None:
                popped = self._sessions.pop(matched_id)
                self._persist_locked()
                return dict(popped)
        return None

    def list_open_visitor_sessions(self, ma_khu_vuc: Optional[str] = None) -> List[Dict[str, Any]]:
        """Liệt kê toàn bộ lượt xe vãng lai đang ở trong bãi (lọc tùy chọn theo khu vực)."""
        with self._lock:
            items = list(self._sessions.values())
        if ma_khu_vuc:
            items = [x for x in items if x.get("ma_khu_vuc") == ma_khu_vuc]
        items.sort(key=lambda x: str(x.get("thoi_gian_vao", "")), reverse=True)
        return [dict(x) for x in items]

    def list_visitor_sessions(self, ma_khu_vuc: Optional[str] = None) -> List[Dict[str, Any]]:
        """Alias tương thích cho module tra cứu."""
        return self.list_open_visitor_sessions(ma_khu_vuc=ma_khu_vuc)

    def count_open_visitors(self) -> int:
        with self._lock:
            return len(self._sessions)
