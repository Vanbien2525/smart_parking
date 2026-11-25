"""Quản lý kết nối PostgreSQL qua Connection Pool (psycopg + psycopg_pool)."""

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

from psycopg.rows import dict_row
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from server.config import DEFAULT_CONFIG, ServerConfig



_POOL_LOCK = threading.RLock()
_POOL: Optional[ConnectionPool] = None


def build_conninfo(config: Optional[ServerConfig] = None) -> str:
    """Tạo chuỗi kết nối PostgreSQL từ `ServerConfig` bằng make_conninfo chuẩn."""
    cfg = config or DEFAULT_CONFIG
    return make_conninfo(
        host=cfg.db_host,
        port=cfg.db_port,
        dbname=cfg.db_name,
        user=cfg.db_user,
        password=cfg.db_password if cfg.db_password else None,
        sslmode=cfg.db_sslmode,
        channel_binding=cfg.db_channel_binding,
        connect_timeout=cfg.db_connect_timeout,
    )


def init_pool(
    config: Optional[ServerConfig] = None,
    min_size: Optional[int] = None,
    max_size: Optional[int] = None,
    open_timeout: Optional[float] = None,
) -> ConnectionPool:
    """Khởi tạo ConnectionPool singleton (thread-safe, hỗ trợ Neon Cold Start & Stale Connection Recovery)."""
    global _POOL
    cfg = config or DEFAULT_CONFIG
    timeout = open_timeout if open_timeout is not None else getattr(cfg, "db_pool_timeout", 30.0)
    with _POOL_LOCK:
        if _POOL is not None and not _POOL.closed:
            return _POOL
        pool = ConnectionPool(
            conninfo=build_conninfo(cfg),
            min_size=min_size if min_size is not None else cfg.db_pool_min,
            max_size=max_size if max_size is not None else cfg.db_pool_max,
            timeout=timeout,
            check=ConnectionPool.check_connection,
            max_idle=300.0,
            max_lifetime=3600.0,
            reconnect_timeout=30.0,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        _POOL = pool
        return pool


def get_pool() -> ConnectionPool:
    """Lấy ConnectionPool hiện tại; tự khởi tạo nếu chưa mở."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None or _POOL.closed:
            return init_pool()
        return _POOL


def close_pool(timeout: Optional[float] = None) -> None:
    """Đóng toàn bộ kết nối trong ConnectionPool khi tắt Server (Graceful Shutdown)."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            try:
                if not _POOL.closed:
                    if timeout is not None:
                        _POOL.close(timeout=timeout)
                    else:
                        _POOL.close()
            finally:
                _POOL = None


def is_pool_ready() -> bool:
    """Kiểm tra pool đã mở và kết nối thử thành công hay chưa."""
    try:
        row = fetch_one("SELECT 1 AS ok")
        return bool(row and row.get("ok") == 1)
    except Exception:
        return False


class TransactionCursor:
    """Bao bọc kết nối trong một DB transaction ACID thống nhất với API module."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row is not None else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return int(cur.rowcount or 0)

    def execute_returning(
        self, sql: str, params: Sequence[Any] = ()
    ) -> Optional[Dict[str, Any]]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row is not None else None


@contextmanager
def transaction() -> Iterator[TransactionCursor]:
    """Context manager đảm bảo COMMIT khi thành công và ROLLBACK khi có lỗi."""
    pool = get_pool()
    with pool.connection() as conn:
        try:
            yield TransactionCursor(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def fetch_one(sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    """Thực thi truy vấn SELECT trả về 1 bản ghi dict hoặc None."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row is not None else None


def fetch_all(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Thực thi truy vấn SELECT trả về danh sách dict."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    """Thực thi câu lệnh INSERT/UPDATE/DELETE độc lập (auto-commit) và trả về số dòng ảnh hưởng."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            affected = int(cur.rowcount or 0)
        conn.commit()
        return affected


def execute_returning(
    sql: str, params: Sequence[Any] = ()
) -> Optional[Dict[str, Any]]:
    """Thực thi câu lệnh có RETURNING và tự động commit, trả về 1 bản ghi dict."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row is not None else None


def run_sql_file(sql_path: Union[str, Path]) -> None:
    """Thực thi toàn bộ nội dung file `.sql` (ví dụ `schema.sql` hoặc `seed_data.sql`)."""
    path = Path(sql_path)
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file SQL: {path}")
    sql_text = path.read_text(encoding="utf-8")
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_text)
        conn.commit()
