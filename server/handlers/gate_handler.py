"""Handler nghiệp vụ Kiểm soát Cổng Vào/Ra Ticketless, Tính phí Vãng lai, Ca trực & Tra cứu (Thành viên 5: Ny).

Bao gồm đầy đủ 11 chức năng lá:
- 2.2.1 – 2.2.3.2 (Workflow 03): Kiểm soát Cổng Vào Ticketless (Xe Cư dân & Xe Vãng lai + Anti-Passback)
- 2.3.1 – 2.3.3 (Workflow 04): Kiểm soát Cổng Ra, Đối chiếu ảnh & Thu phí Vãng lai tự động (Tiền mặt / QR)
- 2.4: Quản lý Ca làm việc, Tổng doanh thu theo ca & Chốt ca
- 2.5.1 – 2.5.3: Tra cứu phương tiện tại chốt bảo vệ kèm lịch sử ảnh cổng vào/ra
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from server.config import DEFAULT_CONFIG, ensure_storage_dirs
from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession, SessionManager
from server.core.state_store import StateStore
from shared.constants import (
    Roles,
    SlotStatus,
    VehicleStatus,
    VehicleTypes,
    ZoneStatus,
)
from shared.protocol import Actions
from shared.validators import normalize_plate, validate_plate


def _resolve_db(db_provider: Any = None) -> Any:
    if db_provider is not None:
        return db_provider
    from server import db as default_db

    return default_db


def normalize_vehicle_type_code(raw_type: Any, plate: str = "") -> str:
    """Chuẩn hóa mã loại xe về 'O_TO' hoặc 'XE_MAY' khớp bảng loai_phuong_tien."""
    text = str(raw_type or "").strip().upper()
    if text in ("O_TO", "OTO", "CAR"):
        return VehicleTypes.O_TO.value
    if text in ("XE_MAY", "XEMAY", "MOTO", "MOTORBIKE"):
        return VehicleTypes.XE_MAY.value
    if plate:
        ok_moto, _ = validate_plate(plate, VehicleTypes.XE_MAY.value)
        if ok_moto:
            return VehicleTypes.XE_MAY.value
    return VehicleTypes.O_TO.value


class GateHandler:
    """Xử lý kiểm soát Cổng Vào/Ra, Thu phí Vãng lai, Doanh thu Ca & Tra cứu Nhân viên."""

    def __init__(
        self,
        file_logger: FileLogger,
        state_store: StateStore,
        session_manager: Optional[SessionManager] = None,
        db_provider: Any = None,
        gates_in_dir: Optional[Path] = None,
        gates_out_dir: Optional[Path] = None,
        shift_state_file: Optional[Path] = None,
    ) -> None:
        self.file_logger = file_logger
        self.state_store = state_store
        self.session_manager = session_manager
        self._db_provider = db_provider

        cfg = ensure_storage_dirs(DEFAULT_CONFIG)
        self.gates_in_dir = Path(gates_in_dir) if gates_in_dir is not None else cfg.gates_in_dir
        self.gates_out_dir = Path(gates_out_dir) if gates_out_dir is not None else cfg.gates_out_dir
        self.gates_in_dir.mkdir(parents=True, exist_ok=True)
        self.gates_out_dir.mkdir(parents=True, exist_ok=True)
        self.shift_state_file = (
            Path(shift_state_file)
            if shift_state_file is not None
            else (cfg.state_dir / "shift_sessions.json")
        )

    def _db(self) -> Any:
        return _resolve_db(self._db_provider)

    @staticmethod
    def _ensure_staff_role(session: ClientSession) -> None:
        if session.vai_tro not in (Roles.NHAN_VIEN.value, Roles.QUAN_LY.value):
            raise PermissionError("Chỉ Nhân viên bãi xe hoặc Quản lý mới có quyền thao tác cổng kiểm soát.")

    def _resolve_staff_id(self, db_or_tx: Any, session: ClientSession) -> int:
        if session.ma_nhan_vien is not None:
            return int(session.ma_nhan_vien)
        if session.ma_tai_khoan is not None:
            row = db_or_tx.fetch_one(
                "SELECT ma_nhan_vien FROM nhan_vien WHERE ma_tai_khoan = %s",
                (int(session.ma_tai_khoan),),
            )
            if row and row.get("ma_nhan_vien") is not None:
                return int(row["ma_nhan_vien"])
        raise BusinessError("Không tìm thấy hồ sơ nhân viên đang đăng nhập.")

    def _check_anti_passback(self, db_or_tx: Any, normalized_plate: str) -> Optional[Dict[str, Any]]:
        """Kiểm tra xe có đang ở trong bãi hay chưa (cả bảng cư dân lẫn active_vang_lai.json)."""
        visitor = self.state_store.get_visitor_by_plate(normalized_plate)
        if visitor is not None:
            return {
                "loai_khach": "vang_lai",
                "thoi_gian_vao": visitor.get("thoi_gian_vao") or "",
                "vi_tri": visitor.get("ma_vi_tri") or visitor.get("ma_khu_vuc") or "",
            }

        resident_in = db_or_tx.fetch_one(
            """
            SELECT pt.bien_so_xe, ql.thoi_gian_vao,
                   COALESCE(ql.ma_vi_tri_co_dinh, ql.ma_khu_vuc_phan_cong) AS vi_tri
            FROM quan_ly_phuong_tien_ra_vao ql
            JOIN phuong_tien pt ON pt.ma_phuong_tien = ql.ma_phuong_tien
            WHERE pt.bien_so_xe = %s AND ql.dang_trong_bai = TRUE
            """,
            (normalized_plate,),
        )
        if resident_in:
            return {
                "loai_khach": "cu_dan",
                "thoi_gian_vao": str(resident_in.get("thoi_gian_vao") or ""),
                "vi_tri": str(resident_in.get("vi_tri") or ""),
            }
        return None

    # ==========================================================================
    # NHÓM 2.2.1 – 2.2.2: KIỂM TRA XE CỔNG VÀO & PHÂN NHÁNH CƯ DÂN / VÃNG LAI
    # ==========================================================================
    def handle_gate_in_check(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """2.2.1–2.2.2: Tra cứu biển số cổng vào, chặn Anti-Passback và gợi ý vị trí đỗ."""
        self._ensure_staff_role(session)

        raw_plate = str(payload.get("bien_so") or payload.get("bien_so_xe") or "").strip()
        if not raw_plate:
            raise BusinessError("Vui lòng nhập hoặc nhận diện biển số xe trước khi kiểm tra.")

        normalized_plate = normalize_plate(raw_plate)
        ok_plate, valid_or_err = validate_plate(normalized_plate)
        if not ok_plate:
            raise BusinessError(valid_or_err)
        normalized_plate = valid_or_err

        db = self._db()
        in_yard = self._check_anti_passback(db, normalized_plate)
        if in_yard is not None:
            thoi_gian = in_yard.get("thoi_gian_vao") or "trước đó"
            vi_tri = in_yard.get("vi_tri") or "trong bãi"
            raise BusinessError(
                f"Biển số {normalized_plate} đang được ghi nhận ở TRONG BÃI ({vi_tri}) từ lúc {thoi_gian}! "
                "Không thể cho vào trùng lặp (Anti-Passback)."
            )

        # Kiểm tra xe cư dân đang hoạt động
        resident_car = db.fetch_one(
            """
            SELECT pt.ma_phuong_tien, pt.ma_cu_dan, pt.ma_loai_xe, pt.bien_so_xe,
                   pt.ten_hang_dong_xe, pt.mau_xe, pt.trang_thai_xe,
                   cd.ho_ten, cd.so_dien_thoai, cd.ma_can_ho,
                   ch.toa_nha, ch.so_tang,
                   ql.ma_vi_tri_co_dinh, ql.ma_khu_vuc_phan_cong,
                   vt.ten_vi_tri AS ten_vi_tri_co_dinh,
                   vt.ma_khu_vuc AS ma_khu_vuc_vi_tri,
                   kv.ten_khu_vuc
            FROM phuong_tien pt
            JOIN cu_dan cd ON cd.ma_cu_dan = pt.ma_cu_dan
            JOIN can_ho ch ON ch.ma_can_ho = cd.ma_can_ho
            LEFT JOIN quan_ly_phuong_tien_ra_vao ql ON ql.ma_phuong_tien = pt.ma_phuong_tien
            LEFT JOIN vi_tri_do vt ON vt.ma_vi_tri = ql.ma_vi_tri_co_dinh
            LEFT JOIN khu_vuc kv ON kv.ma_khu_vuc = COALESCE(ql.ma_khu_vuc_phan_cong, vt.ma_khu_vuc)
            WHERE pt.bien_so_xe = %s AND pt.trang_thai_xe = %s
            """,
            (normalized_plate, VehicleStatus.DANG_HOAT_DONG.value),
        )

        if resident_car:
            ma_loai_xe = str(resident_car.get("ma_loai_xe") or VehicleTypes.O_TO.value)
            ma_khu_vuc = (
                resident_car.get("ma_khu_vuc_phan_cong")
                or resident_car.get("ma_khu_vuc_vi_tri")
                or ""
            )
            ma_vi_tri = resident_car.get("ma_vi_tri_co_dinh")
            return {
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "bien_so_xe": normalized_plate,
                "phan_nhanh": "cu_dan",
                "loai_khach": "cu_dan",
                "ma_loai_xe": ma_loai_xe,
                "ma_phuong_tien": int(resident_car["ma_phuong_tien"]),
                "ten_hang_dong_xe": resident_car.get("ten_hang_dong_xe") or "",
                "mau_xe": resident_car.get("mau_xe") or "",
                "chu_xe": {
                    "ma_cu_dan": int(resident_car["ma_cu_dan"]),
                    "ho_ten": resident_car.get("ho_ten") or "",
                    "so_dien_thoai": resident_car.get("so_dien_thoai") or "",
                    "ma_can_ho": resident_car.get("ma_can_ho") or "",
                    "toa_nha": resident_car.get("toa_nha") or "",
                },
                "ma_vi_tri_co_dinh": ma_vi_tri,
                "ten_vi_tri_co_dinh": resident_car.get("ten_vi_tri_co_dinh") or ma_vi_tri or "",
                "ma_khu_vuc": ma_khu_vuc,
                "ten_khu_vuc": resident_car.get("ten_khu_vuc") or ma_khu_vuc,
                "ghi_chu": "Xe cư dân đã đăng ký — miễn thu phí lượt tại cổng.",
            }

        # Nhánh 2.2.3.2: Khách vãng lai (Ticketless)
        detected_type = normalize_vehicle_type_code(
            payload.get("loai_xe") or payload.get("loai_xe_nhan_dien") or payload.get("ma_loai_xe"),
            normalized_plate,
        )

        available_car_slots = db.fetch_all(
            """
            SELECT vt.ma_vi_tri, vt.ten_vi_tri, vt.ma_khu_vuc, vt.nhom_vi_tri,
                   kv.ten_khu_vuc
            FROM vi_tri_do vt
            JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
            WHERE kv.ma_loai_xe = %s
              AND kv.trang_thai_khu_vuc = %s
              AND vt.trang_thai_vi_tri = %s
              AND (kv.tong_so_cho - kv.so_xe_dang_gui) > 0
            ORDER BY CASE WHEN vt.nhom_vi_tri = 'vi_tri_vang_lai' THEN 0 ELSE 1 END,
                     vt.ma_vi_tri ASC
            """,
            (
                VehicleTypes.O_TO.value,
                ZoneStatus.HOAT_DONG.value,
                SlotStatus.TRONG.value,
            ),
        )

        available_moto_zones = db.fetch_all(
            """
            SELECT ma_khu_vuc, ten_khu_vuc, tong_so_cho, so_xe_dang_gui,
                   GREATEST(0, tong_so_cho - so_xe_dang_gui) AS so_cho_trong
            FROM khu_vuc
            WHERE ma_loai_xe = %s
              AND trang_thai_khu_vuc = %s
              AND (tong_so_cho - so_xe_dang_gui) > 0
            ORDER BY ma_khu_vuc ASC
            """,
            (VehicleTypes.XE_MAY.value, ZoneStatus.HOAT_DONG.value),
        )

        suggested_slot: Optional[Dict[str, Any]] = None
        suggested_zone: Optional[Dict[str, Any]] = None
        if detected_type == VehicleTypes.O_TO.value and available_car_slots:
            suggested_slot = dict(available_car_slots[0])
            suggested_zone = {
                "ma_khu_vuc": suggested_slot["ma_khu_vuc"],
                "ten_khu_vuc": suggested_slot["ten_khu_vuc"],
            }
        elif detected_type == VehicleTypes.XE_MAY.value and available_moto_zones:
            suggested_zone = dict(available_moto_zones[0])

        return {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "bien_so_xe": normalized_plate,
            "phan_nhanh": "vang_lai",
            "loai_khach": "vang_lai",
            "ma_loai_xe": detected_type,
            "suggested_slot": suggested_slot,
            "suggested_zone": suggested_zone,
            "available_car_slots": available_car_slots,
            "available_moto_zones": available_moto_zones,
            "ghi_chu": "Khách vãng lai (Ticketless) — định danh trực tiếp bằng biển số xe.",
        }
