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
