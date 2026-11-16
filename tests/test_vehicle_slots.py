"""Bộ kiểm thử tự động toàn phần cho Thành viên 4 (Long — feat/vehicle-slots).

Kiểm thử đầy đủ 23 chức năng lá:
- Workflow 02 & 05: Cư dân đăng ký xe + ảnh cà-vẹt Base64 -> Quản lý duyệt & gán ô đỗ ACID
- Chỉnh sửa thông tin (3.2.4), Ngừng đăng ký (3.2.5) kèm chặn an toàn xe đang trong bãi
- Hạn mức ô tô, Mua thêm chỗ (3.3.1-3.3.5), Duyệt/Từ chối (1.4.15-1.4.18) & Thu hồi (1.4.19)
- Tra cứu & Định vị xe 2D/3D + 10 lượt vào/ra gần nhất (3.4.1-3.4.4)
- RBAC Admin Read-Only & Offscreen UI trên MainWindow / ManagerWindow / AdminWindow
"""

import base64
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from client.ui.admin.admin_window import AdminWindow
from client.ui.main_window import MainWindow
from client.ui.manager.manager_window import ManagerWindow
from client.ui.manager.slot_picker_dialog import SlotPickerDialog, ZoomableCavetDialog
from client.ui.manager.vehicle_approval_view import VehicleApprovalView
from client.ui.resident.extra_slot_view import ExtraSlotView
from client.ui.resident.vehicle_lookup_view import VehicleLookupView
from client.ui.resident.vehicle_view import ResidentVehicleView, VehicleRequestDialog
from server.core.file_logger import FileLogger
from server.core.router import Router
from server.core.session_manager import ClientSession
from server.handlers.vehicle_handler import register_vehicle_routes
from shared.constants import (
    RequestStatus,
    Roles,
    SlotAllocationStatus,
    SlotAllocationTypes,
    SlotStatus,
    VehicleRequestTypes,
    VehicleStatus,
    VehicleTypes,
)
from shared.protocol import Actions


def _make_sample_png_base64() -> str:
    img = QImage(160, 100, QImage.Format.Format_RGB32)
    img.fill(0xFF2563EB)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return base64.b64encode(bytes(buf.data())).decode("ascii")


class _LongFakeTx:
    def __init__(self, db: "_LongFakeDB") -> None:
        self.db = db

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        return self.db.fetch_one(sql, params)

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return self.db.fetch_all(sql, params)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self.db.execute(sql, params)

    def execute_returning(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        return self.db.execute_returning(sql, params)


class _LongFakeDB:
    """In-memory DB mô phỏng chính xác schema PostgreSQL cho toàn bộ nghiệp vụ của Long."""

    def __init__(self) -> None:
        self.can_ho: List[Dict[str, Any]] = [
            {
                "ma_can_ho": "A1-01",
                "toa_nha": "A",
                "so_tang": 1,
                "so_cho_oto_mac_dinh": 1,
                "so_cho_oto_mua_them": 0,
                "trang_thai_can_ho": "dang_su_dung",
            },
            {
                "ma_can_ho": "B2-02",
                "toa_nha": "B",
                "so_tang": 2,
                "so_cho_oto_mac_dinh": 1,
                "so_cho_oto_mua_them": 0,
                "trang_thai_can_ho": "dang_su_dung",
            },
        ]
        self.cu_dan: List[Dict[str, Any]] = [
            {
                "ma_cu_dan": 1,
                "ma_tai_khoan": 4,
                "ma_can_ho": "A1-01",
                "ho_ten": "Lê Minh Cư Dân",
                "so_dien_thoai": "0909200101",
            },
            {
                "ma_cu_dan": 2,
                "ma_tai_khoan": 5,
                "ma_can_ho": "B2-02",
                "ho_ten": "Phạm Thị Hồng",
                "so_dien_thoai": "0909200202",
            },
        ]
        self.nhan_vien: List[Dict[str, Any]] = [
            {"ma_nhan_vien": 1, "ma_tai_khoan": 2, "ho_ten": "Nguyễn Văn Quản Lý"},
        ]
        self.loai_phuong_tien: List[Dict[str, Any]] = [
            {"ma_loai_xe": "O_TO", "ten_loai_xe": "Ô tô", "kieu_phan_cho": "gan_vi_tri_co_dinh"},
            {"ma_loai_xe": "XE_MAY", "ten_loai_xe": "Xe máy", "kieu_phan_cho": "phan_theo_khu_vuc"},
        ]
        self.khu_vuc: List[Dict[str, Any]] = [
            {
                "ma_khu_vuc": "KHU_A",
                "ten_khu_vuc": "Khu A - Xe máy chung cư",
                "ma_loai_xe": "XE_MAY",
                "tong_so_cho": 120,
                "so_xe_dang_gui": 5,
                "trang_thai_khu_vuc": "hoat_dong",
            },
            {
                "ma_khu_vuc": "HAM_B1",
                "ten_khu_vuc": "Hầm B1 - Ô tô Cư dân",
                "ma_loai_xe": "O_TO",
                "tong_so_cho": 4,
                "so_xe_dang_gui": 0,
                "trang_thai_khu_vuc": "hoat_dong",
            },
            {
                "ma_khu_vuc": "KHU_C",
                "ten_khu_vuc": "Khu C - Ô tô Vãng lai",
                "ma_loai_xe": "O_TO",
                "tong_so_cho": 2,
                "so_xe_dang_gui": 0,
                "trang_thai_khu_vuc": "hoat_dong",
            },
        ]
        self.vi_tri_do: List[Dict[str, Any]] = [
            {"ma_vi_tri": "B1-01", "ma_khu_vuc": "HAM_B1", "ten_vi_tri": "Ô B1-01", "nhom_vi_tri": "vi_tri_dau_bai", "trang_thai_vi_tri": "trong", "ly_do_ngung": None},
            {"ma_vi_tri": "B1-02", "ma_khu_vuc": "HAM_B1", "ten_vi_tri": "Ô B1-02", "nhom_vi_tri": "vi_tri_thuong", "trang_thai_vi_tri": "trong", "ly_do_ngung": None},
            {"ma_vi_tri": "B1-03", "ma_khu_vuc": "HAM_B1", "ten_vi_tri": "Ô B1-03", "nhom_vi_tri": "vi_tri_thuong", "trang_thai_vi_tri": "trong", "ly_do_ngung": None},
            {"ma_vi_tri": "C-01", "ma_khu_vuc": "KHU_C", "ten_vi_tri": "Ô C-01", "nhom_vi_tri": "vi_tri_vang_lai", "trang_thai_vi_tri": "trong", "ly_do_ngung": None},
        ]
        self.bang_gia: List[Dict[str, Any]] = [
            {"ma_bang_gia": 1, "nhom_ap_dung": "cho_oto_mac_dinh", "muc_phi": 1200000.0, "trang_thai_gia": "dang_ap_dung"},
            {"ma_bang_gia": 2, "nhom_ap_dung": "cho_oto_mua_them", "muc_phi": 1800000.0, "trang_thai_gia": "dang_ap_dung"},
        ]
        self.phuong_tien: List[Dict[str, Any]] = []
        self.yeu_cau_dang_ky_xe: List[Dict[str, Any]] = []
        self.yeu_cau_mua_them_cho: List[Dict[str, Any]] = []
        self.phan_bo_cho_do: List[Dict[str, Any]] = []
        self.quan_ly_phuong_tien_ra_vao: List[Dict[str, Any]] = []
        self._seq = {"pt": 0, "ycx": 0, "ycc": 0, "pb": 0, "ql": 0}

    def _next_id(self, key: str) -> int:
        self._seq[key] += 1
        return self._seq[key]

    @contextmanager
    def transaction(self):
        yield _LongFakeTx(self)

    def _zone_map(self) -> Dict[str, Dict[str, Any]]:
        return {z["ma_khu_vuc"]: z for z in self.khu_vuc}

    def _slot_map(self) -> Dict[str, Dict[str, Any]]:
        return {s["ma_vi_tri"]: s for s in self.vi_tri_do}

    def _apt_map(self) -> Dict[str, Dict[str, Any]]:
        return {a["ma_can_ho"]: a for a in self.can_ho}

    def _res_map(self) -> Dict[int, Dict[str, Any]]:
        return {r["ma_cu_dan"]: r for r in self.cu_dan}

    def _veh_map(self) -> Dict[int, Dict[str, Any]]:
        return {v["ma_phuong_tien"]: v for v in self.phuong_tien}

    def _staff_map(self) -> Dict[int, Dict[str, Any]]:
        return {s["ma_nhan_vien"]: s for s in self.nhan_vien}

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        norm = " ".join(sql.split())
        if "FROM cu_dan WHERE ma_cu_dan = %s" in norm:
            for r in self.cu_dan:
                if r["ma_cu_dan"] == params[0]:
                    return dict(r)
            return None
        if "FROM cu_dan WHERE ma_tai_khoan = %s" in norm:
            for r in self.cu_dan:
                if r["ma_tai_khoan"] == params[0]:
                    return dict(r)
            return None
        if "FROM can_ho" in norm and "WHERE ma_can_ho = %s" in norm:
            for a in self.can_ho:
                if a["ma_can_ho"] == params[0]:
                    return dict(a)
            return None
        if "FROM nhan_vien WHERE ma_tai_khoan = %s" in norm:
            for nv in self.nhan_vien:
                if nv["ma_tai_khoan"] == params[0]:
                    return dict(nv)
            return None
        if "COUNT(*) FILTER" in norm and "FROM phuong_tien pt" in norm:
            oto_type, oto_st, moto_type, moto_st, apt = params
            res_ids = {r["ma_cu_dan"] for r in self.cu_dan if r["ma_can_ho"] == apt}
            oto_c = sum(1 for v in self.phuong_tien if v["ma_cu_dan"] in res_ids and v["ma_loai_xe"] == oto_type and v["trang_thai_xe"] == oto_st)
            moto_c = sum(1 for v in self.phuong_tien if v["ma_cu_dan"] in res_ids and v["ma_loai_xe"] == moto_type and v["trang_thai_xe"] == moto_st)
            return {"so_oto_hoat_dong": oto_c, "so_xe_may_hoat_dong": moto_c}
        if "COUNT(*) AS so_oto_cho_duyet" in norm:
            apt, req_type, req_st, vtype = params
            res_ids = {r["ma_cu_dan"] for r in self.cu_dan if r["ma_can_ho"] == apt}
            vmap = self._veh_map()
            cnt = sum(
                1 for yc in self.yeu_cau_dang_ky_xe
                if yc["ma_cu_dan"] in res_ids
                and yc["loai_yeu_cau"] == req_type
                and yc["trang_thai_duyet"] == req_st
                and vmap[yc["ma_phuong_tien"]]["ma_loai_xe"] == vtype
            )
            return {"so_oto_cho_duyet": cnt}
        if "FROM phuong_tien WHERE bien_so_xe = %s" in norm and "ma_phuong_tien <> %s" not in norm:
            for v in self.phuong_tien:
                if v["bien_so_xe"] == params[0]:
                    return dict(v)
            return None
        if "FROM phuong_tien WHERE bien_so_xe = %s AND ma_phuong_tien <> %s" in norm:
            for v in self.phuong_tien:
                if v["bien_so_xe"] == params[0] and v["ma_phuong_tien"] != params[1]:
                    return dict(v)
            return None
        if "FROM phuong_tien WHERE bien_so_xe = %s AND trang_thai_xe = %s AND ma_phuong_tien <> %s" in norm:
            for v in self.phuong_tien:
                if v["bien_so_xe"] == params[0] and v["trang_thai_xe"] == params[1] and v["ma_phuong_tien"] != params[2]:
                    return dict(v)
            return None
        if "FROM phuong_tien WHERE ma_phuong_tien = %s" in norm:
            for v in self.phuong_tien:
                if v["ma_phuong_tien"] == params[0]:
                    return dict(v)
            return None
        if "FROM phuong_tien WHERE duong_dan_cavet = %s" in norm:
            for v in self.phuong_tien:
                if v["duong_dan_cavet"] == params[0]:
                    return dict(v)
            return None
        if "FROM phuong_tien pt" in norm and "WHERE pt.ma_phuong_tien = %s" in norm:
            for v in self.phuong_tien:
                if v["ma_phuong_tien"] == params[0]:
                    row = dict(v)
                    ql = next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_phuong_tien"] == v["ma_phuong_tien"]), None)
                    row["dang_trong_bai"] = ql["dang_trong_bai"] if ql else False
                    row["ma_vi_tri_co_dinh"] = ql["ma_vi_tri_co_dinh"] if ql else None
                    row["ma_khu_vuc_phan_cong"] = ql["ma_khu_vuc_phan_cong"] if ql else None
                    return row
            return None
        if "SELECT ma_yeu_cau_xe FROM yeu_cau_dang_ky_xe" in norm:
            for yc in self.yeu_cau_dang_ky_xe:
                if yc["ma_phuong_tien"] == params[0] and yc["trang_thai_duyet"] == params[1]:
                    return {"ma_yeu_cau_xe": yc["ma_yeu_cau_xe"]}
            return None
        if "FROM yeu_cau_dang_ky_xe yc" in norm and "WHERE yc.ma_yeu_cau_xe = %s" in norm:
            yc = next((y for y in self.yeu_cau_dang_ky_xe if y["ma_yeu_cau_xe"] == params[0]), None)
            if not yc:
                return None
            res = self._res_map()[yc["ma_cu_dan"]]
            veh = self._veh_map()[yc["ma_phuong_tien"]]
            return {
                **yc,
                "ma_can_ho": res["ma_can_ho"],
                "ma_loai_xe": veh["ma_loai_xe"],
                "bien_so_xe": veh["bien_so_xe"],
                "ten_hang_dong_xe": veh["ten_hang_dong_xe"],
                "mau_xe": veh.get("mau_xe"),
                "duong_dan_cavet": veh["duong_dan_cavet"],
                "trang_thai_xe": veh["trang_thai_xe"],
            }
        if "FROM yeu_cau_dang_ky_xe WHERE ma_yeu_cau_xe = %s" in norm:
            yc = next((y for y in self.yeu_cau_dang_ky_xe if y["ma_yeu_cau_xe"] == params[0]), None)
            return dict(yc) if yc else None
        if "FROM vi_tri_do vt JOIN khu_vuc kv" in norm and "WHERE vt.ma_vi_tri = %s" in norm:
            vt = self._slot_map().get(params[0])
            if not vt:
                return None
            kv = self._zone_map()[vt["ma_khu_vuc"]]
            return {**vt, "ma_loai_xe": kv["ma_loai_xe"], "trang_thai_khu_vuc": kv["trang_thai_khu_vuc"]}
        if "FROM vi_tri_do WHERE ma_vi_tri = %s" in norm:
            vt = self._slot_map().get(params[0])
            return dict(vt) if vt else None
        if "FROM khu_vuc WHERE ma_khu_vuc = %s" in norm:
            kv = self._zone_map().get(params[0])
            return dict(kv) if kv else None
        if "FROM phan_bo_cho_do pb" in norm and "pb.ma_vi_tri = %s" in norm:
            apt, slot_code, status = params
            for pb in self.phan_bo_cho_do:
                if pb["ma_can_ho"] == apt and pb["ma_vi_tri"] == slot_code and pb["trang_thai_su_dung"] == status:
                    used = any(q["ma_vi_tri_co_dinh"] == slot_code for q in self.quan_ly_phuong_tien_ra_vao)
                    if not used:
                        return dict(pb)
            return None
        if "FROM phan_bo_cho_do" in norm and "loai_cho_cap = %s" in norm:
            apt, ltype, status = params
            for pb in self.phan_bo_cho_do:
                if pb["ma_can_ho"] == apt and pb["loai_cho_cap"] == ltype and pb["trang_thai_su_dung"] == status:
                    return {"ma_phan_bo": pb["ma_phan_bo"]}
            return None
        if "FROM phan_bo_cho_do" in norm and "WHERE ma_can_ho = %s AND ma_vi_tri = %s" in norm:
            apt, slot_code, status = params
            for pb in self.phan_bo_cho_do:
                if pb["ma_can_ho"] == apt and pb["ma_vi_tri"] == slot_code and pb["trang_thai_su_dung"] == status:
                    return dict(pb)
            return None
        if "FROM phan_bo_cho_do WHERE ma_phan_bo = %s" in norm:
            pb = next((p for p in self.phan_bo_cho_do if p["ma_phan_bo"] == params[0]), None)
            return dict(pb) if pb else None
        if "FROM quan_ly_phuong_tien_ra_vao WHERE ma_phuong_tien = %s" in norm:
            ql = next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_phuong_tien"] == params[0]), None)
            return dict(ql) if ql else None
        if "FROM quan_ly_phuong_tien_ra_vao WHERE ma_vi_tri_co_dinh = %s" in norm:
            ql = next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_vi_tri_co_dinh"] == params[0]), None)
            return dict(ql) if ql else None
        if "SELECT ma_yeu_cau_cho FROM yeu_cau_mua_them_cho" in norm:
            for y in self.yeu_cau_mua_them_cho:
                if y["ma_can_ho"] == params[0] and y["trang_thai_duyet"] == params[1]:
                    return {"ma_yeu_cau_cho": y["ma_yeu_cau_cho"]}
            return None
        if "FROM yeu_cau_mua_them_cho WHERE ma_yeu_cau_cho = %s" in norm:
            y = next((x for x in self.yeu_cau_mua_them_cho if x["ma_yeu_cau_cho"] == params[0]), None)
            return dict(y) if y else None
        raise NotImplementedError(f"fetch_one chưa hỗ trợ: {norm}")

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        norm = " ".join(sql.split())
        zmap = self._zone_map()
        smap = self._slot_map()
        rmap = self._res_map()
        amap = self._apt_map()
        vmap = self._veh_map()
        stmap = self._staff_map()

        if "FROM phan_bo_cho_do pb JOIN vi_tri_do vt" in norm and "ql.ma_quan_ly_xe IS NULL" in norm:
            apt, status = params
            out = []
            for pb in self.phan_bo_cho_do:
                if pb["ma_can_ho"] == apt and pb["trang_thai_su_dung"] == status:
                    if not any(q["ma_vi_tri_co_dinh"] == pb["ma_vi_tri"] for q in self.quan_ly_phuong_tien_ra_vao):
                        vt = smap[pb["ma_vi_tri"]]
                        kv = zmap[vt["ma_khu_vuc"]]
                        out.append({
                            "ma_phan_bo": pb["ma_phan_bo"],
                            "ma_vi_tri": pb["ma_vi_tri"],
                            "loai_cho_cap": pb["loai_cho_cap"],
                            "ma_khu_vuc": vt["ma_khu_vuc"],
                            "ten_vi_tri": vt["ten_vi_tri"],
                            "ten_khu_vuc": kv["ten_khu_vuc"],
                        })
            return out

        if "FROM phuong_tien pt JOIN loai_phuong_tien lpt" in norm and "WHERE pt.ma_cu_dan = %s" in norm:
            if len(params) == 4:
                pending_st, apt, _status_eff, res_id = params
            else:
                apt, _status_eff, res_id = params
                pending_st = RequestStatus.CHO_DUYET.value
            out = []
            for v in self.phuong_tien:
                if v["ma_cu_dan"] != res_id:
                    continue
                ql = next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_phuong_tien"] == v["ma_phuong_tien"]), None)
                slot_code = ql["ma_vi_tri_co_dinh"] if ql else None
                zone_code = ql["ma_khu_vuc_phan_cong"] if ql else None
                vt = smap.get(slot_code) if slot_code else None
                kv = zmap.get(vt["ma_khu_vuc"] if vt else zone_code)
                pb = next(
                    (p for p in self.phan_bo_cho_do if p["ma_vi_tri"] == slot_code and p["ma_can_ho"] == apt and p["trang_thai_su_dung"] == "dang_hieu_luc"),
                    None,
                ) if slot_code else None
                out.append({
                    **v,
                    "ten_loai_xe": "Ô tô" if v["ma_loai_xe"] == "O_TO" else "Xe máy",
                    "ma_vi_tri_co_dinh": slot_code,
                    "ten_vi_tri": vt["ten_vi_tri"] if vt else None,
                    "trang_thai_vi_tri": vt["trang_thai_vi_tri"] if vt else None,
                    "ma_khu_vuc_phan_cong": zone_code,
                    "ten_khu_vuc": kv["ten_khu_vuc"] if kv else None,
                    "tong_so_cho": kv["tong_so_cho"] if kv else None,
                    "so_xe_dang_gui": kv["so_xe_dang_gui"] if kv else None,
                    "dang_trong_bai": ql["dang_trong_bai"] if ql else False,
                    "thoi_gian_vao": ql.get("thoi_gian_vao") if ql else None,
                    "thoi_gian_ra": ql.get("thoi_gian_ra") if ql else None,
                    "loai_cho_cap": pb["loai_cho_cap"] if pb else None,
                    "co_yeu_cau_cho_duyet": any(
                        y["ma_phuong_tien"] == v["ma_phuong_tien"] and y["trang_thai_duyet"] == pending_st
                        for y in self.yeu_cau_dang_ky_xe
                    ),
                })
            return out

        if "FROM yeu_cau_dang_ky_xe yc JOIN phuong_tien pt" in norm:
            res_id = params[0]
            out = []
            for yc in reversed(self.yeu_cau_dang_ky_xe):
                if yc["ma_cu_dan"] == res_id:
                    v = vmap[yc["ma_phuong_tien"]]
                    nv = stmap.get(yc.get("ma_nhan_vien_duyet"))
                    out.append({
                        **yc,
                        "bien_so_xe": v["bien_so_xe"],
                        "ma_loai_xe": v["ma_loai_xe"],
                        "ten_loai_xe": "Ô tô" if v["ma_loai_xe"] == "O_TO" else "Xe máy",
                        "ten_hang_dong_xe": v["ten_hang_dong_xe"],
                        "mau_xe": v.get("mau_xe"),
                        "duong_dan_cavet": v["duong_dan_cavet"],
                        "trang_thai_xe": v["trang_thai_xe"],
                        "ten_quan_ly_duyet": nv["ho_ten"] if nv else None,
                    })
            return out

        if "FROM yeu_cau_dang_ky_xe yc JOIN cu_dan cd" in norm:
            out = []
            for yc in reversed(self.yeu_cau_dang_ky_xe):
                res = rmap[yc["ma_cu_dan"]]
                apt = amap[res["ma_can_ho"]]
                v = vmap[yc["ma_phuong_tien"]]
                ql = next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_phuong_tien"] == v["ma_phuong_tien"]), None)
                nv = stmap.get(yc.get("ma_nhan_vien_duyet"))
                row = {
                    **yc,
                    "ten_cu_dan": res["ho_ten"],
                    "so_dien_thoai": res["so_dien_thoai"],
                    "ma_can_ho": res["ma_can_ho"],
                    "toa_nha": apt["toa_nha"],
                    "so_tang": apt["so_tang"],
                    "so_cho_oto_mac_dinh": apt["so_cho_oto_mac_dinh"],
                    "so_cho_oto_mua_them": apt["so_cho_oto_mua_them"],
                    "ma_loai_xe": v["ma_loai_xe"],
                    "ten_loai_xe": "Ô tô" if v["ma_loai_xe"] == "O_TO" else "Xe máy",
                    "bien_so_xe": v["bien_so_xe"],
                    "ten_hang_dong_xe": v["ten_hang_dong_xe"],
                    "mau_xe": v.get("mau_xe"),
                    "duong_dan_cavet": v["duong_dan_cavet"],
                    "trang_thai_xe": v["trang_thai_xe"],
                    "ma_vi_tri_co_dinh": ql["ma_vi_tri_co_dinh"] if ql else None,
                    "ma_khu_vuc_phan_cong": ql["ma_khu_vuc_phan_cong"] if ql else None,
                    "dang_trong_bai": ql["dang_trong_bai"] if ql else False,
                    "ten_quan_ly_duyet": nv["ho_ten"] if nv else None,
                }
                if "yc.trang_thai_duyet = %s" in norm and row["trang_thai_duyet"] != params[0]:
                    continue
                out.append(row)
            return out

        if "FROM khu_vuc WHERE ma_loai_xe = %s AND trang_thai_khu_vuc = %s" in norm:
            vtype, st = params
            return [
                {**kv, "so_cho_trong": max(0, kv["tong_so_cho"] - kv["so_xe_dang_gui"])}
                for kv in self.khu_vuc
                if kv["ma_loai_xe"] == vtype and kv["trang_thai_khu_vuc"] == st
            ]

        if "FROM vi_tri_do vt JOIN khu_vuc kv" in norm and "WHERE kv.ma_loai_xe = %s" in norm:
            alloc_st, vtype, z_st = params
            out = []
            for vt in self.vi_tri_do:
                kv = zmap[vt["ma_khu_vuc"]]
                if kv["ma_loai_xe"] == vtype and kv["trang_thai_khu_vuc"] == z_st:
                    pb = next((p for p in self.phan_bo_cho_do if p["ma_vi_tri"] == vt["ma_vi_tri"] and p["trang_thai_su_dung"] == alloc_st), None)
                    ql = next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_vi_tri_co_dinh"] == vt["ma_vi_tri"]), None)
                    veh = vmap.get(ql["ma_phuong_tien"]) if ql else None
                    out.append({
                        **vt,
                        "ten_khu_vuc": kv["ten_khu_vuc"],
                        "can_ho_dang_giu": pb["ma_can_ho"] if pb else None,
                        "bien_so_xe": veh["bien_so_xe"] if veh else None,
                    })
            return out

        if "FROM bang_gia WHERE trang_thai_gia = %s" in norm:
            return [dict(b) for b in self.bang_gia if b["trang_thai_gia"] == params[0]]

        if "FROM phan_bo_cho_do pb JOIN vi_tri_do vt" in norm and "WHERE pb.ma_can_ho = %s" in norm:
            active_veh_st, apt = params
            out = []
            for pb in self.phan_bo_cho_do:
                if pb["ma_can_ho"] != apt:
                    continue
                vt = smap[pb["ma_vi_tri"]]
                kv = zmap[vt["ma_khu_vuc"]]
                ql = next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_vi_tri_co_dinh"] == pb["ma_vi_tri"]), None)
                veh = vmap.get(ql["ma_phuong_tien"]) if ql else None
                if veh and veh["trang_thai_xe"] != active_veh_st:
                    veh = None
                out.append({
                    **pb,
                    "ten_vi_tri": vt["ten_vi_tri"],
                    "ma_khu_vuc": vt["ma_khu_vuc"],
                    "nhom_vi_tri": vt["nhom_vi_tri"],
                    "trang_thai_vi_tri": vt["trang_thai_vi_tri"],
                    "ten_khu_vuc": kv["ten_khu_vuc"],
                    "ma_phuong_tien": veh["ma_phuong_tien"] if veh else None,
                    "dang_trong_bai": ql["dang_trong_bai"] if ql else False,
                    "bien_so_xe": veh["bien_so_xe"] if veh else None,
                    "ten_hang_dong_xe": veh["ten_hang_dong_xe"] if veh else None,
                })
            return out

        if "FROM yeu_cau_mua_them_cho yc" in norm and "WHERE yc.ma_can_ho = %s" in norm:
            apt = params[0]
            out = []
            for y in reversed(self.yeu_cau_mua_them_cho):
                if y["ma_can_ho"] == apt:
                    vt = smap.get(y.get("ma_vi_tri_duoc_duyet"))
                    kv = zmap.get(vt["ma_khu_vuc"]) if vt else None
                    nv = stmap.get(y.get("ma_nhan_vien_duyet"))
                    out.append({
                        **y,
                        "ten_vi_tri": vt["ten_vi_tri"] if vt else None,
                        "ten_khu_vuc": kv["ten_khu_vuc"] if kv else None,
                        "ten_quan_ly_duyet": nv["ho_ten"] if nv else None,
                    })
            return out

        if "FROM yeu_cau_mua_them_cho yc JOIN cu_dan cd" in norm:
            out = []
            for y in reversed(self.yeu_cau_mua_them_cho):
                if "yc.trang_thai_duyet = %s" in norm and y["trang_thai_duyet"] != params[0]:
                    continue
                res = rmap[y["ma_cu_dan"]]
                apt = amap[y["ma_can_ho"]]
                vt = smap.get(y.get("ma_vi_tri_duoc_duyet"))
                kv = zmap.get(vt["ma_khu_vuc"]) if vt else None
                nv = stmap.get(y.get("ma_nhan_vien_duyet"))
                out.append({
                    **y,
                    "ten_cu_dan": res["ho_ten"],
                    "so_dien_thoai": res["so_dien_thoai"],
                    "toa_nha": apt["toa_nha"],
                    "so_tang": apt["so_tang"],
                    "so_cho_oto_mac_dinh": apt["so_cho_oto_mac_dinh"],
                    "so_cho_oto_mua_them": apt["so_cho_oto_mua_them"],
                    "ten_vi_tri": vt["ten_vi_tri"] if vt else None,
                    "ten_khu_vuc": kv["ten_khu_vuc"] if kv else None,
                    "ten_quan_ly_duyet": nv["ho_ten"] if nv else None,
                })
            return out

        if "FROM phan_bo_cho_do pb JOIN can_ho ch" in norm:
            eff_st, active_veh_st = params[:2]
            out = []
            for pb in reversed(self.phan_bo_cho_do):
                if "pb.trang_thai_su_dung = %s" in norm and pb["trang_thai_su_dung"] != eff_st:
                    continue
                apt = amap[pb["ma_can_ho"]]
                vt = smap[pb["ma_vi_tri"]]
                kv = zmap[vt["ma_khu_vuc"]]
                ql = next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_vi_tri_co_dinh"] == pb["ma_vi_tri"] and pb["trang_thai_su_dung"] == eff_st), None)
                veh = vmap.get(ql["ma_phuong_tien"]) if ql else None
                if veh and veh["trang_thai_xe"] != active_veh_st:
                    veh = None
                out.append({
                    **pb,
                    "toa_nha": apt["toa_nha"],
                    "so_tang": apt["so_tang"],
                    "so_cho_oto_mac_dinh": apt["so_cho_oto_mac_dinh"],
                    "so_cho_oto_mua_them": apt["so_cho_oto_mua_them"],
                    "ten_vi_tri": vt["ten_vi_tri"],
                    "ma_khu_vuc": vt["ma_khu_vuc"],
                    "ten_khu_vuc": kv["ten_khu_vuc"],
                    "trang_thai_vi_tri": vt["trang_thai_vi_tri"],
                    "ma_phuong_tien": veh["ma_phuong_tien"] if veh else None,
                    "dang_trong_bai": ql["dang_trong_bai"] if ql else False,
                    "bien_so_xe": veh["bien_so_xe"] if veh else None,
                    "ten_hang_dong_xe": veh["ten_hang_dong_xe"] if veh else None,
                })
            return out

        if "FROM vi_tri_do vt LEFT JOIN quan_ly_phuong_tien_ra_vao ql" in norm:
            alloc_st, zid = params
            out = []
            for vt in self.vi_tri_do:
                if vt["ma_khu_vuc"] == zid:
                    ql = next((q for q in self.quan_ly_phuong_tien_ra_vao if q["ma_vi_tri_co_dinh"] == vt["ma_vi_tri"]), None)
                    veh = vmap.get(ql["ma_phuong_tien"]) if ql else None
                    pb = next((p for p in self.phan_bo_cho_do if p["ma_vi_tri"] == vt["ma_vi_tri"] and p["trang_thai_su_dung"] == alloc_st), None)
                    out.append({
                        **vt,
                        "bien_so_xe": veh["bien_so_xe"] if veh else None,
                        "ma_can_ho": pb["ma_can_ho"] if pb else None,
                    })
            return out

        raise NotImplementedError(f"fetch_all chưa hỗ trợ: {norm}")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        norm = " ".join(sql.split())
        if norm.startswith("INSERT INTO phan_bo_cho_do"):
            apt, slot_code, ltype, st = params
            self.phan_bo_cho_do.append({
                "ma_phan_bo": self._next_id("pb"),
                "ma_can_ho": apt,
                "ma_vi_tri": slot_code,
                "loai_cho_cap": ltype,
                "ngay_cap": datetime.now().isoformat(timespec="seconds"),
                "trang_thai_su_dung": st,
                "ly_do_thu_hoi": None,
            })
            return 1
        if norm.startswith("INSERT INTO quan_ly_phuong_tien_ra_vao"):
            vid, slot_code, zone_code = params
            self.quan_ly_phuong_tien_ra_vao.append({
                "ma_quan_ly_xe": self._next_id("ql"),
                "ma_phuong_tien": vid,
                "ma_vi_tri_co_dinh": slot_code,
                "ma_khu_vuc_phan_cong": zone_code,
                "thoi_gian_vao": None,
                "thoi_gian_ra": None,
                "dang_trong_bai": False,
            })
            return 1
        if norm.startswith("UPDATE quan_ly_phuong_tien_ra_vao SET ma_vi_tri_co_dinh = %s, ma_khu_vuc_phan_cong = %s"):
            slot_code, zone_code, vid = params
            for q in self.quan_ly_phuong_tien_ra_vao:
                if q["ma_phuong_tien"] == vid:
                    q["ma_vi_tri_co_dinh"] = slot_code
                    q["ma_khu_vuc_phan_cong"] = zone_code
                    q["dang_trong_bai"] = False
                    return 1
            return 0
        if norm.startswith("UPDATE quan_ly_phuong_tien_ra_vao SET ma_vi_tri_co_dinh = NULL"):
            target_id = params[0]
            for q in self.quan_ly_phuong_tien_ra_vao:
                if "WHERE ma_phuong_tien = %s" in norm and q["ma_phuong_tien"] == target_id:
                    q["ma_vi_tri_co_dinh"] = None
                    q["ma_khu_vuc_phan_cong"] = None
                    return 1
                if "WHERE ma_quan_ly_xe = %s" in norm and q["ma_quan_ly_xe"] == target_id:
                    q["ma_vi_tri_co_dinh"] = None
                    q["ma_khu_vuc_phan_cong"] = None
                    return 1
            return 0
        if norm.startswith("UPDATE vi_tri_do SET trang_thai_vi_tri = %s"):
            st, code = params
            for vt in self.vi_tri_do:
                if vt["ma_vi_tri"] == code:
                    vt["trang_thai_vi_tri"] = st
                    return 1
            return 0
        if norm.startswith("UPDATE phuong_tien SET trang_thai_xe = %s WHERE ma_phuong_tien = %s"):
            st, vid = params
            for v in self.phuong_tien:
                if v["ma_phuong_tien"] == vid:
                    v["trang_thai_xe"] = st
                    return 1
            return 0
        if norm.startswith("UPDATE phuong_tien SET bien_so_xe = %s"):
            plate, ten, mau, cavet, vid = params
            for v in self.phuong_tien:
                if v["ma_phuong_tien"] == vid:
                    v["bien_so_xe"] = plate
                    v["ten_hang_dong_xe"] = ten
                    v["mau_xe"] = mau
                    v["duong_dan_cavet"] = cavet
                    return 1
            return 0
        if norm.startswith("UPDATE phan_bo_cho_do SET trang_thai_su_dung = %s"):
            st, reason, pb_id = params
            for pb in self.phan_bo_cho_do:
                if pb["ma_phan_bo"] == pb_id:
                    pb["trang_thai_su_dung"] = st
                    pb["ly_do_thu_hoi"] = reason
                    return 1
            return 0
        raise NotImplementedError(f"execute chưa hỗ trợ: {norm}")

    def execute_returning(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        norm = " ".join(sql.split())
        if norm.startswith("INSERT INTO phuong_tien"):
            res_id, vtype, plate, ten, mau, cavet = params
            row = {
                "ma_phuong_tien": self._next_id("pt"),
                "ma_cu_dan": res_id,
                "ma_loai_xe": vtype,
                "bien_so_xe": plate,
                "ten_hang_dong_xe": ten,
                "mau_xe": mau,
                "duong_dan_cavet": cavet,
                "trang_thai_xe": "cho_duyet",
            }
            self.phuong_tien.append(row)
            return dict(row)
        if norm.startswith("UPDATE phuong_tien SET ma_cu_dan = %s"):
            res_id, vtype, ten, mau, cavet, vid = params
            for v in self.phuong_tien:
                if v["ma_phuong_tien"] == vid:
                    v.update({
                        "ma_cu_dan": res_id,
                        "ma_loai_xe": vtype,
                        "ten_hang_dong_xe": ten,
                        "mau_xe": mau,
                        "duong_dan_cavet": cavet,
                        "trang_thai_xe": "cho_duyet",
                    })
                    return dict(v)
            return None
        if norm.startswith("INSERT INTO yeu_cau_dang_ky_xe"):
            if "vi_tri_mong_muon" in norm:
                res_id, vid, rtype, pref, st = params
                change_txt = None
            else:
                res_id, vid, rtype, change_txt, st = params
                pref = None
            row = {
                "ma_yeu_cau_xe": self._next_id("ycx"),
                "ma_cu_dan": res_id,
                "ma_phuong_tien": vid,
                "loai_yeu_cau": rtype,
                "vi_tri_mong_muon": pref,
                "noi_dung_thay_doi": change_txt,
                "trang_thai_duyet": st,
                "ma_nhan_vien_duyet": None,
                "ly_do_phan_hoi": None,
                "ngay_gui_yeu_cau": datetime.now().isoformat(timespec="seconds"),
            }
            self.yeu_cau_dang_ky_xe.append(row)
            return dict(row)
        if norm.startswith("UPDATE yeu_cau_dang_ky_xe"):
            st, staff_id, feedback, req_id = params
            for yc in self.yeu_cau_dang_ky_xe:
                if yc["ma_yeu_cau_xe"] == req_id:
                    yc["trang_thai_duyet"] = st
                    yc["ma_nhan_vien_duyet"] = staff_id
                    yc["ly_do_phan_hoi"] = feedback
                    return dict(yc)
            return None
        if norm.startswith("INSERT INTO yeu_cau_mua_them_cho"):
            res_id, apt, pref, reason, st = params
            row = {
                "ma_yeu_cau_cho": self._next_id("ycc"),
                "ma_cu_dan": res_id,
                "ma_can_ho": apt,
                "nhu_cau_vi_tri": pref,
                "ly_do_mua_them": reason,
                "trang_thai_duyet": st,
                "ma_vi_tri_duoc_duyet": None,
                "ma_nhan_vien_duyet": None,
                "ly_do_tu_choi": None,
                "ngay_gui_yeu_cau": datetime.now().isoformat(timespec="seconds"),
            }
            self.yeu_cau_mua_them_cho.append(row)
            return dict(row)
        if norm.startswith("INSERT INTO phan_bo_cho_do"):
            apt, slot_code, ltype, st = params
            row = {
                "ma_phan_bo": self._next_id("pb"),
                "ma_can_ho": apt,
                "ma_vi_tri": slot_code,
                "loai_cho_cap": ltype,
                "ngay_cap": datetime.now().isoformat(timespec="seconds"),
                "trang_thai_su_dung": st,
                "ly_do_thu_hoi": None,
            }
            self.phan_bo_cho_do.append(row)
            return dict(row)
        if norm.startswith("UPDATE can_ho SET so_cho_oto_mua_them = so_cho_oto_mua_them + 1"):
            apt = self._apt_map()[params[0]]
            apt["so_cho_oto_mua_them"] += 1
            return dict(apt)
        if norm.startswith("UPDATE can_ho SET so_cho_oto_mua_them = GREATEST(0, so_cho_oto_mua_them - 1)"):
            apt = self._apt_map()[params[0]]
            apt["so_cho_oto_mua_them"] = max(0, apt["so_cho_oto_mua_them"] - 1)
            return dict(apt)
        if norm.startswith("UPDATE yeu_cau_mua_them_cho") and "ma_vi_tri_duoc_duyet = %s" in norm:
            st, slot_code, staff_id, req_id = params
            for y in self.yeu_cau_mua_them_cho:
                if y["ma_yeu_cau_cho"] == req_id:
                    y["trang_thai_duyet"] = st
                    y["ma_vi_tri_duoc_duyet"] = slot_code
                    y["ma_nhan_vien_duyet"] = staff_id
                    return dict(y)
            return None
        if norm.startswith("UPDATE yeu_cau_mua_them_cho") and "ly_do_tu_choi = %s" in norm:
            st, staff_id, reason, req_id = params
            for y in self.yeu_cau_mua_them_cho:
                if y["ma_yeu_cau_cho"] == req_id:
                    y["trang_thai_duyet"] = st
                    y["ma_nhan_vien_duyet"] = staff_id
                    y["ly_do_tu_choi"] = reason
                    return dict(y)
            return None
        if norm.startswith("UPDATE phan_bo_cho_do SET trang_thai_su_dung = %s"):
            st, reason, pb_id = params
            for pb in self.phan_bo_cho_do:
                if pb["ma_phan_bo"] == pb_id:
                    pb["trang_thai_su_dung"] = st
                    pb["ly_do_thu_hoi"] = reason
                    return dict(pb)
            return None
        raise NotImplementedError(f"execute_returning chưa hỗ trợ: {norm}")


class TestLongVehicleSlots(unittest.TestCase):
    """Kiểm thử toàn bộ nghiệp vụ và UI của Thành viên 4 (Long)."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        root = Path(self.tmp_dir.name)
        self.logs_dir = root / "logs"
        self.cavet_dir = root / "cavet"
        self.file_logger = FileLogger(self.logs_dir)
        self.db = _LongFakeDB()
        self.router = Router(file_logger=self.file_logger)
        self.handler = register_vehicle_routes(
            self.router,
            file_logger=self.file_logger,
            db_provider=self.db,
            cavet_dir=self.cavet_dir,
        )

        self.res_session = ClientSession(
            session_id="s-res",
            sock=None,  # type: ignore[arg-type]
            ip_address="127.0.0.1",
            port=50001,
            ma_tai_khoan=4,
            ten_dang_nhap="cudan_a101",
            vai_tro=Roles.CU_DAN.value,
            ho_ten="Lê Minh Cư Dân",
            ma_cu_dan=1,
            ma_can_ho="A1-01",
        )
        self.mgr_session = ClientSession(
            session_id="s-mgr",
            sock=None,  # type: ignore[arg-type]
            ip_address="127.0.0.1",
            port=50002,
            ma_tai_khoan=2,
            ten_dang_nhap="quanly01",
            vai_tro=Roles.QUAN_LY.value,
            ho_ten="Nguyễn Văn Quản Lý",
            ma_nhan_vien=1,
        )
        self.adm_session = ClientSession(
            session_id="s-adm",
            sock=None,  # type: ignore[arg-type]
            ip_address="127.0.0.1",
            port=50003,
            ma_tai_khoan=1,
            ten_dang_nhap="admin01",
            vai_tro=Roles.ADMIN.value,
            ho_ten="Admin System",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _call(self, session: ClientSession, action: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.router.dispatch(session, action, payload or {}, "req-1")

    def test_01_workflow_02_05_register_cavet_approve_and_reject(self) -> None:
        """Workflow 02 & 05: Đăng ký xe + Base64 cà-vẹt -> Quản lý duyệt gán B1-01 & từ chối/đăng ký lại xe máy."""
        sample_b64 = _make_sample_png_base64()

        # 3.2.1: Cư dân gửi đăng ký ô tô thứ 1
        r1 = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": VehicleRequestTypes.DANG_KY_MOI.value,
                "ma_loai_xe": VehicleTypes.O_TO.value,
                "bien_so_xe": "51h88899",
                "ten_hang_dong_xe": "Toyota Camry 2.5Q",
                "mau_xe": "Đen",
                "cavet_image_base64": sample_b64,
            },
        )
        self.assertTrue(r1["ok"], r1)
        veh_id = r1["data"]["vehicle"]["ma_phuong_tien"]
        req_id = r1["data"]["request"]["ma_yeu_cau_xe"]
        self.assertEqual(r1["data"]["vehicle"]["bien_so_xe"], "51H-888.99")
        self.assertTrue(any(self.cavet_dir.iterdir()))

        # Tải lại ảnh cà-vẹt qua socket (vehicle.get_cavet)
        c_resp = self._call(self.mgr_session, Actions.VEHICLE_GET_CAVET, {"ma_phuong_tien": veh_id})
        self.assertTrue(c_resp["ok"])
        self.assertEqual(c_resp["data"]["image_base64"], sample_b64)

        # 1.4.1 & get_assignable
        m_list = self._call(self.mgr_session, Actions.VEHICLE_MANAGER_LIST_REQUESTS, {"trang_thai_duyet": "cho_duyet"})
        self.assertEqual(m_list["data"]["total"], 1)
        assignable = self._call(
            self.mgr_session,
            Actions.VEHICLE_GET_ASSIGNABLE,
            {"ma_loai_xe": VehicleTypes.O_TO.value, "ma_can_ho": "A1-01"},
        )
        self.assertTrue(any(s["ma_vi_tri"] == "B1-01" and s["assignable"] for s in assignable["data"]["slots"]))
        # Ô vãng lai C-01 không được phép chọn cho xe cư dân
        self.assertTrue(any(s["ma_vi_tri"] == "C-01" and not s["assignable"] for s in assignable["data"]["slots"]))

        # 1.4.2: Quản lý duyệt ô tô gán B1-01
        app_resp = self._call(
            self.mgr_session,
            Actions.VEHICLE_APPROVE_REQUEST,
            {"ma_yeu_cau_xe": req_id, "ma_vi_tri_co_dinh": "B1-01"},
        )
        self.assertTrue(app_resp["ok"], app_resp)
        self.assertEqual(self.db.phuong_tien[0]["trang_thai_xe"], VehicleStatus.DANG_HOAT_DONG.value)
        self.assertEqual(self.db.vi_tri_do[0]["trang_thai_vi_tri"], SlotStatus.DA_PHAN_CHO.value)
        self.assertEqual(self.db.phan_bo_cho_do[0]["loai_cho_cap"], SlotAllocationTypes.CHO_MAC_DINH_XE_1.value)

        # 3.2.2 & 3.2.3: Cư dân xem danh sách xe & trạng thái
        res_list = self._call(self.res_session, Actions.VEHICLE_RESIDENT_LIST)
        self.assertTrue(res_list["ok"], res_list)
        self.assertEqual(len(res_list["data"]["vehicles"]), 1)
        self.assertEqual(res_list["data"]["vehicles"][0]["ma_vi_tri_co_dinh"], "B1-01")
        self.assertEqual(res_list["data"]["requests"][0]["trang_thai_duyet"], RequestStatus.DA_DUYET.value)

        # Thử đăng ký xe máy -> bị từ chối -> đăng ký lại cùng biển số và được duyệt vào KHU_A
        rm1 = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": VehicleRequestTypes.DANG_KY_MOI.value,
                "ma_loai_xe": VehicleTypes.XE_MAY.value,
                "bien_so_xe": "59A1-123.45",
                "ten_hang_dong_xe": "Honda SH 150i",
                "mau_xe": "Trắng",
                "cavet_image_base64": sample_b64,
            },
        )
        self.assertTrue(rm1["ok"])
        rej = self._call(
            self.mgr_session,
            Actions.VEHICLE_REJECT_REQUEST,
            {"ma_yeu_cau_xe": rm1["data"]["request"]["ma_yeu_cau_xe"], "ly_do_phan_hoi": "Ảnh mờ"},
        )
        self.assertTrue(rej["ok"])

        rm2 = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": VehicleRequestTypes.DANG_KY_MOI.value,
                "ma_loai_xe": VehicleTypes.XE_MAY.value,
                "bien_so_xe": "59A1-123.45",
                "ten_hang_dong_xe": "Honda SH 150i",
                "mau_xe": "Trắng",
                "cavet_image_base64": sample_b64,
            },
        )
        self.assertTrue(rm2["ok"], rm2)
        app_m = self._call(
            self.mgr_session,
            Actions.VEHICLE_APPROVE_REQUEST,
            {"ma_yeu_cau_xe": rm2["data"]["request"]["ma_yeu_cau_xe"], "ma_khu_vuc_phan_cong": "KHU_A"},
        )
        self.assertTrue(app_m["ok"], app_m)

    def test_02_edit_and_stop_vehicle_safety(self) -> None:
        """3.2.4 Chỉnh sửa thông tin xe & 3.2.5 Ngừng đăng ký xe (chặn khi xe đang trong bãi)."""
        sample_b64 = _make_sample_png_base64()
        r1 = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": "dang_ky_moi",
                "ma_loai_xe": "O_TO",
                "bien_so_xe": "51H-888.99",
                "ten_hang_dong_xe": "Toyota Camry",
                "mau_xe": "Đen",
                "cavet_image_base64": sample_b64,
            },
        )
        veh_id = r1["data"]["vehicle"]["ma_phuong_tien"]
        self._call(
            self.mgr_session,
            Actions.VEHICLE_APPROVE_REQUEST,
            {"ma_yeu_cau_xe": r1["data"]["request"]["ma_yeu_cau_xe"], "ma_vi_tri_co_dinh": "B1-01"},
        )

        # 3.2.4: Sửa thông tin xe
        ed = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": VehicleRequestTypes.CHINH_SUA_THONG_TIN.value,
                "ma_phuong_tien": veh_id,
                "bien_so_xe": "51H-888.99",
                "ten_hang_dong_xe": "Toyota Camry Hybrid",
                "mau_xe": "Trắng ngọc trai",
            },
        )
        self.assertTrue(ed["ok"], ed)
        app_ed = self._call(
            self.mgr_session,
            Actions.VEHICLE_APPROVE_REQUEST,
            {"ma_yeu_cau_xe": ed["data"]["request"]["ma_yeu_cau_xe"]},
        )
        self.assertTrue(app_ed["ok"])
        self.assertEqual(self.db.phuong_tien[0]["ten_hang_dong_xe"], "Toyota Camry Hybrid")

        # Giả lập xe đang ở trong bãi -> chặn ngừng đăng ký
        self.db.quan_ly_phuong_tien_ra_vao[0]["dang_trong_bai"] = True
        stop_blocked = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {"loai_yeu_cau": VehicleRequestTypes.NGUNG_DANG_KY.value, "ma_phuong_tien": veh_id, "ly_do": "Bán xe"},
        )
        self.assertFalse(stop_blocked["ok"])

        # Đưa xe ra ngoài bãi -> gửi & duyệt ngừng đăng ký -> thu hồi ô mặc định
        self.db.quan_ly_phuong_tien_ra_vao[0]["dang_trong_bai"] = False
        stop_ok = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {"loai_yeu_cau": VehicleRequestTypes.NGUNG_DANG_KY.value, "ma_phuong_tien": veh_id, "ly_do": "Bán xe"},
        )
        self.assertTrue(stop_ok["ok"])
        app_stop = self._call(
            self.mgr_session,
            Actions.VEHICLE_APPROVE_REQUEST,
            {"ma_yeu_cau_xe": stop_ok["data"]["request"]["ma_yeu_cau_xe"]},
        )
        self.assertTrue(app_stop["ok"])
        self.assertEqual(self.db.phuong_tien[0]["trang_thai_xe"], VehicleStatus.NGUNG_HOAT_DONG.value)
        self.assertEqual(self.db.vi_tri_do[0]["trang_thai_vi_tri"], SlotStatus.TRONG.value)
        self.assertEqual(self.db.phan_bo_cho_do[0]["trang_thai_su_dung"], SlotAllocationStatus.DA_THU_HOI.value)

    def test_03_quota_limit_and_extra_slot_workflow(self) -> None:
        """Hạn mức ô tô thứ 2+, mua thêm chỗ (3.3.1), duyệt cấp ô B1-02 (1.4.16) & gắn xe thứ 2."""
        sample_b64 = _make_sample_png_base64()
        r1 = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": "dang_ky_moi",
                "ma_loai_xe": "O_TO",
                "bien_so_xe": "51H-111.11",
                "ten_hang_dong_xe": "Mazda CX-5",
                "cavet_image_base64": sample_b64,
            },
        )
        self._call(
            self.mgr_session,
            Actions.VEHICLE_APPROVE_REQUEST,
            {"ma_yeu_cau_xe": r1["data"]["request"]["ma_yeu_cau_xe"], "ma_vi_tri_co_dinh": "B1-01"},
        )

        # Thử đăng ký ô tô thứ 2 khi chưa mua thêm -> bị chặn
        r2_fail = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": "dang_ky_moi",
                "ma_loai_xe": "O_TO",
                "bien_so_xe": "51H-222.22",
                "ten_hang_dong_xe": "Kia Carnival",
                "cavet_image_base64": sample_b64,
            },
        )
        self.assertFalse(r2_fail["ok"])

        # Gửi yêu cầu mua thêm chỗ (3.3.1)
        es = self._call(
            self.res_session,
            Actions.SLOT_REQUEST_SUBMIT,
            {"nhu_cau_vi_tri": "Hầm B1", "ly_do_mua_them": "Có thêm xe ô tô thứ 2"},
        )
        self.assertTrue(es["ok"], es)
        es_id = es["data"]["request"]["ma_yeu_cau_cho"]

        # Quản lý duyệt & cấp ô B1-02 (1.4.16)
        es_app = self._call(
            self.mgr_session,
            Actions.SLOT_REQUEST_APPROVE,
            {"ma_yeu_cau_cho": es_id, "ma_vi_tri": "B1-02"},
        )
        self.assertTrue(es_app["ok"], es_app)
        self.assertEqual(self.db.can_ho[0]["so_cho_oto_mua_them"], 1)
        self.assertEqual(self.db.vi_tri_do[1]["trang_thai_vi_tri"], SlotStatus.DA_PHAN_CHO.value)

        # Cư dân xem danh sách chỗ được cấp (3.3.3, 3.3.4)
        es_data = self._call(self.res_session, Actions.SLOT_REQUEST_RESIDENT_DATA)
        self.assertEqual(es_data["data"]["quota"]["tong_suat_oto"], 2)

        # Đăng ký & duyệt xe ô tô thứ 2 vào suất B1-02 đã cấp sẵn
        r2_ok = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": "dang_ky_moi",
                "ma_loai_xe": "O_TO",
                "bien_so_xe": "51H-222.22",
                "ten_hang_dong_xe": "Kia Carnival",
                "cavet_image_base64": sample_b64,
            },
        )
        self.assertTrue(r2_ok["ok"], r2_ok)
        assignable = self._call(
            self.mgr_session,
            Actions.VEHICLE_GET_ASSIGNABLE,
            {"ma_loai_xe": "O_TO", "ma_can_ho": "A1-01"},
        )
        self.assertEqual(assignable["data"]["preallocated_slots"][0]["ma_vi_tri"], "B1-02")

        app2 = self._call(
            self.mgr_session,
            Actions.VEHICLE_APPROVE_REQUEST,
            {"ma_yeu_cau_xe": r2_ok["data"]["request"]["ma_yeu_cau_xe"], "ma_vi_tri_co_dinh": "B1-02"},
        )
        self.assertTrue(app2["ok"], app2)

    def test_04_revoke_safety_and_admin_rbac(self) -> None:
        """1.4.19 Thu hồi chỗ đỗ chặn khi ô đang có xe & chặn Admin/Cư dân gọi trái quyền."""
        es = self._call(
            self.res_session,
            Actions.SLOT_REQUEST_SUBMIT,
            {"nhu_cau_vi_tri": "Hầm B1", "ly_do_mua_them": "Mua thêm chỗ"},
        )
        es_app = self._call(
            self.mgr_session,
            Actions.SLOT_REQUEST_APPROVE,
            {"ma_yeu_cau_cho": es["data"]["request"]["ma_yeu_cau_cho"], "ma_vi_tri": "B1-02"},
        )
        pb_id = es_app["data"]["allocation"]["ma_phan_bo"]

        # RBAC: Admin không thể duyệt / thu hồi; Cư dân không thể gọi manager actions
        adm_try = self._call(self.adm_session, Actions.ALLOCATION_REVOKE, {"ma_phan_bo": pb_id, "ly_do_thu_hoi": "Test"})
        self.assertFalse(adm_try["ok"])
        self.assertEqual(adm_try["error_code"], "ADMIN_READ_ONLY")

        res_try = self._call(self.res_session, Actions.VEHICLE_MANAGER_LIST_REQUESTS)
        self.assertFalse(res_try["ok"])
        self.assertEqual(res_try["error_code"], "FORBIDDEN")

        # Giả lập ô B1-02 đang có xe đỗ -> chặn thu hồi
        self.db.vi_tri_do[1]["trang_thai_vi_tri"] = SlotStatus.DANG_CO_XE.value
        rev_blocked = self._call(self.mgr_session, Actions.ALLOCATION_REVOKE, {"ma_phan_bo": pb_id, "ly_do_thu_hoi": "Hết hạn"})
        self.assertFalse(rev_blocked["ok"])

        # Xe rời ô -> thu hồi thành công -> ô về 'trong' & giảm so_cho_oto_mua_them
        self.db.vi_tri_do[1]["trang_thai_vi_tri"] = SlotStatus.DA_PHAN_CHO.value
        rev_ok = self._call(self.mgr_session, Actions.ALLOCATION_REVOKE, {"ma_phan_bo": pb_id, "ly_do_thu_hoi": "Cư dân trả chỗ"})
        self.assertTrue(rev_ok["ok"], rev_ok)
        self.assertEqual(self.db.vi_tri_do[1]["trang_thai_vi_tri"], SlotStatus.TRONG.value)
        self.assertEqual(self.db.can_ho[0]["so_cho_oto_mua_them"], 0)
        self.assertEqual(self.db.phan_bo_cho_do[0]["trang_thai_su_dung"], SlotAllocationStatus.DA_THU_HOI.value)

    def test_05_resident_lookup_and_all_ui_offscreen(self) -> None:
        """3.4.1–3.4.4 Tra cứu định vị xe (tối đa 10 lượt) & Render offscreen toàn bộ UI Long."""
        sample_b64 = _make_sample_png_base64()
        r1 = self._call(
            self.res_session,
            Actions.VEHICLE_SUBMIT_REQUEST,
            {
                "loai_yeu_cau": "dang_ky_moi",
                "ma_loai_xe": "O_TO",
                "bien_so_xe": "51H-888.99",
                "ten_hang_dong_xe": "Toyota Camry",
                "mau_xe": "Đen",
                "cavet_image_base64": sample_b64,
            },
        )
        self._call(
            self.mgr_session,
            Actions.VEHICLE_APPROVE_REQUEST,
            {"ma_yeu_cau_xe": r1["data"]["request"]["ma_yeu_cau_xe"], "ma_vi_tri_co_dinh": "B1-01"},
        )
        for i in range(12):
            self.file_logger.log_parking_history(
                ma_luot_gui=f"TURN-{i+1}",
                loai_khach="cu_dan",
                bien_so_xe="51H-888.99",
                ma_loai_xe="O_TO",
                ma_khu_vuc="HAM_B1",
                ma_vi_tri="B1-01",
                thoi_gian_vao=f"2026-11-16T08:{i:02d}:00",
                thoi_gian_ra=f"2026-11-16T17:{i:02d}:00",
                trang_thai="da_ra_bai",
            )

        lookup = self._call(self.res_session, Actions.VEHICLE_RESIDENT_LOOKUP)
        self.assertTrue(lookup["ok"])
        v0 = lookup["data"]["vehicles"][0]
        self.assertEqual(len(v0["recent_history"]), 10)
        self.assertIn("HAM_B1", lookup["data"]["zone_maps"])

        # Offscreen UI verification
        _app = QApplication.instance() or QApplication([])

        zoom_dlg = ZoomableCavetDialog(image_base64=sample_b64)
        self.assertIsNotNone(zoom_dlg.preview.lbl_image.pixmap())
        zoom_dlg.close()

        picker_dlg = SlotPickerDialog(
            title="Test Picker",
            ma_loai_xe="O_TO",
            ma_can_ho="A1-01",
            assignable_data={
                "ma_loai_xe": "O_TO",
                "zones": self.db.khu_vuc[1:2],
                "slots": [{"ma_vi_tri": "B1-02", "ma_khu_vuc": "HAM_B1", "trang_thai_vi_tri": "trong", "assignable": True, "is_apartment_preallocated": False}],
                "preallocated_slots": [],
            },
            cavet_base64=sample_b64,
        )
        picker_dlg.picker._on_pick("B1-02")
        self.assertEqual(picker_dlg.selected_slot_code(), "B1-02")
        picker_dlg.close()

        mgr_approval = VehicleApprovalView(read_only=False)
        mgr_approval.set_vehicle_requests(
            self._call(self.mgr_session, Actions.VEHICLE_MANAGER_LIST_REQUESTS, {"trang_thai_duyet": "all"})["data"]["items"]
        )
        self.assertEqual(mgr_approval.tbl_vehicle_reqs.rowCount(), 1)
        mgr_approval.close()

        ro_approval = VehicleApprovalView(read_only=True)
        self.assertFalse(ro_approval.btn_approve_v.isEnabled())
        ro_approval.close()

        res_view = ResidentVehicleView()
        res_view.set_data(self._call(self.res_session, Actions.VEHICLE_RESIDENT_LIST)["data"])
        self.assertEqual(res_view.tbl_vehicles.rowCount(), 1)
        res_view.close()

        req_dlg = VehicleRequestDialog()
        self.assertFalse(req_dlg._validate_plate_realtime())
        req_dlg.close()

        es_view = ExtraSlotView()
        es_view.set_data(self._call(self.res_session, Actions.SLOT_REQUEST_RESIDENT_DATA)["data"])
        self.assertEqual(es_view.tbl_allocations.rowCount(), 1)
        es_view.close()

        lk_view = VehicleLookupView()
        lk_view.set_data(lookup["data"])
        self.assertEqual(lk_view.tbl_history.rowCount(), 10)
        lk_view._switch_map_mode(1)
        self.assertEqual(lk_view.map_stack.currentIndex(), 1)
        lk_view.close()

        # Kiểm tra ghép vào MainWindow, ManagerWindow, AdminWindow
        main_res = MainWindow(session_info={"vai_tro": Roles.CU_DAN.value, "ho_ten": "Cư dân A1-01"})
        self.assertIsInstance(main_res.get_page("3.2_my_vehicles"), ResidentVehicleView)
        self.assertIsInstance(main_res.get_page("3.3_buy_slots"), ExtraSlotView)
        self.assertIsInstance(main_res.get_page("3.4_track_vehicle"), VehicleLookupView)
        main_res.close()

        mgr_win = ManagerWindow()
        self.assertIsInstance(mgr_win.vehicle_approval_view, VehicleApprovalView)
        mgr_win.close()

        adm_win = AdminWindow()
        self.assertTrue(adm_win.ro_vehicle_approval_view.read_only)
        adm_win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
