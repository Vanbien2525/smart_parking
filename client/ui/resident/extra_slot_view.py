"""Giao diện Mua thêm Chỗ đỗ Ô tô & Xem vị trí được cấp của Cư dân (3.3.1–3.3.5)."""

from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    SectionCard,
    StatCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
    style_table,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.constants import PricingGroups, RequestStatus
from shared.protocol import Actions


def _fmt_vnd(val: Any) -> str:
    try:
        return f"{int(round(float(val or 0))):,} đ".replace(",", ".")
    except (TypeError, ValueError):
        return "0 đ"


class ExtraSlotRequestDialog(QDialog):
    """Hộp thoại 3.3.1: Cư dân gửi yêu cầu mua thêm chỗ đỗ ô tô."""

    def __init__(self, extra_fee: float = 0.0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gửi yêu cầu mua thêm chỗ đỗ ô tô (3.3.1)")
        self.setMinimumWidth(460)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        lbl_info = QLabel(
            f"Đơn giá áp dụng cho chỗ đỗ ô tô mua thêm: {_fmt_vnd(extra_fee)} / tháng "
            "(chờ Ban Quản Lý phê duyệt và cấp ô đỗ cụ thể)."
        )
        lbl_info.setWordWrap(True)
        lbl_info.setStyleSheet(
            f"background-color: {Colors.ROYAL_BLUE_SOFT}; color: {Colors.ROYAL_BLUE}; "
            f"border: 1px solid #BFDBFE; border-radius: 8px; padding: 8px 12px; font-size: 12.5px;"
        )
        root.addWidget(lbl_info)

        form = QFormLayout()
        form.setSpacing(10)
        self.txt_preference = QLineEdit("Hầm B1 - Ô tô Cư dân")
        self.txt_preference.setPlaceholderText("VD: Hầm B1 gần thang máy tòa A")
        self.txt_reason = QTextEdit()
        self.txt_reason.setPlaceholderText("VD: Gia đình có thêm xe ô tô thứ 2 phục vụ đi làm...")
        self.txt_reason.setMaximumHeight(100)

        form.addRow("Nhu cầu vị trí (*):", self.txt_preference)
        form.addRow("Lý do mua thêm (*):", self.txt_reason)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Hủy")
        set_button_variant(btn_cancel, "default", "sm")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Gửi yêu cầu mua thêm")
        set_button_variant(btn_ok, "primary", "md")
        btn_ok.clicked.connect(self._on_accept)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        root.addLayout(row)

    def _on_accept(self) -> None:
        if not self.txt_preference.text().strip():
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập nhu cầu vị trí.")
            return
        if not self.txt_reason.toPlainText().strip():
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập lý do mua thêm chỗ đỗ.")
            return
        self.accept()

    def get_payload(self) -> Dict[str, Any]:
        return {
            "nhu_cau_vi_tri": self.txt_preference.text().strip(),
            "ly_do_mua_them": self.txt_reason.toPlainText().strip(),
        }


class ExtraSlotView(QWidget):
    """Màn hình 3.3.1–3.3.5: Quản lý mua thêm chỗ đỗ ô tô và theo dõi vị trí được cấp."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._quota: Dict[str, Any] = {}
        self._pricing: Dict[str, float] = {}
        self._allocations: List[Dict[str, Any]] = []
        self._requests: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Mua thêm Chỗ đỗ Ô tô & Vị trí được cấp (3.3)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        lbl_sub = QLabel(
            "Xem danh sách chỗ đỗ ô tô của căn hộ, gửi yêu cầu mua thêm chỗ và theo dõi phê duyệt"
        )
        lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        header.addLayout(title_col, 1)

        self.btn_refresh = QPushButton("Làm mới")
        set_button_variant(self.btn_refresh, "default", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)

        self.btn_submit_extra = QPushButton("+ Gửi yêu cầu mua thêm chỗ (3.3.1)")
        set_button_variant(self.btn_submit_extra, "primary", "md")
        self.btn_submit_extra.clicked.connect(self._open_request_dialog)

        header.addWidget(self.btn_refresh)
        header.addWidget(self.btn_submit_extra)
        root.addLayout(header)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self.kpi_default = StatCard("Suất Ô tô mặc định", "1", "Tiêu chuẩn căn hộ", Colors.ROYAL_BLUE)
        self.kpi_extra = StatCard("Suất mua thêm", "0", "Đã được cấp", Colors.INDIGO)
        self.kpi_total = StatCard("Tổng suất Ô tô", "1", "Có thể đăng ký", Colors.SUCCESS)
        self.kpi_fee = StatCard("Đơn giá suất mua thêm", "0 đ", "Theo tháng", Colors.WARNING)
        for card in (self.kpi_default, self.kpi_extra, self.kpi_total, self.kpi_fee):
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        self.pending_banner = QFrame()
        self.pending_banner.setVisible(False)
        self.pending_banner.setStyleSheet(
            f"QFrame {{ background-color: {Colors.WARNING_BG}; border: 1px solid {Colors.WARNING_BORDER}; "
            f"border-radius: 10px; }}"
        )
        pb_lay = QHBoxLayout(self.pending_banner)
        pb_lay.setContentsMargins(14, 10, 14, 10)
        self.lbl_pending_banner = QLabel("")
        self.lbl_pending_banner.setWordWrap(True)
        self.lbl_pending_banner.setStyleSheet(
            f"font-weight: 600; color: {Colors.WARNING_TEXT}; border: none;"
        )
        pb_lay.addWidget(self.lbl_pending_banner)
        root.addWidget(self.pending_banner)

        self.tabs = QTabWidget()

        # Tab 1: Danh sách chỗ đỗ đã cấp (3.3.3 & 3.3.4)
        tab_alloc = QWidget()
        lay_a = QVBoxLayout(tab_alloc)
        lay_a.setContentsMargins(0, 10, 0, 0)
        card_a = SectionCard(
            title="Danh sách Chỗ đỗ & Vị trí đã được cấp (3.3.3 & 3.3.4)",
            subtitle="Bao gồm suất mặc định xe 1 và các ô đỗ mua thêm của căn hộ",
        )
        self.tbl_allocations = QTableWidget(0, 7)
        self.tbl_allocations.setHorizontalHeaderLabels(
            ["Mã PB", "Loại suất", "Mã ô đỗ", "Khu vực", "Xe đang gắn", "Phí tháng", "Trạng thái"]
        )
        style_table(self.tbl_allocations)
        card_a.body_layout.addWidget(self.tbl_allocations, 1)
        lay_a.addWidget(card_a, 1)
        self.tabs.addTab(tab_alloc, "Chỗ đỗ được cấp (3.3.3–3.3.4)")

        # Tab 2: Trạng thái & Lịch sử yêu cầu mua thêm (3.3.2 & 3.3.5)
        tab_hist = QWidget()
        lay_h = QVBoxLayout(tab_hist)
        lay_h.setContentsMargins(0, 10, 0, 0)
        card_h = SectionCard(
            title="Trạng thái & Lịch sử Yêu cầu Mua thêm Chỗ đỗ (3.3.2 & 3.3.5)",
            subtitle="Theo dõi kết quả duyệt, ô đỗ được cấp hoặc lý do từ chối",
        )
        self.tbl_requests = QTableWidget(0, 7)
        self.tbl_requests.setHorizontalHeaderLabels(
            ["Mã YC", "Nhu cầu vị trí", "Lý do mua thêm", "Ngày gửi", "Ô được cấp", "Trạng thái", "Phản hồi"]
        )
        style_table(self.tbl_requests)
        card_h.body_layout.addWidget(self.tbl_requests, 1)
        lay_h.addWidget(card_h, 1)
        self.tabs.addTab(tab_hist, "Lịch sử Yêu cầu (3.3.2 & 3.3.5)")

        root.addWidget(self.tabs, 1)

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(Actions.SLOT_REQUEST_RESIDENT_DATA, {})
        if resp.get("ok") and isinstance(resp.get("data"), dict):
            self.set_data(resp["data"])

    def set_data(self, data: Dict[str, Any]) -> None:
        self._quota = dict(data.get("quota") or {})
        self._pricing = dict(data.get("pricing") or {})
        self._allocations = list(data.get("allocations") or [])
        self._requests = list(data.get("requests") or [])

        extra_fee = float(self._pricing.get(PricingGroups.CHO_OTO_MUA_THEM.value) or 0.0)
        self.kpi_default.set_value(str(self._quota.get("so_cho_oto_mac_dinh", 1)))
        self.kpi_extra.set_value(str(self._quota.get("so_cho_oto_mua_them", 0)))
        self.kpi_total.set_value(str(self._quota.get("tong_suat_oto", 1)))
        self.kpi_fee.set_value(_fmt_vnd(extra_fee))

        pending_reqs = [r for r in self._requests if r.get("trang_thai_duyet") == RequestStatus.CHO_DUYET.value]
        if pending_reqs:
            first = pending_reqs[0]
            self.lbl_pending_banner.setText(
                f"Đang có yêu cầu mua thêm chỗ #{first.get('ma_yeu_cau_cho')} chờ Ban Quản Lý duyệt "
                f"(Nhu cầu: {first.get('nhu_cau_vi_tri', '-')})."
            )
            self.pending_banner.setVisible(True)
        else:
            self.pending_banner.setVisible(False)

        self.tbl_allocations.setRowCount(len(self._allocations))
        for idx, a in enumerate(self._allocations):
            self.tbl_allocations.setItem(idx, 0, QTableWidgetItem(f"#{a.get('ma_phan_bo', '')}"))
            kind_lbl = "Mua thêm" if a.get("loai_cho_cap") == "cho_mua_them" else "Mặc định xe 1"
            self.tbl_allocations.setItem(idx, 1, QTableWidgetItem(kind_lbl))
            self.tbl_allocations.setItem(idx, 2, QTableWidgetItem(str(a.get("ma_vi_tri") or "")))
            self.tbl_allocations.setItem(idx, 3, QTableWidgetItem(str(a.get("ten_khu_vuc") or a.get("ma_khu_vuc") or "")))
            plate = str(a.get("bien_so_xe") or "Chưa gắn xe")
            self.tbl_allocations.setItem(idx, 4, QTableWidgetItem(plate))
            self.tbl_allocations.setItem(idx, 5, QTableWidgetItem(_fmt_vnd(a.get("muc_phi_thang"))))
            active = a.get("trang_thai_su_dung") == "dang_hieu_luc"
            self.tbl_allocations.setCellWidget(
                idx,
                6,
                StatusBadge("Đang hiệu lực" if active else "Đã thu hồi", "success" if active else "neutral"),
            )

        self.tbl_requests.setRowCount(len(self._requests))
        for idx, r in enumerate(self._requests):
            self.tbl_requests.setItem(idx, 0, QTableWidgetItem(f"#{r.get('ma_yeu_cau_cho', '')}"))
            self.tbl_requests.setItem(idx, 1, QTableWidgetItem(str(r.get("nhu_cau_vi_tri") or "")))
            self.tbl_requests.setItem(idx, 2, QTableWidgetItem(str(r.get("ly_do_mua_them") or "")))
            self.tbl_requests.setItem(idx, 3, QTableWidgetItem(str(r.get("ngay_gui_yeu_cau") or "")))
            self.tbl_requests.setItem(idx, 4, QTableWidgetItem(str(r.get("ma_vi_tri_duoc_duyet") or "-")))
            st = str(r.get("trang_thai_duyet") or "")
            st_lbl, st_var = {
                "cho_duyet": ("Chờ duyệt", "warning"),
                "da_duyet": ("Đã duyệt", "success"),
                "tu_choi": ("Từ chối", "danger"),
                "da_huy": ("Đã hủy", "neutral"),
            }.get(st, (st, "neutral"))
            self.tbl_requests.setCellWidget(idx, 5, StatusBadge(st_lbl, st_var))
            self.tbl_requests.setItem(idx, 6, QTableWidgetItem(str(r.get("ly_do_tu_choi") or "-")))

    def _open_request_dialog(self) -> None:
        extra_fee = float(self._pricing.get(PricingGroups.CHO_OTO_MUA_THEM.value) or 0.0)
        dlg = ExtraSlotRequestDialog(extra_fee=extra_fee, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self.socket_client is None:
            return
        resp = self.socket_client.call(Actions.SLOT_REQUEST_SUBMIT, dlg.get_payload())
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error") or "Gửi yêu cầu thất bại."), variant="danger")
            return
        ToastNotification.show_toast(self.window(), "Đã gửi yêu cầu mua thêm chỗ đỗ ô tô.", variant="success")
        self.refresh_data()
        self.tabs.setCurrentIndex(1)
