"""MacroRelay OCR Engine — 상시 백그라운드 TCP 서버.

브라우저 액션 서버(browser_action.py)와 동일한 TCP 소켓 패턴을 따른다.
매크로 실행 시 한 번 시작되어 OCR 요청을 처리하고,
매크로 종료 시 또는 유휴 타임아웃 후 자동 종료된다.

실행 방법::

    python ocr_engine.py --server --port 9234

JSON 요청/응답 프로토콜로 통신한다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import socketserver
import sys
import threading
import time
from pathlib import Path
from typing import Any

__all__ = ["OcrEngineServer", "main"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).resolve().parent / "runtime"
LOG_FILE = LOG_DIR / "ocr_engine.log"
PID_FILE = LOG_DIR / "ocr_engine.pid"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("ocr_engine")

# ---------------------------------------------------------------------------
# Sys.path Bootstrap for OpenCV and Runtime Packages
# ---------------------------------------------------------------------------
_base_dir = Path(__file__).resolve().parent
_python_ver = f"cp{sys.version_info.major}{sys.version_info.minor}"
_pkg_candidates = [
    _base_dir / "runtime" / "opencv" / _python_ver / "packages",
]
for _candidate in reversed(_pkg_candidates):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

for _rp in (_base_dir / "runtime_packages" / _python_ver, _base_dir / "runtime_packages"):
    if _rp.exists() and str(_rp) not in sys.path:
        sys.path.append(str(_rp))


def _setup_file_logging() -> None:
    """파일 로깅을 설정한다."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger.addHandler(handler)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lazy imports for OCR modules
# ---------------------------------------------------------------------------

_capture_mod = None
_preprocess_mod = None
_paddle_mod = None
_tesseract_mod = None
_postprocess_mod = None


def _import_modules() -> None:
    """OCR 모듈을 지연 임포트한다."""
    global _capture_mod, _preprocess_mod, _paddle_mod, _tesseract_mod, _postprocess_mod
    if _capture_mod is not None:
        return
    try:
        import ocr_capture as _c
        _capture_mod = _c
    except ImportError:
        logger.warning("ocr_capture 모듈을 찾을 수 없습니다.")
    try:
        import ocr_preprocess as _p
        _preprocess_mod = _p
    except ImportError:
        logger.warning("ocr_preprocess 모듈을 찾을 수 없습니다.")
    try:
        import ocr_paddle as _pd
        _paddle_mod = _pd
    except ImportError:
        logger.warning("ocr_paddle 모듈을 찾을 수 없습니다.")
    try:
        import ocr_tesseract as _t
        _tesseract_mod = _t
    except ImportError:
        logger.warning("ocr_tesseract 모듈을 찾을 수 없습니다.")
    try:
        import ocr_postprocess as _pp
        _postprocess_mod = _pp
    except ImportError:
        logger.warning("ocr_postprocess 모듈을 찾을 수 없습니다.")


# ---------------------------------------------------------------------------
# Engine State
# ---------------------------------------------------------------------------


class EngineState:
    """OCR 엔진 전역 상태."""

    def __init__(self, model_dir: str = "", idle_timeout: int = 300) -> None:
        self.model_dir = model_dir
        self.idle_timeout = idle_timeout  # 초 단위 (기본 5분)
        self._paddle_engine: Any | None = None
        self._last_activity = time.time()
        self._lock = threading.Lock()
        self._unload_timer: threading.Timer | None = None
        self._request_count = 0
        self._start_time = time.time()

    def touch(self) -> None:
        """활동 타임스탬프를 갱신한다."""
        self._last_activity = time.time()
        self._request_count += 1
        self._reset_unload_timer()

    def _reset_unload_timer(self) -> None:
        """모델 자동 해제 타이머를 재설정한다."""
        if self._unload_timer:
            self._unload_timer.cancel()
        if self.idle_timeout > 0:
            self._unload_timer = threading.Timer(
                self.idle_timeout, self._auto_unload
            )
            self._unload_timer.daemon = True
            self._unload_timer.start()

    def _auto_unload(self) -> None:
        """유휴 타임아웃 시 모델을 자동 해제한다."""
        elapsed = time.time() - self._last_activity
        if elapsed >= self.idle_timeout:
            logger.info(
                "유휴 %.0f초 경과, 모델 자동 해제", elapsed
            )
            self.unload_models()

    def get_paddle_engine(self) -> Any:
        """PaddleOCR 엔진을 반환한다 (필요 시 로딩)."""
        with self._lock:
            if self._paddle_engine is None:
                _import_modules()
                if _paddle_mod and _paddle_mod.is_available():
                    model_dir = self.model_dir or None
                    self._paddle_engine = _paddle_mod.PaddleEngine(
                        model_dir=model_dir
                    )
                    self._paddle_engine.load_models()
                    logger.info("PaddleOCR 엔진 로딩 완료")
            return self._paddle_engine

    def unload_models(self) -> None:
        """모든 모델을 메모리에서 해제한다."""
        with self._lock:
            if self._paddle_engine:
                try:
                    self._paddle_engine.unload_models()
                except Exception as exc:
                    logger.warning("PaddleOCR 해제 오류: %s", exc)
                self._paddle_engine = None
            logger.info("모델 해제 완료")

    def status(self) -> dict[str, Any]:
        """엔진 상태 정보를 반환한다."""
        paddle_status: dict[str, Any] = {}
        if self._paddle_engine:
            try:
                paddle_status = self._paddle_engine.status()
            except Exception:
                paddle_status = {"loaded": True}

        _import_modules()
        tesseract_available = False
        if _tesseract_mod:
            try:
                tesseract_available = _tesseract_mod.is_available()
            except Exception:
                pass

        return {
            "running": True,
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "request_count": self._request_count,
            "idle_seconds": round(time.time() - self._last_activity, 1),
            "idle_timeout": self.idle_timeout,
            "paddle": paddle_status,
            "tesseract_available": tesseract_available,
            "model_dir": self.model_dir,
        }


# ---------------------------------------------------------------------------
# Request Handlers
# ---------------------------------------------------------------------------


def handle_ping(state: EngineState, _req: dict) -> dict:
    """서버 상태 확인."""
    state.touch()
    return {"ok": True, "status": "running", **state.status()}


def handle_shutdown(state: EngineState, _req: dict) -> dict:
    """서버 정상 종료."""
    state.unload_models()
    # 서버 종료는 호출자에서 처리
    return {"ok": True, "status": "shutting_down"}


def handle_unload(state: EngineState, _req: dict) -> dict:
    """모델 메모리 해제."""
    state.unload_models()
    state.touch()
    return {"ok": True, "status": "models_unloaded"}


def handle_status(state: EngineState, _req: dict) -> dict:
    """상세 상태 반환."""
    state.touch()
    return {"ok": True, **state.status()}


def handle_ocr(state: EngineState, req: dict) -> dict:
    """OCR 인식 실행."""
    state.touch()
    _import_modules()

    start_time = time.perf_counter()

    # 요청 파라미터 파싱
    region = req.get("region", [0, 0, 0, 0])
    capture_mode = req.get("capture_mode", "screen")
    window_title = req.get("window_title", "")
    window_hwnd = int(req.get("window_hwnd", 0) or 0)
    coord_base = req.get("coord_base", "screen")
    lang = req.get("lang", "eng+kor")
    profile = req.get("profile", "auto")
    expect_text = req.get("expect_text", "")
    regex = req.get("regex", "")
    whitelist = req.get("whitelist", "")
    find_text = req.get("find_text", "")
    match_mode = req.get("match_mode", "contains")
    engine_preference = req.get("engine_preference", "auto")
    debug = req.get("debug", False)
    debug_dir = req.get("debug_dir", "")
    ocr_action = req.get("ocr_action", "extract")
    number_condition = req.get("number_condition", "")
    number_value = float(req.get("number_value", 0) or 0)
    value_regex = str(req.get("value_regex", "") or "")
    value_group = max(0, int(req.get("value_group", 1) or 0))
    minimum_confidence = max(0.0, min(float(req.get("minimum_confidence", 0) or 0), 1.0))
    position_priority = str(req.get("position_priority", "top_left") or "top_left")

    is_number = ocr_action in ("extract_number", "number_condition")

    try:
        # 1) 화면 캡처
        if _capture_mod is None:
            return _error_response("ocr_capture 모듈을 사용할 수 없습니다.")

        image, capture_meta = _capture_mod.region_to_image(
            region=list(region),
            capture_mode=capture_mode,
            window_title=window_title,
            window_hwnd=window_hwnd,
        )

        if image is None or image.size == 0:
            return _error_response("화면 캡처에 실패했습니다.")

        # 2) 전처리
        preprocess_results = []
        if _preprocess_mod:
            try:
                preprocess_results = _preprocess_mod.preprocess(
                    image,
                    profile=profile,
                    debug_dir=debug_dir if debug else "",
                )
            except Exception as prep_exc:
                logger.warning("전처리 파이프라인 오류, fallback 사용: %s", prep_exc)
                preprocess_results = []

        # 전처리 결과가 없으면 원본 사용
        if not preprocess_results:
            try:
                if len(image.shape) == 3:
                    try:
                        import cv2
                        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    except Exception:
                        gray = image.mean(axis=2).astype(image.dtype)
                else:
                    gray = image

                from dataclasses import dataclass

                @dataclass
                class _FallbackResult:
                    image: Any
                    name: str = "original"
                    description: str = "원본 이미지"
                    scale: float = 1.0
                    is_inverted: bool = False
                    is_grayscale: bool = True

                preprocess_results = [_FallbackResult(image=gray)]
            except Exception as fallback_exc:
                logger.error("전처리 fallback 실패: %s", fallback_exc)
                return _error_response(f"전처리 실패: {fallback_exc}")

        # 3) OCR 엔진 실행
        candidates = []
        paddle_satisfied = False

        # PaddleOCR (주 엔진)
        paddle_requested = engine_preference in ("auto", "paddle")

        if paddle_requested:
            paddle_engine = state.get_paddle_engine()
            language_supported = bool(
                paddle_engine
                and (
                    engine_preference == "paddle"
                    or not hasattr(paddle_engine, "supports_language")
                    or paddle_engine.supports_language(lang)
                )
            )
            if paddle_engine and paddle_engine.is_loaded and language_supported:
                try:
                    # PaddleOCR는 원본 BGR 이미지를 사용
                    paddle_result = paddle_engine.recognize(image, lang=lang)
                    if _postprocess_mod:
                        candidates.append(
                            _postprocess_mod.OcrCandidate(
                                text=paddle_result.text,
                                normalized_text=paddle_result.normalized_text,
                                confidence=paddle_result.confidence,
                                boxes=[
                                    _postprocess_mod.OcrBox(
                                        text=b.text,
                                        confidence=b.confidence,
                                        rect=b.rect,
                                        center=b.center,
                                        engine=paddle_result.engine,
                                    )
                                    for b in paddle_result.boxes
                                ],
                                engine=paddle_result.engine,
                                elapsed_ms=paddle_result.elapsed_ms,
                                profile=profile,
                            )
                        )
                        paddle_text = paddle_result.normalized_text or paddle_result.text
                        paddle_satisfied = bool(paddle_text.strip()) and paddle_result.confidence >= max(minimum_confidence, 0.84)
                        if paddle_satisfied and find_text and _postprocess_mod:
                            paddle_satisfied = _postprocess_mod.match_text(paddle_text, find_text, match_mode)
                        if paddle_satisfied and expect_text:
                            paddle_satisfied = expect_text.casefold() in paddle_text.casefold()
                        if paddle_satisfied and regex:
                            try:
                                paddle_satisfied = bool(re.search(regex, paddle_text))
                            except re.error:
                                paddle_satisfied = False
                        # 최고 정확도는 신뢰도와 무관하게 두 엔진을 교차 판정한다.
                        if profile == "precise":
                            paddle_satisfied = False
                except Exception as exc:
                    logger.warning("PaddleOCR 인식 오류: %s", exc)
            elif paddle_engine and paddle_engine.is_loaded and engine_preference == "auto":
                logger.info("요청 언어(%s)는 현재 Paddle 모델과 맞지 않아 Tesseract를 사용합니다.", lang)

        # Tesseract (보조 엔진)
        if engine_preference == "tesseract" or (engine_preference == "auto" and not paddle_satisfied):
            if _tesseract_mod and _tesseract_mod.is_available():
                try:
                    tess_result = _tesseract_mod.recognize_candidates(
                        preprocess_results,
                        lang=lang,
                        whitelist=whitelist,
                        expect_text=expect_text,
                        tessdata_variant="best" if profile == "precise" else "fast",
                    )
                    if _postprocess_mod:
                        candidates.append(
                            _postprocess_mod.OcrCandidate(
                                text=tess_result.text,
                                normalized_text=tess_result.normalized_text,
                                confidence=tess_result.confidence,
                                boxes=[
                                    _postprocess_mod.OcrBox(
                                        text=b.text,
                                        confidence=b.confidence,
                                        rect=b.rect,
                                        center=b.center,
                                        engine="tesseract",
                                    )
                                    for b in tess_result.boxes
                                ],
                                engine="tesseract",
                                elapsed_ms=tess_result.elapsed_ms,
                                profile=profile,
                            )
                        )
                except Exception as exc:
                    logger.warning("Tesseract 인식 오류: %s", exc)

        # 4) 결과 병합 및 보정
        if _postprocess_mod and candidates:
            final = _postprocess_mod.merge_results(
                candidates=candidates,
                expect_text=expect_text,
                regex=regex,
                whitelist=whitelist,
                find_text=find_text,
                match_mode=match_mode,
                is_number=is_number,
                number_condition=number_condition,
                number_value=number_value,
                minimum_confidence=minimum_confidence,
                position_priority=position_priority,
                lang=lang,
            )
        elif candidates:
            # postprocess 모듈 없으면 첫 번째 결과 사용
            best = candidates[0]
            final_boxes = [
                {
                    "text": b.text,
                    "confidence": b.confidence,
                    "rect": list(b.rect),
                    "center": list(b.center),
                }
                for b in best.boxes
            ]
            elapsed = (time.perf_counter() - start_time) * 1000
            return {
                "success": bool(best.text.strip()),
                "text": best.text,
                "normalized_text": best.normalized_text,
                "confidence": best.confidence,
                "boxes": final_boxes,
                "engine": best.engine,
                "profile": profile,
                "elapsed_ms": round(elapsed, 2),
            }
        else:
            elapsed = (time.perf_counter() - start_time) * 1000
            return _error_response(
                "OCR 엔진을 사용할 수 없습니다.", elapsed_ms=elapsed
            )

        # 5) 응답 생성
        elapsed = (time.perf_counter() - start_time) * 1000

        actual_region = capture_meta.get("actual_region") or [0, 0, 0, 0]
        origin_x = int(actual_region[0]) if len(actual_region) >= 2 else 0
        origin_y = int(actual_region[1]) if len(actual_region) >= 2 else 0

        def absolute_box(box: Any) -> dict[str, Any]:
            x1, y1, x2, y2 = (int(value) for value in box.rect)
            cx, cy = (int(value) for value in box.center)
            return {
                "text": box.text,
                "confidence": box.confidence,
                "rect": [x1 + origin_x, y1 + origin_y, x2 + origin_x, y2 + origin_y],
                "center": [cx + origin_x, cy + origin_y],
                "local_rect": [x1, y1, x2, y2],
                "local_center": [cx, cy],
            }

        response: dict[str, Any] = {
            "success": final.success,
            "text": final.text,
            "normalized_text": final.normalized_text,
            "confidence": final.confidence,
            "boxes": [absolute_box(b) for b in final.boxes],
            "engine": final.engine,
            "profile": final.profile,
            "elapsed_ms": round(elapsed, 2),
        }

        if value_regex:
            extracted_value = _postprocess_mod.extract_regex_value(
                final.normalized_text or final.text,
                value_regex,
                value_group,
            ) if _postprocess_mod else None
            response["extract_matched"] = extracted_value is not None
            if extracted_value is None:
                response["success"] = False
            else:
                response["extracted_value"] = extracted_value
                if is_number and _postprocess_mod:
                    extracted_number = _postprocess_mod.extract_number(extracted_value)
                    if extracted_number is None:
                        response["success"] = False
                    else:
                        final.extracted_number = extracted_number

        if final.match_found:
            response["match_found"] = True
            response["match_text"] = final.match_text
            if final.match_box:
                response["match_box"] = absolute_box(final.match_box)

        if final.extracted_number is not None:
            response["extracted_number"] = final.extracted_number

        if debug:
            response["candidates"] = [
                {
                    "engine": c.engine,
                    "text": c.text,
                    "confidence": c.confidence,
                    "elapsed_ms": c.elapsed_ms,
                }
                for c in final.candidates
            ]
            response["capture_meta"] = capture_meta

        return response

    except Exception as exc:
        elapsed = (time.perf_counter() - start_time) * 1000
        logger.error("OCR 처리 오류: %s", exc, exc_info=True)
        return _error_response(str(exc), elapsed_ms=elapsed)


def _error_response(message: str, elapsed_ms: float = 0.0) -> dict:
    """에러 응답을 생성한다."""
    return {
        "success": False,
        "text": "",
        "normalized_text": "",
        "confidence": 0.0,
        "boxes": [],
        "engine": "error",
        "profile": "",
        "elapsed_ms": round(elapsed_ms, 2),
        "error": message,
    }


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

COMMANDS: dict[str, Any] = {
    "ping": handle_ping,
    "shutdown": handle_shutdown,
    "unload_models": handle_unload,
    "status": handle_status,
    "ocr": handle_ocr,
}


# ---------------------------------------------------------------------------
# TCP Socket Server (browser_action.py 패턴)
# ---------------------------------------------------------------------------


class OcrRequestHandler(socketserver.StreamRequestHandler):
    """개별 TCP 요청 처리기."""

    server: "OcrEngineServer"

    def handle(self) -> None:
        try:
            raw = b""
            while True:
                chunk = self.request.recv(16384)
                if not chunk:
                    break
                raw += chunk
                # shutdown 시그널 감지 (half-close)
                if len(chunk) < 16384:
                    break

            if not raw:
                return

            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                return

            try:
                req = json.loads(text)
            except json.JSONDecodeError as exc:
                resp = {"ok": False, "error": f"JSON 파싱 오류: {exc}"}
                self._send_response(resp)
                return

            cmd = req.get("cmd", "").lower()
            handler = COMMANDS.get(cmd)

            if handler is None:
                resp = {"ok": False, "error": f"unknown command: {cmd}"}
                self._send_response(resp)
                return

            resp = handler(self.server.engine_state, req)

            if cmd == "shutdown":
                resp["ok"] = True
                self._send_response(resp)
                # 별도 스레드에서 서버 종료
                threading.Thread(
                    target=self.server.shutdown, daemon=True
                ).start()
                return

            self._send_response(resp)

        except Exception as exc:
            logger.error("요청 처리 오류: %s", exc, exc_info=True)
            try:
                resp = {"ok": False, "error": str(exc)}
                self._send_response(resp)
            except Exception:
                pass

    def _send_response(self, resp: dict) -> None:
        """JSON 응답을 전송한다."""
        data = json.dumps(resp, ensure_ascii=False)
        self.request.sendall(data.encode("utf-8"))


class OcrEngineServer(socketserver.TCPServer):
    """OCR 엔진 TCP 서버."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        port: int = 9234,
        model_dir: str = "",
        idle_timeout: int = 300,
    ) -> None:
        self._port = port
        self.engine_state = EngineState(
            model_dir=model_dir, idle_timeout=idle_timeout
        )
        super().__init__(("127.0.0.1", port), OcrRequestHandler)

    def server_activate(self) -> None:
        super().server_activate()
        logger.info("OCR 엔진 서버 시작: 127.0.0.1:%d", self._port)
        self._write_pid()

    def server_close(self) -> None:
        logger.info("OCR 엔진 서버 종료")
        self.engine_state.unload_models()
        self._remove_pid()
        super().server_close()

    def _write_pid(self) -> None:
        """PID 파일을 기록한다."""
        try:
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

    def _remove_pid(self) -> None:
        """PID 파일을 삭제한다."""
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI 엔트리포인트."""
    parser = argparse.ArgumentParser(description="MacroRelay OCR Engine")
    parser.add_argument(
        "--server", action="store_true", help="TCP 서버 모드로 실행"
    )
    parser.add_argument(
        "--port", type=int, default=9234, help="서버 포트 (기본: 9234)"
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=0,
        help="서버 포트 (--port 대신 사용, browser_action.py 호환)",
    )
    parser.add_argument(
        "--model-dir", default="", help="OCR 모델 디렉토리 경로"
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=300,
        help="유휴 시 모델 자동 해제 시간(초), 0=해제 안 함 (기본: 300)",
    )
    parser.add_argument(
        "--log-file", action="store_true", help="파일 로깅 활성화"
    )
    args = parser.parse_args()

    if args.log_file:
        _setup_file_logging()

    port = args.server_port if args.server_port > 0 else args.port

    if args.server:
        # 기존 서버 종료 시도
        _try_shutdown_existing(port)

        server = OcrEngineServer(
            port=port,
            model_dir=args.model_dir,
            idle_timeout=args.idle_timeout,
        )

        # SIGINT/SIGTERM 처리
        def _signal_handler(sig: int, _frame: Any) -> None:
            logger.info("시그널 %d 수신, 서버 종료", sig)
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        try:
            server.serve_forever()
        finally:
            server.server_close()
    else:
        # 단일 요청 모드 (stdin에서 JSON 읽기)
        raw = sys.stdin.read().strip()
        if not raw:
            print(json.dumps({"ok": False, "error": "empty input"}))
            return

        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": f"JSON error: {exc}"}))
            return

        state = EngineState(model_dir=args.model_dir, idle_timeout=0)
        cmd = req.get("cmd", "ocr")
        handler = COMMANDS.get(cmd, handle_ocr)
        resp = handler(state, req)
        print(json.dumps(resp, ensure_ascii=False))
        state.unload_models()


def _try_shutdown_existing(port: int) -> None:
    """기존 서버가 있으면 종료를 시도한다."""
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(("127.0.0.1", port))
        payload = json.dumps({"cmd": "shutdown"}).encode("utf-8")
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        try:
            sock.recv(4096)
        except Exception:
            pass
        sock.close()
        time.sleep(0.3)
    except Exception:
        pass


if __name__ == "__main__":
    main()
