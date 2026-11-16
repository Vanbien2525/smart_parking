"""Giao diện Quản lý Duyệt Phương tiện (1.4.1–1.4.4) & Mua thêm / Thu hồi Chỗ đỗ (1.4.15–1.4.19).

Hỗ trợ `read_only=True` kèm `ReadOnlyBanner` cho Admin (4.6).
"""

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.manager.slot_picker_dialog import SlotPickerDialog, ZoomableCavetDialog
from client.ui.theme.components import (
    PlateBadge,
    ReadOnlyBanner,
    SectionCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
    style_table,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.constants import RequestStatus, SlotStatus, VehicleRequestTypes
from shared.protocol import Actions

_REQ_TYPE_LABELS = {
    "dang_ky_moi": "Đăng ký mới",
    "chinh_sua_thong_tin": "Chỉnh sửa thông tin",
    "ngung_dang_ky": "Ngừng đăng ký",
}

_STATUS_LABELS = {
    "cho_duyet": ("Chờ duyệt", "warning"),
    "da_duyet": ("Đã duyệt", "success"),
    "tu_choi": ("Từ chối", "danger"),
    "da_huy": ("Đã hủy", "neutral"),
}


class VehicleApprovalView(QWidget):
    """Màn hình Quản lý duyệt phương tiện & phân bổ/thu hồi chỗ đỗ (1.4.1–1.4.4, 1.4.15–1.4.19)."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        read_only: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.read_only = read_only

        self._vehicle_reqs: List[Dict[str, Any]] = []
        self._extra_reqs: List[Dict[str, Any]] = []
        self._allocations: List[Dict[str, Any]] = []
        self._selected_vehicle_req: Optional[Dict[str, Any]] = None

        self._build_ui()

    def _mark_mutation(self, btn: QPushButton) -> QPushButton:
        btn.setProperty("is_mutation", True)
        if self.read_only:
            btn.setEnabled(False)
            btn.setVisible(False)
        return btn

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        if self.read_only:
            root.addWidget(ReadOnlyBanner(self))

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Duyệt Phương tiện & Phân bổ / Thu hồi Chỗ đỗ (1.4)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 20px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        lbl_sub = QLabel(
            "Phê duyệt đăng ký mới kèm cà-vẹt, gán ô đỗ cố định và quản lý suất mua thêm ô tô"
        )
        lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        header.addLayout(title_col, 1)

        self.btn_refresh_all = QPushButton("Làm mới dữ liệu")
        set_button_variant(self.btn_refresh_all, "default", "sm")
        self.btn_refresh_all.clicked.connect(self.refresh_data)
        header.addWidget(self.btn_refresh_all)
        root.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_vehicle_requests_tab(), "Duyệt Phương tiện (1.4.1–1.4.4)")
        self.tabs.addTab(self._build_extra_slots_tab(), "Mua thêm & Thu hồi Chỗ đỗ (1.4.15–1.4.19)")
        root.addWidget(self.tabs, 1)

    # --------------------------------------------------------------------------
    # TAB 1: DUYỆT ĐĂNG KÝ / CHỈNH SỬA / NGỪNG PHƯƠNG TIỆN (1.4.1–1.4.4)
    # --------------------------------------------------------------------------
    def _build_vehicle_requests_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(10)

        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)
        self.cbo_v_status = QComboBox()
        self.cbo_v_status.addItem("Chờ duyệt (1.4.1)", "cho_duyet")
        self.cbo_v_status.addItem("Đã duyệt (Lịch sử 1.4.4)", "da_duyet")
        self.cbo_v_status.addItem("Từ chối (Lịch sử 1.4.4)", "tu_choi")
        self.cbo_v_status.addItem("Tất cả trạng thái", "all")

        self.cbo_v_type = QComboBox()
        self.cbo_v_type.addItem("Tất cả loại yêu cầu", "all")
        self.cbo_v_type.addItem("Đăng ký mới", "dang_ky_moi")
        self.cbo_v_type.addItem("Chỉnh sửa thông tin", "chinh_sua_thong_tin")
        self.cbo_v_type.addItem("Ngừng đăng ký", "ngung_dang_ky")

        self.cbo_v_vehicle_type = QComboBox()
        self.cbo_v_vehicle_type.addItem("Ô tô & Xe máy", "ALL")
        self.cbo_v_vehicle_type.addItem("Ô tô (O_TO)", "O_TO")
        self.cbo_v_vehicle_type.addItem("Xe máy (XE_MAY)", "XE_MAY")

        self.txt_v_kw = QLineEdit()
        self.txt_v_kw.setPlaceholderText("Tìm biển số, tên cư dân, căn hộ...")

        self.btn_v_filter = QPushButton("Lọc")
        set_button_variant(self.btn_v_filter, "primary", "sm")
        self.btn_v_filter.clicked.connect(self.refresh_vehicle_requests)

        for w in (self.cbo_v_status, self.cbo_v_type, self.cbo_v_vehicle_type):
            w.currentIndexChanged.connect(self.refresh_vehicle_requests)

        filter_bar.addWidget(self.cbo_v_status)
        filter_bar.addWidget(self.cbo_v_type)
        filter_bar.addWidget(self.cbo_v_vehicle_type)
        filter_bar.addWidget(self.txt_v_kw, 1)
        filter_bar.addWidget(self.btn_v_filter)
        lay.addLayout(filter_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_card = SectionCard(
            title="Danh sách Yêu cầu Phương tiện",
            subtitle="Chọn một dòng để đối chiếu hạn mức căn hộ & ảnh cà-vẹt",
        )
        self.tbl_vehicle_reqs = QTableWidget(0, 7)
        self.tbl_vehicle_reqs.setHorizontalHeaderLabels(
            ["Mã YC", "Căn hộ", "Cư dân", "Biển số", "Loại xe", "Loại YC", "Trạng thái"]
        )
        style_table(self.tbl_vehicle_reqs)
        self.tbl_vehicle_reqs.itemSelectionChanged.connect(self._on_vehicle_req_selected)
        left_card.body_layout.addWidget(self.tbl_vehicle_reqs, 1)
        splitter.addWidget(left_card)

        detail_card = SectionCard(
            title="Chi tiết Hồ sơ & Thao tác Duyệt",
            subtitle="Đối chiếu cà-vẹt và gán vị trí cố định / khu vực đỗ",
        )
        detail_card.setMinimumWidth(360)
        self.plate_badge = PlateBadge("---")
        detail_card.body_layout.addWidget(self.plate_badge, 0, Qt.AlignmentFlag.AlignLeft)

        form = QFormLayout()
        form.setSpacing(6)
        self.lbl_det_resident = QLabel("-")
        self.lbl_det_quota = QLabel("-")
        self.lbl_det_vehicle = QLabel("-")
        self.lbl_det_type = QLabel("-")
        self.lbl_det_note = QLabel("-")
        self.lbl_det_note.setWordWrap(True)
        self.lbl_det_feedback = QLabel("-")
        self.lbl_det_feedback.setWordWrap(True)

        form.addRow("Cư dân / Căn hộ:", self.lbl_det_resident)
        form.addRow("Hạn mức Ô tô:", self.lbl_det_quota)
        form.addRow("Thông tin xe:", self.lbl_det_vehicle)
        form.addRow("Loại yêu cầu:", self.lbl_det_type)
        form.addRow("Chi tiết / Lý do:", self.lbl_det_note)
        form.addRow("Phản hồi duyệt:", self.lbl_det_feedback)
        detail_card.body_layout.addLayout(form)

        self.btn_view_cavet = QPushButton("Xem ảnh Cà-vẹt phóng to")
        set_button_variant(self.btn_view_cavet, "default", "sm")
        self.btn_view_cavet.setEnabled(False)
        self.btn_view_cavet.clicked.connect(self._view_selected_cavet)
        detail_card.body_layout.addWidget(self.btn_view_cavet)

        detail_card.body_layout.addStretch(1)

        act_row = QHBoxLayout()
        act_row.setSpacing(8)
        self.btn_approve_v = self._mark_mutation(QPushButton("Duyệt & Gán chỗ (1.4.2)"))
        set_button_variant(self.btn_approve_v, "success", "md")
        self.btn_approve_v.clicked.connect(self._approve_vehicle_req)

        self.btn_reject_v = self._mark_mutation(QPushButton("Từ chối (1.4.3)"))
        set_button_variant(self.btn_reject_v, "danger", "md")
        self.btn_reject_v.clicked.connect(self._reject_vehicle_req)

        act_row.addWidget(self.btn_approve_v, 1)
        act_row.addWidget(self.btn_reject_v, 1)
        detail_card.body_layout.addLayout(act_row)

        splitter.addWidget(detail_card)
        splitter.setSizes([640, 380])
        lay.addWidget(splitter, 1)
        return host

    # --------------------------------------------------------------------------
    # TAB 2: DUYỆT MUA THÊM & THU HỒI CHỖ ĐỖ (1.4.15–1.4.19)
    # --------------------------------------------------------------------------
    def _build_extra_slots_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(10)

        self.extra_sub_tabs = QTabWidget()

        # Sub-tab 2A: Yêu cầu mua thêm chỗ (1.4.15-1.4.17)
        tab_reqs = QWidget()
        lay_r = QVBoxLayout(tab_reqs)
        lay_r.setContentsMargins(0, 8, 0, 0)
        card_r = SectionCard(
            title="Yêu cầu Mua thêm Chỗ đỗ Ô tô (1.4.15–1.4.17)",
            subtitle="Duyệt và chọn ô đỗ ô tô trống để cấp cho căn hộ",
        )
        bar_r = QHBoxLayout()
        self.cbo_es_status = QComboBox()
        self.cbo_es_status.addItem("Chờ duyệt (1.4.15)", "cho_duyet")
        self.cbo_es_status.addItem("Đã duyệt", "da_duyet")
        self.cbo_es_status.addItem("Từ chối", "tu_choi")
        self.cbo_es_status.addItem("Tất cả", "all")
        self.cbo_es_status.currentIndexChanged.connect(self.refresh_extra_slots)

        self.txt_es_kw = QLineEdit()
        self.txt_es_kw.setPlaceholderText("Tìm theo mã căn hộ, cư dân...")
        btn_es_filter = QPushButton("Tìm / Làm mới")
        set_button_variant(btn_es_filter, "default", "sm")
        btn_es_filter.clicked.connect(self.refresh_extra_slots)

        self.btn_es_approve = self._mark_mutation(QPushButton("Duyệt & Cấp ô đỗ (1.4.16)"))
        set_button_variant(self.btn_es_approve, "success", "sm")
        self.btn_es_approve.clicked.connect(self._approve_extra_slot_req)

        self.btn_es_reject = self._mark_mutation(QPushButton("Từ chối (1.4.17)"))
        set_button_variant(self.btn_es_reject, "danger", "sm")
        self.btn_es_reject.clicked.connect(self._reject_extra_slot_req)

        bar_r.addWidget(self.cbo_es_status)
        bar_r.addWidget(self.txt_es_kw, 1)
        bar_r.addWidget(btn_es_filter)
        bar_r.addWidget(self.btn_es_approve)
        bar_r.addWidget(self.btn_es_reject)
        card_r.body_layout.addLayout(bar_r)

        self.tbl_extra_reqs = QTableWidget(0, 7)
        self.tbl_extra_reqs.setHorizontalHeaderLabels(
            ["Mã YC", "Căn hộ", "Cư dân", "Nhu cầu vị trí", "Lý do mua thêm", "Ô được cấp", "Trạng thái"]
        )
        style_table(self.tbl_extra_reqs)
        card_r.body_layout.addWidget(self.tbl_extra_reqs, 1)
        lay_r.addWidget(card_r, 1)
        self.extra_sub_tabs.addTab(tab_reqs, "Yêu cầu Mua thêm Chỗ (1.4.15–1.4.17)")

        # Sub-tab 2B: Danh sách căn hộ đã cấp chỗ & Thu hồi (1.4.18-1.4.19)
        tab_alloc = QWidget()
        lay_a = QVBoxLayout(tab_alloc)
        lay_a.setContentsMargins(0, 8, 0, 0)
        card_a = SectionCard(
            title="Danh sách Căn hộ đã Cấp chỗ & Thu hồi (1.4.18–1.4.19)",
            subtitle="Chỗ đỗ đang có xe (dang_co_xe) bị khóa thao tác thu hồi để đảm bảo an toàn",
        )
        bar_a = QHBoxLayout()
        btn_a_refresh = QPushButton("Làm mới danh sách")
        set_button_variant(btn_a_refresh, "default", "sm")
        btn_a_refresh.clicked.connect(self.refresh_extra_slots)

        self.btn_revoke_alloc = self._mark_mutation(QPushButton("Thu hồi chỗ đỗ (1.4.19)"))
        set_button_variant(self.btn_revoke_alloc, "danger", "sm")
        self.btn_revoke_alloc.clicked.connect(self._revoke_allocation)

        bar_a.addStretch(1)
        bar_a.addWidget(btn_a_refresh)
        bar_a.addWidget(self.btn_revoke_alloc)
        card_a.body_layout.addLayout(bar_a)

        self.tbl_allocations = QTableWidget(0, 7)
        self.tbl_allocations.setHorizontalHeaderLabels(
            ["Mã PB", "Căn hộ", "Ô đỗ", "Khu vực", "Loại suất", "Xe đang gắn", "Trạng thái ô"]
        )
        style_table(self.tbl_allocations)
        self.tbl_allocations.itemSelectionChanged.connect(self._sync_revoke_button_state)
        card_a.body_layout.addWidget(self.tbl_allocations, 1)
        lay_a.addWidget(card_a, 1)
        self.extra_sub_tabs.addTab(tab_alloc, "Căn hộ đã Mua thêm & Thu hồi (1.4.18–1.4.19)")

        lay.addWidget(self.extra_sub_tabs, 1)
        return host

    # --------------------------------------------------------------------------
    # NẠP & RENDER DỮ LIỆU
    # --------------------------------------------------------------------------
    def refresh_data(self) -> None:
        self.refresh_vehicle_requests()
        self.refresh_extra_slots()

    def refresh_vehicle_requests(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.VEHICLE_MANAGER_LIST_REQUESTS,
            {
                "trang_thai_duyet": self.cbo_v_status.currentData(),
                "loai_yeu_cau": self.cbo_v_type.currentData(),
                "ma_loai_xe": self.cbo_v_vehicle_type.currentData(),
                "keyword": self.txt_v_kw.text().strip(),
            },
        )
        if resp.get("ok") and isinstance(resp.get("data"), dict):
            self.set_vehicle_requests(resp["data"].get("items") or [])

    def refresh_extra_slots(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.SLOT_REQUEST_MANAGER_LIST,
            {
                "trang_thai_duyet": self.cbo_es_status.currentData(),
                "keyword": self.txt_es_kw.text().strip(),
            },
        )
        if resp.get("ok") and isinstance(resp.get("data"), dict):
            data = resp["data"]
            self.set_extra_slots_data(
                requests=data.get("requests") or [],
                allocations=data.get("allocations") or [],
            )

    def set_vehicle_requests(self, items: List[Dict[str, Any]]) -> None:
        self._vehicle_reqs = list(items)
        self.tbl_vehicle_reqs.setRowCount(len(self._vehicle_reqs))
        for idx, r in enumerate(self._vehicle_reqs):
            self.tbl_vehicle_reqs.setItem(idx, 0, QTableWidgetItem(f"#{r.get('ma_yeu_cau_xe', '')}"))
            self.tbl_vehicle_reqs.setItem(idx, 1, QTableWidgetItem(str(r.get("ma_can_ho") or "")))
            self.tbl_vehicle_reqs.setItem(idx, 2, QTableWidgetItem(str(r.get("ten_cu_dan") or "")))
            self.tbl_vehicle_reqs.setItem(idx, 3, QTableWidgetItem(str(r.get("bien_so_xe") or "")))
            self.tbl_vehicle_reqs.setItem(idx, 4, QTableWidgetItem(str(r.get("ma_loai_xe") or "")))
            req_t = _REQ_TYPE_LABELS.get(str(r.get("loai_yeu_cau") or ""), str(r.get("loai_yeu_cau") or ""))
            self.tbl_vehicle_reqs.setItem(idx, 5, QTableWidgetItem(req_t))
            st_text, st_var = _STATUS_LABELS.get(str(r.get("trang_thai_duyet") or ""), ("-", "neutral"))
            self.tbl_vehicle_reqs.setCellWidget(idx, 6, StatusBadge(st_text, st_var))
        if self._vehicle_reqs:
            self.tbl_vehicle_reqs.selectRow(0)
        else:
            self._selected_vehicle_req = None
            self._render_vehicle_detail(None)

    def set_extra_slots_data(
        self,
        requests: List[Dict[str, Any]],
        allocations: List[Dict[str, Any]],
    ) -> None:
        self._extra_reqs = list(requests)
        self.tbl_extra_reqs.setRowCount(len(self._extra_reqs))
        for idx, r in enumerate(self._extra_reqs):
            self.tbl_extra_reqs.setItem(idx, 0, QTableWidgetItem(f"#{r.get('ma_yeu_cau_cho', '')}"))
            self.tbl_extra_reqs.setItem(idx, 1, QTableWidgetItem(str(r.get("ma_can_ho") or "")))
            self.tbl_extra_reqs.setItem(idx, 2, QTableWidgetItem(str(r.get("ten_cu_dan") or "")))
            self.tbl_extra_reqs.setItem(idx, 3, QTableWidgetItem(str(r.get("nhu_cau_vi_tri") or "")))
            self.tbl_extra_reqs.setItem(idx, 4, QTableWidgetItem(str(r.get("ly_do_mua_them") or "")))
            self.tbl_extra_reqs.setItem(idx, 5, QTableWidgetItem(str(r.get("ma_vi_tri_duoc_duyet") or "-")))
            st_text, st_var = _STATUS_LABELS.get(str(r.get("trang_thai_duyet") or ""), ("-", "neutral"))
            self.tbl_extra_reqs.setCellWidget(idx, 6, StatusBadge(st_text, st_var))

        self._allocations = list(allocations)
        self.tbl_allocations.setRowCount(len(self._allocations))
        for idx, a in enumerate(self._allocations):
            self.tbl_allocations.setItem(idx, 0, QTableWidgetItem(f"#{a.get('ma_phan_bo', '')}"))
            self.tbl_allocations.setItem(idx, 1, QTableWidgetItem(str(a.get("ma_can_ho") or "")))
            self.tbl_allocations.setItem(idx, 2, QTableWidgetItem(str(a.get("ma_vi_tri") or "")))
            self.tbl_allocations.setItem(idx, 3, QTableWidgetItem(str(a.get("ten_khu_vuc") or a.get("ma_khu_vuc") or "")))
            kind_lbl = "Mua thêm" if a.get("loai_cho_cap") == "cho_mua_them" else "Mặc định xe 1"
            self.tbl_allocations.setItem(idx, 4, QTableWidgetItem(kind_lbl))
            plate_txt = str(a.get("bien_so_xe") or "(Chưa gắn xe)")
            self.tbl_allocations.setItem(idx, 5, QTableWidgetItem(plate_txt))

            slot_st = str(a.get("trang_thai_vi_tri") or "trong")
            st_var = {"dang_co_xe": "danger", "da_phan_cho": "primary", "trong": "success"}.get(slot_st, "neutral")
            st_lbl = {"dang_co_xe": "Đang có xe", "da_phan_cho": "Đã phân chỗ", "trong": "Trống"}.get(slot_st, slot_st)
            self.tbl_allocations.setCellWidget(idx, 6, StatusBadge(st_lbl, st_var))
        self._sync_revoke_button_state()

    def _on_vehicle_req_selected(self) -> None:
        row = self.tbl_vehicle_reqs.currentRow()
        if row < 0 or row >= len(self._vehicle_reqs):
            self._selected_vehicle_req = None
            self._render_vehicle_detail(None)
            return
        self._selected_vehicle_req = self._vehicle_reqs[row]
        self._render_vehicle_detail(self._selected_vehicle_req)

    def _render_vehicle_detail(self, req: Optional[Dict[str, Any]]) -> None:
        if req is None:
            self.plate_badge.setText("---")
            for lbl in (
                self.lbl_det_resident,
                self.lbl_det_quota,
                self.lbl_det_vehicle,
                self.lbl_det_type,
                self.lbl_det_note,
                self.lbl_det_feedback,
            ):
                lbl.setText("-")
            self.btn_view_cavet.setEnabled(False)
            if not self.read_only:
                self.btn_approve_v.setEnabled(False)
                self.btn_reject_v.setEnabled(False)
            return

        self.plate_badge.setText(str(req.get("bien_so_xe") or "---"))
        self.lbl_det_resident.setText(
            f"{req.get('ten_cu_dan', '-')} · Căn hộ {req.get('ma_can_ho', '-')}"
        )
        self.lbl_det_quota.setText(
            f"Ô tô đang dùng: {req.get('so_oto_dang_hoat_dong', 0)}/{req.get('tong_suat_oto', 1)} suất "
            f"(Còn: {req.get('so_suat_oto_con_lai', 0)})"
        )
        self.lbl_det_vehicle.setText(
            f"{req.get('ten_hang_dong_xe', '-')} · Màu: {req.get('mau_xe') or '-'} · {req.get('ma_loai_xe', '')}"
        )
        loai_yc = str(req.get("loai_yeu_cau") or "")
        self.lbl_det_type.setText(_REQ_TYPE_LABELS.get(loai_yc, loai_yc))

        raw_note = str(req.get("noi_dung_thay_doi") or req.get("vi_tri_mong_muon") or "-")
        if raw_note.startswith("{"):
            try:
                parsed = json.loads(raw_note)
                raw_note = ", ".join(f"{k}: {v}" for k, v in parsed.items() if v)
            except json.JSONDecodeError:
                pass
        self.lbl_det_note.setText(raw_note)
        self.lbl_det_feedback.setText(str(req.get("ly_do_phan_hoi") or "-"))

        self.btn_view_cavet.setEnabled(bool(req.get("duong_dan_cavet") or req.get("ma_phuong_tien")))
        can_act = (not self.read_only) and (req.get("trang_thai_duyet") == RequestStatus.CHO_DUYET.value)
        self.btn_approve_v.setEnabled(can_act)
        self.btn_reject_v.setEnabled(can_act)

    def _sync_revoke_button_state(self) -> None:
        if self.read_only:
            return
        row = self.tbl_allocations.currentRow()
        if row < 0 or row >= len(self._allocations):
            self.btn_revoke_alloc.setEnabled(False)
            self.btn_revoke_alloc.setToolTip("Chọn một phân bổ chỗ đỗ để thu hồi.")
            return
        alloc = self._allocations[row]
        in_use = (
            alloc.get("trang_thai_vi_tri") == SlotStatus.DANG_CO_XE.value
            or bool(alloc.get("dang_trong_bai"))
        )
        self.btn_revoke_alloc.setEnabled(not in_use)
        if in_use:
            self.btn_revoke_alloc.setToolTip("Ô đỗ đang có xe bên trong, không thể thu hồi cho đến khi xe rời bãi.")
        else:
            self.btn_revoke_alloc.setToolTip("Thu hồi quyền sử dụng ô đỗ này và trả về trạng thái Trống.")

    # --------------------------------------------------------------------------
    # ACTIONS
    # --------------------------------------------------------------------------
    def _view_selected_cavet(self) -> None:
        req = self._selected_vehicle_req
        if not req:
            return
        dlg = ZoomableCavetDialog(
            title=f"Ảnh Cà-vẹt — {req.get('bien_so_xe', '')}",
            socket_client=self.socket_client,
            ma_phuong_tien=req.get("ma_phuong_tien"),
            duong_dan_cavet=str(req.get("duong_dan_cavet") or ""),
            parent=self,
        )
        dlg.exec()

    def _approve_vehicle_req(self) -> None:
        req = self._selected_vehicle_req
        if not req or self.socket_client is None:
            return

        payload: Dict[str, Any] = {"ma_yeu_cau_xe": req["ma_yeu_cau_xe"]}
        if req.get("loai_yeu_cau") == VehicleRequestTypes.DANG_KY_MOI.value:
            dlg = SlotPickerDialog(
                title="Duyệt Đăng ký Phương tiện & Phân bổ Chỗ đỗ (1.4.2)",
                ma_loai_xe=str(req.get("ma_loai_xe") or "O_TO"),
                ma_can_ho=str(req.get("ma_can_ho") or ""),
                summary_info=req,
                socket_client=self.socket_client,
                ma_phuong_tien=req.get("ma_phuong_tien"),
                show_cavet_panel=True,
                parent=self,
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if dlg.selected_slot_code():
                payload["ma_vi_tri_co_dinh"] = dlg.selected_slot_code()
            if dlg.selected_zone_code():
                payload["ma_khu_vuc_phan_cong"] = dlg.selected_zone_code()
        else:
            confirm = QMessageBox.question(
                self,
                "Xác nhận duyệt yêu cầu",
                f"Phê duyệt yêu cầu {_REQ_TYPE_LABELS.get(str(req.get('loai_yeu_cau')), '')} "
                f"cho xe {req.get('bien_so_xe')}?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        resp = self.socket_client.call(Actions.VEHICLE_APPROVE_REQUEST, payload)
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error") or "Duyệt thất bại."), variant="danger")
            return
        ToastNotification.show_toast(self.window(), "Đã duyệt yêu cầu phương tiện thành công.", variant="success")
        self.refresh_data()

    def _reject_vehicle_req(self) -> None:
        req = self._selected_vehicle_req
        if not req or self.socket_client is None:
            return
        reason, ok = QInputDialog.getText(
            self,
            "Từ chối yêu cầu (1.4.3)",
            f"Nhập lý do từ chối yêu cầu cho xe {req.get('bien_so_xe')}:",
        )
        if not ok:
            return
        if not reason.strip():
            ToastNotification.show_toast(self.window(), "Vui lòng nhập lý do từ chối.", variant="warning")
            return
        resp = self.socket_client.call(
            Actions.VEHICLE_REJECT_REQUEST,
            {"ma_yeu_cau_xe": req["ma_yeu_cau_xe"], "ly_do_phan_hoi": reason.strip()},
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error") or "Từ chối thất bại."), variant="danger")
            return
        ToastNotification.show_toast(self.window(), "Đã từ chối yêu cầu phương tiện.", variant="info")
        self.refresh_vehicle_requests()

    def _approve_extra_slot_req(self) -> None:
        if self.socket_client is None:
            return
        row = self.tbl_extra_reqs.currentRow()
        if row < 0 or row >= len(self._extra_reqs):
            return
        req = self._extra_reqs[row]
        if req.get("trang_thai_duyet") != RequestStatus.CHO_DUYET.value:
            ToastNotification.show_toast(self.window(), "Yêu cầu này đã được xử lý trước đó.", variant="warning")
            return

        dlg = SlotPickerDialog(
            title="Duyệt Mua thêm Chỗ đỗ Ô tô (1.4.16)",
            ma_loai_xe="O_TO",
            ma_can_ho=str(req.get("ma_can_ho") or ""),
            summary_info=req,
            socket_client=self.socket_client,
            show_cavet_panel=False,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        slot_code = dlg.selected_slot_code()
        if not slot_code:
            return

        resp = self.socket_client.call(
            Actions.SLOT_REQUEST_APPROVE,
            {"ma_yeu_cau_cho": req["ma_yeu_cau_cho"], "ma_vi_tri": slot_code},
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error") or "Duyệt thất bại."), variant="danger")
            return
        ToastNotification.show_toast(self.window(), f"Đã cấp ô {slot_code} cho căn hộ {req.get('ma_can_ho')}.", variant="success")
        self.refresh_extra_slots()

    def _reject_extra_slot_req(self) -> None:
        if self.socket_client is None:
            return
        row = self.tbl_extra_reqs.currentRow()
        if row < 0 or row >= len(self._extra_reqs):
            return
        req = self._extra_reqs[row]
        if req.get("trang_thai_duyet") != RequestStatus.CHO_DUYET.value:
            ToastNotification.show_toast(self.window(), "Yêu cầu này đã được xử lý trước đó.", variant="warning")
            return
        reason, ok = QInputDialog.getText(
            self,
            "Từ chối mua thêm chỗ (1.4.17)",
            f"Nhập lý do từ chối yêu cầu của căn hộ {req.get('ma_can_ho')}:",
        )
        if not ok:
            return
        if not reason.strip():
            ToastNotification.show_toast(self.window(), "Vui lòng nhập lý do từ chối.", variant="warning")
            return
        resp = self.socket_client.call(
            Actions.SLOT_REQUEST_REJECT,
            {"ma_yeu_cau_cho": req["ma_yeu_cau_cho"], "ly_do_tu_choi": reason.strip()},
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error") or "Từ chối thất bại."), variant="danger")
            return
        ToastNotification.show_toast(self.window(), "Đã từ chối yêu cầu mua thêm chỗ.", variant="info")
        self.refresh_extra_slots()

    def _revoke_allocation(self) -> None:
        if self.socket_client is None:
            return
        row = self.tbl_allocations.currentRow()
        if row < 0 or row >= len(self._allocations):
            return
        alloc = self._allocations[row]
        reason, ok = QInputDialog.getText(
            self,
            "Thu hồi chỗ đỗ (1.4.19)",
            f"Nhập lý do thu hồi ô {alloc.get('ma_vi_tri')} của căn hộ {alloc.get('ma_can_ho')}:",
        )
        if not ok:
            return
        if not reason.strip():
            ToastNotification.show_toast(self.window(), "Vui lòng nhập lý do thu hồi.", variant="warning")
            return
        resp = self.socket_client.call(
            Actions.ALLOCATION_REVOKE,
            {"ma_phan_bo": alloc["ma_phan_bo"], "ly_do_thu_hoi": reason.strip()},
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error") or "Thu hồi thất bại."), variant="danger")
            return
        ToastNotification.show_toast(self.window(), f"Đã thu hồi ô {alloc.get('ma_vi_tri')}.", variant="success")
        self.refresh_extra_slots()
