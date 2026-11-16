"""Entrypoint chính khởi chạy ứng dụng Desktop Smart Parking (PySide6)."""

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QApplication

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from client.network.socket_client import SocketClient
from client.ui.auth.login_dialog import LoginDialog
from client.ui.main_window import MainWindow
from client.ui.theme.components import apply_theme


def run_app(argv: Optional[list[str]] = None) -> int:
    """Khởi chạy vòng đời LoginDialog -> MainWindow (hỗ trợ đăng xuất / thu hồi phiên)."""
    app = QApplication(argv if argv is not None else sys.argv)
    apply_theme(app)

    socket_client = SocketClient()
    # Thử kết nối sẵn tới Server mặc định (nếu chưa bật, LoginDialog vẫn cho sửa Host/Port)
    socket_client.connect_to_server(timeout=1.5)

    try:
        while True:
            login_dlg = LoginDialog(socket_client=socket_client)
            if login_dlg.exec() != LoginDialog.DialogCode.Accepted or not login_dlg.login_result:
                break

            session_info = dict(login_dlg.login_result)
            window = MainWindow(session_info=session_info, socket_client=socket_client)

            reopen_login = {"flag": False}

            def _back_to_login(_reason: str = "") -> None:
                reopen_login["flag"] = True
                window.close()

            window.logout_requested.connect(_back_to_login)
            window.session_revoked.connect(_back_to_login)

            window.show()
            window.load_initial_data()
            app.exec()

            if not reopen_login["flag"]:
                break
            if not socket_client.is_connected():
                socket_client.connect_to_server(timeout=1.5)
    finally:
        socket_client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(run_app())
