"""Hằng số và Enum dùng chung cho toàn hệ thống Smart Parking — Chung cư Đào Tiên.

Đồng bộ 100% với 18 bảng CSDL PostgreSQL và bộ Design DNA (`client/ui/theme/tokens.py`).
"""

from enum import Enum


class StrEnum(str, Enum):
    """Base Enum chuỗi tương thích Python <3.11 & JSON serialization trực tiếp."""

    def __str__(self) -> str:
        return self.value


class Roles(StrEnum):
    QUAN_LY = "quan_ly"
    NHAN_VIEN = "nhan_vien"
    CU_DAN = "cu_dan"
    ADMIN = "admin"


class AccountStatus(StrEnum):
    HOAT_DONG = "hoat_dong"
    TAM_KHOA = "tam_khoa"


class ApartmentStatus(StrEnum):
    DANG_SU_DUNG = "dang_su_dung"
    TRONG = "trong"
    NGUNG_SU_DUNG = "ngung_su_dung"


class VehicleTypes(StrEnum):
    O_TO = "O_TO"
    XE_MAY = "XE_MAY"


class AllocationKinds(StrEnum):
    GAN_VI_TRI_CO_DINH = "gan_vi_tri_co_dinh"
    PHAN_THEO_KHU_VUC = "phan_theo_khu_vuc"


class ZoneStatus(StrEnum):
    HOAT_DONG = "hoat_dong"
    NGUNG_SU_DUNG = "ngung_su_dung"


class SlotGroups(StrEnum):
    VI_TRI_THUONG = "vi_tri_thuong"
    VI_TRI_DAU_BAI = "vi_tri_dau_bai"
    VI_TRI_VANG_LAI = "vi_tri_vang_lai"


class SlotStatus(StrEnum):
    TRONG = "trong"
    DA_PHAN_CHO = "da_phan_cho"
    DANG_CO_XE = "dang_co_xe"
    NGUNG_SU_DUNG = "ngung_su_dung"


class VehicleStatus(StrEnum):
    DANG_HOAT_DONG = "dang_hoat_dong"
    NGUNG_HOAT_DONG = "ngung_hoat_dong"


class VehicleRequestTypes(StrEnum):
    DANG_KY_MOI = "dang_ky_moi"
    CHINH_SUA_THONG_TIN = "chinh_sua_thong_tin"
    NGUNG_DANG_KY = "ngung_dang_ky"


class RequestStatus(StrEnum):
    CHO_DUYET = "cho_duyet"
    DA_DUYET = "da_duyet"
    TU_CHOI = "tu_choi"
    DA_HUY = "da_huy"


class SlotAllocationTypes(StrEnum):
    CHO_MAC_DINH_XE_1 = "cho_mac_dinh_xe_1"
    CHO_MUA_THEM = "cho_mua_them"


class SlotAllocationStatus(StrEnum):
    DANG_HIEU_LUC = "dang_hieu_luc"
    DA_THU_HOI = "da_thu_hoi"


class PricingGroups(StrEnum):
    CHO_OTO_MAC_DINH = "cho_oto_mac_dinh"
    CHO_OTO_MUA_THEM = "cho_oto_mua_them"
    XE_MAY_CU_DAN = "xe_may_cu_dan"
    KHACH_VANG_LAI = "khach_vang_lai"


class PricingUnits(StrEnum):
    THANG = "thang"
    GIO = "gio"
    LUOT = "luot"


class PricingStatus(StrEnum):
    DANG_AP_DUNG = "dang_ap_dung"
    NGUNG_AP_DUNG = "ngung_ap_dung"


class InvoiceStatus(StrEnum):
    CHUA_THANH_TOAN = "chua_thanh_toan"
    DA_THANH_TOAN = "da_thanh_toan"
    QUA_HAN = "qua_han"


class PaymentMethods(StrEnum):
    CHUYEN_KHOAN_QR = "chuyen_khoan_qr"
    TIEN_MAT = "tien_mat"


class TransactionStatus(StrEnum):
    CHO_XAC_NHAN = "cho_xac_nhan"
    THANH_TOAN_THANH_CONG = "thanh_toan_thanh_cong"
    GIAO_DICH_LOI_QUA_HAN = "giao_dich_loi_qua_han"


class NotifyTypes(StrEnum):
    KHAN_CAP = "khan_cap"
    BAO_TRI_KY_THUAT = "bao_tri_ky_thuat"
    KET_QUA_DUYET_HO_SO = "ket_qua_duyet_ho_so"


class NotifyLevels(StrEnum):
    BINH_THUONG = "binh_thuong"
    QUAN_TRONG = "quan_trong"
    KHAN_CAP = "khan_cap"


class NotifyRecipientGroups(StrEnum):
    TAT_CA = "tat_ca"
    CU_DAN = "cu_dan"
    NHAN_VIEN = "nhan_vien"
    CA_NHAN = "ca_nhan"


class NotifySendStatus(StrEnum):
    GUI_THANH_CONG = "gui_thanh_cong"
    GUI_THAT_BAI = "gui_that_bai"


class NotifyAckStatus(StrEnum):
    CHUA_TIEP_NHAN = "chua_tiep_nhan"
    DA_TIEP_NHAN = "da_tiep_nhan"


class ShiftTypes(StrEnum):
    CA_SANG = "ca_sang"
    CA_CHIEU = "ca_chieu"
    CA_DEM = "ca_dem"


class LogLevels(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class AuthResults(StrEnum):
    THANH_CONG = "thanh_cong"
    THAT_BAI = "that_bai"
    BI_KHOA = "bi_khoa"


class ParkingCustomerTypes(StrEnum):
    CU_DAN = "cu_dan"
    VANG_LAI = "vang_lai"


class ParkingHistoryStatus(StrEnum):
    DANG_GUI = "dang_gui"
    DA_RA_BAI = "da_ra_bai"


class ConnectionStatus(StrEnum):
    ONLINE = "online"
    DISCONNECTED = "disconnected"
    TIMEOUT = "timeout"
