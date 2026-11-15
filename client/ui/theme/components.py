"""Bộ UI Components dùng chung cho toàn bộ project Smart Parking (PySide6).

Đảm bảo cả 5 thành viên (Biên, Hậu, Minh, Long, Ny) tạo ra màn hình có giao diện,
căn lề, màu sắc, bảng biểu, thẻ KPI, sơ đồ bãi xe và cảnh báo khẩn cấp đồng bộ 100%.
"""

from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from client.ui.theme.tokens import (
    BADGE_VARIANTS,
    ROLE_THEMES,
    SLOT_STATE_STYLES,
    Colors,
    Fonts,
    Layout,
)


THEME_DIR = Path(__file__).resolve().parent
QSS_PATH = THEME_DIR / "styles.qss"


def apply_theme(app: QApplication) -> None:
    """Nạp Stylesheet Design DNA và font chữ chuẩn cho toàn ứng dụng."""
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    if QSS_PATH.exists():
        app.setStyleSheet(QSS_PATH.read_text(encoding="utf-8"))


def set_button_variant(button: QPushButton, variant: str = "primary", size: str = "md") -> QPushButton:
    """Gắn variant ('primary', 'teal', 'success', 'danger', 'ghost', 'default') cho QPushButton."""
    button.setProperty("variant", variant)
    button.setProperty("size", size)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def style_table(table: QTableWidget) -> QTableWidget:
    """Chuẩn hóa QTableWidget theo chuẩn Enterprise Grid."""
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(42)
    header = table.horizontalHeader()
    header.setStretchLastSection(True)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    return table


def _apply_soft_shadow(widget: QWidget, blur: int = 16, alpha: int = 18) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, 3)
    shadow.setColor(QColor(15, 23, 42, alpha))
    widget.setGraphicsEffect(shadow)


class SectionCard(QFrame):
    """Khung Card chuẩn có tiêu đề, mô tả phụ và vùng action góc phải."""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "SectionCard")
        self.setObjectName("SectionCard")
        self.setStyleSheet(
            f"QFrame#SectionCard {{ background-color: {Colors.BG_CARD}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Layout.RADIUS_MD}px; }}"
        )

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(18, 16, 18, 16)
        self._root.setSpacing(12)

        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setProperty("class", "CardTitle")
        self.title_label.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {Colors.TEXT_PRIMARY}; border: none;"
        )
        title_col.addWidget(self.title_label)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setProperty("class", "CardSubtitle")
        self.subtitle_label.setStyleSheet(
            f"font-size: 12px; color: {Colors.TEXT_MUTED}; border: none;"
        )
        self.subtitle_label.setVisible(bool(subtitle))
        title_col.addWidget(self.subtitle_label)

        self.header_layout.addLayout(title_col, 1)
        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(8)
        self.header_layout.addLayout(self.actions_layout)

        if title or subtitle:
            self._root.addLayout(self.header_layout)

        self.body_layout = QVBoxLayout()
        self.body_layout.setSpacing(10)
        self._root.addLayout(self.body_layout)

    def add_header_widget(self, widget: QWidget) -> None:
        self.actions_layout.addWidget(widget)


class StatCard(QFrame):
    """Card hiển thị chỉ số KPI cho Dashboard, Giám sát và Tài chính."""

    def __init__(
        self,
        label: str,
        value: str = "0",
        subtext: str = "",
        accent_color: str = Colors.ROYAL_BLUE,
        mono_value: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setStyleSheet(
            f"QFrame#StatCard {{ background-color: {Colors.BG_CARD}; "
            f"border: 1px solid {Colors.BORDER}; border-top: 3px solid {accent_color}; "
            f"border-radius: {Layout.RADIUS_MD}px; }}"
        )
        _apply_soft_shadow(self, blur=12, alpha=14)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        self.lbl_title = QLabel(label)
        self.lbl_title.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {Colors.TEXT_MUTED}; border: none;"
        )
        layout.addWidget(self.lbl_title)

        self.lbl_value = QLabel(value)
        font_family = Fonts.MONO_FAMILY if mono_value else Fonts.HEADING_FAMILY
        self.lbl_value.setStyleSheet(
            f"font-family: {font_family}; font-size: 24px; font-weight: 700; "
            f"color: {Colors.TEXT_PRIMARY}; border: none;"
        )
        layout.addWidget(self.lbl_value)

        self.lbl_sub = QLabel(subtext)
        self.lbl_sub.setStyleSheet(
            f"font-size: 11.5px; color: {Colors.TEXT_SECONDARY}; border: none;"
        )
        self.lbl_sub.setVisible(bool(subtext))
        layout.addWidget(self.lbl_sub)

    def set_value(self, value: str, subtext: Optional[str] = None) -> None:
        self.lbl_value.setText(value)
        if subtext is not None:
            self.lbl_sub.setText(subtext)
            self.lbl_sub.setVisible(bool(subtext))


class StatusBadge(QLabel):
    """Nhãn trạng thái bo viên thuốc dùng trong bảng và chi tiết hồ sơ."""

    def __init__(
        self,
        text: str,
        variant: str = "neutral",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_variant(variant)

    def set_variant(self, variant: str) -> None:
        style = BADGE_VARIANTS.get(variant, BADGE_VARIANTS["neutral"])
        self.setStyleSheet(
            f"background-color: {style['bg']}; color: {style['text']}; "
            f"border: 1px solid {style['border']}; border-radius: 10px; "
            f"padding: 2px 10px; font-size: 11.5px; font-weight: 700;"
        )


class RoleBadge(QLabel):
    """Badge nhận diện nhanh 1 trong 4 vai trò trên Topbar."""

    def __init__(self, role_code: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        theme = ROLE_THEMES.get(role_code, ROLE_THEMES["cu_dan"])
        self.setText(theme.label)
        self.setStyleSheet(
            f"background-color: {theme.bg_soft}; color: {theme.accent}; "
            f"border: 1px solid {theme.border}; border-radius: 12px; "
            f"padding: 4px 12px; font-size: 12px; font-weight: 700;"
        )


class PlateBadge(QLabel):
    """Hiển thị biển số xe đồng bộ (font Monospace đậm, khung biển trắng viền đen)."""

    def __init__(self, plate_text: str = "---", parent: Optional[QWidget] = None) -> None:
        super().__init__(plate_text, parent)
        self.setProperty("class", "PlateBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"font-family: {Fonts.MONO_FAMILY}; font-size: 15px; font-weight: 800; "
            f"letter-spacing: 1px; background-color: #FFFFFF; color: {Colors.TEXT_PRIMARY}; "
            f"border: 2px solid {Colors.TEXT_PRIMARY}; border-radius: 6px; padding: 4px 12px;"
        )


class ReadOnlyBanner(QFrame):
    """Banner cảnh báo chế độ chỉ xem dành riêng cho vai trò Admin."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReadOnlyBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        lbl = QLabel(
            "CHẾ ĐỘ CHỈ XEM (ADMIN): Bạn có quyền giám sát toàn bộ dữ liệu, phiên kết nối và nhật ký hệ thống; "
            "không thực hiện thêm, sửa, xóa hoặc thay đổi nghiệp vụ."
        )
        lbl.setObjectName("ReadOnlyBannerText")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)


class EmergencyBanner(QFrame):
    """Banner đỏ đầu cửa sổ khi có thông báo khẩn cấp đang hiệu lực (Workflow 01)."""

    acknowledged = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("EmergencyBanner")
        self._current_notify_id: Optional[int] = None
        self.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        self.lbl_msg = QLabel("")
        self.lbl_msg.setObjectName("EmergencyBannerText")
        self.lbl_msg.setWordWrap(True)
        layout.addWidget(self.lbl_msg, 1)

        self.btn_ack = QPushButton("Xác nhận đã tiếp nhận")
        set_button_variant(self.btn_ack, "danger", "sm")
        self.btn_ack.clicked.connect(self._on_ack)
        layout.addWidget(self.btn_ack)

    def show_alert(self, notify_id: int, title: str, content: str) -> None:
        self._current_notify_id = notify_id
        self.lbl_msg.setText(f"[KHẨN CẤP] {title}: {content}")
        self.setVisible(True)

    def _on_ack(self) -> None:
        if self._current_notify_id is not None:
            self.acknowledged.emit(self._current_notify_id)
        self.setVisible(False)


class EmergencyDialog(QDialog):
    """Popup khẩn cấp nổi bật khi Server Push sự cố (Cháy, Thiên tai, Mất điện)."""

    def __init__(
        self,
        title: str,
        content: str,
        sender_name: str = "Ban Quản Lý Chung Cư Đào Tiên",
        sent_at: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cảnh báo khẩn cấp — Chung cư Đào Tiên")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(f"QDialog {{ background-color: #FFFFFF; border: 2px solid {Colors.ERROR}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 18)
        root.setSpacing(14)

        header = QFrame()
        header.setStyleSheet(f"background-color: {Colors.ERROR};")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(20, 14, 20, 14)
        lbl_badge = QLabel("THÔNG BÁO KHẨN CẤP")
        lbl_badge.setStyleSheet("color: #FEE2E2; font-size: 11px; font-weight: 700; letter-spacing: 1px;")
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("color: #FFFFFF; font-size: 17px; font-weight: 700;")
        lbl_title.setWordWrap(True)
        h_layout.addWidget(lbl_badge)
        h_layout.addWidget(lbl_title)
        root.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(20, 4, 20, 0)
        body.setSpacing(10)

        lbl_content = QLabel(content)
        lbl_content.setWordWrap(True)
        lbl_content.setStyleSheet(f"font-size: 14px; color: {Colors.TEXT_PRIMARY}; line-height: 1.5;")
        body.addWidget(lbl_content)

        meta_text = f"Người phát: {sender_name}" + (f" · Lúc: {sent_at}" if sent_at else "")
        lbl_meta = QLabel(meta_text)
        lbl_meta.setStyleSheet(f"font-size: 12px; color: {Colors.TEXT_MUTED};")
        body.addWidget(lbl_meta)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_confirm = QPushButton("Xác nhận đã tiếp nhận")
        set_button_variant(btn_confirm, "danger")
        btn_confirm.clicked.connect(self.accept)
        btn_row.addWidget(btn_confirm)
        body.addLayout(btn_row)

        root.addLayout(body)


class ToastNotification(QFrame):
    """Thông báo nổi gọn nhẹ góc phải dưới cho phản hồi thao tác & tin thường."""

    def __init__(
        self,
        message: str,
        variant: str = "info",
        duration_ms: int = 3500,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        style = BADGE_VARIANTS.get(variant, BADGE_VARIANTS["info"])
        self.setStyleSheet(
            f"QFrame {{ background-color: #FFFFFF; border: 1px solid {style['border']}; "
            f"border-left: 4px solid {style['text']}; border-radius: 10px; }}"
        )
        _apply_soft_shadow(self, blur=20, alpha=35)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(320)
        lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-weight: 600; border: none;")
        layout.addWidget(lbl)

        QTimer.singleShot(duration_ms, self.close)

    @classmethod
    def show_toast(
        cls,
        parent_window: QWidget,
        message: str,
        variant: str = "info",
        duration_ms: int = 3500,
    ) -> "ToastNotification":
        toast = cls(message, variant=variant, duration_ms=duration_ms, parent=parent_window)
        toast.adjustSize()
        geo = parent_window.geometry()
        x = geo.x() + geo.width() - toast.width() - 24
        y = geo.y() + geo.height() - toast.height() - 24
        toast.move(x, y)
        toast.show()
        return toast


class ParkingSlotTile(QPushButton):
    """Ô đỗ xe trực quan trên sơ đồ bãi giữ xe (dùng chung cho Quản lý, Nhân viên, Admin)."""

    slot_clicked = Signal(dict)

    def __init__(self, slot_data: Dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.slot_data = slot_data
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(108, 70)
        self.update_slot(slot_data)
        self.clicked.connect(lambda: self.slot_clicked.emit(self.slot_data))

    def update_slot(self, slot_data: Dict) -> None:
        self.slot_data = slot_data
        code = slot_data.get("ma_vi_tri", "---")
        status = slot_data.get("trang_thai_vi_tri", "trong")
        plate = slot_data.get("bien_so_xe") or ""
        style = SLOT_STATE_STYLES.get(status, SLOT_STATE_STYLES["trong"])

        line2 = plate if plate else style["label"]
        self.setText(f"{code}\n{line2}")
        self.setStyleSheet(
            f"QPushButton {{ font-family: {Fonts.MONO_FAMILY}; font-size: 12px; font-weight: 700; "
            f"background-color: {style['bg']}; color: {style['text']}; "
            f"border: 1.5px solid {style['border']}; border-radius: 8px; padding: 6px; }}"
            f"QPushButton:hover {{ border: 2px solid {Colors.TEXT_PRIMARY}; }}"
        )


class ParkingGridWidget(QWidget):
    """Lưới sơ đồ các ô đỗ xe kèm chú thích màu trạng thái dùng chung."""

    slot_selected = Signal(dict)

    def __init__(self, columns: int = 6, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._columns = columns

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # Thanh chú thích 4 trạng thái chuẩn
        legend = QHBoxLayout()
        legend.setSpacing(10)
        for state_key, info in SLOT_STATE_STYLES.items():
            badge = StatusBadge(info["label"], {
                "trong": "success",
                "da_phan_cho": "primary",
                "dang_co_xe": "danger",
                "ngung_su_dung": "neutral",
            }.get(state_key, "neutral"))
            legend.addWidget(badge)
        legend.addStretch(1)
        root.addLayout(legend)

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(10)
        root.addWidget(self._grid_host)

    def set_slots(self, slots: Iterable[Dict]) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        slot_list: List[Dict] = list(slots)
        for idx, slot in enumerate(slot_list):
            tile = ParkingSlotTile(slot)
            tile.slot_clicked.connect(self.slot_selected.emit)
            row = idx // self._columns
            col = idx % self._columns
            self._grid.addWidget(tile, row, col)


class EmptyStateWidget(QFrame):
    """Hiển thị khi danh sách/bảng chưa có dữ liệu."""

    def __init__(
        self,
        title: str = "Chưa có dữ liệu",
        description: str = "Không tìm thấy bản ghi nào phù hợp với bộ lọc hiện tại.",
        action_text: str = "",
        on_action: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background-color: {Colors.BG_CARD}; "
            f"border: 1px dashed {Colors.BORDER_INPUT}; border-radius: 12px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_t = QLabel(title)
        lbl_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_t.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {Colors.TEXT_PRIMARY}; border: none;")
        layout.addWidget(lbl_t)

        lbl_d = QLabel(description)
        lbl_d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_d.setWordWrap(True)
        lbl_d.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED}; border: none;")
        layout.addWidget(lbl_d)

        if action_text and on_action is not None:
            btn = QPushButton(action_text)
            set_button_variant(btn, "primary", "sm")
            btn.clicked.connect(on_action)
            layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
