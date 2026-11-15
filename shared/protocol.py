"""Giao thức TCP Socket dùng chung giữa Client (PySide6) và Server (ThreadingTCPServer).

Đóng gói khung tin theo chuẩn Length-Prefix:
    [4-byte unsigned int big-endian (length)] + [UTF-8 JSON payload]
Hỗ trợ song song 2 chiều REQUEST/RESPONSE (theo request_id UUID) và Server PUSH trên 1 kết nối TCP.
"""

import json
import socket
import struct
import uuid
from typing import Any, Dict, Optional

HEADER_STRUCT = struct.Struct("!I")
MAX_FRAME_SIZE = 16 * 1024 * 1024  # 16 MB (hỗ trợ ảnh cà-vẹt / camera base64)


class MsgType:
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    PUSH = "PUSH"


class Actions:
    # --- 1. Hệ thống, Dashboard & Admin (Biên - Leader) ---
    PING = "system.ping"
    DASHBOARD_SUMMARY = "manager.dashboard_summary"            # 1.1.1 - 1.1.4
    ADMIN_LIST_ACCOUNTS = "admin.list_accounts"                # 4.2.1
    ADMIN_GET_ACCOUNT = "admin.get_account"                    # 4.2.2, 4.2.3, 4.2.4
    ADMIN_ACCOUNT_HISTORY = "admin.account_status_history"     # 4.2.5
    ADMIN_LIST_SESSIONS = "admin.list_sessions"                # 4.3.1
    ADMIN_GET_SESSION = "admin.get_session"                    # 4.3.2
    ADMIN_QUERY_LOGS = "admin.query_logs"                      # 4.4.1, 4.4.2, 4.4.3

    # --- 2. Xác thực, Nhân sự, Hạ tầng, Bảng giá & Hóa đơn (Hậu) ---
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_CHANGE_PASSWORD = "auth.change_password"
    AUTH_REQUEST_OTP = "auth.request_otp"
    AUTH_RESEND_OTP = "auth.resend_otp"
    AUTH_VERIFY_OTP = "auth.verify_otp"
    AUTH_RESET_PASSWORD = "auth.reset_password"
    AUTH_GET_PROFILE = "auth.get_profile"
    AUTH_UPDATE_PROFILE = "auth.update_profile"

    HR_CREATE_RESIDENT = "hr.create_resident"
    HR_SEARCH_RESIDENTS = "hr.search_residents"
    HR_GET_RESIDENT = "hr.get_resident"
    HR_DEACTIVATE_RESIDENT = "hr.deactivate_resident"
    HR_ACTIVATE_RESIDENT = "hr.activate_resident"
    HR_CREATE_STAFF = "hr.create_staff"
    HR_LIST_STAFF = "hr.list_staff"
    HR_UPDATE_STAFF = "hr.update_staff"
    HR_DEACTIVATE_STAFF = "hr.deactivate_staff"
    HR_ACTIVATE_STAFF = "hr.activate_staff"

    INFRA_CREATE_ZONE = "infra.create_zone"
    INFRA_LIST_ZONES = "infra.list_zones"
    INFRA_UPDATE_ZONE = "infra.update_zone"
    INFRA_DEACTIVATE_ZONE = "infra.deactivate_zone"
    INFRA_ACTIVATE_ZONE = "infra.activate_zone"
    INFRA_CREATE_SLOT = "infra.create_slot"
    INFRA_LIST_SLOTS = "infra.list_slots"
    INFRA_UPDATE_SLOT = "infra.update_slot"
    INFRA_DEACTIVATE_SLOT = "infra.deactivate_slot"
    INFRA_ACTIVATE_SLOT = "infra.activate_slot"

    PRICING_CREATE = "pricing.create"
    PRICING_UPDATE = "pricing.update"
    PRICING_DEACTIVATE = "pricing.deactivate"
    PRICING_LIST = "pricing.list"

    BILLING_GENERATE_MONTHLY = "billing.generate_monthly_invoices"
    BILLING_MY_INVOICES = "billing.my_invoices"
    BILLING_SEARCH_MY_INVOICES = "billing.search_my_invoices"
    BILLING_GET_INVOICE_DETAIL = "billing.get_invoice_detail"
    BILLING_GET_RECEIPT = "billing.get_receipt"
    BILLING_CREATE_QR_PAYMENT = "billing.create_qr_payment"
    BILLING_COMPLETE_QR_PAYMENT = "billing.complete_qr_payment"
    BILLING_CONFIRM_CASH_PAYMENT = "billing.confirm_cash_payment"
    BILLING_LIST_RECEIVABLES = "billing.list_receivables"
    BILLING_LIST_COLLECTED = "billing.list_collected"
    BILLING_SUMMARY = "billing.summary"
    BILLING_GET_TRANSACTION_DETAIL = "billing.get_transaction_detail"

    # --- 3. Thông báo, Giám sát, Thống kê & Lịch sử (Minh) ---
    NOTIFY_SAVE_DRAFT = "notify.save_draft"
    NOTIFY_PUBLISH = "notify.publish"
    NOTIFY_RETRY_FAILED = "notify.retry_failed"
    NOTIFY_LIST_SENT = "notify.list_sent"
    NOTIFY_MY_INBOX = "notify.my_inbox"
    NOTIFY_GET_DETAIL = "notify.get_detail"
    NOTIFY_MARK_READ = "notify.mark_read"
    NOTIFY_ACK_EMERGENCY = "notify.ack_emergency"

    MONITOR_ACTIVE_PARKINGS = "monitor.active_parkings"
    MONITOR_AVAILABLE_SLOTS = "monitor.available_slots"
    MONITOR_OCCUPIED_SLOTS = "monitor.occupied_slots"
    MONITOR_OVERVIEW_MAP = "monitor.overview_map"

    STATS_VEHICLE_TURNS = "stats.vehicle_turns"
    STATS_RESIDENT_VS_VISITOR = "stats.resident_vs_visitor"
    STATS_REVENUE = "stats.revenue"

    HISTORY_ENTRIES = "history.entries"
    HISTORY_EXITS = "history.exits"
    HISTORY_SESSION_DETAIL = "history.session_detail"

    # --- 4. Phương tiện & Phân bổ chỗ đỗ (Long) ---
    VEHICLE_RESIDENT_LIST = "vehicle.resident_list"
    VEHICLE_SUBMIT_REQUEST = "vehicle.submit_request"
    VEHICLE_GET_CAVET = "vehicle.get_cavet"
    VEHICLE_MANAGER_LIST_REQUESTS = "vehicle.manager_list_requests"
    VEHICLE_GET_ASSIGNABLE = "vehicle.get_assignable"
    VEHICLE_APPROVE_REQUEST = "vehicle.approve_request"
    VEHICLE_REJECT_REQUEST = "vehicle.reject_request"
    VEHICLE_RESIDENT_LOOKUP = "vehicle.resident_lookup"

    SLOT_REQUEST_RESIDENT_DATA = "slot_request.resident_data"
    SLOT_REQUEST_SUBMIT = "slot_request.submit"
    SLOT_REQUEST_MANAGER_LIST = "slot_request.manager_list"
    SLOT_REQUEST_APPROVE = "slot_request.approve"
    SLOT_REQUEST_REJECT = "slot_request.reject"
    ALLOCATION_REVOKE = "allocation.revoke"

    # --- 5. Kiểm soát Cổng Vào/Ra & Ca trực (Ny) ---
    GATE_IN_CHECK = "gate.in_check"
    GATE_IN_CONFIRM = "gate.in_confirm"
    GATE_OUT_CHECK = "gate.out_check"
    GATE_OUT_CONFIRM = "gate.out_confirm"
    GATE_LOOKUP_VEHICLE = "gate.lookup_vehicle"
    SHIFT_GET_REVENUE = "shift.get_revenue"
    SHIFT_CLOSE = "shift.close"


class PushEvents:
    EMERGENCY_ALERT = "push.emergency_alert"
    NORMAL_NOTIFICATION = "push.normal_notification"
    SESSION_REVOKED = "push.session_revoked"
    PARKING_STATE_CHANGED = "push.parking_state_changed"


def make_request(
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Tạo gói tin REQUEST chuẩn."""
    return {
        "msg_type": MsgType.REQUEST,
        "request_id": request_id or str(uuid.uuid4()),
        "action": action,
        "payload": payload if payload is not None else {},
    }


def make_response(
    request_id: str,
    ok: bool,
    data: Any = None,
    error: Optional[str] = None,
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Tạo gói tin RESPONSE chuẩn khớp với request_id."""
    resp: Dict[str, Any] = {
        "msg_type": MsgType.RESPONSE,
        "request_id": request_id,
        "ok": ok,
        "data": data,
        "error": error,
    }
    if error_code:
        resp["error_code"] = error_code
    return resp


def make_push(event: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Tạo gói tin PUSH chủ động từ Server xuống Client."""
    return {
        "msg_type": MsgType.PUSH,
        "event": event,
        "data": data if data is not None else {},
    }


def _recv_exact(sock: socket.socket, num_bytes: int) -> Optional[bytes]:
    """Đọc chính xác num_bytes từ TCP stream, tránh cắt gói khi đọc mạng."""
    chunks = []
    remaining = num_bytes
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_frame(message: Dict[str, Any]) -> bytes:
    """Tuần tự hóa dict sang khung bytes [4B Length][UTF-8 JSON]."""
    raw = json.dumps(message, ensure_ascii=False, default=str).encode("utf-8")
    if len(raw) == 0 or len(raw) > MAX_FRAME_SIZE:
        raise ValueError(f"Kích thước gói tin vượt giới hạn cho phép: {len(raw)} bytes")
    return HEADER_STRUCT.pack(len(raw)) + raw


def send_frame(sock: socket.socket, message: Dict[str, Any]) -> None:
    """Gửi trọn vẹn một khung tin qua TCP socket."""
    sock.sendall(encode_frame(message))


def recv_frame(sock: socket.socket) -> Optional[Dict[str, Any]]:
    """Đọc trọn vẹn một khung tin từ TCP socket; trả về None nếu peer đã đóng kết nối."""
    header = _recv_exact(sock, HEADER_STRUCT.size)
    if not header:
        return None
    (length,) = HEADER_STRUCT.unpack(header)
    if length <= 0 or length > MAX_FRAME_SIZE:
        raise ValueError(f"Kích thước gói tin không hợp lệ: {length}")
    payload = _recv_exact(sock, length)
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))
