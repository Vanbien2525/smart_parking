# BÁO CÁO KỸ THUẬT: HOTFIX ĐỘ ỔN ĐỊNH KẾT NỐI WEBSOCKET CLIENT & DESKTOP UI
**Hệ thống Quản lý Bãi giữ xe Thông minh Chung cư Đào Tiên**
**Thời gian thực hiện**: 25/11/2026
**Môi trường thực thi**: Python 3.13.7 (.venv), PySide6 6.12.0, FastAPI 0.165.0, Uvicorn 0.54.0, websocket-client 1.9.2, psycopg 3.3.9, Neon PostgreSQL 18.7 (AWS Singapore).

---

## 1. TỔNG QUAN SỰ CỐ (ISSUE SUMMARY)

### 1.1. Hiện tượng người dùng ghi nhận
Khi cấu hình Desktop Client chạy qua đường truyền WebSocket Cloud Gateway:
```powershell
$env:CLIENT_TRANSPORT="websocket"
$env:CLIENT_WS_URL="ws://127.0.0.1:8000/ws"
python -m client.main
```
Các hiện tượng bất thường sau xuất hiện:
1. **Login Dialog mở ra**: Sau 1.5 giây, huy hiệu trạng thái kết nối chuyển sang màu đỏ với thông báo lỗi: `Connection timed out`.
2. **Người dùng bấm nút "Kết nối"**: Trạng thái chuyển xanh nhưng hiển thị `Online · 127.0.0.1:5555` (bị chuyển sang giao thức TCP ngoài ý muốn).
3. **Đăng nhập vai trò Admin (`admin01`)**: Đăng nhập thành công, màn hình Dashboard xuất hiện nhưng ngay lập tức Topbar báo lỗi màu đỏ: `Mất kết nối WebSocket: Connection timed out`.
4. **Hồ sơ Admin (Profile View) bị trắng**: Tên đăng nhập hiển thị dấu `-`, các trường thông tin tài khoản không tải được dữ liệu.

---

## 2. PHÂN TÍCH NGUYÊN NHÂN GỐC RỄ (ROOT CAUSE ANALYSIS)

Sau khi kiểm toán toàn bộ luồng mạng `client/network/socket_client.py`, giao diện `client/ui/auth/login_dialog.py`, `client/main.py` và `client/ui/auth/profile_view.py`, đội ngũ kỹ thuật đã xác định chính xác 3 nguyên nhân cốt lõi:

### Nguyên nhân 1: Nhầm lẫn giữa Timeout bắt tay (Handshake Timeout) và Timeout nhận dữ liệu (Read/Idle Timeout)
- **Vị trí**: `client/network/socket_client.py` (`_connect_websocket` và `_WebSocketReaderThread.run`).
- **Cơ chế lỗi**:
  - Khi gọi `websocket.create_connection(url, timeout=timeout)`, thư viện `websocket-client` gán giá trị `timeout` (ở đây là `1.5` từ `client/main.py` hoặc `4.0` / `10.0`) vào thuộc tính socket timeout cho **mọi thao tác đọc `recv()` tiếp theo**.
  - Luồng nền `_WebSocketReaderThread` chạy hàm `data = self._ws.recv()`.
  - Trong kiến trúc Request/Response và Server Push, khi không có người dùng thao tác và Server chưa phát Push notification (trạng thái idle/nhàn rỗi), Gateway không gửi gói tin nào.
  - Sau đúng `1.5` giây (hoặc `4.0` giây), hàm `self._ws.recv()` văng ngoại lệ `websocket.WebSocketTimeoutException: Connection timed out`.
  - `_WebSocketReaderThread` bắt ngoại lệ qua khối `except Exception as exc:`, coi đây là lỗi mất kết nối nghiêm trọng, thoát vòng lặp `while self._running` và phát tín hiệu `disconnected.emit("Mất kết nối WebSocket: Connection timed out")`.
  - Tín hiệu này đánh thức `_on_reader_disconnected`, đóng socket, xóa `self._ws = None` và đổi trạng thái toàn bộ ứng dụng sang Disconnected.

### Nguyên nhân 2: Nút "Kết nối" trong LoginDialog chiếm quyền (Hijack) và ép chuyển sang TCP Socket
- **Vị trí**: `client/ui/auth/login_dialog.py` (`_reconnect_server` và `_on_connection_changed`).
- **Cơ chế lỗi**:
  - Giao diện `LoginDialog` khởi tạo hai ô `self.txt_host` ("127.0.0.1") và `self.txt_port` ("5555"). Khi chạy WebSocket, hai ô này vẫn hiển thị `127.0.0.1` và `5555`.
  - Khi người dùng bấm nút "Kết nối", hàm `_reconnect_server()` đọc giá trị text từ hai ô này và gọi `self.socket_client.connect_to_server(host=host, port=port, timeout=4.0)`.
  - Do `host` là `127.0.0.1` (không có tiền tố `ws://`), hàm kết nối không nhận biết đây là WebSocket nếu không truyền rõ ràng `ws_url` hoặc `transport`.
  - Hơn nữa, hàm `_on_connection_changed` hiển thị chuỗi cứng:
    ```python
    self.badge_conn.setText(f"Online · {self.socket_client.host}:{self.socket_client.port}")
    ```
    dẫn đến việc huy hiệu luôn hiển thị `Online · 127.0.0.1:5555`, gây hiểu lầm là đã chuyển sang TCP.
  - Kế tiếp, timeout `4.0s` lại được gán vào socket read timeout, khiến Dashboard bị ngắt kết nối sau 4 giây tiếp theo.

### Nguyên nhân 3: Hồ sơ Admin (Profile View) bị trắng do kết nối bị ngắt trước khi nạp dữ liệu
- **Vị trí**: `client/ui/auth/profile_view.py` (`refresh_data`) và `client/ui/main_window.py` (`load_initial_data`).
- **Cơ chế lỗi**:
  - Khi người dùng đăng nhập Admin, `LoginDialog` gọi `auth.login` thành công vì thao tác đăng nhập diễn ra trước khi chạm mốc timeout.
  - Ngay khi `MainWindow` hiển thị và bắt đầu chạy `window.load_initial_data()`, luồng đọc `_WebSocketReaderThread` vừa chạm mốc socket timeout và phát tín hiệu ngắt kết nối.
  - Khi `ProfileView.refresh_data()` được kích hoạt, nó kiểm tra:
    ```python
    if self.socket_client is None or not self.socket_client.is_connected():
        return
    ```
  - Do `is_connected()` đã trở thành `False`, hàm `refresh_data()` lập tức bỏ qua lệnh gọi `Actions.AUTH_GET_PROFILE`.
  - Kết quả: Các trường `lbl_username`, `txt_ho_ten`, `txt_email`... giữ nguyên giá trị khởi tạo mặc định là `-` và rỗng.

---

## 3. CHI TIẾT CÁC THAY ĐỔI MÃ NGUỒN (CODE MODIFICATIONS)

### 3.1. Sửa `client/network/socket_client.py`
Tách biệt triệt để Connection Timeout (dành cho DNS, TCP, TLS, WebSocket Handshake) và Read Timeout (dành cho luồng đọc `recv`):
1. **Thiết lập chu kỳ thăm dò (Polling interval)**: Sau khi kết nối thành công, đặt `self._ws.settimeout(1.0)` để `recv()` định kỳ giải phóng quyền điều khiển và kiểm tra trạng thái `self._running`.
2. **Xử lý an toàn `WebSocketTimeoutException`**: Khi `recv()` bị timeout trong lúc đường truyền nhàn rỗi, ngoại lệ `websocket.WebSocketTimeoutException` (cùng `socket.timeout` và `TimeoutError`) được bắt và thực hiện lệnh `continue`. Đây là hành vi nhàn rỗi bình thường, tuyệt đối **không ngắt kết nối** và **không phát tín hiệu `disconnected`**.
3. **Nhận diện chính xác ngắt kết nối thực sự**: Chỉ ngắt kết nối khi máy chủ đóng kênh (`WebSocketConnectionClosedException`, `EOFError`, `ConnectionResetError`, hoặc `data is None / "" / b""`).
4. **Hiển thị giao thức minh bạch**: Tín hiệu kết nối phát ra ghi rõ giao thức `WS` hoặc `WSS` tương ứng với URL.

```python
# client/network/socket_client.py
class _WebSocketReaderThread(QThread):
    ...
    def run(self) -> None:
        reason = "Đã ngắt kết nối WebSocket."
        try:
            if hasattr(self._ws, "settimeout"):
                self._ws.settimeout(1.0)
        except Exception:
            pass

        ws_timeout_types = (socket.timeout, TimeoutError)
        ws_closed_types = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, EOFError)
        if websocket is not None:
            ws_timeout_types = (websocket.WebSocketTimeoutException, socket.timeout, TimeoutError)
            ws_closed_types = (websocket.WebSocketConnectionClosedException, *ws_closed_types)

        try:
            while self._running:
                try:
                    data = self._ws.recv()
                except ws_timeout_types:
                    # Timeout khi nhận dữ liệu khi kết nối nhàn rỗi (idle) KHÔNG PHẢI lỗi ngắt kết nối.
                    continue
                except ws_closed_types as exc:
                    reason = f"Máy chủ đã đóng kết nối WebSocket: {exc}"
                    break
                except OSError as exc:
                    if not self._running:
                        break
                    reason = f"Lỗi socket WebSocket: {exc}"
                    break

                if data is None or data == "" or data == b"":
                    reason = "Máy chủ đã đóng kết nối WebSocket."
                    break

                # Xử lý frame Length-Prefix + JSON...
```

### 3.2. Sửa `client/ui/auth/login_dialog.py`
1. **Giao diện tự thích ứng với Transport**:
   - Nếu client đang chạy `websocket`, `self.txt_host` hiển thị `ws_url` (ví dụ `ws://127.0.0.1:8000/ws` hoặc `wss://...`), ô nhập `txt_port` được ẩn đi nhằm tránh người dùng hiểu lầm.
   - Nếu chạy `tcp`, hiển thị `Host` và `Port` như cũ.
2. **Cơ chế Reconnect thông minh**:
   - Tự động nhận diện chuỗi `ws://` hoặc `wss://` từ ô nhập liệu để kết nối WebSocket.
   - Nếu `transport` đang là `websocket`, tái kết nối qua `self.socket_client.connect_to_server(ws_url=..., transport="websocket")` mà không bị giáng cấp về TCP.
3. **Đồng bộ huy hiệu trạng thái chính xác**:
   - Hiển thị rõ ràng `Online (WS) · ws://127.0.0.1:8000/ws` hoặc `Online (WSS) · wss://...` hoặc `Online (TCP) · 127.0.0.1:5555`.

### 3.3. Sửa `client/main.py`
Nâng thời gian chờ bắt tay ban đầu từ `1.5s` lên `3.0s` (`socket_client.connect_to_server(timeout=3.0)`) để thích ứng an toàn với độ trễ mạng Internet và chu kỳ Cold Start của Render Web Service.

---

## 4. BẢNG TỔNG HỢP KẾT QUẢ KIỂM THỬ (TEST VERIFICATION MATRIX)

Tất cả các bài kiểm thử đã được chạy trên môi trường thực tế `.venv` với Python 3.13.7 và Neon PostgreSQL Cloud Singapore:

| STT | Bộ kiểm thử | Mục tiêu kiểm chứng | Số test | Kết quả | Trạng thái |
| :---: | :--- | :--- | :---: | :---: | :---: |
| 1 | `test_neon.py` | Schema 18 bảng, 28 FKs, 6 indexes, 46 records mẫu, SSL/TLS require, Transaction rollback | 21 / 21 | **100% PASS** | CSDL toàn vẹn |
| 2 | `test_client_server.py` | TCP Socket Core 5555, Request/Response Length-Prefix, RBAC 4 vai trò, Cư dân, Xe, Hóa đơn | 17 / 17 | **100% PASS** | TCP chuẩn xác |
| 3 | `test_websocket_gateway.py` | HTTP GET `/`, `/health`, `/ready`, WebSocket WSS forwarding, Multi-client concurrency | 9 / 9 | **100% PASS** | Gateway sẵn sàng |
| 4 | `test_websocket_stability.py` | **Hotfix**: WebSocket Idle 4.0s không disconnect, Ping latency, Login Admin, Nạp Profile, Reconnect | 12 / 12 | **100% PASS** | **Khắc phục triệt để** |
| **Tổng** | **Toàn bộ hệ thống** | **Kiểm thử liên hoàn tích hợp đa tầng** | **59 / 59** | **100% PASS** | **SẴN SÀNG KIỂM THỬ** |

---

## 5. NHẬT KÝ KIỂM THỬ ĐỘ ỔN ĐỊNH WEBSOCKET THỰC TẾ (`test_websocket_stability.py`)

```text
===========================================================================
   SMART PARKING — WEBSOCKET CLIENT STABILITY & HOTFIX VERIFICATION
===========================================================================

[PASS]   Gateway Startup — Uvicorn lắng nghe tại ws://127.0.0.1:8001/ws
[PASS]   WebSocket Handshake — Đã kết nối thành công tới ws://127.0.0.1:8001/ws qua SocketClient
[PASS]   WebSocket Idle Stability (4.0s) — Giữ kết nối nhàn rỗi 4.0s hoàn toàn ổn định (không xảy ra timeout disconnect)
[PASS]   WebSocket Heartbeat Ping — Phản hồi Ping/Pong thành công (latency=1ms)
[PASS]   WebSocket Admin Login — Đăng nhập thành công tài khoản 'admin01' (vai_tro=admin)
[PASS]   Admin Profile Query (auth.get_profile) — Hồ sơ tải thành công: ten_dang_nhap=admin01, ho_ten=admin01
[PASS]   Admin Accounts List (admin.list_accounts) — Tải thành công 5 tài khoản từ CSDL Neon qua WebSocket
[PASS]   Multi-role Auth over WebSocket — Đăng nhập và phân quyền chính xác cho Quản lý, Nhân viên, Cư dân qua WebSocket
[PASS]   WebSocket Reconnect Action — Tái kết nối thành công, giữ nguyên giao thức WebSocket không bị chuyển nhầm sang TCP
[PASS]   Post-reconnect Operation — Giao tiếp Request/Response tiếp tục hoạt động bình thường sau tái kết nối
[PASS]   Graceful Disconnect — Reader QThread đã dừng hoàn toàn, socket giải phóng an toàn
[PASS]   Gateway Teardown — Đã dọn dẹp tiến trình Uvicorn và giải phóng port

---------------------------------------------------------------------------
TOTAL STABILITY CHECKS: 12
PASSED:                 12
FAILED:                 0
---------------------------------------------------------------------------

[SUCCESS] Hotfix WebSocket hoạt động hoàn hảo, sẵn sàng cho kiểm thử người dùng!
```

---

## 6. HƯỚNG DẪN KIỂM THỬ THỦ CÔNG DÀNH CHO NGƯỜI DÙNG (MANUAL VERIFICATION STEPS)

Người dùng thực hiện các bước sau trên máy tính cá nhân để xác minh trực quan trên giao diện đồ họa PySide6:

### Bước 1: Khởi động Cloud Gateway (Terminal 1)
```powershell
# Chuyển vào thư mục mã nguồn
cd c:\Users\LENOVO\Downloads\smart_parking-main\smart_parking-main

# Khởi chạy Gateway
.\.venv\Scripts\python.exe -m uvicorn server.gateway:app --host 127.0.0.1 --port 8000
```
*Quan sát log Terminal 1*: Hiển thị thông báo kết nối Neon PostgreSQL thành công và Internal TCP Server Core chạy tại port 5555.

### Bước 2: Khởi chạy Desktop Client ở chế độ WebSocket (Terminal 2)
```powershell
cd c:\Users\LENOVO\Downloads\smart_parking-main\smart_parking-main

# Kích hoạt chế độ WebSocket
$env:CLIENT_TRANSPORT="websocket"
$env:CLIENT_WS_URL="ws://127.0.0.1:8000/ws"

# Khởi chạy PySide6 Client
.\.venv\Scripts\python.exe -m client.main
```

### Bước 3: Kiểm tra màn hình Đăng nhập (Login Dialog)
1. **Quan sát huy hiệu kết nối**: Hiển thị màu xanh lá cây: `Online (WS) · ws://127.0.0.1:8000/ws`.
2. **Kiểm tra trạng thái Idle**: Để yên màn hình trong hơn 10 giây. Huy hiệu **không bị chuyển sang màu đỏ** và không báo `Connection timed out`.
3. **Kiểm tra nút "Kết nối"**: Bấm thử nút "Kết nối". Trạng thái vẫn duy trì `Online (WS)` kết nối tới Gateway port 8000, không bị nhảy sang TCP port 5555.

### Bước 4: Đăng nhập Admin và kiểm tra Dashboard
1. Nhập thông tin:
   - **Tên đăng nhập**: `admin01`
   - **Mật khẩu**: `123456`
2. Bấm **Đăng nhập**.
3. **Kiểm tra Topbar**: Huy hiệu Socket hiển thị chấm xanh `● Online · Xms` ổn định, không hiện thông báo lỗi mất kết nối.
4. **Kiểm tra Màn hình 4.1 "Hồ sơ Admin"**:
   - Tên đăng nhập: `admin01`.
   - Trạng thái: `hoat_dong`.
   - Đơn vị / Chức năng: `Quản trị hệ thống (Read-Only)`.
   - Toàn bộ thông tin hiển thị đầy đủ, không bị rỗng hay hiển thị dấu `-`.
5. **Kiểm tra Màn hình 4.2 "Tài khoản hệ thống"**: Hiển thị đủ 5 tài khoản hệ thống từ CSDL Neon Cloud Singapore.

---

## 7. KẾT LUẬN & TRẠNG THÁI HIỆN TẠI
- Toàn bộ các nguyên nhân gây mất kết nối, hiển thị sai giao thức và rỗng dữ liệu hồ sơ đã được giải quyết triệt để ở tầng socket/thread.
- Kiến trúc Dual Transport (TCP Socket Local 5555 & WebSocket Cloud 8000) bảo toàn 100% tính tương thích.
- CSDL Neon PostgreSQL hoàn toàn nguyên vẹn, không có bất kỳ thao tác xóa/reset nào.
- Trạng thái dự án: **`READY FOR MANUAL VERIFICATION`**.
