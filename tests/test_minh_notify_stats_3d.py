"""Bộ kiểm thử tự động toàn phần cho Thành viên 3 (Minh — feat/ui-notify-stats)."""

import base64
import json
import os
import struct
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from client.ui.admin.admin_window import AdminWindow
from client.ui.main_window import MainWindow
from client.ui.manager.manager_window import ManagerWindow
from client.ui.manager.stats_history_view import (
    HistoryDetailDialog,
    ManagerHistoryView,
    ManagerMonitorView,
    ManagerNotifyView,
    ManagerStatsView,
)
from client.ui.resident.resident_notify_view import ResidentNotifyView
from client.ui.staff.staff_monitor_notify_view import (
    StaffMonitorView,
    StaffNotifyInboxView,
)
from client.ui.theme.parking_3d_widget import Parking3DSceneWidget
from server.core.file_logger import FileLogger
from server.core.router import Router
from server.core.session_manager import ClientSession, SessionManager
from server.core.state_store import StateStore
from server.handlers.monitor_admin_handler import register_monitor_admin_routes
from server.handlers.notify_handler import register_notify_routes
from shared.constants import (
    NotifyAckStatus,
    NotifyLevels,
    NotifyRecipientGroups,
    NotifySendStatus,
    NotifyTypes,
    Roles,
    SlotStatus,
)
from shared.protocol import Actions, HEADER_STRUCT, MsgType, PushEvents


class _CapturingSocket:
    """Socket giả lập lưu trọn vẹn các frame length-prefix đã gửi qua TCP."""

    def __init__(self) -> None:
        self.raw_frames: List[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.raw_frames.append(data)

    def shutdown(self, _how: int) -> None:
        pass

    def close(self) -> None:
        pass

    def decoded_messages(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for raw in self.raw_frames:
            (length,) = HEADER_STRUCT.unpack(raw[: HEADER_STRUCT.size])
            payload = raw[HEADER_STRUCT.size : HEADER_STRUCT.size + length]
            out.append(json.loads(payload.decode("utf-8")))
        return out


class _MinhFakeDB:
    """In-memory DB mô phỏng đầy đủ bảng cho Notify, Monitor, Stats & History."""

    def __init__(self) -> None:
        self.tai_khoan: List[Dict[str, Any]] = [
            {"ma_tai_khoan": 1, "ten_dang_nhap": "admin01", "vai_tro": "admin", "trang_thai": "hoat_dong"},
            {"ma_tai_khoan": 2, "ten_dang_nhap": "quanly01", "vai_tro": "quan_ly", "trang_thai": "hoat_dong"},
            {"ma_tai_khoan": 3, "ten_dang_nhap": "nhanvien01", "vai_tro": "nhan_vien", "trang_thai": "hoat_dong"},
            {"ma_tai_khoan": 4, "ten_dang_nhap": "cudan_a101", "vai_tro": "cu_dan", "trang_thai": "hoat_dong"},
        ]
        self.nhan_vien: List[Dict[str, Any]] = [
            {"ma_nhan_vien": 1, "ma_tai_khoan": 2, "ho_ten": "Nguyễn Quản Lý"},
            {"ma_nhan_vien": 2, "ma_tai_khoan": 3, "ho_ten": "Trần Bảo Vệ"},
        ]
        self.cu_dan: List[Dict[str, Any]] = [
            {"ma_cu_dan": 1, "ma_tai_khoan": 4, "ma_can_ho": "A1-01", "ho_ten": "Lê Minh Cư Dân"},
        ]
        self.khu_vuc: List[Dict[str, Any]] = [
            {"ma_khu_vuc": "HAM_B1", "ten_khu_vuc": "Hầm B1 Ô tô", "ma_loai_xe": "O_TO", "tong_so_cho": 4, "so_xe_dang_gui": 1, "trang_thai_khu_vuc": "hoat_dong", "ly_do_ngung": None},
            {"ma_khu_vuc": "KHU_C", "ten_khu_vuc": "Khu C Vãng lai", "ma_loai_xe": "O_TO", "tong_so_cho": 2, "so_xe_dang_gui": 1, "trang_thai_khu_vuc": "hoat_dong", "ly_do_ngung": None},
        ]
        self.vi_tri_do: List[Dict[str, Any]] = [
            {"ma_vi_tri": "B1-01", "ma_khu_vuc": "HAM_B1", "ten_vi_tri": "Ô B1-01", "nhom_vi_tri": "vi_tri_dau_bai", "trang_thai_vi_tri": "dang_co_xe", "ly_do_ngung": None, "thoi_gian_cap_nhat": "2026-11-16T08:00:00"},
            {"ma_vi_tri": "B1-02", "ma_khu_vuc": "HAM_B1", "ten_vi_tri": "Ô B1-02", "nhom_vi_tri": "vi_tri_thuong", "trang_thai_vi_tri": "da_phan_cho", "ly_do_ngung": None, "thoi_gian_cap_nhat": "2026-11-16T08:00:00"},
            {"ma_vi_tri": "B1-03", "ma_khu_vuc": "HAM_B1", "ten_vi_tri": "Ô B1-03", "nhom_vi_tri": "vi_tri_thuong", "trang_thai_vi_tri": "trong", "ly_do_ngung": None, "thoi_gian_cap_nhat": "2026-11-16T08:00:00"},
            {"ma_vi_tri": "B1-04", "ma_khu_vuc": "HAM_B1", "ten_vi_tri": "Ô B1-04", "nhom_vi_tri": "vi_tri_thuong", "trang_thai_vi_tri": "ngung_su_dung", "ly_do_ngung": "Bảo trì đèn", "thoi_gian_cap_nhat": "2026-11-16T08:00:00"},
            {"ma_vi_tri": "C-01", "ma_khu_vuc": "KHU_C", "ten_vi_tri": "Ô C-01", "nhom_vi_tri": "vi_tri_vang_lai", "trang_thai_vi_tri": "dang_co_xe", "ly_do_ngung": None, "thoi_gian_cap_nhat": "2026-11-16T08:00:00"},
            {"ma_vi_tri": "C-02", "ma_khu_vuc": "KHU_C", "ten_vi_tri": "Ô C-02", "nhom_vi_tri": "vi_tri_vang_lai", "trang_thai_vi_tri": "trong", "ly_do_ngung": None, "thoi_gian_cap_nhat": "2026-11-16T08:00:00"},
        ]
        self.phan_bo_cho_do: List[Dict[str, Any]] = [
            {"ma_phan_bo": 1, "ma_can_ho": "A1-01", "ma_vi_tri": "B1-01", "loai_cho_cap": "cho_mac_dinh_xe_1", "trang_thai_su_dung": "dang_hieu_luc"},
            {"ma_phan_bo": 2, "ma_can_ho": "A1-01", "ma_vi_tri": "B1-02", "loai_cho_cap": "cho_mua_them", "trang_thai_su_dung": "dang_hieu_luc"},
        ]
        self.phuong_tien: List[Dict[str, Any]] = [
            {"ma_phuong_tien": 1, "ma_cu_dan": 1, "ma_loai_xe": "O_TO", "bien_so_xe": "51H-888.99", "ten_hang_dong_xe": "Toyota Camry", "mau_xe": "Đen"},
        ]
        self.quan_ly_phuong_tien_ra_vao: List[Dict[str, Any]] = [
            {"ma_quan_ly_xe": 1, "ma_phuong_tien": 1, "ma_vi_tri_co_dinh": "B1-01", "ma_khu_vuc_phan_cong": "HAM_B1", "thoi_gian_vao": "2026-11-16T08:15:00", "dang_trong_bai": True},
        ]
        self.bao_cao_luot_va_thu_phi: List[Dict[str, Any]] = [
            {"ma_bao_cao": 1, "ma_nhan_vien": 2, "ma_khu_vuc": "HAM_B1", "ngay_ghi_nhan": "2026-11-16", "ca_lam_viec": "ca_sang", "so_luot_oto_vang_lai": 2, "so_luot_xemay_vang_lai": 3, "so_luot_xe_cu_dan": 5, "tong_tien_thu_vang_lai": 65000.0},
        ]
        self.giao_dich_thanh_toan: List[Dict[str, Any]] = [
            {"ma_giao_dich": 1, "ma_hoa_don": 1, "so_tien_thanh_toan": 1200000.0, "trang_thai_giao_dich": "thanh_toan_thanh_cong", "thoi_gian_giao_dich": "2026-11-16T09:00:00"},
        ]
        self.thong_bao: List[Dict[str, Any]] = []
        self.nguoi_nhan_thong_bao: List[Dict[str, Any]] = []

    @contextmanager
    def transaction(self):
        yield self

    def fetch_one(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        q = " ".join(sql.split())
        if "SELECT ma_nhan_vien FROM nhan_vien WHERE ma_tai_khoan = %s" in q:
            return next((dict(n) for n in self.nhan_vien if n["ma_tai_khoan"] == params[0]), None)
        if "SELECT ho_ten FROM nhan_vien WHERE ma_nhan_vien = %s" in q:
            return next((dict(n) for n in self.nhan_vien if n["ma_nhan_vien"] == params[0]), None)
        if "FROM thong_bao WHERE ma_thong_bao = %s" in q:
            return next((dict(t) for t in self.thong_bao if t["ma_thong_bao"] == params[0]), None)
        if "FROM nguoi_nhan_thong_bao nn JOIN thong_bao tb" in q and "WHERE nn.ma_thong_bao = %s AND nn.ma_tai_khoan_nhan = %s" in q:
            nid, uid = params[0], params[1]
            nn = next((r for r in self.nguoi_nhan_thong_bao if r["ma_thong_bao"] == nid and r["ma_tai_khoan_nhan"] == uid), None)
            if not nn:
                return None
            tb = next((t for t in self.thong_bao if t["ma_thong_bao"] == nid), {})
            nv = next((n for n in self.nhan_vien if n["ma_nhan_vien"] == tb.get("ma_nguoi_gui")), {})
            return {**nn, **tb, "ten_nguoi_gui": nv.get("ho_ten", "Ban Quản Lý")}
        if "SELECT COUNT(*) AS so_luong FROM quan_ly_phuong_tien_ra_vao" in q:
            return {"so_luong": sum(1 for x in self.quan_ly_phuong_tien_ra_vao if x["dang_trong_bai"])}
        return None

    def fetch_all(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        q = " ".join(sql.split())
        if "FROM tai_khoan WHERE trang_thai = %s" in q:
            return [dict(t) for t in self.tai_khoan if t["trang_thai"] == params[0]]
        if "FROM tai_khoan WHERE vai_tro = %s AND trang_thai = %s" in q:
            return [dict(t) for t in self.tai_khoan if t["vai_tro"] == params[0] and t["trang_thai"] == params[1]]
        if "FROM tai_khoan WHERE ma_tai_khoan = %s AND trang_thai = %s" in q:
            return [dict(t) for t in self.tai_khoan if t["ma_tai_khoan"] == params[0] and t["trang_thai"] == params[1]]
        if "FROM nguoi_nhan_thong_bao WHERE ma_thong_bao = %s" in q:
            return [dict(r) for r in self.nguoi_nhan_thong_bao if r["ma_thong_bao"] == params[0]]
        if "FROM thong_bao tb" in q:
            out = []
            for tb in self.thong_bao:
                nv = next((n for n in self.nhan_vien if n["ma_nhan_vien"] == tb.get("ma_nguoi_gui")), {})
                out.append({**tb, "ten_nguoi_gui": nv.get("ho_ten", "Ban Quản Lý")})
            return list(reversed(out))
        if "FROM nguoi_nhan_thong_bao nn JOIN tai_khoan tk" in q:
            out = []
            for r in self.nguoi_nhan_thong_bao:
                tk = next((t for t in self.tai_khoan if t["ma_tai_khoan"] == r["ma_tai_khoan_nhan"]), {})
                cd = next((c for c in self.cu_dan if c["ma_tai_khoan"] == r["ma_tai_khoan_nhan"]), None)
                nv = next((n for n in self.nhan_vien if n["ma_tai_khoan"] == r["ma_tai_khoan_nhan"]), None)
                out.append(
                    {
                        **r,
                        "ten_dang_nhap": tk.get("ten_dang_nhap", ""),
                        "vai_tro": tk.get("vai_tro", ""),
                        "ho_ten_nhan": (cd or nv or tk).get("ho_ten") or tk.get("ten_dang_nhap", ""),
                    }
                )
            return out
        if "FROM nguoi_nhan_thong_bao nn JOIN thong_bao tb" in q and "WHERE nn.ma_tai_khoan_nhan = %s" in q:
            uid = params[0]
            out = []
            for r in self.nguoi_nhan_thong_bao:
                if r["ma_tai_khoan_nhan"] != uid:
                    continue
                tb = next((t for t in self.thong_bao if t["ma_thong_bao"] == r["ma_thong_bao"]), {})
                nv = next((n for n in self.nhan_vien if n["ma_nhan_vien"] == tb.get("ma_nguoi_gui")), {})
                out.append({**r, **tb, "ten_nguoi_gui": nv.get("ho_ten", "Ban Quản Lý")})
            out.sort(
                key=lambda x: (
                    0 if x.get("loai_thong_bao") == "khan_cap" or x.get("muc_do") == "khan_cap" else 1,
                    0 if not x.get("da_doc") else 1,
                )
            )
            return out
        if "FROM quan_ly_phuong_tien_ra_vao ql" in q:
            out = []
            for ql in self.quan_ly_phuong_tien_ra_vao:
                if not ql["dang_trong_bai"]:
                    continue
                pt = next((p for p in self.phuong_tien if p["ma_phuong_tien"] == ql["ma_phuong_tien"]), {})
                cd = next((c for c in self.cu_dan if c["ma_cu_dan"] == pt.get("ma_cu_dan")), {})
                out.append(
                    {
                        "ma_quan_ly_xe": ql["ma_quan_ly_xe"],
                        "ma_phuong_tien": ql["ma_phuong_tien"],
                        "ma_khu_vuc": ql["ma_khu_vuc_phan_cong"],
                        "ma_vi_tri": ql["ma_vi_tri_co_dinh"],
                        "thoi_gian_vao": ql["thoi_gian_vao"],
                        "bien_so_xe": pt.get("bien_so_xe"),
                        "ma_loai_xe": pt.get("ma_loai_xe"),
                        "ten_hang_dong_xe": pt.get("ten_hang_dong_xe"),
                        "mau_xe": pt.get("mau_xe"),
                        "ten_chu_xe": cd.get("ho_ten"),
                        "ma_can_ho": cd.get("ma_can_ho"),
                    }
                )
            return out
        if "FROM vi_tri_do v JOIN khu_vuc kv" in q:
            out = []
            for v in self.vi_tri_do:
                kv = next((k for k in self.khu_vuc if k["ma_khu_vuc"] == v["ma_khu_vuc"]), {})
                if "v.trang_thai_vi_tri IN" in q and v["trang_thai_vi_tri"] not in params:
                    continue
                if "v.ma_khu_vuc = %s" in q and v["ma_khu_vuc"] != params[-1]:
                    continue
                out.append({**v, "ten_khu_vuc": kv.get("ten_khu_vuc", ""), "ma_loai_xe": kv.get("ma_loai_xe", "O_TO")})
            return out
        if "FROM phan_bo_cho_do pb" in q:
            out = []
            for pb in self.phan_bo_cho_do:
                ql = next((x for x in self.quan_ly_phuong_tien_ra_vao if x["ma_vi_tri_co_dinh"] == pb["ma_vi_tri"]), None)
                pt = next((p for p in self.phuong_tien if ql and p["ma_phuong_tien"] == ql["ma_phuong_tien"]), {})
                cd = next((c for c in self.cu_dan if c["ma_can_ho"] == pb["ma_can_ho"]), {})
                out.append({**pb, "bien_so_xe": pt.get("bien_so_xe", ""), "ten_chu_xe": cd.get("ho_ten", "")})
            return out
        if "FROM khu_vuc" in q:
            return [dict(k) for k in self.khu_vuc]
        if "SELECT trang_thai_vi_tri, COUNT(*) AS so_luong FROM vi_tri_do" in q:
            counts: Dict[str, int] = {}
            for v in self.vi_tri_do:
                counts[v["trang_thai_vi_tri"]] = counts.get(v["trang_thai_vi_tri"], 0) + 1
            return [{"trang_thai_vi_tri": k, "so_luong": val} for k, val in counts.items()]
        if "FROM bao_cao_luot_va_thu_phi" in q:
            rows = self.bao_cao_luot_va_thu_phi
            if "SUM(tong_tien_thu_vang_lai)" in q:
                return [{"ngay_ghi_nhan": r["ngay_ghi_nhan"], "tien_vang_lai": r["tong_tien_thu_vang_lai"]} for r in rows]
            if "SUM(so_luot_oto_vang_lai)" in q:
                return [
                    {
                        "ngay_ghi_nhan": r["ngay_ghi_nhan"],
                        "vl_oto": r["so_luot_oto_vang_lai"],
                        "vl_xemay": r["so_luot_xemay_vang_lai"],
                        "cd_tong": r["so_luot_xe_cu_dan"],
                    }
                    for r in rows
                ]
            return [
                {
                    "ngay_ghi_nhan": r["ngay_ghi_nhan"],
                    "tong_luot": r["so_luot_oto_vang_lai"] + r["so_luot_xemay_vang_lai"] + r["so_luot_xe_cu_dan"],
                }
                for r in rows
            ]
        if "FROM giao_dich_thanh_toan" in q:
            return [
                {"ngay_gd": g["thoi_gian_giao_dich"][:10], "tong_tien": g["so_tien_thanh_toan"]}
                for g in self.giao_dich_thanh_toan
                if g["trang_thai_giao_dich"] == "thanh_toan_thanh_cong"
            ]
        return []

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> int:
        q = " ".join(sql.split())
        if "UPDATE nguoi_nhan_thong_bao SET trang_thai_tiep_nhan = %s" in q:
            new_st, uid, old_st = params[0], params[1], params[2]
            cnt = 0
            for r in self.nguoi_nhan_thong_bao:
                if r["ma_tai_khoan_nhan"] == uid and r["trang_thai_tiep_nhan"] == old_st:
                    r["trang_thai_tiep_nhan"] = new_st
                    r["thoi_gian_tiep_nhan"] = r["thoi_gian_tiep_nhan"] or datetime.now().isoformat(timespec="seconds")
                    cnt += 1
            return cnt
        if "UPDATE nguoi_nhan_thong_bao SET da_doc = TRUE" in q:
            new_st, uid = params[0], params[1]
            cnt = 0
            for r in self.nguoi_nhan_thong_bao:
                if r["ma_tai_khoan_nhan"] == uid and not r["da_doc"]:
                    r["da_doc"] = True
                    r["trang_thai_tiep_nhan"] = new_st
                    r["thoi_gian_tiep_nhan"] = r["thoi_gian_tiep_nhan"] or datetime.now().isoformat(timespec="seconds")
                    cnt += 1
            return cnt
        return 0

    def execute_returning(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        q = " ".join(sql.split())
        if "INSERT INTO thong_bao" in q:
            nid = len(self.thong_bao) + 1
            row = {
                "ma_thong_bao": nid,
                "ma_nguoi_gui": params[0],
                "tieu_de": params[1],
                "noi_dung": params[2],
                "loai_thong_bao": params[3],
                "muc_do": params[4],
                "nhom_nguoi_nhan": params[5],
                "thoi_gian_gui": datetime.now().isoformat(timespec="seconds"),
            }
            self.thong_bao.append(row)
            return dict(row)
        if "INSERT INTO nguoi_nhan_thong_bao" in q:
            rid = len(self.nguoi_nhan_thong_bao) + 1
            row = {
                "ma_tiep_nhan": rid,
                "ma_thong_bao": params[0],
                "ma_tai_khoan_nhan": params[1],
                "trang_thai_gui": params[2],
                "so_lan_gui_lai": 0,
                "trang_thai_tiep_nhan": params[3],
                "da_doc": False,
                "thoi_gian_tiep_nhan": None,
            }
            self.nguoi_nhan_thong_bao.append(row)
            return dict(row)
        if "UPDATE nguoi_nhan_thong_bao SET so_lan_gui_lai = so_lan_gui_lai + 1" in q:
            new_status, rec_id = params[0], params[1]
            for r in self.nguoi_nhan_thong_bao:
                if r["ma_tiep_nhan"] == rec_id:
                    r["so_lan_gui_lai"] += 1
                    r["trang_thai_gui"] = new_status
                    return dict(r)
            return None
        if "UPDATE nguoi_nhan_thong_bao SET da_doc = TRUE" in q:
            new_st, nid, uid = params[0], params[1], params[2]
            for r in self.nguoi_nhan_thong_bao:
                if r["ma_thong_bao"] == nid and r["ma_tai_khoan_nhan"] == uid:
                    r["da_doc"] = True
                    r["trang_thai_tiep_nhan"] = new_st
                    r["thoi_gian_tiep_nhan"] = r["thoi_gian_tiep_nhan"] or datetime.now().isoformat(timespec="seconds")
                    return dict(r)
            return None
        if "UPDATE nguoi_nhan_thong_bao SET trang_thai_tiep_nhan = %s" in q:
            new_st, nid, uid = params[0], params[1], params[2]
            for r in self.nguoi_nhan_thong_bao:
                if r["ma_thong_bao"] == nid and r["ma_tai_khoan_nhan"] == uid:
                    r["trang_thai_tiep_nhan"] = new_st
                    r["thoi_gian_tiep_nhan"] = r["thoi_gian_tiep_nhan"] or datetime.now().isoformat(timespec="seconds")
                    return dict(r)
            return None
        return None


class TestMinhNotifyStats3D(unittest.TestCase):
    """Kiểm thử toàn bộ phần của Minh: Workflow 01, Sa bàn 3D, Giám sát, Thống kê & Lịch sử."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.file_logger = FileLogger(self.tmp_path / "logs")
        self.state_store = StateStore(self.tmp_path / "state" / "active_vang_lai.json")
        self.session_manager = SessionManager(file_logger=self.file_logger)
        self.router = Router(file_logger=self.file_logger)
        self.db = _MinhFakeDB()

        register_notify_routes(self.router, self.session_manager, self.file_logger, db_provider=self.db)
        register_monitor_admin_routes(
            self.router,
            self.session_manager,
            self.file_logger,
            self.state_store,
            db_provider=self.db,
        )

    def tearDown(self) -> None:
        self.session_manager.stop_reaper()
        self.tmp_dir.cleanup()

    def _make_session(self, ma_tai_khoan: int, username: str, vai_tro: str, ma_nv: Optional[int] = None) -> ClientSession:
        return ClientSession(
            session_id=f"s-{username}",
            sock=_CapturingSocket(),  # type: ignore[arg-type]
            ip_address="127.0.0.1",
            port=50000 + ma_tai_khoan,
            ma_tai_khoan=ma_tai_khoan,
            ten_dang_nhap=username,
            vai_tro=vai_tro,
            ho_ten=username,
            ma_nhan_vien=ma_nv,
        )

    def test_01_workflow_01_draft_publish_push_retry_ack_and_read(self) -> None:
        """Test trọn vẹn Workflow 01 (1.10.1–1.10.3, 2.7.1–2.7.3, 3.6.1–3.6.3)."""
        staff_sock = _CapturingSocket()
        staff_live = self.session_manager.register_connection(staff_sock, ("127.0.0.1", 51003))  # type: ignore[arg-type]
        self.session_manager.bind_authenticated_user(
            staff_live.session_id,
            {"ma_tai_khoan": 3, "ten_dang_nhap": "nhanvien01", "vai_tro": Roles.NHAN_VIEN.value, "ho_ten": "Trần Bảo Vệ", "ma_nhan_vien": 2},
        )

        mgr = self._make_session(2, "quanly01", Roles.QUAN_LY.value, ma_nv=1)
        res_user = self._make_session(4, "cudan_a101", Roles.CU_DAN.value)
        adm = self._make_session(1, "admin01", Roles.ADMIN.value)

        # 1.10.1 Lưu nháp
        draft_res = self.router.dispatch(
            mgr,
            Actions.NOTIFY_SAVE_DRAFT,
            {
                "tieu_de": "KHẨN CẤP: Cháy hầm B1",
                "noi_dung": "Đề nghị di chuyển khỏi tầng hầm ngay lập tức.",
                "loai_thong_bao": NotifyTypes.KHAN_CAP.value,
                "muc_do": NotifyLevels.KHAN_CAP.value,
                "nhom_nguoi_nhan": NotifyRecipientGroups.TAT_CA.value,
            },
            "1",
        )
        self.assertTrue(draft_res["ok"])
        draft_id = draft_res["data"]["draft_id"]
        self.assertTrue(draft_id.startswith("DRAFT-"))

        # 1.10.2 Phát thông báo từ nháp & giả lập lỗi gửi tới tài khoản 4 (cudan_a101)
        pub_res = self.router.dispatch(
            mgr,
            Actions.NOTIFY_PUBLISH,
            {"draft_id": draft_id, "simulate_failed_account_ids": [4]},
            "2",
        )
        self.assertTrue(pub_res["ok"])
        pub_data = pub_res["data"]
        nid = pub_data["thong_bao"]["ma_thong_bao"]
        self.assertEqual(pub_data["push_event"], PushEvents.EMERGENCY_ALERT)
        self.assertEqual(pub_data["so_gui_that_bai"], 1)
        self.assertGreaterEqual(pub_data["so_gui_thanh_cong"], 1)

        # Nhân viên đang online phải nhận ngay frame PUSH EMERGENCY_ALERT
        pushed = staff_sock.decoded_messages()
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0]["msg_type"], MsgType.PUSH)
        self.assertEqual(pushed[0]["event"], PushEvents.EMERGENCY_ALERT)
        self.assertEqual(pushed[0]["data"]["ma_thong_bao"], nid)

        # Cư dân 4 có trang_thai_gui = gui_that_bai
        rec_c4 = next(r for r in self.db.nguoi_nhan_thong_bao if r["ma_tai_khoan_nhan"] == 4)
        self.assertEqual(rec_c4["trang_thai_gui"], NotifySendStatus.GUI_THAT_BAI.value)
        self.assertEqual(rec_c4["so_lan_gui_lai"], 0)

        # 1.10.2 Retry -> tăng so_lan_gui_lai = 1 và chuyển gui_thanh_cong
        retry_res = self.router.dispatch(mgr, Actions.NOTIFY_RETRY_FAILED, {"ma_thong_bao": nid}, "3")
        self.assertTrue(retry_res["ok"])
        self.assertEqual(retry_res["data"]["retried_count"], 1)
        self.assertEqual(rec_c4["so_lan_gui_lai"], 1)
        self.assertEqual(rec_c4["trang_thai_gui"], NotifySendStatus.GUI_THANH_CONG.value)

        # Nhân viên xác nhận ACK khẩn cấp
        ack_res = self.router.dispatch(staff_live, Actions.NOTIFY_ACK_EMERGENCY, {"ma_thong_bao": nid}, "4")
        self.assertTrue(ack_res["ok"])
        self.assertEqual(ack_res["data"]["recipient"]["trang_thai_tiep_nhan"], NotifyAckStatus.DA_TIEP_NHAN.value)
        self.assertIsNotNone(ack_res["data"]["recipient"]["thoi_gian_tiep_nhan"])

        # Cư dân mở hộp thư (3.6.1) -> tự động cập nhật da_tiep_nhan, sau đó xem chi tiết (3.6.2) và mark_read (3.6.3)
        inbox_res = self.router.dispatch(res_user, Actions.NOTIFY_MY_INBOX, {}, "5")
        self.assertTrue(inbox_res["ok"])
        self.assertEqual(inbox_res["data"]["unread_count"], 1)
        self.assertEqual(rec_c4["trang_thai_tiep_nhan"], NotifyAckStatus.DA_TIEP_NHAN.value)

        det_res = self.router.dispatch(res_user, Actions.NOTIFY_GET_DETAIL, {"ma_thong_bao": nid}, "6")
        self.assertTrue(det_res["ok"])
        self.assertEqual(det_res["data"]["notification"]["tieu_de"], "KHẨN CẤP: Cháy hầm B1")

        read_res = self.router.dispatch(res_user, Actions.NOTIFY_MARK_READ, {"ma_thong_bao": nid}, "7")
        self.assertTrue(read_res["ok"])
        self.assertTrue(rec_c4["da_doc"])

        # 1.10.3 Quản lý xem thống kê đã gửi
        sent_res = self.router.dispatch(mgr, Actions.NOTIFY_LIST_SENT, {}, "8")
        self.assertTrue(sent_res["ok"])
        self.assertEqual(sent_res["data"]["total"], 1)
        self.assertGreaterEqual(sent_res["data"]["items"][0]["so_da_doc"], 1)

        # Admin chỉ xem: bị chặn các route ghi
        adm_pub = self.router.dispatch(adm, Actions.NOTIFY_PUBLISH, {"tieu_de": "x", "noi_dung": "y"}, "9")
        self.assertFalse(adm_pub["ok"])
        self.assertEqual(adm_pub["error_code"], "ADMIN_READ_ONLY")

    def test_02_monitor_active_available_occupied_and_overview_map(self) -> None:
        """Test nhóm 1.6 & 2.6 giám sát bãi xe kết hợp DB + StateStore vãng lai."""
        self.state_store.add_visitor_session(
            bien_so_xe="51F-999.01",
            ma_loai_xe="O_TO",
            ma_khu_vuc="KHU_C",
            ma_vi_tri="C-01",
            ma_luot_gui="VL-TEST-01",
        )
        staff = self._make_session(3, "nhanvien01", Roles.NHAN_VIEN.value, ma_nv=2)

        act = self.router.dispatch(staff, Actions.MONITOR_ACTIVE_PARKINGS, {}, "1")
        self.assertTrue(act["ok"])
        self.assertEqual(act["data"]["total"], 2)
        self.assertEqual(act["data"]["resident_count"], 1)
        self.assertEqual(act["data"]["visitor_count"], 1)

        avail = self.router.dispatch(staff, Actions.MONITOR_AVAILABLE_SLOTS, {}, "2")
        self.assertTrue(avail["ok"])
        self.assertEqual(avail["data"]["total"], 2)  # B1-03 và C-02

        occ = self.router.dispatch(staff, Actions.MONITOR_OCCUPIED_SLOTS, {}, "3")
        self.assertTrue(occ["ok"])
        self.assertEqual(occ["data"]["total"], 2)  # B1-01 và C-01

        ov = self.router.dispatch(staff, Actions.MONITOR_OVERVIEW_MAP, {}, "4")
        self.assertTrue(ov["ok"])
        self.assertEqual(len(ov["data"]["slots"]), 6)
        b1_01 = next(s for s in ov["data"]["slots"] if s["ma_vi_tri"] == "B1-01")
        c_01 = next(s for s in ov["data"]["slots"] if s["ma_vi_tri"] == "C-01")
        self.assertEqual(b1_01["bien_so_xe"], "51H-888.99")
        self.assertEqual(c_01["bien_so_xe"], "51F-999.01")

    def test_03_stats_and_history_with_images(self) -> None:
        """Test 1.8 thống kê lượt xe, cư dân vs vãng lai, doanh thu & 1.9 lịch sử vào/ra."""
        dummy_img_b64 = base64.b64encode(b"FAKE_JPEG_DATA").decode("ascii")
        self.file_logger.log_parking_history(
            ma_luot_gui="VL-20261116-01",
            loai_khach="vang_lai",
            bien_so_xe="51F-123.45",
            ma_loai_xe="O_TO",
            ma_khu_vuc="KHU_C",
            ma_vi_tri="C-01",
            thoi_gian_vao="2026-11-16T08:00:00",
            anh_vao=dummy_img_b64,
            ma_nhan_vien_vao=2,
            trang_thai="dang_gui",
            timestamp="2026-11-16T08:00:00",
        )
        self.file_logger.log_parking_history(
            ma_luot_gui="VL-20261116-01",
            loai_khach="vang_lai",
            bien_so_xe="51F-123.45",
            ma_loai_xe="O_TO",
            ma_khu_vuc="KHU_C",
            ma_vi_tri="C-01",
            thoi_gian_vao="2026-11-16T08:00:00",
            thoi_gian_ra="2026-11-16T10:30:00",
            anh_ra=dummy_img_b64,
            tien_thu=75000.0,
            ma_nhan_vien_ra=2,
            trang_thai="da_ra_bai",
            timestamp="2026-11-16T10:30:00",
        )

        mgr = self._make_session(2, "quanly01", Roles.QUAN_LY.value, ma_nv=1)

        t_res = self.router.dispatch(mgr, Actions.STATS_VEHICLE_TURNS, {}, "1")
        self.assertTrue(t_res["ok"])
        self.assertGreaterEqual(t_res["data"]["tong_luot_vao"], 1)
        self.assertGreaterEqual(t_res["data"]["tong_luot_ra"], 1)

        cmp_res = self.router.dispatch(mgr, Actions.STATS_RESIDENT_VS_VISITOR, {}, "2")
        self.assertTrue(cmp_res["ok"])
        self.assertEqual(cmp_res["data"]["tong_cu_dan"], 5)
        self.assertEqual(cmp_res["data"]["tong_vang_lai"], 5)

        rev_res = self.router.dispatch(mgr, Actions.STATS_REVENUE, {"group_by": "month"}, "3")
        self.assertTrue(rev_res["ok"])
        self.assertEqual(rev_res["data"]["doanh_thu_cu_dan"], 1200000.0)
        self.assertEqual(rev_res["data"]["doanh_thu_vang_lai"], 75000.0)
        self.assertEqual(rev_res["data"]["tong_doanh_thu"], 1275000.0)

        ent_res = self.router.dispatch(mgr, Actions.HISTORY_ENTRIES, {"bien_so_xe": "51F"}, "4")
        self.assertTrue(ent_res["ok"])
        self.assertEqual(ent_res["data"]["total"], 1)

        ex_res = self.router.dispatch(mgr, Actions.HISTORY_EXITS, {}, "5")
        self.assertTrue(ex_res["ok"])
        self.assertEqual(ex_res["data"]["total"], 1)
        self.assertEqual(ex_res["data"]["items"][0]["tien_thu"], 75000.0)

        det_res = self.router.dispatch(mgr, Actions.HISTORY_SESSION_DETAIL, {"ma_luot_gui": "VL-20261116-01"}, "6")
        self.assertTrue(det_res["ok"])
        sess = det_res["data"]["session"]
        self.assertEqual(sess["anh_vao"], dummy_img_b64)
        self.assertEqual(sess["anh_ra"], dummy_img_b64)
        self.assertEqual(sess["ten_nhan_vien_vao"], "Trần Bảo Vệ")

    def test_04_parking_3d_scene_widget_12_slots_and_offscreen_render(self) -> None:
        """Kiểm tra khởi tạo, phép chiếu 3D, render offscreen và reset góc nhìn với 12 ô đỗ."""
        _app = QApplication.instance() or QApplication([])
        widget = Parking3DSceneWidget(columns=4)
        widget.resize(900, 560)

        statuses = [
            SlotStatus.TRONG.value,
            SlotStatus.DA_PHAN_CHO.value,
            SlotStatus.DANG_CO_XE.value,
            SlotStatus.NGUNG_SU_DUNG.value,
        ]
        slots = []
        for i in range(12):
            st = statuses[i % 4]
            slots.append(
                {
                    "ma_vi_tri": f"B1-{i+1:02d}",
                    "ma_khu_vuc": "HAM_B1",
                    "trang_thai_vi_tri": st,
                    "ma_loai_xe": "XE_MAY" if i % 2 else "O_TO",
                    "bien_so_xe": f"51H-100.{i+10}" if st == SlotStatus.DANG_CO_XE.value else "",
                }
            )
        widget.set_slots(slots)

        sx, sy, depth = widget.project_point(1.0, 2.0, 0.5)
        self.assertTrue(all(isinstance(val, float) for val in (sx, sy, depth)))

        img = QImage(900, 560, QImage.Format.Format_ARGB32)
        widget.render(img)
        self.assertEqual(len(widget._hit_polygons), 12)

        # Kiểm tra click vào một ô đã chiếu
        clicked: List[Dict[str, Any]] = []
        widget.slot_clicked.connect(clicked.append)
        first_poly, _ = widget._hit_polygons[-1]
        center = first_poly.boundingRect().center()
        press = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            center,
            center,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            center,
            center,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.mousePressEvent(press)
        widget.mouseReleaseEvent(release)
        self.assertEqual(len(clicked), 1)

        widget._yaw_deg = 120.0
        widget._zoom = 1.8
        widget.reset_view()
        self.assertEqual(widget._yaw_deg, Parking3DSceneWidget.DEFAULT_YAW_DEG)
        self.assertEqual(widget._zoom, Parking3DSceneWidget.DEFAULT_ZOOM)
        widget.close()

    def test_05_ui_offscreen_main_window_and_all_minh_views(self) -> None:
        """Kiểm tra render offscreen MainWindow 4 vai trò và toàn bộ màn hình của Minh."""
        _app = QApplication.instance() or QApplication([])

        expected_counts = {
            Roles.QUAN_LY.value: 10,
            Roles.NHAN_VIEN.value: 7,
            Roles.CU_DAN.value: 6,
            Roles.ADMIN.value: 8,
        }
        for role, count in expected_counts.items():
            win = MainWindow(session_info={"vai_tro": role, "ho_ten": f"User {role}"})
            self.assertEqual(len(win.page_keys()), count)
            win.toggle_sidebar()
            self.assertEqual(win.sidebar.width(), 72)
            win.toggle_sidebar()
            self.assertEqual(win.sidebar.width(), 256)
            win.set_unread_count(3)
            self.assertIn("3", win.btn_bell.text())
            win.close()

        # Kiểm tra ManagerWindow & AdminWindow đã ghép màn hình Minh
        mgr_win = ManagerWindow()
        self.assertIsInstance(mgr_win.monitor_view, ManagerMonitorView)
        self.assertIsInstance(mgr_win.stats_view, ManagerStatsView)
        self.assertIsInstance(mgr_win.history_view, ManagerHistoryView)
        self.assertIsInstance(mgr_win.notify_view, ManagerNotifyView)
        mgr_win.close()

        adm_win = AdminWindow()
        self.assertTrue(adm_win.ro_stats_view.read_only)
        self.assertTrue(adm_win.ro_monitor_view.read_only)
        self.assertTrue(adm_win.ro_history_view.read_only)
        adm_win.close()

        # Kiểm tra từng view con với dữ liệu mẫu
        s_mon = StaffMonitorView()
        s_mon.apply_overview_data({"updated_at": "now", "zones": self.db.khu_vuc, "slots": self.db.vi_tri_do, "summary": {}})
        self.assertEqual(s_mon.map_stack.count(), 2)
        s_mon.close()

        r_not = ResidentNotifyView()
        r_not.apply_inbox_data({"unread_count": 1, "items": [{"ma_thong_bao": 1, "tieu_de": "Test", "noi_dung": "ND", "muc_do": "khan_cap", "loai_thong_bao": "khan_cap", "da_doc": False, "trang_thai_tiep_nhan": "da_tiep_nhan", "thoi_gian_gui": "2026-11-16"}]})
        self.assertEqual(r_not.tbl_inbox.rowCount(), 1)
        r_not.close()

        s_not = StaffNotifyInboxView()
        s_not.apply_inbox_data({"unread_count": 0, "items": []})
        self.assertEqual(s_not.tbl_inbox.rowCount(), 0)
        s_not.close()

        h_dlg = HistoryDetailDialog({"ma_luot_gui": "VL-1", "bien_so_xe": "51F-123.45", "trang_thai": "da_ra_bai"})
        self.assertIn("VL-1", h_dlg.windowTitle())
        h_dlg.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
