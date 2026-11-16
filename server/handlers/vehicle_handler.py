"""Handler Quản lý Phương tiện, Duyệt đăng ký/biến động & Phân bổ/Thu hồi Chỗ đỗ (Thành viên 4: Long).

Bao gồm toàn bộ 23 chức năng lá:
- 3.2.1 – 3.2.5: Quản lý phương tiện cư dân & upload ảnh cà-vẹt Base64 (Workflow 02)
- 1.4.1 – 1.4.4: Quản lý duyệt yêu cầu phương tiện & gán vị trí/khu vực đỗ (Workflow 05)
- 3.3.1 – 3.3.5: Cư dân mua thêm chỗ đỗ ô tô & xem vị trí được cấp
- 1.4.15 – 1.4.19: Quản lý duyệt mua thêm chỗ & thu hồi chỗ đỗ đã cấp
- 3.4.1 – 3.4.4: Tra cứu & định vị xe của cư dân
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from server.config import DEFAULT_CONFIG, ensure_storage_dirs
from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession
from shared.constants import (
    RequestStatus,
    Roles,
    SlotAllocationStatus,
    SlotAllocationTypes,
    VehicleRequestTypes,
    VehicleStatus,
    VehicleTypes,
)
from shared.protocol import Actions


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
    return handler
