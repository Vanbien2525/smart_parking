"""Bộ định tuyến Action TCP Socket, kiểm tra RBAC và chặn thao tác ghi cho vai trò Admin."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Optional, Set

from server.core.file_logger import FileLogger
from server.core.session_manager import ClientSession
from shared.constants import LogLevels, Roles
from shared.protocol import Actions, make_response

HandlerFn = Callable[[ClientSession, Dict[str, Any]], Any]


class BusinessError(ValueError):
    """Ngoại lệ nghiệp vụ để trả thông báo lỗi rõ ràng về Client mà không ghi lỗi hệ thống."""


@dataclass(frozen=True)
class RouteEntry:
    action: str
    handler: HandlerFn
    allowed_roles: Optional[Set[str]]
    is_mutation: bool
    require_auth: bool


class Router:
    """Điều phối request theo `action`, kiểm tra đăng nhập, RBAC và chế độ chỉ-xem của Admin."""

    def __init__(self, file_logger: Optional[FileLogger] = None) -> None:
        self._routes: Dict[str, RouteEntry] = {}
        self._file_logger = file_logger
        self.register(
            Actions.PING,
            self._handle_ping,
            allowed_roles=None,
            is_mutation=False,
            require_auth=False,
        )

    @staticmethod
    def _handle_ping(session: ClientSession, _payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "pong": True,
            "server_time": datetime.now().isoformat(timespec="seconds"),
            "session_id": session.session_id,
        }

    def register(
        self,
        action: str,
        handler_fn: HandlerFn,
        allowed_roles: Optional[Iterable[str]] = None,
        is_mutation: bool = False,
        require_auth: bool = True,
    ) -> None:
        """Đăng ký một handler cho `action` cụ thể."""
        role_set = {str(r) for r in allowed_roles} if allowed_roles is not None else None
        self._routes[action] = RouteEntry(
            action=action,
            handler=handler_fn,
            allowed_roles=role_set,
            is_mutation=bool(is_mutation),
            require_auth=bool(require_auth),
        )

    def has_action(self, action: str) -> bool:
        return action in self._routes

    def list_actions(self) -> list[str]:
        return sorted(self._routes.keys())

    def dispatch(
        self,
        session: ClientSession,
        action: str,
        payload: Optional[Dict[str, Any]],
        request_id: str,
    ) -> Dict[str, Any]:
        """Kiểm tra quyền và thực thi handler tương ứng với `action`."""
        route = self._routes.get(action)
        if route is None:
            return make_response(
                request_id,
                ok=False,
                error=f"Chức năng '{action}' chưa được đăng ký trên Server.",
                error_code="UNKNOWN_ACTION",
            )

        if route.require_auth and session.ma_tai_khoan is None:
            return make_response(
                request_id,
                ok=False,
                error="Phiên chưa đăng nhập. Vui lòng đăng nhập để tiếp tục.",
                error_code="UNAUTHORIZED",
            )

        # Quy tắc bất biến: Admin tuyệt đối chỉ xem, chặn mọi thao tác ghi/thay đổi dữ liệu
        if session.vai_tro == Roles.ADMIN.value and route.is_mutation:
            if self._file_logger is not None:
                self._file_logger.log_error(
                    noi_dung=f"Chặn thao tác ghi từ Admin '{session.ten_dang_nhap}' vào action '{action}'",
                    muc_do=LogLevels.WARN,
                    nguon="router.rbac",
                    ma_loi="ADMIN_READ_ONLY",
                    chi_tiet={"ma_tai_khoan": session.ma_tai_khoan, "action": action},
                )
            return make_response(
                request_id,
                ok=False,
                error="Vai trò Admin chỉ có quyền xem, không được phép thực hiện thao tác thay đổi dữ liệu.",
                error_code="ADMIN_READ_ONLY",
            )

        if route.allowed_roles is not None and session.vai_tro not in route.allowed_roles:
            return make_response(
                request_id,
                ok=False,
                error=f"Tài khoản vai trò '{session.vai_tro}' không có quyền gọi '{action}'.",
                error_code="FORBIDDEN",
            )

        safe_payload = payload if isinstance(payload, dict) else {}
        try:
            result = route.handler(session, safe_payload)
            return make_response(request_id, ok=True, data=result)
        except PermissionError as exc:
            return make_response(
                request_id,
                ok=False,
                error=str(exc) or "Không có quyền truy cập.",
                error_code="FORBIDDEN",
            )
        except (BusinessError, ValueError) as exc:
            return make_response(
                request_id,
                ok=False,
                error=str(exc),
                error_code="BAD_REQUEST",
            )
        except Exception as exc:
            if self._file_logger is not None:
                self._file_logger.log_error(
                    noi_dung=f"Lỗi xử lý action '{action}': {exc}",
                    muc_do=LogLevels.ERROR,
                    nguon="router.dispatch",
                    ma_loi="HANDLER_EXCEPTION",
                    chi_tiet={
                        "action": action,
                        "session_id": session.session_id,
                        "ma_tai_khoan": session.ma_tai_khoan,
                        "error": repr(exc),
                    },
                )
            return make_response(
                request_id,
                ok=False,
                error=f"Lỗi hệ thống khi xử lý '{action}': {exc}",
                error_code="INTERNAL_ERROR",
            )
