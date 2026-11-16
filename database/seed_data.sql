-- =============================================================================
-- Smart Parking — Chung cư Đào Tiên
-- Dữ liệu mẫu chuẩn (Idempotent) phục vụ toàn bộ 5 thành viên kiểm thử
-- Mật khẩu mặc định cho tất cả tài khoản mẫu: 123456 (băm bcrypt)
-- =============================================================================

BEGIN;

-- 1. Tài khoản hệ thống (5 tài khoản: 1 Admin, 1 Quản lý, 1 Nhân viên, 2 Cư dân)
INSERT INTO tai_khoan (ten_dang_nhap, mat_khau_bam, vai_tro, trang_thai)
VALUES
    ('admin01',    '$2b$10$r/bp03N69xtVS6.GeW7MYOObm9JVGwD/lXFLCPUtwwBXKnh73d..K', 'admin',     'hoat_dong'),
    ('quanly01',   '$2b$10$r/bp03N69xtVS6.GeW7MYOObm9JVGwD/lXFLCPUtwwBXKnh73d..K', 'quan_ly',   'hoat_dong'),
    ('nhanvien01', '$2b$10$r/bp03N69xtVS6.GeW7MYOObm9JVGwD/lXFLCPUtwwBXKnh73d..K', 'nhan_vien', 'hoat_dong'),
    ('cudan_a101', '$2b$10$r/bp03N69xtVS6.GeW7MYOObm9JVGwD/lXFLCPUtwwBXKnh73d..K', 'cu_dan',    'hoat_dong'),
    ('cudan_b202', '$2b$10$r/bp03N69xtVS6.GeW7MYOObm9JVGwD/lXFLCPUtwwBXKnh73d..K', 'cu_dan',    'hoat_dong')
ON CONFLICT (ten_dang_nhap) DO NOTHING;

-- 2. Căn hộ chung cư
INSERT INTO can_ho (ma_can_ho, toa_nha, so_tang, so_cho_oto_mac_dinh, so_cho_oto_mua_them, trang_thai_can_ho)
VALUES
    ('A1-01', 'A', 1, 1, 0, 'dang_su_dung'),
    ('B2-02', 'B', 2, 1, 1, 'dang_su_dung')
ON CONFLICT (ma_can_ho) DO NOTHING;

-- 3. Hồ sơ Nhân viên & Quản lý
INSERT INTO nhan_vien (ma_tai_khoan, ma_nhan_vien_code, ho_ten, so_cccd, so_dien_thoai, email, chuc_vu)
SELECT tk.ma_tai_khoan, 'QL001', 'Nguyễn Văn Quản Lý', '079090000001', '0909100001', 'quanly01@daotien.vn', 'quan_ly_chung_cu'
FROM tai_khoan tk WHERE tk.ten_dang_nhap = 'quanly01'
ON CONFLICT (ma_tai_khoan) DO NOTHING;

INSERT INTO nhan_vien (ma_tai_khoan, ma_nhan_vien_code, ho_ten, so_cccd, so_dien_thoai, email, chuc_vu)
SELECT tk.ma_tai_khoan, 'NV001', 'Trần Bảo Vệ', '079092000002', '0909100002', 'nhanvien01@daotien.vn', 'nhan_vien_bai_xe'
FROM tai_khoan tk WHERE tk.ten_dang_nhap = 'nhanvien01'
ON CONFLICT (ma_tai_khoan) DO NOTHING;

-- 4. Hồ sơ Cư dân
INSERT INTO cu_dan (ma_tai_khoan, ma_can_ho, ho_ten, so_cccd, so_dien_thoai, email, dia_chi)
SELECT tk.ma_tai_khoan, 'A1-01', 'Lê Minh Cư Dân', '079095000101', '0909200101', 'a101@daotien.vn', 'Căn hộ A1-01, Chung cư Đào Tiên'
FROM tai_khoan tk WHERE tk.ten_dang_nhap = 'cudan_a101'
ON CONFLICT (ma_tai_khoan) DO NOTHING;

INSERT INTO cu_dan (ma_tai_khoan, ma_can_ho, ho_ten, so_cccd, so_dien_thoai, email, dia_chi)
SELECT tk.ma_tai_khoan, 'B2-02', 'Phạm Thị Hồng', '079096000202', '0909200202', 'b202@daotien.vn', 'Căn hộ B2-02, Chung cư Đào Tiên'
FROM tai_khoan tk WHERE tk.ten_dang_nhap = 'cudan_b202'
ON CONFLICT (ma_tai_khoan) DO NOTHING;

-- 5. Danh mục Loại phương tiện
INSERT INTO loai_phuong_tien (ma_loai_xe, ten_loai_xe, kieu_phan_cho, mo_ta)
VALUES
    ('O_TO',   'Ô tô',   'gan_vi_tri_co_dinh', 'Gán vị trí ô đỗ cố định theo căn hộ hoặc ô vãng lai riêng'),
    ('XE_MAY', 'Xe máy', 'phan_theo_khu_vuc',  'Phân theo khu vực đỗ xe máy chung cư')
ON CONFLICT (ma_loai_xe) DO NOTHING;

-- 6. Khu vực bãi xe (3 khu vực)
INSERT INTO khu_vuc (ma_khu_vuc, ten_khu_vuc, ma_loai_xe, tong_so_cho, so_xe_dang_gui, trang_thai_khu_vuc)
VALUES
    ('KHU_A',  'Khu A - Xe máy chung cư',       'XE_MAY', 120, 0, 'hoat_dong'),
    ('HAM_B1', 'Hầm B1 - Ô tô Cư dân',          'O_TO',   12,  0, 'hoat_dong'),
    ('KHU_C',  'Khu C - Ô tô Vãng lai/Đầu bãi', 'O_TO',   8,   0, 'hoat_dong')
ON CONFLICT (ma_khu_vuc) DO NOTHING;

-- 7. 20 Vị trí đỗ mẫu (B1-01..B1-12 và C-01..C-08)
INSERT INTO vi_tri_do (ma_vi_tri, ma_khu_vuc, ten_vi_tri, nhom_vi_tri, trang_thai_vi_tri)
VALUES
    ('B1-01', 'HAM_B1', 'Ô B1-01', 'vi_tri_dau_bai', 'da_phan_cho'),
    ('B1-02', 'HAM_B1', 'Ô B1-02', 'vi_tri_dau_bai', 'trong'),
    ('B1-03', 'HAM_B1', 'Ô B1-03', 'vi_tri_thuong',  'trong'),
    ('B1-04', 'HAM_B1', 'Ô B1-04', 'vi_tri_thuong',  'trong'),
    ('B1-05', 'HAM_B1', 'Ô B1-05', 'vi_tri_thuong',  'trong'),
    ('B1-06', 'HAM_B1', 'Ô B1-06', 'vi_tri_thuong',  'trong'),
    ('B1-07', 'HAM_B1', 'Ô B1-07', 'vi_tri_thuong',  'trong'),
    ('B1-08', 'HAM_B1', 'Ô B1-08', 'vi_tri_thuong',  'trong'),
    ('B1-09', 'HAM_B1', 'Ô B1-09', 'vi_tri_thuong',  'trong'),
    ('B1-10', 'HAM_B1', 'Ô B1-10', 'vi_tri_thuong',  'trong'),
    ('B1-11', 'HAM_B1', 'Ô B1-11', 'vi_tri_thuong',  'trong'),
    ('B1-12', 'HAM_B1', 'Ô B1-12', 'vi_tri_thuong',  'trong'),
    ('C-01',  'KHU_C',  'Ô C-01',  'vi_tri_vang_lai', 'trong'),
    ('C-02',  'KHU_C',  'Ô C-02',  'vi_tri_vang_lai', 'trong'),
    ('C-03',  'KHU_C',  'Ô C-03',  'vi_tri_vang_lai', 'trong'),
    ('C-04',  'KHU_C',  'Ô C-04',  'vi_tri_vang_lai', 'trong'),
    ('C-05',  'KHU_C',  'Ô C-05',  'vi_tri_vang_lai', 'trong'),
    ('C-06',  'KHU_C',  'Ô C-06',  'vi_tri_vang_lai', 'trong'),
    ('C-07',  'KHU_C',  'Ô C-07',  'vi_tri_vang_lai', 'trong'),
    ('C-08',  'KHU_C',  'Ô C-08',  'vi_tri_vang_lai', 'trong')
ON CONFLICT (ma_vi_tri) DO NOTHING;

-- 8. Phân bổ chỗ đỗ mẫu cho căn hộ A1-01 tại B1-01
INSERT INTO phan_bo_cho_do (ma_can_ho, ma_vi_tri, loai_cho_cap, trang_thai_su_dung)
SELECT 'A1-01', 'B1-01', 'cho_mac_dinh_xe_1', 'dang_hieu_luc'
WHERE NOT EXISTS (
    SELECT 1 FROM phan_bo_cho_do WHERE ma_can_ho = 'A1-01' AND ma_vi_tri = 'B1-01' AND trang_thai_su_dung = 'dang_hieu_luc'
);

-- 9. Phương tiện mẫu của cư dân A1-01 (1 Ô tô + 1 Xe máy)
INSERT INTO phuong_tien (ma_cu_dan, ma_loai_xe, bien_so_xe, ten_hang_dong_xe, mau_xe, duong_dan_cavet, trang_thai_xe)
SELECT cd.ma_cu_dan, 'O_TO', '51H-888.99', 'Toyota Camry 2.5Q', 'Đen', 'storage/uploads/cavet/sample_oto.jpg', 'dang_hoat_dong'
FROM cu_dan cd WHERE cd.ma_can_ho = 'A1-01'
ON CONFLICT (bien_so_xe) DO NOTHING;

INSERT INTO phuong_tien (ma_cu_dan, ma_loai_xe, bien_so_xe, ten_hang_dong_xe, mau_xe, duong_dan_cavet, trang_thai_xe)
SELECT cd.ma_cu_dan, 'XE_MAY', '59A1-123.45', 'Honda SH 150i', 'Trắng', 'storage/uploads/cavet/sample_xemay.jpg', 'dang_hoat_dong'
FROM cu_dan cd WHERE cd.ma_can_ho = 'A1-01'
ON CONFLICT (bien_so_xe) DO NOTHING;

INSERT INTO quan_ly_phuong_tien_ra_vao (ma_phuong_tien, ma_vi_tri_co_dinh, ma_khu_vuc_phan_cong, dang_trong_bai)
SELECT pt.ma_phuong_tien, 'B1-01', 'HAM_B1', FALSE
FROM phuong_tien pt WHERE pt.bien_so_xe = '51H-888.99'
ON CONFLICT (ma_phuong_tien) DO NOTHING;

INSERT INTO quan_ly_phuong_tien_ra_vao (ma_phuong_tien, ma_vi_tri_co_dinh, ma_khu_vuc_phan_cong, dang_trong_bai)
SELECT pt.ma_phuong_tien, NULL, 'KHU_A', FALSE
FROM phuong_tien pt WHERE pt.bien_so_xe = '59A1-123.45'
ON CONFLICT (ma_phuong_tien) DO NOTHING;

-- 10. Bảng giá dịch vụ đầy đủ 4 nhóm (5 mức giá đang áp dụng)
INSERT INTO bang_gia (ma_loai_xe, nhom_ap_dung, don_vi_tinh, muc_phi, trang_thai_gia)
SELECT 'O_TO', 'cho_oto_mac_dinh', 'thang', 1200000.00, 'dang_ap_dung'
WHERE NOT EXISTS (
    SELECT 1 FROM bang_gia WHERE ma_loai_xe = 'O_TO' AND nhom_ap_dung = 'cho_oto_mac_dinh' AND trang_thai_gia = 'dang_ap_dung'
);

INSERT INTO bang_gia (ma_loai_xe, nhom_ap_dung, don_vi_tinh, muc_phi, trang_thai_gia)
SELECT 'O_TO', 'cho_oto_mua_them', 'thang', 1800000.00, 'dang_ap_dung'
WHERE NOT EXISTS (
    SELECT 1 FROM bang_gia WHERE ma_loai_xe = 'O_TO' AND nhom_ap_dung = 'cho_oto_mua_them' AND trang_thai_gia = 'dang_ap_dung'
);

INSERT INTO bang_gia (ma_loai_xe, nhom_ap_dung, don_vi_tinh, muc_phi, trang_thai_gia)
SELECT 'XE_MAY', 'xe_may_cu_dan', 'thang', 120000.00, 'dang_ap_dung'
WHERE NOT EXISTS (
    SELECT 1 FROM bang_gia WHERE ma_loai_xe = 'XE_MAY' AND nhom_ap_dung = 'xe_may_cu_dan' AND trang_thai_gia = 'dang_ap_dung'
);

INSERT INTO bang_gia (ma_loai_xe, nhom_ap_dung, don_vi_tinh, muc_phi, trang_thai_gia)
SELECT 'O_TO', 'khach_vang_lai', 'gio', 25000.00, 'dang_ap_dung'
WHERE NOT EXISTS (
    SELECT 1 FROM bang_gia WHERE ma_loai_xe = 'O_TO' AND nhom_ap_dung = 'khach_vang_lai' AND trang_thai_gia = 'dang_ap_dung'
);

INSERT INTO bang_gia (ma_loai_xe, nhom_ap_dung, don_vi_tinh, muc_phi, trang_thai_gia)
SELECT 'XE_MAY', 'khach_vang_lai', 'luot', 5000.00, 'dang_ap_dung'
WHERE NOT EXISTS (
    SELECT 1 FROM bang_gia WHERE ma_loai_xe = 'XE_MAY' AND nhom_ap_dung = 'khach_vang_lai' AND trang_thai_gia = 'dang_ap_dung'
);

COMMIT;
