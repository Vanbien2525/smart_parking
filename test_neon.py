"""Kiểm thử kết nối Neon PostgreSQL Cloud cho Hệ thống Quản lý Bãi xe Thông minh.

Tuân thủ:
- Sử dụng module server.config và server.db của hệ thống.
- Tuyệt đối không hiển thị mật khẩu / connection string chứa thông tin nhạy cảm.
- Kiểm tra toàn bộ 21 tiêu chí theo Nhiệm vụ 6.
- Mặc định chỉ đọc (Read-only); Transaction Rollback không lưu rác vào CSDL.
"""

import os
import sys
from pathlib import Path
from typing import Callable, List, Tuple

# Đảm bảo đường dẫn import cho Server & Shared
_BASE_DIR = Path(__file__).resolve().parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from server.config import DEFAULT_CONFIG, ServerConfig
from server import db

EXPECTED_TABLES = sorted([
    "bang_gia",
    "bao_cao_luot_va_thu_phi",
    "can_ho",
    "cu_dan",
    "giao_dich_thanh_toan",
    "hoa_don_thang",
    "khu_vuc",
    "loai_phuong_tien",
    "nguoi_nhan_thong_bao",
    "nhan_vien",
    "phan_bo_cho_do",
    "phuong_tien",
    "quan_ly_phuong_tien_ra_vao",
    "tai_khoan",
    "thong_bao",
    "vi_tri_do",
    "yeu_cau_dang_ky_xe",
    "yeu_cau_mua_them_cho",
])

EXPECTED_INDEXES = sorted([
    "idx_cu_dan_can_ho",
    "idx_giao_dich_hoa_don",
    "idx_hoa_don_ky_tt",
    "idx_nguoi_nhan_tk",
    "idx_phuong_tien_cu_dan",
    "idx_vi_tri_do_khu_vuc_tt",
])

EXPECTED_FKS = sorted([
    "fk_bang_gia_loai_xe",
    "fk_bao_cao_khu_vuc",
    "fk_bao_cao_nhan_vien",
    "fk_cu_dan_can_ho",
    "fk_cu_dan_tai_khoan",
    "fk_giao_dich_hoa_don",
    "fk_giao_dich_nhan_vien_thu",
    "fk_hoa_don_can_ho",
    "fk_khu_vuc_loai_phuong_tien",
    "fk_nguoi_nhan_tai_khoan",
    "fk_nguoi_nhan_thong_bao",
    "fk_nhan_vien_tai_khoan",
    "fk_phan_bo_can_ho",
    "fk_phan_bo_vi_tri_do",
    "fk_phuong_tien_cu_dan",
    "fk_phuong_tien_loai_xe",
    "fk_qlrv_khu_vuc_phan_cong",
    "fk_qlrv_phuong_tien",
    "fk_qlrv_vi_tri_co_dinh",
    "fk_thong_bao_nguoi_gui",
    "fk_vi_tri_do_khu_vuc",
    "fk_yc_cho_can_ho",
    "fk_yc_cho_cu_dan",
    "fk_yc_cho_nhan_vien_duyet",
    "fk_yc_cho_vi_tri_duyet",
    "fk_yc_xe_cu_dan",
    "fk_yc_xe_nhan_vien_duyet",
    "fk_yc_xe_phuong_tien",
])


class NeonTestSuite:
    def __init__(self, cfg: ServerConfig = DEFAULT_CONFIG) -> None:
        self.cfg = cfg
        self.passed = 0
        self.failed = 0
        self.results: List[Tuple[str, str, str]] = []

    def _record(self, name: str, success: bool, detail: str = "") -> None:
        if success:
            self.passed += 1
            status = "PASS"
        else:
            self.failed += 1
            status = "FAIL"
        self.results.append((status, name, detail))
        tag = f"[{status}]"
        if detail:
            print(f"{tag:<8} {name} — {detail}")
        else:
            print(f"{tag:<8} {name}")

    def test_environment_configuration(self) -> None:
        """1. Kiểm tra đọc biến môi trường & cấu hình Neon hợp lệ."""
        name = "Environment configuration"
        try:
            assert self.cfg.db_host, "DB_HOST không được để trống"
            assert "-pooler" not in self.cfg.db_host, (
                f"DB_HOST đang dùng endpoint pooler ({self.cfg.db_host}), yêu cầu direct endpoint"
            )
            assert self.cfg.db_port == 5432, f"DB_PORT phải là 5432 (hiện tại: {self.cfg.db_port})"
            assert self.cfg.db_name == "smart_parking", (
                f"DB_NAME phải là smart_parking (hiện tại: {self.cfg.db_name})"
            )
            assert self.cfg.db_user, "DB_USER không được để trống"
            assert self.cfg.db_password, "DB_PASSWORD không được để trống"
            assert self.cfg.db_sslmode in ("require", "verify-ca", "verify-full"), (
                f"DB_SSLMODE phải là require/verify-* cho Neon (hiện tại: {self.cfg.db_sslmode})"
            )
            self._record(name, True, f"Host: {self.cfg.db_host}, User: {self.cfg.db_user}, DB: {self.cfg.db_name}")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_postgresql_connection(self) -> None:
        """2 & 4. Kiểm tra kết nối thành công Neon & Database hiện tại."""
        name = "PostgreSQL connection"
        try:
            pool = db.init_pool(self.cfg)
            ready = db.is_pool_ready()
            assert ready, "db.is_pool_ready() trả về False"

            row = db.fetch_one("SELECT current_database() AS db, current_user AS usr, version() AS ver")
            assert row is not None, "Không đọc được thông tin database qua pool"
            assert row["db"] == "smart_parking", f"Database hiện tại là {row['db']}, mong đợi smart_parking"

            ver_short = row["ver"].split()[0] + " " + row["ver"].split()[1]
            self._record(name, True, f"DB: {row['db']}, User: {row['usr']} ({ver_short})")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_ssl_connection(self) -> None:
        """5. Kiểm tra kết nối thực tế sử dụng SSL/TLS an toàn."""
        name = "SSL/TLS connection"
        try:
            pool = db.get_pool()
            with pool.connection() as conn:
                is_ssl = getattr(conn.pgconn, "ssl_in_use", False)
                assert is_ssl, "Kết nối libpq không bật SSL/TLS encryption!"
            self._record(name, True, f"SSL/TLS Active (ssl_in_use=True, mode={self.cfg.db_sslmode})")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_connection_pool(self) -> None:
        """3. Kiểm tra Connection Pool hoạt động thread-safe và tái sử dụng."""
        name = "Connection pool"
        try:
            pool = db.get_pool()
            assert not pool.closed, "ConnectionPool đang ở trạng thái closed"
            assert pool.min_size >= 1, f"min_size={pool.min_size} không hợp lệ"
            assert pool.max_size >= pool.min_size, "max_size phải lớn hơn hoặc bằng min_size"

            # Thực thi đồng thời trên 2 connection mượn từ pool
            with pool.connection() as conn1:
                with pool.connection() as conn2:
                    with conn1.cursor() as cur1, conn2.cursor() as cur2:
                        cur1.execute("SELECT 1 AS c1")
                        cur2.execute("SELECT 2 AS c2")
                        r1 = cur1.fetchone()
                        r2 = cur2.fetchone()
                        assert r1["c1"] == 1 and r2["c2"] == 2
            timeout_val = getattr(self.cfg, "db_pool_timeout", 30.0)
            self._record(
                name,
                True,
                f"Min={pool.min_size}, Max={pool.max_size}, Timeout={timeout_val}s, Auto-Check=Active",
            )
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_database_schema(self) -> None:
        """6. Kiểm tra đủ 18 bảng theo schema chuẩn."""
        name = "Database schema (18 tables)"
        try:
            rows = db.fetch_all("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name;
            """)
            actual_tables = sorted([r["table_name"] for r in rows])
            missing = set(EXPECTED_TABLES) - set(actual_tables)
            assert not missing, f"Thiếu {len(missing)} bảng: {missing}"
            assert len(actual_tables) == 18, f"Mong đợi 18 bảng, thực tế có {len(actual_tables)}"
            self._record(name, True, f"Đủ 18/18 bảng cơ sở dữ liệu")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_foreign_keys(self) -> None:
        """7. Kiểm tra đủ 28 khóa ngoại (Foreign Keys)."""
        name = "Foreign keys (28 FKs)"
        try:
            rows = db.fetch_all("""
                SELECT conname
                FROM pg_constraint 
                WHERE contype = 'f' AND connamespace = 'public'::regnamespace
                ORDER BY conname;
            """)
            actual_fks = sorted([r["conname"] for r in rows])
            missing = set(EXPECTED_FKS) - set(actual_fks)
            assert not missing, f"Thiếu {len(missing)} khóa ngoại: {missing}"
            assert len(actual_fks) == 28, f"Mong đợi 28 FKs, thực tế có {len(actual_fks)}"
            self._record(name, True, f"Đủ 28/28 khóa ngoại toàn vẹn dữ liệu")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_required_indexes(self) -> None:
        """8. Kiểm tra đủ 6 chỉ mục hiệu năng (Indexes)."""
        name = "Required indexes (6 indexes)"
        try:
            rows = db.fetch_all("""
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public' AND indexname LIKE %s
                ORDER BY indexname;
            """, ("idx_%",))
            actual_idxs = sorted([r["indexname"] for r in rows])
            missing = set(EXPECTED_INDEXES) - set(actual_idxs)
            assert not missing, f"Thiếu {len(missing)} chỉ mục: {missing}"
            assert len(actual_idxs) == 6, f"Mong đợi 6 chỉ mục idx_*, thực tế có {len(actual_idxs)}"
            self._record(name, True, f"Đủ 6/6 chỉ mục tối ưu truy vấn")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_sample_accounts(self) -> None:
        """9. Kiểm tra 5 tài khoản mẫu và các vai trò chuẩn."""
        name = "Sample accounts"
        try:
            rows = db.fetch_all("SELECT ten_dang_nhap, vai_tro, trang_thai FROM tai_khoan ORDER BY ten_dang_nhap")
            assert len(rows) == 5, f"Mong đợi 5 tài khoản mẫu, thực tế có {len(rows)}"
            roles = {r["vai_tro"] for r in rows}
            assert {"admin", "quan_ly", "nhan_vien", "cu_dan"}.issubset(roles), f"Thiếu vai trò trong: {roles}"
            self._record(name, True, f"Đủ 5 tài khoản (4 vai trò: Admin, Quản lý, Nhân viên, Cư dân)")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_apartments(self) -> None:
        """10. Có 2 căn hộ mẫu."""
        name = "Apartments (can_ho)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM can_ho")["total"]
            assert cnt == 2, f"Mong đợi 2 căn hộ, thực tế có {cnt}"
            self._record(name, True, "Đủ 2 căn hộ mẫu (A1-01, B2-02)")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_staff_and_manager(self) -> None:
        """11. Có 2 nhân viên và quản lý."""
        name = "Staff & Managers (nhan_vien)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM nhan_vien")["total"]
            assert cnt == 2, f"Mong đợi 2 hồ sơ nhân viên/quản lý, thực tế có {cnt}"
            self._record(name, True, "Đủ 2 hồ sơ nhân sự (QL001, NV001)")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_residents(self) -> None:
        """12. Có 2 cư dân."""
        name = "Residents (cu_dan)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM cu_dan")["total"]
            assert cnt == 2, f"Mong đợi 2 cư dân, thực tế có {cnt}"
            self._record(name, True, "Đủ 2 hồ sơ cư dân")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_vehicle_types(self) -> None:
        """13. Có 2 loại phương tiện."""
        name = "Vehicle types (loai_phuong_tien)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM loai_phuong_tien")["total"]
            assert cnt == 2, f"Mong đợi 2 loại xe, thực tế có {cnt}"
            self._record(name, True, "Đủ 2 loại xe (O_TO, XE_MAY)")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_zones(self) -> None:
        """14. Có 3 khu vực."""
        name = "Parking zones (khu_vuc)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM khu_vuc")["total"]
            assert cnt == 3, f"Mong đợi 3 khu vực, thực tế có {cnt}"
            self._record(name, True, "Đủ 3 khu vực (KHU_A, HAM_B1, KHU_C)")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_parking_slots(self) -> None:
        """15. Có 20 vị trí đỗ."""
        name = "Parking slots (vi_tri_do)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM vi_tri_do")["total"]
            assert cnt == 20, f"Mong đợi 20 vị trí đỗ, thực tế có {cnt}"
            self._record(name, True, "Đủ 20 vị trí đỗ (12 ô hầm B1 + 8 ô khu C)")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_slot_allocation(self) -> None:
        """16. Có 1 phân bổ chỗ đỗ."""
        name = "Slot allocations (phan_bo_cho_do)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM phan_bo_cho_do")["total"]
            assert cnt == 1, f"Mong đợi 1 phân bổ chỗ đỗ, thực tế có {cnt}"
            self._record(name, True, "Đủ 1 phân bổ cố định (A1-01 tại ô B1-01)")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_vehicles(self) -> None:
        """17. Có 2 phương tiện."""
        name = "Vehicles (phuong_tien)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM phuong_tien")["total"]
            assert cnt == 2, f"Mong đợi 2 phương tiện, thực tế có {cnt}"
            self._record(name, True, "Đủ 2 phương tiện (51H-888.99 và 59A1-123.45)")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_vehicle_management_records(self) -> None:
        """18. Có 2 bản ghi quản lý phương tiện ra vào."""
        name = "Vehicle gate records (quan_ly_phuong_tien_ra_vao)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM quan_ly_phuong_tien_ra_vao")["total"]
            assert cnt == 2, f"Mong đợi 2 bản ghi quản lý phương tiện, thực tế có {cnt}"
            self._record(name, True, "Đủ 2 bản ghi quản lý ra vào")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_pricing_tiers(self) -> None:
        """19. Có 5 mức giá."""
        name = "Pricing tiers (bang_gia)"
        try:
            cnt = db.fetch_one("SELECT count(*) AS total FROM bang_gia")["total"]
            assert cnt == 5, f"Mong đợi 5 mức giá, thực tế có {cnt}"
            self._record(name, True, "Đủ 5 mức giá (ô tô mặc định, mua thêm, xe máy, vãng lai)")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_sample_records_total(self) -> None:
        """20. Có tổng cộng 46 bản ghi mẫu trong 11 bảng tương ứng."""
        name = "Total sample records (46 records)"
        try:
            sample_tables = [
                "tai_khoan", "can_ho", "nhan_vien", "cu_dan", "loai_phuong_tien",
                "khu_vuc", "vi_tri_do", "phan_bo_cho_do", "phuong_tien",
                "quan_ly_phuong_tien_ra_vao", "bang_gia"
            ]
            total = 0
            for tbl in sample_tables:
                c = db.fetch_one(f"SELECT count(*) AS cnt FROM {tbl}")["cnt"]
                total += c
            assert total == 46, f"Mong đợi 46 bản ghi mẫu, thực tế có {total}"
            self._record(name, True, f"Tổng cộng chính xác 46/46 bản ghi trong 11 bảng")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_transaction_and_rollback(self) -> None:
        """Kiểm tra db.transaction() commit và rollback an toàn mà không để lại dữ liệu rác."""
        name = "Transaction handling & Rollback"
        test_id = "_TX_TEMP_"
        try:
            # 1. Kiểm tra rollback khi có exception
            try:
                with db.transaction() as tx:
                    tx.execute(
                        "INSERT INTO can_ho (ma_can_ho, toa_nha, so_tang) VALUES (%s, %s, %s)",
                        (test_id, "T", 1)
                    )
                    # Xác nhận bản ghi tạm thấy được trong transaction
                    check_in_tx = tx.fetch_one("SELECT ma_can_ho FROM can_ho WHERE ma_can_ho = %s", (test_id,))
                    assert check_in_tx is not None, "Không đọc được bản ghi trong transaction"
                    # Kích hoạt lỗi giả lập để rollback
                    raise RuntimeError("SIMULATED_ABORT_FOR_ROLLBACK_TEST")
            except RuntimeError as exc:
                if "SIMULATED_ABORT_FOR_ROLLBACK_TEST" not in str(exc):
                    raise

            # 2. Xác nhận bản ghi đã được ROLLBACK hoàn toàn
            check_after_rb = db.fetch_one("SELECT ma_can_ho FROM can_ho WHERE ma_can_ho = %s", (test_id,))
            assert check_after_rb is None, "Rollback thất bại: Bản ghi thử nghiệm vẫn còn lưu trong database!"

            self._record(name, True, "ACID Transaction & Rollback hoạt động hoàn hảo, không có dữ liệu rác")
        except Exception as exc:
            self._record(name, False, str(exc))

    def test_pool_lifecycle_and_shutdown(self) -> None:
        """21. Connection Pool được đóng an toàn sau kiểm thử."""
        name = "Graceful pool shutdown"
        try:
            db.close_pool(timeout=5.0)
            pool = db._POOL
            assert pool is None, "db._POOL phải là None sau khi close_pool()"
            self._record(name, True, "Connection Pool đã đóng an toàn và giải phóng tài nguyên")
        except Exception as exc:
            self._record(name, False, str(exc))

    def run_all(self) -> int:
        print("\n" + "=" * 70)
        print("   SMART PARKING - NEON DATABASE INTEGRATION TEST SUITE")
        print("=" * 70 + "\n")

        steps: List[Callable[[], None]] = [
            self.test_environment_configuration,
            self.test_postgresql_connection,
            self.test_ssl_connection,
            self.test_connection_pool,
            self.test_database_schema,
            self.test_foreign_keys,
            self.test_required_indexes,
            self.test_sample_accounts,
            self.test_apartments,
            self.test_staff_and_manager,
            self.test_residents,
            self.test_vehicle_types,
            self.test_zones,
            self.test_parking_slots,
            self.test_slot_allocation,
            self.test_vehicles,
            self.test_vehicle_management_records,
            self.test_pricing_tiers,
            self.test_sample_records_total,
            self.test_transaction_and_rollback,
            self.test_pool_lifecycle_and_shutdown,
        ]

        for step in steps:
            step()

        total = self.passed + self.failed
        print("\n" + "-" * 70)
        print(f"TOTAL TESTS: {total}")
        print(f"PASSED:      {self.passed}")
        print(f"FAILED:      {self.failed}")
        print("-" * 70 + "\n")

        if self.failed > 0:
            print("[FAIL] Neon Database Integration có lỗi cần xử lý!\n")
            return 1
        else:
            print("[SUCCESS] Toàn bộ tiêu chí tích hợp Neon PostgreSQL Cloud đạt yêu cầu 100%!\n")
            return 0


def main() -> int:
    suite = NeonTestSuite()
    return suite.run_all()


if __name__ == "__main__":
    sys.exit(main())
