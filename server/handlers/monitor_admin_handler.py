"""Handler nghiệp vụ cho Dashboard Quản lý (1.1) và cụm Admin Read-Only (4.2, 4.3, 4.4)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession, SessionManager
from server.core.state_store import StateStore
from shared.constants import (
    InvoiceStatus,
    LogLevels,
    RequestStatus,
    Roles,
    SlotStatus,
    TransactionStatus,
    ZoneStatus,
)
from shared.protocol import Actions


def _resolve_db(db_provider: Any = None) -> Any:
    """Lấy module/provider DB nếu đã sẵn sàng; trả về None nếu chưa khởi tạo."""
    if db_provider is not None:
        return db_provider
    try:
        from server import db as default_db

        return default_db
    except ImportError:
        return None


def _safe_fetch_one(db: Any, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    if db is None:
        return None
    try:
        return db.fetch_one(sql, params)
    except Exception:
        return None


def _safe_fetch_all(db: Any, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    if db is None:
        return []
    try:
        return db.fetch_all(sql, params)
    except Exception:
        return []


class MonitorAdminHandler:
    """Xử lý các API Dashboard Quản lý (1.1) và Giám sát hệ thống Admin (4.2–4.4)."""

    def __init__(
        self,
        session_manager: SessionManager,
        file_logger: FileLogger,
        state_store: StateStore,
        db_provider: Any = None,
    ) -> None:
        self.session_manager = session_manager
        self.file_logger = file_logger
        self.state_store = state_store
        self._db_provider = db_provider

    def _db(self) -> Any:
        return _resolve_db(self._db_provider)

    # ==========================================================================
    # NHÓM 1.1: DASHBOARD TỔNG QUAN QUẢN LÝ (1.1.1 - 1.1.4)
    # ==========================================================================
    def handle_dashboard_summary(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Tổng hợp 4 nhóm KPI Dashboard cho Quản lý chung cư (1.1.1 – 1.1.4)."""
        db = self._db()
        ky_thu = str(payload.get("ky_thu_thang_nam") or datetime.now().strftime("%m/%Y"))
        today_str = datetime.now().date().isoformat()

        pending_requests = self._collect_pending_requests(db)
        parking_capacity = self._collect_parking_capacity(db)
        revenue_summary = self._collect_revenue_summary(db, ky_thu, today_str)
        system_alerts = self._collect_system_alerts(db)

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db_connected": db is not None,
            "ky_thu_thang_nam": ky_thu,
            "pending_requests": pending_requests,   # 1.1.1
            "parking_capacity": parking_capacity,   # 1.1.2
            "revenue_summary": revenue_summary,     # 1.1.3
            "system_alerts": system_alerts,         # 1.1.4
        }

    def _collect_pending_requests(self, db: Any) -> Dict[str, Any]:
        """1.1.1 Yêu cầu đang chờ duyệt (đăng ký/sửa/ngừng xe & mua thêm chỗ)."""
        vehicle_reqs = _safe_fetch_all(
            db,
            """
            SELECT y.ma_yeu_cau_xe AS ma_yeu_cau,
                   'dang_ky_xe' AS nhom_yeu_cau,
                   y.loai_yeu_cau,
                   y.ngay_gui_yeu_cau AS thoi_gian_gui,
                   cd.ho_ten AS ten_cu_dan,
                   cd.ma_can_ho,
                   pt.bien_so_xe
            FROM yeu_cau_dang_ky_xe y
            JOIN cu_dan cd ON cd.ma_cu_dan = y.ma_cu_dan
            LEFT JOIN phuong_tien pt ON pt.ma_phuong_tien = y.ma_phuong_tien
            WHERE y.trang_thai_duyet = %s
            ORDER BY y.ngay_gui_yeu_cau DESC
            LIMIT 20
            """,
            (RequestStatus.CHO_DUYET.value,),
        )
        slot_reqs = _safe_fetch_all(
            db,
            """
            SELECT y.ma_yeu_cau_cho AS ma_yeu_cau,
                   'mua_them_cho' AS nhom_yeu_cau,
                   'mua_them_cho' AS loai_yeu_cau,
                   y.ngay_gui_yeu_cau AS thoi_gian_gui,
                   cd.ho_ten AS ten_cu_dan,
                   y.ma_can_ho,
                   y.nhu_cau_vi_tri AS bien_so_xe
            FROM yeu_cau_mua_them_cho y
            JOIN cu_dan cd ON cd.ma_cu_dan = y.ma_cu_dan
            WHERE y.trang_thai_duyet = %s
            ORDER BY y.ngay_gui_yeu_cau DESC
            LIMIT 20
            """,
            (RequestStatus.CHO_DUYET.value,),
        )
        combined = sorted(
            [*vehicle_reqs, *slot_reqs],
            key=lambda item: str(item.get("thoi_gian_gui") or ""),
            reverse=True,
        )
        return {
            "so_yeu_cau_xe": len(vehicle_reqs),
            "so_yeu_cau_mua_cho": len(slot_reqs),
            "tong_cho_duyet": len(vehicle_reqs) + len(slot_reqs),
            "items": combined[:20],
        }

    def _collect_parking_capacity(self, db: Any) -> Dict[str, Any]:
        """1.1.2 Xe đang gửi và chỗ còn trống theo khu vực & vị trí đỗ."""
        zones = _safe_fetch_all(
            db,
            """
            SELECT ma_khu_vuc, ten_khu_vuc, ma_loai_xe, tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc
            FROM khu_vuc
            ORDER BY ma_khu_vuc
            """,
        )
        slot_stats = _safe_fetch_all(
            db,
            """
            SELECT trang_thai_vi_tri, COUNT(*) AS so_luong
            FROM vi_tri_do
            GROUP BY trang_thai_vi_tri
            """,
        )
        resident_in_row = _safe_fetch_one(
            db,
            "SELECT COUNT(*) AS so_luong FROM quan_ly_phuong_tien_ra_vao WHERE dang_trong_bai = TRUE",
        )

        slot_counts = {
            SlotStatus.TRONG.value: 0,
            SlotStatus.DA_PHAN_CHO.value: 0,
            SlotStatus.DANG_CO_XE.value: 0,
            SlotStatus.NGUNG_SU_DUNG.value: 0,
        }
        for row in slot_stats:
            status = str(row.get("trang_thai_vi_tri") or "")
            if status in slot_counts:
                slot_counts[status] = int(row.get("so_luong") or 0)

        visitor_in_count = self.state_store.count_open_visitors()
        resident_in_count = int((resident_in_row or {}).get("so_luong") or 0)
        tong_suc_chua = sum(int(z.get("tong_so_cho") or 0) for z in zones)
        tong_dang_gui_khu_vuc = sum(int(z.get("so_xe_dang_gui") or 0) for z in zones)
        tong_xe_dang_gui = max(tong_dang_gui_khu_vuc, resident_in_count + visitor_in_count)
        cho_con_trong = max(0, tong_suc_chua - tong_xe_dang_gui) if tong_suc_chua > 0 else slot_counts[SlotStatus.TRONG.value]

        return {
            "tong_suc_chua": tong_suc_chua,
            "tong_xe_dang_gui": tong_xe_dang_gui,
            "xe_cu_dan_dang_gui": resident_in_count,
            "xe_vang_lai_dang_gui": visitor_in_count,
            "cho_con_trong": cho_con_trong,
            "chi_tiet_vi_tri": slot_counts,
            "khu_vuc": zones,
        }

    def _collect_revenue_summary(
        self, db: Any, ky_thu: str, today_str: str
    ) -> Dict[str, Any]:
        """1.1.3 Doanh thu và phí cư dân (hóa đơn tháng + giao dịch + vãng lai)."""
        invoice_row = _safe_fetch_one(
            db,
            """
            SELECT
                COALESCE(SUM(tong_tien_phai_thu), 0) AS tong_phai_thu,
                COALESCE(SUM(CASE WHEN trang_thai_hoa_don = %s THEN tong_tien_phai_thu ELSE 0 END), 0) AS da_thu_cu_dan,
                COALESCE(SUM(CASE WHEN trang_thai_hoa_don IN (%s, %s) THEN tong_tien_phai_thu ELSE 0 END), 0) AS con_no_cu_dan,
                COUNT(CASE WHEN trang_thai_hoa_don = %s THEN 1 END) AS so_hoa_don_da_thu,
                COUNT(CASE WHEN trang_thai_hoa_don IN (%s, %s) THEN 1 END) AS so_hoa_don_chua_thu
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
        visitor_row = _safe_fetch_one(
            db,
            """
            SELECT COALESCE(SUM(tong_tien_thu_vang_lai), 0) AS doanh_thu_vang_lai
            FROM bao_cao_luot_va_thu_phi
            WHERE ngay_ghi_nhan = %s
            """,
            (today_str,),
        )
        tx_month_row = _safe_fetch_one(
            db,
            """
            SELECT COALESCE(SUM(so_tien_thanh_toan), 0) AS tong_giao_dich_thanh_cong
            FROM giao_dich_thanh_toan g
            JOIN hoa_don_thang h ON h.ma_hoa_don = g.ma_hoa_don
            WHERE h.ky_thu_thang_nam = %s AND g.trang_thai_giao_dich = %s
            """,
            (ky_thu, TransactionStatus.THANH_TOAN_THANH_CONG.value),
        )

        da_thu_cu_dan = float((invoice_row or {}).get("da_thu_cu_dan") or 0)
        doanh_thu_vang_lai = float((visitor_row or {}).get("doanh_thu_vang_lai") or 0)
        return {
            "ky_thu_thang_nam": ky_thu,
            "tong_phai_thu_cu_dan": float((invoice_row or {}).get("tong_phai_thu") or 0),
            "da_thu_cu_dan": da_thu_cu_dan,
            "con_no_cu_dan": float((invoice_row or {}).get("con_no_cu_dan") or 0),
            "so_hoa_don_da_thu": int((invoice_row or {}).get("so_hoa_don_da_thu") or 0),
            "so_hoa_don_chua_thu": int((invoice_row or {}).get("so_hoa_don_chua_thu") or 0),
            "tong_giao_dich_thanh_cong": float((tx_month_row or {}).get("tong_giao_dich_thanh_cong") or 0),
            "doanh_thu_vang_lai_hom_nay": doanh_thu_vang_lai,
            "tong_doanh_thu_hien_thi": da_thu_cu_dan + doanh_thu_vang_lai,
        }

    def _collect_system_alerts(self, db: Any) -> Dict[str, Any]:
        """1.1.4 Cảnh báo hệ thống và bãi xe từ error_*.jsonl và khu_vuc/vi_tri_do."""
        recent_logs = self.file_logger.query_logs("error", limit=50)
        alert_items: List[Dict[str, Any]] = []

        for entry in recent_logs:
            level = str(entry.get("muc_do") or LogLevels.INFO.value)
            if level in (LogLevels.WARN.value, LogLevels.ERROR.value):
                alert_items.append(
                    {
                        "timestamp": entry.get("timestamp", ""),
                        "muc_do": level,
                        "nguon": entry.get("nguon", "system"),
                        "ma_loi": entry.get("ma_loi", ""),
                        "noi_dung": entry.get("noi_dung", ""),
                    }
                )

        stopped_zones = _safe_fetch_all(
            db,
            """
            SELECT ma_khu_vuc, ten_khu_vuc, ly_do_ngung
            FROM khu_vuc
            WHERE trang_thai_khu_vuc = %s
            """,
            (ZoneStatus.NGUNG_SU_DUNG.value,),
        )
        for z in stopped_zones:
            alert_items.append(
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "muc_do": LogLevels.WARN.value,
                    "nguon": "khu_vuc",
                    "ma_loi": "ZONE_DISABLED",
                    "noi_dung": f"Khu vực {z.get('ma_khu_vuc')} đang ngừng sử dụng: {z.get('ly_do_ngung') or 'Không rõ lý do'}",
                }
            )

        stopped_slots = _safe_fetch_all(
            db,
            """
            SELECT ma_vi_tri, ma_khu_vuc, ly_do_ngung, thoi_gian_cap_nhat
            FROM vi_tri_do
            WHERE trang_thai_vi_tri = %s
            ORDER BY thoi_gian_cap_nhat DESC
            LIMIT 10
            """,
            (SlotStatus.NGUNG_SU_DUNG.value,),
        )
        for s in stopped_slots:
            alert_items.append(
                {
                    "timestamp": str(s.get("thoi_gian_cap_nhat") or ""),
                    "muc_do": LogLevels.WARN.value,
                    "nguon": "vi_tri_do",
                    "ma_loi": "SLOT_DISABLED",
                    "noi_dung": f"Vị trí {s.get('ma_vi_tri')} ({s.get('ma_khu_vuc')}) ngừng sử dụng: {s.get('ly_do_ngung') or 'Bảo trì'}",
                }
            )

        alert_items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return {
            "tong_canh_bao": len(alert_items),
            "items": alert_items[:25],
        }

    # ==========================================================================
    # NHÓM 4.2: ADMIN XEM & TRA CỨU TÀI KHOẢN HỆ THỐNG (4.2.1 - 4.2.5)
    # ==========================================================================
    def handle_admin_list_accounts(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.2.1 / 4.2.3 / 4.2.4: Admin xem danh sách tài khoản hệ thống (ẩn mat_khau_bam)."""
        db = self._db()
        vai_tro = str(payload.get("vai_tro") or "").strip()
        if payload.get("only_managers"):
            vai_tro = Roles.QUAN_LY.value
        trang_thai = str(payload.get("trang_thai") or "").strip()
        keyword = str(payload.get("keyword") or payload.get("ten_dang_nhap") or "").strip()

        conditions = ["1=1"]
        params: List[Any] = []
        if vai_tro:
            conditions.append("tk.vai_tro = %s")
            params.append(vai_tro)
        if trang_thai:
            conditions.append("tk.trang_thai = %s")
            params.append(trang_thai)
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                "(tk.ten_dang_nhap ILIKE %s OR COALESCE(cd.ho_ten, nv.ho_ten, '') ILIKE %s "
                "OR COALESCE(cd.email, nv.email, '') ILIKE %s)"
            )
            params.extend([like, like, like])

        where_sql = " AND ".join(conditions)
        rows = _safe_fetch_all(
            db,
            f"""
            SELECT tk.ma_tai_khoan,
                   tk.ten_dang_nhap,
                   tk.vai_tro,
                   tk.trang_thai,
                   tk.ly_do_khoa,
                   tk.ngay_tao,
                   COALESCE(cd.ho_ten, nv.ho_ten, tk.ten_dang_nhap) AS ho_ten,
                   COALESCE(cd.so_dien_thoai, nv.so_dien_thoai, '') AS so_dien_thoai,
                   COALESCE(cd.email, nv.email, '') AS email,
                   cd.ma_can_ho,
                   nv.ma_nhan_vien_code,
                   nv.chuc_vu
            FROM tai_khoan tk
            LEFT JOIN cu_dan cd ON cd.ma_tai_khoan = tk.ma_tai_khoan
            LEFT JOIN nhan_vien nv ON nv.ma_tai_khoan = tk.ma_tai_khoan
            WHERE {where_sql}
            ORDER BY tk.ma_tai_khoan ASC
            """,
            tuple(params),
        )
        return {
            "db_connected": db is not None,
            "total": len(rows),
            "items": rows,
        }

    def handle_admin_get_account(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.2.2 / 4.2.3 / 4.2.4: Admin xem chi tiết tài khoản, trạng thái khóa & đăng nhập gần nhất."""
        ma_tai_khoan = payload.get("ma_tai_khoan")
        if ma_tai_khoan is None:
            raise BusinessError("Thiếu `ma_tai_khoan` để xem chi tiết.")

        db = self._db()
        account = _safe_fetch_one(
            db,
            """
            SELECT tk.ma_tai_khoan,
                   tk.ten_dang_nhap,
                   tk.vai_tro,
                   tk.trang_thai,
                   tk.ly_do_khoa,
                   tk.ngay_tao,
                   COALESCE(cd.ho_ten, nv.ho_ten, tk.ten_dang_nhap) AS ho_ten,
                   COALESCE(cd.so_cccd, nv.so_cccd, '') AS so_cccd,
                   COALESCE(cd.so_dien_thoai, nv.so_dien_thoai, '') AS so_dien_thoai,
                   COALESCE(cd.email, nv.email, '') AS email,
                   cd.ma_cu_dan,
                   cd.ma_can_ho,
                   cd.dia_chi,
                   nv.ma_nhan_vien,
                   nv.ma_nhan_vien_code,
                   nv.chuc_vu
            FROM tai_khoan tk
            LEFT JOIN cu_dan cd ON cd.ma_tai_khoan = tk.ma_tai_khoan
            LEFT JOIN nhan_vien nv ON nv.ma_tai_khoan = tk.ma_tai_khoan
            WHERE tk.ma_tai_khoan = %s
            """,
            (int(ma_tai_khoan),),
        )
        if db is not None and account is None:
            raise BusinessError(f"Không tìm thấy tài khoản mã {ma_tai_khoan}.")

        last_auth = self.file_logger.get_last_auth_for_account(int(ma_tai_khoan))
        active_sessions = [
            s for s in self.session_manager.list_sessions()
            if s.get("ma_tai_khoan") == int(ma_tai_khoan)
        ]
        return {
            "db_connected": db is not None,
            "account": account,
            "last_login": last_auth,
            "active_sessions": active_sessions,
        }

    def handle_admin_account_history(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.2.5: Tra cứu lịch sử trạng thái (khóa/mở khóa) của tài khoản từ audit_*.jsonl."""
        ma_tai_khoan = payload.get("ma_tai_khoan")
        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        limit = int(payload.get("limit") or 100)

        filters: Dict[str, Any] = {"doi_tuong": "tai_khoan"}
        if ma_tai_khoan is not None and str(ma_tai_khoan).strip() != "":
            filters["ma_doi_tuong"] = int(ma_tai_khoan)

        items = self.file_logger.query_logs(
            "audit",
            start_date=start_date,
            end_date=end_date,
            filters=filters,
            limit=limit,
        )
        return {
            "total": len(items),
            "items": items,
        }

    # ==========================================================================
    # NHÓM 4.3: ADMIN XEM PHIÊN KẾT NỐI TRÊN RAM (4.3.1 - 4.3.2)
    # ==========================================================================
    def handle_admin_list_sessions(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.3.1: Xem danh sách các phiên kết nối TCP đang hoạt động trên RAM Server."""
        vai_tro = payload.get("vai_tro")
        ten_dang_nhap = payload.get("ten_dang_nhap")
        authenticated_only = bool(payload.get("authenticated_only", False))
        sessions = self.session_manager.list_sessions(
            vai_tro=vai_tro,
            ten_dang_nhap=ten_dang_nhap,
            authenticated_only=authenticated_only,
        )
        return {
            "total": len(sessions),
            "items": sessions,
        }

    def handle_admin_get_session(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.3.2: Xem chi tiết một phiên kết nối theo `session_id`."""
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise BusinessError("Thiếu `session_id` để xem chi tiết phiên kết nối.")
        target = self.session_manager.get_session(session_id)
        if target is None:
            raise BusinessError(f"Phiên kết nối '{session_id}' không còn tồn tại trên RAM.")
        return {"session": target.to_public_dict()}

    # ==========================================================================
    # NHÓM 4.4: ADMIN XEM NHẬT KÝ HỆ THỐNG (4.4.1 - 4.4.3)
    # ==========================================================================
    def handle_admin_query_logs(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4.4.1 / 4.4.2 / 4.4.3: Truy vấn nhật ký thao tác, đăng nhập và lỗi hệ thống."""
        log_type = str(payload.get("log_type") or "audit").strip()
        if log_type not in ("audit", "auth", "error", "parking_history"):
            raise BusinessError(f"Loại nhật ký không hợp lệ: '{log_type}'.")

        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        limit = int(payload.get("limit") or 200)

        filters: Dict[str, Any] = {}
        for key in (
            "ma_tai_khoan",
            "ten_dang_nhap",
            "vai_tro",
            "loai_thao_tac",
            "doi_tuong",
            "ket_qua",
            "muc_do",
            "nguon",
            "keyword",
        ):
            if payload.get(key) not in (None, ""):
                filters[key] = payload[key]

        items = self.file_logger.query_logs(
            log_type=log_type,
            start_date=start_date,
            end_date=end_date,
            filters=filters,
            limit=limit,
        )
        return {
            "log_type": log_type,
            "total": len(items),
            "items": items,
        }

    # ==========================================================================
    # NHÓM 1.6 & 2.6: GIÁM SÁT BÃI XE 2D/3D (MINH)
    # ==========================================================================
    def _collect_active_parking_items(
        self,
        db: Any,
        ma_khu_vuc: str = "",
        bien_so_xe: str = "",
        loai_khach: str = "",
        ma_loai_xe: str = "",
    ) -> List[Dict[str, Any]]:
        """Gộp danh sách xe cư dân đang trong bãi (CSDL) và xe vãng lai (StateStore)."""
        items: List[Dict[str, Any]] = []
        plate_filter = bien_so_xe.strip().upper()
        zone_filter = ma_khu_vuc.strip().upper()
        type_filter = ma_loai_xe.strip().upper()
        guest_filter = loai_khach.strip().lower()

        if guest_filter in ("", "cu_dan"):
            resident_rows = _safe_fetch_all(
                db,
                """
                SELECT ql.ma_quan_ly_xe,
                       ql.ma_phuong_tien,
                       COALESCE(v.ma_khu_vuc, ql.ma_khu_vuc_phan_cong) AS ma_khu_vuc,
                       ql.ma_vi_tri_co_dinh AS ma_vi_tri,
                       ql.thoi_gian_vao,
                       pt.bien_so_xe,
                       pt.ma_loai_xe,
                       pt.ten_hang_dong_xe,
                       pt.mau_xe,
                       cd.ho_ten AS ten_chu_xe,
                       cd.ma_can_ho
                FROM quan_ly_phuong_tien_ra_vao ql
                JOIN phuong_tien pt ON pt.ma_phuong_tien = ql.ma_phuong_tien
                JOIN cu_dan cd ON cd.ma_cu_dan = pt.ma_cu_dan
                LEFT JOIN vi_tri_do v ON v.ma_vi_tri = ql.ma_vi_tri_co_dinh
                WHERE ql.dang_trong_bai = TRUE
                ORDER BY ql.thoi_gian_vao DESC NULLS LAST, pt.bien_so_xe ASC
                """,
            )
            for row in resident_rows:
                items.append(
                    {
                        "ma_luot_gui": f"CD-{row.get('ma_quan_ly_xe')}",
                        "loai_khach": "cu_dan",
                        "bien_so_xe": row.get("bien_so_xe") or "",
                        "ma_loai_xe": row.get("ma_loai_xe") or "",
                        "ma_khu_vuc": row.get("ma_khu_vuc") or "",
                        "ma_vi_tri": row.get("ma_vi_tri") or "",
                        "thoi_gian_vao": str(row.get("thoi_gian_vao") or ""),
                        "ten_chu_xe": row.get("ten_chu_xe") or "",
                        "ma_can_ho": row.get("ma_can_ho") or "",
                        "ten_hang_dong_xe": row.get("ten_hang_dong_xe") or "",
                    }
                )

        if guest_filter in ("", "vang_lai"):
            visitor_rows = self.state_store.list_open_visitor_sessions()
            for row in visitor_rows:
                items.append(
                    {
                        "ma_luot_gui": row.get("ma_luot_gui") or "",
                        "loai_khach": "vang_lai",
                        "bien_so_xe": row.get("bien_so_xe") or "",
                        "ma_loai_xe": row.get("ma_loai_xe") or "",
                        "ma_khu_vuc": row.get("ma_khu_vuc") or "",
                        "ma_vi_tri": row.get("ma_vi_tri") or "",
                        "thoi_gian_vao": str(row.get("thoi_gian_vao") or ""),
                        "ten_chu_xe": "Khách vãng lai",
                        "ma_can_ho": "-",
                        "ten_hang_dong_xe": "",
                    }
                )

        filtered: List[Dict[str, Any]] = []
        for it in items:
            if zone_filter and str(it.get("ma_khu_vuc") or "").upper() != zone_filter:
                continue
            if type_filter and str(it.get("ma_loai_xe") or "").upper() != type_filter:
                continue
            if plate_filter and plate_filter not in str(it.get("bien_so_xe") or "").upper():
                continue
            filtered.append(it)

        filtered.sort(key=lambda x: str(x.get("thoi_gian_vao") or ""), reverse=True)
        return filtered

    def handle_monitor_active_parkings(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.6.1: Xem danh sách xe đang gửi trong bãi (cư dân + khách vãng lai)."""
        db = self._db()
        items = self._collect_active_parking_items(
            db,
            ma_khu_vuc=str(payload.get("ma_khu_vuc") or ""),
            bien_so_xe=str(payload.get("bien_so_xe") or ""),
            loai_khach=str(payload.get("loai_khach") or ""),
            ma_loai_xe=str(payload.get("ma_loai_xe") or ""),
        )
        res_cnt = sum(1 for x in items if x.get("loai_khach") == "cu_dan")
        vis_cnt = sum(1 for x in items if x.get("loai_khach") == "vang_lai")
        return {
            "total": len(items),
            "resident_count": res_cnt,
            "visitor_count": vis_cnt,
            "items": items,
        }

    def handle_monitor_available_slots(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.6.2: Xem các chỗ đỗ đang trống (hoặc đã phân chỗ nhưng hiện chưa có xe đỗ)."""
        db = self._db()
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()
        include_allocated = bool(payload.get("include_allocated", False))
        allowed_statuses = (
            [SlotStatus.TRONG.value, SlotStatus.DA_PHAN_CHO.value]
            if include_allocated
            else [SlotStatus.TRONG.value]
        )

        placeholders = ", ".join(["%s"] * len(allowed_statuses))
        conditions = [f"v.trang_thai_vi_tri IN ({placeholders})"]
        params: List[Any] = list(allowed_statuses)
        if ma_khu_vuc:
            conditions.append("v.ma_khu_vuc = %s")
            params.append(ma_khu_vuc)

        where_sql = " AND ".join(conditions)
        rows = _safe_fetch_all(
            db,
            f"""
            SELECT v.ma_vi_tri,
                   v.ma_khu_vuc,
                   kv.ten_khu_vuc,
                   kv.ma_loai_xe,
                   v.ten_vi_tri,
                   v.nhom_vi_tri,
                   v.trang_thai_vi_tri,
                   v.thoi_gian_cap_nhat
            FROM vi_tri_do v
            JOIN khu_vuc kv ON kv.ma_khu_vuc = v.ma_khu_vuc
            WHERE {where_sql}
            ORDER BY v.ma_khu_vuc ASC, v.ma_vi_tri ASC
            """,
            tuple(params),
        )
        return {
            "total": len(rows),
            "items": rows,
        }

    def handle_monitor_occupied_slots(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.6.3: Xem các vị trí đang có xe kèm biển số & thông tin xe đang đỗ."""
        overview = self.handle_monitor_overview_map(_session, payload)
        bien_so_filter = str(payload.get("bien_so_xe") or "").strip().upper()
        occupied = [
            s
            for s in overview.get("slots", [])
            if s.get("trang_thai_vi_tri") == SlotStatus.DANG_CO_XE.value
            and (
                not bien_so_filter
                or bien_so_filter in str(s.get("bien_so_xe") or "").upper()
            )
        ]
        return {
            "total": len(occupied),
            "items": occupied,
        }

    def handle_monitor_overview_map(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.6.4 & 2.6.1 / 2.6.2: Trả về tổng quan khu vực và toàn bộ ô đỗ để render 2D/3D."""
        db = self._db()
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()
        status_filter = str(payload.get("trang_thai_vi_tri") or "").strip()

        zones = _safe_fetch_all(
            db,
            """
            SELECT ma_khu_vuc, ten_khu_vuc, ma_loai_xe, tong_so_cho,
                   so_xe_dang_gui, trang_thai_khu_vuc, ly_do_ngung
            FROM khu_vuc
            ORDER BY ma_khu_vuc ASC
            """,
        )

        slot_conditions = ["1=1"]
        slot_params: List[Any] = []
        if ma_khu_vuc:
            slot_conditions.append("v.ma_khu_vuc = %s")
            slot_params.append(ma_khu_vuc)
        if status_filter:
            slot_conditions.append("v.trang_thai_vi_tri = %s")
            slot_params.append(status_filter)

        raw_slots = _safe_fetch_all(
            db,
            f"""
            SELECT v.ma_vi_tri,
                   v.ma_khu_vuc,
                   kv.ten_khu_vuc,
                   kv.ma_loai_xe,
                   v.ten_vi_tri,
                   v.nhom_vi_tri,
                   v.trang_thai_vi_tri,
                   v.ly_do_ngung,
                   v.thoi_gian_cap_nhat
            FROM vi_tri_do v
            JOIN khu_vuc kv ON kv.ma_khu_vuc = v.ma_khu_vuc
            WHERE {" AND ".join(slot_conditions)}
            ORDER BY v.ma_khu_vuc ASC, v.ma_vi_tri ASC
            """,
            tuple(slot_params),
        )

        # Map biển số đang đỗ hoặc đã phân chỗ vào từng vị trí
        active_items = self._collect_active_parking_items(db)
        active_by_slot: Dict[str, Dict[str, Any]] = {}
        for item in active_items:
            slot_code = str(item.get("ma_vi_tri") or "").strip()
            if slot_code:
                active_by_slot[slot_code] = item

        alloc_rows = _safe_fetch_all(
            db,
            """
            SELECT pb.ma_vi_tri,
                   pb.ma_can_ho,
                   pb.loai_cho_cap,
                   pt.bien_so_xe,
                   cd.ho_ten AS ten_chu_xe
            FROM phan_bo_cho_do pb
            LEFT JOIN quan_ly_phuong_tien_ra_vao ql ON ql.ma_vi_tri_co_dinh = pb.ma_vi_tri
            LEFT JOIN phuong_tien pt ON pt.ma_phuong_tien = ql.ma_phuong_tien
            LEFT JOIN cu_dan cd ON cd.ma_cu_dan = pt.ma_cu_dan
            WHERE pb.trang_thai_su_dung = 'dang_hieu_luc'
            """,
        )
        alloc_by_slot: Dict[str, Dict[str, Any]] = {
            str(r.get("ma_vi_tri")): r for r in alloc_rows if r.get("ma_vi_tri")
        }

        enriched_slots: List[Dict[str, Any]] = []
        counts = {
            SlotStatus.TRONG.value: 0,
            SlotStatus.DA_PHAN_CHO.value: 0,
            SlotStatus.DANG_CO_XE.value: 0,
            SlotStatus.NGUNG_SU_DUNG.value: 0,
        }
        for s in raw_slots:
            code = str(s.get("ma_vi_tri") or "")
            st = str(s.get("trang_thai_vi_tri") or SlotStatus.TRONG.value)
            if st in counts:
                counts[st] += 1

            active = active_by_slot.get(code)
            alloc = alloc_by_slot.get(code)
            bien_so = (active or {}).get("bien_so_xe") or (alloc or {}).get("bien_so_xe") or ""
            ten_chu = (active or {}).get("ten_chu_xe") or (alloc or {}).get("ten_chu_xe") or ""
            ma_can_ho = (active or {}).get("ma_can_ho") or (alloc or {}).get("ma_can_ho") or ""

            enriched_slots.append(
                {
                    **s,
                    "bien_so_xe": bien_so,
                    "loai_khach": (active or {}).get("loai_khach") or ("cu_dan" if alloc else ""),
                    "ten_chu_xe": ten_chu,
                    "ma_can_ho": ma_can_ho,
                    "thoi_gian_vao": (active or {}).get("thoi_gian_vao") or "",
                }
            )

        cap_summary = self._collect_parking_capacity(db)
        return {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "zones": zones,
            "slots": enriched_slots,
            "active_parkings": active_items,
            "summary": {
                "tong_suc_chua": cap_summary.get("tong_suc_chua", 0),
                "tong_xe_dang_gui": cap_summary.get("tong_xe_dang_gui", 0),
                "xe_cu_dan_dang_gui": cap_summary.get("xe_cu_dan_dang_gui", 0),
                "xe_vang_lai_dang_gui": cap_summary.get("xe_vang_lai_dang_gui", 0),
                "cho_con_trong": cap_summary.get("cho_con_trong", 0),
                **counts,
            },
        }

    # ==========================================================================
    # NHÓM 1.8: THỐNG KÊ LƯỢT XE, CƯ DÂN VS VÃNG LAI & DOANH THU (MINH)
    # ==========================================================================
    @staticmethod
    def _in_date_range(day_str: str, start_date: str, end_date: str) -> bool:
        d = (day_str or "")[:10]
        if not d:
            return False
        if start_date and d < start_date[:10]:
            return False
        if end_date and d > end_date[:10]:
            return False
        return True

    def _query_all_parking_history(
        self,
        start_date: str = "",
        end_date: str = "",
        ma_khu_vuc: str = "",
    ) -> List[Dict[str, Any]]:
        filters: Dict[str, Any] = {}
        if ma_khu_vuc:
            filters["ma_khu_vuc"] = ma_khu_vuc
        return self.file_logger.query_logs(
            "parking_history",
            start_date=start_date or None,
            end_date=end_date or None,
            filters=filters,
            limit=0,
        )

    def handle_stats_vehicle_turns(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.8.1: Thống kê lượt xe vào/ra theo ngày và khu vực."""
        start_date = str(payload.get("start_date") or "").strip()
        end_date = str(payload.get("end_date") or "").strip()
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()

        history_rows = self._query_all_parking_history(start_date, end_date, ma_khu_vuc)
        by_day: Dict[str, Dict[str, Any]] = {}

        for rec in history_rows:
            in_day = str(rec.get("thoi_gian_vao") or rec.get("timestamp") or "")[:10]
            if in_day and self._in_date_range(in_day, start_date, end_date):
                bucket = by_day.setdefault(
                    in_day, {"ngay": in_day, "luot_vao": 0, "luot_ra": 0, "bao_cao_ca": 0}
                )
                bucket["luot_vao"] += 1

            out_raw = rec.get("thoi_gian_ra")
            if out_raw or rec.get("trang_thai") == "da_ra_bai":
                out_day = str(out_raw or rec.get("timestamp") or "")[:10]
                if out_day and self._in_date_range(out_day, start_date, end_date):
                    bucket = by_day.setdefault(
                        out_day, {"ngay": out_day, "luot_vao": 0, "luot_ra": 0, "bao_cao_ca": 0}
                    )
                    bucket["luot_ra"] += 1

        db = self._db()
        conds = ["1=1"]
        params: List[Any] = []
        if start_date:
            conds.append("ngay_ghi_nhan >= %s")
            params.append(start_date[:10])
        if end_date:
            conds.append("ngay_ghi_nhan <= %s")
            params.append(end_date[:10])
        if ma_khu_vuc:
            conds.append("ma_khu_vuc = %s")
            params.append(ma_khu_vuc)

        report_rows = _safe_fetch_all(
            db,
            f"""
            SELECT ngay_ghi_nhan,
                   COALESCE(SUM(so_luot_oto_vang_lai + so_luot_xemay_vang_lai + so_luot_xe_cu_dan), 0) AS tong_luot
            FROM bao_cao_luot_va_thu_phi
            WHERE {" AND ".join(conds)}
            GROUP BY ngay_ghi_nhan
            ORDER BY ngay_ghi_nhan ASC
            """,
            tuple(params),
        )
        for rr in report_rows:
            d = str(rr.get("ngay_ghi_nhan") or "")[:10]
            if not d:
                continue
            rep_turns = int(rr.get("tong_luot") or 0)
            bucket = by_day.setdefault(
                d, {"ngay": d, "luot_vao": 0, "luot_ra": 0, "bao_cao_ca": 0}
            )
            bucket["bao_cao_ca"] += rep_turns
            if bucket["luot_vao"] == 0 and bucket["luot_ra"] == 0 and rep_turns > 0:
                bucket["luot_vao"] = rep_turns
                bucket["luot_ra"] = rep_turns

        series = [by_day[k] for k in sorted(by_day.keys())]
        tong_vao = sum(int(x["luot_vao"]) for x in series)
        tong_ra = sum(int(x["luot_ra"]) for x in series)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "ma_khu_vuc": ma_khu_vuc,
            "tong_luot_vao": tong_vao,
            "tong_luot_ra": tong_ra,
            "tong_luot": tong_vao + tong_ra,
            "series": series,
        }

    def handle_stats_resident_vs_visitor(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.8.2: Thống kê so sánh lượt xe Cư dân vs Khách vãng lai theo loại xe và ngày."""
        start_date = str(payload.get("start_date") or "").strip()
        end_date = str(payload.get("end_date") or "").strip()
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()
        ma_loai_xe = str(payload.get("ma_loai_xe") or "").strip().upper()

        history_rows = self._query_all_parking_history(start_date, end_date, ma_khu_vuc)
        by_day: Dict[str, Dict[str, int]] = {}

        for rec in history_rows:
            v_type = str(rec.get("ma_loai_xe") or "").upper()
            if ma_loai_xe and v_type != ma_loai_xe:
                continue
            day = str(rec.get("thoi_gian_vao") or rec.get("timestamp") or "")[:10]
            if not day or not self._in_date_range(day, start_date, end_date):
                continue
            b = by_day.setdefault(
                day,
                {
                    "ngay": day,
                    "cu_dan_oto": 0,
                    "cu_dan_xemay": 0,
                    "cu_dan_tong": 0,
                    "vang_lai_oto": 0,
                    "vang_lai_xemay": 0,
                    "vang_lai_tong": 0,
                },
            )
            guest = str(rec.get("loai_khach") or "").lower()
            if guest == "cu_dan":
                if v_type == "XE_MAY":
                    b["cu_dan_xemay"] += 1
                else:
                    b["cu_dan_oto"] += 1
                b["cu_dan_tong"] += 1
            elif guest == "vang_lai":
                if v_type == "XE_MAY":
                    b["vang_lai_xemay"] += 1
                else:
                    b["vang_lai_oto"] += 1
                b["vang_lai_tong"] += 1

        db = self._db()
        conds = ["1=1"]
        params: List[Any] = []
        if start_date:
            conds.append("ngay_ghi_nhan >= %s")
            params.append(start_date[:10])
        if end_date:
            conds.append("ngay_ghi_nhan <= %s")
            params.append(end_date[:10])
        if ma_khu_vuc:
            conds.append("ma_khu_vuc = %s")
            params.append(ma_khu_vuc)

        report_rows = _safe_fetch_all(
            db,
            f"""
            SELECT ngay_ghi_nhan,
                   COALESCE(SUM(so_luot_oto_vang_lai), 0) AS vl_oto,
                   COALESCE(SUM(so_luot_xemay_vang_lai), 0) AS vl_xemay,
                   COALESCE(SUM(so_luot_xe_cu_dan), 0) AS cd_tong
            FROM bao_cao_luot_va_thu_phi
            WHERE {" AND ".join(conds)}
            GROUP BY ngay_ghi_nhan
            ORDER BY ngay_ghi_nhan ASC
            """,
            tuple(params),
        )
        for rr in report_rows:
            day = str(rr.get("ngay_ghi_nhan") or "")[:10]
            if not day:
                continue
            b = by_day.setdefault(
                day,
                {
                    "ngay": day,
                    "cu_dan_oto": 0,
                    "cu_dan_xemay": 0,
                    "cu_dan_tong": 0,
                    "vang_lai_oto": 0,
                    "vang_lai_xemay": 0,
                    "vang_lai_tong": 0,
                },
            )
            rep_vl_oto = int(rr.get("vl_oto") or 0) if ma_loai_xe in ("", "O_TO") else 0
            rep_vl_xm = int(rr.get("vl_xemay") or 0) if ma_loai_xe in ("", "XE_MAY") else 0
            rep_cd = int(rr.get("cd_tong") or 0)
            if rep_vl_oto > b["vang_lai_oto"]:
                b["vang_lai_oto"] = rep_vl_oto
            if rep_vl_xm > b["vang_lai_xemay"]:
                b["vang_lai_xemay"] = rep_vl_xm
            b["vang_lai_tong"] = b["vang_lai_oto"] + b["vang_lai_xemay"]
            if rep_cd > b["cu_dan_tong"]:
                b["cu_dan_tong"] = rep_cd

        series = [by_day[k] for k in sorted(by_day.keys())]
        tong_cd = sum(x["cu_dan_tong"] for x in series)
        tong_vl = sum(x["vang_lai_tong"] for x in series)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "ma_khu_vuc": ma_khu_vuc,
            "ma_loai_xe": ma_loai_xe,
            "cu_dan_oto": sum(x["cu_dan_oto"] for x in series),
            "cu_dan_xemay": sum(x["cu_dan_xemay"] for x in series),
            "tong_cu_dan": tong_cd,
            "vang_lai_oto": sum(x["vang_lai_oto"] for x in series),
            "vang_lai_xemay": sum(x["vang_lai_xemay"] for x in series),
            "tong_vang_lai": tong_vl,
            "series": series,
        }

    def handle_stats_revenue(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.8.3: Thống kê doanh thu theo ngày/tháng tách rõ phí cư dân và vãng lai."""
        start_date = str(payload.get("start_date") or "").strip()
        end_date = str(payload.get("end_date") or "").strip()
        group_by = str(payload.get("group_by") or "day").strip().lower()
        if group_by not in ("day", "month"):
            group_by = "day"
        ma_khu_vuc = str(payload.get("ma_khu_vuc") or "").strip().upper()

        db = self._db()
        by_period: Dict[str, Dict[str, Any]] = {}

        def _period_key(date_text: str) -> str:
            d = (date_text or "")[:10]
            if not d:
                return ""
            return d[:7] if group_by == "month" else d

        # 1. Doanh thu phí tháng cư dân (giao_dich_thanh_toan thành công)
        tx_conds = ["trang_thai_giao_dich = %s"]
        tx_params: List[Any] = [TransactionStatus.THANH_TOAN_THANH_CONG.value]
        if start_date:
            tx_conds.append("DATE(thoi_gian_giao_dich) >= %s")
            tx_params.append(start_date[:10])
        if end_date:
            tx_conds.append("DATE(thoi_gian_giao_dich) <= %s")
            tx_params.append(end_date[:10])

        tx_rows = _safe_fetch_all(
            db,
            f"""
            SELECT DATE(thoi_gian_giao_dich) AS ngay_gd,
                   COALESCE(SUM(so_tien_thanh_toan), 0) AS tong_tien
            FROM giao_dich_thanh_toan
            WHERE {" AND ".join(tx_conds)}
            GROUP BY DATE(thoi_gian_giao_dich)
            ORDER BY ngay_gd ASC
            """,
            tuple(tx_params),
        )
        for tr in tx_rows:
            pk = _period_key(str(tr.get("ngay_gd") or ""))
            if not pk:
                continue
            bucket = by_period.setdefault(
                pk, {"period": pk, "doanh_thu_cu_dan": 0.0, "doanh_thu_vang_lai": 0.0, "tong_doanh_thu": 0.0}
            )
            bucket["doanh_thu_cu_dan"] += float(tr.get("tong_tien") or 0)

        # 2. Doanh thu khách vãng lai (bao_cao_luot_va_thu_phi + fallback parking_history)
        rep_conds = ["1=1"]
        rep_params: List[Any] = []
        if start_date:
            rep_conds.append("ngay_ghi_nhan >= %s")
            rep_params.append(start_date[:10])
        if end_date:
            rep_conds.append("ngay_ghi_nhan <= %s")
            rep_params.append(end_date[:10])
        if ma_khu_vuc:
            rep_conds.append("ma_khu_vuc = %s")
            rep_params.append(ma_khu_vuc)

        rep_rows = _safe_fetch_all(
            db,
            f"""
            SELECT ngay_ghi_nhan,
                   COALESCE(SUM(tong_tien_thu_vang_lai), 0) AS tien_vang_lai
            FROM bao_cao_luot_va_thu_phi
            WHERE {" AND ".join(rep_conds)}
            GROUP BY ngay_ghi_nhan
            ORDER BY ngay_ghi_nhan ASC
            """,
            tuple(rep_params),
        )
        report_visitor_by_period: Dict[str, float] = {}
        for rr in rep_rows:
            pk = _period_key(str(rr.get("ngay_ghi_nhan") or ""))
            if pk:
                report_visitor_by_period[pk] = report_visitor_by_period.get(pk, 0.0) + float(
                    rr.get("tien_vang_lai") or 0
                )

        hist_visitor_by_period: Dict[str, float] = {}
        for rec in self._query_all_parking_history(start_date, end_date, ma_khu_vuc):
            if str(rec.get("loai_khach") or "").lower() != "vang_lai":
                continue
            out_day = str(rec.get("thoi_gian_ra") or rec.get("timestamp") or "")[:10]
            if not out_day or not self._in_date_range(out_day, start_date, end_date):
                continue
            pk = _period_key(out_day)
            if pk:
                hist_visitor_by_period[pk] = hist_visitor_by_period.get(pk, 0.0) + float(
                    rec.get("tien_thu") or 0
                )

        all_vis_periods = set(report_visitor_by_period.keys()) | set(hist_visitor_by_period.keys())
        for pk in all_vis_periods:
            vis_amount = max(
                report_visitor_by_period.get(pk, 0.0),
                hist_visitor_by_period.get(pk, 0.0),
            )
            bucket = by_period.setdefault(
                pk, {"period": pk, "doanh_thu_cu_dan": 0.0, "doanh_thu_vang_lai": 0.0, "tong_doanh_thu": 0.0}
            )
            bucket["doanh_thu_vang_lai"] += vis_amount

        series: List[Dict[str, Any]] = []
        for pk in sorted(by_period.keys()):
            b = by_period[pk]
            b["tong_doanh_thu"] = b["doanh_thu_cu_dan"] + b["doanh_thu_vang_lai"]
            series.append(b)

        total_cd = sum(x["doanh_thu_cu_dan"] for x in series)
        total_vl = sum(x["doanh_thu_vang_lai"] for x in series)
        return {
            "group_by": group_by,
            "start_date": start_date,
            "end_date": end_date,
            "doanh_thu_cu_dan": total_cd,
            "doanh_thu_vang_lai": total_vl,
            "tong_doanh_thu": total_cd + total_vl,
            "series": series,
        }


def register_monitor_admin_routes(
    router: Router,
    session_manager: SessionManager,
    file_logger: FileLogger,
    state_store: StateStore,
    db_provider: Any = None,
) -> MonitorAdminHandler:
    """Đăng ký các route thuộc quyền phụ trách của Leader Biên lên Router."""
    handler = MonitorAdminHandler(
        session_manager=session_manager,
        file_logger=file_logger,
        state_store=state_store,
        db_provider=db_provider,
    )
    router.register(
        Actions.DASHBOARD_SUMMARY,
        handler.handle_dashboard_summary,
        allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.ADMIN_LIST_ACCOUNTS,
        handler.handle_admin_list_accounts,
        allowed_roles=[Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.ADMIN_GET_ACCOUNT,
        handler.handle_admin_get_account,
        allowed_roles=[Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.ADMIN_ACCOUNT_HISTORY,
        handler.handle_admin_account_history,
        allowed_roles=[Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.ADMIN_LIST_SESSIONS,
        handler.handle_admin_list_sessions,
        allowed_roles=[Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.ADMIN_GET_SESSION,
        handler.handle_admin_get_session,
        allowed_roles=[Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.ADMIN_QUERY_LOGS,
        handler.handle_admin_query_logs,
        allowed_roles=[Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.MONITOR_ACTIVE_PARKINGS,
        handler.handle_monitor_active_parkings,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.MONITOR_AVAILABLE_SLOTS,
        handler.handle_monitor_available_slots,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.MONITOR_OCCUPIED_SLOTS,
        handler.handle_monitor_occupied_slots,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.MONITOR_OVERVIEW_MAP,
        handler.handle_monitor_overview_map,
        allowed_roles=[Roles.QUAN_LY.value, Roles.NHAN_VIEN.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.STATS_VEHICLE_TURNS,
        handler.handle_stats_vehicle_turns,
        allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.STATS_RESIDENT_VS_VISITOR,
        handler.handle_stats_resident_vs_visitor,
        allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    router.register(
        Actions.STATS_REVENUE,
        handler.handle_stats_revenue,
        allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    return handler
