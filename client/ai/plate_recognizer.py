"""Engine AI nhận diện biển số xe Hybrid 2 tầng: Gemini Vision API (ưu tiên 1) + YOLO/EasyOCR (fallback offline)."""

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from dotenv import load_dotenv
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from client.ai.camera_worker import bgr_to_qimage
from shared.constants import VehicleTypes
from shared.validators import normalize_plate, validate_plate

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

_CHAR_TO_DIGIT = {
    "O": "0",
    "D": "0",
    "Q": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "A": "4",
    "S": "5",
    "G": "6",
    "T": "7",
    "B": "8",
}

_DIGIT_TO_CHAR = {
    "0": "D",
    "1": "A",
    "2": "Z",
    "4": "A",
    "5": "S",
    "6": "G",
    "8": "B",
}

_GEMINI_SYSTEM_PROMPT = (
    "Bạn là hệ thống AI nhận diện biển số xe Việt Nam tại trạm kiểm soát bãi giữ xe thông minh.\n"
    "Hãy đọc chính xác biển số xe có trong ảnh (hỗ trợ cả biển 1 dòng ô tô như 51H-888.99, 30A-123.45 "
    "và biển 2 dòng xe máy như 59A1-123.45, 29B1-678.90).\n"
    "Trả về DUY NHẤT một JSON object hợp lệ với các trường:\n"
    '{\n'
    '  "bien_so": "chuỗi biển số đọc được, ví dụ 51H-888.99 hoặc 59A1-123.45 (rỗng nếu không thấy)",\n'
    '  "loai_xe": "O_TO" hoặc "XE_MAY",\n'
    '  "confidence": số thực từ 0.0 đến 1.0,\n'
    '  "bbox": [ymin, xmin, ymax, xmax] theo thang tọa độ chuẩn hóa 0..1000\n'
    "}"
)


def normalize_vehicle_type(raw_type: Optional[str], normalized_plate: str = "") -> str:
    """Chuẩn hóa loại xe về hằng số hệ thống ('O_TO' hoặc 'XE_MAY')."""
    text = (raw_type or "").strip().upper()
    if text in ("O_TO", "OTO", "CAR", "4_CHO", "7_CHO"):
        return VehicleTypes.O_TO.value
    if text in ("XE_MAY", "XEMAY", "MOTO", "MOTORBIKE", "MOTORCYCLE"):
        return VehicleTypes.XE_MAY.value

    if normalized_plate:
        ok_moto, _ = validate_plate(normalized_plate, VehicleTypes.XE_MAY.value)
        if ok_moto:
            return VehicleTypes.XE_MAY.value
        ok_car, _ = validate_plate(normalized_plate, VehicleTypes.O_TO.value)
        if ok_car:
            return VehicleTypes.O_TO.value
    return VehicleTypes.O_TO.value


def _repair_ocr_chars(cleaned: str) -> str:
    """Sửa lỗi nhầm lẫn ký tự thường gặp của OCR trên cấu trúc biển số Việt Nam."""
    if len(cleaned) < 6:
        return cleaned

    chars = list(cleaned)
    # 2 ký tự đầu luôn là mã tỉnh (chữ số)
    for idx in (0, 1):
        if chars[idx] in _CHAR_TO_DIGIT:
            chars[idx] = _CHAR_TO_DIGIT[chars[idx]]

    # Ký tự thứ 3 luôn là seri chữ cái
    if chars[2] in _DIGIT_TO_CHAR:
        chars[2] = _DIGIT_TO_CHAR[chars[2]]

    # Phần đuôi (4 hoặc 5 ký tự cuối) luôn là chữ số
    tail_len = 5 if len(chars) >= 8 else 4
    tail_start = max(3, len(chars) - tail_len)
    for idx in range(tail_start, len(chars)):
        if chars[idx] in _CHAR_TO_DIGIT:
            chars[idx] = _CHAR_TO_DIGIT[chars[idx]]

    return "".join(chars)


def post_process_plate_text(
    raw_text: str,
    vehicle_hint: Optional[str] = None,
) -> Tuple[str, str]:
    """Hậu xử lý chuỗi OCR thô -> (bien_so_chuan, loai_xe 'O_TO'|'XE_MAY')."""
    if not raw_text:
        return "", normalize_vehicle_type(vehicle_hint, "")

    cleaned = re.sub(r"[^A-Z0-9]", "", str(raw_text).strip().upper())
    if not cleaned:
        return "", normalize_vehicle_type(vehicle_hint, "")

    repaired = _repair_ocr_chars(cleaned)
    normalized = normalize_plate(repaired)

    # Kiểm tra khớp xe máy hoặc ô tô
    ok_car, car_plate = validate_plate(normalized, VehicleTypes.O_TO.value)
    ok_moto, moto_plate = validate_plate(normalized, VehicleTypes.XE_MAY.value)

    hint_norm = (vehicle_hint or "").strip().upper()
    if hint_norm in ("XE_MAY", "XEMAY", "MOTO") and ok_moto:
        return moto_plate, VehicleTypes.XE_MAY.value
    if hint_norm in ("O_TO", "OTO", "CAR") and ok_car:
        return car_plate, VehicleTypes.O_TO.value

    if ok_car and not ok_moto:
        return car_plate, VehicleTypes.O_TO.value
    if ok_moto and not ok_car:
        return moto_plate, VehicleTypes.XE_MAY.value
    if ok_car and ok_moto:
        return (moto_plate, VehicleTypes.XE_MAY.value) if hint_norm == "XE_MAY" else (car_plate, VehicleTypes.O_TO.value)

    return normalized, normalize_vehicle_type(vehicle_hint, normalized)


def _crop_by_bbox(frame_bgr: np.ndarray, bbox: Any) -> Optional[np.ndarray]:
    """Cắt vùng ảnh biển số theo bbox chuẩn hóa [ymin, xmin, ymax, xmax] (0..1000) hoặc pixel."""
    if frame_bgr is None or frame_bgr.size == 0 or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        vals = [float(x) for x in bbox]
    except (TypeError, ValueError):
        return None

    h, w = frame_bgr.shape[:2]
    ymin, xmin, ymax, xmax = vals
    if max(vals) <= 1.0:
        y1, x1, y2, x2 = int(ymin * h), int(xmin * w), int(ymax * h), int(xmax * w)
    elif max(vals) <= 1000.0:
        y1 = int((ymin / 1000.0) * h)
        x1 = int((xmin / 1000.0) * w)
        y2 = int((ymax / 1000.0) * h)
        x2 = int((xmax / 1000.0) * w)
    else:
        y1, x1, y2, x2 = int(ymin), int(xmin), int(ymax), int(xmax)

    pad_x = max(4, int((x2 - x1) * 0.08))
    pad_y = max(4, int((y2 - y1) * 0.10))
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(w, x2 + pad_x)
    y2 = min(h, y2 + pad_y)
    if x2 - x1 < 12 or y2 - y1 < 8:
        return None
    return frame_bgr[y1:y2, x1:x2].copy()


def _detect_plate_candidates_cv(frame_bgr: np.ndarray) -> List[Tuple[np.ndarray, Tuple[int, int, int, int]]]:
    """Tìm vùng ứng viên biển số bằng OpenCV Contour / tỷ lệ khung hình khi không có weights YOLO."""
    if frame_bgr is None or frame_bgr.size == 0:
        return []
    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.bilateralFilter(gray, 11, 17, 17)
    edges = cv2.Canny(blur, 40, 160)
    contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:35]

    candidates: List[Tuple[np.ndarray, Tuple[int, int, int, int]]] = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < max(400.0, (w * h) * 0.002):
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bh <= 0 or bw <= 0:
            continue
        ratio = bw / float(bh)
        # Biển số 2 dòng (~1.1 - 1.9) hoặc 1 dòng (~2.5 - 5.5)
        if 1.05 <= ratio <= 5.8 and bw >= 50 and bh >= 22:
            crop = frame_bgr[y : y + bh, x : x + bw].copy()
            candidates.append((crop, (y, x, y + bh, x + bw)))
            if len(candidates) >= 4:
                break
    return candidates


class PlateRecognizer:
    """Bộ nhận diện biển số Hybrid 2 tầng dùng được cả đồng bộ lẫn trong QThread."""

    _GEMINI_MAX_IMAGE_SIDE = 1024
    _GEMINI_JPEG_QUALITY = 80
    _GEMINI_TIMEOUT_MS = 12_000

    _easyocr_lock = threading.Lock()
    _easyocr_reader: Any = None
    _yolo_lock = threading.Lock()
    _yolo_model: Any = None
    _yolo_checked: bool = False
    _genai_lock = threading.Lock()
    _genai_clients: Dict[str, Any] = {}
    _model_cooldowns: Dict[str, float] = {}

    def __init__(
        self,
        api_key: Optional[str] = None,
        primary_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")).strip()
        self.primary_model = (primary_model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")).strip()
        self.fallback_model = (fallback_model or os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")).strip()

    @staticmethod
    def _is_valid_api_key(key: str) -> bool:
        cleaned = (key or "").strip().strip('"').strip("'")
        if not cleaned or cleaned.lower() in ("aaaa", "your_gemini_api_key", "none", "null"):
            return False
        return len(cleaned) >= 12

    @classmethod
    def _get_easyocr_reader(cls) -> Any:
        with cls._easyocr_lock:
            if cls._easyocr_reader is None:
                import easyocr

                cls._easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            return cls._easyocr_reader

    @classmethod
    def _get_yolo_model(cls) -> Any:
        with cls._yolo_lock:
            if cls._yolo_checked:
                return cls._yolo_model
            cls._yolo_checked = True
            model_path = os.getenv("YOLO_PLATE_MODEL", "").strip()
            if model_path and Path(model_path).is_file():
                try:
                    from ultralytics import YOLO

                    cls._yolo_model = YOLO(model_path)
                except Exception:
                    cls._yolo_model = None
            return cls._yolo_model

    @classmethod
    def _get_genai_client(cls, genai_module: Any, types_module: Any, clean_key: str) -> Any:
        """Tái sử dụng genai.Client theo API key và tắt retry ngầm để tránh treo 30s+ khi lỗi."""
        with cls._genai_lock:
            cached = cls._genai_clients.get(clean_key)
            if cached is not None:
                return cached

            http_options = None
            try:
                retry_cls = getattr(types_module, "HttpRetryOptions", None)
                http_cls = getattr(types_module, "HttpOptions", None)
                if http_cls is not None and retry_cls is not None:
                    http_options = http_cls(
                        timeout=cls._GEMINI_TIMEOUT_MS,
                        retry_options=retry_cls(attempts=1),
                    )
                elif http_cls is not None:
                    http_options = http_cls(timeout=cls._GEMINI_TIMEOUT_MS)
            except Exception:
                http_options = None

            client = (
                genai_module.Client(api_key=clean_key, http_options=http_options)
                if http_options is not None
                else genai_module.Client(api_key=clean_key)
            )
            cls._genai_clients[clean_key] = client
            return client

    @classmethod
    def _encode_frame_for_gemini(cls, frame_bgr: np.ndarray) -> Optional[bytes]:
        """Resize ảnh về cạnh dài tối đa 1024px và nén JPEG để gửi Gemini nhanh hơn."""
        if frame_bgr is None or frame_bgr.size == 0:
            return None
        h, w = frame_bgr.shape[:2]
        max_dim = max(h, w)
        target = frame_bgr
        if max_dim > cls._GEMINI_MAX_IMAGE_SIDE:
            scale = cls._GEMINI_MAX_IMAGE_SIDE / float(max_dim)
            new_w = max(1, int(round(w * scale)))
            new_h = max(1, int(round(h * scale)))
            target = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)

        ok, encoded = cv2.imencode(
            ".jpg",
            target,
            [int(cv2.IMWRITE_JPEG_QUALITY), cls._GEMINI_JPEG_QUALITY],
        )
        return encoded.tobytes() if ok else None

    @staticmethod
    def _build_gemini_config(types_module: Any, model_name: str, minimize_thinking: bool = True) -> Any:
        """Tạo cấu hình GenerateContent tối ưu độ trễ cho OCR (giảm tối đa thinking tokens)."""
        kwargs: Dict[str, Any] = {
            "response_mime_type": "application/json",
            "temperature": 0.1,
            "max_output_tokens": 256,
        }
        if minimize_thinking:
            thinking_cls = getattr(types_module, "ThinkingConfig", None)
            if thinking_cls is not None:
                if "2.5" in model_name:
                    kwargs["thinking_config"] = thinking_cls(thinking_budget=0)
                else:
                    level_enum = getattr(types_module, "ThinkingLevel", None)
                    level_val = getattr(level_enum, "MINIMAL", "MINIMAL") if level_enum is not None else "MINIMAL"
                    kwargs["thinking_config"] = thinking_cls(thinking_level=level_val)
        return types_module.GenerateContentConfig(**kwargs)

    @classmethod
    def _mark_model_cooldown(cls, model_name: str, seconds: float) -> None:
        with cls._genai_lock:
            cls._model_cooldowns[model_name] = time.monotonic() + seconds

    @classmethod
    def _select_models_to_try(cls, primary_model: str, fallback_model: str) -> List[str]:
        candidates = [m for m in (primary_model, fallback_model) if m]
        # Loại trùng nhưng giữ nguyên thứ tự
        unique_models = list(dict.fromkeys(candidates))
        now = time.monotonic()
        with cls._genai_lock:
            active = [m for m in unique_models if now >= cls._model_cooldowns.get(m, 0.0)]
        return active if active else unique_models

    def recognize_with_gemini_vision(self, frame_bgr: np.ndarray) -> Optional[Dict[str, Any]]:
        """Tầng 1: Gọi Gemini Flash Vision API để đọc biển số, loại xe và bounding box."""
        clean_key = self.api_key.strip().strip('"').strip("'")
        if not self._is_valid_api_key(clean_key):
            return None

        image_bytes = self._encode_frame_for_gemini(frame_bgr)
        if not image_bytes:
            return None

        try:
            from google import genai
            from google.genai import types
        except Exception:
            return None

        client = self._get_genai_client(genai, types, clean_key)
        models_to_try = self._select_models_to_try(self.primary_model, self.fallback_model)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        last_error: Optional[Exception] = None
        for model_name in models_to_try:
            for minimize_thinking in (True, False):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[image_part, _GEMINI_SYSTEM_PROMPT],
                        config=self._build_gemini_config(types, model_name, minimize_thinking=minimize_thinking),
                    )
                    raw_json = (getattr(response, "text", None) or "").strip()
                    if not raw_json:
                        break
                    parsed = self._parse_gemini_json(raw_json)
                    raw_plate = str(parsed.get("bien_so") or "").strip()
                    if not raw_plate:
                        break

                    plate, vtype = post_process_plate_text(raw_plate, parsed.get("loai_xe"))
                    confidence = float(parsed.get("confidence") or 0.95)
                    confidence = max(0.0, min(1.0, confidence))
                    crop_bgr = _crop_by_bbox(frame_bgr, parsed.get("bbox"))
                    return {
                        "success": bool(plate),
                        "bien_so": plate,
                        "loai_xe": vtype,
                        "confidence": confidence,
                        "engine_used": f"Gemini Vision AI ({model_name})",
                        "crop_bgr": crop_bgr,
                        "raw_text": raw_plate,
                    }
                except Exception as exc:
                    last_error = exc
                    err_txt = str(exc).lower()
                    # Nếu model không hỗ trợ thinking_config, thử lại ngay không truyền thinking_config
                    if minimize_thinking and ("thinking" in err_txt or "400" in err_txt or "invalid_argument" in err_txt):
                        continue
                    if "429" in err_txt or "quota" in err_txt or "resource_exhausted" in err_txt:
                        cooldown = 300.0 if ("per_day" in err_txt or "day" in err_txt) else 60.0
                        self._mark_model_cooldown(model_name, cooldown)
                    elif "404" in err_txt or "not_found" in err_txt or "not found" in err_txt:
                        self._mark_model_cooldown(model_name, 600.0)
                    break

        if last_error is not None:
            return None
        return None

    @staticmethod
    def _parse_gemini_json(raw_text: str) -> Dict[str, Any]:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                    return data if isinstance(data, dict) else {}
                except json.JSONDecodeError:
                    return {}
            return {}

    def recognize_with_yolo_easyocr(self, frame_bgr: np.ndarray) -> Dict[str, Any]:
        """Tầng 2 (Fallback Offline): YOLO/OpenCV phát hiện vùng biển số + EasyOCR đọc ký tự."""
        candidates: List[Tuple[np.ndarray, Optional[Tuple[int, int, int, int]]]] = []
        yolo_model = self._get_yolo_model()
        if yolo_model is not None:
            try:
                preds = yolo_model.predict(frame_bgr, verbose=False)
                for pred in preds:
                    for box in getattr(pred, "boxes", []) or []:
                        xyxy = box.xyxy[0].tolist()
                        x1, y1, x2, y2 = [int(v) for v in xyxy[:4]]
                        if x2 > x1 and y2 > y1:
                            candidates.append((frame_bgr[y1:y2, x1:x2].copy(), (y1, x1, y2, x2)))
            except Exception:
                pass

        for crop, box in _detect_plate_candidates_cv(frame_bgr):
            candidates.append((crop, box))

        # Cuối cùng luôn thêm toàn khung hình làm phương án dự phòng
        candidates.append((frame_bgr, None))

        try:
            reader = self._get_easyocr_reader()
        except Exception as exc:
            return {
                "success": False,
                "bien_so": "",
                "loai_xe": VehicleTypes.O_TO.value,
                "confidence": 0.0,
                "engine_used": "YOLO + EasyOCR (Chưa sẵn sàng)",
                "crop_bgr": None,
                "raw_text": "",
                "error": f"Không thể khởi tạo EasyOCR: {exc}",
            }

        best_plate = ""
        best_vtype = VehicleTypes.O_TO.value
        best_conf = 0.0
        best_crop: Optional[np.ndarray] = None
        best_raw = ""

        for crop_bgr, _box in candidates:
            try:
                ocr_results = reader.readtext(crop_bgr, detail=1)
            except Exception:
                continue
            if not ocr_results:
                continue

            # Ghép các dòng OCR theo thứ tự từ trên xuống dưới (hỗ trợ biển 2 dòng xe máy)
            sorted_items = sorted(ocr_results, key=lambda r: (r[0][0][1], r[0][0][0]))
            combined_text = "".join(str(item[1]) for item in sorted_items)
            avg_conf = sum(float(item[2]) for item in sorted_items) / float(len(sorted_items))
            plate, vtype = post_process_plate_text(combined_text)
            is_valid, _ = validate_plate(plate)

            score = avg_conf + (0.35 if is_valid else 0.0)
            best_score = best_conf + (0.35 if validate_plate(best_plate)[0] else 0.0)
            if plate and score > best_score:
                best_plate = plate
                best_vtype = vtype
                best_conf = min(1.0, avg_conf)
                best_crop = crop_bgr if crop_bgr is not frame_bgr else None
                best_raw = combined_text
                if is_valid and avg_conf >= 0.65:
                    break

        return {
            "success": bool(best_plate),
            "bien_so": best_plate,
            "loai_xe": best_vtype,
            "confidence": round(float(best_conf), 3),
            "engine_used": "YOLO + EasyOCR (Offline)",
            "crop_bgr": best_crop,
            "raw_text": best_raw,
        }

    def recognize_frame(self, frame_bgr: Optional[np.ndarray]) -> Dict[str, Any]:
        """Nhận diện biển số từ frame BGR (Tầng 1 Gemini Vision -> Tầng 2 YOLO/EasyOCR)."""
        t0 = time.monotonic()
        if frame_bgr is None or frame_bgr.size == 0:
            return {
                "success": False,
                "bien_so": "",
                "loai_xe": VehicleTypes.O_TO.value,
                "confidence": 0.0,
                "engine_used": "None",
                "plate_crop_qimage": QImage(),
                "raw_text": "",
                "elapsed_ms": 0,
                "error": "Không có khung hình hợp lệ để nhận diện.",
            }

        result = self.recognize_with_gemini_vision(frame_bgr)
        if not result or not result.get("success"):
            result = self.recognize_with_yolo_easyocr(frame_bgr)

        crop_bgr = result.pop("crop_bgr", None)
        crop_qimg = bgr_to_qimage(crop_bgr) if crop_bgr is not None else bgr_to_qimage(frame_bgr)
        result["plate_crop_qimage"] = crop_qimg
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result


class PlateRecognitionWorker(QThread):
    """QThread chạy nhận diện biển số bất đồng bộ để không chặn luồng giao diện."""

    recognition_finished = Signal(dict)

    def __init__(
        self,
        frame_bgr: np.ndarray,
        recognizer: Optional[PlateRecognizer] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._frame_bgr = frame_bgr.copy() if frame_bgr is not None else None
        self._recognizer = recognizer or PlateRecognizer()

    def run(self) -> None:
        result = self._recognizer.recognize_frame(self._frame_bgr)
        self.recognition_finished.emit(result)
