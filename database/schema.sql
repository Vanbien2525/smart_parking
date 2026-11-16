-- =============================================================================
-- Smart Parking — Chung cư Đào Tiên
-- Schema PostgreSQL chuẩn: 18 Bảng & 28 Khóa ngoại (FK)
-- Đồng bộ 100% với csdl_workflow siu cấp.html và shared/constants.py
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- BẢNG 01: tai_khoan — Tài khoản đăng nhập & Phân quyền (4 vai trò)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tai_khoan (
    ma_tai_khoan INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ten_dang_nhap VARCHAR(50) NOT NULL UNIQUE,
    mat_khau_bam VARCHAR(255) NOT NULL,
    vai_tro VARCHAR(20) NOT NULL
        CHECK (vai_tro IN ('quan_ly', 'nhan_vien', 'cu_dan', 'admin')),
    trang_thai VARCHAR(20) NOT NULL DEFAULT 'hoat_dong'
        CHECK (trang_thai IN ('hoat_dong', 'tam_khoa')),
    ly_do_khoa VARCHAR(255),
    ngay_tao TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- BẢNG 02: can_ho — Căn hộ chung cư & Định mức chỗ đỗ ô tô
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS can_ho (
    ma_can_ho VARCHAR(20) PRIMARY KEY,
    toa_nha VARCHAR(20) NOT NULL,
    so_tang INT NOT NULL CHECK (so_tang >= 0),
    so_cho_oto_mac_dinh INT NOT NULL DEFAULT 1 CHECK (so_cho_oto_mac_dinh >= 0),
    so_cho_oto_mua_them INT NOT NULL DEFAULT 0 CHECK (so_cho_oto_mua_them >= 0),
    trang_thai_can_ho VARCHAR(25) NOT NULL DEFAULT 'dang_su_dung'
        CHECK (trang_thai_can_ho IN ('dang_su_dung', 'trong', 'ngung_su_dung', 'tam_ngung'))
);

-- -----------------------------------------------------------------------------
-- BẢNG 03: cu_dan — Hồ sơ Cư dân chung cư
-- (FK #1 -> tai_khoan, FK #2 -> can_ho)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cu_dan (
    ma_cu_dan INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_tai_khoan INT NOT NULL UNIQUE,
    ma_can_ho VARCHAR(20) NOT NULL,
    ho_ten VARCHAR(100) NOT NULL,
    so_cccd VARCHAR(20) NOT NULL UNIQUE,
    so_dien_thoai VARCHAR(15) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    dia_chi VARCHAR(255),
    CONSTRAINT fk_cu_dan_tai_khoan
        FOREIGN KEY (ma_tai_khoan) REFERENCES tai_khoan (ma_tai_khoan),
    CONSTRAINT fk_cu_dan_can_ho
        FOREIGN KEY (ma_can_ho) REFERENCES can_ho (ma_can_ho)
);

-- -----------------------------------------------------------------------------
-- BẢNG 04: nhan_vien — Hồ sơ Nhân viên bãi xe & Quản lý chung cư
-- (FK #3 -> tai_khoan)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nhan_vien (
    ma_nhan_vien INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_tai_khoan INT NOT NULL UNIQUE,
    ma_nhan_vien_code VARCHAR(20) NOT NULL UNIQUE,
    ho_ten VARCHAR(100) NOT NULL,
    so_cccd VARCHAR(20) NOT NULL UNIQUE,
    so_dien_thoai VARCHAR(15) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    chuc_vu VARCHAR(30) NOT NULL,
    CONSTRAINT fk_nhan_vien_tai_khoan
        FOREIGN KEY (ma_tai_khoan) REFERENCES tai_khoan (ma_tai_khoan)
);

-- -----------------------------------------------------------------------------
-- BẢNG 05: loai_phuong_tien — Danh mục Loại phương tiện (Ô tô, Xe máy)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS loai_phuong_tien (
    ma_loai_xe VARCHAR(20) PRIMARY KEY,
    ten_loai_xe VARCHAR(50) NOT NULL,
    kieu_phan_cho VARCHAR(30) NOT NULL
        CHECK (kieu_phan_cho IN ('gan_vi_tri_co_dinh', 'phan_theo_khu_vuc')),
    mo_ta VARCHAR(255)
);

-- -----------------------------------------------------------------------------
-- BẢNG 06: khu_vuc — Khu vực bãi giữ xe
-- (FK #4 -> loai_phuong_tien)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS khu_vuc (
    ma_khu_vuc VARCHAR(20) PRIMARY KEY,
    ten_khu_vuc VARCHAR(100) NOT NULL,
    ma_loai_xe VARCHAR(20) NOT NULL,
    tong_so_cho INT NOT NULL DEFAULT 0 CHECK (tong_so_cho >= 0),
    so_xe_dang_gui INT NOT NULL DEFAULT 0 CHECK (so_xe_dang_gui >= 0),
    trang_thai_khu_vuc VARCHAR(25) NOT NULL DEFAULT 'hoat_dong'
        CHECK (trang_thai_khu_vuc IN ('hoat_dong', 'ngung_su_dung')),
    ly_do_ngung VARCHAR(255),
    CONSTRAINT fk_khu_vuc_loai_phuong_tien
        FOREIGN KEY (ma_loai_xe) REFERENCES loai_phuong_tien (ma_loai_xe)
);

-- -----------------------------------------------------------------------------
-- BẢNG 07: vi_tri_do — Vị trí đỗ / Ô đỗ xe cụ thể
-- (FK #5 -> khu_vuc)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vi_tri_do (
    ma_vi_tri VARCHAR(20) PRIMARY KEY,
    ma_khu_vuc VARCHAR(20) NOT NULL,
    ten_vi_tri VARCHAR(50) NOT NULL,
    nhom_vi_tri VARCHAR(30) NOT NULL
        CHECK (nhom_vi_tri IN ('vi_tri_thuong', 'vi_tri_dau_bai', 'vi_tri_vang_lai')),
    trang_thai_vi_tri VARCHAR(25) NOT NULL DEFAULT 'trong'
        CHECK (trang_thai_vi_tri IN ('trong', 'da_phan_cho', 'dang_co_xe', 'ngung_su_dung')),
    ly_do_ngung VARCHAR(255),
    thoi_gian_cap_nhat TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_vi_tri_do_khu_vuc
        FOREIGN KEY (ma_khu_vuc) REFERENCES khu_vuc (ma_khu_vuc)
);

-- -----------------------------------------------------------------------------
-- BẢNG 08: phan_bo_cho_do — Phân bổ & Thu hồi quyền sử dụng chỗ đỗ Ô tô
-- (FK #6 -> can_ho, FK #7 -> vi_tri_do)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS phan_bo_cho_do (
    ma_phan_bo INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_can_ho VARCHAR(20) NOT NULL,
    ma_vi_tri VARCHAR(20) NOT NULL,
    loai_cho_cap VARCHAR(30) NOT NULL
        CHECK (loai_cho_cap IN ('cho_mac_dinh_xe_1', 'cho_mua_them')),
    ngay_cap TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trang_thai_su_dung VARCHAR(25) NOT NULL DEFAULT 'dang_hieu_luc'
        CHECK (trang_thai_su_dung IN ('dang_hieu_luc', 'da_thu_hoi')),
    ly_do_thu_hoi VARCHAR(255),
    CONSTRAINT fk_phan_bo_can_ho
        FOREIGN KEY (ma_can_ho) REFERENCES can_ho (ma_can_ho),
    CONSTRAINT fk_phan_bo_vi_tri_do
        FOREIGN KEY (ma_vi_tri) REFERENCES vi_tri_do (ma_vi_tri)
);

-- -----------------------------------------------------------------------------
-- BẢNG 09: phuong_tien — Hồ sơ Phương tiện của Cư dân
-- (FK #8 -> cu_dan, FK #9 -> loai_phuong_tien)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS phuong_tien (
    ma_phuong_tien INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_cu_dan INT NOT NULL,
    ma_loai_xe VARCHAR(20) NOT NULL,
    bien_so_xe VARCHAR(20) NOT NULL UNIQUE,
    ten_hang_dong_xe VARCHAR(100) NOT NULL,
    mau_xe VARCHAR(50),
    duong_dan_cavet VARCHAR(255) NOT NULL,
    trang_thai_xe VARCHAR(25) NOT NULL DEFAULT 'cho_duyet'
        CHECK (trang_thai_xe IN ('cho_duyet', 'dang_hoat_dong', 'ngung_hoat_dong', 'ngung_hieu_luc')),
    CONSTRAINT fk_phuong_tien_cu_dan
        FOREIGN KEY (ma_cu_dan) REFERENCES cu_dan (ma_cu_dan),
    CONSTRAINT fk_phuong_tien_loai_xe
        FOREIGN KEY (ma_loai_xe) REFERENCES loai_phuong_tien (ma_loai_xe)
);

-- -----------------------------------------------------------------------------
-- BẢNG 10: yeu_cau_dang_ky_xe — Yêu cầu Đăng ký mới, Sửa, Ngừng đăng ký xe
-- (FK #10 -> cu_dan, FK #11 -> phuong_tien, FK #12 -> nhan_vien)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS yeu_cau_dang_ky_xe (
    ma_yeu_cau_xe INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_cu_dan INT NOT NULL,
    ma_phuong_tien INT NOT NULL,
    loai_yeu_cau VARCHAR(30) NOT NULL
        CHECK (loai_yeu_cau IN ('dang_ky_moi', 'chinh_sua_thong_tin', 'ngung_dang_ky')),
    vi_tri_mong_muon VARCHAR(100),
    noi_dung_thay_doi VARCHAR(500),
    trang_thai_duyet VARCHAR(25) NOT NULL DEFAULT 'cho_duyet'
        CHECK (trang_thai_duyet IN ('cho_duyet', 'da_duyet', 'tu_choi')),
    ma_nhan_vien_duyet INT,
    ly_do_phan_hoi VARCHAR(255),
    ngay_gui_yeu_cau TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_yc_xe_cu_dan
        FOREIGN KEY (ma_cu_dan) REFERENCES cu_dan (ma_cu_dan),
    CONSTRAINT fk_yc_xe_phuong_tien
        FOREIGN KEY (ma_phuong_tien) REFERENCES phuong_tien (ma_phuong_tien),
    CONSTRAINT fk_yc_xe_nhan_vien_duyet
        FOREIGN KEY (ma_nhan_vien_duyet) REFERENCES nhan_vien (ma_nhan_vien)
);

-- -----------------------------------------------------------------------------
-- BẢNG 11: yeu_cau_mua_them_cho — Yêu cầu Mua thêm chỗ đỗ Ô tô của Căn hộ
-- (FK #13 -> cu_dan, FK #14 -> can_ho, FK #15 -> vi_tri_do, FK #16 -> nhan_vien)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS yeu_cau_mua_them_cho (
    ma_yeu_cau_cho INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_cu_dan INT NOT NULL,
    ma_can_ho VARCHAR(20) NOT NULL,
    nhu_cau_vi_tri VARCHAR(255) NOT NULL,
    ly_do_mua_them VARCHAR(255) NOT NULL,
    trang_thai_duyet VARCHAR(25) NOT NULL DEFAULT 'cho_duyet'
        CHECK (trang_thai_duyet IN ('cho_duyet', 'da_duyet', 'tu_choi', 'da_huy')),
    ma_vi_tri_duoc_duyet VARCHAR(20),
    ma_nhan_vien_duyet INT,
    ly_do_tu_choi VARCHAR(255),
    ngay_gui_yeu_cau TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_yc_cho_cu_dan
        FOREIGN KEY (ma_cu_dan) REFERENCES cu_dan (ma_cu_dan),
    CONSTRAINT fk_yc_cho_can_ho
        FOREIGN KEY (ma_can_ho) REFERENCES can_ho (ma_can_ho),
    CONSTRAINT fk_yc_cho_vi_tri_duyet
        FOREIGN KEY (ma_vi_tri_duoc_duyet) REFERENCES vi_tri_do (ma_vi_tri),
    CONSTRAINT fk_yc_cho_nhan_vien_duyet
        FOREIGN KEY (ma_nhan_vien_duyet) REFERENCES nhan_vien (ma_nhan_vien)
);

-- -----------------------------------------------------------------------------
-- BẢNG 12: quan_ly_phuong_tien_ra_vao — Quản lý Phương tiện Chung cư Ra/Vào
-- (FK #17 -> phuong_tien, FK #18 -> vi_tri_do, FK #19 -> khu_vuc)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quan_ly_phuong_tien_ra_vao (
    ma_quan_ly_xe INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_phuong_tien INT NOT NULL UNIQUE,
    ma_vi_tri_co_dinh VARCHAR(20) UNIQUE,
    ma_khu_vuc_phan_cong VARCHAR(20),
    thoi_gian_vao TIMESTAMP,
    thoi_gian_ra TIMESTAMP,
    dang_trong_bai BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT fk_qlrv_phuong_tien
        FOREIGN KEY (ma_phuong_tien) REFERENCES phuong_tien (ma_phuong_tien),
    CONSTRAINT fk_qlrv_vi_tri_co_dinh
        FOREIGN KEY (ma_vi_tri_co_dinh) REFERENCES vi_tri_do (ma_vi_tri),
    CONSTRAINT fk_qlrv_khu_vuc_phan_cong
        FOREIGN KEY (ma_khu_vuc_phan_cong) REFERENCES khu_vuc (ma_khu_vuc)
);

-- -----------------------------------------------------------------------------
-- BẢNG 13: bang_gia — Cấu hình Bảng giá dịch vụ gửi xe
-- (FK #20 -> loai_phuong_tien)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bang_gia (
    ma_bang_gia INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_loai_xe VARCHAR(20) NOT NULL,
    nhom_ap_dung VARCHAR(40) NOT NULL
        CHECK (nhom_ap_dung IN ('cho_oto_mac_dinh', 'cho_oto_mua_them', 'xe_may_cu_dan', 'khach_vang_lai')),
    don_vi_tinh VARCHAR(20) NOT NULL
        CHECK (don_vi_tinh IN ('thang', 'gio', 'luot')),
    muc_phi NUMERIC(12, 2) NOT NULL CHECK (muc_phi >= 0),
    thoi_gian_bat_dau TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    thoi_gian_ket_thuc TIMESTAMP,
    trang_thai_gia VARCHAR(25) NOT NULL DEFAULT 'dang_ap_dung'
        CHECK (trang_thai_gia IN ('dang_ap_dung', 'ngung_ap_dung')),
    CONSTRAINT fk_bang_gia_loai_xe
        FOREIGN KEY (ma_loai_xe) REFERENCES loai_phuong_tien (ma_loai_xe)
);

-- -----------------------------------------------------------------------------
-- BẢNG 14: bao_cao_luot_va_thu_phi — Báo cáo Lượt xe Ra/Vào & Thu phí Vãng lai
-- (FK #21 -> nhan_vien, FK #22 -> khu_vuc)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bao_cao_luot_va_thu_phi (
    ma_bao_cao INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_nhan_vien INT NOT NULL,
    ma_khu_vuc VARCHAR(20) NOT NULL,
    ngay_ghi_nhan DATE NOT NULL,
    ca_lam_viec VARCHAR(20) NOT NULL
        CHECK (ca_lam_viec IN ('ca_sang', 'ca_chieu', 'ca_dem')),
    so_luot_oto_vang_lai INT NOT NULL DEFAULT 0 CHECK (so_luot_oto_vang_lai >= 0),
    so_luot_xemay_vang_lai INT NOT NULL DEFAULT 0 CHECK (so_luot_xemay_vang_lai >= 0),
    so_luot_xe_cu_dan INT NOT NULL DEFAULT 0 CHECK (so_luot_xe_cu_dan >= 0),
    tong_tien_thu_vang_lai NUMERIC(14, 2) NOT NULL DEFAULT 0 CHECK (tong_tien_thu_vang_lai >= 0),
    CONSTRAINT fk_bao_cao_nhan_vien
        FOREIGN KEY (ma_nhan_vien) REFERENCES nhan_vien (ma_nhan_vien),
    CONSTRAINT fk_bao_cao_khu_vuc
        FOREIGN KEY (ma_khu_vuc) REFERENCES khu_vuc (ma_khu_vuc)
);

-- -----------------------------------------------------------------------------
-- BẢNG 15: hoa_don_thang — Hóa đơn Phí gửi xe hàng tháng của Căn hộ
-- (FK #23 -> can_ho)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hoa_don_thang (
    ma_hoa_don INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_can_ho VARCHAR(20) NOT NULL,
    ky_thu_thang_nam VARCHAR(7) NOT NULL,
    so_cho_oto_mac_dinh INT NOT NULL DEFAULT 0 CHECK (so_cho_oto_mac_dinh >= 0),
    so_cho_oto_mua_them INT NOT NULL DEFAULT 0 CHECK (so_cho_oto_mua_them >= 0),
    so_luong_xe_may INT NOT NULL DEFAULT 0 CHECK (so_luong_xe_may >= 0),
    tong_tien_phai_thu NUMERIC(14, 2) NOT NULL DEFAULT 0 CHECK (tong_tien_phai_thu >= 0),
    trang_thai_hoa_don VARCHAR(25) NOT NULL DEFAULT 'chua_thanh_toan'
        CHECK (trang_thai_hoa_don IN ('chua_thanh_toan', 'da_thanh_toan', 'qua_han')),
    ngay_tao_hoa_don TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_hoa_don_can_ho
        FOREIGN KEY (ma_can_ho) REFERENCES can_ho (ma_can_ho),
    CONSTRAINT uq_hoa_don_can_ho_ky UNIQUE (ma_can_ho, ky_thu_thang_nam)
);

-- -----------------------------------------------------------------------------
-- BẢNG 16: giao_dich_thanh_toan — Giao dịch Thanh toán Hóa đơn & Biên nhận
-- (FK #24 -> hoa_don_thang, FK #25 -> nhan_vien)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS giao_dich_thanh_toan (
    ma_giao_dich INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_hoa_don INT NOT NULL,
    hinh_thuc_thanh_toan VARCHAR(25) NOT NULL
        CHECK (hinh_thuc_thanh_toan IN ('chuyen_khoan_qr', 'tien_mat')),
    so_tien_thanh_toan NUMERIC(14, 2) NOT NULL CHECK (so_tien_thanh_toan >= 0),
    ma_qr VARCHAR(255),
    han_ma_qr TIMESTAMP,
    ma_bien_lai VARCHAR(50) UNIQUE,
    ma_nhan_vien_thu INT,
    trang_thai_giao_dich VARCHAR(30) NOT NULL
        CHECK (trang_thai_giao_dich IN ('cho_xac_nhan', 'thanh_toan_thanh_cong', 'giao_dich_loi_qua_han')),
    ly_do_loi VARCHAR(255),
    thoi_gian_giao_dich TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_giao_dich_hoa_don
        FOREIGN KEY (ma_hoa_don) REFERENCES hoa_don_thang (ma_hoa_don),
    CONSTRAINT fk_giao_dich_nhan_vien_thu
        FOREIGN KEY (ma_nhan_vien_thu) REFERENCES nhan_vien (ma_nhan_vien)
);

-- -----------------------------------------------------------------------------
-- BẢNG 17: thong_bao — Bản tin Thông báo Khẩn cấp & Hệ thống
-- (FK #26 -> nhan_vien)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS thong_bao (
    ma_thong_bao INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_nguoi_gui INT NOT NULL,
    tieu_de VARCHAR(200) NOT NULL,
    noi_dung VARCHAR(1000) NOT NULL,
    loai_thong_bao VARCHAR(30) NOT NULL
        CHECK (loai_thong_bao IN ('khan_cap', 'bao_tri_ky_thuat', 'ket_qua_duyet_ho_so')),
    muc_do VARCHAR(20) NOT NULL
        CHECK (muc_do IN ('binh_thuong', 'quan_trong', 'khan_cap')),
    nhom_nguoi_nhan VARCHAR(30) NOT NULL
        CHECK (nhom_nguoi_nhan IN ('tat_ca', 'cu_dan', 'nhan_vien', 'ca_nhan')),
    thoi_gian_gui TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_thong_bao_nguoi_gui
        FOREIGN KEY (ma_nguoi_gui) REFERENCES nhan_vien (ma_nhan_vien)
);

-- -----------------------------------------------------------------------------
-- BẢNG 18: nguoi_nhan_thong_bao — Chi tiết Gửi, Tiếp nhận & Đọc Thông báo
-- (FK #27 -> thong_bao, FK #28 -> tai_khoan)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nguoi_nhan_thong_bao (
    ma_tiep_nhan INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ma_thong_bao INT NOT NULL,
    ma_tai_khoan_nhan INT NOT NULL,
    trang_thai_gui VARCHAR(25) NOT NULL DEFAULT 'gui_thanh_cong'
        CHECK (trang_thai_gui IN ('gui_thanh_cong', 'gui_that_bai')),
    so_lan_gui_lai INT NOT NULL DEFAULT 0 CHECK (so_lan_gui_lai >= 0),
    trang_thai_tiep_nhan VARCHAR(25) NOT NULL DEFAULT 'chua_tiep_nhan'
        CHECK (trang_thai_tiep_nhan IN ('da_tiep_nhan', 'chua_tiep_nhan')),
    da_doc BOOLEAN NOT NULL DEFAULT FALSE,
    thoi_gian_tiep_nhan TIMESTAMP,
    CONSTRAINT fk_nguoi_nhan_thong_bao
        FOREIGN KEY (ma_thong_bao) REFERENCES thong_bao (ma_thong_bao),
    CONSTRAINT fk_nguoi_nhan_tai_khoan
        FOREIGN KEY (ma_tai_khoan_nhan) REFERENCES tai_khoan (ma_tai_khoan)
);

-- -----------------------------------------------------------------------------
-- Chỉ mục hỗ trợ truy vấn hiệu năng cao cho toàn hệ thống
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_cu_dan_can_ho ON cu_dan (ma_can_ho);
CREATE INDEX IF NOT EXISTS idx_vi_tri_do_khu_vuc_tt ON vi_tri_do (ma_khu_vuc, trang_thai_vi_tri);
CREATE INDEX IF NOT EXISTS idx_phuong_tien_cu_dan ON phuong_tien (ma_cu_dan);
CREATE INDEX IF NOT EXISTS idx_hoa_don_ky_tt ON hoa_don_thang (ky_thu_thang_nam, trang_thai_hoa_don);
CREATE INDEX IF NOT EXISTS idx_giao_dich_hoa_don ON giao_dich_thanh_toan (ma_hoa_don, trang_thai_giao_dich);
CREATE INDEX IF NOT EXISTS idx_nguoi_nhan_tk ON nguoi_nhan_thong_bao (ma_tai_khoan_nhan, da_doc);

COMMIT;
