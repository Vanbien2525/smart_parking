"""Bộ kiểm thử tự động toàn phần cho Thành viên 5 (Ny — feat/gate-ai-ocr).

Kiểm thử đầy đủ:
- Chuẩn hóa biển số & Hybrid AI Recognizer (Gemini Vision + YOLO/EasyOCR fallback) + CameraWorker
- Workflow 03 (2.2.1–2.2.3.2): Cổng vào Ticketless Cư dân / Vãng lai & Anti-Passback
- Workflow 04 (2.3.1–2.3.3): Cổng ra đối chiếu ảnh Vào/Ra, tính phí vãng lai & thanh toán Tiền mặt / QR
- Màn hình 2.4: Quản lý ca trực, thống kê doanh thu ca & chốt ca
- Màn hình 2.5 (2.5.1–2.5.3): Tra cứu phương tiện nhân viên & lịch sử ảnh cổng vào/ra
- RBAC (chỉ Nhân viên/Quản lý; chặn Cư dân & Admin mutation) + Offscreen UI & MainWindow
"""

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Sequence
from unittest.mock import MagicMock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from client.ai.camera_worker import CameraWorker, encode_bgr_to_base64
from client.ai.plate_recognizer import PlateRecognizer, post_process_plate_text
from client.ui.main_window import MainWindow
from client.ui.staff.gate_in_view import GateInView
from client.ui.staff.gate_out_view import GateOutView
from client.ui.staff.shift_revenue_view import ShiftRevenueView
from client.ui.staff.staff_lookup_view import StaffLookupView
from server.core.file_logger import FileLogger
from server.core.router import Router
from server.core.session_manager import ClientSession
from server.core.state_store import StateStore
from server.handlers.gate_handler import GateHandler, register_gate_routes
from shared.constants import Roles, SlotStatus, VehicleTypes
from shared.protocol import Actions


def _make_sample_png_base64() -> str:
    img = QImage(160, 100, QImage.Format.Format_RGB32)
    img.fill(0xFF0D9488)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return base64.b64encode(bytes(buf.data())).decode("ascii")


class _NyFakeTx:
    def __init__(self, db: "_NyFakeDB") -> None:
        self.db = db

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        return self.db.fetch_one(sql, params)

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return self.db.fetch_all(sql, params)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self.db.execute(sql, params)


class _NyFakeDB:
    """In-memory DB mô phỏng chính xác schema 18 bảng cho luồng Cổng Vào/Ra & Báo cáo ca."""

    def __init__(self) -> None:
        self.can_ho: List[Dict[str, Any]] = [
            {"ma_can_ho": "A1-01", "toa_nha": "A", "so_tang": 1},
        ]
        self.cu_dan: List[Dict[str, Any]] = [
            {
                "ma_cu_dan": 1,
                "ma_tai_khoan": 4,
                "ma_can_ho": "A1-01",
                "ho_ten": "Lê Minh Cư Dân",
                "so_dien_thoai": "0909200101",
            },
        ]
        self.nhan_vien: List[Dict[str, Any]] = [
            {
                "ma_nhan_vien": 2,
                "ma_tai_khoan": 3,
                "ma_nhan_vien_code": "NV001",
                "ho_ten": "Trần Bảo Vệ",
                "chuc_vu": "nhan_vien_bai_xe",
            },
        ]
        self.khu_vuc: List[Dict[str, Any]] = [
            {
                "ma_khu_vuc": "KHU_A",
                "ten_khu_vuc": "Khu A - Xe máy chung cư",
                "ma_loai_xe": "XE_MAY",
                "tong_so_cho": 120,
                "so_xe_dang_gui": 0,
                "trang_thai_khu_vuc": "hoat_dong",
            },
            {
                "ma_khu_vuc": "HAM_B1",
                "ten_khu_vuc": "Hầm B1 - Ô tô Cư dân",
                "ma_loai_xe": "O_TO",
                "tong_so_cho": 12,
                "so_xe_dang_gui": 0,
                "trang_thai_khu_vuc": "hoat_dong",
            },
            {
                "ma_khu_vuc": "KHU_C",
                "ten_khu_vuc": "Khu C - Ô tô Vãng lai/Đầu bãi",
                "ma_loai_xe": "O_TO",
                "tong_so_cho": 8,
                "so_xe_dang_gui": 0,
                "trang_thai_khu_vuc": "hoat_dong",
            },
        ]
        self.vi_tri_do: List[Dict[str, Any]] = [
            {
                "ma_vi_tri": "B1-01",
                "ma_khu_vuc": "HAM_B1",
                "ten_vi_tri": "Ô B1-01",
                "nhom_vi_tri": "vi_tri_dau_bai",
                "trang_thai_vi_tri": "da_phan_cho",
                "thoi_gian_cap_nhat": datetime.now(),
            },
            {
                "ma_vi_tri": "B1-02",
                "ma_khu_vuc": "HAM_B1",
                "ten_vi_tri": "Ô B1-02",
                "nhom_vi_tri": "vi_tri_thuong",
                "trang_thai_vi_tri": "trong",
                "thoi_gian_cap_nhat": datetime.now(),
            },
            {
                "ma_vi_tri": "C-01",
                "ma_khu_vuc": "KHU_C",
                "ten_vi_tri": "Ô C-01",
                "nhom_vi_tri": "vi_tri_vang_lai",
                "trang_thai_vi_tri": "trong",
                "thoi_gian_cap_nhat": datetime.now(),
            },
        ]
        self.phuong_tien: List[Dict[str, Any]] = [
            {
                "ma_phuong_tien": 1,
                "ma_cu_dan": 1,
                "ma_loai_xe": "O_TO",
                "bien_so_xe": "51H-888.99",
                "ten_hang_dong_xe": "Toyota Camry 2.5Q",
                "mau_xe": "Đen",
                "trang_thai_xe": "dang_hoat_dong",
            },
            {
                "ma_phuong_tien": 2,
                "ma_cu_dan": 1,
                "ma_loai_xe": "XE_MAY",
                "bien_so_xe": "59A1-123.45",
                "ten_hang_dong_xe": "Honda SH 150i",
                "mau_xe": "Trắng",
                "trang_thai_xe": "dang_hoat_dong",
            },
        ]
        self.quan_ly_phuong_tien_ra_vao: List[Dict[str, Any]] = [
            {
                "ma_quan_ly_xe": 1,
                "ma_phuong_tien": 1,
                "ma_vi_tri_co_dinh": "B1-01",
                "ma_khu_vuc_phan_cong": "HAM_B1",
                "thoi_gian_vao": None,
                "thoi_gian_ra": None,
                "dang_trong_bai": False,
            },
            {
                "ma_quan_ly_xe": 2,
                "ma_phuong_tien": 2,
                "ma_vi_tri_co_dinh": None,
                "ma_khu_vuc_phan_cong": "KHU_A",
                "thoi_gian_vao": None,
                "thoi_gian_ra": None,
                "dang_trong_bai": False,
            },
        ]
        self.bang_gia: List[Dict[str, Any]] = [
            {
                "ma_bang_gia": 1,
                "ma_loai_xe": "O_TO",
                "nhom_ap_dung": "khach_vang_lai",
                "don_vi_tinh": "gio",
                "muc_phi": 25000.0,
                "thoi_gian_bat_dau": datetime(2026, 1, 1),
                "trang_thai_gia": "dang_ap_dung",
            },
            {
                "ma_bang_gia": 2,
                "ma_loai_xe": "XE_MAY",
                "nhom_ap_dung": "khach_vang_lai",
                "don_vi_tinh": "luot",
                "muc_phi": 5000.0,
                "thoi_gian_bat_dau": datetime(2026, 1, 1),
                "trang_thai_gia": "dang_ap_dung",
            },
        ]
        self.bao_cao_luot_va_thu_phi: List[Dict[str, Any]] = []
        self._report_seq = 1

    @contextmanager
    def transaction(self):
        yield _NyFakeTx(self)

    def _zone(self, code: Optional[str]) -> Optional[Dict[str, Any]]:
        return next((z for z in self.khu_vuc if z["ma_khu_vuc"] == code), None)

    def _slot(self, code: Optional[str]) -> Optional[Dict[str, Any]]:
        return next((s for s in self.vi_tri_do if s["ma_vi_tri"] == code), None)

    def _ql(self, ma_pt: int) -> Optional[Dict[str, Any]]:
        return next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_phuong_tien"] == ma_pt), None)

    def _cd(self, ma_cd: int) -> Optional[Dict[str, Any]]:
        return next((c for c in self.cu_dan if c["ma_cu_dan"] == ma_cd), None)

    def _ch(self, ma_ch: str) -> Optional[Dict[str, Any]]:
        return next((c for c in self.can_ho if c["ma_can_ho"] == ma_ch), None)

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        q = " ".join(sql.split())

        if "FROM nhan_vien WHERE ma_nhan_vien" in q:
            sid = int(params[0])
            return [dict(n) for n in self.nhan_vien if n["ma_nhan_vien"] == sid]

        if "FROM nhan_vien WHERE ma_tai_khoan" in q:
            aid = int(params[0])
            return [dict(n) for n in self.nhan_vien if n["ma_tai_khoan"] == aid]

        if "FROM quan_ly_phuong_tien_ra_vao ql" in q and "ql.dang_trong_bai = TRUE" in q:
            plate = str(params[0])
            out: List[Dict[str, Any]] = []
            for pt in self.phuong_tien:
                if pt["bien_so_xe"] != plate:
                    continue
                ql = self._ql(pt["ma_phuong_tien"])
                if not ql or not ql["dang_trong_bai"]:
                    continue
                cd = self._cd(pt["ma_cu_dan"]) or {}
                vt = self._slot(ql.get("ma_vi_tri_co_dinh")) or {}
                kv_code = ql.get("ma_khu_vuc_phan_cong") or vt.get("ma_khu_vuc")
                kv = self._zone(kv_code) or {}
                out.append(
                    {
                        "bien_so_xe": pt["bien_so_xe"],
                        "ma_phuong_tien": pt["ma_phuong_tien"],
                        "ma_loai_xe": pt["ma_loai_xe"],
                        "ten_hang_dong_xe": pt["ten_hang_dong_xe"],
                        "mau_xe": pt["mau_xe"],
                        "ho_ten": cd.get("ho_ten"),
                        "so_dien_thoai": cd.get("so_dien_thoai"),
                        "ma_can_ho": cd.get("ma_can_ho"),
                        "ma_vi_tri_co_dinh": ql.get("ma_vi_tri_co_dinh"),
                        "ma_khu_vuc_phan_cong": ql.get("ma_khu_vuc_phan_cong"),
                        "thoi_gian_vao": ql.get("thoi_gian_vao"),
                        "ten_vi_tri_co_dinh": vt.get("ten_vi_tri"),
                        "ma_khu_vuc_vi_tri": vt.get("ma_khu_vuc"),
                        "ten_khu_vuc": kv.get("ten_khu_vuc"),
                        "vi_tri": ql.get("ma_vi_tri_co_dinh") or ql.get("ma_khu_vuc_phan_cong"),
                    }
                )
            return out

        if "FROM phuong_tien pt" in q and "WHERE pt.bien_so_xe = %s" in q:
            plate, vstatus = str(params[0]), str(params[1])
            out = []
            for pt in self.phuong_tien:
                if pt["bien_so_xe"] != plate or pt["trang_thai_xe"] != vstatus:
                    continue
                cd = self._cd(pt["ma_cu_dan"]) or {}
                ch = self._ch(str(cd.get("ma_can_ho") or "")) or {}
                ql = self._ql(pt["ma_phuong_tien"]) or {}
                vt = self._slot(ql.get("ma_vi_tri_co_dinh")) or {}
                kv_code = ql.get("ma_khu_vuc_phan_cong") or vt.get("ma_khu_vuc")
                kv = self._zone(kv_code) or {}
                out.append(
                    {
                        **pt,
                        "ho_ten": cd.get("ho_ten"),
                        "so_dien_thoai": cd.get("so_dien_thoai"),
                        "ma_can_ho": cd.get("ma_can_ho"),
                        "toa_nha": ch.get("toa_nha"),
                        "so_tang": ch.get("so_tang"),
                        "ma_quan_ly_xe": ql.get("ma_quan_ly_xe"),
                        "ma_vi_tri_co_dinh": ql.get("ma_vi_tri_co_dinh"),
                        "ma_khu_vuc_phan_cong": ql.get("ma_khu_vuc_phan_cong"),
                        "ten_vi_tri_co_dinh": vt.get("ten_vi_tri"),
                        "ma_khu_vuc_vi_tri": vt.get("ma_khu_vuc"),
                        "ten_khu_vuc": kv.get("ten_khu_vuc"),
                    }
                )
            return out

        if "FROM phuong_tien pt" in q and "WHERE pt.trang_thai_xe = %s" in q:
            vstatus = str(params[0])
            out = []
            for pt in self.phuong_tien:
                if pt["trang_thai_xe"] != vstatus:
                    continue
                cd = self._cd(pt["ma_cu_dan"]) or {}
                ql = self._ql(pt["ma_phuong_tien"]) or {}
                vt = self._slot(ql.get("ma_vi_tri_co_dinh")) or {}
                kv_code = ql.get("ma_khu_vuc_phan_cong") or vt.get("ma_khu_vuc")
                kv = self._zone(kv_code) or {}
                out.append(
                    {
                        **pt,
                        "ho_ten": cd.get("ho_ten"),
                        "so_dien_thoai": cd.get("so_dien_thoai"),
                        "ma_can_ho": cd.get("ma_can_ho"),
                        "dang_trong_bai": ql.get("dang_trong_bai", False),
                        "thoi_gian_vao": ql.get("thoi_gian_vao"),
                        "thoi_gian_ra": ql.get("thoi_gian_ra"),
                        "ma_vi_tri_co_dinh": ql.get("ma_vi_tri_co_dinh"),
                        "ma_khu_vuc_phan_cong": ql.get("ma_khu_vuc_phan_cong"),
                        "ten_vi_tri_co_dinh": vt.get("ten_vi_tri"),
                        "ma_khu_vuc_vi_tri": vt.get("ma_khu_vuc"),
                        "ten_khu_vuc": kv.get("ten_khu_vuc"),
                    }
                )
            return out

        if "FROM vi_tri_do vt" in q and "WHERE vt.ma_vi_tri = %s" in q:
            vt = self._slot(str(params[0]))
            if not vt:
                return []
            kv = self._zone(vt["ma_khu_vuc"]) or {}
            return [{**vt, **kv, "ma_vi_tri": vt["ma_vi_tri"], "ma_khu_vuc": vt["ma_khu_vuc"]}]

        if "FROM vi_tri_do vt" in q:
            vtype, zstatus, sstatus = str(params[0]), str(params[1]), str(params[2])
            out = []
            for vt in self.vi_tri_do:
                kv = self._zone(vt["ma_khu_vuc"])
                if not kv:
                    continue
                if (
                    kv["ma_loai_xe"] == vtype
                    and kv["trang_thai_khu_vuc"] == zstatus
                    and vt["trang_thai_vi_tri"] == sstatus
                    and (kv["tong_so_cho"] - kv["so_xe_dang_gui"]) > 0
                ):
                    out.append({**vt, "ten_khu_vuc": kv["ten_khu_vuc"], "ma_loai_xe": kv["ma_loai_xe"], "trang_thai_khu_vuc": kv["trang_thai_khu_vuc"], "tong_so_cho": kv["tong_so_cho"], "so_xe_dang_gui": kv["so_xe_dang_gui"]})
            out.sort(key=lambda x: (0 if x["nhom_vi_tri"] == "vi_tri_vang_lai" else 1, x["ma_vi_tri"]))
            return out

        if "FROM khu_vuc" in q and "WHERE ma_khu_vuc = %s" in q:
            kv = self._zone(str(params[0]))
            return [dict(kv)] if kv else []

        if "FROM khu_vuc" in q and "WHERE ma_loai_xe = %s" in q:
            vtype, zstatus = str(params[0]), str(params[1])
            return [
                {**z, "so_cho_trong": max(0, z["tong_so_cho"] - z["so_xe_dang_gui"])}
                for z in self.khu_vuc
                if z["ma_loai_xe"] == vtype and z["trang_thai_khu_vuc"] == zstatus and (z["tong_so_cho"] - z["so_xe_dang_gui"]) > 0
            ]

        if "FROM bang_gia" in q:
            vtype, nhom, st = str(params[0]), str(params[1]), str(params[2])
            return [
                dict(b)
                for b in self.bang_gia
                if b["ma_loai_xe"] == vtype and b["nhom_ap_dung"] == nhom and b["trang_thai_gia"] == st
            ]

        if "SELECT ma_bao_cao FROM bao_cao_luot_va_thu_phi" in q:
            sid, kv, day_val, ca = int(params[0]), str(params[1]), str(params[2]), str(params[3])
            return [
                dict(r)
                for r in self.bao_cao_luot_va_thu_phi
                if r["ma_nhan_vien"] == sid
                and r["ma_khu_vuc"] == kv
                and str(r["ngay_ghi_nhan"]) == day_val
                and r["ca_lam_viec"] == ca
            ]

        if "FROM bao_cao_luot_va_thu_phi" in q:
            sid, day_val, ca = int(params[0]), str(params[1]), str(params[2])
            return [
                dict(r)
                for r in self.bao_cao_luot_va_thu_phi
                if r["ma_nhan_vien"] == sid
                and str(r["ngay_ghi_nhan"]) == day_val
                and r["ca_lam_viec"] == ca
            ]

        return []

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        q = " ".join(sql.split())

        if q.startswith("UPDATE quan_ly_phuong_tien_ra_vao SET dang_trong_bai = TRUE"):
            dt, ma_pt = params[0], int(params[1])
            ql = self._ql(ma_pt)
            if ql:
                ql["dang_trong_bai"] = True
                ql["thoi_gian_vao"] = dt
                return 1
            return 0

        if q.startswith("UPDATE quan_ly_phuong_tien_ra_vao SET dang_trong_bai = FALSE"):
            dt, ma_pt = params[0], int(params[1])
            ql = self._ql(ma_pt)
            if ql:
                ql["dang_trong_bai"] = False
                ql["thoi_gian_ra"] = dt
                return 1
            return 0

        if q.startswith("UPDATE vi_tri_do"):
            if len(params) == 4:
                new_st, dt, code, expected_st = params
                vt = self._slot(str(code))
                if vt and vt["trang_thai_vi_tri"] == expected_st:
                    vt["trang_thai_vi_tri"] = new_st
                    vt["thoi_gian_cap_nhat"] = dt
                    return 1
                return 0
            new_st, dt, code = params
            vt = self._slot(str(code))
            if vt:
                vt["trang_thai_vi_tri"] = new_st
                vt["thoi_gian_cap_nhat"] = dt
                return 1
            return 0

        if q.startswith("UPDATE khu_vuc SET so_xe_dang_gui = LEAST"):
            kv = self._zone(str(params[0]))
            if kv:
                kv["so_xe_dang_gui"] = min(kv["tong_so_cho"], kv["so_xe_dang_gui"] + 1)
                return 1
            return 0

        if q.startswith("UPDATE khu_vuc SET so_xe_dang_gui = so_xe_dang_gui + 1"):
            kv = self._zone(str(params[0]))
            if kv and kv["so_xe_dang_gui"] < kv["tong_so_cho"]:
                kv["so_xe_dang_gui"] += 1
                return 1
            return 0

        if q.startswith("UPDATE khu_vuc SET so_xe_dang_gui = GREATEST"):
            kv = self._zone(str(params[0]))
            if kv:
                kv["so_xe_dang_gui"] = max(0, kv["so_xe_dang_gui"] - 1)
                return 1
            return 0

        if q.startswith("UPDATE bao_cao_luot_va_thu_phi"):
            d_oto, d_xm, d_cd, d_tien, ma_bc = params
            for r in self.bao_cao_luot_va_thu_phi:
                if r["ma_bao_cao"] == int(ma_bc):
                    r["so_luot_oto_vang_lai"] += int(d_oto)
                    r["so_luot_xemay_vang_lai"] += int(d_xm)
                    r["so_luot_xe_cu_dan"] += int(d_cd)
                    r["tong_tien_thu_vang_lai"] += float(d_tien)
                    return 1
            return 0

        if q.startswith("INSERT INTO bao_cao_luot_va_thu_phi"):
            sid, kv, day_val, ca, d_oto, d_xm, d_cd, d_tien = params
            self.bao_cao_luot_va_thu_phi.append(
                {
                    "ma_bao_cao": self._report_seq,
                    "ma_nhan_vien": int(sid),
                    "ma_khu_vuc": str(kv),
                    "ngay_ghi_nhan": str(day_val),
                    "ca_lam_viec": str(ca),
                    "so_luot_oto_vang_lai": int(d_oto),
                    "so_luot_xemay_vang_lai": int(d_xm),
                    "so_luot_xe_cu_dan": int(d_cd),
                    "tong_tien_thu_vang_lai": float(d_tien),
                }
            )
            self._report_seq += 1
            return 1

        return 0


class GateAITestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.logger = FileLogger(self.tmp_dir / "logs")
        self.state_store = StateStore(self.tmp_dir / "active_vang_lai.json")
        self.db = _NyFakeDB()
        self.router = Router(file_logger=self.logger)
        self.handler = register_gate_routes(
            self.router,
            file_logger=self.logger,
            state_store=self.state_store,
            db_provider=self.db,
            gates_in_dir=self.tmp_dir / "gates_in",
            gates_out_dir=self.tmp_dir / "gates_out",
            shift_state_file=self.tmp_dir / "shift_sessions.json",
        )
        self.staff_session = self._make_session(3, "nhanvien01", Roles.NHAN_VIEN.value, ma_nhan_vien=2)
        self.resident_session = self._make_session(4, "cudan_a101", Roles.CU_DAN.value, ma_cu_dan=1)
        self.admin_session = self._make_session(1, "admin01", Roles.ADMIN.value)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _make_session(
        ma_tk: int,
        username: str,
        role: str,
        ma_nhan_vien: Optional[int] = None,
        ma_cu_dan: Optional[int] = None,
    ) -> ClientSession:
        return ClientSession(
            session_id=f"sess-{username}",
            sock=MagicMock(),
            ip_address="127.0.0.1",
            port=5000,
            ma_tai_khoan=ma_tk,
            ten_dang_nhap=username,
            vai_tro=role,
            ho_ten=username,
            ma_nhan_vien=ma_nhan_vien,
            ma_cu_dan=ma_cu_dan,
        )

    def _dispatch(self, session: ClientSession, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.router.dispatch(session, action, payload, request_id="req-test")

    def test_01_ocr_post_process_and_hybrid_recognizer(self) -> None:
        self.assertEqual(post_process_plate_text("51H88899"), ("51H-888.99", VehicleTypes.O_TO.value))
        self.assertEqual(post_process_plate_text("59A112345"), ("59A1-123.45", VehicleTypes.XE_MAY.value))
        self.assertEqual(post_process_plate_text("5I812345"), ("51B-123.45", VehicleTypes.O_TO.value))
        self.assertEqual(post_process_plate_text("3O099988"), ("30D-999.88", VehicleTypes.O_TO.value))

        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        cam = CameraWorker()
        cam.set_mock_frame(frame)
        snap = cam.capture_snapshot()
        self.assertIsNotNone(snap)
        self.assertGreater(len(encode_bgr_to_base64(snap)), 20)

        # Kiểm tra Hybrid fallback: Gemini trả None -> tự động chuyển sang YOLO+EasyOCR
        rec = PlateRecognizer(api_key="")
        rec.recognize_with_yolo_easyocr = lambda _f: {  # type: ignore[method-assign]
            "success": True,
            "bien_so": "51H-888.99",
            "loai_xe": VehicleTypes.O_TO.value,
            "confidence": 0.92,
            "engine_used": "YOLO + EasyOCR (Offline)",
            "crop_bgr": frame[20:100, 40:200],
            "raw_text": "51H88899",
        }
        out = rec.recognize_frame(frame)
        self.assertTrue(out["success"])
        self.assertEqual(out["bien_so"], "51H-888.99")
        self.assertFalse(out["plate_crop_qimage"].isNull())

    def test_02_workflow03_04_resident_entry_antipassback_and_exit(self) -> None:
        sample_b64 = _make_sample_png_base64()

        # 1. Check xe cư dân cổng vào (2.2.1 - 2.2.2)
        chk = self._dispatch(self.staff_session, Actions.GATE_IN_CHECK, {"bien_so": "51H88899"})
        self.assertTrue(chk["ok"], chk.get("error"))
        self.assertEqual(chk["data"]["phan_nhanh"], "cu_dan")
        self.assertEqual(chk["data"]["ma_vi_tri_co_dinh"], "B1-01")

        # 2. Xác nhận vào bãi (2.2.3.1)
        ent = self._dispatch(
            self.staff_session,
            Actions.GATE_IN_CONFIRM,
            {"bien_so": "51H-888.99", "loai_khach": "cu_dan", "gate_image_base64": sample_b64},
        )
        self.assertTrue(ent["ok"], ent.get("error"))
        self.assertTrue(self.db.quan_ly_phuong_tien_ra_vao[0]["dang_trong_bai"])
        self.assertEqual(self.db._slot("B1-01")["trang_thai_vi_tri"], SlotStatus.DANG_CO_XE.value)
        self.assertEqual(self.db._zone("HAM_B1")["so_xe_dang_gui"], 1)

        # 3. Anti-Passback: cho cùng biển số vào lần nữa phải bị chặn
        dup = self._dispatch(self.staff_session, Actions.GATE_IN_CHECK, {"bien_so": "51H-888.99"})
        self.assertFalse(dup["ok"])
        self.assertIn("Anti-Passback", dup["error"])

        # 4. Check cổng ra (2.3.1 - 2.3.2): phí = 0 VNĐ và có ảnh lúc vào
        out_chk = self._dispatch(self.staff_session, Actions.GATE_OUT_CHECK, {"bien_so": "51H-888.99"})
        self.assertTrue(out_chk["ok"], out_chk.get("error"))
        self.assertEqual(out_chk["data"]["loai_khach"], "cu_dan")
        self.assertEqual(out_chk["data"]["so_tien_phai_thu"], 0.0)
        self.assertTrue(bool(out_chk["data"]["anh_cong_vao_base64"]))

        # 5. Xác nhận xe cư dân ra bãi (2.3.3)
        out_cfm = self._dispatch(
            self.staff_session,
            Actions.GATE_OUT_CONFIRM,
            {"bien_so": "51H-888.99", "gate_out_image_base64": sample_b64},
        )
        self.assertTrue(out_cfm["ok"], out_cfm.get("error"))
        self.assertFalse(self.db.quan_ly_phuong_tien_ra_vao[0]["dang_trong_bai"])
        self.assertEqual(self.db._slot("B1-01")["trang_thai_vi_tri"], SlotStatus.DA_PHAN_CHO.value)
        self.assertEqual(self.db._zone("HAM_B1")["so_xe_dang_gui"], 0)
        self.assertEqual(self.db.bao_cao_luot_va_thu_phi[0]["so_luot_xe_cu_dan"], 1)

    def test_03_workflow03_04_visitor_ticketless_fee_and_shift_revenue(self) -> None:
        sample_b64 = _make_sample_png_base64()

        # 1. Ô tô vãng lai vào cổng (2.2.2 -> 2.2.3.2)
        chk = self._dispatch(self.staff_session, Actions.GATE_IN_CHECK, {"bien_so": "30A99988", "loai_xe": "O_TO"})
        self.assertTrue(chk["ok"], chk.get("error"))
        self.assertEqual(chk["data"]["phan_nhanh"], "vang_lai")
        self.assertEqual(chk["data"]["suggested_slot"]["ma_vi_tri"], "C-01")

        ent = self._dispatch(
            self.staff_session,
            Actions.GATE_IN_CONFIRM,
            {"bien_so": "30A-999.88", "ma_loai_xe": "O_TO", "gate_image_base64": sample_b64},
        )
        self.assertTrue(ent["ok"], ent.get("error"))
        self.assertEqual(self.db._slot("C-01")["trang_thai_vi_tri"], SlotStatus.DANG_CO_XE.value)
        self.assertEqual(self.db._zone("KHU_C")["so_xe_dang_gui"], 1)
        self.assertIsNotNone(self.state_store.get_visitor_by_plate("30A-999.88"))

        # Lùi thời gian vào 90 phút để kiểm tra tính phí 2 giờ (2 * 25,000 = 50,000)
        with self.state_store._lock:
            for sess_val in self.state_store._sessions.values():
                if sess_val.get("bien_so_xe") == "30A-999.88":
                    sess_val["thoi_gian_vao"] = (datetime.now() - timedelta(minutes=90)).isoformat(timespec="seconds")
            self.state_store._persist_locked()

        out_chk = self._dispatch(self.staff_session, Actions.GATE_OUT_CHECK, {"bien_so": "30A-999.88"})
        self.assertTrue(out_chk["ok"], out_chk.get("error"))
        self.assertEqual(out_chk["data"]["so_tien_phai_thu"], 50000.0)
        self.assertTrue(bool(out_chk["data"]["qr_image_base64"]))

        out_cfm = self._dispatch(
            self.staff_session,
            Actions.GATE_OUT_CONFIRM,
            {
                "bien_so": "30A-999.88",
                "phuong_thuc_thanh_toan": "chuyen_khoan_qr",
                "gate_out_image_base64": sample_b64,
            },
        )
        self.assertTrue(out_cfm["ok"], out_cfm.get("error"))
        self.assertIsNone(self.state_store.get_visitor_by_plate("30A-999.88"))
        self.assertEqual(self.db._slot("C-01")["trang_thai_vi_tri"], SlotStatus.TRONG.value)
        self.assertEqual(self.db._zone("KHU_C")["so_xe_dang_gui"], 0)

        # 2. Thêm 1 lượt xe máy vãng lai thanh toán tiền mặt (5,000)
        ent_xm = self._dispatch(
            self.staff_session,
            Actions.GATE_IN_CONFIRM,
            {"bien_so": "59P1-456.78", "ma_loai_xe": "XE_MAY"},
        )
        self.assertTrue(ent_xm["ok"], ent_xm.get("error"))
        out_xm = self._dispatch(
            self.staff_session,
            Actions.GATE_OUT_CONFIRM,
            {"bien_so": "59P1-456.78", "phuong_thuc_thanh_toan": "tien_mat"},
        )
        self.assertTrue(out_xm["ok"], out_xm.get("error"))

        # 3. Kiểm tra doanh thu ca (2.4) & chốt ca
        rev = self._dispatch(self.staff_session, Actions.SHIFT_GET_REVENUE, {})
        self.assertTrue(rev["ok"], rev.get("error"))
        kpi = rev["data"]["kpi"]
        self.assertEqual(kpi["tong_doanh_thu"], 55000.0)
        self.assertEqual(kpi["doanh_thu_qr"], 50000.0)
        self.assertEqual(kpi["doanh_thu_tien_mat"], 5000.0)
        self.assertEqual(kpi["so_luot_thu_phi"], 2)

        closed = self._dispatch(self.staff_session, Actions.SHIFT_CLOSE, {"ghi_chu": "Bàn giao đủ"})
        self.assertTrue(closed["ok"], closed.get("error"))
        self.assertEqual(closed["data"]["closed_shift"]["trang_thai_ca"], "da_dong")
        self.assertEqual(closed["data"]["closed_shift"]["tong_doanh_thu_chot_ca"], 55000.0)

    def test_04_staff_lookup_and_rbac_permissions(self) -> None:
        sample_b64 = _make_sample_png_base64()
        self._dispatch(
            self.staff_session,
            Actions.GATE_IN_CONFIRM,
            {"bien_so": "51H-888.99", "gate_image_base64": sample_b64},
        )

        # 2.5.1 - 2.5.3 Tra cứu tìm thấy xe cư dân đang trong bãi kèm ảnh cổng vào
        res = self._dispatch(
            self.staff_session,
            Actions.GATE_LOOKUP_VEHICLE,
            {"search_plate": "88899", "trang_thai_filter": "dang_do_trong_bai"},
        )
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["data"]["total"], 1)
        item = res["data"]["items"][0]
        self.assertEqual(item["bien_so_xe"], "51H-888.99")
        self.assertTrue(item["dang_trong_bai"])
        self.assertTrue(bool(item["history"][0]["anh_vao_base64"]))

        # RBAC: Cư dân không được gọi nghiệp vụ cổng; Admin không được gọi thao tác ghi
        forb_res = self._dispatch(self.resident_session, Actions.GATE_IN_CHECK, {"bien_so": "51H-888.99"})
        self.assertFalse(forb_res["ok"])
        self.assertEqual(forb_res["error_code"], "FORBIDDEN")

        ro_admin = self._dispatch(
            self.admin_session,
            Actions.GATE_IN_CONFIRM,
            {"bien_so": "30A-999.88"},
        )
        self.assertFalse(ro_admin["ok"])
        self.assertEqual(ro_admin["error_code"], "ADMIN_READ_ONLY")

    def test_05_offscreen_ui_views_and_mainwindow_staff(self) -> None:
        win = MainWindow({"vai_tro": Roles.NHAN_VIEN.value, "ho_ten": "Trần Bảo Vệ"})
        try:
            gate_in = win.get_page("2.2_gate_in")
            gate_out = win.get_page("2.3_gate_out")
            shift_view = win.get_page("2.4_shift_revenue")
            lookup_view = win.get_page("2.5_lookup")

            self.assertIsInstance(gate_in, GateInView)
            self.assertIsInstance(gate_out, GateOutView)
            self.assertIsInstance(shift_view, ShiftRevenueView)
            self.assertIsInstance(lookup_view, StaffLookupView)

            gate_in.apply_check_result(
                {
                    "phan_nhanh": "vang_lai",
                    "bien_so_xe": "30A-999.88",
                    "ma_loai_xe": "O_TO",
                    "available_car_slots": [{"ma_vi_tri": "C-01", "ma_khu_vuc": "KHU_C", "ten_khu_vuc": "Khu C"}],
                    "available_moto_zones": [],
                }
            )
            self.assertTrue(gate_in.btn_open_gate.isEnabled())

            gate_out.apply_out_check_data(
                {
                    "loai_khach": "vang_lai",
                    "bien_so_xe": "30A-999.88",
                    "ma_loai_xe": "O_TO",
                    "ten_vi_tri": "C-01",
                    "ten_khu_vuc": "Khu C",
                    "thoi_gian_vao": "2026-11-16T10:00:00",
                    "thoi_gian_ra": "2026-11-16T11:30:00",
                    "thoi_luong_text": "1 giờ 30 phút",
                    "so_tien_phai_thu": 50000,
                    "cong_thuc_text": "2 giờ × 25,000 VNĐ/giờ",
                    "qr_image_base64": GateHandler._render_qr_base64("TEST"),
                }
            )
            self.assertTrue(gate_out.btn_confirm_exit.isEnabled())
            self.assertTrue(gate_out.btn_show_qr.isEnabled())

            shift_view.apply_shift_data(
                {
                    "current_shift": {"ma_ca_lam_viec": "CA-01", "ho_ten": "Trần Bảo Vệ", "ten_ca": "Ca Sáng", "trang_thai_ca": "dang_mo"},
                    "kpi": {"tong_doanh_thu": 50000, "doanh_thu_tien_mat": 0, "doanh_thu_qr": 50000, "so_luot_thu_phi": 1},
                    "transactions": [{"ma_tham_chieu": "VL-01", "thoi_gian_ra": "2026-11-16T11:30:00", "bien_so_xe": "30A-999.88", "ma_loai_xe": "O_TO", "phuong_thuc_thanh_toan": "chuyen_khoan_qr", "tien_thu": 50000}],
                    "shift_history": [],
                }
            )
            self.assertEqual(shift_view.tbl_transactions.rowCount(), 1)

            lookup_view.apply_lookup_data(
                {
                    "items": [
                        {
                            "bien_so_xe": "51H-888.99",
                            "ma_loai_xe": "O_TO",
                            "phan_loai_text": "Cư dân · A1-01",
                            "dang_trong_bai": True,
                            "ten_vi_tri": "B1-01",
                            "ten_khu_vuc": "Hầm B1",
                            "ho_ten_chu_xe": "Lê Minh Cư Dân",
                            "ma_can_ho": "A1-01",
                            "so_dien_thoai": "0909200101",
                            "thoi_gian_vao_gan_nhat": "2026-11-16T08:00:00",
                            "history": [],
                        }
                    ]
                }
            )
            self.assertEqual(lookup_view.tbl_vehicles.rowCount(), 1)
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
