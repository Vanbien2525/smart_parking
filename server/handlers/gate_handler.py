"""Handler nghiệp vụ Kiểm soát Cổng Vào/Ra Ticketless, Tính phí Vãng lai, Ca trực & Tra cứu (Thành viên 5: Ny).

Bao gồm đầy đủ 11 chức năng lá:
- 2.2.1 – 2.2.3.2 (Workflow 03): Kiểm soát Cổng Vào Ticketless (Xe Cư dân & Xe Vãng lai + Anti-Passback)
- 2.3.1 – 2.3.3 (Workflow 04): Kiểm soát Cổng Ra, Đối chiếu ảnh & Thu phí Vãng lai tự động (Tiền mặt / QR)
- 2.4: Quản lý Ca làm việc, Tổng doanh thu theo ca & Chốt ca
- 2.5.1 – 2.5.3: Tra cứu phương tiện tại chốt bảo vệ kèm lịch sử ảnh cổng vào/ra
"""

import base64
from datetime import date, datetime
import io
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional
import uuid

import qrcode

from server.config import DEFAULT_CONFIG, ensure_storage_dirs
from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession, SessionManager
from server.core.state_store import StateStore
from shared.constants import (
    ParkingHistoryStatus,
    PaymentMethods,
    PricingGroups,
    PricingStatus,
    Roles,
    ShiftTypes,
    SlotStatus,
    VehicleStatus,
    VehicleTypes,
    ZoneStatus,
)
from shared.protocol import Actions, PushEvents
from shared.validators import normalize_plate, save_base64_image, validate_plate


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
        self._shift_lock = threading.RLock()

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

    def _save_gate_image(
        self,
        b64_str: Optional[str],
        dest_dir: Path,
        prefix: str,
    ) -> Optional[str]:
        """Lưu ảnh chụp cổng vào/ra dạng Base64 xuống đĩa và trả về đường dẫn lưu log."""
        if not b64_str or not str(b64_str).strip():
            return None
        try:
            saved_path = Path(save_base64_image(str(b64_str), dest_dir, prefix=prefix))
        except ValueError as exc:
            raise BusinessError(f"Ảnh chụp cổng không hợp lệ: {exc}") from exc

        try:
            rel = saved_path.resolve().relative_to(DEFAULT_CONFIG.base_dir.resolve())
            return str(rel).replace("\\", "/")
        except ValueError:
            return str(saved_path)

    def _broadcast_parking_state(self, reason: str, data: Optional[Dict[str, Any]] = None) -> None:
        if self.session_manager is None:
            return
        payload = {"reason": reason, "timestamp": datetime.now().isoformat(timespec="seconds")}
        if data:
            payload.update(data)
        try:
            self.session_manager.push_to_roles(
                [Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.CU_DAN.value, Roles.ADMIN.value],
                PushEvents.PARKING_STATE_CHANGED,
                payload,
            )
        except Exception:
            pass

    # ==========================================================================
    # NHÓM 2.2.3.1 & 2.2.3.2 (WORKFLOW 03): XÁC NHẬN CHO XE VÀO BÃI TICKETLESS
    # ==========================================================================
    def handle_gate_in_confirm(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """2.2.3.1 (Cư dân) & 2.2.3.2 (Vãng lai Ticketless): Ghi nhận xe vào bãi ACID."""
        self._ensure_staff_role(session)

        raw_plate = str(payload.get("bien_so") or payload.get("bien_so_xe") or "").strip()
        if not raw_plate:
            raise BusinessError("Thiếu biển số xe để xác nhận mở cổng vào.")

        ok_plate, valid_or_err = validate_plate(raw_plate)
        if not ok_plate:
            raise BusinessError(valid_or_err)
        normalized_plate = valid_or_err

        gate_image_b64 = payload.get("gate_image_base64") or payload.get("anh_vao_base64")
        now_dt = datetime.now()
        now_iso = now_dt.isoformat(timespec="seconds")
        ma_luot_gui = f"LG-{now_dt.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

        db = self._db()
        saved_in_path: Optional[str] = None
        result_record: Dict[str, Any] = {}

        with db.transaction() as tx:
            staff_id = self._resolve_staff_id(tx, session)
            in_yard = self._check_anti_passback(tx, normalized_plate)
            if in_yard is not None:
                raise BusinessError(
                    f"Biển số {normalized_plate} đang ở trong bãi từ lúc {in_yard.get('thoi_gian_vao')}! "
                    "Không thể mở cổng vào trùng lặp."
                )

            # Kiểm tra thực tế trong bảng phuong_tien để đảm bảo phân nhánh chuẩn
            resident_car = tx.fetch_one(
                """
                SELECT pt.ma_phuong_tien, pt.ma_cu_dan, pt.ma_loai_xe, pt.bien_so_xe,
                       cd.ho_ten, cd.ma_can_ho,
                       ql.ma_quan_ly_xe, ql.ma_vi_tri_co_dinh, ql.ma_khu_vuc_phan_cong,
                       vt.ten_vi_tri AS ten_vi_tri_co_dinh,
                       vt.ma_khu_vuc AS ma_khu_vuc_vi_tri,
                       kv.ten_khu_vuc
                FROM phuong_tien pt
                JOIN cu_dan cd ON cd.ma_cu_dan = pt.ma_cu_dan
                LEFT JOIN quan_ly_phuong_tien_ra_vao ql ON ql.ma_phuong_tien = pt.ma_phuong_tien
                LEFT JOIN vi_tri_do vt ON vt.ma_vi_tri = ql.ma_vi_tri_co_dinh
                LEFT JOIN khu_vuc kv ON kv.ma_khu_vuc = COALESCE(ql.ma_khu_vuc_phan_cong, vt.ma_khu_vuc)
                WHERE pt.bien_so_xe = %s AND pt.trang_thai_xe = %s
                """,
                (normalized_plate, VehicleStatus.DANG_HOAT_DONG.value),
            )

            if resident_car:
                # Nhánh 2.2.3.1: Xe đã đăng ký của cư dân
                ma_pt = int(resident_car["ma_phuong_tien"])
                ma_loai_xe = str(resident_car.get("ma_loai_xe") or VehicleTypes.O_TO.value)
                ma_vi_tri = resident_car.get("ma_vi_tri_co_dinh")
                ma_khu_vuc = (
                    resident_car.get("ma_khu_vuc_phan_cong")
                    or resident_car.get("ma_khu_vuc_vi_tri")
                    or ""
                )
                ten_khu_vuc = resident_car.get("ten_khu_vuc") or ma_khu_vuc

                if resident_car.get("ma_quan_ly_xe") is None:
                    raise BusinessError(
                        f"Xe cư dân {normalized_plate} chưa được phân bổ vị trí/khu vực hợp lệ trong hệ thống."
                    )

                if ma_khu_vuc:
                    zone_row = tx.fetch_one(
                        """
                        SELECT ma_khu_vuc, tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc
                        FROM khu_vuc
                        WHERE ma_khu_vuc = %s
                        """,
                        (ma_khu_vuc,),
                    )
                    if not zone_row or zone_row.get("trang_thai_khu_vuc") != ZoneStatus.HOAT_DONG.value:
                        raise BusinessError(f"Khu vực '{ma_khu_vuc}' hiện không hoạt động.")

                saved_in_path = self._save_gate_image(
                    gate_image_b64, self.gates_in_dir, f"in_{normalized_plate}"
                )

                tx.execute(
                    """
                    UPDATE quan_ly_phuong_tien_ra_vao
                    SET dang_trong_bai = TRUE,
                        thoi_gian_vao = %s
                    WHERE ma_phuong_tien = %s
                    """,
                    (now_dt, ma_pt),
                )

                if ma_loai_xe == VehicleTypes.O_TO.value and ma_vi_tri:
                    tx.execute(
                        """
                        UPDATE vi_tri_do
                        SET trang_thai_vi_tri = %s,
                            thoi_gian_cap_nhat = %s
                        WHERE ma_vi_tri = %s
                        """,
                        (SlotStatus.DANG_CO_XE.value, now_dt, ma_vi_tri),
                    )

                if ma_khu_vuc:
                    tx.execute(
                        """
                        UPDATE khu_vuc
                        SET so_xe_dang_gui = LEAST(tong_so_cho, so_xe_dang_gui + 1)
                        WHERE ma_khu_vuc = %s
                        """,
                        (ma_khu_vuc,),
                    )

                result_record = {
                    "ma_luot_gui": ma_luot_gui,
                    "loai_khach": "cu_dan",
                    "bien_so_xe": normalized_plate,
                    "ma_loai_xe": ma_loai_xe,
                    "ma_phuong_tien": ma_pt,
                    "ma_can_ho": resident_car.get("ma_can_ho") or "",
                    "ho_ten_chu_xe": resident_car.get("ho_ten") or "",
                    "ma_vi_tri": ma_vi_tri,
                    "ten_vi_tri": resident_car.get("ten_vi_tri_co_dinh") or ma_vi_tri or "",
                    "ma_khu_vuc": ma_khu_vuc,
                    "ten_khu_vuc": ten_khu_vuc,
                    "thoi_gian_vao": now_iso,
                    "anh_vao": saved_in_path,
                    "ma_nhan_vien_vao": staff_id,
                    "barrier_status": "OPENED",
                }
            else:
                # Nhánh 2.2.3.2: Xe vãng lai (Ticketless)
                ma_loai_xe = normalize_vehicle_type_code(
                    payload.get("ma_loai_xe") or payload.get("loai_xe"),
                    normalized_plate,
                )
                req_slot = str(payload.get("ma_vi_tri") or "").strip() or None
                req_zone = str(payload.get("ma_khu_vuc") or "").strip() or None
                ten_vi_tri = ""
                ten_khu_vuc = ""

                if ma_loai_xe == VehicleTypes.O_TO.value:
                    if req_slot:
                        slot_row = tx.fetch_one(
                            """
                            SELECT vt.ma_vi_tri, vt.ten_vi_tri, vt.ma_khu_vuc, vt.trang_thai_vi_tri,
                                   kv.ten_khu_vuc, kv.ma_loai_xe, kv.trang_thai_khu_vuc,
                                   kv.tong_so_cho, kv.so_xe_dang_gui
                            FROM vi_tri_do vt
                            JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
                            WHERE vt.ma_vi_tri = %s
                            """,
                            (req_slot,),
                        )
                    else:
                        slot_row = tx.fetch_one(
                            """
                            SELECT vt.ma_vi_tri, vt.ten_vi_tri, vt.ma_khu_vuc, vt.trang_thai_vi_tri,
                                   kv.ten_khu_vuc, kv.ma_loai_xe, kv.trang_thai_khu_vuc,
                                   kv.tong_so_cho, kv.so_xe_dang_gui
                            FROM vi_tri_do vt
                            JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
                            WHERE kv.ma_loai_xe = %s
                              AND kv.trang_thai_khu_vuc = %s
                              AND vt.trang_thai_vi_tri = %s
                              AND (kv.tong_so_cho - kv.so_xe_dang_gui) > 0
                            ORDER BY CASE WHEN vt.nhom_vi_tri = 'vi_tri_vang_lai' THEN 0 ELSE 1 END,
                                     vt.ma_vi_tri ASC
                            LIMIT 1
                            """,
                            (
                                VehicleTypes.O_TO.value,
                                ZoneStatus.HOAT_DONG.value,
                                SlotStatus.TRONG.value,
                            ),
                        )

                    if not slot_row:
                        raise BusinessError("Hiện không còn ô đỗ ô tô trống cho khách vãng lai!")
                    if slot_row.get("ma_loai_xe") != VehicleTypes.O_TO.value:
                        raise BusinessError(f"Ô đỗ '{slot_row['ma_vi_tri']}' không dành cho Ô tô.")
                    if slot_row.get("trang_thai_khu_vuc") != ZoneStatus.HOAT_DONG.value:
                        raise BusinessError(f"Khu vực '{slot_row['ma_khu_vuc']}' hiện đang ngừng sử dụng.")
                    if slot_row.get("trang_thai_vi_tri") != SlotStatus.TRONG.value:
                        raise BusinessError(
                            f"Ô đỗ '{slot_row['ma_vi_tri']}' không còn trống (trạng thái: {slot_row.get('trang_thai_vi_tri')})."
                        )
                    if int(slot_row.get("so_xe_dang_gui") or 0) >= int(slot_row.get("tong_so_cho") or 0):
                        raise BusinessError(f"Khu vực '{slot_row['ma_khu_vuc']}' đã đầy chỗ.")

                    ma_vi_tri = str(slot_row["ma_vi_tri"])
                    ten_vi_tri = str(slot_row.get("ten_vi_tri") or ma_vi_tri)
                    ma_khu_vuc = str(slot_row["ma_khu_vuc"])
                    ten_khu_vuc = str(slot_row.get("ten_khu_vuc") or ma_khu_vuc)

                    updated_slots = tx.execute(
                        """
                        UPDATE vi_tri_do
                        SET trang_thai_vi_tri = %s,
                            thoi_gian_cap_nhat = %s
                        WHERE ma_vi_tri = %s AND trang_thai_vi_tri = %s
                        """,
                        (SlotStatus.DANG_CO_XE.value, now_dt, ma_vi_tri, SlotStatus.TRONG.value),
                    )
                    if updated_slots <= 0:
                        raise BusinessError(f"Ô đỗ '{ma_vi_tri}' vừa được xe khác chiếm chỗ.")
                else:
                    # Xe máy vãng lai: phân theo khu vực xe máy còn chỗ
                    if req_zone:
                        zone_row = tx.fetch_one(
                            """
                            SELECT ma_khu_vuc, ten_khu_vuc, ma_loai_xe,
                                   tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc
                            FROM khu_vuc
                            WHERE ma_khu_vuc = %s
                            """,
                            (req_zone,),
                        )
                    else:
                        zone_row = tx.fetch_one(
                            """
                            SELECT ma_khu_vuc, ten_khu_vuc, ma_loai_xe,
                                   tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc
                            FROM khu_vuc
                            WHERE ma_loai_xe = %s
                              AND trang_thai_khu_vuc = %s
                              AND (tong_so_cho - so_xe_dang_gui) > 0
                            ORDER BY ma_khu_vuc ASC
                            LIMIT 1
                            """,
                            (VehicleTypes.XE_MAY.value, ZoneStatus.HOAT_DONG.value),
                        )

                    if not zone_row:
                        raise BusinessError("Hiện không còn khu vực xe máy còn chỗ cho khách vãng lai!")
                    if zone_row.get("ma_loai_xe") != VehicleTypes.XE_MAY.value:
                        raise BusinessError(f"Khu vực '{zone_row['ma_khu_vuc']}' không dành cho Xe máy.")
                    if zone_row.get("trang_thai_khu_vuc") != ZoneStatus.HOAT_DONG.value:
                        raise BusinessError(f"Khu vực '{zone_row['ma_khu_vuc']}' đang ngừng sử dụng.")
                    if int(zone_row.get("so_xe_dang_gui") or 0) >= int(zone_row.get("tong_so_cho") or 0):
                        raise BusinessError(f"Khu vực '{zone_row['ma_khu_vuc']}' đã hết chỗ trống.")

                    ma_vi_tri = None
                    ma_khu_vuc = str(zone_row["ma_khu_vuc"])
                    ten_khu_vuc = str(zone_row.get("ten_khu_vuc") or ma_khu_vuc)

                updated_zones = tx.execute(
                    """
                    UPDATE khu_vuc
                    SET so_xe_dang_gui = so_xe_dang_gui + 1
                    WHERE ma_khu_vuc = %s AND so_xe_dang_gui < tong_so_cho
                    """,
                    (ma_khu_vuc,),
                )
                if updated_zones <= 0:
                    raise BusinessError(f"Khu vực '{ma_khu_vuc}' đã đầy sức chứa.")

                saved_in_path = self._save_gate_image(
                    gate_image_b64, self.gates_in_dir, f"in_{normalized_plate}"
                )

                result_record = {
                    "ma_luot_gui": ma_luot_gui,
                    "loai_khach": "vang_lai",
                    "bien_so_xe": normalized_plate,
                    "ma_loai_xe": ma_loai_xe,
                    "ma_vi_tri": ma_vi_tri,
                    "ten_vi_tri": ten_vi_tri,
                    "ma_khu_vuc": ma_khu_vuc,
                    "ten_khu_vuc": ten_khu_vuc,
                    "thoi_gian_vao": now_iso,
                    "anh_vao": saved_in_path,
                    "ma_nhan_vien_vao": staff_id,
                    "barrier_status": "OPENED",
                }

        # Sau khi COMMIT PostgreSQL thành công: lưu StateStore (nếu vãng lai) & FileLogger
        if result_record["loai_khach"] == "vang_lai":
            self.state_store.add_visitor_session(
                bien_so_xe=result_record["bien_so_xe"],
                ma_loai_xe=result_record["ma_loai_xe"],
                ma_khu_vuc=result_record["ma_khu_vuc"],
                ma_vi_tri=result_record["ma_vi_tri"],
                ma_nhan_vien_vao=result_record["ma_nhan_vien_vao"],
                anh_vao=result_record["anh_vao"],
                ma_luot_gui=result_record["ma_luot_gui"],
                thoi_gian_vao=result_record["thoi_gian_vao"],
                ten_khu_vuc=result_record.get("ten_khu_vuc"),
                ten_vi_tri=result_record.get("ten_vi_tri"),
            )

        self.file_logger.log_parking_history(
            ma_luot_gui=result_record["ma_luot_gui"],
            loai_khach=result_record["loai_khach"],
            bien_so_xe=result_record["bien_so_xe"],
            ma_loai_xe=result_record["ma_loai_xe"],
            ma_khu_vuc=result_record["ma_khu_vuc"],
            ma_vi_tri=result_record["ma_vi_tri"],
            thoi_gian_vao=result_record["thoi_gian_vao"],
            anh_vao=result_record["anh_vao"],
            ma_nhan_vien_vao=result_record["ma_nhan_vien_vao"],
            trang_thai=ParkingHistoryStatus.DANG_GUI.value,
            extra={
                "ten_khu_vuc": result_record.get("ten_khu_vuc") or "",
                "ten_vi_tri": result_record.get("ten_vi_tri") or "",
                "ho_ten_chu_xe": result_record.get("ho_ten_chu_xe") or "",
                "ma_can_ho": result_record.get("ma_can_ho") or "",
            },
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="GATE_IN_ENTRY",
            doi_tuong="phuong_tien_ra_vao",
            ma_doi_tuong=result_record["bien_so_xe"],
            gia_tri_moi={
                "ma_luot_gui": result_record["ma_luot_gui"],
                "loai_khach": result_record["loai_khach"],
                "ma_khu_vuc": result_record["ma_khu_vuc"],
                "ma_vi_tri": result_record["ma_vi_tri"],
            },
        )
        self._broadcast_parking_state(
            "gate_in",
            {
                "bien_so_xe": result_record["bien_so_xe"],
                "loai_khach": result_record["loai_khach"],
                "ma_khu_vuc": result_record["ma_khu_vuc"],
                "ma_vi_tri": result_record["ma_vi_tri"],
            },
        )

        return {
            "entry": result_record,
            "message": f"Đã mở cổng cho xe {result_record['bien_so_xe']} vào bãi ({result_record.get('ten_vi_tri') or result_record.get('ten_khu_vuc')}).",
        }

    # ==========================================================================
    # HELPERS ĐỐI CHIẾU ẢNH, TÍNH PHÍ VÃNG LAI & SINH MÃ QR
    # ==========================================================================
    @staticmethod
    def _load_image_base64(path_or_b64: Optional[str]) -> str:
        if not path_or_b64:
            return ""
        text = str(path_or_b64).strip()
        if not text:
            return ""
        if text.startswith("data:image/"):
            return text.split(",", 1)[1] if "," in text else text
        candidate = Path(text)
        if candidate.is_file():
            try:
                return base64.b64encode(candidate.read_bytes()).decode("ascii")
            except OSError:
                return ""
        rel = DEFAULT_CONFIG.base_dir / text
        if rel.is_file():
            try:
                return base64.b64encode(rel.read_bytes()).decode("ascii")
            except OSError:
                return ""
        return ""

    @staticmethod
    def _parse_iso_datetime(value: Any, fallback: Optional[datetime] = None) -> datetime:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        if value:
            text = str(value).strip()
            try:
                return datetime.fromisoformat(text).replace(tzinfo=None)
            except ValueError:
                pass
        return fallback or datetime.now()

    @staticmethod
    def _format_duration_text(total_minutes: int) -> str:
        mins = max(1, int(total_minutes))
        hours, rem = divmod(mins, 60)
        days, rem_hours = divmod(hours, 24)
        parts: List[str] = []
        if days > 0:
            parts.append(f"{days} ngày")
        if rem_hours > 0:
            parts.append(f"{rem_hours} giờ")
        if rem > 0 or not parts:
            parts.append(f"{rem} phút")
        return " ".join(parts)

    @staticmethod
    def _render_qr_base64(qr_payload: str) -> str:
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(qr_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def _find_open_entry_log(self, normalized_plate: str) -> Optional[Dict[str, Any]]:
        rows = self.file_logger.query_logs(
            "parking_history",
            filters={"bien_so_xe": normalized_plate, "trang_thai": ParkingHistoryStatus.DANG_GUI.value},
            limit=10,
        )
        for row in rows:
            if normalize_plate(str(row.get("bien_so_xe") or "")) == normalized_plate:
                return row
        return None

    def _calculate_visitor_fee(
        self,
        db_or_tx: Any,
        ma_loai_xe: str,
        thoi_gian_vao: datetime,
        thoi_gian_ra: datetime,
    ) -> Dict[str, Any]:
        """Tính phí gửi xe vãng lai dựa trên bảng `bang_gia` (nhom_ap_dung='khach_vang_lai')."""
        diff_seconds = max(60.0, (thoi_gian_ra - thoi_gian_vao).total_seconds())
        so_phut = max(1, int(math.ceil(diff_seconds / 60.0)))
        so_gio = max(1, int(math.ceil(so_phut / 60.0)))

        pricing = db_or_tx.fetch_one(
            """
            SELECT ma_bang_gia, don_vi_tinh, muc_phi
            FROM bang_gia
            WHERE ma_loai_xe = %s
              AND nhom_ap_dung = %s
              AND trang_thai_gia = %s
            ORDER BY thoi_gian_bat_dau DESC, ma_bang_gia DESC
            LIMIT 1
            """,
            (
                ma_loai_xe,
                PricingGroups.KHACH_VANG_LAI.value,
                PricingStatus.DANG_AP_DUNG.value,
            ),
        )

        if pricing:
            don_vi = str(pricing.get("don_vi_tinh") or "gio").strip().lower()
            muc_phi = float(pricing.get("muc_phi") or 0)
        else:
            don_vi = "gio" if ma_loai_xe == VehicleTypes.O_TO.value else "luot"
            muc_phi = 25000.0 if ma_loai_xe == VehicleTypes.O_TO.value else 5000.0

        if don_vi == "gio":
            so_don_vi = so_gio
            so_tien = float(so_don_vi) * muc_phi
            cong_thuc = f"{so_don_vi} giờ × {int(muc_phi):,} VNĐ/giờ"
        elif don_vi == "luot":
            so_don_vi = max(1, int(math.ceil(so_gio / 4.0)))
            so_tien = float(so_don_vi) * muc_phi
            cong_thuc = f"{so_don_vi} lượt (block 4h) × {int(muc_phi):,} VNĐ/lượt"
        else:
            so_don_vi = max(1, int(math.ceil(so_gio / 24.0)))
            so_tien = float(so_don_vi) * muc_phi
            cong_thuc = f"{so_don_vi} kỳ × {int(muc_phi):,} VNĐ"

        return {
            "so_phut_gui": so_phut,
            "so_gio_gui": so_gio,
            "thoi_luong_text": self._format_duration_text(so_phut),
            "don_vi_tinh": don_vi,
            "don_gia": muc_phi,
            "so_don_vi": so_don_vi,
            "so_tien_phai_thu": round(so_tien, 2),
            "cong_thuc_text": cong_thuc,
        }

    # ==========================================================================
    # NHÓM 2.3.1 – 2.3.2: ĐỐI CHIẾU XE CỔNG RA & TÍNH PHÍ VÃNG LAI TỰ ĐỘNG
    # ==========================================================================
    def handle_gate_out_check(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """2.3.1–2.3.2: Kiểm tra xe trong bãi khi ra cổng, trả ảnh lúc vào & bảng tính phí."""
        self._ensure_staff_role(session)

        raw_plate = str(payload.get("bien_so") or payload.get("bien_so_xe") or "").strip()
        if not raw_plate:
            raise BusinessError("Vui lòng nhập hoặc nhận diện biển số xe tại cổng ra.")

        ok_plate, valid_or_err = validate_plate(raw_plate)
        if not ok_plate:
            raise BusinessError(valid_or_err)
        normalized_plate = valid_or_err

        now_dt = datetime.now()
        now_iso = now_dt.isoformat(timespec="seconds")
        db = self._db()

        # 1. Kiểm tra xe cư dân đang đỗ trong bãi
        resident_in = db.fetch_one(
            """
            SELECT pt.ma_phuong_tien, pt.ma_loai_xe, pt.bien_so_xe,
                   pt.ten_hang_dong_xe, pt.mau_xe,
                   cd.ho_ten, cd.so_dien_thoai, cd.ma_can_ho,
                   ql.ma_vi_tri_co_dinh, ql.ma_khu_vuc_phan_cong, ql.thoi_gian_vao,
                   vt.ten_vi_tri AS ten_vi_tri_co_dinh,
                   vt.ma_khu_vuc AS ma_khu_vuc_vi_tri,
                   kv.ten_khu_vuc
            FROM quan_ly_phuong_tien_ra_vao ql
            JOIN phuong_tien pt ON pt.ma_phuong_tien = ql.ma_phuong_tien
            JOIN cu_dan cd ON cd.ma_cu_dan = pt.ma_cu_dan
            LEFT JOIN vi_tri_do vt ON vt.ma_vi_tri = ql.ma_vi_tri_co_dinh
            LEFT JOIN khu_vuc kv ON kv.ma_khu_vuc = COALESCE(ql.ma_khu_vuc_phan_cong, vt.ma_khu_vuc)
            WHERE pt.bien_so_xe = %s AND ql.dang_trong_bai = TRUE
            """,
            (normalized_plate,),
        )

        if resident_in:
            entry_log = self._find_open_entry_log(normalized_plate)
            entry_dt = self._parse_iso_datetime(
                resident_in.get("thoi_gian_vao") or (entry_log or {}).get("thoi_gian_vao"),
                fallback=now_dt,
            )
            mins = max(1, int(math.ceil(max(60.0, (now_dt - entry_dt).total_seconds()) / 60.0)))
            ma_khu_vuc = (
                resident_in.get("ma_khu_vuc_phan_cong")
                or resident_in.get("ma_khu_vuc_vi_tri")
                or ""
            )
            anh_vao_path = (entry_log or {}).get("anh_vao")
            return {
                "checked_at": now_iso,
                "bien_so_xe": normalized_plate,
                "loai_khach": "cu_dan",
                "ma_loai_xe": str(resident_in.get("ma_loai_xe") or VehicleTypes.O_TO.value),
                "ma_phuong_tien": int(resident_in["ma_phuong_tien"]),
                "ma_luot_gui": (entry_log or {}).get("ma_luot_gui") or "",
                "chu_xe": {
                    "ho_ten": resident_in.get("ho_ten") or "",
                    "so_dien_thoai": resident_in.get("so_dien_thoai") or "",
                    "ma_can_ho": resident_in.get("ma_can_ho") or "",
                },
                "ma_vi_tri": resident_in.get("ma_vi_tri_co_dinh"),
                "ten_vi_tri": resident_in.get("ten_vi_tri_co_dinh") or resident_in.get("ma_vi_tri_co_dinh") or "",
                "ma_khu_vuc": ma_khu_vuc,
                "ten_khu_vuc": resident_in.get("ten_khu_vuc") or ma_khu_vuc,
                "thoi_gian_vao": entry_dt.isoformat(timespec="seconds"),
                "thoi_gian_ra": now_iso,
                "thoi_luong_phut": mins,
                "thoi_luong_text": self._format_duration_text(mins),
                "don_gia": 0.0,
                "don_vi_tinh": "thang",
                "so_tien_phai_thu": 0.0,
                "cong_thuc_text": "XE CƯ DÂN — MIỄN THU PHÍ LƯỢT (0 VNĐ)",
                "anh_cong_vao": anh_vao_path or "",
                "anh_cong_vao_base64": self._load_image_base64(anh_vao_path),
                "qr_payload": "",
                "qr_image_base64": "",
            }

        # 2. Kiểm tra xe vãng lai đang mở phiên trong StateStore (active_vang_lai.json)
        visitor = self.state_store.get_visitor_by_plate(normalized_plate)
        if visitor:
            ma_loai_xe = str(visitor.get("ma_loai_xe") or VehicleTypes.O_TO.value)
            entry_dt = self._parse_iso_datetime(visitor.get("thoi_gian_vao"), fallback=now_dt)
            fee_info = self._calculate_visitor_fee(db, ma_loai_xe, entry_dt, now_dt)
            ma_luot_gui = str(visitor.get("ma_luot_gui") or "")
            qr_payload = (
                f"SMARTPARKING|CHUNG_CU_DAO_TIEN|VANG_LAI|{normalized_plate}|"
                f"{int(fee_info['so_tien_phai_thu'])}|{ma_luot_gui}"
            )
            anh_vao_path = visitor.get("anh_vao")
            return {
                "checked_at": now_iso,
                "bien_so_xe": normalized_plate,
                "loai_khach": "vang_lai",
                "ma_loai_xe": ma_loai_xe,
                "ma_luot_gui": ma_luot_gui,
                "ma_vi_tri": visitor.get("ma_vi_tri"),
                "ten_vi_tri": visitor.get("ten_vi_tri") or visitor.get("ma_vi_tri") or "",
                "ma_khu_vuc": visitor.get("ma_khu_vuc") or "",
                "ten_khu_vuc": visitor.get("ten_khu_vuc") or visitor.get("ma_khu_vuc") or "",
                "thoi_gian_vao": entry_dt.isoformat(timespec="seconds"),
                "thoi_gian_ra": now_iso,
                "thoi_luong_phut": fee_info["so_phut_gui"],
                "thoi_luong_text": fee_info["thoi_luong_text"],
                "don_gia": fee_info["don_gia"],
                "don_vi_tinh": fee_info["don_vi_tinh"],
                "so_tien_phai_thu": fee_info["so_tien_phai_thu"],
                "cong_thuc_text": fee_info["cong_thuc_text"],
                "anh_cong_vao": anh_vao_path or "",
                "anh_cong_vao_base64": self._load_image_base64(anh_vao_path),
                "qr_payload": qr_payload,
                "qr_image_base64": self._render_qr_base64(qr_payload),
            }

        raise BusinessError(
            f"Không tìm thấy phiên gửi xe đang mở trong bãi cho biển số '{normalized_plate}'!"
        )

    # ==========================================================================
    # HELPERS CA LÀM VIỆC & BÁO CÁO LƯỢT / THU PHÍ
    # ==========================================================================
    @staticmethod
    def _determine_shift_info(dt: datetime) -> tuple[str, str]:
        hour = dt.hour
        if 6 <= hour < 14:
            return ShiftTypes.CA_SANG.value, "Ca Sáng (06:00 - 14:00)"
        if 14 <= hour < 22:
            return ShiftTypes.CA_CHIEU.value, "Ca Chiều (14:00 - 22:00)"
        return ShiftTypes.CA_DEM.value, "Ca Đêm (22:00 - 06:00)"

    def _load_shift_sessions(self) -> List[Dict[str, Any]]:
        with self._shift_lock:
            if not self.shift_state_file.exists():
                return []
            try:
                raw = self.shift_state_file.read_text(encoding="utf-8").strip()
                if not raw:
                    return []
                data = json.loads(raw)
                return [dict(x) for x in data if isinstance(x, dict)] if isinstance(data, list) else []
            except Exception:
                return []

    def _save_shift_sessions(self, items: List[Dict[str, Any]]) -> None:
        with self._shift_lock:
            self.shift_state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.shift_state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, self.shift_state_file)

    def _resolve_staff_profile(self, db_or_tx: Any, session: ClientSession) -> Dict[str, Any]:
        staff_id = self._resolve_staff_id(db_or_tx, session)
        row = db_or_tx.fetch_one(
            "SELECT ma_nhan_vien, ma_nhan_vien_code, ho_ten, chuc_vu FROM nhan_vien WHERE ma_nhan_vien = %s",
            (staff_id,),
        )
        if row:
            return dict(row)
        return {
            "ma_nhan_vien": staff_id,
            "ma_nhan_vien_code": f"NV{staff_id:03d}",
            "ho_ten": session.ho_ten or session.ten_dang_nhap or "Nhân viên bãi xe",
            "chuc_vu": "nhan_vien_bai_xe",
        }

    def _ensure_open_shift(
        self,
        db_or_tx: Any,
        session: ClientSession,
        now_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Đảm bảo nhân viên có ca làm việc đang mở (lưu bền vững trong shift_sessions.json)."""
        now_val = now_dt or datetime.now()
        staff = self._resolve_staff_profile(db_or_tx, session)
        staff_id = int(staff["ma_nhan_vien"])
        shift_code, shift_label = self._determine_shift_info(now_val)

        with self._shift_lock:
            shifts = self._load_shift_sessions()
            for item in shifts:
                if int(item.get("ma_nhan_vien") or 0) == staff_id and item.get("trang_thai_ca") == "dang_mo":
                    return dict(item)

            new_shift = {
                "ma_ca_lam_viec": f"CA-{now_val.strftime('%Y%m%d%H%M%S')}-NV{staff_id}",
                "ma_nhan_vien": staff_id,
                "ma_nhan_vien_code": staff.get("ma_nhan_vien_code") or f"NV{staff_id:03d}",
                "ho_ten": staff.get("ho_ten") or "",
                "ca_lam_viec": shift_code,
                "ten_ca": shift_label,
                "ngay_ghi_nhan": now_val.date().isoformat(),
                "gio_bat_dau": now_val.isoformat(timespec="seconds"),
                "gio_ket_thuc": None,
                "trang_thai_ca": "dang_mo",
            }
            shifts.insert(0, new_shift)
            self._save_shift_sessions(shifts)
            return dict(new_shift)

    def _upsert_shift_report(
        self,
        tx: Any,
        staff_id: int,
        ma_khu_vuc: Optional[str],
        ngay_ghi_nhan: date,
        ca_lam_viec: str,
        delta_oto_vl: int = 0,
        delta_xemay_vl: int = 0,
        delta_cu_dan: int = 0,
        delta_tien: float = 0.0,
    ) -> None:
        """Cập nhật cộng dồn vào bảng 14 `bao_cao_luot_va_thu_phi`."""
        if not ma_khu_vuc:
            return
        existing = tx.fetch_one(
            """
            SELECT ma_bao_cao
            FROM bao_cao_luot_va_thu_phi
            WHERE ma_nhan_vien = %s
              AND ma_khu_vuc = %s
              AND ngay_ghi_nhan = %s
              AND ca_lam_viec = %s
            LIMIT 1
            """,
            (staff_id, ma_khu_vuc, ngay_ghi_nhan, ca_lam_viec),
        )
        if existing:
            tx.execute(
                """
                UPDATE bao_cao_luot_va_thu_phi
                SET so_luot_oto_vang_lai = so_luot_oto_vang_lai + %s,
                    so_luot_xemay_vang_lai = so_luot_xemay_vang_lai + %s,
                    so_luot_xe_cu_dan = so_luot_xe_cu_dan + %s,
                    tong_tien_thu_vang_lai = tong_tien_thu_vang_lai + %s
                WHERE ma_bao_cao = %s
                """,
                (delta_oto_vl, delta_xemay_vl, delta_cu_dan, delta_tien, int(existing["ma_bao_cao"])),
            )
        else:
            tx.execute(
                """
                INSERT INTO bao_cao_luot_va_thu_phi (
                    ma_nhan_vien, ma_khu_vuc, ngay_ghi_nhan, ca_lam_viec,
                    so_luot_oto_vang_lai, so_luot_xemay_vang_lai,
                    so_luot_xe_cu_dan, tong_tien_thu_vang_lai
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    staff_id,
                    ma_khu_vuc,
                    ngay_ghi_nhan,
                    ca_lam_viec,
                    delta_oto_vl,
                    delta_xemay_vl,
                    delta_cu_dan,
                    delta_tien,
                ),
            )

    # ==========================================================================
    # NHÓM 2.3.3 (WORKFLOW 04): XÁC NHẬN XE RA, THU PHÍ VÃNG LAI & MỞ CỔNG
    # ==========================================================================
    def handle_gate_out_confirm(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """2.3.3: Xác nhận thu phí (nếu vãng lai) & mở barie cổng ra ACID."""
        self._ensure_staff_role(session)

        raw_plate = str(payload.get("bien_so") or payload.get("bien_so_xe") or "").strip()
        if not raw_plate:
            raise BusinessError("Thiếu biển số xe để xác nhận mở cổng ra.")

        ok_plate, valid_or_err = validate_plate(raw_plate)
        if not ok_plate:
            raise BusinessError(valid_or_err)
        normalized_plate = valid_or_err

        payment_method = str(payload.get("phuong_thuc_thanh_toan") or PaymentMethods.TIEN_MAT.value).strip()
        if payment_method not in (PaymentMethods.TIEN_MAT.value, PaymentMethods.CHUYEN_KHOAN_QR.value):
            raise BusinessError("Phương thức thanh toán phải là 'tien_mat' hoặc 'chuyen_khoan_qr'.")

        gate_out_b64 = payload.get("gate_out_image_base64") or payload.get("gate_image_base64") or payload.get("anh_ra_base64")
        now_dt = datetime.now()
        now_iso = now_dt.isoformat(timespec="seconds")
        db = self._db()

        exit_record: Dict[str, Any] = {}
        pop_visitor_plate: Optional[str] = None

        with db.transaction() as tx:
            shift_info = self._ensure_open_shift(tx, session, now_dt=now_dt)
            staff_id = int(shift_info["ma_nhan_vien"])
            ca_code = str(shift_info["ca_lam_viec"])

            resident_in = tx.fetch_one(
                """
                SELECT pt.ma_phuong_tien, pt.ma_loai_xe, pt.bien_so_xe,
                       cd.ho_ten, cd.ma_can_ho,
                       ql.ma_vi_tri_co_dinh, ql.ma_khu_vuc_phan_cong, ql.thoi_gian_vao,
                       vt.ten_vi_tri AS ten_vi_tri_co_dinh,
                       vt.ma_khu_vuc AS ma_khu_vuc_vi_tri,
                       kv.ten_khu_vuc
                FROM quan_ly_phuong_tien_ra_vao ql
                JOIN phuong_tien pt ON pt.ma_phuong_tien = ql.ma_phuong_tien
                JOIN cu_dan cd ON cd.ma_cu_dan = pt.ma_cu_dan
                LEFT JOIN vi_tri_do vt ON vt.ma_vi_tri = ql.ma_vi_tri_co_dinh
                LEFT JOIN khu_vuc kv ON kv.ma_khu_vuc = COALESCE(ql.ma_khu_vuc_phan_cong, vt.ma_khu_vuc)
                WHERE pt.bien_so_xe = %s AND ql.dang_trong_bai = TRUE
                """,
                (normalized_plate,),
            )

            if resident_in:
                ma_pt = int(resident_in["ma_phuong_tien"])
                ma_loai_xe = str(resident_in.get("ma_loai_xe") or VehicleTypes.O_TO.value)
                ma_vi_tri = resident_in.get("ma_vi_tri_co_dinh")
                ma_khu_vuc = (
                    resident_in.get("ma_khu_vuc_phan_cong")
                    or resident_in.get("ma_khu_vuc_vi_tri")
                    or ""
                )
                entry_log = self._find_open_entry_log(normalized_plate)
                entry_dt = self._parse_iso_datetime(
                    resident_in.get("thoi_gian_vao") or (entry_log or {}).get("thoi_gian_vao"),
                    fallback=now_dt,
                )
                saved_out_path = self._save_gate_image(
                    gate_out_b64, self.gates_out_dir, f"out_{normalized_plate}"
                )

                tx.execute(
                    """
                    UPDATE quan_ly_phuong_tien_ra_vao
                    SET dang_trong_bai = FALSE,
                        thoi_gian_ra = %s
                    WHERE ma_phuong_tien = %s
                    """,
                    (now_dt, ma_pt),
                )
                if ma_loai_xe == VehicleTypes.O_TO.value and ma_vi_tri:
                    tx.execute(
                        """
                        UPDATE vi_tri_do
                        SET trang_thai_vi_tri = %s,
                            thoi_gian_cap_nhat = %s
                        WHERE ma_vi_tri = %s
                        """,
                        (SlotStatus.DA_PHAN_CHO.value, now_dt, ma_vi_tri),
                    )
                if ma_khu_vuc:
                    tx.execute(
                        """
                        UPDATE khu_vuc
                        SET so_xe_dang_gui = GREATEST(0, so_xe_dang_gui - 1)
                        WHERE ma_khu_vuc = %s
                        """,
                        (ma_khu_vuc,),
                    )
                    self._upsert_shift_report(
                        tx,
                        staff_id=staff_id,
                        ma_khu_vuc=ma_khu_vuc,
                        ngay_ghi_nhan=now_dt.date(),
                        ca_lam_viec=ca_code,
                        delta_cu_dan=1,
                    )

                ma_luot_gui = (entry_log or {}).get("ma_luot_gui") or f"LG-{now_dt.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
                exit_record = {
                    "ma_luot_gui": ma_luot_gui,
                    "loai_khach": "cu_dan",
                    "bien_so_xe": normalized_plate,
                    "ma_loai_xe": ma_loai_xe,
                    "ma_vi_tri": ma_vi_tri,
                    "ten_vi_tri": resident_in.get("ten_vi_tri_co_dinh") or ma_vi_tri or "",
                    "ma_khu_vuc": ma_khu_vuc,
                    "ten_khu_vuc": resident_in.get("ten_khu_vuc") or ma_khu_vuc,
                    "thoi_gian_vao": entry_dt.isoformat(timespec="seconds"),
                    "thoi_gian_ra": now_iso,
                    "anh_vao": (entry_log or {}).get("anh_vao"),
                    "anh_ra": saved_out_path,
                    "tien_thu": 0.0,
                    "phuong_thuc_thanh_toan": "ve_thang",
                    "ma_nhan_vien_vao": (entry_log or {}).get("ma_nhan_vien_vao"),
                    "ma_nhan_vien_ra": staff_id,
                    "ma_ca_lam_viec": shift_info["ma_ca_lam_viec"],
                    "ca_lam_viec": ca_code,
                    "ho_ten_chu_xe": resident_in.get("ho_ten") or "",
                    "ma_can_ho": resident_in.get("ma_can_ho") or "",
                    "barrier_status": "OPENED",
                }
            else:
                visitor = self.state_store.get_visitor_by_plate(normalized_plate)
                if visitor is None:
                    raise BusinessError(
                        f"Không tìm thấy phiên gửi xe đang mở trong bãi cho biển số '{normalized_plate}'!"
                    )

                ma_loai_xe = str(visitor.get("ma_loai_xe") or VehicleTypes.O_TO.value)
                ma_vi_tri = visitor.get("ma_vi_tri")
                ma_khu_vuc = str(visitor.get("ma_khu_vuc") or "")
                entry_dt = self._parse_iso_datetime(visitor.get("thoi_gian_vao"), fallback=now_dt)
                fee_info = self._calculate_visitor_fee(tx, ma_loai_xe, entry_dt, now_dt)
                so_tien = float(fee_info["so_tien_phai_thu"])
                saved_out_path = self._save_gate_image(
                    gate_out_b64, self.gates_out_dir, f"out_{normalized_plate}"
                )

                if ma_loai_xe == VehicleTypes.O_TO.value and ma_vi_tri:
                    tx.execute(
                        """
                        UPDATE vi_tri_do
                        SET trang_thai_vi_tri = %s,
                            thoi_gian_cap_nhat = %s
                        WHERE ma_vi_tri = %s
                        """,
                        (SlotStatus.TRONG.value, now_dt, ma_vi_tri),
                    )
                if ma_khu_vuc:
                    tx.execute(
                        """
                        UPDATE khu_vuc
                        SET so_xe_dang_gui = GREATEST(0, so_xe_dang_gui - 1)
                        WHERE ma_khu_vuc = %s
                        """,
                        (ma_khu_vuc,),
                    )
                    self._upsert_shift_report(
                        tx,
                        staff_id=staff_id,
                        ma_khu_vuc=ma_khu_vuc,
                        ngay_ghi_nhan=now_dt.date(),
                        ca_lam_viec=ca_code,
                        delta_oto_vl=1 if ma_loai_xe == VehicleTypes.O_TO.value else 0,
                        delta_xemay_vl=1 if ma_loai_xe == VehicleTypes.XE_MAY.value else 0,
                        delta_tien=so_tien,
                    )

                pop_visitor_plate = normalized_plate
                ma_tham_chieu = f"VL-{now_dt.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
                exit_record = {
                    "ma_luot_gui": str(visitor.get("ma_luot_gui") or ma_tham_chieu),
                    "ma_tham_chieu": ma_tham_chieu,
                    "loai_khach": "vang_lai",
                    "bien_so_xe": normalized_plate,
                    "ma_loai_xe": ma_loai_xe,
                    "ma_vi_tri": ma_vi_tri,
                    "ten_vi_tri": visitor.get("ten_vi_tri") or ma_vi_tri or "",
                    "ma_khu_vuc": ma_khu_vuc,
                    "ten_khu_vuc": visitor.get("ten_khu_vuc") or ma_khu_vuc,
                    "thoi_gian_vao": entry_dt.isoformat(timespec="seconds"),
                    "thoi_gian_ra": now_iso,
                    "thoi_luong_text": fee_info["thoi_luong_text"],
                    "anh_vao": visitor.get("anh_vao"),
                    "anh_ra": saved_out_path,
                    "tien_thu": so_tien,
                    "cong_thuc_text": fee_info["cong_thuc_text"],
                    "phuong_thuc_thanh_toan": payment_method,
                    "ma_nhan_vien_vao": visitor.get("ma_nhan_vien_vao"),
                    "ma_nhan_vien_ra": staff_id,
                    "ma_ca_lam_viec": shift_info["ma_ca_lam_viec"],
                    "ca_lam_viec": ca_code,
                    "barrier_status": "OPENED",
                }

        if pop_visitor_plate is not None:
            self.state_store.pop_visitor_session(pop_visitor_plate)

        self.file_logger.log_parking_history(
            ma_luot_gui=exit_record["ma_luot_gui"],
            loai_khach=exit_record["loai_khach"],
            bien_so_xe=exit_record["bien_so_xe"],
            ma_loai_xe=exit_record["ma_loai_xe"],
            ma_khu_vuc=exit_record["ma_khu_vuc"],
            ma_vi_tri=exit_record["ma_vi_tri"],
            thoi_gian_vao=exit_record["thoi_gian_vao"],
            thoi_gian_ra=exit_record["thoi_gian_ra"],
            anh_vao=exit_record.get("anh_vao"),
            anh_ra=exit_record.get("anh_ra"),
            tien_thu=exit_record["tien_thu"],
            ma_nhan_vien_vao=exit_record.get("ma_nhan_vien_vao"),
            ma_nhan_vien_ra=exit_record.get("ma_nhan_vien_ra"),
            trang_thai=ParkingHistoryStatus.DA_RA_BAI.value,
            extra={
                "phuong_thuc_thanh_toan": exit_record["phuong_thuc_thanh_toan"],
                "ma_ca_lam_viec": exit_record["ma_ca_lam_viec"],
                "ca_lam_viec": exit_record["ca_lam_viec"],
                "ma_tham_chieu": exit_record.get("ma_tham_chieu") or "",
                "cong_thuc_text": exit_record.get("cong_thuc_text") or "",
                "ten_khu_vuc": exit_record.get("ten_khu_vuc") or "",
                "ten_vi_tri": exit_record.get("ten_vi_tri") or "",
                "ho_ten_chu_xe": exit_record.get("ho_ten_chu_xe") or "",
                "ma_can_ho": exit_record.get("ma_can_ho") or "",
            },
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="GATE_OUT_EXIT",
            doi_tuong="phuong_tien_ra_vao",
            ma_doi_tuong=exit_record["bien_so_xe"],
            gia_tri_moi={
                "ma_luot_gui": exit_record["ma_luot_gui"],
                "loai_khach": exit_record["loai_khach"],
                "tien_thu": exit_record["tien_thu"],
                "phuong_thuc_thanh_toan": exit_record["phuong_thuc_thanh_toan"],
                "ma_ca_lam_viec": exit_record["ma_ca_lam_viec"],
            },
        )
        self._broadcast_parking_state(
            "gate_out",
            {
                "bien_so_xe": exit_record["bien_so_xe"],
                "loai_khach": exit_record["loai_khach"],
                "tien_thu": exit_record["tien_thu"],
                "ma_khu_vuc": exit_record["ma_khu_vuc"],
                "ma_vi_tri": exit_record["ma_vi_tri"],
            },
        )

        return {
            "exit": exit_record,
            "message": (
                f"Đã xác nhận thu {int(exit_record['tien_thu']):,} VNĐ và mở cổng ra cho xe {exit_record['bien_so_xe']}."
                if exit_record["loai_khach"] == "vang_lai"
                else f"Đã mở cổng ra cho xe cư dân {exit_record['bien_so_xe']} (Miễn phí lượt)."
            ),
        }

    # ==========================================================================
    # NHÓM 2.4: QUẢN LÝ CA LÀM VIỆC, DOANH THU THEO CA & CHỐT CA
    # ==========================================================================
    def _collect_shift_metrics(
        self, db_or_tx: Any, shift_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Tổng hợp KPI và danh sách giao dịch thu phí của một ca làm việc."""
        shift_id = str(shift_info.get("ma_ca_lam_viec") or "")
        staff_id = int(shift_info.get("ma_nhan_vien") or 0)
        shift_date = str(shift_info.get("ngay_ghi_nhan") or datetime.now().date().isoformat())[:10]
        ca_code = str(shift_info.get("ca_lam_viec") or "")

        raw_logs = self.file_logger.query_logs(
            "parking_history",
            start_date=shift_date,
            end_date=shift_date,
            limit=0,
        )

        transactions: List[Dict[str, Any]] = []
        cash_total = 0.0
        qr_total = 0.0
        oto_vl = 0
        xemay_vl = 0
        cu_dan_turns = 0

        for rec in raw_logs:
            if rec.get("trang_thai") != ParkingHistoryStatus.DA_RA_BAI.value:
                continue
            rec_shift_id = str(rec.get("ma_ca_lam_viec") or "")
            if rec_shift_id:
                if rec_shift_id != shift_id:
                    continue
            else:
                if int(rec.get("ma_nhan_vien_ra") or 0) != staff_id or str(rec.get("ca_lam_viec") or "") != ca_code:
                    continue

            loai_khach = str(rec.get("loai_khach") or "")
            ma_loai_xe = str(rec.get("ma_loai_xe") or VehicleTypes.O_TO.value)
            tien_thu = float(rec.get("tien_thu") or 0)
            method = str(rec.get("phuong_thuc_thanh_toan") or PaymentMethods.TIEN_MAT.value)

            if loai_khach == "cu_dan":
                cu_dan_turns += 1
                continue

            if ma_loai_xe == VehicleTypes.O_TO.value:
                oto_vl += 1
            else:
                xemay_vl += 1

            if method == PaymentMethods.CHUYEN_KHOAN_QR.value:
                qr_total += tien_thu
            else:
                cash_total += tien_thu

            transactions.append(
                {
                    "ma_luot_gui": rec.get("ma_luot_gui") or "",
                    "ma_tham_chieu": rec.get("ma_tham_chieu") or rec.get("ma_luot_gui") or "",
                    "thoi_gian_vao": rec.get("thoi_gian_vao") or "",
                    "thoi_gian_ra": rec.get("thoi_gian_ra") or rec.get("timestamp") or "",
                    "bien_so_xe": rec.get("bien_so_xe") or "",
                    "ma_loai_xe": ma_loai_xe,
                    "ma_khu_vuc": rec.get("ma_khu_vuc") or "",
                    "ma_vi_tri": rec.get("ma_vi_tri") or "",
                    "phuong_thuc_thanh_toan": method,
                    "tien_thu": tien_thu,
                    "cong_thuc_text": rec.get("cong_thuc_text") or "",
                }
            )

        report_rows = db_or_tx.fetch_all(
            """
            SELECT ma_khu_vuc, so_luot_oto_vang_lai, so_luot_xemay_vang_lai,
                   so_luot_xe_cu_dan, tong_tien_thu_vang_lai
            FROM bao_cao_luot_va_thu_phi
            WHERE ma_nhan_vien = %s
              AND ngay_ghi_nhan = %s
              AND ca_lam_viec = %s
            ORDER BY ma_khu_vuc ASC
            """,
            (staff_id, shift_date, ca_code),
        )
        db_total_money = sum(float(r.get("tong_tien_thu_vang_lai") or 0) for r in report_rows)
        log_total_money = cash_total + qr_total
        # Nếu ca đã chốt có snapshot cố định thì ưu tiên snapshot/log của riêng ca đó
        if shift_info.get("trang_thai_ca") == "da_dong" and shift_info.get("tong_doanh_thu_chot_ca") is not None:
            tong_doanh_thu = max(log_total_money, float(shift_info.get("tong_doanh_thu_chot_ca") or 0))
        else:
            tong_doanh_thu = log_total_money if transactions else max(log_total_money, db_total_money)

        return {
            "kpi": {
                "tong_doanh_thu": round(tong_doanh_thu, 2),
                "doanh_thu_tien_mat": round(cash_total, 2),
                "doanh_thu_qr": round(qr_total, 2),
                "so_luot_thu_phi": len(transactions),
                "so_luot_oto_vang_lai": oto_vl,
                "so_luot_xemay_vang_lai": xemay_vl,
                "so_luot_xe_cu_dan": cu_dan_turns,
            },
            "transactions": transactions,
            "zone_reports": report_rows,
        }

    def handle_shift_get_revenue(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """2.4: Lấy thông tin ca trực hiện tại, KPI doanh thu, chi tiết giao dịch & lịch sử ca."""
        self._ensure_staff_role(session)
        db = self._db()
        current_shift = self._ensure_open_shift(db, session)
        staff_id = int(current_shift["ma_nhan_vien"])

        requested_shift_id = str(payload.get("ma_ca_lam_viec") or "").strip()
        all_shifts = [
            s for s in self._load_shift_sessions()
            if int(s.get("ma_nhan_vien") or 0) == staff_id
        ]

        selected_shift = current_shift
        if requested_shift_id:
            for s in all_shifts:
                if str(s.get("ma_ca_lam_viec") or "") == requested_shift_id:
                    selected_shift = dict(s)
                    break

        metrics = self._collect_shift_metrics(db, selected_shift)

        shift_history: List[Dict[str, Any]] = []
        for s in all_shifts[:20]:
            s_copy = dict(s)
            if str(s_copy.get("ma_ca_lam_viec") or "") == str(selected_shift.get("ma_ca_lam_viec") or ""):
                s_copy.setdefault("tong_doanh_thu_chot_ca", metrics["kpi"]["tong_doanh_thu"])
                s_copy.setdefault("so_luot_thu_phi", metrics["kpi"]["so_luot_thu_phi"])
            elif s_copy.get("tong_doanh_thu_chot_ca") is None:
                m = self._collect_shift_metrics(db, s_copy)
                s_copy["tong_doanh_thu_chot_ca"] = m["kpi"]["tong_doanh_thu"]
                s_copy["so_luot_thu_phi"] = m["kpi"]["so_luot_thu_phi"]
            shift_history.append(s_copy)

        return {
            "current_shift": current_shift,
            "selected_shift": selected_shift,
            "kpi": metrics["kpi"],
            "transactions": metrics["transactions"],
            "zone_reports": metrics["zone_reports"],
            "shift_history": shift_history,
        }

    def handle_shift_close(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """2.4: Chốt ca làm việc đang mở của nhân viên và ghi nhận tổng kết bàn giao."""
        self._ensure_staff_role(session)
        db = self._db()
        staff = self._resolve_staff_profile(db, session)
        staff_id = int(staff["ma_nhan_vien"])
        requested_shift_id = str(payload.get("ma_ca_lam_viec") or "").strip()
        ghi_chu = str(payload.get("ghi_chu") or "").strip()
        now_iso = datetime.now().isoformat(timespec="seconds")

        with self._shift_lock:
            shifts = self._load_shift_sessions()
            target_idx: Optional[int] = None
            for idx, item in enumerate(shifts):
                if int(item.get("ma_nhan_vien") or 0) != staff_id:
                    continue
                if requested_shift_id and str(item.get("ma_ca_lam_viec") or "") != requested_shift_id:
                    continue
                if item.get("trang_thai_ca") == "dang_mo":
                    target_idx = idx
                    break

            if target_idx is None:
                raise BusinessError("Hiện không có ca trực nào đang mở để chốt ca.")

            target_shift = dict(shifts[target_idx])
            metrics = self._collect_shift_metrics(db, target_shift)
            kpi = metrics["kpi"]

            target_shift["trang_thai_ca"] = "da_dong"
            target_shift["gio_ket_thuc"] = now_iso
            target_shift["tong_doanh_thu_chot_ca"] = kpi["tong_doanh_thu"]
            target_shift["doanh_thu_tien_mat"] = kpi["doanh_thu_tien_mat"]
            target_shift["doanh_thu_qr"] = kpi["doanh_thu_qr"]
            target_shift["so_luot_thu_phi"] = kpi["so_luot_thu_phi"]
            target_shift["so_luot_xe_cu_dan"] = kpi["so_luot_xe_cu_dan"]
            target_shift["ghi_chu_ban_giao"] = ghi_chu
            shifts[target_idx] = target_shift
            self._save_shift_sessions(shifts)

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="SHIFT_CLOSE",
            doi_tuong="ca_lam_viec",
            ma_doi_tuong=target_shift["ma_ca_lam_viec"],
            gia_tri_moi={
                "tong_doanh_thu_chot_ca": target_shift["tong_doanh_thu_chot_ca"],
                "doanh_thu_tien_mat": target_shift["doanh_thu_tien_mat"],
                "doanh_thu_qr": target_shift["doanh_thu_qr"],
                "so_luot_thu_phi": target_shift["so_luot_thu_phi"],
            },
        )

        return {
            "closed_shift": target_shift,
            "kpi": kpi,
            "message": (
                f"Đã chốt {target_shift['ten_ca']} ({target_shift['ma_ca_lam_viec']}). "
                f"Tổng bàn giao: {int(target_shift['tong_doanh_thu_chot_ca']):,} VNĐ."
            ),
        }

    # ==========================================================================
    # NHÓM 2.5.1 – 2.5.3: TRA CỨU PHƯƠNG TIỆN TẠI CHỐT BẢO VỆ
    # ==========================================================================
    @staticmethod
    def _plate_matches_query(plate: str, query: str) -> bool:
        if not query:
            return True
        q_raw = query.strip().upper()
        p_raw = str(plate or "").strip().upper()
        if q_raw in p_raw:
            return True
        q_compact = "".join(ch for ch in q_raw if ch.isalnum())
        p_compact = "".join(ch for ch in p_raw if ch.isalnum())
        return bool(q_compact and q_compact in p_compact)

    def _build_plate_history_map(self) -> Dict[str, List[Dict[str, Any]]]:
        """Gộp lịch sử parking_history_*.jsonl theo `ma_luot_gui` và nhóm theo biển số."""
        raw = self.file_logger.query_logs("parking_history", limit=500)
        merged: Dict[str, Dict[str, Any]] = {}
        for rec in reversed(raw):
            sid = str(rec.get("ma_luot_gui") or "").strip()
            if not sid:
                sid = f"AUTO-{rec.get('bien_so_xe', '')}-{rec.get('timestamp', '')}"
            existing = merged.get(sid, {})
            combined = dict(existing)
            for k, v in rec.items():
                if v not in (None, "") or k not in combined:
                    combined[k] = v
            merged[sid] = combined

        by_plate: Dict[str, List[Dict[str, Any]]] = {}
        for item in merged.values():
            plate = normalize_plate(str(item.get("bien_so_xe") or ""))
            if not plate:
                continue
            by_plate.setdefault(plate, []).append(item)

        for plate_list in by_plate.values():
            plate_list.sort(
                key=lambda x: str(x.get("thoi_gian_vao") or x.get("timestamp") or ""),
                reverse=True,
            )
        return by_plate

    def handle_gate_lookup_vehicle(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """2.5.1–2.5.3: Tra cứu biển số, trạng thái trong/ngoài bãi và lịch sử ảnh cổng vào/ra."""
        self._ensure_staff_role(session)

        search_plate = str(
            payload.get("search_plate") or payload.get("bien_so") or payload.get("keyword") or ""
        ).strip()
        trang_thai_filter = str(payload.get("trang_thai_filter") or "all").strip().lower()
        loai_khach_filter = str(payload.get("loai_khach_filter") or "all").strip().lower()
        include_images = bool(payload.get("include_images", True))
        limit = max(1, min(200, int(payload.get("limit") or 100)))

        db = self._db()
        history_by_plate = self._build_plate_history_map()

        results: List[Dict[str, Any]] = []
        seen_plates: set[str] = set()

        # 1. Xe cư dân trong CSDL
        if loai_khach_filter in ("all", "cu_dan"):
            resident_rows = db.fetch_all(
                """
                SELECT pt.ma_phuong_tien, pt.bien_so_xe, pt.ma_loai_xe,
                       pt.ten_hang_dong_xe, pt.mau_xe, pt.trang_thai_xe,
                       cd.ho_ten, cd.so_dien_thoai, cd.ma_can_ho,
                       ql.dang_trong_bai, ql.thoi_gian_vao, ql.thoi_gian_ra,
                       ql.ma_vi_tri_co_dinh, ql.ma_khu_vuc_phan_cong,
                       vt.ten_vi_tri AS ten_vi_tri_co_dinh,
                       vt.ma_khu_vuc AS ma_khu_vuc_vi_tri,
                       kv.ten_khu_vuc
                FROM phuong_tien pt
                JOIN cu_dan cd ON cd.ma_cu_dan = pt.ma_cu_dan
                LEFT JOIN quan_ly_phuong_tien_ra_vao ql ON ql.ma_phuong_tien = pt.ma_phuong_tien
                LEFT JOIN vi_tri_do vt ON vt.ma_vi_tri = ql.ma_vi_tri_co_dinh
                LEFT JOIN khu_vuc kv ON kv.ma_khu_vuc = COALESCE(ql.ma_khu_vuc_phan_cong, vt.ma_khu_vuc)
                WHERE pt.trang_thai_xe = %s
                ORDER BY COALESCE(ql.dang_trong_bai, FALSE) DESC, pt.bien_so_xe ASC
                """,
                (VehicleStatus.DANG_HOAT_DONG.value,),
            )
            for r in resident_rows:
                plate = normalize_plate(str(r.get("bien_so_xe") or ""))
                if not plate or not self._plate_matches_query(plate, search_plate):
                    continue
                in_yard = bool(r.get("dang_trong_bai"))
                status_code = "dang_do_trong_bai" if in_yard else "ngoai_bai"
                if trang_thai_filter != "all" and status_code != trang_thai_filter:
                    continue

                seen_plates.add(plate)
                ma_khu_vuc = r.get("ma_khu_vuc_phan_cong") or r.get("ma_khu_vuc_vi_tri") or ""
                results.append(
                    {
                        "bien_so_xe": plate,
                        "loai_khach": "cu_dan",
                        "phan_loai_text": f"Cư dân · Căn hộ {r.get('ma_can_ho') or '-'}",
                        "ma_loai_xe": str(r.get("ma_loai_xe") or VehicleTypes.O_TO.value),
                        "ten_hang_dong_xe": r.get("ten_hang_dong_xe") or "",
                        "mau_xe": r.get("mau_xe") or "",
                        "ho_ten_chu_xe": r.get("ho_ten") or "",
                        "so_dien_thoai": r.get("so_dien_thoai") or "",
                        "ma_can_ho": r.get("ma_can_ho") or "",
                        "dang_trong_bai": in_yard,
                        "trang_thai_hien_tai": status_code,
                        "ma_vi_tri": r.get("ma_vi_tri_co_dinh"),
                        "ten_vi_tri": r.get("ten_vi_tri_co_dinh") or r.get("ma_vi_tri_co_dinh") or "",
                        "ma_khu_vuc": ma_khu_vuc,
                        "ten_khu_vuc": r.get("ten_khu_vuc") or ma_khu_vuc,
                        "thoi_gian_vao_gan_nhat": str(r.get("thoi_gian_vao") or ""),
                        "thoi_gian_ra_gan_nhat": str(r.get("thoi_gian_ra") or ""),
                    }
                )

        # 2. Xe vãng lai đang gửi trong bãi (StateStore)
        if loai_khach_filter in ("all", "vang_lai") and trang_thai_filter in ("all", "dang_do_trong_bai"):
            for v in self.state_store.list_visitor_sessions():
                plate = normalize_plate(str(v.get("bien_so_xe") or ""))
                if not plate or plate in seen_plates or not self._plate_matches_query(plate, search_plate):
                    continue
                seen_plates.add(plate)
                results.append(
                    {
                        "bien_so_xe": plate,
                        "loai_khach": "vang_lai",
                        "phan_loai_text": "Khách vãng lai (Đang gửi)",
                        "ma_loai_xe": str(v.get("ma_loai_xe") or VehicleTypes.O_TO.value),
                        "ten_hang_dong_xe": "Khách vãng lai",
                        "mau_xe": "",
                        "ho_ten_chu_xe": "Khách vãng lai",
                        "so_dien_thoai": "",
                        "ma_can_ho": "",
                        "dang_trong_bai": True,
                        "trang_thai_hien_tai": "dang_do_trong_bai",
                        "ma_vi_tri": v.get("ma_vi_tri"),
                        "ten_vi_tri": v.get("ten_vi_tri") or v.get("ma_vi_tri") or "",
                        "ma_khu_vuc": v.get("ma_khu_vuc") or "",
                        "ten_khu_vuc": v.get("ten_khu_vuc") or v.get("ma_khu_vuc") or "",
                        "thoi_gian_vao_gan_nhat": str(v.get("thoi_gian_vao") or ""),
                        "thoi_gian_ra_gan_nhat": "",
                    }
                )

        # 3. Xe vãng lai đã ra bãi trước đó (từ parking_history)
        if loai_khach_filter in ("all", "vang_lai") and trang_thai_filter in ("all", "ngoai_bai"):
            for plate, hist_list in history_by_plate.items():
                if plate in seen_plates or not self._plate_matches_query(plate, search_plate):
                    continue
                latest = hist_list[0]
                if str(latest.get("loai_khach") or "") != "vang_lai":
                    continue
                seen_plates.add(plate)
                results.append(
                    {
                        "bien_so_xe": plate,
                        "loai_khach": "vang_lai",
                        "phan_loai_text": "Khách vãng lai (Đã ra bãi)",
                        "ma_loai_xe": str(latest.get("ma_loai_xe") or VehicleTypes.O_TO.value),
                        "ten_hang_dong_xe": "Khách vãng lai",
                        "mau_xe": "",
                        "ho_ten_chu_xe": "Khách vãng lai",
                        "so_dien_thoai": "",
                        "ma_can_ho": "",
                        "dang_trong_bai": False,
                        "trang_thai_hien_tai": "ngoai_bai",
                        "ma_vi_tri": latest.get("ma_vi_tri"),
                        "ten_vi_tri": latest.get("ten_vi_tri") or latest.get("ma_vi_tri") or "",
                        "ma_khu_vuc": latest.get("ma_khu_vuc") or "",
                        "ten_khu_vuc": latest.get("ten_khu_vuc") or latest.get("ma_khu_vuc") or "",
                        "thoi_gian_vao_gan_nhat": str(latest.get("thoi_gian_vao") or ""),
                        "thoi_gian_ra_gan_nhat": str(latest.get("thoi_gian_ra") or ""),
                    }
                )

        # Đính kèm lịch sử vào/ra gần nhất (tối đa 10 lượt/xe) kèm ảnh cổng vào/ra Base64
        results = results[:limit]
        for item in results:
            plate = item["bien_so_xe"]
            turns = []
            for h in history_by_plate.get(plate, [])[:10]:
                turn_data = {
                    "ma_luot_gui": h.get("ma_luot_gui") or "",
                    "loai_khach": h.get("loai_khach") or item["loai_khach"],
                    "ma_khu_vuc": h.get("ma_khu_vuc") or "",
                    "ten_khu_vuc": h.get("ten_khu_vuc") or h.get("ma_khu_vuc") or "",
                    "ma_vi_tri": h.get("ma_vi_tri") or "",
                    "ten_vi_tri": h.get("ten_vi_tri") or h.get("ma_vi_tri") or "",
                    "thoi_gian_vao": h.get("thoi_gian_vao") or "",
                    "thoi_gian_ra": h.get("thoi_gian_ra") or "",
                    "trang_thai": h.get("trang_thai") or "",
                    "tien_thu": float(h.get("tien_thu") or 0),
                    "phuong_thuc_thanh_toan": h.get("phuong_thuc_thanh_toan") or "",
                    "anh_vao": h.get("anh_vao") or "",
                    "anh_ra": h.get("anh_ra") or "",
                }
                if include_images:
                    turn_data["anh_vao_base64"] = self._load_image_base64(h.get("anh_vao"))
                    turn_data["anh_ra_base64"] = self._load_image_base64(h.get("anh_ra"))
                turns.append(turn_data)
            item["history"] = turns

        return {
            "total": len(results),
            "items": results,
        }


def register_gate_routes(
    router: Router,
    file_logger: FileLogger,
    state_store: StateStore,
    session_manager: Optional[SessionManager] = None,
    db_provider: Any = None,
    gates_in_dir: Optional[Path] = None,
    gates_out_dir: Optional[Path] = None,
    shift_state_file: Optional[Path] = None,
) -> GateHandler:
    """Đăng ký 7 action kiểm soát cổng vào/ra, doanh thu ca & tra cứu của Ny lên Router."""
    handler = GateHandler(
        file_logger=file_logger,
        state_store=state_store,
        session_manager=session_manager,
        db_provider=db_provider,
        gates_in_dir=gates_in_dir,
        gates_out_dir=gates_out_dir,
        shift_state_file=shift_state_file,
    )
    staff_roles = (Roles.NHAN_VIEN.value, Roles.QUAN_LY.value)

    router.register(Actions.GATE_IN_CHECK, handler.handle_gate_in_check, allowed_roles=staff_roles, is_mutation=False)
    router.register(Actions.GATE_IN_CONFIRM, handler.handle_gate_in_confirm, allowed_roles=staff_roles, is_mutation=True)
    router.register(Actions.GATE_OUT_CHECK, handler.handle_gate_out_check, allowed_roles=staff_roles, is_mutation=False)
    router.register(Actions.GATE_OUT_CONFIRM, handler.handle_gate_out_confirm, allowed_roles=staff_roles, is_mutation=True)
    router.register(Actions.GATE_LOOKUP_VEHICLE, handler.handle_gate_lookup_vehicle, allowed_roles=staff_roles, is_mutation=False)
    router.register(Actions.SHIFT_GET_REVENUE, handler.handle_shift_get_revenue, allowed_roles=staff_roles, is_mutation=False)
    router.register(Actions.SHIFT_CLOSE, handler.handle_shift_close, allowed_roles=staff_roles, is_mutation=True)

    return handler
