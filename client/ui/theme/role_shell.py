"""Khung cửa sổ tách biệt theo từng vai trò (Sidebar + Topbar + Stacked Pages)."""

from pathlib import Path
from typing import Dict, Optional

from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from client.ui.theme.tokens import Layout

_QSS_PATH = Path(__file__).resolve().parent / "styles.qss"


def apply_design_stylesheet(widget: Optional[QWidget] = None) -> None:
    """Nạp stylesheet Design DNA cho QApplication hoặc một widget độc lập."""
    if not _QSS_PATH.exists():
        return
    qss = _QSS_PATH.read_text(encoding="utf-8")
    app = QApplication.instance()
    if app is not None and not app.styleSheet():
        app.setStyleSheet(qss)
    elif widget is not None and not widget.styleSheet():
        widget.setStyleSheet(qss)


class RolePortalWindow(QMainWindow):
    """Cửa sổ làm việc độc lập cho một vai trò cụ thể (phục vụ chạy & phát triển riêng)."""

    def __init__(
        self,
        window_title: str,
        brand_subtitle: str,
        role_badge_text: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.resize(1320, 820)
        apply_design_stylesheet(self)

        self._nav_buttons: Dict[str, QPushButton] = {}
        self._pages: Dict[str, QWidget] = {}
        self._page_titles: Dict[str, tuple[str, str]] = {}
        self._current_group: Optional[str] = None

        root_host = QWidget(self)
        root_host.setObjectName("AppWorkspace")
        root_layout = QHBoxLayout(root_host)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Sidebar tối cố định 256px
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(Layout.SIDEBAR_WIDTH)
        self._sidebar_layout = QVBoxLayout(sidebar)
        self._sidebar_layout.setContentsMargins(0, 16, 0, 16)
        self._sidebar_layout.setSpacing(4)

        brand_box = QVBoxLayout()
        brand_box.setContentsMargins(16, 4, 16, 12)
        brand_box.setSpacing(2)
        lbl_brand = QLabel("SMART PARKING")
        lbl_brand.setObjectName("SidebarBrandTitle")
        lbl_sub = QLabel(brand_subtitle)
        lbl_sub.setObjectName("SidebarBrandSubtitle")
        brand_box.addWidget(lbl_brand)
        brand_box.addWidget(lbl_sub)
        self._sidebar_layout.addLayout(brand_box)

        self._nav_container = QVBoxLayout()
        self._nav_container.setContentsMargins(0, 0, 0, 0)
        self._nav_container.setSpacing(2)
        self._sidebar_layout.addLayout(self._nav_container)
        self._sidebar_layout.addStretch(1)

        footer = QLabel(role_badge_text)
        footer.setObjectName("SidebarBrandSubtitle")
        footer.setContentsMargins(16, 8, 16, 0)
        self._sidebar_layout.addWidget(footer)
        root_layout.addWidget(sidebar)

        # Khu vực Topbar + Stack nội dung
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)

        topbar = QWidget()
        topbar.setObjectName("Topbar")
        topbar.setFixedHeight(Layout.TOPBAR_HEIGHT)
        topbar_lay = QHBoxLayout(topbar)
        topbar_lay.setContentsMargins(20, 8, 20, 8)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.lbl_topbar_title = QLabel("")
        self.lbl_topbar_title.setObjectName("TopbarTitle")
        self.lbl_topbar_subtitle = QLabel("")
        self.lbl_topbar_subtitle.setObjectName("TopbarSubtitle")
        title_col.addWidget(self.lbl_topbar_title)
        title_col.addWidget(self.lbl_topbar_subtitle)
        topbar_lay.addLayout(title_col, 1)

        self.lbl_connection_info = QLabel(role_badge_text)
        self.lbl_connection_info.setProperty("class", "MutedText")
        topbar_lay.addWidget(self.lbl_connection_info)
        right_col.addWidget(topbar)

        self.stack = QStackedWidget()
        right_col.addWidget(self.stack, 1)

        right_host = QWidget()
        right_host.setLayout(right_col)
        root_layout.addWidget(right_host, 1)
        self.setCentralWidget(root_host)

    def add_nav_page(
        self,
        key: str,
        nav_label: str,
        page_title: str,
        page_subtitle: str,
        widget: QWidget,
        group_label: Optional[str] = None,
    ) -> None:
        """Thêm một màn hình vào thanh điều hướng bên trái."""
        if group_label and group_label != self._current_group:
            self._current_group = group_label
            lbl_group = QLabel(group_label)
            lbl_group.setObjectName("SidebarGroupLabel")
            self._nav_container.addWidget(lbl_group)

        btn = QPushButton(nav_label)
        btn.setProperty("class", "SidebarNavBtn")
        btn.setProperty("active", "false")
        btn.clicked.connect(lambda _checked=False, k=key: self.select_page(k))
        self._nav_container.addWidget(btn)

        self._nav_buttons[key] = btn
        self._pages[key] = widget
        self._page_titles[key] = (page_title, page_subtitle)
        self.stack.addWidget(widget)

        if len(self._pages) == 1:
            self.select_page(key)

    def select_page(self, key: str) -> None:
        """Chuyển màn hình đang hiển thị và cập nhật trạng thái nút Sidebar."""
        widget = self._pages.get(key)
        if widget is None:
            return
        self.stack.setCurrentWidget(widget)
        title, subtitle = self._page_titles.get(key, ("", ""))
        self.lbl_topbar_title.setText(title)
        self.lbl_topbar_subtitle.setText(subtitle)

        for btn_key, btn in self._nav_buttons.items():
            btn.setProperty("active", "true" if btn_key == key else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def page_keys(self) -> list[str]:
        """Trả về danh sách mã trang đang đăng ký trong cửa sổ."""
        return list(self._pages.keys())
