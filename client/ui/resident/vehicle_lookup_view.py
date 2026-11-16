"""Giao diện Tra cứu & Định vị Xe 2D/3D của Cư dân (3.4.1–3.4.4)."""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from client.network.socket_client import SocketClient
from client.ui.manager.slot_picker_dialog import ZoomableCavetDialog
from client.ui.theme.components import (
    EmptyStateWidget,
    ParkingGridWidget,
    PlateBadge,
    SectionCard,
    StatusBadge,
    set_button_variant,
    style_table,
)
from client.ui.theme.parking_3d_widget import Parking3DSceneWidget
from client.ui.theme.tokens import Colors, Fonts
from shared.protocol import Actions


class VehicleStatusCard(QFrame):
    """Thẻ trạng thái phương tiện trực quan cho Cư dân (3.4.1–3.4.4)."""

    locate_requested = Signal(dict)
    cavet_requested = Signal(dict)

    def __init__(
        self,
        vehicle: Dict[str, Any],
        selected: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.vehicle = vehicle
        in_yard = bool(vehicle.get("dang_trong_bai"))
        accent = Colors.SUCCESS if in_yard else Colors.BORDER_INPUT
        bg = "#F8FAFC" if selected else Colors.BG_CARD
        border_w = "2px" if selected else "1px"
        border_col = Colors.ROYAL_BLUE if selected else Colors.BORDER

        self.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: {border_w} solid {border_col}; "
            f"border-left: 4px solid {accent}; border-radius: 10px; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # Hàng 1: Biển số + Badge trong bãi / ngoài bãi + Badge đăng ký
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(PlateBadge(str(vehicle.get("bien_so_xe") or "---")))
        top.addWidget(
            StatusBadge(
                "ĐANG ĐỖ TRONG BÃI" if in_yard else "ĐANG Ở NGOÀI BÃI",
                "success" if in_yard else "neutral",
            )
        )

        reg_st = str(vehicle.get("trang_thai_xe") or "")
        reg_lbl, reg_var = {
            "dang_hoat_dong": ("Đang hoạt động", "primary"),
            "cho_duyet": ("Chờ duyệt", "warning"),
            "ngung_hoat_dong": ("Ngừng hoạt động", "neutral"),
            "ngung_hieu_luc": ("Hết hiệu lực", "danger"),
        }.get(reg_st, (reg_st, "neutral"))
        top.addWidget(StatusBadge(reg_lbl, reg_var))
        top.addStretch(1)
        root.addLayout(top)

        # Hàng 2: Tên xe & vị trí đỗ
        lbl_name = QLabel(
            f"{vehicle.get('ten_hang_dong_xe', '-')} · Màu: {vehicle.get('mau_xe') or '-'} "
            f"({vehicle.get('ten_loai_xe') or vehicle.get('ma_loai_xe', '')})"
        )
        lbl_name.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_PRIMARY}; border: none;"
        )
        root.addWidget(lbl_name)

        slot_code = vehicle.get("ma_vi_tri_co_dinh")
        zone_name = vehicle.get("ten_khu_vuc") or vehicle.get("ma_khu_vuc_phan_cong") or "Chưa phân khu"
        if slot_code:
            loc_txt = f"Vị trí cố định (3.4.2): Ô {slot_code} — {zone_name}"
        else:
            loc_txt = f"Khu vực đỗ (3.4.2): {zone_name}"
        lbl_loc = QLabel(loc_txt)
        lbl_loc.setStyleSheet(f"font-size: 12.5px; color: {Colors.ROYAL_BLUE}; font-weight: 600; border: none;")
        root.addWidget(lbl_loc)

        # Hàng 3: Thời gian vào / ra gần nhất (3.4.3)
        t_in = vehicle.get("thoi_gian_vao") or "Chưa có lượt vào"
        t_out = vehicle.get("thoi_gian_ra") or "Chưa có lượt ra"
        lbl_time = QLabel(f"Vào gần nhất: {t_in}   |   Ra gần nhất: {t_out}")
        lbl_time.setWordWrap(True)
        lbl_time.setStyleSheet(f"font-size: 11.5px; color: {Colors.TEXT_MUTED}; border: none;")
        root.addWidget(lbl_time)

        # Hàng 4: Nút định vị & Xem cà-vẹt
        actions = QHBoxLayout()
        actions.setSpacing(8)
        btn_locate = QPushButton("Định vị trên Sơ đồ (2D/3D)")
        set_button_variant(btn_locate, "primary", "sm")
        btn_locate.clicked.connect(lambda: self.locate_requested.emit(self.vehicle))

        btn_cavet = QPushButton("Xem ảnh Cà-vẹt")
        set_button_variant(btn_cavet, "default", "sm")
        btn_cavet.clicked.connect(lambda: self.cavet_requested.emit(self.vehicle))

        actions.addWidget(btn_locate)
        actions.addWidget(btn_cavet)
        actions.addStretch(1)
        root.addLayout(actions)


class VehicleLookupView(QWidget):
    """Màn hình 3.4.1–3.4.4: Vehicle Status Cards + Mini-Map 2D/3D định vị & 10 lượt gần nhất."""

    def __init__(
        self,
        socket_client: Optional[SocketClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.socket_client = socket_client
        self._vehicles: List[Dict[str, Any]] = []
        self._zone_maps: Dict[str, List[Dict[str, Any]]] = {}
        self._zone_info: Dict[str, Dict[str, Any]] = {}
        self._selected_vehicle_id: Optional[int] = None
        self._pulse_on = False

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(550)
        self._pulse_timer.timeout.connect(self._toggle_pulse)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Tra cứu & Định vị Xe Cư dân (3.4)")
        lbl_title.setStyleSheet(
            f"font-family: {Fonts.HEADING_FAMILY}; font-size: 21px; font-weight: 700; color: {Colors.TEXT_PRIMARY};"
        )
        lbl_sub = QLabel(
            "Theo dõi xe đang ở trong/ngoài bãi, định vị vị trí đỗ 2D/3D và 10 lượt vào/ra gần nhất"
        )
        lbl_sub.setStyleSheet(f"font-size: 12.5px; color: {Colors.TEXT_MUTED};")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        header.addLayout(title_col, 1)

        self.txt_kw = QLineEdit()
        self.txt_kw.setPlaceholderText("Lọc biển số, tên xe, mã ô đỗ...")
        self.txt_kw.setMaximumWidth(260)
        self.btn_refresh = QPushButton("Tìm / Làm mới")
        set_button_variant(self.btn_refresh, "default", "sm")
        self.btn_refresh.clicked.connect(self.refresh_data)
        header.addWidget(self.txt_kw)
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Cột trái: Vehicle Status Cards
        left_card = SectionCard(
            title="Danh sách Phương tiện & Trạng thái (3.4.1–3.4.4)",
            subtitle="Bấm 'Định vị trên Sơ đồ' để làm nổi bật vị trí đỗ của xe",
        )
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_host = QWidget()
        self._cards_lay = QVBoxLayout(self._cards_host)
        self._cards_lay.setContentsMargins(2, 2, 2, 2)
        self._cards_lay.setSpacing(10)
        self._cards_lay.addStretch(1)
        self.cards_scroll.setWidget(self._cards_host)
        left_card.body_layout.addWidget(self.cards_scroll, 1)
        splitter.addWidget(left_card)

        # Cột phải: Mini-Map 2D/3D + Bảng 10 lượt gần nhất
        right_host = QWidget()
        right_lay = QVBoxLayout(right_host)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(12)

        map_card = SectionCard(
            title="Sơ đồ Định vị Xe 2D / 3D (3.4.2)",
            subtitle="Hiển thị khu vực / ô đỗ cố định của phương tiện đang chọn",
        )
        self.btn_2d = QPushButton("2D Mini-Map")
        self.btn_3d = QPushButton("3D Sa bàn")
        set_button_variant(self.btn_2d, "primary", "sm")
        set_button_variant(self.btn_3d, "default", "sm")
        self.btn_2d.clicked.connect(lambda: self._switch_map_mode(0))
        self.btn_3d.clicked.connect(lambda: self._switch_map_mode(1))
        map_card.add_header_widget(self.btn_2d)
        map_card.add_header_widget(self.btn_3d)

        self.lbl_focus_banner = QLabel("Chọn một phương tiện bên trái để định vị.")
        self.lbl_focus_banner.setStyleSheet(
            f"font-size: 12.5px; font-weight: 600; color: {Colors.ROYAL_BLUE}; "
            f"background-color: {Colors.ROYAL_BLUE_SOFT}; border-radius: 8px; padding: 6px 10px;"
        )
        map_card.body_layout.addWidget(self.lbl_focus_banner)

        self.map_stack = QStackedWidget()
        self.grid_2d = ParkingGridWidget(columns=4)
        self.scene_3d = Parking3DSceneWidget(columns=4)
        self.scene_3d.setMinimumHeight(250)
        self.moto_zone_info = EmptyStateWidget(
            title="Khu vực đỗ Xe máy chung cư",
            description="Xe máy phân bổ theo khu vực chung không gán ô đỗ cố định.",
        )
        self.map_stack.addWidget(self.grid_2d)
        self.map_stack.addWidget(self.scene_3d)
        self.map_stack.addWidget(self.moto_zone_info)
        map_card.body_layout.addWidget(self.map_stack, 1)
        right_lay.addWidget(map_card, 3)

        hist_card = SectionCard(
            title="10 Lượt Vào / Ra Gần Nhất (3.4.3)",
            subtitle="Trích xuất trực tiếp từ nhật ký cổng parking_history_*.jsonl",
        )
        self.tbl_history = QTableWidget(0, 5)
        self.tbl_history.setHorizontalHeaderLabels(
            ["Mã lượt", "Thời gian vào", "Thời gian ra", "Khu vực / Ô", "Trạng thái"]
        )
        style_table(self.tbl_history)
        hist_card.body_layout.addWidget(self.tbl_history, 1)
        right_lay.addWidget(hist_card, 2)

        splitter.addWidget(right_host)
        splitter.setSizes([470, 580])
        root.addWidget(splitter, 1)

    def _switch_map_mode(self, idx: int) -> None:
        if self.map_stack.currentIndex() == 2:
            return
        self.map_stack.setCurrentIndex(idx)
        set_button_variant(self.btn_2d, "primary" if idx == 0 else "default", "sm")
        set_button_variant(self.btn_3d, "primary" if idx == 1 else "default", "sm")

    def refresh_data(self) -> None:
        if self.socket_client is None or not self.socket_client.is_connected():
            return
        resp = self.socket_client.call(
            Actions.VEHICLE_RESIDENT_LOOKUP,
            {"keyword": self.txt_kw.text().strip()},
        )
        if resp.get("ok") and isinstance(resp.get("data"), dict):
            self.set_data(resp["data"])

    def set_data(self, data: Dict[str, Any]) -> None:
        self._vehicles = list(data.get("vehicles") or [])
        self._zone_maps = dict(data.get("zone_maps") or {})
        self._zone_info = dict(data.get("zone_info") or {})

        if self._vehicles and (
            self._selected_vehicle_id is None
            or not any(v.get("ma_phuong_tien") == self._selected_vehicle_id for v in self._vehicles)
        ):
            self._selected_vehicle_id = self._vehicles[0].get("ma_phuong_tien")

        self._render_cards()
        selected = next(
            (v for v in self._vehicles if v.get("ma_phuong_tien") == self._selected_vehicle_id),
            None,
        )
        self._focus_vehicle(selected)

    def _render_cards(self) -> None:
        while self._cards_lay.count():
            item = self._cards_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self._vehicles:
            self._cards_lay.addWidget(
                EmptyStateWidget(
                    title="Chưa có phương tiện phù hợp",
                    description="Đăng ký xe tại mục 3.2 hoặc xóa từ khóa tìm kiếm để xem tất cả xe.",
                )
            )
            self._cards_lay.addStretch(1)
            return

        for v in self._vehicles:
            card = VehicleStatusCard(
                vehicle=v,
                selected=(v.get("ma_phuong_tien") == self._selected_vehicle_id),
            )
            card.locate_requested.connect(self._on_locate_vehicle)
            card.cavet_requested.connect(self._on_view_cavet)
            self._cards_lay.addWidget(card)
        self._cards_lay.addStretch(1)

    def _on_locate_vehicle(self, vehicle: Dict[str, Any]) -> None:
        self._selected_vehicle_id = vehicle.get("ma_phuong_tien")
        self._render_cards()
        self._focus_vehicle(vehicle)

    def _on_view_cavet(self, vehicle: Dict[str, Any]) -> None:
        dlg = ZoomableCavetDialog(
            title=f"Ảnh Cà-vẹt xe {vehicle.get('bien_so_xe', '')}",
            socket_client=self.socket_client,
            ma_phuong_tien=vehicle.get("ma_phuong_tien"),
            duong_dan_cavet=str(vehicle.get("duong_dan_cavet") or ""),
            parent=self,
        )
        dlg.exec()

    def _focus_vehicle(self, vehicle: Optional[Dict[str, Any]]) -> None:
        self._pulse_timer.stop()
        if vehicle is None:
            self.lbl_focus_banner.setText("Chọn một phương tiện bên trái để định vị.")
            self.grid_2d.set_slots([])
            self.scene_3d.set_slots([])
            self.tbl_history.setRowCount(0)
            return

        plate = str(vehicle.get("bien_so_xe") or "---")
        slot_code = vehicle.get("ma_vi_tri_co_dinh")
        zone_id = str(vehicle.get("ma_khu_vuc_phan_cong") or "")
        zone_name = str(vehicle.get("ten_khu_vuc") or zone_id or "Chưa phân khu")
        in_yard = bool(vehicle.get("dang_trong_bai"))

        self.lbl_focus_banner.setText(
            f"Đang định vị: {plate} · {'Đang đỗ trong bãi' if in_yard else 'Đang ở ngoài bãi'} · "
            f"{f'Ô cố định {slot_code} ({zone_name})' if slot_code else f'Khu vực {zone_name}'}"
        )

        slots = list(self._zone_maps.get(zone_id) or [])
        if slots:
            if self.map_stack.currentIndex() == 2:
                self.map_stack.setCurrentIndex(0)
            self.grid_2d.set_slots(slots)
            self.scene_3d.set_slots(slots, columns=4)
            self.scene_3d.set_selected_slot(str(slot_code) if slot_code else None)
            if slot_code:
                self._pulse_on = True
                self._apply_slot_highlight(str(slot_code), True)
                self._pulse_timer.start()
        else:
            z_meta = self._zone_info.get(zone_id) or {}
            total = z_meta.get("tong_so_cho", vehicle.get("tong_so_cho", "-"))
            used = z_meta.get("so_xe_dang_gui", vehicle.get("so_xe_dang_gui", "-"))
            self.map_stack.setCurrentIndex(2)
            # Cập nhật mô tả khu vực xe máy
            for lbl in self.moto_zone_info.findChildren(QLabel):
                if "Khu vực" in lbl.text() or "Xe máy" in lbl.text():
                    lbl.setText(f"{zone_name} (Đang gửi: {used}/{total} chỗ)")
                    break

        history = list(vehicle.get("recent_history") or [])[:10]
        self.tbl_history.setRowCount(len(history))
        for idx, h in enumerate(history):
            self.tbl_history.setItem(idx, 0, QTableWidgetItem(str(h.get("ma_luot_gui") or "-")))
            self.tbl_history.setItem(idx, 1, QTableWidgetItem(str(h.get("thoi_gian_vao") or "-")))
            self.tbl_history.setItem(idx, 2, QTableWidgetItem(str(h.get("thoi_gian_ra") or "-")))
            loc = h.get("ma_vi_tri") or h.get("ma_khu_vuc") or "-"
            self.tbl_history.setItem(idx, 3, QTableWidgetItem(str(loc)))
            self.tbl_history.setItem(idx, 4, QTableWidgetItem(str(h.get("trang_thai") or "-")))

    def _toggle_pulse(self) -> None:
        selected = next(
            (v for v in self._vehicles if v.get("ma_phuong_tien") == self._selected_vehicle_id),
            None,
        )
        slot_code = selected.get("ma_vi_tri_co_dinh") if selected else None
        if not slot_code:
            self._pulse_timer.stop()
            return
        self._pulse_on = not self._pulse_on
        self._apply_slot_highlight(str(slot_code), self._pulse_on)

    def _apply_slot_highlight(self, target_code: str, bright: bool) -> None:
        for idx in range(self.grid_2d._grid.count()):
            item = self.grid_2d._grid.itemAt(idx)
            tile = item.widget() if item is not None else None
            if tile is None or not hasattr(tile, "slot_data"):
                continue
            if str(tile.slot_data.get("ma_vi_tri") or "") == target_code:
                border = Colors.ROYAL_BLUE if bright else Colors.WARNING
                bg = "#DBEAFE" if bright else "#FEF3C7"
                tile.setStyleSheet(
                    f"QPushButton {{ font-family: {Fonts.MONO_FAMILY}; font-size: 12px; font-weight: 800; "
                    f"background-color: {bg}; color: {Colors.TEXT_PRIMARY}; "
                    f"border: 3px solid {border}; border-radius: 8px; padding: 6px; }}"
                )
