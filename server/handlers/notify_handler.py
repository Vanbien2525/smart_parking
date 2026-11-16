"""Handler Thông báo & Cảnh báo Khẩn cấp — Workflow 01 (1.10, 2.7, 3.6)."""

import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from server.core.file_logger import FileLogger
from server.core.router import BusinessError, Router
from server.core.session_manager import ClientSession, SessionManager
from shared.constants import (
    AccountStatus,
    NotifyAckStatus,
    NotifyLevels,
    NotifyRecipientGroups,
    NotifySendStatus,
    NotifyTypes,
    Roles,
)
from shared.protocol import Actions, PushEvents

_VALID_TYPES = {e.value for e in NotifyTypes}
_VALID_LEVELS = {e.value for e in NotifyLevels}
_VALID_GROUPS = {e.value for e in NotifyRecipientGroups}


def _resolve_db(db_provider: Any = None) -> Any:
    if db_provider is not None:
        return db_provider
    from server import db as default_db

    return default_db


class NotifyHandler:
    """Xử lý nghiệp vụ soạn nháp, phát thông báo Push, retry, đọc và ACK (Workflow 01)."""

    def __init__(
        self,
        session_manager: SessionManager,
        file_logger: FileLogger,
        db_provider: Any = None,
    ) -> None:
        self.session_manager = session_manager
        self.file_logger = file_logger
        self._db_provider = db_provider
        self._draft_lock = threading.RLock()
        self._drafts: Dict[str, Dict[str, Any]] = {}

    def _db(self) -> Any:
        return _resolve_db(self._db_provider)

    @staticmethod
    def _validate_notify_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
        tieu_de = str(payload.get("tieu_de") or "").strip()
        noi_dung = str(payload.get("noi_dung") or "").strip()
        loai_thong_bao = str(payload.get("loai_thong_bao") or NotifyTypes.BAO_TRI_KY_THUAT.value).strip()
        muc_do = str(payload.get("muc_do") or NotifyLevels.BINH_THUONG.value).strip()
        nhom_nguoi_nhan = str(payload.get("nhom_nguoi_nhan") or NotifyRecipientGroups.TAT_CA.value).strip()
        ma_tai_khoan_ca_nhan = payload.get("ma_tai_khoan_ca_nhan")

        if not tieu_de:
            raise BusinessError("Vui lòng nhập tiêu đề thông báo.")
        if len(tieu_de) > 200:
            raise BusinessError("Tiêu đề thông báo tối đa 200 ký tự.")
        if not noi_dung:
            raise BusinessError("Vui lòng nhập nội dung thông báo.")
        if len(noi_dung) > 1000:
            raise BusinessError("Nội dung thông báo tối đa 1000 ký tự.")
        if loai_thong_bao not in _VALID_TYPES:
            raise BusinessError(f"Loại thông báo không hợp lệ: '{loai_thong_bao}'.")
        if muc_do not in _VALID_LEVELS:
            raise BusinessError(f"Mức độ thông báo không hợp lệ: '{muc_do}'.")
        if nhom_nguoi_nhan not in _VALID_GROUPS:
            raise BusinessError(f"Nhóm người nhận không hợp lệ: '{nhom_nguoi_nhan}'.")

        parsed_target_id: Optional[int] = None
        if nhom_nguoi_nhan == NotifyRecipientGroups.CA_NHAN.value:
            if ma_tai_khoan_ca_nhan in (None, ""):
                raise BusinessError("Vui lòng chọn hoặc nhập mã tài khoản khi gửi cá nhân.")
            try:
                parsed_target_id = int(ma_tai_khoan_ca_nhan)
            except (TypeError, ValueError) as exc:
                raise BusinessError("Mã tài khoản cá nhân phải là số nguyên.") from exc

        return {
            "tieu_de": tieu_de,
            "noi_dung": noi_dung,
            "loai_thong_bao": loai_thong_bao,
            "muc_do": muc_do,
            "nhom_nguoi_nhan": nhom_nguoi_nhan,
            "ma_tai_khoan_ca_nhan": parsed_target_id,
        }

    # ==========================================================================
    # 1.10.1: SOẠN & LƯU NHÁP THÔNG BÁO (RAM SERVER)
    # ==========================================================================
    def handle_save_draft(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.10.1: Lưu bản nháp thông báo tạm trên RAM Server để Quản lý kiểm tra trước khi phát."""
        fields = self._validate_notify_fields(payload)
        draft_id = str(payload.get("draft_id") or "").strip() or f"DRAFT-{uuid.uuid4().hex[:8].upper()}"
        now_iso = datetime.now().isoformat(timespec="seconds")

        draft_record: Dict[str, Any] = {
            "draft_id": draft_id,
            "ma_tai_khoan_tao": session.ma_tai_khoan,
            "ma_nguoi_gui": session.ma_nhan_vien,
            "ten_nguoi_tao": session.ho_ten or session.ten_dang_nhap or "",
            "updated_at": now_iso,
            **fields,
        }
        with self._draft_lock:
            self._drafts[draft_id] = draft_record
            total_drafts = len(self._drafts)

        return {
            "draft_id": draft_id,
            "draft": dict(draft_record),
            "total_drafts": total_drafts,
        }

    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        with self._draft_lock:
            item = self._drafts.get(draft_id)
            return dict(item) if item is not None else None

    def list_drafts(self, ma_tai_khoan: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._draft_lock:
            items = list(self._drafts.values())
        if ma_tai_khoan is not None:
            items = [d for d in items if d.get("ma_tai_khoan_tao") == ma_tai_khoan]
        items.sort(key=lambda d: str(d.get("updated_at") or ""), reverse=True)
        return [dict(d) for d in items]

    def _resolve_sender_nhan_vien_id(self, db: Any, session: ClientSession) -> int:
        if session.ma_nhan_vien is not None:
            return int(session.ma_nhan_vien)
        if session.ma_tai_khoan is not None:
            row = db.fetch_one(
                "SELECT ma_nhan_vien FROM nhan_vien WHERE ma_tai_khoan = %s",
                (int(session.ma_tai_khoan),),
            )
            if row and row.get("ma_nhan_vien") is not None:
                return int(row["ma_nhan_vien"])
        raise BusinessError("Tài khoản hiện tại chưa gắn hồ sơ nhân viên/quản lý để gửi thông báo.")

    @staticmethod
    def _resolve_push_event(loai_thong_bao: str, muc_do: str) -> str:
        if (
            loai_thong_bao == NotifyTypes.KHAN_CAP.value
            or muc_do == NotifyLevels.KHAN_CAP.value
        ):
            return PushEvents.EMERGENCY_ALERT
        return PushEvents.NORMAL_NOTIFICATION

    def _fetch_target_accounts(
        self,
        db: Any,
        nhom_nguoi_nhan: str,
        ma_tai_khoan_ca_nhan: Optional[int],
        sender_account_id: Optional[int],
    ) -> List[Dict[str, Any]]:
        if nhom_nguoi_nhan == NotifyRecipientGroups.CA_NHAN.value:
            return db.fetch_all(
                """
                SELECT ma_tai_khoan, ten_dang_nhap, vai_tro
                FROM tai_khoan
                WHERE ma_tai_khoan = %s AND trang_thai = %s
                """,
                (ma_tai_khoan_ca_nhan, AccountStatus.HOAT_DONG.value),
            )
        if nhom_nguoi_nhan == NotifyRecipientGroups.CU_DAN.value:
            return db.fetch_all(
                """
                SELECT ma_tai_khoan, ten_dang_nhap, vai_tro
                FROM tai_khoan
                WHERE vai_tro = %s AND trang_thai = %s
                ORDER BY ma_tai_khoan ASC
                """,
                (Roles.CU_DAN.value, AccountStatus.HOAT_DONG.value),
            )
        if nhom_nguoi_nhan == NotifyRecipientGroups.NHAN_VIEN.value:
            return db.fetch_all(
                """
                SELECT ma_tai_khoan, ten_dang_nhap, vai_tro
                FROM tai_khoan
                WHERE vai_tro = %s AND trang_thai = %s
                ORDER BY ma_tai_khoan ASC
                """,
                (Roles.NHAN_VIEN.value, AccountStatus.HOAT_DONG.value),
            )
        # tat_ca: toàn bộ tài khoản đang hoạt động (trừ chính người gửi nếu có tài khoản khác)
        rows = db.fetch_all(
            """
            SELECT ma_tai_khoan, ten_dang_nhap, vai_tro
            FROM tai_khoan
            WHERE trang_thai = %s
            ORDER BY ma_tai_khoan ASC
            """,
            (AccountStatus.HOAT_DONG.value,),
        )
        if sender_account_id is not None:
            filtered = [r for r in rows if int(r["ma_tai_khoan"]) != int(sender_account_id)]
            if filtered:
                return filtered
        return rows

    def _has_broken_socket(self, account_id: int, push_result: Dict[str, List[int]]) -> bool:
        """Kiểm tra tài khoản có đang mở session nhưng gửi socket thất bại hay không."""
        active_for_user = [
            s
            for s in self.session_manager.list_sessions(authenticated_only=True)
            if s.get("ma_tai_khoan") == account_id
        ]
        return bool(active_for_user) and account_id in push_result.get("gui_that_bai", [])

    # ==========================================================================
    # 1.10.2: PHÁT THÔNG BÁO PUSH REAL-TIME & THỬ GỬI LẠI (WORKFLOW 01)
    # ==========================================================================
    def handle_publish(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.10.2: Lưu CSDL bảng 17-18, đẩy Socket Push tới người nhận và tổng hợp trạng thái."""
        draft_id = str(payload.get("draft_id") or "").strip()
        merged_payload = dict(payload)
        if draft_id:
            draft = self.get_draft(draft_id)
            if draft is None:
                raise BusinessError(f"Không tìm thấy bản nháp '{draft_id}'.")
            for k in (
                "tieu_de",
                "noi_dung",
                "loai_thong_bao",
                "muc_do",
                "nhom_nguoi_nhan",
                "ma_tai_khoan_ca_nhan",
            ):
                if merged_payload.get(k) in (None, ""):
                    merged_payload[k] = draft.get(k)

        fields = self._validate_notify_fields(merged_payload)
        db = self._db()
        ma_nguoi_gui = self._resolve_sender_nhan_vien_id(db, session)

        raw_sim_fail = payload.get("simulate_failed_account_ids") or []
        sim_failed_ids = {int(x) for x in raw_sim_fail if x not in (None, "")}

        with db.transaction() as tx:
            targets = self._fetch_target_accounts(
                tx,
                nhom_nguoi_nhan=fields["nhom_nguoi_nhan"],
                ma_tai_khoan_ca_nhan=fields["ma_tai_khoan_ca_nhan"],
                sender_account_id=session.ma_tai_khoan,
            )
            if not targets:
                raise BusinessError("Không tìm thấy tài khoản người nhận đang hoạt động phù hợp.")

            thong_bao = tx.execute_returning(
                """
                INSERT INTO thong_bao (
                    ma_nguoi_gui, tieu_de, noi_dung, loai_thong_bao, muc_do, nhom_nguoi_nhan, thoi_gian_gui
                ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING ma_thong_bao, ma_nguoi_gui, tieu_de, noi_dung, loai_thong_bao, muc_do, nhom_nguoi_nhan, thoi_gian_gui
                """,
                (
                    ma_nguoi_gui,
                    fields["tieu_de"],
                    fields["noi_dung"],
                    fields["loai_thong_bao"],
                    fields["muc_do"],
                    fields["nhom_nguoi_nhan"],
                ),
            )
            ma_thong_bao = int(thong_bao["ma_thong_bao"])

            push_event = self._resolve_push_event(fields["loai_thong_bao"], fields["muc_do"])
            push_data = {
                "ma_thong_bao": ma_thong_bao,
                "tieu_de": fields["tieu_de"],
                "noi_dung": fields["noi_dung"],
                "loai_thong_bao": fields["loai_thong_bao"],
                "muc_do": fields["muc_do"],
                "nhom_nguoi_nhan": fields["nhom_nguoi_nhan"],
                "ten_nguoi_gui": session.ho_ten or session.ten_dang_nhap or "Ban Quản Lý",
                "thoi_gian_gui": str(thong_bao.get("thoi_gian_gui") or datetime.now().isoformat(timespec="seconds")),
            }

            push_candidate_ids = [
                int(t["ma_tai_khoan"])
                for t in targets
                if int(t["ma_tai_khoan"]) not in sim_failed_ids
            ]
            push_result = self.session_manager.push_to_users(push_candidate_ids, push_event, push_data)

            recipient_rows: List[Dict[str, Any]] = []
            success_count = 0
            failed_count = 0
            online_pushed_count = 0

            for target in targets:
                acc_id = int(target["ma_tai_khoan"])
                is_failed = acc_id in sim_failed_ids or self._has_broken_socket(acc_id, push_result)
                send_status = (
                    NotifySendStatus.GUI_THAT_BAI.value
                    if is_failed
                    else NotifySendStatus.GUI_THANH_CONG.value
                )
                if is_failed:
                    failed_count += 1
                else:
                    success_count += 1
                    if acc_id in push_result.get("gui_thanh_cong", []):
                        online_pushed_count += 1

                rec = tx.execute_returning(
                    """
                    INSERT INTO nguoi_nhan_thong_bao (
                        ma_thong_bao, ma_tai_khoan_nhan, trang_thai_gui, so_lan_gui_lai,
                        trang_thai_tiep_nhan, da_doc, thoi_gian_tiep_nhan
                    ) VALUES (%s, %s, %s, 0, %s, FALSE, NULL)
                    RETURNING ma_tiep_nhan, ma_thong_bao, ma_tai_khoan_nhan, trang_thai_gui,
                              so_lan_gui_lai, trang_thai_tiep_nhan, da_doc, thoi_gian_tiep_nhan
                    """,
                    (
                        ma_thong_bao,
                        acc_id,
                        send_status,
                        NotifyAckStatus.CHUA_TIEP_NHAN.value,
                    ),
                )
                recipient_rows.append(rec)

        if draft_id:
            with self._draft_lock:
                self._drafts.pop(draft_id, None)

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="PHAT_THONG_BAO",
            doi_tuong="thong_bao",
            ma_doi_tuong=ma_thong_bao,
            gia_tri_cu=None,
            gia_tri_moi={
                "tieu_de": fields["tieu_de"],
                "loai_thong_bao": fields["loai_thong_bao"],
                "muc_do": fields["muc_do"],
                "nhom_nguoi_nhan": fields["nhom_nguoi_nhan"],
                "tong_nguoi_nhan": len(recipient_rows),
                "gui_thanh_cong": success_count,
                "gui_that_bai": failed_count,
            },
        )

        return {
            "thong_bao": thong_bao,
            "push_event": push_event,
            "tong_nguoi_nhan": len(recipient_rows),
            "so_gui_thanh_cong": success_count,
            "so_gui_that_bai": failed_count,
            "so_online_da_push": online_pushed_count,
            "so_da_tiep_nhan": 0,
            "so_chua_tiep_nhan": len(recipient_rows),
            "recipients": recipient_rows,
        }

    def handle_retry_failed(
        self, session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.10.2 (Retry): Gửi lại thông báo tới các người nhận gui_that_bai (hoặc chua_tiep_nhan)."""
        raw_id = payload.get("ma_thong_bao")
        if raw_id in (None, ""):
            raise BusinessError("Thiếu `ma_thong_bao` để thử gửi lại.")
        try:
            ma_thong_bao = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise BusinessError("`ma_thong_bao` phải là số nguyên.") from exc

        include_unacked = bool(payload.get("include_unacked", False))
        raw_sim_fail = payload.get("simulate_failed_account_ids") or []
        sim_failed_ids = {int(x) for x in raw_sim_fail if x not in (None, "")}

        db = self._db()
        with db.transaction() as tx:
            tb = tx.fetch_one(
                """
                SELECT ma_thong_bao, ma_nguoi_gui, tieu_de, noi_dung, loai_thong_bao,
                       muc_do, nhom_nguoi_nhan, thoi_gian_gui
                FROM thong_bao
                WHERE ma_thong_bao = %s
                """,
                (ma_thong_bao,),
            )
            if tb is None:
                raise BusinessError(f"Không tìm thấy thông báo mã {ma_thong_bao}.")

            rows = tx.fetch_all(
                """
                SELECT ma_tiep_nhan, ma_thong_bao, ma_tai_khoan_nhan, trang_thai_gui,
                       so_lan_gui_lai, trang_thai_tiep_nhan, da_doc, thoi_gian_tiep_nhan
                FROM nguoi_nhan_thong_bao
                WHERE ma_thong_bao = %s
                ORDER BY ma_tiep_nhan ASC
                """,
                (ma_thong_bao,),
            )
            retry_targets = [
                r
                for r in rows
                if r.get("trang_thai_gui") == NotifySendStatus.GUI_THAT_BAI.value
                or (
                    include_unacked
                    and r.get("trang_thai_tiep_nhan") == NotifyAckStatus.CHUA_TIEP_NHAN.value
                )
            ]
            if not retry_targets:
                return {
                    "ma_thong_bao": ma_thong_bao,
                    "retried_count": 0,
                    "so_gui_thanh_cong": 0,
                    "so_gui_that_bai": 0,
                    "recipients": [],
                }

            push_event = self._resolve_push_event(
                str(tb.get("loai_thong_bao") or ""),
                str(tb.get("muc_do") or ""),
            )
            push_data = {
                "ma_thong_bao": ma_thong_bao,
                "tieu_de": tb.get("tieu_de"),
                "noi_dung": tb.get("noi_dung"),
                "loai_thong_bao": tb.get("loai_thong_bao"),
                "muc_do": tb.get("muc_do"),
                "nhom_nguoi_nhan": tb.get("nhom_nguoi_nhan"),
                "ten_nguoi_gui": session.ho_ten or session.ten_dang_nhap or "Ban Quản Lý",
                "thoi_gian_gui": str(tb.get("thoi_gian_gui") or ""),
                "is_retry": True,
            }

            candidate_ids = [
                int(r["ma_tai_khoan_nhan"])
                for r in retry_targets
                if int(r["ma_tai_khoan_nhan"]) not in sim_failed_ids
            ]
            push_result = self.session_manager.push_to_users(candidate_ids, push_event, push_data)

            updated_rows: List[Dict[str, Any]] = []
            success_count = 0
            failed_count = 0
            for rec in retry_targets:
                acc_id = int(rec["ma_tai_khoan_nhan"])
                is_failed = acc_id in sim_failed_ids or self._has_broken_socket(acc_id, push_result)
                new_status = (
                    NotifySendStatus.GUI_THAT_BAI.value
                    if is_failed
                    else NotifySendStatus.GUI_THANH_CONG.value
                )
                if is_failed:
                    failed_count += 1
                else:
                    success_count += 1

                updated = tx.execute_returning(
                    """
                    UPDATE nguoi_nhan_thong_bao
                    SET so_lan_gui_lai = so_lan_gui_lai + 1,
                        trang_thai_gui = %s
                    WHERE ma_tiep_nhan = %s
                    RETURNING ma_tiep_nhan, ma_thong_bao, ma_tai_khoan_nhan, trang_thai_gui,
                              so_lan_gui_lai, trang_thai_tiep_nhan, da_doc, thoi_gian_tiep_nhan
                    """,
                    (new_status, int(rec["ma_tiep_nhan"])),
                )
                updated_rows.append(updated)

        self.file_logger.log_audit(
            ma_tai_khoan=session.ma_tai_khoan,
            ten_dang_nhap=session.ten_dang_nhap or "",
            vai_tro=session.vai_tro or "",
            loai_thao_tac="GUI_LAI_THONG_BAO",
            doi_tuong="thong_bao",
            ma_doi_tuong=ma_thong_bao,
            gia_tri_cu=None,
            gia_tri_moi={
                "retried_count": len(updated_rows),
                "so_gui_thanh_cong": success_count,
                "so_gui_that_bai": failed_count,
            },
        )

        return {
            "ma_thong_bao": ma_thong_bao,
            "retried_count": len(updated_rows),
            "so_gui_thanh_cong": success_count,
            "so_gui_that_bai": failed_count,
            "recipients": updated_rows,
        }

    # ==========================================================================
    # 1.10.3: XEM THÔNG BÁO ĐÃ GỬI & THỐNG KÊ GỬI / TIẾP NHẬN / ĐỌC
    # ==========================================================================
    def handle_list_sent(
        self, _session: ClientSession, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1.10.3: Danh sách thông báo đã phát hành kèm thống kê chi tiết từ nguoi_nhan_thong_bao."""
        db = self._db()
        nhom = str(payload.get("nhom_nguoi_nhan") or "").strip()
        loai = str(payload.get("loai_thong_bao") or "").strip()
        muc_do = str(payload.get("muc_do") or "").strip()
        start_date = str(payload.get("start_date") or "").strip()
        end_date = str(payload.get("end_date") or "").strip()
        keyword = str(payload.get("keyword") or "").strip()
        ma_thong_bao = payload.get("ma_thong_bao")

        conditions = ["1=1"]
        params: List[Any] = []
        if ma_thong_bao not in (None, ""):
            conditions.append("tb.ma_thong_bao = %s")
            params.append(int(ma_thong_bao))
        if nhom:
            conditions.append("tb.nhom_nguoi_nhan = %s")
            params.append(nhom)
        if loai:
            conditions.append("tb.loai_thong_bao = %s")
            params.append(loai)
        if muc_do:
            conditions.append("tb.muc_do = %s")
            params.append(muc_do)
        if start_date:
            conditions.append("DATE(tb.thoi_gian_gui) >= %s")
            params.append(start_date[:10])
        if end_date:
            conditions.append("DATE(tb.thoi_gian_gui) <= %s")
            params.append(end_date[:10])
        if keyword:
            like = f"%{keyword}%"
            conditions.append("(tb.tieu_de ILIKE %s OR tb.noi_dung ILIKE %s)")
            params.extend([like, like])

        where_sql = " AND ".join(conditions)
        notices = db.fetch_all(
            f"""
            SELECT tb.ma_thong_bao,
                   tb.ma_nguoi_gui,
                   COALESCE(nv.ho_ten, 'Ban Quản Lý') AS ten_nguoi_gui,
                   tb.tieu_de,
                   tb.noi_dung,
                   tb.loai_thong_bao,
                   tb.muc_do,
                   tb.nhom_nguoi_nhan,
                   tb.thoi_gian_gui
            FROM thong_bao tb
            LEFT JOIN nhan_vien nv ON nv.ma_nhan_vien = tb.ma_nguoi_gui
            WHERE {where_sql}
            ORDER BY tb.thoi_gian_gui DESC, tb.ma_thong_bao DESC
            """,
            tuple(params),
        )

        rec_rows = db.fetch_all(
            """
            SELECT nn.ma_tiep_nhan,
                   nn.ma_thong_bao,
                   nn.ma_tai_khoan_nhan,
                   tk.ten_dang_nhap,
                   tk.vai_tro,
                   COALESCE(cd.ho_ten, nv.ho_ten, tk.ten_dang_nhap) AS ho_ten_nhan,
                   nn.trang_thai_gui,
                   nn.so_lan_gui_lai,
                   nn.trang_thai_tiep_nhan,
                   nn.da_doc,
                   nn.thoi_gian_tiep_nhan
            FROM nguoi_nhan_thong_bao nn
            JOIN tai_khoan tk ON tk.ma_tai_khoan = nn.ma_tai_khoan_nhan
            LEFT JOIN cu_dan cd ON cd.ma_tai_khoan = tk.ma_tai_khoan
            LEFT JOIN nhan_vien nv ON nv.ma_tai_khoan = tk.ma_tai_khoan
            ORDER BY nn.ma_tiep_nhan ASC
            """
        )

        rec_by_notice: Dict[int, List[Dict[str, Any]]] = {}
        for rec in rec_rows:
            rec_by_notice.setdefault(int(rec["ma_thong_bao"]), []).append(rec)

        items: List[Dict[str, Any]] = []
        for tb in notices:
            nid = int(tb["ma_thong_bao"])
            recs = rec_by_notice.get(nid, [])
            tong = len(recs)
            ok_cnt = sum(1 for r in recs if r.get("trang_thai_gui") == NotifySendStatus.GUI_THANH_CONG.value)
            fail_cnt = sum(1 for r in recs if r.get("trang_thai_gui") == NotifySendStatus.GUI_THAT_BAI.value)
            ack_cnt = sum(1 for r in recs if r.get("trang_thai_tiep_nhan") == NotifyAckStatus.DA_TIEP_NHAN.value)
            read_cnt = sum(1 for r in recs if bool(r.get("da_doc")))
            retry_sum = sum(int(r.get("so_lan_gui_lai") or 0) for r in recs)
            items.append(
                {
                    **tb,
                    "tong_nguoi_nhan": tong,
                    "so_gui_thanh_cong": ok_cnt,
                    "so_gui_that_bai": fail_cnt,
                    "so_da_tiep_nhan": ack_cnt,
                    "so_chua_tiep_nhan": max(0, tong - ack_cnt),
                    "so_da_doc": read_cnt,
                    "tong_so_lan_gui_lai": retry_sum,
                    "recipients": recs,
                }
            )

        return {
            "total": len(items),
            "items": items,
        }


def register_notify_routes(
    router: Router,
    session_manager: SessionManager,
    file_logger: FileLogger,
    db_provider: Any = None,
) -> NotifyHandler:
    """Đăng ký các route Thông báo (1.10, 2.7, 3.6) lên Router."""
    handler = NotifyHandler(
        session_manager=session_manager,
        file_logger=file_logger,
        db_provider=db_provider,
    )
    router.register(
        Actions.NOTIFY_SAVE_DRAFT,
        handler.handle_save_draft,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.NOTIFY_PUBLISH,
        handler.handle_publish,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.NOTIFY_RETRY_FAILED,
        handler.handle_retry_failed,
        allowed_roles=[Roles.QUAN_LY.value],
        is_mutation=True,
        require_auth=True,
    )
    router.register(
        Actions.NOTIFY_LIST_SENT,
        handler.handle_list_sent,
        allowed_roles=[Roles.QUAN_LY.value, Roles.ADMIN.value],
        is_mutation=False,
        require_auth=True,
    )
    return handler
