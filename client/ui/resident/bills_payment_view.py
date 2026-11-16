"""Giao diện Hóa đơn & Thanh toán QR/Tiền mặt của Cư dân (3.5 & Workflow 06)."""

import base64
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.theme.components import (
    SectionCard,
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


class InvoiceDetailDialog(QDialog):
    """Hộp thoại 3.5.3: Xem chi tiết phân tách hóa đơn, vị trí đỗ & lịch sử giao dịch."""

    def __init__(self, detail: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        inv = detail.get("invoice") or {}
        self.setWindowTitle(f"Chi tiết Hóa đơn #{inv.get('ma_hoa_don', '')} — Kỳ {inv.get('ky_thu_thang_nam', '')}")
        self.setMinimumSize(640, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        lbl_head = QLabel(
            f"Căn hộ {inv.get('ma_can_ho', '-')} · Kỳ thu {inv.get('ky_thu_thang_nam', '-')} · "
            f"Tổng phải thu: {_fmt_vnd(inv.get('tong_tien_phai_thu'))}"
        )
        lbl_head.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 16px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        root.addWidget(lbl_head)

        tbl_break = QTableWidget(0, 4)
        tbl_break.setHorizontalHeaderLabels(["Khoản mục", "Số lượng", "Đơn giá", "Thành tiền"])
        style_table(tbl_break)
        breakdown = detail.get("breakdown") or []
        tbl_break.setRowCount(len(breakdown))
        for idx, b in enumerate(breakdown):
            tbl_break.setItem(idx, 0, QTableWidgetItem(str(b.get("khoan_muc") or "")))
            tbl_break.setItem(idx, 1, QTableWidgetItem(str(b.get("so_luong") or 0)))
            tbl_break.setItem(idx, 2, QTableWidgetItem(_fmt_vnd(b.get("don_gia"))))
            tbl_break.setItem(idx, 3, QTableWidgetItem(_fmt_vnd(b.get("thanh_tien"))))
        root.addWidget(tbl_break)

        slots = detail.get("owned_slots") or []
        slot_names = ", ".join(f"{s.get('ma_vi_tri')} ({s.get('ma_khu_vuc')})" for s in slots) or "Chưa gán vị trí cố định"
        lbl_slots = QLabel(f"Vị trí đỗ đang sở hữu: {slot_names}")
        lbl_slots.setWordWrap(True)
        lbl_slots.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_SECONDARY};")
        root.addWidget(lbl_slots)

        tbl_tx = QTableWidget(0, 5)
        tbl_tx.setHorizontalHeaderLabels(["Thời gian", "Hình thức", "Số tiền", "Trạng thái", "Biên lai / Ghi chú"])
        style_table(tbl_tx)
        txs = detail.get("transactions") or []
        tbl_tx.setRowCount(len(txs))
        for idx, t in enumerate(txs):
            tbl_tx.setItem(idx, 0, QTableWidgetItem(str(t.get("thoi_gian_giao_dich") or "")))
            tbl_tx.setItem(idx, 1, QTableWidgetItem(str(t.get("hinh_thuc_thanh_toan") or "")))
            tbl_tx.setItem(idx, 2, QTableWidgetItem(_fmt_vnd(t.get("so_tien_thanh_toan"))))
            tbl_tx.setItem(idx, 3, QTableWidgetItem(str(t.get("trang_thai_giao_dich") or "")))
            tbl_tx.setItem(idx, 4, QTableWidgetItem(str(t.get("ma_bien_lai") or t.get("ly_do_loi") or "-")))
        root.addWidget(tbl_tx, 1)

        btn_close = QPushButton("Đóng")
        set_button_variant(btn_close, "default", "sm")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)


class ReceiptDialog(QDialog):
    """Hộp thoại 3.5.4: Xem biên nhận thanh toán thành công."""

    def __init__(self, receipt: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Biên nhận Thanh toán — {receipt.get('ma_bien_lai', '')}")
        self.setMinimumWidth(440)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        card = SectionCard(
            title="BIÊN NHẬN THANH TOÁN PHÍ GỬI XE",
            subtitle=f"Mã biên lai: {receipt.get('ma_bien_lai') or '-'}",
        )
        form = QFormLayout()
        for label, val in (
            ("Mã hóa đơn:", receipt.get("ma_hoa_don")),
            ("Căn hộ:", receipt.get("ma_can_ho")),
            ("Kỳ thu:", receipt.get("ky_thu_thang_nam")),
            ("Hình thức:", receipt.get("hinh_thuc_thanh_toan")),
            ("Số tiền đã thu:", _fmt_vnd(receipt.get("so_tien_thanh_toan"))),
            ("Nhân viên thu:", receipt.get("ten_nhan_vien_thu") or "Hệ thống QR Tự động"),
            ("Thời gian:", receipt.get("thoi_gian_giao_dich")),
            ("Trạng thái:", receipt.get("trang_thai_giao_dich")),
        ):
            form.addRow(label, QLabel(str(val if val is not None else "-")))
        card.body_layout.addLayout(form)
        root.addWidget(card)

        btn_close = QPushButton("Đóng")
        set_button_variant(btn_close, "primary", "sm")
        btn_close.clicked.connect(self.accept)
        root.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignRight)


class PaymentWorkflow06Dialog(QDialog):
    """Dialog Thanh toán Workflow 06: Chuyển khoản QR (Yes/No) hoặc Tiền mặt."""

    def __init__(
        self,
        invoice: Dict[str, Any],
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.invoice = invoice
        self.socket_client = socket_client
        self._ma_giao_dich: Optional[int] = None
        self._remaining_seconds: int = 300
        self.payment_completed = False

        self.setWindowTitle(f"Thanh toán Hóa đơn #{invoice.get('ma_hoa_don')} — Workflow 06")
        self.setMinimumSize(480, 520)
        self.setModal(True)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        lbl_sum = QLabel(
            f"Hóa đơn #{self.invoice.get('ma_hoa_don')} · Kỳ {self.invoice.get('ky_thu_thang_nam')} · "
            f"Số tiền: {_fmt_vnd(self.invoice.get('tong_tien_phai_thu'))}"
        )
        lbl_sum.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 15px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        root.addWidget(lbl_sum)

        self.lbl_msg = QLabel("")
        self.lbl_msg.setWordWrap(True)
        self.lbl_msg.setVisible(False)
        root.addWidget(self.lbl_msg)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_method_page())
        self.stack.addWidget(self._build_qr_page())
        self.stack.addWidget(self._build_cash_page())
        root.addWidget(self.stack, 1)

    def _build_method_page(self) -> QWidget:
        card = SectionCard(
            title="Chọn hình thức thanh toán",
            subtitle="Theo đúng luồng Workflow 06: Chuyển khoản QR hoặc Tiền mặt tại phòng bảo vệ",
        )
        self.btn_choose_qr = QPushButton("Chuyển khoản & Hiển thị mã QR (Trực tuyến)")
        set_button_variant(self.btn_choose_qr, "primary", "md")
        self.btn_choose_qr.clicked.connect(self._start_qr_payment)

        self.btn_choose_cash = QPushButton("Tiền mặt (Đến phòng bảo vệ nộp tiền & nhận biên lai)")
        set_button_variant(self.btn_choose_cash, "teal", "md")
        self.btn_choose_cash.clicked.connect(lambda: self.stack.setCurrentIndex(2))

        card.body_layout.addWidget(self.btn_choose_qr)
        card.body_layout.addWidget(self.btn_choose_cash)
        card.body_layout.addStretch(1)
        return card

    def _build_qr_page(self) -> QWidget:
        card = SectionCard(
            title="Quét mã QR thanh toán",
            subtitle="Vui lòng quét mã trong thời gian hiệu lực (5 phút)",
        )
        self.lbl_qr_image = QLabel("Đang tạo mã QR...")
        self.lbl_qr_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_qr_image.setMinimumSize(230, 230)
        self.lbl_qr_image.setStyleSheet(
            f"background: #FFFFFF; border: 1px solid {Colors.BORDER}; border-radius: 10px; padding: 8px;"
        )
        self.lbl_countdown = QLabel("Hiệu lực còn lại: 05:00")
        self.lbl_countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_countdown.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 14px; font-weight: 700; color: {Colors.WARNING_TEXT}; border: none;"
        )
        self.lbl_qr_code_text = QLabel("")
        self.lbl_qr_code_text.setWordWrap(True)
        self.lbl_qr_code_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_qr_code_text.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 11px; color: {Colors.TEXT_MUTED}; border: none;"
        )

        card.body_layout.addWidget(self.lbl_qr_image, 0, Qt.AlignmentFlag.AlignCenter)
        card.body_layout.addWidget(self.lbl_countdown)
        card.body_layout.addWidget(self.lbl_qr_code_text)

        row = QHBoxLayout()
        self.btn_qr_fail = QPushButton("Mô phỏng lỗi / Quá hạn (No)")
        set_button_variant(self.btn_qr_fail, "danger", "sm")
        self.btn_qr_fail.clicked.connect(lambda: self._complete_qr("loi_qua_han"))

        self.btn_qr_success = QPushButton("Xác nhận đã quét thanh toán (Yes)")
        set_button_variant(self.btn_qr_success, "success", "md")
        self.btn_qr_success.clicked.connect(lambda: self._complete_qr("thanh_cong"))

        row.addWidget(self.btn_qr_fail)
        row.addWidget(self.btn_qr_success)
        card.body_layout.addLayout(row)
        return card

    def _build_cash_page(self) -> QWidget:
        card = SectionCard(
            title="Thanh toán tiền mặt tại phòng bảo vệ",
            subtitle="Hướng dẫn quy trình nộp tiền mặt (Workflow 06 — Nhánh Tiền mặt)",
        )
        lbl = QLabel(
            f"1. Vui lòng mang mã hóa đơn #{self.invoice.get('ma_hoa_don')} đến Phòng Bảo Vệ / Ban Quản Lý.\n"
            f"2. Nộp số tiền {_fmt_vnd(self.invoice.get('tong_tien_phai_thu'))} cho nhân viên trực.\n"
            "3. Nhân viên sẽ xác nhận thu tiền trên hệ thống và cấp mã biên lai (BL-TM-...).\n"
            "4. Sau khi nộp, bấm 'Làm mới danh sách' để xem biên nhận 3.5.4."
        )
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size: 13px; color: {Colors.TEXT_PRIMARY}; line-height: 1.5; border: none;")
        card.body_layout.addWidget(lbl)
        card.body_layout.addStretch(1)

        row = QHBoxLayout()
        btn_back = QPushButton("Quay lại chọn hình thức")
        set_button_variant(btn_back, "default", "sm")
        btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        btn_done = QPushButton("Đã hiểu")
        set_button_variant(btn_done, "primary", "sm")
        btn_done.clicked.connect(self.accept)
        row.addWidget(btn_back)
        row.addStretch(1)
        row.addWidget(btn_done)
        card.body_layout.addLayout(row)
        return card

    def _show_msg(self, text: str, is_error: bool) -> None:
        bg, fg, bd = (
            (Colors.ERROR_BG, Colors.ERROR_TEXT, Colors.ERROR_BORDER)
            if is_error
            else (Colors.SUCCESS_BG, Colors.SUCCESS_TEXT, Colors.SUCCESS_BORDER)
        )
        self.lbl_msg.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {bd}; "
            "border-radius: 8px; padding: 8px 10px; font-size: 12px; font-weight: 600;"
        )
        self.lbl_msg.setText(text)
        self.lbl_msg.setVisible(True)

    def _start_qr_payment(self) -> None:
        self.lbl_msg.setVisible(False)
        if self.socket_client is None or not self.socket_client.is_connected():
            self._show_msg("Chưa kết nối tới Server.", True)
            return
        resp = self.socket_client.call(
            Actions.BILLING_CREATE_QR_PAYMENT,
            {"ma_hoa_don": self.invoice.get("ma_hoa_don")},
        )
        if not resp.get("ok"):
            self._show_msg(str(resp.get("error") or "Không tạo được mã QR."), True)
            return

        data = resp.get("data") or {}
        self._ma_giao_dich = data.get("ma_giao_dich")
        self._remaining_seconds = int(data.get("expires_in_seconds") or 300)
        self.lbl_qr_code_text.setText(str(data.get("ma_qr") or ""))

        b64 = str(data.get("qr_image_base64") or "")
        if b64:
            pix = QPixmap()
            if pix.loadFromData(base64.b64decode(b64), "PNG"):
                self.lbl_qr_image.setPixmap(
                    pix.scaled(210, 210, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )

        self._update_countdown_label()
        self._timer.start()
        self.stack.setCurrentIndex(1)

    def _on_tick(self) -> None:
        self._remaining_seconds = max(0, self._remaining_seconds - 1)
        self._update_countdown_label()
        if self._remaining_seconds <= 0:
            self._timer.stop()
            self._complete_qr("loi_qua_han")

    def _update_countdown_label(self) -> None:
        mins, secs = divmod(self._remaining_seconds, 60)
        self.lbl_countdown.setText(f"Hiệu lực còn lại: {mins:02d}:{secs:02d}")

    def _complete_qr(self, ket_qua: str) -> None:
        self._timer.stop()
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_COMPLETE_QR_PAYMENT,
            {
                "ma_hoa_don": self.invoice.get("ma_hoa_don"),
                "ma_giao_dich": self._ma_giao_dich,
                "ket_qua": ket_qua,
            },
        )
        if not resp.get("ok"):
            self._show_msg(str(resp.get("error") or "Lỗi xử lý thanh toán QR."), True)
            self.stack.setCurrentIndex(0)
            return

        data = resp.get("data") or {}
        if data.get("success"):
            self.payment_completed = True
            self.accept()
        else:
            self._show_msg(
                "Giao dịch lỗi hoặc mã QR quá hạn — hóa đơn vẫn ở trạng thái chưa thanh toán. "
                "Vui lòng chọn lại hình thức thanh toán.",
                True,
            )
            self.stack.setCurrentIndex(0)

    def closeEvent(self, event: object) -> None:
        self._timer.stop()
        super().closeEvent(event)  # type: ignore[arg-type]


class BillsPaymentView(QWidget):
    """Màn hình 3.5: Hóa đơn gửi xe của Cư dân & Thanh toán Workflow 06."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._invoices: List[Dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        lbl_title = QLabel("Hóa đơn phí gửi xe hàng tháng & Thanh toán (3.5)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        root.addWidget(lbl_title)

        card = SectionCard(
            title="Danh sách Hóa đơn của Căn hộ",
            subtitle="Xem chi tiết khoản phí, thanh toán qua QR hoặc xem biên nhận đã nộp",
        )

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Tìm theo mã hóa đơn hoặc kỳ thu (VD: 11/2026)...")
        self.cbo_status = QComboBox()
        self.cbo_status.addItem("Tất cả trạng thái", "")
        self.cbo_status.addItem("Chưa thanh toán", "chua_thanh_toan")
        self.cbo_status.addItem("Đã thanh toán", "da_thanh_toan")
        self.cbo_status.addItem("Quá hạn", "qua_han")

        self.btn_search = QPushButton("Tìm kiếm (3.5.2)")
        set_button_variant(self.btn_search, "default", "sm")
        self.btn_search.clicked.connect(self.search_invoices)

        self.btn_detail = QPushButton("Xem chi tiết (3.5.3)")
        set_button_variant(self.btn_detail, "teal", "sm")
        self.btn_detail.clicked.connect(self._open_detail)

        self.btn_pay = QPushButton("Thanh toán (Workflow 06)")
        set_button_variant(self.btn_pay, "primary", "sm")
        self.btn_pay.clicked.connect(self._open_payment)

        self.btn_receipt = QPushButton("Xem biên nhận (3.5.4)")
        set_button_variant(self.btn_receipt, "success", "sm")
        self.btn_receipt.clicked.connect(self._open_receipt)

        bar.addWidget(self.txt_search, 2)
        bar.addWidget(self.cbo_status, 1)
        bar.addWidget(self.btn_search)
        bar.addWidget(self.btn_detail)
        bar.addWidget(self.btn_pay)
        bar.addWidget(self.btn_receipt)
        card.body_layout.addLayout(bar)

        self.tbl_invoices = QTableWidget(0, 7)
        self.tbl_invoices.setHorizontalHeaderLabels(
            ["Mã HĐ", "Kỳ thu", "Ô tô mặc định", "Ô tô mua thêm", "Xe máy", "Tổng tiền", "Trạng thái"]
        )
        style_table(self.tbl_invoices)
        self.tbl_invoices.cellDoubleClicked.connect(lambda _r, _c: self._open_detail())
        card.body_layout.addWidget(self.tbl_invoices, 1)
        root.addWidget(card, 1)

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_MY_INVOICES,
            {"trang_thai_hoa_don": self.cbo_status.currentData()},
        )
        if resp.get("ok") and resp.get("data"):
            self.set_invoices(resp["data"].get("items") or [])

    def search_invoices(self) -> None:
        kw = self.txt_search.text().strip()
        if not kw:
            self.refresh_data()
            return
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_SEARCH_MY_INVOICES,
            {"keyword": kw, "trang_thai_hoa_don": self.cbo_status.currentData()},
        )
        if resp.get("ok") and resp.get("data"):
            self.set_invoices(resp["data"].get("items") or [])

    def set_invoices(self, items: List[Dict[str, Any]]) -> None:
        self._invoices = items
        self.tbl_invoices.setRowCount(len(items))
        for idx, inv in enumerate(items):
            status = str(inv.get("trang_thai_hoa_don") or "chua_thanh_toan")
            variant = "success" if status == "da_thanh_toan" else ("danger" if status == "qua_han" else "warning")
            self.tbl_invoices.setItem(idx, 0, QTableWidgetItem(str(inv.get("ma_hoa_don") or "")))
            self.tbl_invoices.setItem(idx, 1, QTableWidgetItem(str(inv.get("ky_thu_thang_nam") or "")))
            self.tbl_invoices.setItem(idx, 2, QTableWidgetItem(str(inv.get("so_cho_oto_mac_dinh") or 0)))
            self.tbl_invoices.setItem(idx, 3, QTableWidgetItem(str(inv.get("so_cho_oto_mua_them") or 0)))
            self.tbl_invoices.setItem(idx, 4, QTableWidgetItem(str(inv.get("so_luong_xe_may") or 0)))
            self.tbl_invoices.setItem(idx, 5, QTableWidgetItem(_fmt_vnd(inv.get("tong_tien_phai_thu"))))
            self.tbl_invoices.setCellWidget(idx, 6, StatusBadge(status, variant))

    def _selected_invoice(self) -> Optional[Dict[str, Any]]:
        row = self.tbl_invoices.currentRow()
        if row < 0 or row >= len(self._invoices):
            return None
        return self._invoices[row]

    def _open_detail(self) -> None:
        inv = self._selected_invoice()
        if inv is None or self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_GET_INVOICE_DETAIL, {"ma_hoa_don": inv["ma_hoa_don"]}
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error")), variant="danger")
            return
        InvoiceDetailDialog(resp.get("data") or {}, parent=self).exec()

    def _open_payment(self) -> None:
        inv = self._selected_invoice()
        if inv is None:
            return
        if inv.get("trang_thai_hoa_don") == "da_thanh_toan":
            ToastNotification.show_toast(self.window(), "Hóa đơn này đã được thanh toán.", variant="info")
            return
        dlg = PaymentWorkflow06Dialog(inv, socket_client=self.socket_client, parent=self)
        dlg.exec()
        if dlg.payment_completed:
            ToastNotification.show_toast(self.window(), "Thanh toán hóa đơn thành công!", variant="success")
            self.refresh_data()

    def _open_receipt(self) -> None:
        inv = self._selected_invoice()
        if inv is None or self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.BILLING_GET_RECEIPT, {"ma_hoa_don": inv["ma_hoa_don"]}
        )
        if not resp.get("ok"):
            ToastNotification.show_toast(self.window(), str(resp.get("error")), variant="warning")
            return
        ReceiptDialog((resp.get("data") or {}).get("receipt") or {}, parent=self).exec()
