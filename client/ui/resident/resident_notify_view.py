"""Giao diện Hộp thư Thông báo của Cư dân (3.6.1 – 3.6.3)."""

from typing import Optional

from PySide6.QtWidgets import QWidget

from client.network.socket_client import SocketClient
from client.ui.staff.staff_monitor_notify_view import StaffNotifyInboxView


class ResidentNotifyView(StaffNotifyInboxView):
    """Màn hình 3.6 Hộp thư Thông báo Cư dân: Xem danh sách, chi tiết, ACK & Đánh dấu đã đọc."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            socket_client=socket_client,
            section_code="3.6",
            role_title="Cư dân Chung cư Đào Tiên",
            parent=parent,
        )
