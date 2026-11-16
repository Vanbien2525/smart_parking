"""Handler Hóa đơn Tháng, Thanh toán QR/Tiền mặt (Workflow 06) & Theo dõi Thu phí (3.5 & 1.7)."""

import base64
import io
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import qrcode

from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession
from shared.constants import (
    InvoiceStatus,
    PaymentMethods,
    PricingGroups,
    PricingStatus,
    Roles,
    TransactionStatus,
)
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

    # ==========================================================================
    # WORKFLOW 06: THANH TOÁN PHÍ HÀNG THÁNG (CHUYỂN KHOẢN QR & TIỀN MẶT)
    # ==========================================================================
    @staticmethod
    def _render_qr_base64(qr_payload: str) -> str:
        """Tạo ảnh PNG mã QR dạng Base64 bằng thư viện `qrcode`."""
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(qr_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _make_receipt_code(prefix: str, ma_hoa_don: int) -> str:
        return f"{prefix}-{ma_hoa_don}-{uuid.uuid4().hex[:6].upper()}"

    def _resolve_staff_id(self, db_or_tx: Any, session: ClientSession) -> Optional[int]:
        if session.ma_nhan_vien is not None:
            return int(session.ma_nhan_vien)
        if session.ma_tai_khoan is not None:
            row = db_or_tx.fetch_one(
                "SELECT ma_nhan_vien FROM nhan_vien WHERE ma_tai_khoan = %s",
                (int(session.ma_tai_khoan),),
            )
            if row and row.get("ma_nhan_vien") is not None:
                return int(row["ma_nhan_vien"])
        return None

    def handle_create_qr_payment(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Workflow 06 (Nhánh QR): Sinh mã QR ảo, ảnh PNG base64 & han_ma_qr (+5 phút)."""
        ma_hoa_don = payload.get("ma_hoa_don")
        if ma_hoa_don is None:
            raise BusinessError("Thiếu `ma_hoa_don` để khởi tạo mã QR thanh toán.")

        db = self._db()
        inv = db.fetch_one("SELECT * FROM hoa_don_thang WHERE ma_hoa_don = %s", (int(ma_hoa_don),))
        if not inv:
            raise BusinessError(f"Không tìm thấy hóa đơn mã {ma_hoa_don}.")
        self._assert_invoice_access(session, inv)
        if inv.get("trang_thai_hoa_don") == InvoiceStatus.DA_THANH_TOAN.value:
            raise BusinessError("Hóa đơn này đã được thanh toán trước đó.")

        now = datetime.now()
        ttl_seconds = int(payload.get("ttl_seconds") or 300)
        expires_at = now + timedelta(seconds=max(10, ttl_seconds))
        so_tien = float(inv.get("tong_tien_phai_thu") or 0)
        qr_text = (
            f"SMARTPARKING|HD={inv['ma_hoa_don']}|CANHO={inv['ma_can_ho']}"
            f"|KY={inv['ky_thu_thang_nam']}|TIEN={int(round(so_tien))}|TS={int(now.timestamp())}"
        )
        qr_b64 = self._render_qr_base64(qr_text)

        tx_row = db.execute_returning(
            """
            INSERT INTO giao_dich_thanh_toan (
                ma_hoa_don, hinh_thuc_thanh_toan, so_tien_thanh_toan,
                ma_qr, han_ma_qr, trang_thai_giao_dich, thoi_gian_giao_dich
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING ma_giao_dich, ma_hoa_don, hinh_thuc_thanh_toan, so_tien_thanh_toan,
                      ma_qr, han_ma_qr, trang_thai_giao_dich
            """,
            (
                int(ma_hoa_don),
                PaymentMethods.CHUYEN_KHOAN_QR.value,
                so_tien,
                qr_text,
                expires_at,
                TransactionStatus.CHO_XAC_NHAN.value,
                now,
            ),
        )
        return {
            "ma_giao_dich": tx_row["ma_giao_dich"],
            "ma_hoa_don": int(ma_hoa_don),
            "so_tien_thanh_toan": so_tien,
            "ma_qr": qr_text,
            "qr_image_base64": qr_b64,
            "han_ma_qr": expires_at.isoformat(timespec="seconds"),
            "expires_in_seconds": max(10, ttl_seconds),
        }

    def handle_complete_qr_payment(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Workflow 06 (Quét mã thanh toán? Yes -> Thành công | No -> Giao dịch lỗi/quá hạn)."""
        ma_hoa_don = payload.get("ma_hoa_don")
        if ma_hoa_don is None:
            raise BusinessError("Thiếu `ma_hoa_don` để hoàn tất thanh toán QR.")
        ket_qua = str(payload.get("ket_qua") or "thanh_cong").strip()
        if ket_qua not in ("thanh_cong", "loi_qua_han"):
            raise BusinessError("`ket_qua` phải là `thanh_cong` hoặc `loi_qua_han`.")
        ma_giao_dich = payload.get("ma_giao_dich")
        ly_do_loi = str(payload.get("ly_do_loi") or "Mã QR quá hạn hoặc giao dịch bị hủy").strip()

        db = self._db()
        now = datetime.now()

        with db.transaction() as tx:
            inv = tx.fetch_one("SELECT * FROM hoa_don_thang WHERE ma_hoa_don = %s", (int(ma_hoa_don),))
            if not inv:
                raise BusinessError(f"Không tìm thấy hóa đơn mã {ma_hoa_don}.")
            self._assert_invoice_access(session, inv)
            if inv.get("trang_thai_hoa_don") == InvoiceStatus.DA_THANH_TOAN.value:
                raise BusinessError("Hóa đơn này đã được thanh toán trước đó.")

            pending_tx = None
            if ma_giao_dich is not None:
                pending_tx = tx.fetch_one(
                    "SELECT * FROM giao_dich_thanh_toan WHERE ma_giao_dich = %s AND ma_hoa_don = %s",
                    (int(ma_giao_dich), int(ma_hoa_don)),
                )

            # Nếu chọn Yes nhưng mã QR đã hết hạn thì tự động chuyển về nhánh lỗi quá hạn
            expired = False
            if pending_tx and pending_tx.get("han_ma_qr") is not None:
                han = pending_tx["han_ma_qr"]
                if isinstance(han, str):
                    han = datetime.fromisoformat(han)
                if isinstance(han, datetime) and now > han:
                    expired = True

            if ket_qua == "thanh_cong" and not expired:
                ma_bien_lai = self._make_receipt_code("BL-QR", int(ma_hoa_don))
                if pending_tx:
                    gd_row = tx.execute_returning(
                        """
                        UPDATE giao_dich_thanh_toan
                        SET trang_thai_giao_dich = %s,
                            ma_bien_lai = %s,
                            ly_do_loi = NULL,
                            thoi_gian_giao_dich = %s
                        WHERE ma_giao_dich = %s
                        RETURNING *
                        """,
                        (
                            TransactionStatus.THANH_TOAN_THANH_CONG.value,
                            ma_bien_lai,
                            now,
                            int(pending_tx["ma_giao_dich"]),
                        ),
                    )
                else:
                    gd_row = tx.execute_returning(
                        """
                        INSERT INTO giao_dich_thanh_toan (
                            ma_hoa_don, hinh_thuc_thanh_toan, so_tien_thanh_toan,
                            ma_bien_lai, trang_thai_giao_dich, thoi_gian_giao_dich
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            int(ma_hoa_don),
                            PaymentMethods.CHUYEN_KHOAN_QR.value,
                            float(inv.get("tong_tien_phai_thu") or 0),
                            ma_bien_lai,
                            TransactionStatus.THANH_TOAN_THANH_CONG.value,
                            now,
                        ),
                    )
                tx.execute(
                    "UPDATE hoa_don_thang SET trang_thai_hoa_don = %s WHERE ma_hoa_don = %s",
                    (InvoiceStatus.DA_THANH_TOAN.value, int(ma_hoa_don)),
                )
                result_status = InvoiceStatus.DA_THANH_TOAN.value
            else:
                reason = "Mã QR đã quá hạn 5 phút." if expired and ket_qua == "thanh_cong" else ly_do_loi
                if pending_tx:
                    gd_row = tx.execute_returning(
                        """
                        UPDATE giao_dich_thanh_toan
                        SET trang_thai_giao_dich = %s,
                            ly_do_loi = %s,
                            thoi_gian_giao_dich = %s
                        WHERE ma_giao_dich = %s
                        RETURNING *
                        """,
                        (
                            TransactionStatus.GIAO_DICH_LOI_QUA_HAN.value,
                            reason,
                            now,
                            int(pending_tx["ma_giao_dich"]),
                        ),
                    )
                else:
                    gd_row = tx.execute_returning(
                        """
                        INSERT INTO giao_dich_thanh_toan (
                            ma_hoa_don, hinh_thuc_thanh_toan, so_tien_thanh_toan,
                            trang_thai_giao_dich, ly_do_loi, thoi_gian_giao_dich
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            int(ma_hoa_don),
                            PaymentMethods.CHUYEN_KHOAN_QR.value,
                            float(inv.get("tong_tien_phai_thu") or 0),
                            TransactionStatus.GIAO_DICH_LOI_QUA_HAN.value,
                            reason,
                            now,
                        ),
                    )
                result_status = str(inv.get("trang_thai_hoa_don") or InvoiceStatus.CHUA_THANH_TOAN.value)

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="THANH_TOAN_QR",
            doi_tuong="hoa_don_thang",
            ma_doi_tuong=int(ma_hoa_don),
            gia_tri_cu={"trang_thai_hoa_don": inv.get("trang_thai_hoa_don")},
            gia_tri_moi={
                "trang_thai_hoa_don": result_status,
                "trang_thai_giao_dich": gd_row.get("trang_thai_giao_dich"),
                "ma_bien_lai": gd_row.get("ma_bien_lai"),
            },
        )
        return {
            "transaction": gd_row,
            "trang_thai_hoa_don": result_status,
            "success": result_status == InvoiceStatus.DA_THANH_TOAN.value,
        }

    def handle_confirm_cash_payment(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Workflow 06 (Nhánh Tiền mặt): Nhân viên/Quản lý thu tiền tại phòng bảo vệ & cấp biên lai."""
        ma_hoa_don = payload.get("ma_hoa_don")
        if ma_hoa_don is None:
            raise BusinessError("Thiếu `ma_hoa_don` để xác nhận thu tiền mặt.")

        db = self._db()
        now = datetime.now()
        with db.transaction() as tx:
            inv = tx.fetch_one("SELECT * FROM hoa_don_thang WHERE ma_hoa_don = %s", (int(ma_hoa_don),))
            if not inv:
                raise BusinessError(f"Không tìm thấy hóa đơn mã {ma_hoa_don}.")
            if inv.get("trang_thai_hoa_don") == InvoiceStatus.DA_THANH_TOAN.value:
                raise BusinessError("Hóa đơn này đã được thanh toán trước đó.")

            ma_nv_thu = self._resolve_staff_id(tx, session)
            ma_bien_lai = self._make_receipt_code("BL-TM", int(ma_hoa_don))
            so_tien = float(inv.get("tong_tien_phai_thu") or 0)

            gd_row = tx.execute_returning(
                """
                INSERT INTO giao_dich_thanh_toan (
                    ma_hoa_don, hinh_thuc_thanh_toan, so_tien_thanh_toan,
                    ma_bien_lai, ma_nhan_vien_thu, trang_thai_giao_dich, thoi_gian_giao_dich
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    int(ma_hoa_don),
                    PaymentMethods.TIEN_MAT.value,
                    so_tien,
                    ma_bien_lai,
                    ma_nv_thu,
                    TransactionStatus.THANH_TOAN_THANH_CONG.value,
                    now,
                ),
            )
            tx.execute(
                "UPDATE hoa_don_thang SET trang_thai_hoa_don = %s WHERE ma_hoa_don = %s",
                (InvoiceStatus.DA_THANH_TOAN.value, int(ma_hoa_don)),
            )

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="THU_TIEN_MAT_HOA_DON",
            doi_tuong="hoa_don_thang",
            ma_doi_tuong=int(ma_hoa_don),
            gia_tri_cu={"trang_thai_hoa_don": inv.get("trang_thai_hoa_don")},
            gia_tri_moi={
                "trang_thai_hoa_don": InvoiceStatus.DA_THANH_TOAN.value,
                "ma_bien_lai": ma_bien_lai,
                "ma_nhan_vien_thu": ma_nv_thu,
            },
        )
        return {
            "transaction": gd_row,
            "trang_thai_hoa_don": InvoiceStatus.DA_THANH_TOAN.value,
            "ma_bien_lai": ma_bien_lai,
        }

    # ==========================================================================
    # CHỨC NĂNG 1.7.1 - 1.7.4: QUẢN LÝ THEO DÕI THU PHÍ
    # ==========================================================================
    def handle_list_receivables(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.7.1: Xem danh sách khoản phải thu theo kỳ thu, trạng thái và căn hộ."""
        ky_thu = str(payload.get("ky_thu_thang_nam") or "").strip()
        trang_thai = str(payload.get("trang_thai_hoa_don") or "").strip()
        keyword = str(payload.get("keyword") or payload.get("ma_can_ho") or "").strip()

        conditions = ["1=1"]
        params: List[Any] = []
        if ky_thu:
            conditions.append("hd.ky_thu_thang_nam = %s")
            params.append(ky_thu)
        if trang_thai:
            conditions.append("hd.trang_thai_hoa_don = %s")
            params.append(trang_thai)
        if keyword:
            like = f"%{keyword}%"
            conditions.append("(hd.ma_can_ho ILIKE %s OR CAST(hd.ma_hoa_don AS TEXT) ILIKE %s)")
            params.extend([like, like])

        where_sql = " AND ".join(conditions)
        rows = self._db().fetch_all(
            f"""
            SELECT hd.ma_hoa_don,
                   hd.ma_can_ho,
                   ch.toa_nha,
                   ch.so_tang,
                   hd.ky_thu_thang_nam,
                   hd.so_cho_oto_mac_dinh,
                   hd.so_cho_oto_mua_them,
                   hd.so_luong_xe_may,
                   hd.tong_tien_phai_thu,
                   hd.trang_thai_hoa_don,
                   hd.ngay_tao_hoa_don
            FROM hoa_don_thang hd
            JOIN can_ho ch ON ch.ma_can_ho = hd.ma_can_ho
            WHERE {where_sql}
            ORDER BY hd.ngay_tao_hoa_don DESC, hd.ma_can_ho ASC
            """,
            tuple(params),
        )
        return {"total": len(rows), "items": rows}

    def handle_list_collected(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.7.2: Xem danh sách khoản đã thu lọc theo khoảng ngày, nhân viên thu, hình thức."""
        tu_ngay = str(payload.get("tu_ngay") or "").strip()
        den_ngay = str(payload.get("den_ngay") or "").strip()
        hinh_thuc = str(payload.get("hinh_thuc_thanh_toan") or "").strip()
        ma_nv = payload.get("ma_nhan_vien_thu")
        ky_thu = str(payload.get("ky_thu_thang_nam") or "").strip()

        conditions = ["gd.trang_thai_giao_dich = %s"]
        params: List[Any] = [TransactionStatus.THANH_TOAN_THANH_CONG.value]
        if tu_ngay:
            conditions.append("gd.thoi_gian_giao_dich::date >= %s::date")
            params.append(tu_ngay)
        if den_ngay:
            conditions.append("gd.thoi_gian_giao_dich::date <= %s::date")
            params.append(den_ngay)
        if hinh_thuc:
            conditions.append("gd.hinh_thuc_thanh_toan = %s")
            params.append(hinh_thuc)
        if ma_nv not in (None, ""):
            conditions.append("gd.ma_nhan_vien_thu = %s")
            params.append(int(ma_nv))
        if ky_thu:
            conditions.append("hd.ky_thu_thang_nam = %s")
            params.append(ky_thu)

        where_sql = " AND ".join(conditions)
        rows = self._db().fetch_all(
            f"""
            SELECT gd.ma_giao_dich,
                   gd.ma_hoa_don,
                   hd.ma_can_ho,
                   hd.ky_thu_thang_nam,
                   gd.hinh_thuc_thanh_toan,
                   gd.so_tien_thanh_toan,
                   gd.ma_bien_lai,
                   gd.ma_nhan_vien_thu,
                   nv.ho_ten AS ten_nhan_vien_thu,
                   nv.ma_nhan_vien_code,
                   gd.trang_thai_giao_dich,
                   gd.thoi_gian_giao_dich
            FROM giao_dich_thanh_toan gd
            JOIN hoa_don_thang hd ON hd.ma_hoa_don = gd.ma_hoa_don
            LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = gd.ma_nhan_vien_thu
            WHERE {where_sql}
            ORDER BY gd.thoi_gian_giao_dich DESC, gd.ma_giao_dich DESC
            """,
            tuple(params),
        )
        return {"total": len(rows), "items": rows}

    def handle_billing_summary(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.7.3: Tổng hợp thu phí theo kỳ (Tổng phải thu, tổng đã thu, tổng còn nợ)."""
        ky_thu = str(payload.get("ky_thu_thang_nam") or "").strip()
        db = self._db()

        if ky_thu:
            row = db.fetch_one(
                """
                SELECT
                    COALESCE(SUM(tong_tien_phai_thu), 0) AS tong_phai_thu,
                    COALESCE(SUM(CASE WHEN trang_thai_hoa_don = %s THEN tong_tien_phai_thu ELSE 0 END), 0) AS tong_da_thu,
                    COALESCE(SUM(CASE WHEN trang_thai_hoa_don IN (%s, %s) THEN tong_tien_phai_thu ELSE 0 END), 0) AS tong_con_no,
                    COUNT(*) AS tong_hoa_don,
                    COUNT(CASE WHEN trang_thai_hoa_don = %s THEN 1 END) AS so_da_thu,
                    COUNT(CASE WHEN trang_thai_hoa_don IN (%s, %s) THEN 1 END) AS so_con_no
                FROM hoa_don_thang
                WHERE ky_thu_thang_nam = %s
                """,
                (
                    InvoiceStatus.DA_THANH_TOAN.value,
                    InvoiceStatus.CHUA_THANH_TOAN.value,
                    InvoiceStatus.QUA_HAN.value,
                    InvoiceStatus.DA_THANH_TOAN.value,
                    InvoiceStatus.CHUA_THANH_TOAN.value,
                    InvoiceStatus.QUA_HAN.value,
                    ky_thu,
                ),
            )
        else:
            row = db.fetch_one(
                """
                SELECT
                    COALESCE(SUM(tong_tien_phai_thu), 0) AS tong_phai_thu,
                    COALESCE(SUM(CASE WHEN trang_thai_hoa_don = %s THEN tong_tien_phai_thu ELSE 0 END), 0) AS tong_da_thu,
                    COALESCE(SUM(CASE WHEN trang_thai_hoa_don IN (%s, %s) THEN tong_tien_phai_thu ELSE 0 END), 0) AS tong_con_no,
                    COUNT(*) AS tong_hoa_don,
                    COUNT(CASE WHEN trang_thai_hoa_don = %s THEN 1 END) AS so_da_thu,
                    COUNT(CASE WHEN trang_thai_hoa_don IN (%s, %s) THEN 1 END) AS so_con_no
                FROM hoa_don_thang
                """,
                (
                    InvoiceStatus.DA_THANH_TOAN.value,
                    InvoiceStatus.CHUA_THANH_TOAN.value,
                    InvoiceStatus.QUA_HAN.value,
                    InvoiceStatus.DA_THANH_TOAN.value,
                    InvoiceStatus.CHUA_THANH_TOAN.value,
                    InvoiceStatus.QUA_HAN.value,
                ),
            )
        data = row or {}
        return {
            "ky_thu_thang_nam": ky_thu or "Tất cả kỳ",
            "tong_phai_thu": float(data.get("tong_phai_thu") or 0),
            "tong_da_thu": float(data.get("tong_da_thu") or 0),
            "tong_con_no": float(data.get("tong_con_no") or 0),
            "tong_hoa_don": int(data.get("tong_hoa_don") or 0),
            "so_da_thu": int(data.get("so_da_thu") or 0),
            "so_con_no": int(data.get("so_con_no") or 0),
        }

    def handle_get_transaction_detail(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.7.4: Xem chi tiết 1 giao dịch thanh toán kèm hóa đơn, căn hộ, bảng giá và người thu."""
        ma_giao_dich = payload.get("ma_giao_dich")
        if ma_giao_dich is None:
            raise BusinessError("Thiếu `ma_giao_dich` để xem chi tiết thanh toán.")

        db = self._db()
        tx_row = db.fetch_one(
            """
            SELECT gd.*,
                   hd.ma_can_ho, hd.ky_thu_thang_nam, hd.so_cho_oto_mac_dinh,
                   hd.so_cho_oto_mua_them, hd.so_luong_xe_may, hd.tong_tien_phai_thu,
                   hd.trang_thai_hoa_don,
                   ch.toa_nha, ch.so_tang,
                   nv.ho_ten AS ten_nhan_vien_thu, nv.ma_nhan_vien_code
            FROM giao_dich_thanh_toan gd
            JOIN hoa_don_thang hd ON hd.ma_hoa_don = gd.ma_hoa_don
            JOIN can_ho ch ON ch.ma_can_ho = hd.ma_can_ho
            LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = gd.ma_nhan_vien_thu
            WHERE gd.ma_giao_dich = %s
            """,
            (int(ma_giao_dich),),
        )
        if not tx_row:
            raise BusinessError(f"Không tìm thấy giao dịch mã {ma_giao_dich}.")

        rates = self._load_active_monthly_rates(db)
        return {
            "transaction": tx_row,
            "applied_rates": rates,
        }


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
    router.register(
        Actions.BILLING_CREATE_QR_PAYMENT,
        handler.handle_create_qr_payment,
        allowed_roles=[Roles.CU_DAN.value, Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_COMPLETE_QR_PAYMENT,
        handler.handle_complete_qr_payment,
        allowed_roles=[Roles.CU_DAN.value, Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_CONFIRM_CASH_PAYMENT,
        handler.handle_confirm_cash_payment,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_LIST_RECEIVABLES,
        handler.handle_list_receivables,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_LIST_COLLECTED,
        handler.handle_list_collected,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_SUMMARY,
        handler.handle_billing_summary,
        allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.BILLING_GET_TRANSACTION_DETAIL,
        handler.handle_get_transaction_detail,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    return handler
