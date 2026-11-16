"""Handler Quản lý Khu vực (1.4.5–1.4.9), Vị trí đỗ (1.4.10–1.4.14) & Bảng giá (1.5)."""

from typing import Any, Dict, List, Optional

from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession
from shared.constants import Roles, SlotGroups, SlotStatus, VehicleTypes, ZoneStatus
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

    # ==========================================================================
    # CHỨC NĂNG 1.4.10 - 1.4.14: QUẢN LÝ VỊ TRÍ / Ô ĐỖ XE
    # ==========================================================================
    def handle_create_slot(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.10: Thêm ô đỗ mới vào khu vực và đồng bộ `tong_so_cho`."""
        ma_vi_tri = str(payload.get("ma_vi_tri") or "").strip().upper()
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()
        ten_vi_tri = str(payload.get("ten_vi_tri") or ma_vi_tri).strip()
        nhom_vi_tri = str(payload.get("nhom_vi_tri") or SlotGroups.VI_TRI_THUONG.value).strip()

        if not ma_vi_tri or not ma_khu_vuc:
            raise BusinessError("Vui lòng nhập đầy đủ mã vị trí và mã khu vực.")
        valid_groups = (
            SlotGroups.VI_TRI_THUONG.value,
            SlotGroups.VI_TRI_DAU_BAI.value,
            SlotGroups.VI_TRI_VANG_LAI.value,
        )
        if nhom_vi_tri not in valid_groups:
            raise BusinessError("Nhóm vị trí không hợp lệ.")

        db = self._db()
        with db.transaction() as tx:
            zone = tx.fetch_one("SELECT ma_khu_vuc, tong_so_cho FROM khu_vuc WHERE ma_khu_vuc = %s", (ma_khu_vuc,))
            if not zone:
                raise BusinessError(f"Không tìm thấy khu vực '{ma_khu_vuc}'.")
            if tx.fetch_one("SELECT ma_vi_tri FROM vi_tri_do WHERE ma_vi_tri = %s", (ma_vi_tri,)):
                raise BusinessError(f"Mã vị trí '{ma_vi_tri}' đã tồn tại.")

            slot = tx.execute_returning(
                """
                INSERT INTO vi_tri_do (
                    ma_vi_tri, ma_khu_vuc, ten_vi_tri, nhom_vi_tri, trang_thai_vi_tri, thoi_gian_cap_nhat
                ) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING ma_vi_tri, ma_khu_vuc, ten_vi_tri, nhom_vi_tri, trang_thai_vi_tri, ly_do_ngung, thoi_gian_cap_nhat
                """,
                (ma_vi_tri, ma_khu_vuc, ten_vi_tri, nhom_vi_tri, SlotStatus.TRONG.value),
            )
            cnt_row = tx.fetch_one(
                "SELECT COUNT(*) AS cnt FROM vi_tri_do WHERE ma_khu_vuc = %s",
                (ma_khu_vuc,),
            )
            slot_count = int((cnt_row or {}).get("cnt") or 0)
            if slot_count > int(zone.get("tong_so_cho") or 0):
                tx.execute(
                    "UPDATE khu_vuc SET tong_so_cho = %s WHERE ma_khu_vuc = %s",
                    (slot_count, ma_khu_vuc),
                )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="THEM_VI_TRI",
            doi_tuong="vi_tri_do",
            ma_doi_tuong=ma_vi_tri,
            gia_tri_cu=None,
            gia_tri_moi=slot,
        )
        return {"slot": slot}

    def handle_list_slots(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.11: Xem danh sách vị trí đỗ theo khu vực, mã, nhóm và trạng thái."""
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()
        keyword = str(payload.get("ma_vi_tri") or payload.get("keyword") or "").strip()
        nhom_vi_tri = str(payload.get("nhom_vi_tri") or "").strip()
        trang_thai = str(payload.get("trang_thai_vi_tri") or payload.get("trang_thai") or "").strip()

        conditions = ["1=1"]
        params: List[Any] = []
        if ma_khu_vuc:
            conditions.append("v.ma_khu_vuc = %s")
            params.append(ma_khu_vuc)
        if keyword:
            like = f"%{keyword}%"
            conditions.append("(v.ma_vi_tri ILIKE %s OR v.ten_vi_tri ILIKE %s)")
            params.extend([like, like])
        if nhom_vi_tri:
            conditions.append("v.nhom_vi_tri = %s")
            params.append(nhom_vi_tri)
        if trang_thai:
            conditions.append("v.trang_thai_vi_tri = %s")
            params.append(trang_thai)

        where_sql = " AND ".join(conditions)
        rows = self._db().fetch_all(
            f"""
            SELECT v.ma_vi_tri,
                   v.ma_khu_vuc,
                   kv.ten_khu_vuc,
                   kv.ma_loai_xe,
                   v.ten_vi_tri,
                   v.nhom_vi_tri,
                   v.trang_thai_vi_tri,
                   v.ly_do_ngung,
                   v.thoi_gian_cap_nhat,
                   pb.ma_can_ho AS can_ho_so_huu
            FROM vi_tri_do v
            JOIN khu_vuc kv ON kv.ma_khu_vuc = v.ma_khu_vuc
            LEFT JOIN phan_bo_cho_do pb
                   ON pb.ma_vi_tri = v.ma_vi_tri AND pb.trang_thai_su_dung = 'dang_hieu_luc'
            WHERE {where_sql}
            ORDER BY v.ma_khu_vuc ASC, v.ma_vi_tri ASC
            """,
            tuple(params),
        )
        return {"total": len(rows), "items": rows}

    def handle_update_slot(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.12: Sửa tên hiển thị và nhóm vị trí đỗ."""
        ma_vi_tri = str(payload.get("ma_vi_tri") or "").strip().upper()
        if not ma_vi_tri:
            raise BusinessError("Thiếu `ma_vi_tri` để cập nhật.")

        db = self._db()
        old = db.fetch_one("SELECT * FROM vi_tri_do WHERE ma_vi_tri = %s", (ma_vi_tri,))
        if not old:
            raise BusinessError(f"Không tìm thấy vị trí '{ma_vi_tri}'.")

        ten_vi_tri = str(
            payload.get("ten_vi_tri") if payload.get("ten_vi_tri") is not None else old["ten_vi_tri"]
        ).strip()
        nhom_vi_tri = str(
            payload.get("nhom_vi_tri") if payload.get("nhom_vi_tri") is not None else old["nhom_vi_tri"]
        ).strip()
        if not ten_vi_tri:
            raise BusinessError("Tên vị trí không được để trống.")
        if nhom_vi_tri not in (
            SlotGroups.VI_TRI_THUONG.value,
            SlotGroups.VI_TRI_DAU_BAI.value,
            SlotGroups.VI_TRI_VANG_LAI.value,
        ):
            raise BusinessError("Nhóm vị trí không hợp lệ.")

        updated = db.execute_returning(
            """
            UPDATE vi_tri_do
            SET ten_vi_tri = %s, nhom_vi_tri = %s, thoi_gian_cap_nhat = CURRENT_TIMESTAMP
            WHERE ma_vi_tri = %s
            RETURNING ma_vi_tri, ma_khu_vuc, ten_vi_tri, nhom_vi_tri, trang_thai_vi_tri, ly_do_ngung, thoi_gian_cap_nhat
            """,
            (ten_vi_tri, nhom_vi_tri, ma_vi_tri),
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="SUA_VI_TRI",
            doi_tuong="vi_tri_do",
            ma_doi_tuong=ma_vi_tri,
            gia_tri_cu={"ten_vi_tri": old["ten_vi_tri"], "nhom_vi_tri": old["nhom_vi_tri"]},
            gia_tri_moi={"ten_vi_tri": ten_vi_tri, "nhom_vi_tri": nhom_vi_tri},
        )
        return {"slot": updated}

    def handle_deactivate_slot(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.13: Ngừng sử dụng vị trí (chặn nếu trang_thai_vi_tri == 'dang_co_xe')."""
        ma_vi_tri = str(payload.get("ma_vi_tri") or "").strip().upper()
        ly_do_ngung = str(payload.get("ly_do_ngung") or payload.get("ly_do") or "").strip()
        if not ma_vi_tri:
            raise BusinessError("Thiếu `ma_vi_tri` để ngừng sử dụng.")
        if not ly_do_ngung:
            raise BusinessError("Vui lòng nhập lý do ngừng sử dụng vị trí.")

        db = self._db()
        slot = db.fetch_one("SELECT * FROM vi_tri_do WHERE ma_vi_tri = %s", (ma_vi_tri,))
        if not slot:
            raise BusinessError(f"Không tìm thấy vị trí '{ma_vi_tri}'.")
        if slot.get("trang_thai_vi_tri") == SlotStatus.DANG_CO_XE.value:
            raise BusinessError(
                f"Vị trí '{ma_vi_tri}' đang có xe đỗ (dang_co_xe), không thể ngừng sử dụng."
            )

        updated = db.execute_returning(
            """
            UPDATE vi_tri_do
            SET trang_thai_vi_tri = %s, ly_do_ngung = %s, thoi_gian_cap_nhat = CURRENT_TIMESTAMP
            WHERE ma_vi_tri = %s
            RETURNING ma_vi_tri, ma_khu_vuc, ten_vi_tri, nhom_vi_tri, trang_thai_vi_tri, ly_do_ngung, thoi_gian_cap_nhat
            """,
            (SlotStatus.NGUNG_SU_DUNG.value, ly_do_ngung, ma_vi_tri),
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="NGUNG_VI_TRI",
            doi_tuong="vi_tri_do",
            ma_doi_tuong=ma_vi_tri,
            gia_tri_cu={"trang_thai_vi_tri": slot["trang_thai_vi_tri"]},
            gia_tri_moi={"trang_thai_vi_tri": SlotStatus.NGUNG_SU_DUNG.value, "ly_do_ngung": ly_do_ngung},
        )
        return {"slot": updated}

    def handle_activate_slot(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.4.14: Kích hoạt lại vị trí (tự trả về da_phan_cho nếu đang được phân bổ hợp lệ)."""
        ma_vi_tri = str(payload.get("ma_vi_tri") or "").strip().upper()
        if not ma_vi_tri:
            raise BusinessError("Thiếu `ma_vi_tri` để kích hoạt lại.")

        db = self._db()
        slot = db.fetch_one("SELECT * FROM vi_tri_do WHERE ma_vi_tri = %s", (ma_vi_tri,))
        if not slot:
            raise BusinessError(f"Không tìm thấy vị trí '{ma_vi_tri}'.")

        alloc = db.fetch_one(
            """
            SELECT ma_phan_bo FROM phan_bo_cho_do
            WHERE ma_vi_tri = %s AND trang_thai_su_dung = 'dang_hieu_luc'
            LIMIT 1
            """,
            (ma_vi_tri,),
        )
        next_status = SlotStatus.DA_PHAN_CHO.value if alloc else SlotStatus.TRONG.value

        updated = db.execute_returning(
            """
            UPDATE vi_tri_do
            SET trang_thai_vi_tri = %s, ly_do_ngung = NULL, thoi_gian_cap_nhat = CURRENT_TIMESTAMP
            WHERE ma_vi_tri = %s
            RETURNING ma_vi_tri, ma_khu_vuc, ten_vi_tri, nhom_vi_tri, trang_thai_vi_tri, ly_do_ngung, thoi_gian_cap_nhat
            """,
            (next_status, ma_vi_tri),
        )
        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="KICH_HOAT_VI_TRI",
            doi_tuong="vi_tri_do",
            ma_doi_tuong=ma_vi_tri,
            gia_tri_cu={"trang_thai_vi_tri": slot["trang_thai_vi_tri"]},
            gia_tri_moi={"trang_thai_vi_tri": next_status},
        )
        return {"slot": updated}


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
    router.register(
        Actions.INFRA_CREATE_SLOT,
        handler.handle_create_slot,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.INFRA_LIST_SLOTS,
        handler.handle_list_slots,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.INFRA_UPDATE_SLOT,
        handler.handle_update_slot,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.INFRA_DEACTIVATE_SLOT,
        handler.handle_deactivate_slot,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.INFRA_ACTIVATE_SLOT,
        handler.handle_activate_slot,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    return handler
