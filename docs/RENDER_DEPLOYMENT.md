# HƯỚNG DẪN TRIỂN KHAI RENDER CLOUD — SMART PARKING
**Dự án**: Hệ thống Quản lý Bãi xe Thông minh Chung cư Đào Tiên  
**Môn học**: Lập trình mạng (TCP Socket Port 5555 & WebSocket WSS)  
**Nhóm phát triển**: 5 thành viên  
**Phiên bản Gateway**: 2.0.0  

---

## 1. Tổng quan Kiến trúc Dual-Transport

Hệ thống hỗ trợ 2 chế độ truyền tải song song độc lập, sử dụng chung 100% CSDL Neon PostgreSQL, Router Core, SessionManager và các Handlers nghiệp vụ:

1. **Chế độ Cục bộ (Local Mode)**:
   - Dành cho báo cáo môn Lập trình mạng trên máy tính cá nhân hoặc mạng nội bộ LAN.
   - Client PySide6 kết nối qua **TCP Socket cổng 5555**.
   - Chuẩn gói tin: `[4-byte Big-Endian Length] + [UTF-8 JSON Payload]`.
   - Lệnh chạy Server: `python server/main.py`.

2. **Chế độ Đám mây (Cloud Mode — Render Free)**:
   - Dành cho việc triển khai máy chủ lên Internet miễn phí trên nền tảng Render.
   - Client PySide6 kết nối qua **WebSocket Secure (`wss://.../ws`)**.
   - Render Web Service chuyển tiếp hai chiều trực tiếp tới Internal TCP Server Core qua loopback `127.0.0.1:5555`.
   - Cung cấp sẵn các endpoint HTTP:
     - `GET /`: Trang thông tin hệ thống trực quan (HTML Dashboard).
     - `GET /health`: Healthcheck endpoint cho Render Web Service.
     - `GET /ready`: Kiểm tra sẵn sàng CSDL Neon và Socket Core (bảo mật tuyệt đối, không lộ credentials).
     - `WSS /ws`: Điểm cuối WebSocket phục vụ Desktop Client.
   - Lệnh chạy Gateway: `uvicorn server.gateway:app --host 0.0.0.0 --port $PORT`.

---

## 2. Chuẩn bị Git Repository và Đẩy mã nguồn lên GitHub

> [!IMPORTANT]
> Thư mục làm việc hiện tại được giải nén từ file zip, chưa có liên kết Git (`.git`). Tuyệt đối **không** khởi tạo lịch sử Git mới đè lên repository chính thức của nhóm.

### Các bước đẩy mã nguồn lên nhánh mới an toàn:

1. Mở Terminal và clone repository chính thức của nhóm vào một thư mục tạm:
   ```bash
   git clone https://github.com/Vanbien2525/smart_parking.git smart_parking_git
   cd smart_parking_git
   ```

2. Tạo nhánh tính năng mới từ nhánh `main` (hoặc `master`):
   ```bash
   git checkout -b feat/cloud-render-deployment
   ```

3. Sao chép các tập tin đã được nâng cấp trong Phase 2 vào repository:
   - `server/gateway.py` (Mới)
   - `server/core/email_provider.py` (Mới)
   - `server/core/storage_provider.py` (Mới)
   - `server/core/otp_service.py` (Cập nhật)
   - `server/config.py` (Cập nhật)
   - `server/db.py` (Cập nhật)
   - `client/network/socket_client.py` (Cập nhật Dual Mode)
   - `client/ui/auth/login_dialog.py` (Cập nhật badge WSS)
   - `requirements-server.txt` (Mới - Tối ưu cho Render)
   - `requirements-client.txt` (Mới - Tối ưu cho Desktop)
   - `requirements.txt` (Cập nhật)
   - `render.yaml` (Mới)
   - `.env.render.example` (Mới)
   - `Dockerfile` & `.dockerignore` (Mới)
   - `test_neon.py` (Kiểm thử CSDL)
   - `test_client_server.py` (Kiểm thử TCP)
   - `test_websocket_gateway.py` (Kiểm thử WebSocket)
   - `docs/RENDER_DEPLOYMENT.md`
   - `docs/RENDER_PHASE2_REPORT.md`

4. Kiểm tra lại không có file `.env` hoặc tệp tin rác lọt vào git status:
   ```bash
   git status
   ```

5. Commit và push lên GitHub nhánh `feat/cloud-render-deployment`:
   ```bash
   git add .
   git commit -m "feat(render): integrate websocket gateway, dual transport and cloud email provider"
   git push origin feat/cloud-render-deployment
   ```

6. Tạo Pull Request trên GitHub để các thành viên trong nhóm review trước khi merge.

---

## 3. Các bước thiết lập Render Web Service

### Bước 1: Đăng nhập Render
Truy cập [https://dashboard.render.com](https://dashboard.render.com) và đăng nhập bằng tài khoản GitHub của nhóm.

### Bước 2: Tạo Web Service mới
1. Nhấp nút **New +** ở góc trên bên phải, chọn **Web Service**.
2. Chọn **Build and deploy from a Git repository**.
3. Kết nối với repository `Vanbien2525/smart_parking`.

### Bước 3: Cấu hình Web Service
Điền chính xác các thông số kỹ thuật sau:
- **Name**: `smart-parking-server` (hoặc tên tùy chọn của nhóm)
- **Region**: `Singapore (Southeast Asia)` *(Gần cụm Neon AWS Singapore nhất, giảm độ trễ mạng)*
- **Branch**: `feat/cloud-render-deployment`
- **Root Directory**: Để trống nếu repo gốc chứa mã nguồn, hoặc điền `smart_parking-main` nếu repository có thư mục con.
- **Runtime**: `Python 3`
- **Build Command**:
  ```bash
  pip install -r requirements-server.txt
  ```
- **Start Command**:
  ```bash
  uvicorn server.gateway:app --host 0.0.0.0 --port $PORT
  ```
- **Instance Type**: `Free` ($0/tháng, 512 MB RAM, 0.1 CPU)
- **Health Check Path**: `/health`

### Bước 4: Nhập Biến Môi Trường (Environment Variables)
Chuyển xuống mục **Environment Variables** trong trang tạo Service và thêm các biến sau:

| Tên biến (Key) | Giá trị (Value) | Giải thích |
|:---|:---|:---|
| `PYTHON_VERSION` | `3.11.9` | Phiên bản Python ổn định trên Linux |
| `SERVER_HOST` | `127.0.0.1` | Loopback IP cho TCP Server Core nội bộ |
| `SERVER_PORT` | `5555` | Cổng TCP nội bộ |
| `DB_HOST` | `ep-soft-sea-bit4ojib.c-5.ap-southeast-1.aws.neon.tech` | Direct endpoint của Neon (không có `-pooler`) |
| `DB_PORT` | `5432` | Cổng PostgreSQL |
| `DB_NAME` | `smart_parking` | Tên cơ sở dữ liệu |
| `DB_USER` | `neondb_owner` | Tên người dùng database |
| `DB_PASSWORD` | `<Mật khẩu Neon trong .env>` | Mật khẩu CSDL của bạn |
| `DB_SSLMODE` | `require` | Bắt buộc mã hóa SSL/TLS |
| `DB_CHANNEL_BINDING` | `require` | Xác thực SCRAM an toàn |
| `DB_CONNECT_TIMEOUT` | `15` | Timeout kết nối Neon |
| `DB_POOL_MIN` | `2` | Số kết nối tối thiểu |
| `DB_POOL_MAX` | `10` | Số kết nối tối đa |
| `DB_POOL_TIMEOUT` | `30` | Hỗ trợ Neon Cold Start |
| `EMAIL_PROVIDER` | `resend` | HTTPS API Provider (vượt rào chặn SMTP) |
| `RESEND_API_KEY` | `<API Key từ resend.com>` | Tạo miễn phí tại Resend (nếu cần test OTP) |
| `RESEND_FROM_EMAIL` | `onboarding@resend.dev` | Email gửi mặc định của Resend |

### Bước 5: Khởi tạo và Theo dõi
Nhấp **Create Web Service**. Render sẽ bắt đầu kéo source code, cài đặt dependencies từ `requirements-server.txt` (~15-30 giây) và khởi chạy `uvicorn`.

Khi thấy dòng log:
```text
Application startup complete.
Uvicorn running on http://0.0.0.0:10000
```
Dịch vụ của bạn đã online!

---

## 4. Lấy URL và Kết nối từ Desktop Client (PySide6)

Sau khi deploy thành công, Render sẽ cấp cho bạn một URL công khai dạng:
`https://smart-parking-server.onrender.com`

### 4.1. Kiểm tra trên trình duyệt
Mở trình duyệt web và truy cập `https://smart-parking-server.onrender.com`.
Bạn sẽ thấy trang **HTML Dashboard** hiển thị thông tin máy chủ, trạng thái Neon CSDL và các endpoints.

### 4.2. Cấu hình PySide6 Desktop Client
Trên máy tính của các thành viên trong nhóm hoặc máy tính giảng viên:
1. Mở file `.env` ở thư mục ứng dụng Client.
2. Cập nhật cấu hình sang chế độ Cloud WebSocket:
   ```env
   CLIENT_TRANSPORT=websocket
   CLIENT_WS_URL=wss://smart-parking-server.onrender.com/ws
   ```
3. Chạy Desktop Client:
   ```bash
   python client/main.py
   ```
4. Giao diện LoginDialog sẽ tự động kết nối qua Internet đến Render WSS. Huy hiệu trạng thái trên góc phải sẽ chuyển sang màu xanh lá:
   `Online (WSS) · wss://smart-parking-server.onrender.com/ws`
5. Đăng nhập với các tài khoản mẫu:
   - Admin: `admin01` / `123456`
   - Quản lý: `quanly01` / `123456`
   - Nhân viên: `nhanvien01` / `123456`
   - Cư dân: `cudan_a101` / `123456`

---

## 5. Kịch bản Demo cho Giảng viên môn Lập trình mạng

Để gây ấn tượng tốt nhất trong buổi báo cáo, nhóm thực hiện theo kịch bản 3 bước sau:

1. **Bước 1 — Trực quan hóa Hạ tầng Cloud**:
   - Mở trình duyệt web máy chiếu chiếu trang `https://smart-parking-server.onrender.com`.
   - Giới thiệu kiến trúc: Hệ thống chạy trên Render Cloud (Singapore) kết nối CSDL Neon PostgreSQL 18.
   - Mở tab `https://smart-parking-server.onrender.com/health` (hiển thị JSON status ok) và `/ready` (hiển thị database connected).

2. **Bước 2 — Demo Ứng dụng Desktop qua Internet (Cloud Mode)**:
   - Mở ứng dụng PySide6 `client/main.py` trên laptop kết nối 4G/Wifi trường.
   - Chỉ vào huy hiệu `Online (WSS)` chứng minh Client kết nối qua WebSocket Secure tới Cloud Server.
   - Đăng nhập tài khoản Quản lý `quanly01`, mở danh sách 20 vị trí đỗ xe, 3 khu vực và 5 mức giá lấy thời gian thực từ Neon CSDL.
   - Thao tác đăng xuất và đăng nhập Cư dân `cudan_a101`, xem danh sách 2 xe ô tô/xe máy của căn hộ A1-01.

3. **Bước 3 — Demo Kiến trúc TCP Socket Cục bộ (Local Mode)**:
   - Đổi cấu hình `.env` client sang `CLIENT_TRANSPORT=tcp` và `CLIENT_CONNECT_HOST=127.0.0.1`.
   - Bật server local: `python server/main.py` (lắng nghe TCP port 5555).
   - Chứng minh kiến trúc tuân thủ chuẩn Lập trình mạng Socket với Length-Prefix 4-byte framing.

---

## 6. Hướng dẫn Rollback về Local (Nếu cần)

Nếu xảy ra sự cố mạng trường hoặc giảng viên yêu cầu chỉ chạy hoàn toàn cục bộ:
1. Đổi biến môi trường trong file `.env`:
   ```env
   CLIENT_TRANSPORT=tcp
   CLIENT_CONNECT_HOST=127.0.0.1
   SERVER_PORT=5555
   ```
2. Khởi chạy Server local:
   ```bash
   python server/main.py
   ```
3. Chạy Client local:
   ```bash
   python client/main.py
   ```
Toàn bộ logic hoạt động bình thường 100% trên cổng TCP 5555.
