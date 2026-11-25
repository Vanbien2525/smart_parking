# BÁO CÁO TÍCH HỢP NEON POSTGRESQL CLOUD — SMART PARKING
**Dự án**: Hệ thống Quản lý Bãi giữ xe Thông minh Chung cư Đào Tiên  
**Môn học**: Lập trình mạng (TCP Socket Port 5555)  
**Nhóm phát triển**: 5 thành viên  
**Thời gian hoàn thành**: 25/11/2026  
**Trạng thái kiểm thử**: 100% PASSED (Neon Suite: 21/21, Client-Server Suite: 17/17)

---

## 1. Kiến trúc hệ thống trước và sau tích hợp

### 1.1. Trước tích hợp
- **Database**: PostgreSQL cài đặt cục bộ (`localhost:5432`), database `smart_parking`, tài khoản mặc định `postgres`.
- **Cơ chế kết nối**: Ghép chuỗi thủ công `host=... port=... dbname=... user=... password=...` trong `server/db.py`.
- **Bảo mật & Mã hóa**: Không cấu hình SSL/TLS (`sslmode` không được truyền vào chuỗi kết nối), không có channel binding.
- **Connection Pool**: `open_timeout = 5.0s`, không có cơ chế kiểm tra kết nối sống (`check_connection`), dễ bị lỗi kết nối gãy khi database sleep hoặc cold start.
- **Biến môi trường**: Chỉ đọc `.env` tại thư mục hiện tại, có nguy cơ ghi đè biến môi trường trên cloud, thiếu các tham số SSL và timeout.

### 1.2. Sau tích hợp Neon Cloud
- **Database**: Neon PostgreSQL Cloud v18.7 (AWS Singapore, Region `ap-southeast-1`), Branch `production`.
- **Cơ chế kết nối**: Sử dụng `psycopg.conninfo.make_conninfo` chuẩn hóa, tự động xử lý ký tự đặc biệt và các tham số bảo mật.
- **Bảo mật & Mã hóa**:
  - `DB_SSLMODE=require`: Bắt buộc mã hóa toàn bộ dữ liệu truyền qua Internet bằng SSL/TLS.
  - `DB_CHANNEL_BINDING=require`: Xác thực kênh SCRAM-SHA-256-PLUS ngăn chặn hoàn toàn tấn công Man-in-the-Middle (MITM).
- **Connection Pool**:
  - Thời gian chờ mở rộng: `timeout=30.0s` (hỗ trợ Neon Cold Start khi đánh thức compute instance từ trạng thái sleep).
  - Tự động phục hồi kết nối: `check=ConnectionPool.check_connection` giúp kiểm tra sức khỏe socket trước khi cấp phát cho worker thread.
  - Quản lý tài nguyên nhàn rỗi: `max_idle=300.0s`, `max_lifetime=3600.0s`, `reconnect_timeout=30.0s`.
  - Graceful Shutdown: `close_pool(timeout=...)` giải phóng sạch tài nguyên socket khi tắt máy chủ.
- **Kiến trúc mạng không đổi**:
  - Máy chủ trung gian: Python Server lắng nghe TCP raw socket port 5555.
  - Máy trạm Client: Desktop App PySide6 giao tiếp với Server qua giao thức phân tách khung tin `[4-byte Length-Prefix] + [UTF-8 JSON]`.
  - Bảo vệ CSDL: Desktop Client tuyệt đối không kết nối trực tiếp đến Neon PostgreSQL; mọi nghiệp vụ đều đi qua tầng xác thực và phân quyền RBAC của Python Server.

---

## 2. Danh sách file đã chỉnh sửa & tạo mới

| STT | Đường dẫn File | Hành động | Mục đích |
|:---:|:---|:---:|:---|
| 1 | `server/config.py` | Chỉnh sửa | Nâng cấp nạp `.env` linh hoạt (thư mục hiện tại & thư mục cha), `override=False` bảo vệ biến môi trường Cloud, thêm `db_pool_timeout`. |
| 2 | `server/db.py` | Chỉnh sửa | Chuyển sang `make_conninfo`, cấu hình `ConnectionPool` hỗ trợ Neon Cold Start (`check_connection`, timeout 30s, max_idle 300s). |
| 3 | `.gitignore` | Chỉnh sửa | Bảo vệ secrets: loại trừ `.env`, `.env.*`, `!.env.example`, `.venv/`, `venv/`, `__pycache__/`, `*.pyc`. |
| 4 | `.env.example` | Chỉnh sửa | Cập nhật cấu hình mẫu song song Local PostgreSQL và Neon Cloud, hướng dẫn tham số chi tiết. |
| 5 | `.env` | Chỉnh sửa | Bổ sung `DB_CHANNEL_BINDING=require`, `DB_CONNECT_TIMEOUT=15`, `DB_POOL_TIMEOUT=30` (giữ nguyên thông tin bí mật của người dùng). |
| 6 | `test_neon.py` | Tạo mới | Bộ kiểm thử tự động 21 tiêu chí CSDL Neon (Schema, FKs, Indexes, 46 bản ghi mẫu, Transaction Rollback, Pool Lifecycle). |
| 7 | `test_client_server.py` | Tạo mới | Bộ kiểm thử tích hợp 17 tiêu chí Client-Server TCP Socket Port 5555 + Neon DB. |
| 8 | `docs/NEON_INTEGRATION_REPORT.md` | Tạo mới | Tài liệu báo cáo chi tiết kỹ thuật tích hợp và kế hoạch triển khai Render. |

---

## 3. Chi tiết thay đổi và Lý do kỹ thuật

### 3.1. Nâng cấp `server/config.py`
- **Vấn đề**: Khi dự án được giải nén dạng lồng thư mục (`smart_parking-main/smart_parking-main`), đường dẫn `BASE_DIR / ".env"` không tìm thấy file nếu file `.env` đặt tại thư mục gốc. Ngoài ra, hàm `load_dotenv()` nếu không chỉ định `override=False` có thể ghi đè biến môi trường hệ thống trên Production Cloud Server (như Render).
- **Giải pháp**:
  ```python
  BASE_DIR = Path(__file__).resolve().parent.parent
  ENV_PATH = BASE_DIR / ".env"
  PARENT_ENV_PATH = BASE_DIR.parent / ".env"

  if ENV_PATH.exists():
      load_dotenv(ENV_PATH, override=False)
  elif PARENT_ENV_PATH.exists():
      load_dotenv(PARENT_ENV_PATH, override=False)
  else:
      load_dotenv(override=False)
  ```
  Đồng thời bổ sung `db_pool_timeout: float = float(_int_env("DB_POOL_TIMEOUT", 30))` trong dataclass `ServerConfig`.

### 3.2. Nâng cấp `server/db.py`
- **Vấn đề**: Chuỗi kết nối nối chuỗi thủ công bỏ sót `sslmode`, `channel_binding`, `connect_timeout`. Connection Pool mặc định timeout chỉ 5 giây khiến kết nối thất bại khi Neon ở trạng thái Compute Inactive (Cold Start mất ~1.5 - 3 giây). Khi kết nối ngắt do Neon idle timeout, pool không tự phát hiện connection bị stale.
- **Giải pháp**:
  1. Sử dụng `psycopg.conninfo.make_conninfo`:
     ```python
     def build_conninfo(config: Optional[ServerConfig] = None) -> str:
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
     ```
  2. Bổ sung `check=ConnectionPool.check_connection`, nâng timeout lên 30s và cấu hình `max_idle=300.0s`:
     ```python
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
     ```

### 3.3. Bảo mật thông tin cấu hình (`.gitignore`, `.env.example`, Git audit)
- File `.env` chứa thông tin nhạy cảm đã được kiểm tra: Không nằm trong bất kỳ git tracking nào (dự án được giải nén từ archive zip).
- `.gitignore` được cập nhật toàn diện nhằm đảm bảo các biến thể `.env` hoặc file bytecode không bao giờ bị vô tình đẩy lên GitHub.
- `.env.example` loại bỏ mọi thông tin mật khẩu thực tế, chỉ lưu giữ chuỗi cấu hình mẫu an toàn.

---

## 4. Hướng dẫn vận hành và Kiểm thử

### 4.1. Cấu hình biến môi trường an toàn (.env)
Tạo file `.env` tại thư mục dự án với nội dung cấu hình chuẩn kết nối Neon:
```env
SERVER_HOST=0.0.0.0
SERVER_PORT=5555
CLIENT_CONNECT_HOST=127.0.0.1
HEARTBEAT_TIMEOUT_SECONDS=45

DB_HOST=ep-example-subdomain.ap-southeast-1.aws.neon.tech
DB_PORT=5432
DB_NAME=smart_parking
DB_USER=neondb_owner
DB_PASSWORD=your_actual_password_here
DB_SSLMODE=require
DB_CHANNEL_BINDING=require
DB_CONNECT_TIMEOUT=15
DB_POOL_MIN=2
DB_POOL_MAX=10
DB_POOL_TIMEOUT=30
```

### 4.2. Hướng dẫn chạy kiểm thử Neon CSDL (`test_neon.py`)
Mở Terminal tại thư mục gốc dự án và chạy:
```bash
python test_neon.py
```
**Kết quả mong đợi**:
```text
======================================================================
   SMART PARKING - NEON DATABASE INTEGRATION TEST SUITE
======================================================================

[PASS]   Environment configuration
[PASS]   PostgreSQL connection
[PASS]   SSL/TLS connection
[PASS]   Connection pool
[PASS]   Database schema (18 tables)
[PASS]   Foreign keys (28 FKs)
[PASS]   Required indexes (6 indexes)
[PASS]   Sample accounts
[PASS]   Apartments (can_ho)
[PASS]   Staff & Managers (nhan_vien)
[PASS]   Residents (cu_dan)
[PASS]   Vehicle types (loai_phuong_tien)
[PASS]   Parking zones (khu_vuc)
[PASS]   Parking slots (vi_tri_do)
[PASS]   Slot allocations (phan_bo_cho_do)
[PASS]   Vehicles (phuong_tien)
[PASS]   Vehicle gate records (quan_ly_phuong_tien_ra_vao)
[PASS]   Pricing tiers (bang_gia)
[PASS]   Total sample records (46 records)
[PASS]   Transaction handling & Rollback
[PASS]   Graceful pool shutdown

TOTAL TESTS: 21
PASSED:      21
FAILED:      0
```

### 4.3. Hướng dẫn chạy kiểm thử Client-Server TCP Socket (`test_client_server.py`)
```bash
python test_client_server.py
```
**Kết quả mong đợi**:
```text
======================================================================
   SMART PARKING - CLIENT-SERVER & NEON INTEGRATION VERIFICATION
======================================================================

[PASS]   TCP Server startup — Lắng nghe tại 0.0.0.0:5555 (Neon DB ready)
[PASS]   TCP Client connection — Kết nối thành công 127.0.0.1:5555
[PASS]   Ping / Heartbeat — Phản hồi pong=True qua TCP Socket
[PASS]   SessionManager tracking — Theo dõi 1 phiên TCP kết nối
[PASS]   Auth invalid credentials rejection — Từ chối đúng: Tên đăng nhập hoặc mật khẩu không chính xác.
[PASS]   Auth admin01 login & bcrypt verify — Đăng nhập admin thành công (vai trò: admin)
[PASS]   Admin list accounts (Neon DB) — Đọc 3 tài khoản từ Neon CSDL
[PASS]   Infra zones & slots (Neon DB) — 2 khu vực, 2 vị trí đỗ
[PASS]   Pricing list (Neon DB) — 2 mức phí đang áp dụng
[PASS]   HR search residents (Neon DB) — Đọc 3 hồ sơ cư dân
[PASS]   Auth cudan_a101 login & bcrypt verify — Đăng nhập cư dân thành công (vai trò: cu_dan)
[PASS]   RBAC permission enforcement — Chặn cư dân truy cập API quản trị viên (ROLE_FORBIDDEN)
[PASS]   Vehicle resident list (Neon DB) — Đọc 3 phương tiện thuộc căn hộ A1-01
[PASS]   Billing my invoices (Neon DB) — Đọc 3 hóa đơn tiền giữ xe
[PASS]   Notify my inbox (Neon DB) — Hộp thư cá nhân sẵn sàng (4 thông báo)
[PASS]   Staff & Manager authentication — Xác thực thành công nhanvien01 và quanly01
[PASS]   TCP Server & Pool shutdown — Đã đóng kết nối TCP và Connection Pool an toàn

TOTAL CLIENT-SERVER CHECKS: 17
PASSED:                     17
FAILED:                     0
```

### 4.4. Hướng dẫn khởi động Server độc lập
```bash
python smart_parking-main/server/main.py
```
Server sẽ tự động:
1. Nạp biến môi trường từ `.env`.
2. Khởi tạo `ConnectionPool` kết nối tới Neon Cloud.
3. Đồng bộ hóa đơn kỳ hiện tại nếu chưa có.
4. Lắng nghe tại `0.0.0.0:5555`.

### 4.5. Hướng dẫn khởi động Desktop Client (PySide6)
Yêu cầu môi trường có cài đặt `PySide6`:
```bash
pip install PySide6
python smart_parking-main/client/main.py
```
Giao diện đăng nhập sẽ mở ra, kết nối tới Server cổng 5555 và cho phép đăng nhập các tài khoản mẫu (`admin01`, `quanly01`, `nhanvien01`, `cudan_a101` với mật khẩu `123456`).

---

## 5. Hướng dẫn chuyển đổi về PostgreSQL Local khi cần
Hệ thống hoàn toàn tương thích ngược (Backward Compatible) 100% với PostgreSQL cục bộ. Khi cần quay lại môi trường Local:
1. Mở file `.env`.
2. Sửa thông số database:
   ```env
   DB_HOST=localhost
   DB_PORT=5432
   DB_NAME=smart_parking
   DB_USER=postgres
   DB_PASSWORD=your_local_password
   DB_SSLMODE=prefer
   DB_CHANNEL_BINDING=prefer
   ```
3. Lưu file và khởi động lại Server.

---

## 6. Phân tích Tương thích & Kế hoạch Triển khai Render

### 6.1. Đánh giá tính khả thi kiến trúc hiện tại trên Render
1. **TCP Socket Raw Port 5555 trên Render**:
   - *Đặc điểm Render*: Dịch vụ **Render Web Service** chỉ cho phép công khai các cổng HTTP/HTTPS (port 80/443) và WebSocket thông qua reverse proxy của Render. Render **không** mở cổng TCP thô (raw TCP socket) cho client từ Internet kết nối vào Web Service.
   - *Render Private Service*: Chỉ giao tiếp nội bộ giữa các service trong cùng private network trên Render.
2. **PySide6 Desktop Client**:
   - Là ứng dụng Desktop chạy trên máy tính người dùng (Windows/macOS/Linux), không deploy lên Render.
3. **Python Server**:
   - Có thể chạy dưới dạng Web Service hoặc Background Worker trên Render.
4. **Neon PostgreSQL**:
   - Host tại AWS Singapore, kết nối qua Internet với SSL/TLS hoàn toàn tương thích và độ trễ thấp với Render Singapore/Frankfurt/Oregon.
5. **SMTP OTP & AI OCR**:
   - Hoạt động qua outbound HTTPS/SMTP, hoàn toàn tương thích với Render.

### 6.2. Giải pháp kiến trúc đề xuất cho Render (Giai đoạn tiếp theo)
Nhằm giữ nguyên 100% mã nguồn TCP Socket cho môn học Lập trình mạng, đồng thời có thể đưa Server lên Render:
- **Phương án Hybrid (Đề xuất tối ưu)**:
  - Bổ sung một **WebSocket Gateway** mỏng (`server/gateway/ws_bridge.py`) hoặc chạy song song cả 2 listener:
    - **Cổng 5555 (Raw TCP Socket)**: Giữ nguyên phục vụ bài tập lớn Lập trình mạng, chạy khi phát triển local hoặc mạng LAN.
    - **Cổng HTTP/WebSocket (Port $PORT do Render cấp)**: Đóng gói frame `[Length-Prefix] + JSON` vào khung tin WebSocket Binary/Text Frame. Desktop Client chỉ cần một module chuyển đổi nhỏ (`client/network/ws_socket_client.py`) để kết nối qua `wss://smart-parking.onrender.com`.
  - **Lưu ý State & Session**: Render Web Service miễn phí (Free Tier) sẽ ngủ (spin down) sau 15 phút không có request. Vì vậy, việc giữ kết nối Neon Cloud và lưu trạng thái vào CSDL thay vì chỉ lưu bộ nhớ RAM là cực kỳ quan trọng và đã được giải quyết triệt để thông qua tích hợp Neon Cloud lần này.

---

## 7. Hướng dẫn Rollback Source Code (Nếu cần)
Nếu cần quay về phiên bản trước khi tích hợp:
1. Khôi phục `server/config.py`: Thay đổi hàm nạp dotenv về đường dẫn cố định và bỏ `db_pool_timeout`.
2. Khôi phục `server/db.py`: Thay đổi `make_conninfo` về cách nối chuỗi cũ `parts.append(...)` và bỏ tham số `check` trong `ConnectionPool`.
3. Khôi phục `.env`: Chuyển `DB_HOST` về `localhost` và bỏ các biến `DB_CHANNEL_BINDING`.
4. Xóa 2 file test: `test_neon.py` và `test_client_server.py`.
