# Smart Parking — Hệ thống Quản lý Bãi giữ xe Chung cư Đào Tiên

Đồ án môn **Lập trình mạng**: Ứng dụng Desktop **Client–Server** viết bằng **Python (PySide6 + TCP Socket)**, cơ sở dữ liệu **PostgreSQL** (18 bảng), tích hợp **OpenCV + OCR** nhận diện biển số thật, **QR ảo** thanh toán và **OTP Email** thật.

## Thành viên & Phân công nhánh Git

| Thành viên | Branch | Nhiệm vụ chính |
| :--- | :--- | :--- |
| **Biên (Leader)** | `feat/core-network` | Core TCP Socket, SessionManager (RAM), FileLogger (`.jsonl`), SMTP OTP, `NetworkClient`, Dashboard (`1.1`) & Admin (`4.2–4.8`) |
| **Hậu** | `feat/db-auth-infra` | PostgreSQL 18 bảng (`schema.sql`), Auth 4 role, Nhân sự (`1.3`), Khu vực & Vị trí (`1.4.5–1.4.14`), Bảng giá (`1.5`), Hóa đơn & QR/Tiền mặt (`3.5`, `1.7`) |
| **Minh** | `feat/ui-notify-stats` | UI Framework & QSS (`PySide6`), Thông báo khẩn cấp Push/ACK (`1.10`, `2.7`, `3.6`), Giám sát (`1.6`, `2.6`), Thống kê (`1.8`) & Lịch sử (`1.9`) |
| **Long** | `feat/vehicle-slots` | Đăng ký/sửa/ngừng xe & cà vẹt (`3.2`), Duyệt xe & gán chỗ (`1.4.1–1.4.4`), Mua thêm & thu hồi chỗ (`3.3`, `1.4.15–1.4.19`), Tra cứu xe của tôi (`3.4`) |
| **Ny** | `feat/gate-ai-ocr` | OpenCV + OCR nhận diện biển số thật, Nghiệp vụ cổng Vào/Ra (`2.2`, `2.3`), `active_vang_lai.json` & Log, Tiền thu ca (`2.4`), Tra cứu (`2.5`) |

## Cài đặt môi trường

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
