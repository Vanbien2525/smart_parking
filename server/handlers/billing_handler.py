"""Handler Hóa đơn Tháng, Thanh toán QR/Tiền mặt (Workflow 06) & Theo dõi Thu phí (3.5 & 1.7)."""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession
from shared.constants import InvoiceStatus, PricingGroups, PricingStatus, Roles, TransactionStatus
from shared.protocol import Actions

_KY_THU_RE = re.compile(r"^(0[1-9]|1[0-2])/\d{4}$")


def _resolve_db(db_provider: Any = None) -> Any:
    if db_provider is not None:
        return db_provider
    from server import db as default_db

    return default_db


def normalize_billing_period(raw_period: Optional[str]) -> str:
    """Chuẩn hóa và kiểm tra kỳ thu định dạng `MM/YYYY`."""
    period = (raw_period or datetime.now().strftime("%m/%Y")).strip()
    if not _KY_THU_RE.match(period):
        raise BusinessError("Kỳ thu phí phải có định dạng MM/YYYY (ví dụ: 11/2026).")
    return period


def calculate_monthly_amount(
    so_cho_oto_mac_dinh: int,
    so_cho_oto_mua_them: int,
    so_luong_xe_may: int,
    gia_oto_mac_dinh: float,
    gia_oto_mua_them: float,
    gia_xe_may: float,
) -> float:
    """Công thức chuẩn tính tổng tiền phải thu hóa đơn tháng của 1 căn hộ."""
    return round(
        max(0, int(so_cho_oto_mac_dinh)) * float(gia_oto_mac_dinh)
        + max(0, int(so_cho_oto_mua_them)) * float(gia_oto_mua_them)
        + max(0, int(so_luong_xe_may)) * float(gia_xe_may),
        2,
    )


class BillingHandler:
    """Xử lý phát hành hóa đơn tháng, thanh toán Workflow 06 (3.5) và thu phí (1.7)."""

    def __init__(self, file_logger: FileLogger, db_provider: Any = None) -> None:
        self.file_logger = file_logger
        self._db_provider = db_provider

    def _db(self) -> Any:
        return _resolve_db(self._db_provider)

    def _load_active_monthly_rates(self, db_or_tx: Any) -> Dict[str, float]:
        rows = db_or_tx.fetch_all(
            """
            SELECT nhom_ap_dung, muc_phi
            FROM bang_gia
            WHERE trang_thai_gia = %s
              AND nhom_ap_dung IN (%s, %s, %s)
            ORDER BY ma_bang_gia DESC
            """,
            (
                PricingStatus.DANG_AP_DUNG.value,
                PricingGroups.CHO_OTO_MAC_DINH.value,
                PricingGroups.CHO_OTO_MUA_THEM.value,
                PricingGroups.XE_MAY_CU_DAN.value,
            ),
        )
        rates: Dict[str, float] = {
            PricingGroups.CHO_OTO_MAC_DINH.value: 0.0,
            PricingGroups.CHO_OTO_MUA_THEM.value: 0.0,
            PricingGroups.XE_MAY_CU_DAN.value: 0.0,
        }
        seen = set()
        for row in rows:
            group = str(row.get("nhom_ap_dung") or "")
            if group in rates and group not in seen:
                rates[group] = float(row.get("muc_phi") or 0)
                seen.add(group)
        return rates

    # ==========================================================================
    # CHỨC NĂNG PHÁT HÀNH / ĐỒNG BỘ HÓA ĐƠN THÁNG
    # ==========================================================================
    def generate_monthly_invoices(
        self,
        ky_thu: Optional[str] = None,
        session: Optional[ClientSession] = None,
    ) -> Dict[str, Any]:
        """Phát hành hoặc đồng bộ hóa đơn tháng `MM/YYYY` cho toàn bộ căn hộ đang sử dụng."""
        period = normalize_billing_period(ky_thu)
        db = self._db()

        created_count = 0
        updated_count = 0
        skipped_paid_count = 0

        with db.transaction() as tx:
            rates = self._load_active_monthly_rates(tx)
            gia_mac_dinh = rates[PricingGroups.CHO_OTO_MAC_DINH.value]
            gia_mua_them = rates[PricingGroups.CHO_OTO_MUA_THEM.value]
            gia_xe_may = rates[PricingGroups.XE_MAY_CU_DAN.value]

            apartments = tx.fetch_all(
                """
                SELECT ch.ma_can_ho,
                       ch.so_cho_oto_mac_dinh,
                       ch.so_cho_oto_mua_them,
                       COALESCE((
                           SELECT COUNT(*)
                           FROM phuong_tien pt
                           JOIN cu_dan cd ON cd.ma_cu_dan = pt.ma_cu_dan
                           WHERE cd.ma_can_ho = ch.ma_can_ho
                             AND pt.ma_loai_xe = 'XE_MAY'
                             AND pt.trang_thai_xe = 'dang_hoat_dong'
                       ), 0) AS so_luong_xe_may
                FROM can_ho ch
                WHERE ch.trang_thai_can_ho = 'dang_su_dung'
                ORDER BY ch.ma_can_ho ASC
                """
            )

            for apt in apartments:
                ma_can_ho = str(apt["ma_can_ho"])
                n_def = int(apt.get("so_cho_oto_mac_dinh") or 0)
                n_extra = int(apt.get("so_cho_oto_mua_them") or 0)
                n_moto = int(apt.get("so_luong_xe_may") or 0)
                total_amount = calculate_monthly_amount(
                    n_def, n_extra, n_moto, gia_mac_dinh, gia_mua_them, gia_xe_may
                )

                existing = tx.fetch_one(
                    """
                    SELECT ma_hoa_don, trang_thai_hoa_don
                    FROM hoa_don_thang
                    WHERE ma_can_ho = %s AND ky_thu_thang_nam = %s
                    """,
                    (ma_can_ho, period),
                )
                if not existing:
                    tx.execute(
                        """
                        INSERT INTO hoa_don_thang (
                            ma_can_ho, ky_thu_thang_nam,
                            so_cho_oto_mac_dinh, so_cho_oto_mua_them, so_luong_xe_may,
                            tong_tien_phai_thu, trang_thai_hoa_don
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            ma_can_ho,
                            period,
                            n_def,
                            n_extra,
                            n_moto,
                            total_amount,
                            InvoiceStatus.CHUA_THANH_TOAN.value,
                        ),
                    )
                    created_count += 1
                elif existing.get("trang_thai_hoa_don") == InvoiceStatus.CHUA_THANH_TOAN.value:
                    tx.execute(
                        """
                        UPDATE hoa_don_thang
                        SET so_cho_oto_mac_dinh = %s,
                            so_cho_oto_mua_them = %s,
                            so_luong_xe_may = %s,
                            tong_tien_phai_thu = %s
                        WHERE ma_hoa_don = %s
                        """,
                        (n_def, n_extra, n_moto, total_amount, int(existing["ma_hoa_don"])),
                    )
                    updated_count += 1
                else:
                    skipped_paid_count += 1

        if session is not None:
            self.file_logger.log_audit(
                ma_tai_khoan=session.ma_tai_khoan,
                ten_dang_nhap=session.ten_dang_nhap or "",
                vai_tro=session.vai_tro or "",
                loai_thao_tac="PHAT_HANH_HOA_DON_THANG",
                doi_tuong="hoa_don_thang",
                ma_doi_tuong=period,
                gia_tri_cu=None,
                gia_tri_moi={
                    "created": created_count,
                    "updated": updated_count,
                    "skipped_paid": skipped_paid_count,
                },
            )
        return {
            "ky_thu_thang_nam": period,
            "created": created_count,
            "updated": updated_count,
            "skipped_paid": skipped_paid_count,
            "rates": rates,
        }

    def handle_generate_monthly_invoices(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handler `billing.generate_monthly_invoices` cho Quản lý."""
        return self.generate_monthly_invoices(
            ky_thu=payload.get("ky_thu_thang_nam"),
            session=session,
        )

    # ==========================================================================
    # CHỨC NĂNG 3.5.1 - 3.5.4: HÓA ĐƠN GỬI XE CỦA CƯ DÂN
    # ==========================================================================
    def _resolve_resident_apartment(self, session: ClientSession) -> str:
        if session.ma_can_ho:
            return str(session.ma_can_ho)
        if session.ma_tai_khoan is not None:
            row = self._db().fetch_one(
                "SELECT ma_can_ho FROM cu_dan WHERE ma_tai_khoan = %s",
                (int(session.ma_tai_khoan),),
            )
            if row and row.get("ma_can_ho"):
                return str(row["ma_can_ho"])
        raise BusinessError("Không xác định được căn hộ của cư dân đang đăng nhập.")

    def _assert_invoice_access(self, session: ClientSession, invoice_row: Dict[str, Any]) -> None:
        if session.vai_tro == Roles.CU_DAN.value:
            my_apt = self._resolve_resident_apartment(session)
            if str(invoice_row.get("ma_can_ho") or "") != my_apt:
                raise PermissionError("Bạn không có quyền xem hóa đơn của căn hộ khác.")

    def handle_my_invoices(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.5.1: Xem danh sách hóa đơn tháng thuộc căn hộ của cư dân."""
        ma_can_ho = self._resolve_resident_apartment(session)
        trang_thai = str(payload.get("trang_thai_hoa_don") or "").strip()

        conditions = ["hd.ma_can_ho = %s"]
        params: List[Any] = [ma_can_ho]
        if trang_thai:
            conditions.append("hd.trang_thai_hoa_don = %s")
            params.append(trang_thai)

        where_sql = " AND ".join(conditions)
        rows = self._db().fetch_all(
            f"""
            SELECT hd.ma_hoa_don,
                   hd.ma_can_ho,
                   hd.ky_thu_thang_nam,
                   hd.so_cho_oto_mac_dinh,
                   hd.so_cho_oto_mua_them,
                   hd.so_luong_xe_may,
                   hd.tong_tien_phai_thu,
                   hd.trang_thai_hoa_don,
                   hd.ngay_tao_hoa_don
            FROM hoa_don_thang hd
            WHERE {where_sql}
            ORDER BY hd.ngay_tao_hoa_don DESC, hd.ma_hoa_don DESC
            """,
            tuple(params),
        )
        return {"ma_can_ho": ma_can_ho, "total": len(rows), "items": rows}

    def handle_search_my_invoices(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.5.2: Tìm hóa đơn của cư dân theo mã hóa đơn, kỳ thu hoặc từ khóa."""
        ma_can_ho = self._resolve_resident_apartment(session)
        ma_hoa_don = payload.get("ma_hoa_don")
        ky_thu = str(payload.get("ky_thu_thang_nam") or "").strip()
        keyword = str(payload.get("keyword") or "").strip()
        trang_thai = str(payload.get("trang_thai_hoa_don") or "").strip()

        conditions = ["hd.ma_can_ho = %s"]
        params: List[Any] = [ma_can_ho]
        if ma_hoa_don not in (None, ""):
            try:
                conditions.append("hd.ma_hoa_don = %s")
                params.append(int(ma_hoa_don))
            except (TypeError, ValueError) as exc:
                raise BusinessError("Mã hóa đơn tìm kiếm phải là số nguyên.") from exc
        if ky_thu:
            conditions.append("hd.ky_thu_thang_nam ILIKE %s")
            params.append(f"%{ky_thu}%")
        if trang_thai:
            conditions.append("hd.trang_thai_hoa_don = %s")
            params.append(trang_thai)
        if keyword:
            like = f"%{keyword}%"
            conditions.append("(CAST(hd.ma_hoa_don AS TEXT) ILIKE %s OR hd.ky_thu_thang_nam ILIKE %s)")
            params.extend([like, like])

        where_sql = " AND ".join(conditions)
        rows = self._db().fetch_all(
            f"""
            SELECT hd.ma_hoa_don,
                   hd.ma_can_ho,
                   hd.ky_thu_thang_nam,
                   hd.so_cho_oto_mac_dinh,
                   hd.so_cho_oto_mua_them,
                   hd.so_luong_xe_may,
                   hd.tong_tien_phai_thu,
                   hd.trang_thai_hoa_don,
                   hd.ngay_tao_hoa_don
            FROM hoa_don_thang hd
            WHERE {where_sql}
            ORDER BY hd.ngay_tao_hoa_don DESC, hd.ma_hoa_don DESC
            """,
            tuple(params),
        )
        return {"ma_can_ho": ma_can_ho, "total": len(rows), "items": rows}

    def handle_get_invoice_detail(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.5.3: Xem chi tiết hóa đơn (phân tách chi phí, vị trí đỗ sở hữu, lịch sử giao dịch)."""
        ma_hoa_don = payload.get("ma_hoa_don")
        if ma_hoa_don is None:
            raise BusinessError("Thiếu `ma_hoa_don` để xem chi tiết.")

        db = self._db()
        inv = db.fetch_one(
            """
            SELECT hd.*, ch.toa_nha, ch.so_tang
            FROM hoa_don_thang hd
            JOIN can_ho ch ON ch.ma_can_ho = hd.ma_can_ho
            WHERE hd.ma_hoa_don = %s
            """,
            (int(ma_hoa_don),),
        )
        if not inv:
            raise BusinessError(f"Không tìm thấy hóa đơn mã {ma_hoa_don}.")
        self._assert_invoice_access(session, inv)

        rates = self._load_active_monthly_rates(db)
        gia_def = rates[PricingGroups.CHO_OTO_MAC_DINH.value]
        gia_ext = rates[PricingGroups.CHO_OTO_MUA_THEM.value]
        gia_moto = rates[PricingGroups.XE_MAY_CU_DAN.value]

        n_def = int(inv.get("so_cho_oto_mac_dinh") or 0)
        n_ext = int(inv.get("so_cho_oto_mua_them") or 0)
        n_moto = int(inv.get("so_luong_xe_may") or 0)
        breakdown = [
            {
                "khoan_muc": "Chỗ ô tô mặc định",
                "so_luong": n_def,
                "don_gia": gia_def,
                "thanh_tien": round(n_def * gia_def, 2),
            },
            {
                "khoan_muc": "Chỗ ô tô mua thêm",
                "so_luong": n_ext,
                "don_gia": gia_ext,
                "thanh_tien": round(n_ext * gia_ext, 2),
            },
            {
                "khoan_muc": "Xe máy cư dân",
                "so_luong": n_moto,
                "don_gia": gia_moto,
                "thanh_tien": round(n_moto * gia_moto, 2),
            },
        ]

        owned_slots = db.fetch_all(
            """
            SELECT pb.ma_phan_bo, pb.ma_vi_tri, pb.loai_cho_cap, pb.ngay_cap,
                   v.ten_vi_tri, v.ma_khu_vuc, kv.ten_khu_vuc
            FROM phan_bo_cho_do pb
            JOIN vi_tri_do v ON v.ma_vi_tri = pb.ma_vi_tri
            JOIN khu_vuc kv ON kv.ma_khu_vuc = v.ma_khu_vuc
            WHERE pb.ma_can_ho = %s AND pb.trang_thai_su_dung = 'dang_hieu_luc'
            ORDER BY pb.ma_vi_tri ASC
            """,
            (inv["ma_can_ho"],),
        )

        transactions = db.fetch_all(
            """
            SELECT gd.ma_giao_dich, gd.hinh_thuc_thanh_toan, gd.so_tien_thanh_toan,
                   gd.ma_qr, gd.han_ma_qr, gd.ma_bien_lai, gd.ma_nhan_vien_thu,
                   nv.ho_ten AS ten_nhan_vien_thu,
                   gd.trang_thai_giao_dich, gd.ly_do_loi, gd.thoi_gian_giao_dich
            FROM giao_dich_thanh_toan gd
            LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = gd.ma_nhan_vien_thu
            WHERE gd.ma_hoa_don = %s
            ORDER BY gd.thoi_gian_giao_dich DESC, gd.ma_giao_dich DESC
            """,
            (int(ma_hoa_don),),
        )
        return {
            "invoice": inv,
            "breakdown": breakdown,
            "owned_slots": owned_slots,
            "transactions": transactions,
        }

    def handle_get_receipt(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """3.5.4: Xem biên nhận thanh toán thành công của hóa đơn."""
        ma_hoa_don = payload.get("ma_hoa_don")
        ma_giao_dich = payload.get("ma_giao_dich")
        if ma_hoa_don is None and ma_giao_dich is None:
            raise BusinessError("Vui lòng cung cấp `ma_hoa_don` hoặc `ma_giao_dich`.")

        db = self._db()
        if ma_giao_dich is not None:
            receipt = db.fetch_one(
                """
                SELECT gd.*, hd.ma_can_ho, hd.ky_thu_thang_nam, hd.tong_tien_phai_thu,
                       nv.ho_ten AS ten_nhan_vien_thu, nv.ma_nhan_vien_code
                FROM giao_dich_thanh_toan gd
                JOIN hoa_don_thang hd ON hd.ma_hoa_don = gd.ma_hoa_don
                LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = gd.ma_nhan_vien_thu
                WHERE gd.ma_giao_dich = %s
                """,
                (int(ma_giao_dich),),
            )
        else:
            receipt = db.fetch_one(
                """
                SELECT gd.*, hd.ma_can_ho, hd.ky_thu_thang_nam, hd.tong_tien_phai_thu,
                       nv.ho_ten AS ten_nhan_vien_thu, nv.ma_nhan_vien_code
                FROM giao_dich_thanh_toan gd
                JOIN hoa_don_thang hd ON hd.ma_hoa_don = gd.ma_hoa_don
                LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = gd.ma_nhan_vien_thu
                WHERE gd.ma_hoa_don = %s AND gd.trang_thai_giao_dich = %s
                ORDER BY gd.thoi_gian_giao_dich DESC, gd.ma_giao_dich DESC
                LIMIT 1
                """,
                (int(ma_hoa_don), TransactionStatus.THANH_TOAN_THANH_CONG.value),
            )
        if not receipt:
            raise BusinessError("Chưa có biên nhận thanh toán thành công cho hóa đơn này.")
        self._assert_invoice_access(session, receipt)
        return {"receipt": receipt}


def register_billing_routes(
    router: Router,
    file_logger: FileLogger,
    db_provider: Any = None,
) -> BillingHandler:
    """Đăng ký các route Hóa đơn, Thanh toán Workflow 06 & Theo dõi thu phí."""
    handler = BillingHandler(file_logger=file_logger, db_provider=db_provider)
    router.register(
        Actions.BILLING_GENERATE_MONTHLY,
        handler.handle_generate_monthly_invoices,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_MY_INVOICES,
        handler.handle_my_invoices,
        allowed_roles=[Roles.CU_DAN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_SEARCH_MY_INVOICES,
        handler.handle_search_my_invoices,
        allowed_roles=[Roles.CU_DAN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_GET_INVOICE_DETAIL,
        handler.handle_get_invoice_detail,
        allowed_roles=[Roles.CU_DAN.value, Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_GET_RECEIPT,
        handler.handle_get_receipt,
        allowed_roles=[Roles.CU_DAN.value, Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    return handler
