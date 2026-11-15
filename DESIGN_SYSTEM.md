# Design System & Quy chuẩn Đồng bộ Giao diện — Smart Parking Chung cư Đào Tiên

> **Nguyên tắc vàng:** Cả 5 thành viên (**Biên, Hậu, Minh, Long, Ny**) **KHÔNG tự nghĩ màu, font chữ hay padding riêng** trong từng màn hình. Mọi giao diện PySide6 đều dùng chung 4 file lõi:
> - [design_dna.json](design_dna.json) — Hồ sơ Design DNA 3 chiều (Design System, Design Style, Visual Effects)
> - [client/ui/theme/tokens.py](client/ui/theme/tokens.py) — Hằng số màu, font, kích thước và mapping trạng thái CSDL
> - [client/ui/theme/styles.qss](client/ui/theme/styles.qss) — Stylesheet PySide6 toàn cục
> - [client/ui/theme/components.py](client/ui/theme/components.py) — Bộ Widget tái sử dụng chuẩn Enterprise

---

## 1. Khởi tạo Theme duy nhất 1 lần ở `client/main.py`

```python
import sys
from PySide6.QtWidgets import QApplication
from client.ui.theme.components import apply_theme

app = QApplication(sys.argv)
apply_theme(app)
```

---

## 2. Nhận diện 4 Vai trò (Đồng bộ khung, tách màu nhấn)

| Vai trò (`vai_tro`) | Màu nhấn (`ROLE_THEMES`) | Quy tắc hiển thị |
| :--- | :--- | :--- |
| `quan_ly` — Quản lý chung cư | Royal Blue `#2563EB` | Toàn quyền nghiệp vụ quản lý |
| `nhan_vien` — Nhân viên bãi xe | Emerald Teal `#0D9488` | Tối ưu thao tác cổng vào/ra & tra cứu nhanh |
| `cu_dan` — Cư dân | Indigo `#4F46E5` | Chỉ xem & thao tác dữ liệu của căn hộ/mình |
| `admin` — Quản trị hệ thống | Amber `#D97706` | **Bắt buộc** gắn `ReadOnlyBanner` đầu trang, vô hiệu hóa mọi nút ghi/sửa/xóa |

Sử dụng sẵn: `RoleBadge(role_code)` và `ReadOnlyBanner()`.

---

## 3. Bảng tra nhanh Component & Styles dùng chung

| Nhu cầu UI | Cách dùng chuẩn (trong `components.py`) | Không được làm |
| :--- | :--- | :--- |
| **Khung khối nội dung** | `SectionCard(title="...", subtitle="...")` rồi add widget vào `card.body_layout` | Tự vẽ `QGroupBox` hay `QFrame` style lộn xộn |
| **Thẻ số liệu KPI** | `StatCard(label, value, subtext, accent_color=Colors.ROYAL_BLUE)` | Tự ghép label cỡ chữ khác nhau |
| **Nút hành động** | `set_button_variant(btn, variant)` với `variant` là `primary`, `teal`, `success`, `danger`, `ghost`, `default` | Tự viết `setStyleSheet("background: green...")` |
| **Bảng dữ liệu** | `style_table(table)` cho mọi `QTableWidget` | Bật `showGrid(True)` hoặc để user sửa trực tiếp trong ô |
| **Nhãn trạng thái** | `StatusBadge(text, variant)` (`success`, `warning`, `danger`, `info`, `neutral`, `primary`) | Dùng chữ thuần không có badge màu |
| **Biển số xe** | `PlateBadge("51F-123.45")` (font `JetBrains Mono` / `Consolas` khung biển trắng viền đen) | Dùng font thường |
| **Sơ đồ bãi xe** | `ParkingGridWidget(columns=6)` + `set_slots(slots)` (tự map 4 trạng thái `trong`, `da_phan_cho`, `dang_co_xe`, `ngung_su_dung`) | Tự vẽ lại ô đỗ bằng màu khác |
| **Cảnh báo khẩn cấp (Workflow 01)** | `EmergencyDialog(...)` + `EmergencyBanner` với nút **"Xác nhận đã tiếp nhận"** | Chỉ hiện thông báo mờ góc dưới cho tin khẩn cấp |
| **Phản hồi thao tác / tin thường** | `ToastNotification.show_toast(window, msg, variant="success")` | Bật `QMessageBox.information` liên tục gây gián đoạn |
| **Danh sách rỗng** | `EmptyStateWidget(title, description, action_text, on_action)` | Để bảng trắng trơn không có giải thích |

---

## 4. Quy tắc Typography & Số liệu

- **Văn bản tiếng Việt:** Font `Segoe UI Variable` / `Segoe UI`, kích thước chuẩn `13px`, tiêu đề trang `20px Bold`, tiêu đề card `15px Bold`.
- **Số liệu kỹ thuật:** Mọi **Biển số xe, Mã vị trí (`A-01`), Mã hóa đơn, Số tiền (`1.300.000đ`), Timestamp Log** dùng `Fonts.MONO_FAMILY` (hoặc class `MonoValue`).
- **Khoảng cách chuẩn:** `Layout.SPACE_LG` (`16px`) giữa các Card/Section; `18px` padding trong Card; `12px` giữa các field form.

---

## 5. Phân công tuân thủ cho 5 thành viên

- **Biên:** Dùng `StatCard`, `SectionCard`, `style_table`, `ReadOnlyBanner` cho Dashboard (`1.1`) và toàn bộ cụm Admin (`4.2–4.8`).
- **Hậu:** Dùng `SectionCard`, `set_button_variant`, `StatusBadge`, `style_table` cho Login/OTP/Hồ sơ, Nhân sự, Khu vực/Vị trí, Bảng giá và Popup QR thanh toán (`3.5`, `1.7`).
- **Minh:** Hoàn thiện `MainWindow` (Sidebar `#0B132B` + Topbar `56px` + `EmergencyBanner`), nối `ParkingGridWidget` (`1.6`, `2.6`), biểu đồ Matplotlib theo `Colors` (`1.8`), Log (`1.9`) và `EmergencyDialog` (`1.10`, `2.7`, `3.6`).
- **Long:** Dùng `SectionCard`, `PlateBadge`, `StatusBadge`, `style_table` cho Đăng ký xe/upload cà vẹt (`3.2`), Duyệt xe & gán chỗ (`1.4.1–4`), Mua thêm & thu hồi chỗ (`3.3`, `1.4.15–19`), Tra cứu xe của tôi (`3.4`).
- **Ny:** Dùng `#CameraViewport` (`QLabel` objectName `CameraViewport`), `PlateBadge`, `set_button_variant("teal" / "success" / "danger")`, `style_table` cho Cổng vào/ra (`2.2`, `2.3`), Đối chiếu ca (`2.4`) và Tra cứu (`2.5`).
