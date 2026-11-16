"""Handler Quản lý Phương tiện, Duyệt đăng ký/biến động & Phân bổ/Thu hồi Chỗ đỗ (Thành viên 4: Long).

Bao gồm toàn bộ 23 chức năng lá:
- 3.2.1 – 3.2.5: Quản lý phương tiện cư dân & upload ảnh cà-vẹt Base64 (Workflow 02)
- 1.4.1 – 1.4.4: Quản lý duyệt yêu cầu phương tiện & gán vị trí/khu vực đỗ (Workflow 05)
- 3.3.1 – 3.3.5: Cư dân mua thêm chỗ đỗ ô tô & xem vị trí được cấp
- 1.4.15 – 1.4.19: Quản lý duyệt mua thêm chỗ & thu hồi chỗ đỗ đã cấp
- 3.4.1 – 3.4.4: Tra cứu & định vị xe của cư dân
"""

import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from server.config import DEFAULT_CONFIG, ensure_storage_dirs
from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession
from shared.constants import (
    PricingGroups,
    PricingStatus,
    RequestStatus,
    Roles,
    SlotAllocationStatus,
    SlotAllocationTypes,
    SlotStatus,
    VehicleRequestTypes,
    VehicleStatus,
    VehicleTypes,
    ZoneStatus,
)
from shared.protocol import Actions
from shared.validators import save_base64_image, validate_license_plate


def _resolve_db(db_provider: Any = None) -> Any:
    if db_provider is not None:
        return db_provider
    from server import db as default_db

    return default_db


class VehicleHandler:
    """Xử lý vòng đời phương tiện cư dân, mua thêm chỗ ô tô và tra cứu định vị xe."""

    def __init__(
        self,
        file_logger: FileLogger,
        db_provider: Any = None,
        cavet_dir: Optional[Path] = None,
    ) -> None:
        self.file_logger = file_logger
        self._db_provider = db_provider
        cfg = ensure_storage_dirs(DEFAULT_CONFIG)
        self.cavet_dir = Path(cavet_dir) if cavet_dir is not None else cfg.cavet_dir
        self.cavet_dir.mkdir(parents=True, exist_ok=True)

    def _db(self) -> Any:
        return _resolve_db(self._db_provider)

    # --------------------------------------------------------------------------
    # HELPERS PHÂN GIẢI CƯ DÂN / NHÂN VIÊN & HẠN MỨC CĂN HỘ
    # --------------------------------------------------------------------------
    def _resolve_resident_context(
        self, db_or_tx: Any, session: ClientSession
    ) -> Tuple[int, str, Dict[str, Any]]:
        """Lấy (ma_cu_dan, ma_can_ho, apartment_row) của cư dân đang đăng nhập."""
        if session.vai_tro != Roles.CU_DAN.value:
            raise PermissionError("Chỉ tài khoản Cư dân mới có quyền thực hiện thao tác này.")

        resident = None
        if session.ma_cu_dan is not None:
            resident = db_or_tx.fetch_one(
                "SELECT ma_cu_dan, ma_can_ho, ho_ten FROM cu_dan WHERE ma_cu_dan = %s",
                (session.ma_cu_dan,),
            )
        if resident is None and session.ma_tai_khoan is not None:
            resident = db_or_tx.fetch_one(
                "SELECT ma_cu_dan, ma_can_ho, ho_ten FROM cu_dan WHERE ma_tai_khoan = %s",
                (session.ma_tai_khoan,),
            )
        if not resident:
            raise BusinessError("Không tìm thấy hồ sơ cư dân gắn với tài khoản hiện tại.")

        ma_cu_dan = int(resident["ma_cu_dan"])
        ma_can_ho = str(resident["ma_can_ho"])
        apartment = db_or_tx.fetch_one(
            """
            SELECT ma_can_ho, toa_nha, so_tang,
                   so_cho_oto_mac_dinh, so_cho_oto_mua_them, trang_thai_can_ho
            FROM can_ho
            WHERE ma_can_ho = %s
            """,
            (ma_can_ho,),
        )
        if not apartment:
            raise BusinessError(f"Không tìm thấy thông tin căn hộ '{ma_can_ho}'.")
        return ma_cu_dan, ma_can_ho, apartment

    def _resolve_staff_id(self, db_or_tx: Any, session: ClientSession) -> int:
        """Lấy `ma_nhan_vien` từ phiên đăng nhập Quản lý."""
        if session.ma_nhan_vien is not None:
            return int(session.ma_nhan_vien)
        if session.ma_tai_khoan is not None:
            row = db_or_tx.fetch_one(
                "SELECT ma_nhan_vien FROM nhan_vien WHERE ma_tai_khoan = %s",
                (session.ma_tai_khoan,),
            )
            if row:
                return int(row["ma_nhan_vien"])
        raise BusinessError("Không tìm thấy hồ sơ nhân viên/quản lý duyệt yêu cầu.")

    def _get_apartment_quota_stats(
        self, db_or_tx: Any, ma_can_ho: str, apartment: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Thống kê hạn mức chỗ đỗ ô tô và số xe đang hoạt động của cả căn hộ."""
        counts = db_or_tx.fetch_one(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE pt.ma_loai_xe = %s AND pt.trang_thai_xe = %s
                ) AS so_oto_hoat_dong,
                COUNT(*) FILTER (
                    WHERE pt.ma_loai_xe = %s AND pt.trang_thai_xe = %s
                ) AS so_xe_may_hoat_dong
            FROM phuong_tien pt
            JOIN cu_dan cd ON cd.ma_cu_dan = pt.ma_cu_dan
            WHERE cd.ma_can_ho = %s
            """,
            (
                VehicleTypes.O_TO.value,
                VehicleStatus.DANG_HOAT_DONG.value,
                VehicleTypes.XE_MAY.value,
                VehicleStatus.DANG_HOAT_DONG.value,
                ma_can_ho,
            ),
        ) or {}

        pending_row = db_or_tx.fetch_one(
            """
            SELECT COUNT(*) AS so_oto_cho_duyet
            FROM yeu_cau_dang_ky_xe yc
            JOIN phuong_tien pt ON pt.ma_phuong_tien = yc.ma_phuong_tien
            JOIN cu_dan cd ON cd.ma_cu_dan = yc.ma_cu_dan
            WHERE cd.ma_can_ho = %s
              AND yc.loai_yeu_cau = %s
              AND yc.trang_thai_duyet = %s
              AND pt.ma_loai_xe = %s
            """,
            (
                ma_can_ho,
                VehicleRequestTypes.DANG_KY_MOI.value,
                RequestStatus.CHO_DUYET.value,
                VehicleTypes.O_TO.value,
            ),
        ) or {}

        unassigned_allocs = db_or_tx.fetch_all(
            """
            SELECT pb.ma_phan_bo, pb.ma_vi_tri, pb.loai_cho_cap,
                   vt.ma_khu_vuc, vt.ten_vi_tri, kv.ten_khu_vuc
            FROM phan_bo_cho_do pb
            JOIN vi_tri_do vt ON vt.ma_vi_tri = pb.ma_vi_tri
            JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
            LEFT JOIN quan_ly_phuong_tien_ra_vao ql
                   ON ql.ma_vi_tri_co_dinh = pb.ma_vi_tri
            WHERE pb.ma_can_ho = %s
              AND pb.trang_thai_su_dung = %s
              AND ql.ma_quan_ly_xe IS NULL
            ORDER BY pb.ma_phan_bo ASC
            """,
            (ma_can_ho, SlotAllocationStatus.DANG_HIEU_LUC.value),
        )

        default_oto = int(apartment.get("so_cho_oto_mac_dinh") or 0)
        extra_oto = int(apartment.get("so_cho_oto_mua_them") or 0)
        total_oto = default_oto + extra_oto
        active_oto = int(counts.get("so_oto_hoat_dong") or 0)
        pending_oto = int(pending_row.get("so_oto_cho_duyet") or 0)
        active_moto = int(counts.get("so_xe_may_hoat_dong") or 0)

        return {
            "ma_can_ho": ma_can_ho,
            "toa_nha": apartment.get("toa_nha"),
            "so_tang": apartment.get("so_tang"),
            "so_cho_oto_mac_dinh": default_oto,
            "so_cho_oto_mua_them": extra_oto,
            "tong_suat_oto": total_oto,
            "so_oto_dang_hoat_dong": active_oto,
            "so_oto_cho_duyet": pending_oto,
            "so_suat_oto_con_lai": max(0, total_oto - active_oto),
            "so_xe_may_dang_hoat_dong": active_moto,
            "unassigned_allocations": unassigned_allocs,
        }

    # ==========================================================================
    # 3.2.2 & 3.2.3: CƯ DÂN XEM DANH SÁCH XE & TRẠNG THÁI YÊU CẦU ĐĂNG KÝ XE
    # ==========================================================================
    def handle_resident_list(
        self, session: ClientSession, _payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.2.2 & 3.2.3: Trả danh sách phương tiện, hạn mức căn hộ & lịch sử yêu cầu của cư dân."""
        db = self._db()
        ma_cu_dan, ma_can_ho, apartment = self._resolve_resident_context(db, session)
        quota = self._get_apartment_quota_stats(db, ma_can_ho, apartment)

        vehicles = db.fetch_all(
            """
            SELECT pt.ma_phuong_tien,
                   pt.ma_cu_dan,
                   pt.ma_loai_xe,
                   lpt.ten_loai_xe,
                   pt.bien_so_xe,
                   pt.ten_hang_dong_xe,
                   pt.mau_xe,
                   pt.duong_dan_cavet,
                   pt.trang_thai_xe,
                   ql.ma_vi_tri_co_dinh,
                   vt.ten_vi_tri,
                   vt.trang_thai_vi_tri,
                   ql.ma_khu_vuc_phan_cong,
                   kv.ten_khu_vuc,
                   ql.dang_trong_bai,
                   ql.thoi_gian_vao,
                   ql.thoi_gian_ra,
                   pb.loai_cho_cap,
                   EXISTS (
                       SELECT 1
                       FROM yeu_cau_dang_ky_xe yc_p
                       WHERE yc_p.ma_phuong_tien = pt.ma_phuong_tien
                         AND yc_p.trang_thai_duyet = %s
                   ) AS co_yeu_cau_cho_duyet
            FROM phuong_tien pt
            JOIN loai_phuong_tien lpt ON lpt.ma_loai_xe = pt.ma_loai_xe
            LEFT JOIN quan_ly_phuong_tien_ra_vao ql ON ql.ma_phuong_tien = pt.ma_phuong_tien
            LEFT JOIN vi_tri_do vt ON vt.ma_vi_tri = ql.ma_vi_tri_co_dinh
            LEFT JOIN khu_vuc kv ON kv.ma_khu_vuc = ql.ma_khu_vuc_phan_cong
            LEFT JOIN phan_bo_cho_do pb
                   ON pb.ma_vi_tri = ql.ma_vi_tri_co_dinh
                  AND pb.ma_can_ho = %s
                  AND pb.trang_thai_su_dung = %s
            WHERE pt.ma_cu_dan = %s
            ORDER BY
                CASE pt.trang_thai_xe
                    WHEN 'dang_hoat_dong' THEN 0
                    WHEN 'cho_duyet' THEN 1
                    ELSE 2
                END,
                pt.ma_phuong_tien DESC
            """,
            (
                RequestStatus.CHO_DUYET.value,
                ma_can_ho,
                SlotAllocationStatus.DANG_HIEU_LUC.value,
                ma_cu_dan,
            ),
        )

        requests = db.fetch_all(
            """
            SELECT yc.ma_yeu_cau_xe,
                   yc.ma_cu_dan,
                   yc.ma_phuong_tien,
                   yc.loai_yeu_cau,
                   yc.vi_tri_mong_muon,
                   yc.noi_dung_thay_doi,
                   yc.trang_thai_duyet,
                   yc.ma_nhan_vien_duyet,
                   nv.ho_ten AS ten_quan_ly_duyet,
                   yc.ly_do_phan_hoi,
                   yc.ngay_gui_yeu_cau,
                   pt.bien_so_xe,
                   pt.ma_loai_xe,
                   lpt.ten_loai_xe,
                   pt.ten_hang_dong_xe,
                   pt.mau_xe,
                   pt.duong_dan_cavet,
                   pt.trang_thai_xe
            FROM yeu_cau_dang_ky_xe yc
            JOIN phuong_tien pt ON pt.ma_phuong_tien = yc.ma_phuong_tien
            JOIN loai_phuong_tien lpt ON lpt.ma_loai_xe = pt.ma_loai_xe
            LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = yc.ma_nhan_vien_duyet
            WHERE yc.ma_cu_dan = %s
            ORDER BY yc.ngay_gui_yeu_cau DESC, yc.ma_yeu_cau_xe DESC
            """,
            (ma_cu_dan,),
        )

        return {
            "quota": quota,
            "vehicles": vehicles,
            "requests": requests,
        }

    # --------------------------------------------------------------------------
    # HELPERS LƯU & ĐỌC ẢNH CÀ-VẸT BASE64
    # --------------------------------------------------------------------------
    def _save_cavet_base64(self, b64_str: str, prefix: str) -> str:
        """Giải mã Base64, kiểm tra <= 5MB và lưu vào thư mục storage/uploads/cavet/."""
        saved_path = Path(
            save_base64_image(
                b64_str=b64_str,
                dest_dir=self.cavet_dir,
                prefix=prefix,
                max_bytes=5 * 1024 * 1024,
            )
        )
        try:
            rel = saved_path.resolve().relative_to(DEFAULT_CONFIG.base_dir.resolve())
            return rel.as_posix()
        except ValueError:
            return str(saved_path)

    def _resolve_cavet_path(self, raw_path: str) -> Path:
        """Phân giải đường dẫn ảnh cà-vẹt an toàn (hỗ trợ cả path tương đối và tuyệt đối)."""
        candidate = Path(raw_path)
        if candidate.is_file():
            return candidate
        under_base = DEFAULT_CONFIG.base_dir / raw_path
        if under_base.is_file():
            return under_base
        under_cavet = self.cavet_dir / candidate.name
        if under_cavet.is_file():
            return under_cavet
        raise BusinessError(f"Không tìm thấy tệp ảnh cà-vẹt: '{raw_path}'.")

    # ==========================================================================
    # 3.2.1 (Workflow 02): CƯ DÂN ĐĂNG KÝ XE MỚI + UPLOAD ẢNH CÀ-VẸT BASE64
    # ==========================================================================
    def handle_submit_request(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.2.1 / 3.2.4 / 3.2.5: Cư dân gửi yêu cầu đăng ký mới, sửa hoặc ngừng đăng ký xe."""
        loai_yeu_cau = str(
            payload.get("loai_yeu_cau") or VehicleRequestTypes.DANG_KY_MOI.value
        ).strip()
        if loai_yeu_cau == VehicleRequestTypes.DANG_KY_MOI.value:
            return self._submit_new_vehicle_request(session, payload)
        if loai_yeu_cau == VehicleRequestTypes.CHINH_SUA_THONG_TIN.value:
            return self._submit_edit_vehicle_request(session, payload)
        if loai_yeu_cau == VehicleRequestTypes.NGUNG_DANG_KY.value:
            return self._submit_stop_vehicle_request(session, payload)
        raise BusinessError(f"Loại yêu cầu '{loai_yeu_cau}' không hợp lệ.")

    def _verify_resident_owned_vehicle(
        self, tx: Any, ma_cu_dan: int, raw_vehicle_id: Any
    ) -> Dict[str, Any]:
        """Xác thực phương tiện thuộc cư dân, đang hoạt động và chưa có yêu cầu chờ duyệt."""
        try:
            ma_phuong_tien = int(raw_vehicle_id)
        except (TypeError, ValueError) as exc:
            raise BusinessError("Thiếu hoặc sai định dạng `ma_phuong_tien`.") from exc

        vehicle = tx.fetch_one(
            """
            SELECT pt.*, ql.dang_trong_bai, ql.ma_vi_tri_co_dinh, ql.ma_khu_vuc_phan_cong
            FROM phuong_tien pt
            LEFT JOIN quan_ly_phuong_tien_ra_vao ql ON ql.ma_phuong_tien = pt.ma_phuong_tien
            WHERE pt.ma_phuong_tien = %s
            FOR UPDATE OF pt
            """,
            (ma_phuong_tien,),
        )
        if not vehicle:
            raise BusinessError(f"Không tìm thấy phương tiện #{ma_phuong_tien}.")
        if int(vehicle["ma_cu_dan"]) != ma_cu_dan:
            raise PermissionError("Bạn không có quyền thao tác trên phương tiện của cư dân khác.")
        if vehicle["trang_thai_xe"] != VehicleStatus.DANG_HOAT_DONG.value:
            raise BusinessError("Chỉ có thể gửi yêu cầu cho phương tiện đang hoạt động.")

        pending = tx.fetch_one(
            """
            SELECT ma_yeu_cau_xe
            FROM yeu_cau_dang_ky_xe
            WHERE ma_phuong_tien = %s AND trang_thai_duyet = %s
            """,
            (ma_phuong_tien, RequestStatus.CHO_DUYET.value),
        )
        if pending:
            raise BusinessError("Phương tiện này đang có một yêu cầu chờ duyệt, không thể gửi thêm.")
        return vehicle

    def _submit_edit_vehicle_request(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.2.4: Gửi yêu cầu chỉnh sửa thông tin xe (biển số, tên xe, màu xe, cà-vẹt mới)."""
        db = self._db()
        with db.transaction() as tx:
            ma_cu_dan, _, _ = self._resolve_resident_context(tx, session)
            vehicle = self._verify_resident_owned_vehicle(
                tx, ma_cu_dan, payload.get("ma_phuong_tien")
            )

            ma_loai_xe = str(vehicle["ma_loai_xe"])
            raw_new_plate = str(
                payload.get("bien_so_xe") or payload.get("bien_so") or vehicle["bien_so_xe"]
            ).strip()
            ok_plate, plate_or_err = validate_license_plate(raw_new_plate, ma_loai_xe)
            if not ok_plate:
                raise BusinessError(plate_or_err)
            new_plate = plate_or_err

            if new_plate != vehicle["bien_so_xe"]:
                dup = tx.fetch_one(
                    "SELECT ma_phuong_tien FROM phuong_tien WHERE bien_so_xe = %s AND ma_phuong_tien <> %s",
                    (new_plate, vehicle["ma_phuong_tien"]),
                )
                if dup:
                    raise BusinessError(f"Biển số mới '{new_plate}' đã tồn tại trên hệ thống.")

            new_ten_xe = str(
                payload.get("ten_hang_dong_xe") or payload.get("ten_xe") or vehicle["ten_hang_dong_xe"]
            ).strip()
            if not new_ten_xe:
                raise BusinessError("Tên hãng / dòng xe không được để trống.")
            new_mau_xe = str(
                payload.get("mau_xe") if payload.get("mau_xe") is not None else (vehicle.get("mau_xe") or "")
            ).strip()

            changes: Dict[str, Any] = {
                "bien_so_xe": new_plate,
                "ten_hang_dong_xe": new_ten_xe,
                "mau_xe": new_mau_xe,
            }

            cavet_b64 = str(
                payload.get("cavet_image_base64") or payload.get("cavet_base64") or ""
            ).strip()
            if cavet_b64:
                changes["duong_dan_cavet"] = self._save_cavet_base64(
                    cavet_b64, prefix=f"cavet_edit_{ma_cu_dan}_{new_plate}"
                )

            note = str(payload.get("ghi_chu") or payload.get("noi_dung_thay_doi") or "").strip()
            if note:
                changes["ghi_chu"] = note[:160]

            changes_json = json.dumps(changes, ensure_ascii=False)
            if len(changes_json) > 500:
                changes.pop("ghi_chu", None)
                changes_json = json.dumps(changes, ensure_ascii=False)[:500]

            req_row = tx.execute_returning(
                """
                INSERT INTO yeu_cau_dang_ky_xe (
                    ma_cu_dan, ma_phuong_tien, loai_yeu_cau,
                    noi_dung_thay_doi, trang_thai_duyet
                ) VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    ma_cu_dan,
                    vehicle["ma_phuong_tien"],
                    VehicleRequestTypes.CHINH_SUA_THONG_TIN.value,
                    changes_json,
                    RequestStatus.CHO_DUYET.value,
                ),
            )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="GUI_YEU_CAU_SUA_XE",
            doi_tuong="yeu_cau_dang_ky_xe",
            ma_doi_tuong=req_row["ma_yeu_cau_xe"] if req_row else None,
            gia_tri_cu={"bien_so_xe": vehicle["bien_so_xe"], "ten_hang_dong_xe": vehicle["ten_hang_dong_xe"]},
            gia_tri_moi=changes,
        )
        return {"request": req_row, "vehicle": vehicle}

    def _submit_stop_vehicle_request(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.2.5: Gửi yêu cầu ngừng đăng ký xe (chặn nếu xe đang đỗ trong bãi)."""
        reason = str(payload.get("ly_do") or payload.get("noi_dung_thay_doi") or "").strip()
        if not reason:
            reason = "Cư dân yêu cầu ngừng đăng ký sử dụng bãi giữ xe."

        db = self._db()
        with db.transaction() as tx:
            ma_cu_dan, _, _ = self._resolve_resident_context(tx, session)
            vehicle = self._verify_resident_owned_vehicle(
                tx, ma_cu_dan, payload.get("ma_phuong_tien")
            )
            if bool(vehicle.get("dang_trong_bai")):
                raise BusinessError(
                    f"Xe '{vehicle['bien_so_xe']}' đang đỗ trong bãi. "
                    "Vui lòng đưa xe ra khỏi bãi trước khi gửi yêu cầu ngừng đăng ký."
                )

            req_row = tx.execute_returning(
                """
                INSERT INTO yeu_cau_dang_ky_xe (
                    ma_cu_dan, ma_phuong_tien, loai_yeu_cau,
                    noi_dung_thay_doi, trang_thai_duyet
                ) VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    ma_cu_dan,
                    vehicle["ma_phuong_tien"],
                    VehicleRequestTypes.NGUNG_DANG_KY.value,
                    reason[:500],
                    RequestStatus.CHO_DUYET.value,
                ),
            )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="GUI_YEU_CAU_NGUNG_XE",
            doi_tuong="yeu_cau_dang_ky_xe",
            ma_doi_tuong=req_row["ma_yeu_cau_xe"] if req_row else None,
            gia_tri_cu={"bien_so_xe": vehicle["bien_so_xe"], "trang_thai_xe": vehicle["trang_thai_xe"]},
            gia_tri_moi={"ly_do": reason[:500]},
        )
        return {"request": req_row, "vehicle": vehicle}

    def _submit_new_vehicle_request(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        ma_loai_xe = str(payload.get("ma_loai_xe") or payload.get("loai_xe") or "").strip().upper()
        if ma_loai_xe not in (VehicleTypes.O_TO.value, VehicleTypes.XE_MAY.value):
            raise BusinessError("Loại xe phải là `O_TO` hoặc `XE_MAY`.")

        raw_plate = str(payload.get("bien_so_xe") or payload.get("bien_so") or "").strip()
        ok_plate, plate_or_err = validate_license_plate(raw_plate, ma_loai_xe)
        if not ok_plate:
            raise BusinessError(plate_or_err)
        bien_so = plate_or_err

        ten_xe = str(payload.get("ten_hang_dong_xe") or payload.get("ten_xe") or "").strip()
        if not ten_xe:
            raise BusinessError("Vui lòng nhập tên hãng / dòng xe.")
        mau_xe = str(payload.get("mau_xe") or "").strip() or None
        vi_tri_mong_muon = str(payload.get("vi_tri_mong_muon") or payload.get("ghi_chu") or "").strip() or None

        cavet_b64 = str(
            payload.get("cavet_image_base64") or payload.get("cavet_base64") or ""
        ).strip()
        if not cavet_b64:
            raise BusinessError("Vui lòng đính kèm ảnh cà-vẹt xe (giấy đăng ký xe).")

        db = self._db()
        with db.transaction() as tx:
            ma_cu_dan, ma_can_ho, apartment = self._resolve_resident_context(tx, session)

            if ma_loai_xe == VehicleTypes.O_TO.value:
                quota = self._get_apartment_quota_stats(tx, ma_can_ho, apartment)
                if quota["so_oto_dang_hoat_dong"] + quota["so_oto_cho_duyet"] >= quota["tong_suat_oto"]:
                    raise BusinessError(
                        f"Căn hộ {ma_can_ho} đã sử dụng hết hạn mức {quota['tong_suat_oto']} suất ô tô "
                        "(bao gồm xe đang hoạt động và hồ sơ chờ duyệt). "
                        "Vui lòng gửi Yêu cầu mua thêm chỗ đỗ ô tô (3.3.1) trước."
                    )

            existing = tx.fetch_one(
                "SELECT ma_phuong_tien, trang_thai_xe FROM phuong_tien WHERE bien_so_xe = %s FOR UPDATE",
                (bien_so,),
            )
            if existing and existing["trang_thai_xe"] in (
                VehicleStatus.DANG_HOAT_DONG.value,
                "cho_duyet",
            ):
                raise BusinessError(f"Biển số '{bien_so}' đã được đăng ký hoặc đang chờ duyệt.")

            duong_dan_cavet = self._save_cavet_base64(
                cavet_b64, prefix=f"cavet_{ma_cu_dan}_{bien_so}"
            )

            if existing:
                vehicle_row = tx.execute_returning(
                    """
                    UPDATE phuong_tien
                    SET ma_cu_dan = %s,
                        ma_loai_xe = %s,
                        ten_hang_dong_xe = %s,
                        mau_xe = %s,
                        duong_dan_cavet = %s,
                        trang_thai_xe = 'cho_duyet'
                    WHERE ma_phuong_tien = %s
                    RETURNING *
                    """,
                    (ma_cu_dan, ma_loai_xe, ten_xe, mau_xe, duong_dan_cavet, existing["ma_phuong_tien"]),
                )
            else:
                vehicle_row = tx.execute_returning(
                    """
                    INSERT INTO phuong_tien (
                        ma_cu_dan, ma_loai_xe, bien_so_xe, ten_hang_dong_xe,
                        mau_xe, duong_dan_cavet, trang_thai_xe
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'cho_duyet')
                    RETURNING *
                    """,
                    (ma_cu_dan, ma_loai_xe, bien_so, ten_xe, mau_xe, duong_dan_cavet),
                )

            req_row = tx.execute_returning(
                """
                INSERT INTO yeu_cau_dang_ky_xe (
                    ma_cu_dan, ma_phuong_tien, loai_yeu_cau,
                    vi_tri_mong_muon, trang_thai_duyet
                ) VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    ma_cu_dan,
                    vehicle_row["ma_phuong_tien"],
                    VehicleRequestTypes.DANG_KY_MOI.value,
                    vi_tri_mong_muon,
                    RequestStatus.CHO_DUYET.value,
                ),
            )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="GUI_YEU_CAU_DANG_KY_XE",
            doi_tuong="yeu_cau_dang_ky_xe",
            ma_doi_tuong=req_row["ma_yeu_cau_xe"] if req_row else None,
            gia_tri_cu=None,
            gia_tri_moi={
                "bien_so_xe": bien_so,
                "ma_loai_xe": ma_loai_xe,
                "duong_dan_cavet": duong_dan_cavet,
            },
        )
        return {"request": req_row, "vehicle": vehicle_row}

    def handle_get_cavet(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Tải ảnh cà-vẹt độ phân giải gốc (Base64) qua TCP Socket cho Quản lý/Admin/Chủ xe."""
        db = self._db()
        ma_phuong_tien = payload.get("ma_phuong_tien")
        raw_path = str(payload.get("duong_dan_cavet") or "").strip()

        vehicle = None
        if ma_phuong_tien is not None:
            try:
                vid = int(ma_phuong_tien)
            except (TypeError, ValueError) as exc:
                raise BusinessError("Mã phương tiện không hợp lệ.") from exc
            vehicle = db.fetch_one(
                "SELECT ma_phuong_tien, ma_cu_dan, bien_so_xe, duong_dan_cavet FROM phuong_tien WHERE ma_phuong_tien = %s",
                (vid,),
            )
            if not vehicle:
                raise BusinessError(f"Không tìm thấy phương tiện #{vid}.")
            if not raw_path:
                raw_path = str(vehicle.get("duong_dan_cavet") or "")
        elif raw_path:
            vehicle = db.fetch_one(
                "SELECT ma_phuong_tien, ma_cu_dan, bien_so_xe, duong_dan_cavet FROM phuong_tien WHERE duong_dan_cavet = %s",
                (raw_path,),
            )

        if session.vai_tro == Roles.CU_DAN.value:
            ma_cu_dan, _, _ = self._resolve_resident_context(db, session)
            if vehicle is None or int(vehicle["ma_cu_dan"]) != ma_cu_dan:
                raise PermissionError("Bạn chỉ được xem ảnh cà-vẹt của phương tiện thuộc tài khoản mình.")
        elif session.vai_tro not in (Roles.QUAN_LY.value, Roles.ADMIN.value):
            raise PermissionError("Không có quyền truy cập ảnh cà-vẹt.")

        if not raw_path:
            raise BusinessError("Phương tiện chưa có đường dẫn ảnh cà-vẹt.")

        img_path = self._resolve_cavet_path(raw_path)
        raw_bytes = img_path.read_bytes()
        ext = img_path.suffix.lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return {
            "ma_phuong_tien": vehicle["ma_phuong_tien"] if vehicle else None,
            "bien_so_xe": vehicle["bien_so_xe"] if vehicle else None,
            "filename": img_path.name,
            "mime_type": mime,
            "duong_dan_cavet": raw_path,
            "image_base64": base64.b64encode(raw_bytes).decode("ascii"),
        }

    # ==========================================================================
    # 1.4.1 & 1.4.4: QUẢN LÝ XEM DANH SÁCH YÊU CẦU PHƯƠNG TIỆN & LỊCH SỬ DUYỆT
    # ==========================================================================
    def handle_manager_list_requests(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.1 & 1.4.4: Lọc danh sách yêu cầu phương tiện chờ duyệt hoặc lịch sử duyệt."""
        trang_thai = str(payload.get("trang_thai_duyet") or "").strip()
        loai_yeu_cau = str(payload.get("loai_yeu_cau") or "").strip()
        ma_loai_xe = str(payload.get("ma_loai_xe") or "").strip().upper()
        keyword = str(payload.get("keyword") or payload.get("search_query") or "").strip()

        conditions = ["1=1"]
        params: List[Any] = []
        if trang_thai and trang_thai != "all":
            conditions.append("yc.trang_thai_duyet = %s")
            params.append(trang_thai)
        if loai_yeu_cau and loai_yeu_cau != "all":
            conditions.append("yc.loai_yeu_cau = %s")
            params.append(loai_yeu_cau)
        if ma_loai_xe and ma_loai_xe != "ALL":
            conditions.append("pt.ma_loai_xe = %s")
            params.append(ma_loai_xe)
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                "(pt.bien_so_xe ILIKE %s OR cd.ho_ten ILIKE %s OR cd.ma_can_ho ILIKE %s OR pt.ten_hang_dong_xe ILIKE %s)"
            )
            params.extend([like, like, like, like])

        where_sql = " AND ".join(conditions)
        db = self._db()
        rows = db.fetch_all(
            f"""
            SELECT yc.ma_yeu_cau_xe,
                   yc.ma_cu_dan,
                   yc.ma_phuong_tien,
                   yc.loai_yeu_cau,
                   yc.vi_tri_mong_muon,
                   yc.noi_dung_thay_doi,
                   yc.trang_thai_duyet,
                   yc.ma_nhan_vien_duyet,
                   nv.ho_ten AS ten_quan_ly_duyet,
                   yc.ly_do_phan_hoi,
                   yc.ngay_gui_yeu_cau,
                   cd.ho_ten AS ten_cu_dan,
                   cd.so_dien_thoai,
                   cd.ma_can_ho,
                   ch.toa_nha,
                   ch.so_tang,
                   ch.so_cho_oto_mac_dinh,
                   ch.so_cho_oto_mua_them,
                   pt.ma_loai_xe,
                   lpt.ten_loai_xe,
                   pt.bien_so_xe,
                   pt.ten_hang_dong_xe,
                   pt.mau_xe,
                   pt.duong_dan_cavet,
                   pt.trang_thai_xe,
                   ql.ma_vi_tri_co_dinh,
                   ql.ma_khu_vuc_phan_cong,
                   ql.dang_trong_bai
            FROM yeu_cau_dang_ky_xe yc
            JOIN cu_dan cd ON cd.ma_cu_dan = yc.ma_cu_dan
            JOIN can_ho ch ON ch.ma_can_ho = cd.ma_can_ho
            JOIN phuong_tien pt ON pt.ma_phuong_tien = yc.ma_phuong_tien
            JOIN loai_phuong_tien lpt ON lpt.ma_loai_xe = pt.ma_loai_xe
            LEFT JOIN quan_ly_phuong_tien_ra_vao ql ON ql.ma_phuong_tien = pt.ma_phuong_tien
            LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = yc.ma_nhan_vien_duyet
            WHERE {where_sql}
            ORDER BY
                CASE yc.trang_thai_duyet WHEN 'cho_duyet' THEN 0 ELSE 1 END,
                yc.ngay_gui_yeu_cau DESC,
                yc.ma_yeu_cau_xe DESC
            """,
            tuple(params),
        )

        # Bổ sung thống kê hạn mức hiện tại cho từng căn hộ xuất hiện trong danh sách
        quota_cache: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            apt_id = str(row["ma_can_ho"])
            if apt_id not in quota_cache:
                quota_cache[apt_id] = self._get_apartment_quota_stats(
                    db,
                    apt_id,
                    {
                        "ma_can_ho": apt_id,
                        "toa_nha": row.get("toa_nha"),
                        "so_tang": row.get("so_tang"),
                        "so_cho_oto_mac_dinh": row.get("so_cho_oto_mac_dinh"),
                        "so_cho_oto_mua_them": row.get("so_cho_oto_mua_them"),
                    },
                )
            q = quota_cache[apt_id]
            row["so_oto_dang_hoat_dong"] = q["so_oto_dang_hoat_dong"]
            row["tong_suat_oto"] = q["tong_suat_oto"]
            row["so_suat_oto_con_lai"] = q["so_suat_oto_con_lai"]
            row["unassigned_allocations"] = q["unassigned_allocations"]

        return {"total": len(rows), "items": rows}

    def handle_get_assignable(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Lấy sơ đồ ô đỗ ô tô hoặc khu vực xe máy khả dụng phục vụ `SlotPickerDialog`."""
        ma_loai_xe = str(payload.get("ma_loai_xe") or VehicleTypes.O_TO.value).strip().upper()
        ma_can_ho = str(payload.get("ma_can_ho") or "").strip()
        db = self._db()

        if ma_loai_xe == VehicleTypes.XE_MAY.value:
            zones = db.fetch_all(
                """
                SELECT ma_khu_vuc, ten_khu_vuc, ma_loai_xe,
                       tong_so_cho, so_xe_dang_gui,
                       GREATEST(0, tong_so_cho - so_xe_dang_gui) AS so_cho_trong,
                       trang_thai_khu_vuc
                FROM khu_vuc
                WHERE ma_loai_xe = %s AND trang_thai_khu_vuc = %s
                ORDER BY ma_khu_vuc ASC
                """,
                (VehicleTypes.XE_MAY.value, ZoneStatus.HOAT_DONG.value),
            )
            return {
                "ma_loai_xe": ma_loai_xe,
                "zones": zones,
                "slots": [],
                "preallocated_slots": [],
            }

        preallocated_codes: set[str] = set()
        preallocated_rows: List[Dict[str, Any]] = []
        if ma_can_ho:
            preallocated_rows = db.fetch_all(
                """
                SELECT pb.ma_phan_bo, pb.ma_vi_tri, pb.loai_cho_cap,
                       vt.ma_khu_vuc, vt.ten_vi_tri, kv.ten_khu_vuc
                FROM phan_bo_cho_do pb
                JOIN vi_tri_do vt ON vt.ma_vi_tri = pb.ma_vi_tri
                JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
                LEFT JOIN quan_ly_phuong_tien_ra_vao ql
                       ON ql.ma_vi_tri_co_dinh = pb.ma_vi_tri
                WHERE pb.ma_can_ho = %s
                  AND pb.trang_thai_su_dung = %s
                  AND ql.ma_quan_ly_xe IS NULL
                ORDER BY pb.ma_phan_bo ASC
                """,
                (ma_can_ho, SlotAllocationStatus.DANG_HIEU_LUC.value),
            )
            preallocated_codes = {str(r["ma_vi_tri"]) for r in preallocated_rows}

        zones = db.fetch_all(
            """
            SELECT ma_khu_vuc, ten_khu_vuc, ma_loai_xe,
                   tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc
            FROM khu_vuc
            WHERE ma_loai_xe = %s AND trang_thai_khu_vuc = %s
            ORDER BY ma_khu_vuc ASC
            """,
            (VehicleTypes.O_TO.value, ZoneStatus.HOAT_DONG.value),
        )
        slots = db.fetch_all(
            """
            SELECT vt.ma_vi_tri,
                   vt.ma_khu_vuc,
                   kv.ten_khu_vuc,
                   vt.ten_vi_tri,
                   vt.nhom_vi_tri,
                   vt.trang_thai_vi_tri,
                   vt.ly_do_ngung,
                   pb.ma_can_ho AS can_ho_dang_giu,
                   pt.bien_so_xe
            FROM vi_tri_do vt
            JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
            LEFT JOIN phan_bo_cho_do pb
                   ON pb.ma_vi_tri = vt.ma_vi_tri
                  AND pb.trang_thai_su_dung = %s
            LEFT JOIN quan_ly_phuong_tien_ra_vao ql
                   ON ql.ma_vi_tri_co_dinh = vt.ma_vi_tri
            LEFT JOIN phuong_tien pt
                   ON pt.ma_phuong_tien = ql.ma_phuong_tien
            WHERE kv.ma_loai_xe = %s AND kv.trang_thai_khu_vuc = %s
            ORDER BY vt.ma_khu_vuc ASC, vt.ma_vi_tri ASC
            """,
            (
                SlotAllocationStatus.DANG_HIEU_LUC.value,
                VehicleTypes.O_TO.value,
                ZoneStatus.HOAT_DONG.value,
            ),
        )
        for s in slots:
            code = str(s["ma_vi_tri"])
            is_pre = code in preallocated_codes
            is_free = s.get("trang_thai_vi_tri") == SlotStatus.TRONG.value
            is_visitor_only = s.get("nhom_vi_tri") == "vi_tri_vang_lai"
            s["is_apartment_preallocated"] = is_pre
            s["assignable"] = bool(is_pre or (is_free and not is_visitor_only))

        return {
            "ma_loai_xe": ma_loai_xe,
            "zones": zones,
            "slots": slots,
            "preallocated_slots": preallocated_rows,
        }

    # ==========================================================================
    # 1.4.2 (Workflow 05): QUẢN LÝ DUYỆT YÊU CẦU PHƯƠNG TIỆN & PHÂN BỔ Ô ĐỖ ACID
    # ==========================================================================
    def _upsert_vehicle_tracking(
        self,
        tx: Any,
        ma_phuong_tien: int,
        ma_vi_tri_co_dinh: Optional[str],
        ma_khu_vuc_phan_cong: Optional[str],
    ) -> None:
        existing = tx.fetch_one(
            "SELECT ma_quan_ly_xe FROM quan_ly_phuong_tien_ra_vao WHERE ma_phuong_tien = %s FOR UPDATE",
            (ma_phuong_tien,),
        )
        if existing:
            tx.execute(
                """
                UPDATE quan_ly_phuong_tien_ra_vao
                SET ma_vi_tri_co_dinh = %s,
                    ma_khu_vuc_phan_cong = %s,
                    dang_trong_bai = FALSE
                WHERE ma_phuong_tien = %s
                """,
                (ma_vi_tri_co_dinh, ma_khu_vuc_phan_cong, ma_phuong_tien),
            )
        else:
            tx.execute(
                """
                INSERT INTO quan_ly_phuong_tien_ra_vao (
                    ma_phuong_tien, ma_vi_tri_co_dinh, ma_khu_vuc_phan_cong, dang_trong_bai
                ) VALUES (%s, %s, %s, FALSE)
                """,
                (ma_phuong_tien, ma_vi_tri_co_dinh, ma_khu_vuc_phan_cong),
            )

    def handle_approve_request(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.2: Duyệt yêu cầu phương tiện (đăng ký mới / sửa thông tin / ngừng đăng ký) trong 1 TX ACID."""
        try:
            ma_yeu_cau_xe = int(payload.get("ma_yeu_cau_xe") or payload.get("ma_yeu_cau_dang_ky"))
        except (TypeError, ValueError) as exc:
            raise BusinessError("Thiếu hoặc sai định dạng `ma_yeu_cau_xe`.") from exc

        ma_vi_tri = str(payload.get("ma_vi_tri_co_dinh") or payload.get("ma_vi_tri") or "").strip().upper() or None
        ma_khu_vuc = str(payload.get("ma_khu_vuc_phan_cong") or payload.get("ma_khu_vuc") or "").strip().upper() or None
        ly_do_phan_hoi = str(payload.get("ly_do_phan_hoi") or "Đã phê duyệt").strip()

        db = self._db()
        with db.transaction() as tx:
            staff_id = self._resolve_staff_id(tx, session)
            req = tx.fetch_one(
                """
                SELECT yc.*, cd.ma_can_ho,
                       pt.ma_loai_xe, pt.bien_so_xe, pt.ten_hang_dong_xe,
                       pt.mau_xe, pt.duong_dan_cavet, pt.trang_thai_xe
                FROM yeu_cau_dang_ky_xe yc
                JOIN cu_dan cd ON cd.ma_cu_dan = yc.ma_cu_dan
                JOIN phuong_tien pt ON pt.ma_phuong_tien = yc.ma_phuong_tien
                WHERE yc.ma_yeu_cau_xe = %s
                FOR UPDATE OF yc
                """,
                (ma_yeu_cau_xe,),
            )
            if not req:
                raise BusinessError(f"Không tìm thấy yêu cầu phương tiện #{ma_yeu_cau_xe}.")
            if req["trang_thai_duyet"] != RequestStatus.CHO_DUYET.value:
                raise BusinessError("Yêu cầu này đã được xử lý trước đó.")

            loai_yeu_cau = str(req["loai_yeu_cau"])
            ma_phuong_tien = int(req["ma_phuong_tien"])
            ma_can_ho = str(req["ma_can_ho"])
            ma_loai_xe = str(req["ma_loai_xe"])

            assigned_slot_code: Optional[str] = None
            assigned_zone_code: Optional[str] = None

            if loai_yeu_cau == VehicleRequestTypes.DANG_KY_MOI.value:
                dup = tx.fetch_one(
                    """
                    SELECT ma_phuong_tien
                    FROM phuong_tien
                    WHERE bien_so_xe = %s AND trang_thai_xe = %s AND ma_phuong_tien <> %s
                    """,
                    (req["bien_so_xe"], VehicleStatus.DANG_HOAT_DONG.value, ma_phuong_tien),
                )
                if dup:
                    raise BusinessError(f"Biển số '{req['bien_so_xe']}' đang hoạt động ở hồ sơ khác.")

                if ma_loai_xe == VehicleTypes.O_TO.value:
                    if not ma_vi_tri:
                        raise BusinessError("Vui lòng chọn ô đỗ ô tô cố định trên sơ đồ trước khi duyệt.")

                    apartment = tx.fetch_one(
                        "SELECT * FROM can_ho WHERE ma_can_ho = %s FOR UPDATE",
                        (ma_can_ho,),
                    )
                    quota = self._get_apartment_quota_stats(tx, ma_can_ho, apartment)
                    if quota["so_oto_dang_hoat_dong"] >= quota["tong_suat_oto"]:
                        raise BusinessError(
                            f"Căn hộ {ma_can_ho} đã hết hạn mức chỗ đỗ ô tô "
                            f"({quota['so_oto_dang_hoat_dong']}/{quota['tong_suat_oto']}). "
                            "Vui lòng duyệt yêu cầu mua thêm chỗ đỗ trước."
                        )

                    slot_row = tx.fetch_one(
                        """
                        SELECT vt.*, kv.ma_loai_xe, kv.trang_thai_khu_vuc
                        FROM vi_tri_do vt
                        JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
                        WHERE vt.ma_vi_tri = %s
                        FOR UPDATE OF vt
                        """,
                        (ma_vi_tri,),
                    )
                    if not slot_row:
                        raise BusinessError(f"Không tìm thấy vị trí đỗ '{ma_vi_tri}'.")
                    if slot_row["ma_loai_xe"] != VehicleTypes.O_TO.value:
                        raise BusinessError(f"Vị trí '{ma_vi_tri}' không thuộc khu vực ô tô.")
                    if slot_row["trang_thai_khu_vuc"] != ZoneStatus.HOAT_DONG.value:
                        raise BusinessError(f"Khu vực của vị trí '{ma_vi_tri}' đang ngừng sử dụng.")
                    if slot_row["nhom_vi_tri"] == "vi_tri_vang_lai":
                        raise BusinessError(f"Vị trí '{ma_vi_tri}' dành cho khách vãng lai, không gán cố định.")

                    prealloc = tx.fetch_one(
                        """
                        SELECT pb.ma_phan_bo, pb.loai_cho_cap
                        FROM phan_bo_cho_do pb
                        LEFT JOIN quan_ly_phuong_tien_ra_vao ql
                               ON ql.ma_vi_tri_co_dinh = pb.ma_vi_tri
                        WHERE pb.ma_can_ho = %s
                          AND pb.ma_vi_tri = %s
                          AND pb.trang_thai_su_dung = %s
                          AND ql.ma_quan_ly_xe IS NULL
                        FOR UPDATE OF pb
                        """,
                        (ma_can_ho, ma_vi_tri, SlotAllocationStatus.DANG_HIEU_LUC.value),
                    )

                    if prealloc:
                        if slot_row["trang_thai_vi_tri"] == SlotStatus.NGUNG_SU_DUNG.value:
                            raise BusinessError(f"Vị trí '{ma_vi_tri}' đang ngừng sử dụng.")
                    else:
                        if slot_row["trang_thai_vi_tri"] != SlotStatus.TRONG.value:
                            raise BusinessError(
                                f"Vị trí '{ma_vi_tri}' hiện ở trạng thái '{slot_row['trang_thai_vi_tri']}', "
                                "không thể phân bổ mới."
                            )
                        has_default = tx.fetch_one(
                            """
                            SELECT ma_phan_bo
                            FROM phan_bo_cho_do
                            WHERE ma_can_ho = %s
                              AND loai_cho_cap = %s
                              AND trang_thai_su_dung = %s
                            """,
                            (
                                ma_can_ho,
                                SlotAllocationTypes.CHO_MAC_DINH_XE_1.value,
                                SlotAllocationStatus.DANG_HIEU_LUC.value,
                            ),
                        )
                        loai_cho_cap = (
                            SlotAllocationTypes.CHO_MUA_THEM.value
                            if has_default
                            else SlotAllocationTypes.CHO_MAC_DINH_XE_1.value
                        )
                        tx.execute(
                            """
                            INSERT INTO phan_bo_cho_do (
                                ma_can_ho, ma_vi_tri, loai_cho_cap, trang_thai_su_dung
                            ) VALUES (%s, %s, %s, %s)
                            """,
                            (
                                ma_can_ho,
                                ma_vi_tri,
                                loai_cho_cap,
                                SlotAllocationStatus.DANG_HIEU_LUC.value,
                            ),
                        )
                        tx.execute(
                            """
                            UPDATE vi_tri_do
                            SET trang_thai_vi_tri = %s, thoi_gian_cap_nhat = CURRENT_TIMESTAMP
                            WHERE ma_vi_tri = %s
                            """,
                            (SlotStatus.DA_PHAN_CHO.value, ma_vi_tri),
                        )

                    assigned_slot_code = ma_vi_tri
                    assigned_zone_code = str(slot_row["ma_khu_vuc"])
                    tx.execute(
                        "UPDATE phuong_tien SET trang_thai_xe = %s WHERE ma_phuong_tien = %s",
                        (VehicleStatus.DANG_HOAT_DONG.value, ma_phuong_tien),
                    )
                    self._upsert_vehicle_tracking(
                        tx,
                        ma_phuong_tien=ma_phuong_tien,
                        ma_vi_tri_co_dinh=assigned_slot_code,
                        ma_khu_vuc_phan_cong=assigned_zone_code,
                    )

                else:  # XE_MAY
                    if not ma_khu_vuc:
                        raise BusinessError("Vui lòng chọn khu vực đỗ xe máy trước khi duyệt.")
                    zone_row = tx.fetch_one(
                        "SELECT * FROM khu_vuc WHERE ma_khu_vuc = %s",
                        (ma_khu_vuc,),
                    )
                    if not zone_row:
                        raise BusinessError(f"Không tìm thấy khu vực '{ma_khu_vuc}'.")
                    if zone_row["ma_loai_xe"] != VehicleTypes.XE_MAY.value:
                        raise BusinessError(f"Khu vực '{ma_khu_vuc}' không dành cho xe máy.")
                    if zone_row["trang_thai_khu_vuc"] != ZoneStatus.HOAT_DONG.value:
                        raise BusinessError(f"Khu vực '{ma_khu_vuc}' đang ngừng sử dụng.")

                    assigned_zone_code = ma_khu_vuc
                    tx.execute(
                        "UPDATE phuong_tien SET trang_thai_xe = %s WHERE ma_phuong_tien = %s",
                        (VehicleStatus.DANG_HOAT_DONG.value, ma_phuong_tien),
                    )
                    self._upsert_vehicle_tracking(
                        tx,
                        ma_phuong_tien=ma_phuong_tien,
                        ma_vi_tri_co_dinh=None,
                        ma_khu_vuc_phan_cong=assigned_zone_code,
                    )

            elif loai_yeu_cau == VehicleRequestTypes.CHINH_SUA_THONG_TIN.value:
                raw_changes = str(req.get("noi_dung_thay_doi") or "{}").strip()
                try:
                    changes = json.loads(raw_changes) if raw_changes.startswith("{") else {}
                except json.JSONDecodeError:
                    changes = {}

                new_plate = str(changes.get("bien_so_xe") or req["bien_so_xe"]).strip()
                new_ten = str(changes.get("ten_hang_dong_xe") or req["ten_hang_dong_xe"]).strip()
                new_mau = changes.get("mau_xe") if "mau_xe" in changes else req.get("mau_xe")
                new_cavet = str(changes.get("duong_dan_cavet") or req["duong_dan_cavet"]).strip()

                if new_plate != req["bien_so_xe"]:
                    dup = tx.fetch_one(
                        "SELECT ma_phuong_tien FROM phuong_tien WHERE bien_so_xe = %s AND ma_phuong_tien <> %s",
                        (new_plate, ma_phuong_tien),
                    )
                    if dup:
                        raise BusinessError(f"Biển số mới '{new_plate}' đã bị sử dụng.")

                tx.execute(
                    """
                    UPDATE phuong_tien
                    SET bien_so_xe = %s,
                        ten_hang_dong_xe = %s,
                        mau_xe = %s,
                        duong_dan_cavet = %s
                    WHERE ma_phuong_tien = %s
                    """,
                    (new_plate, new_ten, new_mau, new_cavet, ma_phuong_tien),
                )

            elif loai_yeu_cau == VehicleRequestTypes.NGUNG_DANG_KY.value:
                tracking = tx.fetch_one(
                    "SELECT * FROM quan_ly_phuong_tien_ra_vao WHERE ma_phuong_tien = %s FOR UPDATE",
                    (ma_phuong_tien,),
                )
                if tracking and bool(tracking.get("dang_trong_bai")):
                    raise BusinessError(
                        f"Xe '{req['bien_so_xe']}' đang đỗ trong bãi, không thể duyệt ngừng đăng ký!"
                    )

                tx.execute(
                    "UPDATE phuong_tien SET trang_thai_xe = %s WHERE ma_phuong_tien = %s",
                    (VehicleStatus.NGUNG_HOAT_DONG.value, ma_phuong_tien),
                )
                old_slot = tracking.get("ma_vi_tri_co_dinh") if tracking else None
                if tracking:
                    tx.execute(
                        """
                        UPDATE quan_ly_phuong_tien_ra_vao
                        SET ma_vi_tri_co_dinh = NULL, ma_khu_vuc_phan_cong = NULL
                        WHERE ma_phuong_tien = %s
                        """,
                        (ma_phuong_tien,),
                    )
                if old_slot:
                    alloc = tx.fetch_one(
                        """
                        SELECT ma_phan_bo, loai_cho_cap
                        FROM phan_bo_cho_do
                        WHERE ma_can_ho = %s AND ma_vi_tri = %s AND trang_thai_su_dung = %s
                        FOR UPDATE
                        """,
                        (ma_can_ho, old_slot, SlotAllocationStatus.DANG_HIEU_LUC.value),
                    )
                    if alloc and alloc["loai_cho_cap"] == SlotAllocationTypes.CHO_MAC_DINH_XE_1.value:
                        tx.execute(
                            """
                            UPDATE phan_bo_cho_do
                            SET trang_thai_su_dung = %s, ly_do_thu_hoi = %s
                            WHERE ma_phan_bo = %s
                            """,
                            (
                                SlotAllocationStatus.DA_THU_HOI.value,
                                "Cư dân ngừng đăng ký xe mặc định",
                                alloc["ma_phan_bo"],
                            ),
                        )
                        tx.execute(
                            """
                            UPDATE vi_tri_do
                            SET trang_thai_vi_tri = %s, thoi_gian_cap_nhat = CURRENT_TIMESTAMP
                            WHERE ma_vi_tri = %s
                            """,
                            (SlotStatus.TRONG.value, old_slot),
                        )

            updated_req = tx.execute_returning(
                """
                UPDATE yeu_cau_dang_ky_xe
                SET trang_thai_duyet = %s,
                    ma_nhan_vien_duyet = %s,
                    ly_do_phan_hoi = %s
                WHERE ma_yeu_cau_xe = %s
                RETURNING *
                """,
                (RequestStatus.DA_DUYET.value, staff_id, ly_do_phan_hoi, ma_yeu_cau_xe),
            )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="DUYET_YEU_CAU_PHUONG_TIEN",
            doi_tuong="yeu_cau_dang_ky_xe",
            ma_doi_tuong=ma_yeu_cau_xe,
            gia_tri_cu={"trang_thai_duyet": RequestStatus.CHO_DUYET.value, "loai_yeu_cau": loai_yeu_cau},
            gia_tri_moi={
                "trang_thai_duyet": RequestStatus.DA_DUYET.value,
                "ma_vi_tri_co_dinh": assigned_slot_code,
                "ma_khu_vuc_phan_cong": assigned_zone_code,
            },
        )
        return {
            "request": updated_req,
            "ma_vi_tri_co_dinh": assigned_slot_code,
            "ma_khu_vuc_phan_cong": assigned_zone_code,
        }

    # ==========================================================================
    # 1.4.3: QUẢN LÝ TỪ CHỐI YÊU CẦU PHƯƠNG TIỆN KÈM LÝ DO
    # ==========================================================================
    def handle_reject_request(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.3: Từ chối yêu cầu đăng ký/chỉnh sửa/ngừng đăng ký xe kèm lý do bắt buộc."""
        try:
            ma_yeu_cau_xe = int(payload.get("ma_yeu_cau_xe") or payload.get("ma_yeu_cau_dang_ky"))
        except (TypeError, ValueError) as exc:
            raise BusinessError("Thiếu hoặc sai định dạng `ma_yeu_cau_xe`.") from exc

        ly_do = str(
            payload.get("ly_do_phan_hoi") or payload.get("ly_do_tu_choi") or ""
        ).strip()
        if not ly_do:
            raise BusinessError("Vui lòng nhập lý do từ chối yêu cầu phương tiện.")

        db = self._db()
        with db.transaction() as tx:
            staff_id = self._resolve_staff_id(tx, session)
            req = tx.fetch_one(
                "SELECT * FROM yeu_cau_dang_ky_xe WHERE ma_yeu_cau_xe = %s FOR UPDATE",
                (ma_yeu_cau_xe,),
            )
            if not req:
                raise BusinessError(f"Không tìm thấy yêu cầu phương tiện #{ma_yeu_cau_xe}.")
            if req["trang_thai_duyet"] != RequestStatus.CHO_DUYET.value:
                raise BusinessError("Yêu cầu này đã được xử lý trước đó.")

            if req["loai_yeu_cau"] == VehicleRequestTypes.DANG_KY_MOI.value:
                tx.execute(
                    "UPDATE phuong_tien SET trang_thai_xe = %s WHERE ma_phuong_tien = %s",
                    ("ngung_hieu_luc", req["ma_phuong_tien"]),
                )

            updated = tx.execute_returning(
                """
                UPDATE yeu_cau_dang_ky_xe
                SET trang_thai_duyet = %s,
                    ma_nhan_vien_duyet = %s,
                    ly_do_phan_hoi = %s
                WHERE ma_yeu_cau_xe = %s
                RETURNING *
                """,
                (RequestStatus.TU_CHOI.value, staff_id, ly_do[:255], ma_yeu_cau_xe),
            )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="TU_CHOI_YEU_CAU_PHUONG_TIEN",
            doi_tuong="yeu_cau_dang_ky_xe",
            ma_doi_tuong=ma_yeu_cau_xe,
            gia_tri_cu={"trang_thai_duyet": RequestStatus.CHO_DUYET.value},
            gia_tri_moi={"trang_thai_duyet": RequestStatus.TU_CHOI.value, "ly_do_phan_hoi": ly_do[:255]},
        )
        return {"request": updated}

    # ==========================================================================
    # 3.3.1 – 3.3.5: CƯ DÂN MUA THÊM CHỖ ĐỖ Ô TÔ & XEM VỊ TRÍ ĐƯỢC CẤP
    # ==========================================================================
    def handle_resident_extra_slots_data(
        self, session: ClientSession, _payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.3.2 – 3.3.5: Lấy hạn mức, danh sách chỗ đỗ ô tô được cấp & lịch sử yêu cầu mua thêm."""
        db = self._db()
        ma_cu_dan, ma_can_ho, apartment = self._resolve_resident_context(db, session)
        quota = self._get_apartment_quota_stats(db, ma_can_ho, apartment)

        price_rows = db.fetch_all(
            """
            SELECT nhom_ap_dung, muc_phi
            FROM bang_gia
            WHERE trang_thai_gia = %s
              AND nhom_ap_dung IN (%s, %s)
            ORDER BY ma_bang_gia DESC
            """,
            (
                PricingStatus.DANG_AP_DUNG.value,
                PricingGroups.CHO_OTO_MAC_DINH.value,
                PricingGroups.CHO_OTO_MUA_THEM.value,
            ),
        )
        pricing: Dict[str, float] = {
            PricingGroups.CHO_OTO_MAC_DINH.value: 0.0,
            PricingGroups.CHO_OTO_MUA_THEM.value: 0.0,
        }
        for pr in price_rows:
            nhom = str(pr["nhom_ap_dung"])
            if pricing.get(nhom, 0.0) == 0.0:
                pricing[nhom] = float(pr["muc_phi"] or 0.0)

        allocations = db.fetch_all(
            """
            SELECT pb.ma_phan_bo,
                   pb.ma_can_ho,
                   pb.ma_vi_tri,
                   pb.loai_cho_cap,
                   pb.ngay_cap,
                   pb.trang_thai_su_dung,
                   pb.ly_do_thu_hoi,
                   vt.ten_vi_tri,
                   vt.ma_khu_vuc,
                   vt.nhom_vi_tri,
                   vt.trang_thai_vi_tri,
                   kv.ten_khu_vuc,
                   ql.ma_phuong_tien,
                   ql.dang_trong_bai,
                   pt.bien_so_xe,
                   pt.ten_hang_dong_xe
            FROM phan_bo_cho_do pb
            JOIN vi_tri_do vt ON vt.ma_vi_tri = pb.ma_vi_tri
            JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
            LEFT JOIN quan_ly_phuong_tien_ra_vao ql
                   ON ql.ma_vi_tri_co_dinh = pb.ma_vi_tri
            LEFT JOIN phuong_tien pt
                   ON pt.ma_phuong_tien = ql.ma_phuong_tien
                  AND pt.trang_thai_xe = %s
            WHERE pb.ma_can_ho = %s
            ORDER BY
                CASE pb.trang_thai_su_dung WHEN 'dang_hieu_luc' THEN 0 ELSE 1 END,
                pb.ma_phan_bo DESC
            """,
            (VehicleStatus.DANG_HOAT_DONG.value, ma_can_ho),
        )
        for alloc in allocations:
            fee_key = (
                PricingGroups.CHO_OTO_MAC_DINH.value
                if alloc["loai_cho_cap"] == SlotAllocationTypes.CHO_MAC_DINH_XE_1.value
                else PricingGroups.CHO_OTO_MUA_THEM.value
            )
            alloc["muc_phi_thang"] = pricing.get(fee_key, 0.0)

        requests = db.fetch_all(
            """
            SELECT yc.ma_yeu_cau_cho,
                   yc.ma_cu_dan,
                   yc.ma_can_ho,
                   yc.nhu_cau_vi_tri,
                   yc.ly_do_mua_them,
                   yc.trang_thai_duyet,
                   yc.ma_vi_tri_duoc_duyet,
                   vt.ten_vi_tri,
                   kv.ten_khu_vuc,
                   yc.ma_nhan_vien_duyet,
                   nv.ho_ten AS ten_quan_ly_duyet,
                   yc.ly_do_tu_choi,
                   yc.ngay_gui_yeu_cau
            FROM yeu_cau_mua_them_cho yc
            LEFT JOIN vi_tri_do vt ON vt.ma_vi_tri = yc.ma_vi_tri_duoc_duyet
            LEFT JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
            LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = yc.ma_nhan_vien_duyet
            WHERE yc.ma_can_ho = %s
            ORDER BY yc.ngay_gui_yeu_cau DESC, yc.ma_yeu_cau_cho DESC
            """,
            (ma_can_ho,),
        )

        return {
            "ma_cu_dan": ma_cu_dan,
            "quota": quota,
            "pricing": pricing,
            "allocations": allocations,
            "requests": requests,
        }

    def handle_resident_submit_extra_slot(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.3.1: Cư dân gửi yêu cầu mua thêm chỗ đỗ ô tô cho căn hộ."""
        nhu_cau_vi_tri = str(
            payload.get("nhu_cau_vi_tri") or payload.get("vi_tri_mong_muon") or "Hầm B1 Ô tô"
        ).strip()
        ly_do_mua_them = str(
            payload.get("ly_do_mua_them") or payload.get("ly_do_yeu_cau") or ""
        ).strip()
        if not nhu_cau_vi_tri:
            raise BusinessError("Vui lòng nhập nhu cầu vị trí / khu vực mong muốn.")
        if not ly_do_mua_them:
            raise BusinessError("Vui lòng nhập lý do mua thêm chỗ đỗ ô tô.")

        db = self._db()
        with db.transaction() as tx:
            ma_cu_dan, ma_can_ho, _ = self._resolve_resident_context(tx, session)
            pending = tx.fetch_one(
                """
                SELECT ma_yeu_cau_cho
                FROM yeu_cau_mua_them_cho
                WHERE ma_can_ho = %s AND trang_thai_duyet = %s
                FOR UPDATE
                """,
                (ma_can_ho, RequestStatus.CHO_DUYET.value),
            )
            if pending:
                raise BusinessError(
                    f"Căn hộ {ma_can_ho} đã có yêu cầu mua thêm chỗ #{pending['ma_yeu_cau_cho']} đang chờ duyệt."
                )

            req_row = tx.execute_returning(
                """
                INSERT INTO yeu_cau_mua_them_cho (
                    ma_cu_dan, ma_can_ho, nhu_cau_vi_tri, ly_do_mua_them, trang_thai_duyet
                ) VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    ma_cu_dan,
                    ma_can_ho,
                    nhu_cau_vi_tri[:255],
                    ly_do_mua_them[:255],
                    RequestStatus.CHO_DUYET.value,
                ),
            )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="GUI_YEU_CAU_MUA_THEM_CHO",
            doi_tuong="yeu_cau_mua_them_cho",
            ma_doi_tuong=req_row["ma_yeu_cau_cho"] if req_row else None,
            gia_tri_cu=None,
            gia_tri_moi={
                "ma_can_ho": ma_can_ho,
                "nhu_cau_vi_tri": nhu_cau_vi_tri[:255],
                "ly_do_mua_them": ly_do_mua_them[:255],
            },
        )
        return {"request": req_row}

    # ==========================================================================
    # 1.4.15 & 1.4.18: QUẢN LÝ DANH SÁCH YÊU CẦU MUA THÊM & CĂN HỘ ĐÃ CẤP CHỖ
    # ==========================================================================
    def handle_manager_list_extra_slots(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.15 & 1.4.18: Lọc yêu cầu mua thêm chỗ và danh sách căn hộ đã được cấp chỗ đỗ."""
        trang_thai = str(payload.get("trang_thai_duyet") or "").strip()
        keyword = str(payload.get("keyword") or "").strip()
        include_revoked = bool(payload.get("include_revoked", False))

        req_conds = ["1=1"]
        req_params: List[Any] = []
        if trang_thai and trang_thai != "all":
            req_conds.append("yc.trang_thai_duyet = %s")
            req_params.append(trang_thai)
        if keyword:
            like = f"%{keyword}%"
            req_conds.append("(yc.ma_can_ho ILIKE %s OR cd.ho_ten ILIKE %s OR yc.nhu_cau_vi_tri ILIKE %s)")
            req_params.extend([like, like, like])

        db = self._db()
        requests = db.fetch_all(
            f"""
            SELECT yc.ma_yeu_cau_cho,
                   yc.ma_cu_dan,
                   yc.ma_can_ho,
                   cd.ho_ten AS ten_cu_dan,
                   cd.so_dien_thoai,
                   ch.toa_nha,
                   ch.so_tang,
                   ch.so_cho_oto_mac_dinh,
                   ch.so_cho_oto_mua_them,
                   yc.nhu_cau_vi_tri,
                   yc.ly_do_mua_them,
                   yc.trang_thai_duyet,
                   yc.ma_vi_tri_duoc_duyet,
                   vt.ten_vi_tri,
                   kv.ten_khu_vuc,
                   yc.ma_nhan_vien_duyet,
                   nv.ho_ten AS ten_quan_ly_duyet,
                   yc.ly_do_tu_choi,
                   yc.ngay_gui_yeu_cau
            FROM yeu_cau_mua_them_cho yc
            JOIN cu_dan cd ON cd.ma_cu_dan = yc.ma_cu_dan
            JOIN can_ho ch ON ch.ma_can_ho = yc.ma_can_ho
            LEFT JOIN vi_tri_do vt ON vt.ma_vi_tri = yc.ma_vi_tri_duoc_duyet
            LEFT JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
            LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = yc.ma_nhan_vien_duyet
            WHERE {' AND '.join(req_conds)}
            ORDER BY
                CASE yc.trang_thai_duyet WHEN 'cho_duyet' THEN 0 ELSE 1 END,
                yc.ngay_gui_yeu_cau DESC,
                yc.ma_yeu_cau_cho DESC
            """,
            tuple(req_params),
        )

        alloc_conds = ["1=1"]
        alloc_params: List[Any] = []
        if not include_revoked:
            alloc_conds.append("pb.trang_thai_su_dung = %s")
            alloc_params.append(SlotAllocationStatus.DANG_HIEU_LUC.value)
        if keyword:
            like = f"%{keyword}%"
            alloc_conds.append("(pb.ma_can_ho ILIKE %s OR pb.ma_vi_tri ILIKE %s OR COALESCE(pt.bien_so_xe, '') ILIKE %s)")
            alloc_params.extend([like, like, like])

        allocations = db.fetch_all(
            f"""
            SELECT pb.ma_phan_bo,
                   pb.ma_can_ho,
                   ch.toa_nha,
                   ch.so_tang,
                   ch.so_cho_oto_mac_dinh,
                   ch.so_cho_oto_mua_them,
                   pb.ma_vi_tri,
                   vt.ten_vi_tri,
                   vt.ma_khu_vuc,
                   kv.ten_khu_vuc,
                   vt.trang_thai_vi_tri,
                   pb.loai_cho_cap,
                   pb.ngay_cap,
                   pb.trang_thai_su_dung,
                   pb.ly_do_thu_hoi,
                   ql.ma_phuong_tien,
                   ql.dang_trong_bai,
                   pt.bien_so_xe,
                   pt.ten_hang_dong_xe
            FROM phan_bo_cho_do pb
            JOIN can_ho ch ON ch.ma_can_ho = pb.ma_can_ho
            JOIN vi_tri_do vt ON vt.ma_vi_tri = pb.ma_vi_tri
            JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
            LEFT JOIN quan_ly_phuong_tien_ra_vao ql
                   ON ql.ma_vi_tri_co_dinh = pb.ma_vi_tri
                  AND pb.trang_thai_su_dung = %s
            LEFT JOIN phuong_tien pt
                   ON pt.ma_phuong_tien = ql.ma_phuong_tien
                  AND pt.trang_thai_xe = %s
            WHERE {' AND '.join(alloc_conds)}
            ORDER BY
                CASE pb.trang_thai_su_dung WHEN 'dang_hieu_luc' THEN 0 ELSE 1 END,
                CASE pb.loai_cho_cap WHEN 'cho_mua_them' THEN 0 ELSE 1 END,
                pb.ma_phan_bo DESC
            """,
            (
                SlotAllocationStatus.DANG_HIEU_LUC.value,
                VehicleStatus.DANG_HOAT_DONG.value,
                *alloc_params,
            ),
        )

        return {
            "total_requests": len(requests),
            "requests": requests,
            "total_allocations": len(allocations),
            "allocations": allocations,
        }

    # ==========================================================================
    # 1.4.16 & 1.4.17: DUYỆT CẤP Ô ĐỖ MUA THÊM & TỪ CHỐI YÊU CẦU MUA THÊM CHỖ
    # ==========================================================================
    def handle_approve_extra_slot(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.16: Duyệt yêu cầu mua thêm chỗ đỗ ô tô, cấp ô đỗ & tăng hạn mức căn hộ ACID."""
        try:
            ma_yeu_cau_cho = int(payload.get("ma_yeu_cau_cho"))
        except (TypeError, ValueError) as exc:
            raise BusinessError("Thiếu hoặc sai định dạng `ma_yeu_cau_cho`.") from exc

        ma_vi_tri = str(
            payload.get("ma_vi_tri")
            or payload.get("ma_vi_tri_duoc_duyet")
            or ""
        ).strip().upper()
        if not ma_vi_tri:
            raise BusinessError("Vui lòng chọn ô đỗ ô tô trống để cấp cho căn hộ.")

        db = self._db()
        with db.transaction() as tx:
            staff_id = self._resolve_staff_id(tx, session)
            req = tx.fetch_one(
                "SELECT * FROM yeu_cau_mua_them_cho WHERE ma_yeu_cau_cho = %s FOR UPDATE",
                (ma_yeu_cau_cho,),
            )
            if not req:
                raise BusinessError(f"Không tìm thấy yêu cầu mua thêm chỗ #{ma_yeu_cau_cho}.")
            if req["trang_thai_duyet"] != RequestStatus.CHO_DUYET.value:
                raise BusinessError("Yêu cầu mua thêm chỗ này đã được xử lý trước đó.")

            slot_row = tx.fetch_one(
                """
                SELECT vt.*, kv.ma_loai_xe, kv.trang_thai_khu_vuc
                FROM vi_tri_do vt
                JOIN khu_vuc kv ON kv.ma_khu_vuc = vt.ma_khu_vuc
                WHERE vt.ma_vi_tri = %s
                FOR UPDATE OF vt
                """,
                (ma_vi_tri,),
            )
            if not slot_row:
                raise BusinessError(f"Không tìm thấy vị trí đỗ '{ma_vi_tri}'.")
            if slot_row["ma_loai_xe"] != VehicleTypes.O_TO.value:
                raise BusinessError(f"Vị trí '{ma_vi_tri}' không thuộc khu vực ô tô.")
            if slot_row["trang_thai_khu_vuc"] != ZoneStatus.HOAT_DONG.value:
                raise BusinessError(f"Khu vực của vị trí '{ma_vi_tri}' đang ngừng sử dụng.")
            if slot_row["nhom_vi_tri"] == "vi_tri_vang_lai":
                raise BusinessError(f"Ô '{ma_vi_tri}' là ô vãng lai, không thể bán cho cư dân.")
            if slot_row["trang_thai_vi_tri"] != SlotStatus.TRONG.value:
                raise BusinessError(
                    f"Ô đỗ '{ma_vi_tri}' hiện ở trạng thái '{slot_row['trang_thai_vi_tri']}', không còn trống."
                )

            ma_can_ho = str(req["ma_can_ho"])
            tx.fetch_one("SELECT ma_can_ho FROM can_ho WHERE ma_can_ho = %s FOR UPDATE", (ma_can_ho,))

            alloc = tx.execute_returning(
                """
                INSERT INTO phan_bo_cho_do (
                    ma_can_ho, ma_vi_tri, loai_cho_cap, trang_thai_su_dung
                ) VALUES (%s, %s, %s, %s)
                RETURNING *
                """,
                (
                    ma_can_ho,
                    ma_vi_tri,
                    SlotAllocationTypes.CHO_MUA_THEM.value,
                    SlotAllocationStatus.DANG_HIEU_LUC.value,
                ),
            )
            tx.execute(
                """
                UPDATE vi_tri_do
                SET trang_thai_vi_tri = %s, thoi_gian_cap_nhat = CURRENT_TIMESTAMP
                WHERE ma_vi_tri = %s
                """,
                (SlotStatus.DA_PHAN_CHO.value, ma_vi_tri),
            )
            apt_updated = tx.execute_returning(
                """
                UPDATE can_ho
                SET so_cho_oto_mua_them = so_cho_oto_mua_them + 1
                WHERE ma_can_ho = %s
                RETURNING ma_can_ho, so_cho_oto_mac_dinh, so_cho_oto_mua_them
                """,
                (ma_can_ho,),
            )
            req_updated = tx.execute_returning(
                """
                UPDATE yeu_cau_mua_them_cho
                SET trang_thai_duyet = %s,
                    ma_vi_tri_duoc_duyet = %s,
                    ma_nhan_vien_duyet = %s
                WHERE ma_yeu_cau_cho = %s
                RETURNING *
                """,
                (RequestStatus.DA_DUYET.value, ma_vi_tri, staff_id, ma_yeu_cau_cho),
            )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="DUYET_MUA_THEM_CHO_DO",
            doi_tuong="yeu_cau_mua_them_cho",
            ma_doi_tuong=ma_yeu_cau_cho,
            gia_tri_cu={"trang_thai_duyet": RequestStatus.CHO_DUYET.value},
            gia_tri_moi={
                "trang_thai_duyet": RequestStatus.DA_DUYET.value,
                "ma_can_ho": ma_can_ho,
                "ma_vi_tri_duoc_duyet": ma_vi_tri,
                "so_cho_oto_mua_them": apt_updated["so_cho_oto_mua_them"] if apt_updated else None,
            },
        )
        return {
            "request": req_updated,
            "allocation": alloc,
            "apartment": apt_updated,
        }

    def handle_reject_extra_slot(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.17: Từ chối yêu cầu mua thêm chỗ đỗ ô tô kèm lý do."""
        try:
            ma_yeu_cau_cho = int(payload.get("ma_yeu_cau_cho"))
        except (TypeError, ValueError) as exc:
            raise BusinessError("Thiếu hoặc sai định dạng `ma_yeu_cau_cho`.") from exc

        ly_do = str(
            payload.get("ly_do_tu_choi") or payload.get("ly_do_phan_hoi") or ""
        ).strip()
        if not ly_do:
            raise BusinessError("Vui lòng nhập lý do từ chối yêu cầu mua thêm chỗ.")

        db = self._db()
        with db.transaction() as tx:
            staff_id = self._resolve_staff_id(tx, session)
            req = tx.fetch_one(
                "SELECT * FROM yeu_cau_mua_them_cho WHERE ma_yeu_cau_cho = %s FOR UPDATE",
                (ma_yeu_cau_cho,),
            )
            if not req:
                raise BusinessError(f"Không tìm thấy yêu cầu mua thêm chỗ #{ma_yeu_cau_cho}.")
            if req["trang_thai_duyet"] != RequestStatus.CHO_DUYET.value:
                raise BusinessError("Yêu cầu này đã được xử lý trước đó.")

            updated = tx.execute_returning(
                """
                UPDATE yeu_cau_mua_them_cho
                SET trang_thai_duyet = %s,
                    ma_nhan_vien_duyet = %s,
                    ly_do_tu_choi = %s
                WHERE ma_yeu_cau_cho = %s
                RETURNING *
                """,
                (RequestStatus.TU_CHOI.value, staff_id, ly_do[:255], ma_yeu_cau_cho),
            )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="TU_CHOI_MUA_THEM_CHO_DO",
            doi_tuong="yeu_cau_mua_them_cho",
            ma_doi_tuong=ma_yeu_cau_cho,
            gia_tri_cu={"trang_thai_duyet": RequestStatus.CHO_DUYET.value},
            gia_tri_moi={"trang_thai_duyet": RequestStatus.TU_CHOI.value, "ly_do_tu_choi": ly_do[:255]},
        )
        return {"request": updated}


def register_vehicle_routes(
    router: Router,
    file_logger: FileLogger,
    db_provider: Any = None,
    cavet_dir: Optional[Path] = None,
) -> VehicleHandler:
    """Đăng ký các route quản lý phương tiện và phân bổ chỗ đỗ của Long."""
    handler = VehicleHandler(
        file_logger=file_logger,
        db_provider=db_provider,
        cavet_dir=cavet_dir,
    )
    router.register(
        Actions.VEHICLE_RESIDENT_LIST,
        handler.handle_resident_list,
        allowed_roles=[Roles.CU_DAN.value],
        is_mutation=False,
    )
    router.register(
        Actions.VEHICLE_SUBMIT_REQUEST,
        handler.handle_submit_request,
        allowed_roles=[Roles.CU_DAN.value],
        is_mutation=True,
    )
    router.register(
        Actions.VEHICLE_GET_CAVET,
        handler.handle_get_cavet,
        allowed_roles=[Roles.CU_DAN.value, Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
    )
    router.register(
        Actions.VEHICLE_MANAGER_LIST_REQUESTS,
        handler.handle_manager_list_requests,
        allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
    )
    router.register(
        Actions.VEHICLE_GET_ASSIGNABLE,
        handler.handle_get_assignable,
        allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
    )
    router.register(
        Actions.VEHICLE_APPROVE_REQUEST,
        handler.handle_approve_request,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
    )
    router.register(
        Actions.VEHICLE_REJECT_REQUEST,
        handler.handle_reject_request,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
    )
    router.register(
        Actions.SLOT_REQUEST_RESIDENT_DATA,
        handler.handle_resident_extra_slots_data,
        allowed_roles=[Roles.CU_DAN.value],
        is_mutation=False,
    )
    router.register(
        Actions.SLOT_REQUEST_SUBMIT,
        handler.handle_resident_submit_extra_slot,
        allowed_roles=[Roles.CU_DAN.value],
        is_mutation=True,
    )
    router.register(
        Actions.SLOT_REQUEST_MANAGER_LIST,
        handler.handle_manager_list_extra_slots,
        allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
    )
    router.register(
        Actions.SLOT_REQUEST_APPROVE,
        handler.handle_approve_extra_slot,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
    )
    router.register(
        Actions.SLOT_REQUEST_REJECT,
        handler.handle_reject_extra_slot,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
    )
    return handler
