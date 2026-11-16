"""Bộ kiểm thử tự động toàn phần cho Thành viên 2 (Hậu — feat/db-auth-infra)."""

import os
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from client.ui.admin.admin_window import AdminWindow
from client.ui.auth.forgot_password_dialog import ForgotPasswordOTPDialog
from client.ui.auth.login_dialog import LoginDialog
from client.ui.auth.profile_view import ProfileView
from client.ui.manager.finance_view import FinanceView
from client.ui.manager.hr_view import HRView
from client.ui.manager.infra_pricing_view import InfraPricingView
from client.ui.manager.manager_window import ManagerWindow
from client.ui.resident.bills_payment_view import BillsPaymentView, PaymentWorkflow06Dialog
from server.config import ServerConfig
from server.core.file_logger import FileLogger
from server.core.otp_service import OTPService
from server.core.router import Router
from server.core.session_manager import SessionManager
from server.handlers.auth_handler import hash_password, register_auth_hr_routes
from server.handlers.billing_handler import register_billing_routes
from server.handlers.infra_handler import register_infra_pricing_routes
from shared.constants import InvoiceStatus, Roles, SlotStatus, TransactionStatus, ZoneStatus
from shared.protocol import Actions


class _DummySocket:
    def sendall(self, _data: bytes) -> None:
        pass

    def shutdown(self, _how: int) -> None:
        pass

    def close(self) -> None:
        pass


class _StatefulFakeDB:
    """In-memory DB mô phỏng đầy đủ bảng liên quan để test ACID logic không cần PostgreSQL live."""

    def __init__(self) -> None:
        pw = hash_password("123456")
        self.tai_khoan: List[Dict[str, Any]] = [
            {"ma_tai_khoan": 1, "ten_dang_nhap": "admin01", "mat_khau_bam": pw, "vai_tro": "admin", "trang_thai": "hoat_dong", "ly_do_khoa": None, "ngay_tao": "2026-11-16T08:00:00"},
            {"ma_tai_khoan": 2, "ten_dang_nhap": "quanly01", "mat_khau_bam": pw, "vai_tro": "quan_ly", "trang_thai": "hoat_dong", "ly_do_khoa": None, "ngay_tao": "2026-11-16T08:00:00"},
            {"ma_tai_khoan": 3, "ten_dang_nhap": "nhanvien01", "mat_khau_bam": pw, "vai_tro": "nhan_vien", "trang_thai": "hoat_dong", "ly_do_khoa": None, "ngay_tao": "2026-11-16T08:00:00"},
            {"ma_tai_khoan": 4, "ten_dang_nhap": "cudan_a101", "mat_khau_bam": pw, "vai_tro": "cu_dan", "trang_thai": "hoat_dong", "ly_do_khoa": None, "ngay_tao": "2026-11-16T08:00:00"},
        ]
        self.can_ho: List[Dict[str, Any]] = [
            {"ma_can_ho": "A1-01", "toa_nha": "A", "so_tang": 1, "so_cho_oto_mac_dinh": 1, "so_cho_oto_mua_them": 0, "trang_thai_can_ho": "dang_su_dung"},
            {"ma_can_ho": "B2-02", "toa_nha": "B", "so_tang": 2, "so_cho_oto_mac_dinh": 1, "so_cho_oto_mua_them": 1, "trang_thai_can_ho": "dang_su_dung"},
        ]
        self.cu_dan: List[Dict[str, Any]] = [
            {"ma_cu_dan": 1, "ma_tai_khoan": 4, "ma_can_ho": "A1-01", "ho_ten": "Lê Minh Cư Dân", "so_cccd": "079095000101", "so_dien_thoai": "0909200101", "email": "a101@daotien.vn", "dia_chi": "A1-01"},
        ]
        self.nhan_vien: List[Dict[str, Any]] = [
            {"ma_nhan_vien": 1, "ma_tai_khoan": 2, "ma_nhan_vien_code": "QL001", "ho_ten": "Nguyễn Quản Lý", "so_cccd": "079090000001", "so_dien_thoai": "0909100001", "email": "quanly01@daotien.vn", "chuc_vu": "quan_ly_chung_cu"},
            {"ma_nhan_vien": 2, "ma_tai_khoan": 3, "ma_nhan_vien_code": "NV001", "ho_ten": "Trần Bảo Vệ", "so_cccd": "079092000002", "so_dien_thoai": "0909100002", "email": "nhanvien01@daotien.vn", "chuc_vu": "nhan_vien_bai_xe"},
        ]
        self.khu_vuc: List[Dict[str, Any]] = [
            {"ma_khu_vuc": "HAM_B1", "ten_khu_vuc": "Hầm B1 Ô tô", "ma_loai_xe": "O_TO", "tong_so_cho": 2, "so_xe_dang_gui": 1, "trang_thai_khu_vuc": "hoat_dong", "ly_do_ngung": None},
        ]
        self.vi_tri_do: List[Dict[str, Any]] = [
            {"ma_vi_tri": "B1-01", "ma_khu_vuc": "HAM_B1", "ten_vi_tri": "Ô B1-01", "nhom_vi_tri": "vi_tri_dau_bai", "trang_thai_vi_tri": "dang_co_xe", "ly_do_ngung": None, "thoi_gian_cap_nhat": "2026-11-16T08:00:00"},
            {"ma_vi_tri": "B1-02", "ma_khu_vuc": "HAM_B1", "ten_vi_tri": "Ô B1-02", "nhom_vi_tri": "vi_tri_thuong", "trang_thai_vi_tri": "trong", "ly_do_ngung": None, "thoi_gian_cap_nhat": "2026-11-16T08:00:00"},
        ]
        self.phan_bo_cho_do: List[Dict[str, Any]] = [
            {"ma_phan_bo": 1, "ma_can_ho": "A1-01", "ma_vi_tri": "B1-01", "loai_cho_cap": "cho_mac_dinh_xe_1", "ngay_cap": "2026-11-01", "trang_thai_su_dung": "dang_hieu_luc"},
        ]
        self.phuong_tien: List[Dict[str, Any]] = [
            {"ma_phuong_tien": 1, "ma_cu_dan": 1, "ma_loai_xe": "XE_MAY", "bien_so_xe": "59A1-123.45", "trang_thai_xe": "dang_hoat_dong"},
        ]
        self.bang_gia: List[Dict[str, Any]] = [
            {"ma_bang_gia": 1, "ma_loai_xe": "O_TO", "nhom_ap_dung": "cho_oto_mac_dinh", "don_vi_tinh": "thang", "muc_phi": 1_200_000.0, "thoi_gian_bat_dau": "2026-11-01", "thoi_gian_ket_thuc": None, "trang_thai_gia": "dang_ap_dung"},
            {"ma_bang_gia": 2, "ma_loai_xe": "O_TO", "nhom_ap_dung": "cho_oto_mua_them", "don_vi_tinh": "thang", "muc_phi": 1_800_000.0, "thoi_gian_bat_dau": "2026-11-01", "thoi_gian_ket_thuc": None, "trang_thai_gia": "dang_ap_dung"},
            {"ma_bang_gia": 3, "ma_loai_xe": "XE_MAY", "nhom_ap_dung": "xe_may_cu_dan", "don_vi_tinh": "thang", "muc_phi": 120_000.0, "thoi_gian_bat_dau": "2026-11-01", "thoi_gian_ket_thuc": None, "trang_thai_gia": "dang_ap_dung"},
        ]
        self.hoa_don_thang: List[Dict[str, Any]] = []
        self.giao_dich_thanh_toan: List[Dict[str, Any]] = []

    @contextmanager
    def transaction(self):
        yield self

    def _enrich_account(self, tk: Dict[str, Any]) -> Dict[str, Any]:
        cd = next((c for c in self.cu_dan if c["ma_tai_khoan"] == tk["ma_tai_khoan"]), None)
        nv = next((n for n in self.nhan_vien if n["ma_tai_khoan"] == tk["ma_tai_khoan"]), None)
        ch = next((a for a in self.can_ho if cd and a["ma_can_ho"] == cd["ma_can_ho"]), None)
        return {
            **tk,
            "ma_cu_dan": cd["ma_cu_dan"] if cd else None,
            "ma_can_ho": cd["ma_can_ho"] if cd else None,
            "cd_ho_ten": cd["ho_ten"] if cd else None,
            "cd_cccd": cd["so_cccd"] if cd else None,
            "cd_sdt": cd["so_dien_thoai"] if cd else None,
            "cd_email": cd["email"] if cd else None,
            "dia_chi": cd["dia_chi"] if cd else None,
            "toa_nha": ch["toa_nha"] if ch else None,
            "so_tang": ch["so_tang"] if ch else None,
            "so_cho_oto_mac_dinh": ch["so_cho_oto_mac_dinh"] if ch else None,
            "so_cho_oto_mua_them": ch["so_cho_oto_mua_them"] if ch else None,
            "ma_nhan_vien": nv["ma_nhan_vien"] if nv else None,
            "ma_nhan_vien_code": nv["ma_nhan_vien_code"] if nv else None,
            "nv_ho_ten": nv["ho_ten"] if nv else None,
            "nv_cccd": nv["so_cccd"] if nv else None,
            "nv_sdt": nv["so_dien_thoai"] if nv else None,
            "nv_email": nv["email"] if nv else None,
            "chuc_vu": nv["chuc_vu"] if nv else None,
        }

    def fetch_one(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        q = " ".join(sql.split())
        if "WHERE tk.ten_dang_nhap = %s" in q:
            tk = next((t for t in self.tai_khoan if t["ten_dang_nhap"] == params[0]), None)
            return self._enrich_account(tk) if tk else None
        if "WHERE tk.ma_tai_khoan = %s" in q:
            tk = next((t for t in self.tai_khoan if t["ma_tai_khoan"] == params[0]), None)
            return self._enrich_account(tk) if tk else None
        if "WHERE LOWER(COALESCE(cd.email, nv.email, '')) = %s" in q:
            email = str(params[0]).lower()
            for tk in self.tai_khoan:
                en = self._enrich_account(tk)
                if str(en.get("cd_email") or en.get("nv_email") or "").lower() == email:
                    return en
            return None
        if "FROM tai_khoan WHERE ma_tai_khoan = %s" in q:
            return next((dict(t) for t in self.tai_khoan if t["ma_tai_khoan"] == params[0]), None)
        if "FROM tai_khoan WHERE ten_dang_nhap = %s" in q:
            return next((dict(t) for t in self.tai_khoan if t["ten_dang_nhap"] == params[0]), None)
        if "UNION ALL" in q and "so_cccd" in q:
            cccd, excl, _, _, email = params[0], params[1], params[2], params[3], params[4]
            for c in self.cu_dan:
                if c["ma_tai_khoan"] != excl and c["so_cccd"] == cccd:
                    return {"field": "cccd"}
                if c["ma_tai_khoan"] != excl and c["email"].lower() == str(email).lower():
                    return {"field": "email"}
            for n in self.nhan_vien:
                if n["ma_tai_khoan"] != excl and n["so_cccd"] == cccd:
                    return {"field": "cccd"}
                if n["ma_tai_khoan"] != excl and n["email"].lower() == str(email).lower():
                    return {"field": "email"}
            return None
        if "FROM can_ho WHERE ma_can_ho = %s" in q:
            return next((dict(a) for a in self.can_ho if a["ma_can_ho"] == params[0]), None)
        if "FROM cu_dan cd" in q and "WHERE cd.ma_cu_dan = %s" in q:
            cd = next((c for c in self.cu_dan if c["ma_cu_dan"] == params[0]), None)
            if not cd:
                return None
            tk = next(t for t in self.tai_khoan if t["ma_tai_khoan"] == cd["ma_tai_khoan"])
            ch = next(a for a in self.can_ho if a["ma_can_ho"] == cd["ma_can_ho"])
            return {**cd, **tk, **ch}
        if "SELECT ma_tai_khoan FROM cu_dan WHERE ma_cu_dan = %s" in q:
            return next((dict(c) for c in self.cu_dan if c["ma_cu_dan"] == params[0]), None)
        if "SELECT ma_tai_khoan FROM nhan_vien WHERE ma_nhan_vien = %s" in q:
            return next((dict(n) for n in self.nhan_vien if n["ma_nhan_vien"] == params[0]), None)
        if "SELECT ma_nhan_vien FROM nhan_vien WHERE ma_nhan_vien_code = %s" in q:
            return next((dict(n) for n in self.nhan_vien if n["ma_nhan_vien_code"] == params[0]), None)
        if "FROM nhan_vien nv JOIN tai_khoan tk" in q and "WHERE nv.ma_nhan_vien = %s" in q:
            nv = next((n for n in self.nhan_vien if n["ma_nhan_vien"] == params[0]), None)
            if not nv:
                return None
            tk = next(t for t in self.tai_khoan if t["ma_tai_khoan"] == nv["ma_tai_khoan"])
            return {**nv, **tk}
        if "FROM khu_vuc WHERE ma_khu_vuc = %s" in q:
            return next((dict(z) for z in self.khu_vuc if z["ma_khu_vuc"] == params[0]), None)
        if "COUNT(*) AS cnt FROM vi_tri_do WHERE ma_khu_vuc = %s AND trang_thai_vi_tri = %s" in q:
            cnt = sum(1 for v in self.vi_tri_do if v["ma_khu_vuc"] == params[0] and v["trang_thai_vi_tri"] == params[1])
            return {"cnt": cnt}
        if "COUNT(*) AS cnt FROM vi_tri_do WHERE ma_khu_vuc = %s" in q:
            cnt = sum(1 for v in self.vi_tri_do if v["ma_khu_vuc"] == params[0])
            return {"cnt": cnt}
        if "FROM vi_tri_do WHERE ma_vi_tri = %s" in q:
            return next((dict(v) for v in self.vi_tri_do if v["ma_vi_tri"] == params[0]), None)
        if "FROM phan_bo_cho_do WHERE ma_vi_tri = %s" in q:
            return next((dict(p) for p in self.phan_bo_cho_do if p["ma_vi_tri"] == params[0] and p["trang_thai_su_dung"] == "dang_hieu_luc"), None)
        if "FROM bang_gia WHERE ma_bang_gia = %s" in q:
            return next((dict(b) for b in self.bang_gia if b["ma_bang_gia"] == params[0]), None)
        if "FROM hoa_don_thang WHERE ma_can_ho = %s AND ky_thu_thang_nam = %s" in q:
            return next((dict(h) for h in self.hoa_don_thang if h["ma_can_ho"] == params[0] and h["ky_thu_thang_nam"] == params[1]), None)
        if "FROM hoa_don_thang WHERE ma_hoa_don = %s" in q:
            return next((dict(h) for h in self.hoa_don_thang if h["ma_hoa_don"] == params[0]), None)
        if "FROM hoa_don_thang hd JOIN can_ho ch" in q and "WHERE hd.ma_hoa_don = %s" in q:
            hd = next((h for h in self.hoa_don_thang if h["ma_hoa_don"] == params[0]), None)
            if not hd:
                return None
            ch = next(a for a in self.can_ho if a["ma_can_ho"] == hd["ma_can_ho"])
            return {**hd, "toa_nha": ch["toa_nha"], "so_tang": ch["so_tang"]}
        if "FROM giao_dich_thanh_toan gd JOIN hoa_don_thang hd" in q:
            if "WHERE gd.ma_giao_dich = %s" in q:
                gd = next((g for g in self.giao_dich_thanh_toan if g["ma_giao_dich"] == params[0]), None)
            else:
                gd = next((g for g in reversed(self.giao_dich_thanh_toan) if g["ma_hoa_don"] == params[0] and g["trang_thai_giao_dich"] == params[1]), None)
            if not gd:
                return None
            hd = next(h for h in self.hoa_don_thang if h["ma_hoa_don"] == gd["ma_hoa_don"])
            nv = next((n for n in self.nhan_vien if n["ma_nhan_vien"] == gd.get("ma_nhan_vien_thu")), None)
            return {**gd, "ma_can_ho": hd["ma_can_ho"], "ky_thu_thang_nam": hd["ky_thu_thang_nam"], "tong_tien_phai_thu": hd["tong_tien_phai_thu"], "ten_nhan_vien_thu": nv["ho_ten"] if nv else None}
        if "FROM giao_dich_thanh_toan WHERE ma_giao_dich = %s AND ma_hoa_don = %s" in q:
            return next((dict(g) for g in self.giao_dich_thanh_toan if g["ma_giao_dich"] == params[0] and g["ma_hoa_don"] == params[1]), None)
        if "SUM(tong_tien_phai_thu)" in q:
            items = [h for h in self.hoa_don_thang if (len(params) < 7 or h["ky_thu_thang_nam"] == params[6])]
            return {
                "tong_phai_thu": sum(h["tong_tien_phai_thu"] for h in items),
                "tong_da_thu": sum(h["tong_tien_phai_thu"] for h in items if h["trang_thai_hoa_don"] == "da_thanh_toan"),
                "tong_con_no": sum(h["tong_tien_phai_thu"] for h in items if h["trang_thai_hoa_don"] in ("chua_thanh_toan", "qua_han")),
                "tong_hoa_don": len(items),
                "so_da_thu": sum(1 for h in items if h["trang_thai_hoa_don"] == "da_thanh_toan"),
                "so_con_no": sum(1 for h in items if h["trang_thai_hoa_don"] in ("chua_thanh_toan", "qua_han")),
            }
        return None

    def fetch_all(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        q = " ".join(sql.split())
        if "FROM cu_dan cd JOIN tai_khoan tk" in q:
            return [{**c, **next(t for t in self.tai_khoan if t["ma_tai_khoan"] == c["ma_tai_khoan"])} for c in self.cu_dan]
        if "FROM can_ho ORDER BY" in q:
            return [dict(a) for a in self.can_ho]
        if "FROM nhan_vien nv JOIN tai_khoan tk" in q:
            return [{**n, **next(t for t in self.tai_khoan if t["ma_tai_khoan"] == n["ma_tai_khoan"])} for n in self.nhan_vien]
        if "FROM khu_vuc kv" in q:
            return [dict(z) for z in self.khu_vuc]
        if "FROM vi_tri_do v" in q:
            return [dict(v) for v in self.vi_tri_do]
        if "FROM bang_gia WHERE trang_thai_gia = %s" in q:
            return [dict(b) for b in reversed(self.bang_gia) if b["trang_thai_gia"] == params[0]]
        if "FROM bang_gia bg" in q:
            return [dict(b) for b in self.bang_gia]
        if "FROM can_ho ch WHERE ch.trang_thai_can_ho = 'dang_su_dung'" in q:
            res = []
            for ch in self.can_ho:
                if ch["trang_thai_can_ho"] != "dang_su_dung":
                    continue
                cd_ids = {c["ma_cu_dan"] for c in self.cu_dan if c["ma_can_ho"] == ch["ma_can_ho"]}
                motos = sum(1 for p in self.phuong_tien if p["ma_cu_dan"] in cd_ids and p["ma_loai_xe"] == "XE_MAY" and p["trang_thai_xe"] == "dang_hoat_dong")
                res.append({**ch, "so_luong_xe_may": motos})
            return res
        if "FROM hoa_don_thang hd" in q:
            if "WHERE hd.ma_can_ho = %s" in q:
                return [dict(h) for h in self.hoa_don_thang if h["ma_can_ho"] == params[0]]
            return [dict(h) for h in self.hoa_don_thang]
        if "FROM phan_bo_cho_do pb" in q:
            return [dict(p) for p in self.phan_bo_cho_do if p["ma_can_ho"] == params[0]]
        if "FROM giao_dich_thanh_toan gd" in q:
            if "WHERE gd.ma_hoa_don = %s" in q:
                return [dict(g) for g in self.giao_dich_thanh_toan if g["ma_hoa_don"] == params[0]]
            return [
                {**g, "ma_can_ho": next(h["ma_can_ho"] for h in self.hoa_don_thang if h["ma_hoa_don"] == g["ma_hoa_don"])}
                for g in self.giao_dich_thanh_toan
                if g["trang_thai_giao_dich"] == params[0]
            ]
        return []

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> int:
        q = " ".join(sql.split())
        if q.startswith("UPDATE tai_khoan SET mat_khau_bam = %s"):
            for t in self.tai_khoan:
                if t["ma_tai_khoan"] == params[1]:
                    t["mat_khau_bam"] = params[0]
                    return 1
        if q.startswith("UPDATE tai_khoan SET trang_thai = %s, ly_do_khoa = %s"):
            for t in self.tai_khoan:
                if t["ma_tai_khoan"] == params[2]:
                    t["trang_thai"], t["ly_do_khoa"] = params[0], params[1]
                    return 1
        if q.startswith("UPDATE tai_khoan SET vai_tro = %s"):
            for t in self.tai_khoan:
                if t["ma_tai_khoan"] == params[1]:
                    t["vai_tro"] = params[0]
                    return 1
        if q.startswith("UPDATE cu_dan SET"):
            for c in self.cu_dan:
                if c["ma_tai_khoan"] == params[5]:
                    c["ho_ten"], c["so_cccd"], c["so_dien_thoai"], c["email"], c["dia_chi"] = params[:5]
                    return 1
        if q.startswith("INSERT INTO can_ho"):
            self.can_ho.append({"ma_can_ho": params[0], "toa_nha": params[1], "so_tang": params[2], "so_cho_oto_mac_dinh": 1, "so_cho_oto_mua_them": 0, "trang_thai_can_ho": "dang_su_dung"})
            return 1
        if q.startswith("UPDATE khu_vuc SET tong_so_cho = %s"):
            for z in self.khu_vuc:
                if z["ma_khu_vuc"] == params[1]:
                    z["tong_so_cho"] = params[0]
                    return 1
        if q.startswith("INSERT INTO hoa_don_thang"):
            nid = len(self.hoa_don_thang) + 1
            self.hoa_don_thang.append({
                "ma_hoa_don": nid,
                "ma_can_ho": params[0],
                "ky_thu_thang_nam": params[1],
                "so_cho_oto_mac_dinh": params[2],
                "so_cho_oto_mua_them": params[3],
                "so_luong_xe_may": params[4],
                "tong_tien_phai_thu": params[5],
                "trang_thai_hoa_don": params[6],
                "ngay_tao_hoa_don": datetime.now().isoformat(timespec="seconds"),
            })
            return 1
        if q.startswith("UPDATE hoa_don_thang SET trang_thai_hoa_don = %s"):
            for h in self.hoa_don_thang:
                if h["ma_hoa_don"] == params[1]:
                    h["trang_thai_hoa_don"] = params[0]
                    return 1
        return 0

    def execute_returning(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        q = " ".join(sql.split())
        if q.startswith("INSERT INTO tai_khoan"):
            nid = max(t["ma_tai_khoan"] for t in self.tai_khoan) + 1
            row = {"ma_tai_khoan": nid, "ten_dang_nhap": params[0], "mat_khau_bam": params[1], "vai_tro": params[2], "trang_thai": params[3], "ly_do_khoa": None, "ngay_tao": "2026-11-16"}
            self.tai_khoan.append(row)
            return dict(row)
        if q.startswith("INSERT INTO cu_dan"):
            nid = max((c["ma_cu_dan"] for c in self.cu_dan), default=0) + 1
            row = {"ma_cu_dan": nid, "ma_tai_khoan": params[0], "ma_can_ho": params[1], "ho_ten": params[2], "so_cccd": params[3], "so_dien_thoai": params[4], "email": params[5], "dia_chi": params[6]}
            self.cu_dan.append(row)
            return dict(row)
        if q.startswith("INSERT INTO nhan_vien"):
            nid = max((n["ma_nhan_vien"] for n in self.nhan_vien), default=0) + 1
            row = {"ma_nhan_vien": nid, "ma_tai_khoan": params[0], "ma_nhan_vien_code": params[1], "ho_ten": params[2], "so_cccd": params[3], "so_dien_thoai": params[4], "email": params[5], "chuc_vu": params[6]}
            self.nhan_vien.append(row)
            return dict(row)
        if q.startswith("UPDATE nhan_vien"):
            for n in self.nhan_vien:
                if n["ma_nhan_vien"] == params[5]:
                    n["ho_ten"], n["so_cccd"], n["so_dien_thoai"], n["email"], n["chuc_vu"] = params[:5]
                    return dict(n)
        if q.startswith("INSERT INTO khu_vuc"):
            row = {"ma_khu_vuc": params[0], "ten_khu_vuc": params[1], "ma_loai_xe": params[2], "tong_so_cho": params[3], "so_xe_dang_gui": 0, "trang_thai_khu_vuc": params[4], "ly_do_ngung": None}
            self.khu_vuc.append(row)
            return dict(row)
        if q.startswith("UPDATE khu_vuc SET trang_thai_khu_vuc = %s, ly_do_ngung = %s"):
            for z in self.khu_vuc:
                if z["ma_khu_vuc"] == params[2]:
                    z["trang_thai_khu_vuc"], z["ly_do_ngung"] = params[0], params[1]
                    return dict(z)
        if q.startswith("UPDATE khu_vuc SET trang_thai_khu_vuc = %s, ly_do_ngung = NULL"):
            for z in self.khu_vuc:
                if z["ma_khu_vuc"] == params[1]:
                    z["trang_thai_khu_vuc"], z["ly_do_ngung"] = params[0], None
                    return dict(z)
        if q.startswith("INSERT INTO vi_tri_do"):
            row = {"ma_vi_tri": params[0], "ma_khu_vuc": params[1], "ten_vi_tri": params[2], "nhom_vi_tri": params[3], "trang_thai_vi_tri": params[4], "ly_do_ngung": None, "thoi_gian_cap_nhat": "2026-11-16"}
            self.vi_tri_do.append(row)
            return dict(row)
        if q.startswith("UPDATE vi_tri_do SET trang_thai_vi_tri = %s, ly_do_ngung = %s"):
            for v in self.vi_tri_do:
                if v["ma_vi_tri"] == params[2]:
                    v["trang_thai_vi_tri"], v["ly_do_ngung"] = params[0], params[1]
                    return dict(v)
        if q.startswith("UPDATE vi_tri_do SET trang_thai_vi_tri = %s, ly_do_ngung = NULL"):
            for v in self.vi_tri_do:
                if v["ma_vi_tri"] == params[1]:
                    v["trang_thai_vi_tri"], v["ly_do_ngung"] = params[0], None
                    return dict(v)
        if q.startswith("INSERT INTO bang_gia"):
            nid = len(self.bang_gia) + 1
            row = {"ma_bang_gia": nid, "ma_loai_xe": params[0], "nhom_ap_dung": params[1], "don_vi_tinh": params[2], "muc_phi": params[3], "thoi_gian_bat_dau": params[4], "thoi_gian_ket_thuc": None, "trang_thai_gia": params[5]}
            self.bang_gia.append(row)
            return dict(row)
        if q.startswith("UPDATE bang_gia SET muc_phi = %s"):
            for b in self.bang_gia:
                if b["ma_bang_gia"] == params[2]:
                    b["muc_phi"], b["thoi_gian_bat_dau"] = params[0], params[1]
                    return dict(b)
        if q.startswith("UPDATE bang_gia SET trang_thai_gia = %s"):
            for b in self.bang_gia:
                if b["ma_bang_gia"] == params[1]:
                    b["trang_thai_gia"] = params[0]
                    return dict(b)
        if q.startswith("INSERT INTO giao_dich_thanh_toan"):
            nid = len(self.giao_dich_thanh_toan) + 1
            if "ma_qr, han_ma_qr" in q:
                row = {"ma_giao_dich": nid, "ma_hoa_don": params[0], "hinh_thuc_thanh_toan": params[1], "so_tien_thanh_toan": params[2], "ma_qr": params[3], "han_ma_qr": params[4], "ma_bien_lai": None, "ma_nhan_vien_thu": None, "trang_thai_giao_dich": params[5], "ly_do_loi": None, "thoi_gian_giao_dich": params[6]}
            else:
                row = {"ma_giao_dich": nid, "ma_hoa_don": params[0], "hinh_thuc_thanh_toan": params[1], "so_tien_thanh_toan": params[2], "ma_qr": None, "han_ma_qr": None, "ma_bien_lai": params[3], "ma_nhan_vien_thu": params[4], "trang_thai_giao_dich": params[5], "ly_do_loi": None, "thoi_gian_giao_dich": params[6]}
            self.giao_dich_thanh_toan.append(row)
            return dict(row)
        if q.startswith("UPDATE giao_dich_thanh_toan SET trang_thai_giao_dich = %s, ma_bien_lai = %s"):
            for g in self.giao_dich_thanh_toan:
                if g["ma_giao_dich"] == params[3]:
                    g["trang_thai_giao_dich"], g["ma_bien_lai"], g["ly_do_loi"], g["thoi_gian_giao_dich"] = params[0], params[1], None, params[2]
                    return dict(g)
        if q.startswith("UPDATE giao_dich_thanh_toan SET trang_thai_giao_dich = %s, ly_do_loi = %s"):
            for g in self.giao_dich_thanh_toan:
                if g["ma_giao_dich"] == params[3]:
                    g["trang_thai_giao_dich"], g["ly_do_loi"], g["thoi_gian_giao_dich"] = params[0], params[1], params[2]
                    return dict(g)
        return None


class TestHauDbAuthBilling(unittest.TestCase):
    """Kiểm thử toàn bộ phần việc Thành viên 2 — Hậu."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.file_logger = FileLogger(self.tmp_path / "logs")
        self.session_manager = SessionManager(file_logger=self.file_logger)
        self.sent_emails: List[Tuple[str, str, str]] = []
        self.otp_service = OTPService(
            config=ServerConfig(otp_expire_seconds=300, otp_resend_cooldown_seconds=0),
            email_sender=lambda to, subj, body: self.sent_emails.append((to, subj, body)),
        )
        self.router = Router(file_logger=self.file_logger)
        self.db = _StatefulFakeDB()

        register_auth_hr_routes(
            self.router,
            session_manager=self.session_manager,
            file_logger=self.file_logger,
            otp_service=self.otp_service,
            db_provider=self.db,
        )
        register_infra_pricing_routes(
            self.router, file_logger=self.file_logger, db_provider=self.db
        )
        self.billing_handler = register_billing_routes(
            self.router, file_logger=self.file_logger, db_provider=self.db
        )

    def tearDown(self) -> None:
        self.session_manager.stop_reaper()
        self.tmp_dir.cleanup()

    def _new_session(self, user_data: Optional[Dict[str, Any]] = None):
        sess = self.session_manager.register_connection(_DummySocket(), ("127.0.0.1", 55000))
        if user_data:
            self.session_manager.bind_authenticated_user(sess.session_id, user_data)
        return sess

    def test_01_schema_sql_18_tables_28_fks_and_seed_data(self) -> None:
        """Kiểm tra file schema.sql đủ 18 bảng, 28 khóa ngoại và seed_data.sql chuẩn."""
        schema_sql = (PROJECT_ROOT / "database" / "schema.sql").read_text(encoding="utf-8")
        tables = re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", schema_sql)
        fks = re.findall(r"FOREIGN KEY\s*\(", schema_sql)
        self.assertEqual(len(tables), 18)
        self.assertEqual(len(fks), 28)

        seed_sql = (PROJECT_ROOT / "database" / "seed_data.sql").read_text(encoding="utf-8")
        for acc in ("admin01", "quanly01", "nhanvien01", "cudan_a101", "cudan_b202"):
            self.assertIn(acc, seed_sql)
        for zone in ("KHU_A", "HAM_B1", "KHU_C"):
            self.assertIn(zone, seed_sql)
        self.assertIn("B1-12", seed_sql)
        self.assertIn("C-08", seed_sql)

    def test_02_auth_login_change_password_profile_and_admin_readonly(self) -> None:
        """Kiểm tra đăng nhập, đổi mật khẩu, xem/sửa hồ sơ và chặn Admin sửa hồ sơ."""
        sess = self._new_session()
        r_ok = self.router.dispatch(
            sess, Actions.AUTH_LOGIN, {"ten_dang_nhap": "cudan_a101", "mat_khau": "123456"}, "1"
        )
        self.assertTrue(r_ok["ok"])
        self.assertEqual(r_ok["data"]["vai_tro"], Roles.CU_DAN.value)

        r_pw = self.router.dispatch(
            sess,
            Actions.AUTH_CHANGE_PASSWORD,
            {"mat_khau_hien_tai": "123456", "mat_khau_moi": "matkhau99", "xac_nhan_mat_khau": "matkhau99"},
            "2",
        )
        self.assertTrue(r_pw["ok"])

        r_prof = self.router.dispatch(
            sess,
            Actions.AUTH_UPDATE_PROFILE,
            {"ho_ten": "Lê Minh Đã Sửa", "so_dien_thoai": "0912345678"},
            "3",
        )
        self.assertTrue(r_prof["ok"])
        self.assertEqual(r_prof["data"]["profile"]["ho_ten"], "Lê Minh Đã Sửa")

        admin_sess = self._new_session()
        self.router.dispatch(
            admin_sess, Actions.AUTH_LOGIN, {"ten_dang_nhap": "admin01", "mat_khau": "123456"}, "4"
        )
        r_admin_ro = self.router.dispatch(
            admin_sess, Actions.AUTH_UPDATE_PROFILE, {"ho_ten": "Không được phép"}, "5"
        )
        self.assertFalse(r_admin_ro["ok"])
        self.assertEqual(r_admin_ro["error_code"], "ADMIN_READ_ONLY")

    def test_03_otp_4_steps_and_revoke_sessions(self) -> None:
        """Kiểm tra luồng 4 bước OTP Email & thu hồi phiên cũ khi đặt lại mật khẩu."""
        active_sess = self._new_session(
            {"ma_tai_khoan": 4, "ten_dang_nhap": "cudan_a101", "vai_tro": Roles.CU_DAN.value}
        )
        anon_sess = self._new_session()

        r1 = self.router.dispatch(anon_sess, Actions.AUTH_REQUEST_OTP, {"email": "a101@daotien.vn"}, "1")
        self.assertTrue(r1["ok"])
        req_id = r1["data"]["ma_yeu_cau_otp"]

        r2 = self.router.dispatch(anon_sess, Actions.AUTH_RESEND_OTP, {"ma_yeu_cau_otp": req_id}, "2")
        self.assertTrue(r2["ok"])
        otp_code = self.otp_service._records[req_id].otp_code

        r3 = self.router.dispatch(
            anon_sess, Actions.AUTH_VERIFY_OTP, {"ma_yeu_cau_otp": req_id, "ma_otp": otp_code}, "3"
        )
        self.assertTrue(r3["ok"])
        token = r3["data"]["reset_token"]

        r4 = self.router.dispatch(
            anon_sess,
            Actions.AUTH_RESET_PASSWORD,
            {"reset_token": token, "mat_khau_moi": "reset123", "xac_nhan": "reset123"},
            "4",
        )
        self.assertTrue(r4["ok"])
        self.assertEqual(r4["data"]["revoked_sessions"], 1)
        self.assertIsNone(self.session_manager.get_session(active_sess.session_id))

    def test_04_hr_create_resident_auto_upsert_can_ho_and_lock(self) -> None:
        """Kiểm tra tạo cư dân 1.3.1 tự động upsert căn hộ C15-08 và thu hồi phiên khi khóa 1.3.4."""
        mgr = self._new_session(
            {"ma_tai_khoan": 2, "ten_dang_nhap": "quanly01", "vai_tro": Roles.QUAN_LY.value}
        )
        resp = self.router.dispatch(
            mgr,
            Actions.HR_CREATE_RESIDENT,
            {
                "ho_ten": "Nguyễn Thị Cư Dân",
                "so_cccd": "079200015008",
                "ma_can_ho": "C15-08",
                "email": "c1508@daotien.vn",
                "so_dien_thoai": "0909555666",
                "ten_dang_nhap": "cudan_c1508",
                "mat_khau_ban_dau": "123456",
            },
            "1",
        )
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["data"]["auto_created_apartment"])
        apt = next(a for a in self.db.can_ho if a["ma_can_ho"] == "C15-08")
        self.assertEqual(apt["toa_nha"], "C")
        self.assertEqual(apt["so_tang"], 15)
        self.assertEqual(apt["so_cho_oto_mac_dinh"], 1)

        new_tk_id = resp["data"]["resident"]["ma_tai_khoan"]
        res_sess = self._new_session(
            {"ma_tai_khoan": new_tk_id, "ten_dang_nhap": "cudan_c1508", "vai_tro": Roles.CU_DAN.value}
        )
        r_lock = self.router.dispatch(
            mgr,
            Actions.HR_DEACTIVATE_RESIDENT,
            {"ma_tai_khoan": new_tk_id, "ly_do_khoa": "Chuyển căn hộ"},
            "2",
        )
        self.assertTrue(r_lock["ok"])
        self.assertEqual(r_lock["data"]["revoked_sessions"], 1)
        self.assertIsNone(self.session_manager.get_session(res_sess.session_id))

        r_login_locked = self.router.dispatch(
            self._new_session(),
            Actions.AUTH_LOGIN,
            {"ten_dang_nhap": "cudan_c1508", "mat_khau": "123456"},
            "3",
        )
        self.assertFalse(r_login_locked["ok"])
        self.assertIn("tạm khóa", r_login_locked["error"].lower())

    def test_05_hr_staff_crud_and_lock(self) -> None:
        """Kiểm tra CRUD & khóa/mở khóa nhân viên 1.3.6-1.3.10."""
        mgr = self._new_session(
            {"ma_tai_khoan": 2, "ten_dang_nhap": "quanly01", "vai_tro": Roles.QUAN_LY.value}
        )
        r_create = self.router.dispatch(
            mgr,
            Actions.HR_CREATE_STAFF,
            {
                "ma_nhan_vien_code": "NV009",
                "ho_ten": "Phạm Bảo Vệ",
                "so_cccd": "079099000009",
                "so_dien_thoai": "0909888999",
                "email": "nv009@daotien.vn",
                "chuc_vu": "nhan_vien_bai_xe",
                "ten_dang_nhap": "nv009",
                "mat_khau_ban_dau": "123456",
            },
            "1",
        )
        self.assertTrue(r_create["ok"])
        nv_id = r_create["data"]["staff"]["ma_nhan_vien"]

        r_upd = self.router.dispatch(
            mgr, Actions.HR_UPDATE_STAFF, {"ma_nhan_vien": nv_id, "ho_ten": "Phạm Bảo Vệ Mới"}, "2"
        )
        self.assertTrue(r_upd["ok"])
        self.assertEqual(r_upd["data"]["staff"]["ho_ten"], "Phạm Bảo Vệ Mới")

        r_lock = self.router.dispatch(
            mgr, Actions.HR_DEACTIVATE_STAFF, {"ma_nhan_vien": nv_id, "ly_do_khoa": "Nghỉ phép dài hạn"}, "3"
        )
        self.assertTrue(r_lock["ok"])
        r_unlock = self.router.dispatch(mgr, Actions.HR_ACTIVATE_STAFF, {"ma_nhan_vien": nv_id}, "4")
        self.assertTrue(r_unlock["ok"])
        self.assertEqual(r_unlock["data"]["trang_thai"], "hoat_dong")

    def test_06_infra_zone_slot_constraints_and_pricing(self) -> None:
        """Kiểm tra ràng buộc ngừng khu vực 1.4.8, ngừng vị trí 1.4.13 và bảng giá 1.5."""
        mgr = self._new_session(
            {"ma_tai_khoan": 2, "ten_dang_nhap": "quanly01", "vai_tro": Roles.QUAN_LY.value}
        )
        # B1-01 đang dang_co_xe & HAM_B1 có so_xe_dang_gui=1 -> phải bị chặn
        r_z_fail = self.router.dispatch(
            mgr, Actions.INFRA_DEACTIVATE_ZONE, {"ma_khu_vuc": "HAM_B1", "ly_do_ngung": "Bảo trì"}, "1"
        )
        self.assertFalse(r_z_fail["ok"])

        r_s_fail = self.router.dispatch(
            mgr, Actions.INFRA_DEACTIVATE_SLOT, {"ma_vi_tri": "B1-01", "ly_do_ngung": "Sửa sàn"}, "2"
        )
        self.assertFalse(r_s_fail["ok"])

        # Vị trí B1-02 đang trống -> ngừng được; kích hoạt lại B1-01 (đang không có xe, có phân bổ) -> da_phan_cho
        r_s_ok = self.router.dispatch(
            mgr, Actions.INFRA_DEACTIVATE_SLOT, {"ma_vi_tri": "B1-02", "ly_do_ngung": "Sơn vạch"}, "3"
        )
        self.assertTrue(r_s_ok["ok"])
        self.db.vi_tri_do[0]["trang_thai_vi_tri"] = SlotStatus.NGUNG_SU_DUNG.value
        r_s_act = self.router.dispatch(mgr, Actions.INFRA_ACTIVATE_SLOT, {"ma_vi_tri": "B1-01"}, "4")
        self.assertTrue(r_s_act["ok"])
        self.assertEqual(r_s_act["data"]["slot"]["trang_thai_vi_tri"], SlotStatus.DA_PHAN_CHO.value)

        # Khi HAM_B1 trống hoàn toàn -> ngừng thành công
        self.db.khu_vuc[0]["so_xe_dang_gui"] = 0
        r_z_ok = self.router.dispatch(
            mgr, Actions.INFRA_DEACTIVATE_ZONE, {"ma_khu_vuc": "HAM_B1", "ly_do_ngung": "Bảo trì"}, "5"
        )
        self.assertTrue(r_z_ok["ok"])
        self.assertEqual(r_z_ok["data"]["zone"]["trang_thai_khu_vuc"], ZoneStatus.NGUNG_SU_DUNG.value)

        r_p = self.router.dispatch(
            mgr,
            Actions.PRICING_CREATE,
            {"ma_loai_xe": "O_TO", "nhom_ap_dung": "cho_oto_mac_dinh", "don_vi_tinh": "thang", "muc_phi": 1_250_000},
            "6",
        )
        self.assertTrue(r_p["ok"])

    def test_07_monthly_billing_formula_and_workflow_06_branches(self) -> None:
        """Kiểm tra công thức hóa đơn tháng & cả 2 nhánh Workflow 06 (QR Yes/No + Tiền mặt)."""
        # Thêm cư dân và 2 xe máy cho B2-02 (1 mặc định + 1 mua thêm + 2 xe máy)
        self.db.cu_dan.append({"ma_cu_dan": 2, "ma_tai_khoan": 99, "ma_can_ho": "B2-02", "ho_ten": "Hồng", "so_cccd": "079096000202", "so_dien_thoai": "0909200202", "email": "b202@daotien.vn", "dia_chi": "B2-02"})
        self.db.phuong_tien.append({"ma_phuong_tien": 2, "ma_cu_dan": 2, "ma_loai_xe": "XE_MAY", "bien_so_xe": "59B1-111.11", "trang_thai_xe": "dang_hoat_dong"})
        self.db.phuong_tien.append({"ma_phuong_tien": 3, "ma_cu_dan": 2, "ma_loai_xe": "XE_MAY", "bien_so_xe": "59B1-222.22", "trang_thai_xe": "dang_hoat_dong"})

        mgr = self._new_session(
            {"ma_tai_khoan": 2, "ten_dang_nhap": "quanly01", "vai_tro": Roles.QUAN_LY.value, "ma_nhan_vien": 1}
        )
        gen = self.router.dispatch(
            mgr, Actions.BILLING_GENERATE_MONTHLY, {"ky_thu_thang_nam": "11/2026"}, "1"
        )
        self.assertTrue(gen["ok"])
        self.assertEqual(gen["data"]["created"], 2)

        inv_a101 = next(h for h in self.db.hoa_don_thang if h["ma_can_ho"] == "A1-01")
        inv_b202 = next(h for h in self.db.hoa_don_thang if h["ma_can_ho"] == "B2-02")
        self.assertEqual(inv_a101["tong_tien_phai_thu"], 1_320_000.0)
        self.assertEqual(inv_b202["tong_tien_phai_thu"], 3_240_000.0)

        # Cư dân A1-01 tạo QR -> thử nhánh No (lỗi/quá hạn) -> hóa đơn vẫn chưa thanh toán
        res_a = self._new_session(
            {"ma_tai_khoan": 4, "ten_dang_nhap": "cudan_a101", "vai_tro": Roles.CU_DAN.value, "ma_can_ho": "A1-01"}
        )
        qr = self.router.dispatch(res_a, Actions.BILLING_CREATE_QR_PAYMENT, {"ma_hoa_don": inv_a101["ma_hoa_don"]}, "2")
        self.assertTrue(qr["ok"])
        self.assertGreater(len(qr["data"]["qr_image_base64"]), 50)

        no_res = self.router.dispatch(
            res_a,
            Actions.BILLING_COMPLETE_QR_PAYMENT,
            {"ma_hoa_don": inv_a101["ma_hoa_don"], "ma_giao_dich": qr["data"]["ma_giao_dich"], "ket_qua": "loi_qua_han"},
            "3",
        )
        self.assertTrue(no_res["ok"])
        self.assertFalse(no_res["data"]["success"])
        self.assertEqual(inv_a101["trang_thai_hoa_don"], InvoiceStatus.CHUA_THANH_TOAN.value)

        # Thử nhánh Yes -> thành công, sinh ma_bien_lai
        yes_res = self.router.dispatch(
            res_a,
            Actions.BILLING_COMPLETE_QR_PAYMENT,
            {"ma_hoa_don": inv_a101["ma_hoa_don"], "ma_giao_dich": qr["data"]["ma_giao_dich"], "ket_qua": "thanh_cong"},
            "4",
        )
        self.assertTrue(yes_res["ok"])
        self.assertTrue(yes_res["data"]["success"])
        self.assertEqual(inv_a101["trang_thai_hoa_don"], InvoiceStatus.DA_THANH_TOAN.value)
        self.assertTrue(str(yes_res["data"]["transaction"]["ma_bien_lai"]).startswith("BL-QR-"))

        # Hóa đơn B2-02 thu tiền mặt tại phòng bảo vệ
        cash = self.router.dispatch(
            mgr, Actions.BILLING_CONFIRM_CASH_PAYMENT, {"ma_hoa_don": inv_b202["ma_hoa_don"]}, "5"
        )
        self.assertTrue(cash["ok"])
        self.assertTrue(str(cash["data"]["ma_bien_lai"]).startswith("BL-TM-"))
        self.assertEqual(inv_b202["trang_thai_hoa_don"], InvoiceStatus.DA_THANH_TOAN.value)

        # Tổng hợp 1.7.3
        summary = self.router.dispatch(
            mgr, Actions.BILLING_SUMMARY, {"ky_thu_thang_nam": "11/2026"}, "6"
        )
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["data"]["tong_da_thu"], 4_560_000.0)
        self.assertEqual(summary["data"]["tong_con_no"], 0.0)

    def test_08_ui_offscreen_all_hau_views_and_portals(self) -> None:
        """Kiểm tra render offscreen toàn bộ UI của Hậu & ghép ManagerWindow/AdminWindow."""
        _app = QApplication.instance() or QApplication([])

        login = LoginDialog()
        self.assertEqual(login.btn_login.text(), "Đăng nhập")

        forgot = ForgotPasswordOTPDialog()
        self.assertEqual(forgot.stack.count(), 3)
        forgot.close()

        prof = ProfileView(vai_tro="admin")
        self.assertFalse(prof.btn_save_profile.isEnabled())

        hr = HRView()
        hr.set_residents(self.db.fetch_all("SELECT * FROM cu_dan cd JOIN tai_khoan tk"))
        self.assertEqual(hr.tbl_residents.rowCount(), 1)

        infra = InfraPricingView()
        infra.set_zones(self.db.khu_vuc)
        infra.set_slots(self.db.vi_tri_do)
        infra.set_pricing_list(self.db.bang_gia)
        self.assertEqual(infra.tbl_slots.rowCount(), 2)

        bills = BillsPaymentView()
        bills.set_invoices([{"ma_hoa_don": 1, "ky_thu_thang_nam": "11/2026", "tong_tien_phai_thu": 1_320_000, "trang_thai_hoa_don": "chua_thanh_toan"}])
        self.assertEqual(bills.tbl_invoices.rowCount(), 1)

        pay_dlg = PaymentWorkflow06Dialog({"ma_hoa_don": 1, "ky_thu_thang_nam": "11/2026", "tong_tien_phai_thu": 1_320_000})
        self.assertEqual(pay_dlg.stack.count(), 3)
        pay_dlg.close()

        fin = FinanceView(read_only=True)
        self.assertFalse(fin.btn_generate.isEnabled())

        mgr_win = ManagerWindow()
        self.assertIsInstance(mgr_win.hr_view, HRView)
        self.assertIsInstance(mgr_win.finance_view, FinanceView)

        adm_win = AdminWindow()
        self.assertTrue(adm_win.ro_hr_view.read_only)
        self.assertTrue(adm_win.ro_finance_view.read_only)


if __name__ == "__main__":
    unittest.main(verbosity=2)
