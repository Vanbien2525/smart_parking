"""Handler Quản lý Khu vực (1.4.5–1.4.9), Vị trí đỗ (1.4.10–1.4.14) & Bảng giá (1.5)."""

from typing import Any, Dict, List, Optional

from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession
from shared.constants import Roles, SlotStatus, VehicleTypes, ZoneStatus
from shared.protocol import Actions


def _resolve_db(db_provider: Any = None) -> Any:
    if db_provider is not None:
        return db_provider
    from server import db as default_db

    return default_db


class InfraPricingHandler:
    """Xử lý CRUD Khu vực, Vị trí đỗ và Cấu hình Bảng giá gửi xe."""

    def __init__(self, file_logger: FileLogger, db_provider: Any = None) -> None:
        self.file_logger = file_logger
        self._db_provider = db_provider

    def _db(self) -> Any:
        return _resolve_db(self._db_provider)

    # ==========================================================================
    # CHỨC NĂNG 1.4.5 - 1.4.9: QUẢN LÝ KHU VỰC BÃI XE
    # ==========================================================================
    def handle_create_zone(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.5: Thêm khu vực đỗ xe mới."""
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()
        ten_khu_vuc = str(payload.get("ten_khu_vuc") or "").strip()
        ma_loai_xe = str(payload.get("ma_loai_xe") or "").strip().upper()
        if not ma_khu_vuc or not ten_khu_vuc:
            raise BusinessError("Vui lòng nhập đầy đủ mã khu vực và tên khu vực.")
        if ma_loai_xe not in (VehicleTypes.O_TO.value, VehicleTypes.XE_MAY.value):
            raise BusinessError("Loại xe áp dụng phải là `O_TO` hoặc `XE_MAY`.")

        try:
            tong_so_cho = int(payload.get("tong_so_cho") if payload.get("tong_so_cho") is not None else 0)
        except (TypeError, ValueError) as exc:
            raise BusinessError("Tổng số chỗ phải là số nguyên không âm.") from exc
        if tong_so_cho < 0:
            raise BusinessError("Tổng số chỗ không được âm.")

        db = self._db()
        if db.fetch_one("SELECT ma_khu_vuc FROM khu_vuc WHERE ma_khu_vuc = %s", (ma_khu_vuc,)):
            raise BusinessError(f"Mã khu vực '{ma_khu_vuc}' đã tồn tại.")

        zone = db.execute_returning(
            """
            INSERT INTO khu_vuc (
                ma_khu_vuc, ten_khu_vuc, ma_loai_xe, tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc
            ) VALUES (%s, %s, %s, %s, 0, %s)
            RETURNING ma_khu_vuc, ten_khu_vuc, ma_loai_xe, tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc, ly_do_ngung
            """,
            (ma_khu_vuc, ten_khu_vuc, ma_loai_xe, tong_so_cho, ZoneStatus.HOAT_DONG.value),
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="THEM_KHU_VUC",
            doi_tuong="khu_vuc",
            ma_doi_tuong=ma_khu_vuc,
            gia_tri_cu=None,
            gia_tri_moi=zone,
        )
        return {"zone": zone}

    def handle_list_zones(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.6: Xem danh sách khu vực lọc theo mã, tên, loại xe, trạng thái."""
        keyword = str(payload.get("keyword") or "").strip()
        ma_loai_xe = str(payload.get("ma_loai_xe") or "").strip().upper()
        trang_thai = str(payload.get("trang_thai_khu_vuc") or payload.get("trang_thai") or "").strip()

        conditions = ["1=1"]
        params: List[Any] = []
        if keyword:
            like = f"%{keyword}%"
            conditions.append("(kv.ma_khu_vuc ILIKE %s OR kv.ten_khu_vuc ILIKE %s)")
            params.extend([like, like])
        if ma_loai_xe:
            conditions.append("kv.ma_loai_xe = %s")
            params.append(ma_loai_xe)
        if trang_thai:
            conditions.append("kv.trang_thai_khu_vuc = %s")
            params.append(trang_thai)

        where_sql = " AND ".join(conditions)
        rows = self._db().fetch_all(
            f"""
            SELECT kv.ma_khu_vuc,
                   kv.ten_khu_vuc,
                   kv.ma_loai_xe,
                   lpt.ten_loai_xe,
                   kv.tong_so_cho,
                   kv.so_xe_dang_gui,
                   kv.trang_thai_khu_vuc,
                   kv.ly_do_ngung
            FROM khu_vuc kv
            JOIN loai_phuong_tien lpt ON lpt.ma_loai_xe = kv.ma_loai_xe
            WHERE {where_sql}
            ORDER BY kv.ma_khu_vuc ASC
            """,
            tuple(params),
        )
        return {"total": len(rows), "items": rows}

    def handle_update_zone(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.7: Cập nhật tên khu vực và tổng số chỗ (không nhỏ hơn so_xe_dang_gui)."""
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()
        if not ma_khu_vuc:
            raise BusinessError("Thiếu `ma_khu_vuc` để cập nhật.")

        db = self._db()
        old = db.fetch_one("SELECT * FROM khu_vuc WHERE ma_khu_vuc = %s", (ma_khu_vuc,))
        if not old:
            raise BusinessError(f"Không tìm thấy khu vực '{ma_khu_vuc}'.")

        ten_khu_vuc = str(
            payload.get("ten_khu_vuc") if payload.get("ten_khu_vuc") is not None else old["ten_khu_vuc"]
        ).strip()
        if not ten_khu_vuc:
            raise BusinessError("Tên khu vực không được để trống.")

        if payload.get("tong_so_cho") is not None:
            try:
                tong_so_cho = int(payload["tong_so_cho"])
            except (TypeError, ValueError) as exc:
                raise BusinessError("Tổng số chỗ phải là số nguyên.") from exc
        else:
            tong_so_cho = int(old["tong_so_cho"])

        so_xe_dang_gui = int(old.get("so_xe_dang_gui") or 0)
        if tong_so_cho < so_xe_dang_gui:
            raise BusinessError(
                f"Tổng số chỗ ({tong_so_cho}) không được nhỏ hơn số xe đang gửi ({so_xe_dang_gui})."
            )

        updated = db.execute_returning(
            """
            UPDATE khu_vuc
            SET ten_khu_vuc = %s, tong_so_cho = %s
            WHERE ma_khu_vuc = %s
            RETURNING ma_khu_vuc, ten_khu_vuc, ma_loai_xe, tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc, ly_do_ngung
            """,
            (ten_khu_vuc, tong_so_cho, ma_khu_vuc),
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="SUA_KHU_VUC",
            doi_tuong="khu_vuc",
            ma_doi_tuong=ma_khu_vuc,
            gia_tri_cu={"ten_khu_vuc": old["ten_khu_vuc"], "tong_so_cho": old["tong_so_cho"]},
            gia_tri_moi={"ten_khu_vuc": ten_khu_vuc, "tong_so_cho": tong_so_cho},
        )
        return {"zone": updated}

    def handle_deactivate_zone(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.8: Ngừng sử dụng khu vực (chỉ cho phép khi so_xe_dang_gui == 0 và không có ô đang có xe)."""
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()
        ly_do_ngung = str(payload.get("ly_do_ngung") or payload.get("ly_do") or "").strip()
        if not ma_khu_vuc:
            raise BusinessError("Thiếu `ma_khu_vuc` để ngừng sử dụng.")
        if not ly_do_ngung:
            raise BusinessError("Vui lòng nhập lý do ngừng sử dụng khu vực.")

        db = self._db()
        zone = db.fetch_one("SELECT * FROM khu_vuc WHERE ma_khu_vuc = %s", (ma_khu_vuc,))
        if not zone:
            raise BusinessError(f"Không tìm thấy khu vực '{ma_khu_vuc}'.")

        if int(zone.get("so_xe_dang_gui") or 0) > 0:
            raise BusinessError(
                f"Khu vực '{ma_khu_vuc}' vẫn còn {zone['so_xe_dang_gui']} xe đang gửi, chưa thể ngừng sử dụng."
            )

        occupied = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM vi_tri_do WHERE ma_khu_vuc = %s AND trang_thai_vi_tri = %s",
            (ma_khu_vuc, SlotStatus.DANG_CO_XE.value),
        )
        if occupied and int(occupied.get("cnt") or 0) > 0:
            raise BusinessError(
                f"Khu vực '{ma_khu_vuc}' vẫn còn ô đỗ đang có xe, chưa thể ngừng sử dụng."
            )

        updated = db.execute_returning(
            """
            UPDATE khu_vuc
            SET trang_thai_khu_vuc = %s, ly_do_ngung = %s
            WHERE ma_khu_vuc = %s
            RETURNING ma_khu_vuc, ten_khu_vuc, ma_loai_xe, tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc, ly_do_ngung
            """,
            (ZoneStatus.NGUNG_SU_DUNG.value, ly_do_ngung, ma_khu_vuc),
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="NGUNG_KHU_VUC",
            doi_tuong="khu_vuc",
            ma_doi_tuong=ma_khu_vuc,
            gia_tri_cu={"trang_thai_khu_vuc": zone["trang_thai_khu_vuc"]},
            gia_tri_moi={"trang_thai_khu_vuc": ZoneStatus.NGUNG_SU_DUNG.value, "ly_do_ngung": ly_do_ngung},
        )
        return {"zone": updated}

    def handle_activate_zone(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.9: Kích hoạt lại khu vực đỗ xe."""
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()
        if not ma_khu_vuc:
            raise BusinessError("Thiếu `ma_khu_vuc` để kích hoạt.")

        db = self._db()
        zone = db.fetch_one("SELECT * FROM khu_vuc WHERE ma_khu_vuc = %s", (ma_khu_vuc,))
        if not zone:
            raise BusinessError(f"Không tìm thấy khu vực '{ma_khu_vuc}'.")

        updated = db.execute_returning(
            """
            UPDATE khu_vuc
            SET trang_thai_khu_vuc = %s, ly_do_ngung = NULL
            WHERE ma_khu_vuc = %s
            RETURNING ma_khu_vuc, ten_khu_vuc, ma_loai_xe, tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc, ly_do_ngung
            """,
            (ZoneStatus.HOAT_DONG.value, ma_khu_vuc),
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="KICH_HOAT_KHU_VUC",
            doi_tuong="khu_vuc",
            ma_doi_tuong=ma_khu_vuc,
            gia_tri_cu={"trang_thai_khu_vuc": zone["trang_thai_khu_vuc"]},
            gia_tri_moi={"trang_thai_khu_vuc": ZoneStatus.HOAT_DONG.value},
        )
        return {"zone": updated}


def register_infra_pricing_routes(
    router: Router,
    file_logger: FileLogger,
    db_provider: Any = None,
) -> InfraPricingHandler:
    """Đăng ký các route Khu vực, Vị trí đỗ & Bảng giá lên Router."""
    handler = InfraPricingHandler(file_logger=file_logger, db_provider=db_provider)

    router.register(
        Actions.INFRA_CREATE_ZONE,
        handler.handle_create_zone,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.INFRA_LIST_ZONES,
        handler.handle_list_zones,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.INFRA_UPDATE_ZONE,
        handler.handle_update_zone,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.INFRA_DEACTIVATE_ZONE,
        handler.handle_deactivate_zone,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.INFRA_ACTIVATE_ZONE,
        handler.handle_activate_zone,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    return handler
