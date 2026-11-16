"""Giao diện Quản lý Theo dõi Thu phí 1.7.1–1.7.4 (hỗ trợ read_only cho Admin 4.7)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.resident.bills_payment_view import InvoiceDetailDialog
from client.ui.theme.components import (
    ReadOnlyBanner,
    SectionCard,
    StatCard,
    StatusBadge,
    ToastNotification,
    set_button_variant,
    style_table,
)
from client.ui.theme.tokens import Colors, Fonts
from shared.protocol import Actions


def _fmt_vnd(val: Any) -> str:
    try:
        return f"{int(round(float(val or 0))):,} đ".replace(",", ".")
    except (TypeError, ValueError):
        return "0 đ"


class TransactionDetailDialog(QDialog):
    """Hộp thoại 1.7.4: Xem chi tiết giao dịch thanh toán, hóa đơn, căn hộ & bảng giá."""

    def __init__(self, data: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        tx = data.get("transaction") or {}
        rates = data.get("applied_rates") or {}
        self.setWindowTitle(f"Chi tiết Giao dịch #{tx.get('ma_giao_dich', '')} (1.7.4)")
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        card = SectionCard(
            title=f"Giao dịch #{tx.get('ma_giao_dich', '-')}",
            subtitle=f"Căn hộ {tx.get('ma_can_ho', '-')} · Kỳ thu {tx.get('ky_thu_thang_nam', '-')}",
        )
        form = QFormLayout()
        for label, val in (
            ("Mã hóa đơn:", tx.get("ma_hoa_don")),
            ("Hình thức:", tx.get("hinh_thuc_thanh_toan")),
            ("Số tiền thanh toán:", _fmt_vnd(tx.get("so_tien_thanh_toan"))),
            ("Trạng thái giao dịch:", tx.get("trang_thai_giao_dich")),
            ("Mã biên lai:", tx.get("ma_bien_lai") or "-"),
            ("Nhân viên thu:", tx.get("ten_nhan_vien_thu") or "Hệ thống QR Tự động"),
            ("Thời gian giao dịch:", tx.get("thoi_gian_giao_dich")),
            ("Chi tiết căn hộ:", f"Ô tô mặc định: {tx.get('so_cho_oto_mac_dinh', 0)} · Mua thêm: {tx.get('so_cho_oto_mua_them', 0)} · Xe máy: {tx.get('so_luong_xe_may', 0)}"),
            ("Đơn giá áp dụng:", f"Ô tô MĐ: {_fmt_vnd(rates.get('cho_oto_mac_dinh'))} | Mua thêm: {_fmt_vnd(rates.get('cho_oto_mua_them'))} | Xe máy: {_fmt_vnd(rates.get('xe_may_cu_dan'))}"),
        ):
            form.addRow(label, QLabel(str(val if val is not None else "-")))
        card.body_layout.addLayout(form)
        root.addWidget(card)

        btn_close = QPushButton("Đóng")
        set_button_variant(btn_close, "default", "sm")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)


class FinanceView(QWidget):
    """Màn hình 1.7 Quản lý Theo dõi Thu phí, hỗ trợ read_only=True cho Admin."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        read_only: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self.read_only = read_only
        self._receivables: List[Dict[str, Any]] = []
        self._collected: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        if self.read_only:
            root.addWidget(ReadOnlyBanner(self))

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Theo dõi Thu phí & Công nợ Hóa đơn tháng (1.7)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        lbl_sub = QLabel("Phát hành hóa đơn định kỳ, theo dõi phải thu, đã thu và xác nhận thu tiền mặt")
        lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        header.addLayout(title_col, 1)

        self.txt_period = QLineEdit(datetime.now().strftime("%m/%Y"))
        self.txt_period.setFixedWidth(96)
        self.txt_period.setPlaceholderText("MM/YYYY")

        self.btn_refresh = QPushButton("Làm mới số liệu")
        set_button_variant(self.btn_refresh, "default", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)

        self.btn_generate = self._mark_mutation(QPushButton("Phát hành hóa đơn kỳ"))
        set_button_variant(self.btn_generate, "primary", "sm")
        self.btn_generate.clicked.connect(self._generate_invoices)

        header.addWidget(QLabel("Kỳ thu:"))
        header.addWidget(self.txt_period)
        header.addWidget(self.btn_refresh)
        header.addWidget(self.btn_generate)
        root.addLayout(header)

        # 3 thẻ KPI tổng hợp 1.7.3
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(14)
        self.card_total_due = StatCard("Tổng phải thu (1.7.3)", "0 đ", "0 hóa đơn", accent_color=Colors.ROYAL_BLUE)
        self.card_total_paid = StatCard("Tổng đã thu (1.7.3)", "0 đ", "0 đã thanh toán", accent_color=Colors.SUCCESS)
        self.card_total_debt = StatCard("Tổng còn nợ (1.7.3)", "0 đ", "0 chưa thanh toán", accent_color=Colors.ERROR)
        kpi_row.addWidget(self.card_total_due, 1)
        kpi_row.addWidget(self.card_total_paid, 1)
        kpi_row.addWidget(self.card_total_debt, 1)
        root.addLayout(kpi_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_receivables_tab(), "1.7.1 Khoản phải thu")
        self.tabs.addTab(self._build_collected_tab(), "1.7.2 Khoản đã thu & Biên lai")
        root.addWidget(self.tabs, 1)

    def _mark_mutation(self, btn: QPushButton) -> QPushButton:
        btn.setProperty("is_mutation", True)
        if self.read_only:
            btn.setEnabled(False)
            btn.setVisible(False)
        return btn

    def _build_receivables_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 10, 0, 0)
        card = SectionCard(
            title="Danh sách Hóa đơn Phải thu theo kỳ (1.7.1)",
            subtitle="Theo dõi căn hộ chưa thanh toán, quá hạn và xác nhận thu tiền mặt tại phòng bảo vệ",
        )
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.txt_recv_kw = QLineEdit()
        self.txt_recv_kw.setPlaceholderText("Tìm theo mã căn hộ / mã hóa đơn...")
        self.cbo_recv_status = QComboBox()
        self.cbo_recv_status.addItem("Tất cả trạng thái", "")
        self.cbo_recv_status.addItem("Chưa thanh toán", "chua_thanh_toan")
        self.cbo_recv_status.addItem("Quá hạn", "qua_han")
        self.cbo_recv_status.addItem("Đã thanh toán", "da_thanh_toan")

        btn_filter = QPushButton("Lọc danh sách")
        set_button_variant(btn_filter, "default", "sm")
        btn_filter.clicked.connect(self.refresh_receivables)

        btn_det = QPushButton("Xem chi tiết HĐ")
        set_button_variant(btn_det, "teal", "sm")
        btn_det.clicked.connect(self._open_invoice_detail)

        self.btn_confirm_cash = self._mark_mutation(QPushButton("Xác nhận thu tiền mặt"))
        set_button_variant(self.btn_confirm_cash, "success", "sm")
        self.btn_confirm_cash.clicked.connect(self._confirm_cash_payment)

        bar.addWidget(self.txt_recv_kw, 2)
        bar.addWidget(self.cbo_recv_status, 1)
        bar.addWidget(btn_filter)
        bar.addWidget(btn_det)
        bar.addWidget(self.btn_confirm_cash)
        card.body_layout.addLayout(bar)

        self.tbl_receivables = QTableWidget(0, 8)
        self.tbl_receivables.setHorizontalHeaderLabels(
            ["Mã HĐ", "Căn hộ", "Kỳ thu", "Ô tô MĐ", "Ô tô thêm", "Xe máy", "Tổng tiền", "Trạng thái"]
        )
        style_table(self.tbl_receivables)
        card.body_layout.addWidget(self.tbl_receivables, 1)
        lay.addWidget(card, 1)
        return host

    def _build_collected_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 10, 0, 0)
        card = SectionCard(
            title="Danh sách Khoản đã thu thành công (1.7.2)",
            subtitle="Lọc theo hình thức QR / Tiền mặt và khoảng ngày giao dịch",
        )
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.cbo_method = QComboBox()
        self.cbo_method.addItem("Tất cả hình thức", "")
        self.cbo_method.addItem("Chuyển khoản QR", "chuyen_khoan_qr")
        self.cbo_method.addItem("Tiền mặt", "tien_mat")

        self.txt_from_date = QLineEdit()
        self.txt_from_date.setPlaceholderText("Từ ngày (YYYY-MM-DD)")
        self.txt_to_date = QLineEdit()
        self.txt_to_date.setPlaceholderText("Đến ngày (YYYY-MM-DD)")

        btn_filter = QPushButton("Lọc khoản đã thu")
        set_button_variant(btn_filter, "default", "sm")
        btn_filter.clicked.connect(self.refresh_collected)

        btn_tx_det = QPushButton("Chi tiết thanh toán (1.7.4)")
        set_button_variant(btn_tx_det, "teal", "sm")
        btn_tx_det.clicked.connect(self._open_tx_detail)

        bar.addWidget(self.cbo_method, 1)
        bar.addWidget(self.txt_from_date, 1)
        bar.addWidget(self.txt_to_date, 1)
        bar.addWidget(btn_filter)
        bar.addWidget(btn_tx_det)
        card.body_layout.addLayout(bar)

        self.tbl_collected = QTableWidget(0, 8)
        self.tbl_collected.setHorizontalHeaderLabels(
            ["Mã GD", "Mã HĐ", "Căn hộ", "Kỳ thu", "Hình thức", "Số tiền", "Mã biên lai", "Người thu"]
        )
        style_table(self.tbl_collected)
        self.tbl_collected.cellDoubleClicked.connect(lambda _r, _c: self._open_tx_detail())
        card.body_layout.addWidget(self.tbl_collected, 1)
        lay.addWidget(card, 1)
        return host

    def refresh_data(self) -> None:
        self.refresh_summary()
        self.refresh_receivables()
        self.refresh_collected()

    def refresh_summary(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_SUMMARY, {"ky_thu_thang_nam": self.txt_period.text().strip()}
        )
        if resp.get("ok") and resp.get("data"):
            self.apply_summary(resp["data"])

    def apply_summary(self, data: Dict[str, Any]) -> None:
        self.card_total_due.set_value(
            _fmt_vnd(data.get("tong_phai_thu")),
            f"{data.get('tong_hoa_don', 0)} hóa đơn trong kỳ",
        )
        self.card_total_paid.set_value(
            _fmt_vnd(data.get("tong_da_thu")),
            f"{data.get('so_da_thu', 0)} hóa đơn đã thanh toán",
        )
        self.card_total_debt.set_value(
            _fmt_vnd(data.get("tong_con_no")),
            f"{data.get('so_con_no', 0)} hóa đơn còn nợ",
        )

    def refresh_receivables(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_LIST_RECEIVABLES,
            {
                "ky_thu_thang_nam": self.txt_period.text().strip(),
                "trang_thai_hoa_don": self.cbo_recv_status.currentData(),
                "keyword": self.txt_recv_kw.text().strip(),
            },
        )
        if resp.get("ok") and resp.get("data"):
            self.set_receivables(resp["data"].get("items") or [])

    def set_receivables(self, items: List[Dict[str, Any]]) -> None:
        self._receivables = items
        self.tbl_receivables.setRowCount(len(items))
        for idx, r in enumerate(items):
            status = str(r.get("trang_thai_hoa_don") or "chua_thanh_toan")
            variant = "success" if status == "da_thanh_toan" else ("danger" if status == "qua_han" else "warning")
            self.tbl_receivables.setItem(idx, 0, QTableWidgetItem(str(r.get("ma_hoa_don") or "")))
            self.tbl_receivables.setItem(idx, 1, QTableWidgetItem(str(r.get("ma_can_ho") or "")))
            self.tbl_receivables.setItem(idx, 2, QTableWidgetItem(str(r.get("ky_thu_thang_nam") or "")))
            self.tbl_receivables.setItem(idx, 3, QTableWidgetItem(str(r.get("so_cho_oto_mac_dinh") or 0)))
            self.tbl_receivables.setItem(idx, 4, QTableWidgetItem(str(r.get("so_cho_oto_mua_them") or 0)))
            self.tbl_receivables.setItem(idx, 5, QTableWidgetItem(str(r.get("so_luong_xe_may") or 0)))
            self.tbl_receivables.setItem(idx, 6, QTableWidgetItem(_fmt_vnd(r.get("tong_tien_phai_thu"))))
            self.tbl_receivables.setCellWidget(idx, 7, StatusBadge(status, variant))

    def refresh_collected(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_LIST_COLLECTED,
            {
                "ky_thu_thang_nam": self.txt_period.text().strip(),
                "hinh_thuc_thanh_toan": self.cbo_method.currentData(),
                "tu_ngay": self.txt_from_date.text().strip() or None,
                "den_ngay": self.txt_to_date.text().strip() or None,
            },
        )
        if resp.get("ok") and resp.get("data"):
            self.set_collected(resp["data"].get("items") or [])

    def set_collected(self, items: List[Dict[str, Any]]) -> None:
        self._collected = items
        self.tbl_collected.setRowCount(len(items))
        for idx, t in enumerate(items):
            self.tbl_collected.setItem(idx, 0, QTableWidgetItem(str(t.get("ma_giao_dich") or "")))
            self.tbl_collected.setItem(idx, 1, QTableWidgetItem(str(t.get("ma_hoa_don") or "")))
            self.tbl_collected.setItem(idx, 2, QTableWidgetItem(str(t.get("ma_can_ho") or "")))
            self.tbl_collected.setItem(idx, 3, QTableWidgetItem(str(t.get("ky_thu_thang_nam") or "")))
            self.tbl_collected.setItem(idx, 4, QTableWidgetItem(str(t.get("hinh_thuc_thanh_toan") or "")))
            self.tbl_collected.setItem(idx, 5, QTableWidgetItem(_fmt_vnd(t.get("so_tien_thanh_toan"))))
            self.tbl_collected.setItem(idx, 6, QTableWidgetItem(str(t.get("ma_bien_lai") or "-")))
            self.tbl_collected.setItem(idx, 7, QTableWidgetItem(str(t.get("ten_nhan_vien_thu") or "QR Tự động")))

    def _generate_invoices(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_GENERATE_MONTHLY,
            {"ky_thu_thang_nam": self.txt_period.text().strip()},
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error")), variant="danger")
            return
        d = resp.get("data") or {}
        ToastNotification.show_toast(
            self.window(),
            f"Đã đồng bộ hóa đơn kỳ {d.get('ky_thu_thang_nam')}: tạo mới {d.get('created', 0)}, cập nhật {d.get('updated', 0)}.",
            variant="success",
        )
        self.refresh_data()

    def _confirm_cash_payment(self) -> None:
        row = self.tbl_receivables.currentRow()
        if row < 0 or row >= len(self._receivables):
            return
        inv = self._receivables[row]
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_CONFIRM_CASH_PAYMENT,
            {"ma_hoa_don": inv["ma_hoa_don"]},
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error")), variant="danger")
            return
        ToastNotification.show_toast(
            self.window(),
            f"Đã thu tiền mặt hóa đơn #{inv['ma_hoa_don']} — Biên lai: {(resp.get('data') or {}).get('ma_bien_lai')}",
            variant="success",
        )
        self.refresh_data()

    def _open_invoice_detail(self) -> None:
        row = self.tbl_receivables.currentRow()
        if row < 0 or row >= len(self._receivables):
            return
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_GET_INVOICE_DETAIL,
            {"ma_hoa_don": self._receivables[row]["ma_hoa_don"]},
        )
        if resp.get("ok") and resp.get("data"):
            InvoiceDetailDialog(resp["data"], parent=self).exec()

    def _open_tx_detail(self) -> None:
        row = self.tbl_collected.currentRow()
        if row < 0 or row >= len(self._collected):
            return
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_GET_TRANSACTION_DETAIL,
            {"ma_giao_dich": self._collected[row]["ma_giao_dich"]},
        )
        if resp.get("ok") and resp.get("data"):
            TransactionDetailDialog(resp["data"], parent=self).exec()
