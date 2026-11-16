"""Màn hình 2.4 Quản lý Ca Trực & Tổng Doanh Thu Theo Ca Làm (Thành viên 5: Ny)."""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
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
from shared.constants import PaymentMethods, VehicleTypes
from shared.protocol import Actions


class ShiftRevenueView(QWidget):
    """Màn hình 2.4: Theo dõi doanh thu theo ca trực, chi tiết giao dịch & chốt ca."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._current_shift: Dict[str, Any] = {}
        self._selected_shift: Dict[str, Any] = {}
        self._shift_history: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # Header ca trực
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Quản lý Ca Trực & Doanh Thu Theo Ca (2.4)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        self.lbl_shift_meta = QLabel("Chưa tải thông tin ca làm việc...")
        self.lbl_shift_meta.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_shift_meta)
        header.addLayout(title_col, 1)

        self.badge_shift_status = StatusBadge("ĐANG MỞ", "success")
        header.addWidget(self.badge_shift_status)

        self.btn_refresh = QPushButton("Làm mới")
        set_button_variant(self.btn_refresh, "teal", "sm")
        self.btn_refresh.clicked.connect(lambda: self.refresh_data())
        header.addWidget(self.btn_refresh)

        self.btn_close_shift = QPushButton("Chốt Ca & Kết Thúc Ca Trực")
        set_button_variant(self.btn_close_shift, "danger", "sm")
        self.btn_close_shift.clicked.connect(self._on_close_shift_clicked)
        header.addWidget(self.btn_close_shift)
        root.addLayout(header)

        # 4 Thẻ KPI
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self.card_total = StatCard("Tổng Doanh Thu Ca (VNĐ)", "0 đ", "Tiền thu vãng lai trong ca", Colors.ROYAL_BLUE)
        self.card_cash = StatCard("Thu Tiền Mặt (VNĐ)", "0 đ", "Thanh toán trực tiếp tại chốt", Colors.EMERALD_TEAL)
        self.card_qr = StatCard("Thu Chuyển Khoản QR (VNĐ)", "0 đ", "Quét mã QR tại cổng ra", Colors.INDIGO)
        self.card_turns = StatCard("Lượt Xe Vãng Lai Đã Thu", "0", "0 Ô tô · 0 Xe máy · 0 Cư dân", Colors.WARNING)
        for card in (self.card_total, self.card_cash, self.card_qr, self.card_turns):
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(10)

        # Bảng trên: Chi tiết giao dịch trong ca đang chọn
        tx_card = SectionCard(
            title="Chi tiết giao dịch thu phí vãng lai trong ca",
            subtitle="Hiển thị các lượt xe vãng lai đã thu tiền và mở cổng ra",
        )
        self.tbl_transactions = QTableWidget(0, 6)
        self.tbl_transactions.setHorizontalHeaderLabels(
            ["Mã tham chiếu", "Thời gian ra", "Biển số xe", "Loại xe", "Phương thức", "Số tiền thu"]
        )
        style_table(self.tbl_transactions)
        tx_card.body_layout.addWidget(self.tbl_transactions, 1)
        splitter.addWidget(tx_card)

        # Bảng dưới: Lịch sử các ca trực của nhân viên
        hist_card = SectionCard(
            title="Lịch sử các ca làm việc của nhân viên",
            subtitle="Chọn một ca để xem lại bảng kê chi tiết giao dịch của ca đó",
        )
        self.tbl_shifts = QTableWidget(0, 7)
        self.tbl_shifts.setHorizontalHeaderLabels(
            ["Mã ca", "Tên ca", "Ngày", "Giờ bắt đầu", "Giờ kết thúc", "Trạng thái", "Tổng doanh thu"]
        )
        style_table(self.tbl_shifts)
        self.tbl_shifts.itemSelectionChanged.connect(self._on_shift_selected)
        hist_card.body_layout.addWidget(self.tbl_shifts, 1)
        splitter.addWidget(hist_card)

        splitter.setSizes([340, 220])
        root.addWidget(splitter, 1)

    def refresh_data(self, ma_ca_lam_viec: Optional[str] = None) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            self.lbl_shift_meta.setText("Chưa kết nối tới Server.")
            return
        payload = {"ma_ca_lam_viec": ma_ca_lam_viec} if ma_ca_lam_viec else {}
        resp = self.socket_client.call(Actions.SHIFT_GET_REVENUE, payload)
        if not resp.get("ok"):
            ToastNotification.show_toast(
                self,
                str(resp.get("error") or "Không thể tải doanh thu ca."),
                variant="danger",
            )
            return
        self.apply_shift_data(resp.get("data") or {})

    def apply_shift_data(self, data: Dict[str, Any]) -> None:
        self._current_shift = dict(data.get("current_shift") or {})
        self._selected_shift = dict(data.get("selected_shift") or self._current_shift)
        self._shift_history = list(data.get("shift_history") or [])
        kpi = data.get("kpi") or {}
        transactions = list(data.get("transactions") or [])

        sel = self._selected_shift
        status = str(sel.get("trang_thai_ca") or "dang_mo")
        is_open = status == "dang_mo"
        self.badge_shift_status.setText("ĐANG MỞ" if is_open else "ĐÃ CHỐT CA")
        self.badge_shift_status.set_variant("success" if is_open else "neutral")
        self.btn_close_shift.setEnabled(bool(self._current_shift.get("trang_thai_ca") == "dang_mo"))

        self.lbl_shift_meta.setText(
            f"Nhân viên: {sel.get('ho_ten', '-')} · {sel.get('ten_ca', '-')} "
            f"({sel.get('ma_ca_lam_viec', '-')}) · Bắt đầu: {sel.get('gio_bat_dau', '-')}"
        )

        self.card_total.set_value(f"{int(float(kpi.get('tong_doanh_thu') or 0)):,} đ")
        self.card_cash.set_value(f"{int(float(kpi.get('doanh_thu_tien_mat') or 0)):,} đ")
        self.card_qr.set_value(f"{int(float(kpi.get('doanh_thu_qr') or 0)):,} đ")
        self.card_turns.set_value(
            str(int(kpi.get("so_luot_thu_phi") or 0)),
            f"{int(kpi.get('so_luot_oto_vang_lai') or 0)} Ô tô · "
            f"{int(kpi.get('so_luot_xemay_vang_lai') or 0)} Xe máy · "
            f"{int(kpi.get('so_luot_xe_cu_dan') or 0)} Cư dân",
        )

        self.tbl_transactions.setRowCount(len(transactions))
        for i, tx in enumerate(transactions):
            vtype = "Ô tô" if tx.get("ma_loai_xe") == VehicleTypes.O_TO.value else "Xe máy"
            method = (
                "Chuyển khoản QR"
                if tx.get("phuong_thuc_thanh_toan") == PaymentMethods.CHUYEN_KHOAN_QR.value
                else "Tiền mặt"
            )
            self.tbl_transactions.setItem(i, 0, QTableWidgetItem(str(tx.get("ma_tham_chieu") or tx.get("ma_luot_gui") or "-")))
            self.tbl_transactions.setItem(i, 1, QTableWidgetItem(str(tx.get("thoi_gian_ra") or "-")))
            self.tbl_transactions.setItem(i, 2, QTableWidgetItem(str(tx.get("bien_so_xe") or "-")))
            self.tbl_transactions.setItem(i, 3, QTableWidgetItem(vtype))
            self.tbl_transactions.setCellWidget(
                i,
                4,
                StatusBadge(
                    method,
                    "primary" if tx.get("phuong_thuc_thanh_toan") == PaymentMethods.CHUYEN_KHOAN_QR.value else "success",
                ),
            )
            amt_item = QTableWidgetItem(f"{int(float(tx.get('tien_thu') or 0)):,} VNĐ")
            amt_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tbl_transactions.setItem(i, 5, amt_item)

        self.tbl_shifts.blockSignals(True)
        self.tbl_shifts.setRowCount(len(self._shift_history))
        for i, sh in enumerate(self._shift_history):
            st = str(sh.get("trang_thai_ca") or "dang_mo")
            self.tbl_shifts.setItem(i, 0, QTableWidgetItem(str(sh.get("ma_ca_lam_viec") or "")))
            self.tbl_shifts.setItem(i, 1, QTableWidgetItem(str(sh.get("ten_ca") or "")))
            self.tbl_shifts.setItem(i, 2, QTableWidgetItem(str(sh.get("ngay_ghi_nhan") or "")))
            self.tbl_shifts.setItem(i, 3, QTableWidgetItem(str(sh.get("gio_bat_dau") or "")))
            self.tbl_shifts.setItem(i, 4, QTableWidgetItem(str(sh.get("gio_ket_thuc") or "Đang trực")))
            self.tbl_shifts.setCellWidget(
                i, 5, StatusBadge("Đang mở" if st == "dang_mo" else "Đã chốt", "success" if st == "dang_mo" else "neutral")
            )
            rev_item = QTableWidgetItem(f"{int(float(sh.get('tong_doanh_thu_chot_ca') or 0)):,} VNĐ")
            rev_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tbl_shifts.setItem(i, 6, rev_item)
        self.tbl_shifts.blockSignals(False)

    def _on_shift_selected(self) -> None:
        row = self.tbl_shifts.currentRow()
        if 0 <= row < len(self._shift_history):
            shift_id = str(self._shift_history[row].get("ma_ca_lam_viec") or "")
            if shift_id and shift_id != str(self._selected_shift.get("ma_ca_lam_viec") or ""):
                self.refresh_data(ma_ca_lam_viec=shift_id)

    def _on_close_shift_clicked(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            ToastNotification.show_toast(self, "Chưa kết nối tới Server.", variant="danger")
            return
        confirm = QMessageBox.question(
            self,
            "Xác nhận chốt ca trực",
            f"Bạn có chắc chắn muốn chốt ca {self._current_shift.get('ten_ca', '')} "
            f"({self.card_total.lbl_value.text()})?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        note, ok = QInputDialog.getText(
            self,
            "Ghi chú bàn giao ca",
            "Nhập ghi chú bàn giao cuối ca (có thể bỏ trống):",
        )
        if not ok:
            return

        resp = self.socket_client.call(
            Actions.SHIFT_CLOSE,
            {
                "ma_ca_lam_viec": self._current_shift.get("ma_ca_lam_viec"),
                "ghi_chu": note.strip(),
            },
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self, str(resp.get("error") or "Không thể chốt ca."), variant="danger")
            return

        ToastNotification.show_toast(
            self,
            str((resp.get("data") or {}).get("message") or "Đã chốt ca thành công."),
            variant="success",
            duration_ms=4500,
        )
        self.refresh_data()
