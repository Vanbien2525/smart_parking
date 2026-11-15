# Kế Hoạch Triển Khai Dự Án Smart Parking — Chung Cư Đào Tiên (5 Thành Viên)

Tài liệu này tổng hợp bộ kế hoạch kỹ thuật chi tiết cho toàn bộ **5 thành viên**, được đồng bộ theo hệ thống thiết kế tại [`DESIGN_SYSTEM.md`](../../DESIGN_SYSTEM.md), [`design_dna.json`](../../design_dna.json) và kiến trúc **Python Desktop (`PySide6`) Client–Server qua TCP Socket + PostgreSQL (18 bảng chuẩn) + State/File Logs JSONL**.

---

## 1. Danh Sách Kế Hoạch Chi Tiết Theo Thành Viên

| STT | Thành viên | Nhánh Git | Trọng trách chính | Tài liệu chi tiết |
| :-: | :--- | :--- | :--- | :--- |
| **1** | **Biên (Leader)** | `feat/core-network` | Giao thức TCP Socket (`protocol.py`), `SocketServer`/`SocketClient`, `SessionManager`, `FileLogger`, `StateStore`, `OTPService`, Dashboard Quản lý (`1.1`), Admin Read-Only & System (`4.2–4.8`) | [01_leader_bien_core_network.md](./01_leader_bien_core_network.md) |
| **2** | **Hậu (TV2)** | `feat/db-auth-infra` | PostgreSQL 18 bảng (`schema.sql`, `seed_data.sql`), `db.py`, Đăng nhập & OTP 4 vai trò (`1.2`, `2.1`, `3.1`, `4.1`), Nhân sự (`1.3`), Khu vực & Ô đỗ (`1.4.5–1.4.14`), Bảng giá (`1.5`), Hóa đơn & Thanh toán Cư dân (`3.5` — `Workflow 06`), Theo dõi phí (`1.7`) | [02_hau_db_auth_infra.md](./02_hau_db_auth_infra.md) |
| **3** | **Minh (TV3)** | `feat/ui-notify-stats` | Khung `MainWindow` (Sidebar + Topbar + `EmergencyBanner`), Thông báo & Cảnh báo khẩn cấp (`1.10`, `2.7`, `3.6` — `Workflow 01`), Giám sát bãi xe `2D Grid` + `3D Sa bàn` tương tác (`1.6`, `2.6`), Thống kê Matplotlib (`1.8`), Lịch sử vào/ra (`1.9`) | [03_minh_ui_notify_stats.md](./03_minh_ui_notify_stats.md) |
| **4** | **Long (TV4)** | `feat/vehicle-slots` | Đăng ký xe Cư dân & Upload cà-vẹt Base64 (`3.2` — `Workflow 02`), Quản lý duyệt xe & Sơ đồ gán ô đỗ (`1.4.1–1.4.4` — `Workflow 05`), Mua thêm & Thu hồi chỗ đỗ Ô tô (`3.3`, `1.4.15–1.4.19`), Tra cứu & Định vị xe Cư dân trên Mini-Map 2D/3D (`3.4`) | [04_long_vehicle_slots.md](./04_long_vehicle_slots.md) |
| **5** | **Ny (TV5)** | `feat/gate-ai-ocr` | `Live Webcam (OpenCV)` + Hybrid AI (`Gemini Vision API` + `YOLO/EasyOCR` fallback), Cổng Vào Ticketless (`2.2` — `Workflow 03`), Cổng Ra & Tính phí Vãng lai (`2.3` — `Workflow 04`), Doanh thu Ca trực (`2.4`), Tra cứu xe Nhân viên (`2.5`) | [05_ny_gate_ai_ocr.md](./05_ny_gate_ai_ocr.md) |

---

## 2. Thứ Tự Triển Khai & Tích Hợp (4 Giai Đoạn)

1. **Giai đoạn 1 — Khung Mạng & Giao Thức (`Biên`):**
   - Hoàn thiện `shared/` (`constants.py`, `protocol.py`, `validators.py`), `server/core/` và `client/network/socket_client.py` để làm nền tảng giao tiếp cho cả nhóm.
2. **Giai đoạn 2 — CSDL 18 Bảng, Đăng Nhập & Hạ Tầng (`Hậu` + `Minh` song song):**
   - **Hậu** dựng `database/schema.sql`, `seed_data.sql`, `server/db.py`, luồng Đăng nhập/OTP và CRUD Căn hộ / Khu vực / Ô đỗ / Bảng giá.
   - **Minh** dựng khung `MainWindow` chung, hệ thống Thông báo Push & Giám sát bãi xe `2D/3D`.
3. **Giai đoạn 3 — Nghiệp Vụ Cốt Lõi & Kiểm Soát Cổng (`Long` + `Ny` song song):**
   - **Long** phát triển luồng Đăng ký xe, Upload cà-vẹt, Duyệt gán ô đỗ cố định và Mua thêm/Thu hồi chỗ.
   - **Ny** phát triển `Live Webcam + AI Nhận diện biển số`, Cổng Vào/Ra Ticketless, Tính phí vãng lai và Ca làm việc.
4. **Giai đoạn 4 — Ghép Nhánh, Dashboard Quản Lý/Admin & Kiểm Thử Toàn Hệ Thống (`Cả 5 thành viên`):**
   - **Biên** hoàn thiện Dashboard Quản lý (`1.1`) và ghép các màn hình Read-Only cho Admin (`4.2–4.8`).
   - Toàn nhóm chạy integration test end-to-end từ `tests/` và hoàn thiện kịch bản demo 6 Workflow.

---

## 3. Quy Tắc Đồng Bộ UI & Tài Liệu Tham Chiếu

- Mọi màn hình PySide6 sử dụng chung bảng màu, typography và component chuẩn từ `client/ui/theme/` (`tokens.py`, `styles.qss`, `components.py`) được định nghĩa trong [`DESIGN_SYSTEM.md`](../../DESIGN_SYSTEM.md).
- Không tự ý thêm bảng mới ngoài **18 bảng & 28 khóa ngoại** trong `schema.sql`; mọi trạng thái phiên mở vãng lai, session socket và log hệ thống/vào-ra đều đi qua `StateStore` (`storage/state/`) và `FileLogger` (`storage/logs/*.jsonl`).
