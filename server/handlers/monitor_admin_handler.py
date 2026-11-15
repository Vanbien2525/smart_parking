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
    return handler
