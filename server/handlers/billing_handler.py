"""Handler Hóa đơn Tháng, Thanh toán QR/Tiền mặt (Workflow 06) & Theo dõi Thu phí (3.5 & 1.7)."""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession
from shared.constants import InvoiceStatus, PricingGroups, PricingStatus, Roles
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
    return handler
