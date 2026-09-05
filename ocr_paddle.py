"""PaddleOCR PP-OCRv5 주 OCR 엔진 모듈.

RapidOCR(rapidocr_onnxruntime) 래퍼 또는 직접 ONNX Runtime 추론을 통해
PaddleOCR PP-OCRv5 한국어+영어 모델로 텍스트를 인식한다.

지원 기능:
- RapidOCR 래퍼 우선 사용 (설치된 경우)
- 직접 ONNX Runtime 추론 fallback
- DirectML GPU 자동 감지 + CPU fallback
- 모델 지연 로딩 및 캐시
- 한국어 + 영어 + 숫자 인식
- 글자별 좌표 박스 + 신뢰도 반환
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "PaddleBox",
    "PaddleResult",
    "PaddleEngine",
    "is_available",
    "get_default_model_dir",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency imports
# ---------------------------------------------------------------------------

_HAS_NUMPY = False
_HAS_ONNXRUNTIME = False
_HAS_RAPIDOCR = False
_HAS_RAPIDOCR_V3 = False
_HAS_RAPIDOCR_LEGACY = False
_HAS_CV2 = False

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]

try:
    import onnxruntime as ort

    _HAS_ONNXRUNTIME = True
except ImportError:
    ort = None  # type: ignore[assignment]

try:
    from rapidocr import (  # type: ignore[import-untyped]
        LangRec as RapidLangRec,
        ModelType as RapidModelType,
        OCRVersion as RapidOCRVersion,
        RapidOCR as RapidOCRV3,
    )

    _HAS_RAPIDOCR = True
    _HAS_RAPIDOCR_V3 = True
except ImportError:
    RapidOCRV3 = None  # type: ignore[assignment,misc]
    RapidLangRec = RapidModelType = RapidOCRVersion = None  # type: ignore[assignment]

try:
    from rapidocr_onnxruntime import RapidOCR as RapidOCRLegacy  # type: ignore[import-untyped]

    _HAS_RAPIDOCR = True
    _HAS_RAPIDOCR_LEGACY = True
except ImportError:
    RapidOCRLegacy = None  # type: ignore[assignment,misc]

try:
    import cv2

    _HAS_CV2 = True
except ImportError:
    cv2 = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PaddleBox:
    """인식된 텍스트 하나의 위치와 신뢰도."""

    text: str
    confidence: float
    rect: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    center: tuple[int, int]
    polygon: list[list[int]] = field(default_factory=list)


@dataclass
class PaddleResult:
    """PaddleOCR 인식 결과."""

    text: str
    normalized_text: str
    confidence: float
    boxes: list[PaddleBox]
    engine: str  # "paddle_rapidocr" | "paddle_onnx" | "paddle_unavailable"
    elapsed_ms: float
    raw_results: Any = None


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def get_default_model_dir() -> Path:
    """모델 디렉토리 기본 경로를 반환한다."""
    return Path(__file__).resolve().parent / "models"


def is_available() -> bool:
    """PaddleOCR 엔진 사용 가능 여부를 반환한다."""
    if _HAS_RAPIDOCR and _HAS_NUMPY:
        return True
    if _HAS_ONNXRUNTIME and _HAS_NUMPY and _HAS_CV2:
        return True
    return False


def _detect_providers() -> list[str | tuple[str, dict[str, Any]]]:
    """사용 가능한 ONNX Runtime Execution Provider 목록을 반환한다."""
    if not _HAS_ONNXRUNTIME:
        return []
    available = ort.get_available_providers()
    providers: list[str | tuple[str, dict[str, Any]]] = []
    if "DmlExecutionProvider" in available:
        providers.append(("DmlExecutionProvider", {"device_id": 0}))
    providers.append("CPUExecutionProvider")
    return providers


def _normalize_text(text: str) -> str:
    """OCR 결과 텍스트를 정규화한다."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned = [line.strip() for line in lines if line.strip()]
    return "\n".join(cleaned)


def _polygon_to_rect(polygon: list[list[int | float]]) -> tuple[int, int, int, int]:
    """다각형 좌표를 (x1, y1, x2, y2) 사각형으로 변환한다."""
    xs = [int(p[0]) for p in polygon]
    ys = [int(p[1]) for p in polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def _rect_center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    """사각형의 중심 좌표를 반환한다."""
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


# ---------------------------------------------------------------------------
# PaddleEngine
# ---------------------------------------------------------------------------


class PaddleEngine:
    """PaddleOCR PP-OCRv5 엔진 래퍼.

    사용 예::

        engine = PaddleEngine()
        engine.load_models()
        result = engine.recognize(image_bgr)
        engine.unload_models()
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        use_gpu: bool = True,
        det_model: str = "ppocrv5_det.onnx",
        rec_model: str = "ppocrv5_korean_rec.onnx",
        cls_model: str = "ch_ppocr_mobile_v2.0_cls.onnx",
        dict_file: str = "korean_dict.txt",
    ) -> None:
        self.model_dir = Path(model_dir) if model_dir else get_default_model_dir()
        self.use_gpu = use_gpu
        self.det_model_name = det_model
        self.rec_model_name = rec_model
        self.cls_model_name = cls_model
        self.dict_file_name = dict_file

        self._rapidocr_engine: Any | None = None
        self._onnx_sessions: dict[str, Any] = {}
        self._character_list: list[str] = []
        self._loaded = False
        self._backend = "none"  # "rapidocr" | "onnx" | "none"
        self._uses_custom_models = False
        self._rapidocr_language = ""

    @property
    def is_loaded(self) -> bool:
        """모델이 로딩되었는지 여부."""
        return self._loaded

    @property
    def backend(self) -> str:
        """현재 사용 중인 백엔드."""
        return self._backend

    def supports_language(self, lang: str) -> bool:
        """Whether the loaded model is appropriate for the requested language."""
        requested = {item.strip().casefold() for item in str(lang or "").split("+") if item.strip()}
        # RapidOCR's bundled fallback model is intended for Chinese/English.
        # Korean auto mode must stay on Tesseract until Korean model files are
        # installed; an explicit Paddle selection still remains available.
        return "kor" not in requested or _HAS_RAPIDOCR_V3 or self._uses_custom_models

    def _model_path(self, name: str) -> Path:
        return self.model_dir / name

    def _models_exist(self) -> bool:
        """필수 모델 파일이 존재하는지 확인한다."""
        required = [self.det_model_name, self.rec_model_name, self.dict_file_name]
        return all(self._model_path(f).exists() for f in required)

    def load_models(self) -> None:
        """OCR 모델을 메모리에 로딩한다."""
        if self._loaded:
            return

        self._uses_custom_models = self._models_exist()

        # 1) RapidOCR 3.x는 언어별 모델을 recognize() 시점에 지연 로딩한다.
        if _HAS_RAPIDOCR_V3:
            self._backend = "rapidocr_v3"
            self._loaded = True
            logger.info("PaddleOCR 로딩 준비 완료 (RapidOCR 3.x)")
            return

        # 2) 구형 RapidOCR fallback
        if _HAS_RAPIDOCR_LEGACY:
            try:
                self._load_rapidocr(use_custom_models=self._uses_custom_models)
                self._backend = "rapidocr_legacy"
                self._loaded = True
                logger.info("PaddleOCR 로딩 완료 (RapidOCR 백엔드)")
                return
            except Exception as exc:
                logger.warning("RapidOCR 로딩 실패, ONNX 직접 추론으로 전환: %s", exc)

        # 3) 직접 ONNX Runtime 추론
        if self._models_exist() and _HAS_ONNXRUNTIME and _HAS_CV2:
            try:
                self._load_onnx_direct()
                self._backend = "onnx"
                self._loaded = True
                logger.info("PaddleOCR 로딩 완료 (ONNX Runtime 직접 추론)")
                return
            except Exception as exc:
                logger.error("ONNX Runtime 로딩 실패: %s", exc)

        self._backend = "none"
        logger.error("PaddleOCR 엔진을 로딩할 수 없습니다.")

    def _load_rapidocr(self, use_custom_models: bool = True) -> None:
        """RapidOCR 래퍼를 통한 모델 로딩."""
        if not _HAS_RAPIDOCR_LEGACY:
            raise RuntimeError("rapidocr_onnxruntime이 설치되어 있지 않습니다.")

        if not use_custom_models:
            # RapidOCR packages include a proven default model set. This keeps
            # the fast engine usable before optional Korean PP-OCR models are
            # downloaded from the component manager.
            self._rapidocr_engine = RapidOCRLegacy()
            return

        det_path = self._model_path(self.det_model_name)
        rec_path = self._model_path(self.rec_model_name)
        cls_path = self._model_path(self.cls_model_name)
        dict_path = self._model_path(self.dict_file_name)

        params: dict[str, Any] = {
            "Det.model_path": str(det_path),
            "Det.limit_side_len": 960,
            "Det.thresh": 0.3,
            "Det.box_thresh": 0.6,
            "Det.unclip_ratio": 1.6,
            "Rec.model_path": str(rec_path),
            "Rec.rec_keys_path": str(dict_path),
            "Rec.rec_img_shape": [3, 48, 320],
        }

        if cls_path.exists():
            params["Cls.model_path"] = str(cls_path)

        self._rapidocr_engine = RapidOCRLegacy(params=params)

        # 워밍업: 더미 이미지로 첫 추론 실행 (DirectML JIT 컴파일)
        if _HAS_NUMPY:
            dummy = np.zeros((48, 320, 3), dtype=np.uint8)
            dummy[10:38, 10:100] = 200  # 밝은 텍스트 영역 시뮬레이션
            try:
                self._rapidocr_engine(dummy)
            except Exception:
                pass  # 워밍업 실패는 무시

    def _load_onnx_direct(self) -> None:
        """ONNX Runtime 직접 추론 모델 로딩."""
        if not _HAS_ONNXRUNTIME:
            raise RuntimeError("onnxruntime이 설치되어 있지 않습니다.")

        providers = _detect_providers() if self.use_gpu else ["CPUExecutionProvider"]

        sess_options = ort.SessionOptions()
        sess_options.enable_mem_pattern = False
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_options.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )

        # Detection model
        det_path = str(self._model_path(self.det_model_name))
        self._onnx_sessions["det"] = ort.InferenceSession(
            det_path, sess_options=sess_options, providers=providers
        )

        # Recognition model
        rec_path = str(self._model_path(self.rec_model_name))
        self._onnx_sessions["rec"] = ort.InferenceSession(
            rec_path, sess_options=sess_options, providers=providers
        )

        # Direction classifier (optional)
        cls_path = self._model_path(self.cls_model_name)
        if cls_path.exists():
            self._onnx_sessions["cls"] = ort.InferenceSession(
                str(cls_path), sess_options=sess_options, providers=providers
            )

        # Load dictionary
        dict_path = self._model_path(self.dict_file_name)
        self._character_list = ["blank"]
        with open(dict_path, "r", encoding="utf-8") as f:
            for line in f:
                ch = line.strip("\n\r")
                if ch:
                    self._character_list.append(ch)
        self._character_list.append(" ")

        # 워밍업
        if _HAS_NUMPY:
            dummy = np.zeros((1, 3, 64, 320), dtype=np.float32)
            det_sess = self._onnx_sessions["det"]
            try:
                det_sess.run(None, {det_sess.get_inputs()[0].name: dummy})
            except Exception:
                pass

    def unload_models(self) -> None:
        """모델을 메모리에서 해제한다."""
        self._rapidocr_engine = None
        self._onnx_sessions.clear()
        self._character_list.clear()
        self._loaded = False
        self._backend = "none"
        self._uses_custom_models = False
        self._rapidocr_language = ""
        logger.info("PaddleOCR 모델 해제 완료")

    def _ensure_rapidocr_v3(self, lang: str) -> None:
        """요청 언어에 맞는 PP-OCRv5 인식 모델을 한 번만 로드한다."""
        requested = {part.strip().casefold() for part in str(lang or "").split("+") if part.strip()}
        language_key = "korean" if "kor" in requested else "english"
        if self._rapidocr_engine is not None and self._rapidocr_language == language_key:
            return
        if not _HAS_RAPIDOCR_V3:
            raise RuntimeError("rapidocr 3.x가 설치되어 있지 않습니다.")

        rec_lang = RapidLangRec.KOREAN if language_key == "korean" else RapidLangRec.EN
        params: dict[str, Any] = {
            "Global.model_root_dir": str(self.model_dir),
            "Global.use_cls": False,
            "Global.log_level": "warning",
            "Det.ocr_version": RapidOCRVersion.PPOCRV5,
            "Det.model_type": RapidModelType.MOBILE,
            # Screen OCR must not enlarge the *short* side of a full-screen
            # capture to 960px: that fragments Korean words into characters.
            # Limiting the long side preserves a full line and is much faster.
            "Det.limit_type": "max",
            "Det.limit_side_len": 1280,
            "Det.box_thresh": 0.30,
            "Det.unclip_ratio": 2.0,
            "Rec.ocr_version": RapidOCRVersion.PPOCRV5,
            "Rec.lang_type": rec_lang,
            "Rec.model_type": RapidModelType.MOBILE,
        }
        self._rapidocr_engine = RapidOCRV3(params=params)
        self._rapidocr_language = language_key
        self._uses_custom_models = language_key == "korean"

        if _HAS_NUMPY:
            try:
                self._rapidocr_engine(np.zeros((64, 320, 3), dtype=np.uint8))
            except Exception:
                pass

    def recognize(
        self,
        image: "np.ndarray",
        scale: float = 1.0,
        lang: str = "eng+kor",
    ) -> PaddleResult:
        """이미지에서 텍스트를 인식한다.

        Args:
            image: BGR 형식 numpy 배열
            scale: 원본 대비 전처리 확대 비율 (좌표 역보정에 사용)

        Returns:
            PaddleResult 인식 결과
        """
        if not _HAS_NUMPY:
            return self._empty_result("paddle_unavailable", "numpy 미설치")

        if not self._loaded:
            self.load_models()

        if not self._loaded:
            return self._empty_result("paddle_unavailable", "모델 로딩 실패")

        start = time.perf_counter()

        if self._backend == "rapidocr_v3":
            try:
                self._ensure_rapidocr_v3(lang)
            except Exception as exc:
                logger.error("RapidOCR 3.x 모델 로딩 오류: %s", exc)
                return self._empty_result("paddle_rapidocr_v3", str(exc))
            result = self._recognize_rapidocr_v3(image, scale)
        elif self._backend == "rapidocr_legacy":
            result = self._recognize_rapidocr(image, scale)
        elif self._backend == "onnx":
            result = self._recognize_onnx(image, scale)
        else:
            result = self._empty_result("paddle_unavailable", "엔진 없음")

        elapsed = (time.perf_counter() - start) * 1000
        result.elapsed_ms = round(elapsed, 2)
        return result

    def _recognize_rapidocr_v3(
        self, image: "np.ndarray", scale: float
    ) -> PaddleResult:
        """RapidOCR 3.x 구조화 출력을 공통 결과 형식으로 변환한다."""
        try:
            output = self._rapidocr_engine(image)
        except Exception as exc:
            logger.error("RapidOCR 3.x 인식 오류: %s", exc)
            return self._empty_result("paddle_rapidocr_v3", str(exc))

        raw_boxes = getattr(output, "boxes", None)
        raw_texts = getattr(output, "txts", None)
        raw_scores = getattr(output, "scores", None)
        if raw_boxes is None or raw_texts is None or raw_scores is None:
            return self._empty_result("paddle_rapidocr_v3")

        boxes: list[PaddleBox] = []
        texts: list[str] = []
        weighted_confidence = 0.0
        weight_total = 0
        for polygon_raw, text_raw, score_raw in zip(raw_boxes, raw_texts, raw_scores):
            text = str(text_raw).strip()
            if not text:
                continue
            confidence = max(0.0, min(float(score_raw), 1.0))
            if scale != 1.0 and scale > 0:
                polygon = [[int(round(float(p[0]) / scale)), int(round(float(p[1]) / scale))] for p in polygon_raw]
            else:
                polygon = [[int(round(float(p[0]))), int(round(float(p[1])))] for p in polygon_raw]
            rect = _polygon_to_rect(polygon)
            boxes.append(PaddleBox(text, confidence, rect, _rect_center(rect), polygon))
            texts.append(text)
            weight = max(len(text), 1)
            weighted_confidence += confidence * weight
            weight_total += weight

        full_text = "\n".join(texts)
        return PaddleResult(
            text=full_text,
            normalized_text=_normalize_text(full_text),
            confidence=round(weighted_confidence / weight_total, 4) if weight_total else 0.0,
            boxes=boxes,
            engine="paddle_korean_v5" if self._rapidocr_language == "korean" else "paddle_english_v5",
            elapsed_ms=0.0,
            raw_results=output,
        )

    def _recognize_rapidocr(
        self, image: "np.ndarray", scale: float
    ) -> PaddleResult:
        """RapidOCR 래퍼를 통한 인식."""
        try:
            result, elapse_list = self._rapidocr_engine(image)
        except Exception as exc:
            logger.error("RapidOCR 인식 오류: %s", exc)
            return self._empty_result("paddle_rapidocr", str(exc))

        if not result:
            return PaddleResult(
                text="",
                normalized_text="",
                confidence=0.0,
                boxes=[],
                engine="paddle_rapidocr",
                elapsed_ms=0.0,
                raw_results=result,
            )

        boxes: list[PaddleBox] = []
        all_texts: list[str] = []
        total_conf = 0.0

        for item in result:
            polygon_raw = item[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            text = str(item[1])
            conf = float(item[2])

            # 스케일 역보정
            if scale != 1.0 and scale > 0:
                polygon = [[int(p[0] / scale), int(p[1] / scale)] for p in polygon_raw]
            else:
                polygon = [[int(p[0]), int(p[1])] for p in polygon_raw]

            rect = _polygon_to_rect(polygon)
            center = _rect_center(rect)

            boxes.append(
                PaddleBox(
                    text=text,
                    confidence=conf,
                    rect=rect,
                    center=center,
                    polygon=polygon,
                )
            )
            all_texts.append(text)
            total_conf += conf

        full_text = "\n".join(all_texts)
        avg_conf = total_conf / len(boxes) if boxes else 0.0

        return PaddleResult(
            text=full_text,
            normalized_text=_normalize_text(full_text),
            confidence=round(avg_conf, 4),
            boxes=boxes,
            engine="paddle_rapidocr",
            elapsed_ms=0.0,
            raw_results=result,
        )

    def _recognize_onnx(self, image: "np.ndarray", scale: float) -> PaddleResult:
        """직접 ONNX Runtime 추론."""
        if not _HAS_CV2 or not _HAS_NUMPY:
            return self._empty_result("paddle_onnx", "cv2/numpy 미설치")

        try:
            # 1) Text Detection
            det_input, ratio_hw = self._preprocess_det(image)
            det_sess = self._onnx_sessions["det"]
            det_output = det_sess.run(
                None, {det_sess.get_inputs()[0].name: det_input}
            )[0]

            src_h, src_w = image.shape[:2]
            text_boxes = self._postprocess_dbnet(
                det_output, ratio_hw, src_h, src_w
            )

            if not text_boxes:
                return PaddleResult(
                    text="",
                    normalized_text="",
                    confidence=0.0,
                    boxes=[],
                    engine="paddle_onnx",
                    elapsed_ms=0.0,
                )

            # 2) Recognize each detected text region
            boxes: list[PaddleBox] = []
            all_texts: list[str] = []
            total_conf = 0.0

            for box_points in text_boxes:
                crop = self._get_rotate_crop(image, box_points)
                if crop is None or crop.shape[0] < 4 or crop.shape[1] < 4:
                    continue

                rec_input = self._preprocess_rec(crop)
                rec_sess = self._onnx_sessions["rec"]
                rec_output = rec_sess.run(
                    None, {rec_sess.get_inputs()[0].name: rec_input}
                )[0]

                text, conf = self._ctc_decode(rec_output)
                if not text.strip():
                    continue

                # 스케일 역보정
                if scale != 1.0 and scale > 0:
                    polygon = [
                        [int(p[0] / scale), int(p[1] / scale)]
                        for p in box_points.tolist()
                    ]
                else:
                    polygon = [[int(p[0]), int(p[1])] for p in box_points.tolist()]

                rect = _polygon_to_rect(polygon)
                center = _rect_center(rect)

                boxes.append(
                    PaddleBox(
                        text=text,
                        confidence=round(conf, 4),
                        rect=rect,
                        center=center,
                        polygon=polygon,
                    )
                )
                all_texts.append(text)
                total_conf += conf

            full_text = "\n".join(all_texts)
            avg_conf = total_conf / len(boxes) if boxes else 0.0

            return PaddleResult(
                text=full_text,
                normalized_text=_normalize_text(full_text),
                confidence=round(avg_conf, 4),
                boxes=boxes,
                engine="paddle_onnx",
                elapsed_ms=0.0,
            )

        except Exception as exc:
            logger.error("ONNX 추론 오류: %s", exc)
            return self._empty_result("paddle_onnx", str(exc))

    # ------------------------------------------------------------------
    # ONNX direct inference helpers
    # ------------------------------------------------------------------

    def _preprocess_det(
        self, image: "np.ndarray", max_side: int = 960
    ) -> tuple["np.ndarray", tuple[float, float]]:
        """Detection 모델 입력 전처리."""
        h, w = image.shape[:2]
        ratio = 1.0
        if max(h, w) > max_side:
            ratio = float(max_side) / float(max(h, w))
        resize_h = max(int(round(h * ratio / 32) * 32), 32)
        resize_w = max(int(round(w * ratio / 32) * 32), 32)
        resized = cv2.resize(image, (resize_w, resize_h))

        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)
        norm_img = (resized.astype(np.float32) / 255.0 - mean) / std
        norm_img = norm_img.transpose((2, 0, 1))[np.newaxis, ...]
        return norm_img.astype(np.float32), (resize_h / h, resize_w / w)

    def _postprocess_dbnet(
        self,
        pred_map: "np.ndarray",
        ratio_hw: tuple[float, float],
        src_h: int,
        src_w: int,
        thresh: float = 0.3,
        box_thresh: float = 0.6,
        unclip_ratio: float = 1.6,
    ) -> list["np.ndarray"]:
        """DBNet 출력에서 텍스트 영역 박스를 추출한다."""
        pred = pred_map[0, 0, :, :]
        segmentation = (pred > thresh).astype(np.uint8) * 255

        contours, _ = cv2.findContours(
            segmentation, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
        )

        boxes: list[np.ndarray] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 16:
                continue

            mask = np.zeros(pred.shape, dtype=np.uint8)
            cv2.drawContours(mask, [contour], -1, 1, -1)
            score = float(cv2.mean(pred, mask=mask)[0])
            if score < box_thresh:
                continue

            # Polygon expansion (unclipping)
            try:
                import pyclipper  # type: ignore[import-untyped]

                poly = contour.reshape(-1, 2)
                peri = cv2.arcLength(contour, True)
                if peri < 1:
                    continue
                distance = area * unclip_ratio / peri
                offset = pyclipper.PyclipperOffset()
                offset.AddPath(
                    poly.tolist(),
                    pyclipper.JT_ROUND,
                    pyclipper.ET_CLOSEDPOLYGON,
                )
                expanded = offset.Execute(distance)
                if not expanded:
                    continue
                expanded_poly = np.array(expanded[0])
            except ImportError:
                # pyclipper 없으면 원본 contour 사용
                expanded_poly = contour.reshape(-1, 2)

            rect = cv2.minAreaRect(expanded_poly)
            box = cv2.boxPoints(rect)

            box[:, 0] = np.clip(box[:, 0] / ratio_hw[1], 0, src_w)
            box[:, 1] = np.clip(box[:, 1] / ratio_hw[0], 0, src_h)
            boxes.append(box.astype(np.int32))

        return boxes

    def _get_rotate_crop(
        self, image: "np.ndarray", points: "np.ndarray"
    ) -> "np.ndarray | None":
        """텍스트 영역을 투시 변환으로 잘라낸다."""
        points = points.astype(np.float32)
        rect = np.zeros((4, 2), dtype=np.float32)
        s = points.sum(axis=1)
        rect[0] = points[np.argmin(s)]
        rect[2] = points[np.argmax(s)]
        diff = np.diff(points, axis=1).flatten()
        rect[1] = points[np.argmin(diff)]
        rect[3] = points[np.argmax(diff)]

        width = int(
            max(
                np.linalg.norm(rect[0] - rect[1]),
                np.linalg.norm(rect[2] - rect[3]),
            )
        )
        height = int(
            max(
                np.linalg.norm(rect[0] - rect[3]),
                np.linalg.norm(rect[1] - rect[2]),
            )
        )
        if width < 2 or height < 2:
            return None

        dst_pts = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )
        M = cv2.getPerspectiveTransform(rect, dst_pts)
        warped = cv2.warpPerspective(
            image, M, (width, height), borderMode=cv2.BORDER_REPLICATE
        )
        if height > width * 1.5:
            warped = np.rot90(warped, -1).copy()
        return warped

    def _preprocess_rec(
        self, image: "np.ndarray", rec_h: int = 48, max_wh_ratio: float = 25.0
    ) -> "np.ndarray":
        """Recognition 모델 입력 전처리."""
        h, w = image.shape[:2]
        ratio = w / max(float(h), 1.0)
        resized_w = int(rec_h * ratio)
        resized_w = max(min(resized_w, int(rec_h * max_wh_ratio)), 32)
        resized = cv2.resize(image, (resized_w, rec_h))
        norm_img = (resized.astype(np.float32) / 255.0 - 0.5) / 0.5
        norm_img = norm_img.transpose((2, 0, 1))[np.newaxis, ...]
        return norm_img.astype(np.float32)

    def _ctc_decode(self, preds: "np.ndarray") -> tuple[str, float]:
        """CTC greedy 디코딩."""
        indices = np.argmax(preds, axis=2)[0]
        probs = np.max(preds, axis=2)[0]
        text_chars: list[str] = []
        scores: list[float] = []

        for i, idx in enumerate(indices):
            if idx != 0 and (i == 0 or idx != indices[i - 1]):
                if idx < len(self._character_list):
                    text_chars.append(self._character_list[idx])
                    scores.append(float(probs[i]))

        mean_score = float(np.mean(scores)) if scores else 0.0
        return "".join(text_chars), mean_score

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_result(engine: str, reason: str = "") -> PaddleResult:
        """빈 결과를 반환한다."""
        if reason:
            logger.debug("PaddleOCR 빈 결과: %s", reason)
        return PaddleResult(
            text="",
            normalized_text="",
            confidence=0.0,
            boxes=[],
            engine=engine,
            elapsed_ms=0.0,
        )

    def status(self) -> dict[str, Any]:
        """엔진 상태 정보를 반환한다."""
        return {
            "loaded": self._loaded,
            "backend": self._backend,
            "model_dir": str(self.model_dir),
            "models_exist": self._models_exist(),
            "has_rapidocr": _HAS_RAPIDOCR,
            "has_rapidocr_v3": _HAS_RAPIDOCR_V3,
            "rapidocr_language": self._rapidocr_language,
            "has_onnxruntime": _HAS_ONNXRUNTIME,
            "has_cv2": _HAS_CV2,
            "has_numpy": _HAS_NUMPY,
            "providers": (
                _detect_providers() if _HAS_ONNXRUNTIME else []
            ),
            "dict_size": len(self._character_list),
        }
