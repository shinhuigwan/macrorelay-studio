"""Persistent OpenCV image-search engine for MacroRelay.

The legacy helper starts a fresh Python process for every image-search step.
This local-only TCP service keeps OpenCV, NumPy, MSS and prepared templates in
memory, then automatically exits after an idle period.  Requests and responses
are UTF-8 JSON and the server binds only to 127.0.0.1.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import socketserver
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
PID_FILE = RUNTIME / "vision_engine.pid"
LOG_FILE = RUNTIME / "vision_engine.log"
PORT = 9235

python_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
for candidate in (
    ROOT / "runtime" / "opencv" / python_tag / "packages",
    ROOT / "runtime_packages" / python_tag,
    ROOT / "runtime_packages",
):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import opencv_search as search  # noqa: E402


logger = logging.getLogger("vision_engine")


def setup_logging(enabled: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if enabled:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def send_request(port: int, payload: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
        client.sendall(data)
        client.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    parsed = json.loads(b"".join(chunks).decode("utf-8", errors="replace") or "{}")
    return parsed if isinstance(parsed, dict) else {"ok": False, "error": "invalid_response"}


class VisionState:
    def __init__(self, cache_limit: int = 48) -> None:
        self.cache_limit = max(4, int(cache_limit))
        self.cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self.last_hits: dict[str, tuple[int, int, int, int]] = {}
        self.started = time.time()
        self.last_activity = self.started
        self.request_count = 0
        self._lock = threading.Lock()
        self._cv2 = None
        self._np = None
        self._grabber = None

    def _modules(self):
        if self._cv2 is None or self._np is None:
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            required = ("imdecode", "matchTemplate", "cvtColor", "minMaxLoc")
            missing = [name for name in required if not hasattr(cv2, name)]
            if missing:
                raise ImportError("incomplete cv2 module; missing " + "/".join(missing))
            self._cv2, self._np = cv2, np
        if self._grabber is None:
            try:
                import mss  # type: ignore

                self._grabber = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
            except Exception:
                self._grabber = False
        return self._cv2, self._np

    def _template(self, image_path: str, profile: str) -> tuple[dict[str, Any], bool]:
        cv2, np = self._modules()
        path = Path(image_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"search image missing: {path}")
        stat = path.stat()
        key = (str(path).casefold(), stat.st_mtime_ns, stat.st_size, profile)
        cached = self.cache.get(key)
        if cached is not None:
            self.cache.move_to_end(key)
            return cached, True

        template = search.read_image_unicode(str(path), cv2, np)
        if template is None:
            raise ValueError(f"search image decode failed: {path}")
        mask = None
        if template.ndim == 3 and template.shape[2] == 4:
            alpha = template[:, :, 3]
            if int(np.count_nonzero(alpha > 8)):
                mask = np.where(alpha > 8, 255, 0).astype(np.uint8)
            template = template[:, :, :3]
        elif template.ndim == 2:
            template = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)
        template, mask, crop_origin, canvas_size = search.trim_transparent_template(template, mask, np)
        prepared = {
            "path": str(path),
            "template": template,
            "mask": mask,
            "crop_origin": crop_origin,
            "canvas_size": canvas_size,
            "precise_cache": {},
            "standard_cache": {},
        }
        self.cache[key] = prepared
        while len(self.cache) > self.cache_limit:
            self.cache.popitem(last=False)
        return prepared, False

    def _match(self, frame, prepared: dict[str, Any], threshold: float, profile: str):
        cv2, np = self._modules()
        if profile == "precise":
            probe, probe_score = search.adaptive_standard_match(
                frame,
                prepared["template"],
                prepared["mask"],
                threshold,
                "fast",
                cv2,
                np,
                prepared["standard_cache"],
                crop_origin=prepared["crop_origin"],
                canvas_size=prepared["canvas_size"],
                fallback_full=False,
            )
            if probe is not None:
                return probe, probe_score
            match, score = search.adaptive_precise_match(
                frame,
                prepared["template"],
                prepared["mask"],
                threshold,
                cv2,
                np,
                prepared["precise_cache"],
                crop_origin=prepared["crop_origin"],
                canvas_size=prepared["canvas_size"],
            )
            return match, max(float(probe_score), float(score))
        return search.adaptive_standard_match(
            frame,
            prepared["template"],
            prepared["mask"],
            threshold,
            profile,
            cv2,
            np,
            prepared["standard_cache"],
            crop_origin=prepared["crop_origin"],
            canvas_size=prepared["canvas_size"],
        )

    @staticmethod
    def _regions(raw_regions: Any) -> list[tuple[int, int, int, int]]:
        regions: list[tuple[int, int, int, int]] = []
        for raw in raw_regions if isinstance(raw_regions, list) else []:
            if not isinstance(raw, (list, tuple)) or len(raw) < 4:
                continue
            left, top, right, bottom = (int(float(value)) for value in raw[:4])
            if right > left and bottom > top:
                regions.append((left, top, right, bottom))
        if not regions:
            raise ValueError("at least one valid search region is required")
        return regions

    def _search_region(
        self,
        region: tuple[int, int, int, int],
        prepared: dict[str, Any],
        threshold: float,
        profile: str,
    ):
        left, top, right, bottom = region
        frame = search.capture_region(left, top, right, bottom, self._grabber or None)
        if frame is None:
            return None, 0.0
        match, score = self._match(frame, prepared, threshold, profile)
        if match is None:
            return None, float(score)
        confidence, location, width, height = match
        return (
            float(confidence),
            left + int(location[0]) + int(width) // 2,
            top + int(location[1]) + int(height) // 2,
            int(width),
            int(height),
        ), float(score)

    def _search_multi(
        self,
        image_paths: list[str],
        regions: list[tuple[int, int, int, int]],
        threshold: float,
        profile: str,
        timeout_ms: int,
        poll_ms: int,
        started: float,
    ) -> dict[str, Any]:
        prepared_items: list[tuple[dict[str, Any], bool]] = [
            self._template(path, profile) for path in image_paths
        ]
        self._modules()
        deadline = time.perf_counter() + timeout_ms / 1000.0
        best_score = 0.0
        while True:
            cycle_started = time.perf_counter()
            hits: list[tuple[float, int, int, int, int, int, dict[str, Any]]] = []
            for left, top, right, bottom in regions:
                # Capture each region once, then compare every registered
                # template against that immutable frame.
                frame = search.capture_region(left, top, right, bottom, self._grabber or None)
                if frame is None:
                    continue
                for index, (prepared, _cache_hit) in enumerate(prepared_items):
                    match, score = self._match(frame, prepared, threshold, profile)
                    best_score = max(best_score, float(score))
                    if match is None:
                        continue
                    confidence, location, width, height = match
                    hits.append(
                        (
                            float(confidence),
                            index,
                            left + int(location[0]) + int(width) // 2,
                            top + int(location[1]) + int(height) // 2,
                            int(width),
                            int(height),
                            prepared,
                        )
                    )
            if hits:
                # Highest confidence wins. Stable index ordering supplies the
                # user's checklist priority when scores are equal.
                confidence, index, center_x, center_y, width, height, prepared = max(
                    hits, key=lambda item: (item[0], -item[1])
                )
                image_key = str(prepared["path"]).casefold()
                self.last_hits[image_key] = (center_x, center_y, width, height)
                canvas_width, canvas_height = prepared.get("canvas_size") or (width, height)
                return {
                    "ok": True,
                    "found": True,
                    "x": center_x,
                    "y": center_y,
                    "confidence": round(confidence, 6),
                    "best_score": round(max(best_score, confidence), 6),
                    "width": width,
                    "height": height,
                    "source_width": int(canvas_width),
                    "source_height": int(canvas_height),
                    "match_index": index + 1,
                    "matched_image": str(prepared["path"]),
                    "image_count": len(prepared_items),
                    "profile": profile,
                    "cache_hit": all(hit for _prepared, hit in prepared_items),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            if timeout_ms <= 0 or time.perf_counter() >= deadline:
                break
            remaining = max(0.0, poll_ms / 1000.0 - (time.perf_counter() - cycle_started))
            if remaining:
                time.sleep(remaining)
        return {
            "ok": True,
            "found": False,
            "best_score": round(best_score, 6),
            "image_count": len(prepared_items),
            "profile": profile,
            "cache_hit": all(hit for _prepared, hit in prepared_items),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        with self._lock:
            self.last_activity = time.time()
            self.request_count += 1
            image_path = str(request.get("image") or "")
            profile = str(request.get("profile") or "balanced").lower()
            if profile not in {"fast", "balanced", "precise"}:
                profile = "balanced"
            threshold = max(0.5, min(0.99, float(request.get("threshold") or 0.86)))
            timeout_ms = max(0, int(request.get("timeout") or 0))
            poll_ms = max(10, int(request.get("poll") or 60))
            regions = self._regions(request.get("regions"))
            raw_images = request.get("images")
            image_paths = list(
                dict.fromkeys(
                    str(value)
                    for value in raw_images if str(value).strip()
                )
            ) if isinstance(raw_images, list) else []
            if len(image_paths) > 1:
                return self._search_multi(
                    image_paths,
                    regions,
                    threshold,
                    profile,
                    timeout_ms,
                    poll_ms,
                    started,
                )
            prepared, cache_hit = self._template(image_path, profile)
            self._modules()

            deadline = time.perf_counter() + timeout_ms / 1000.0
            best_score = 0.0
            image_key = str(prepared["path"]).casefold()
            last_hit = self.last_hits.get(image_key)
            while True:
                cycle_started = time.perf_counter()
                candidates = list(regions)
                if last_hit:
                    center_x, center_y, width, height = last_hit
                    for left, top, right, bottom in regions:
                        if left <= center_x < right and top <= center_y < bottom:
                            padding_x = max(80, width * 2)
                            padding_y = max(80, height * 2)
                            recent = (
                                max(left, center_x - padding_x),
                                max(top, center_y - padding_y),
                                min(right, center_x + padding_x),
                                min(bottom, center_y + padding_y),
                            )
                            if recent[2] > recent[0] and recent[3] > recent[1]:
                                candidates.insert(0, recent)
                            break
                seen: set[tuple[int, int, int, int]] = set()
                for region in candidates:
                    if region in seen:
                        continue
                    seen.add(region)
                    match, score = self._search_region(region, prepared, threshold, profile)
                    best_score = max(best_score, score)
                    if match is not None:
                        confidence, center_x, center_y, width, height = match
                        self.last_hits[image_key] = (center_x, center_y, width, height)
                        return {
                            "ok": True,
                            "found": True,
                            "x": center_x,
                            "y": center_y,
                            "confidence": round(confidence, 6),
                            "best_score": round(max(best_score, confidence), 6),
                            "width": width,
                            "height": height,
                            "profile": profile,
                            "cache_hit": cache_hit,
                            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        }
                if timeout_ms <= 0 or time.perf_counter() >= deadline:
                    break
                remaining = max(0.0, poll_ms / 1000.0 - (time.perf_counter() - cycle_started))
                if remaining:
                    time.sleep(remaining)
            return {
                "ok": True,
                "found": False,
                "best_score": round(best_score, 6),
                "profile": profile,
                "cache_hit": cache_hit,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "running": True,
            "uptime_seconds": round(time.time() - self.started, 1),
            "idle_seconds": round(time.time() - self.last_activity, 1),
            "request_count": self.request_count,
            "template_cache_count": len(self.cache),
        }

    def close(self) -> None:
        if self._grabber:
            try:
                self._grabber.close()
            except Exception:
                pass
        self._grabber = None


class VisionHandler(socketserver.StreamRequestHandler):
    server: "VisionServer"

    def handle(self) -> None:
        try:
            raw = bytearray()
            while len(raw) <= 2_000_000:
                chunk = self.request.recv(65536)
                if not chunk:
                    break
                raw.extend(chunk)
                if b"\n" in chunk:
                    break
            payload = bytes(raw).partition(b"\n")[0]
            request = json.loads(payload.decode("utf-8", errors="replace") or "{}")
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            command = str(request.get("cmd") or "search").lower()
            if command == "ping" or command == "status":
                response = self.server.state.status()
            elif command == "search":
                response = self.server.state.search(request)
            elif command == "shutdown":
                response = {"ok": True, "status": "shutting_down"}
                self.server.should_stop = True
            else:
                response = {"ok": False, "error": "unknown_command", "detail": command}
        except Exception as exc:
            logger.exception("vision request failed")
            response = {
                "ok": False,
                "error": type(exc).__name__.upper(),
                "detail": str(exc)[:500],
            }
        self.request.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8"))


class VisionServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, port: int, cache_limit: int = 48) -> None:
        self.should_stop = False
        self.state = VisionState(cache_limit)
        super().__init__(("127.0.0.1", port), VisionHandler)

    def server_activate(self) -> None:
        super().server_activate()
        RUNTIME.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding="ascii")
        logger.info("Vision Engine started on 127.0.0.1:%d", self.server_address[1])

    def server_close(self) -> None:
        self.state.close()
        try:
            if PID_FILE.is_file() and PID_FILE.read_text(encoding="ascii").strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass
        super().server_close()


def server_available(port: int) -> bool:
    try:
        return bool(send_request(port, {"cmd": "ping"}, 0.4).get("ok"))
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="MacroRelay persistent OpenCV Vision Engine")
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--idle-timeout", type=int, default=600)
    parser.add_argument("--cache-limit", type=int, default=48)
    parser.add_argument("--log-file", action="store_true")
    parser.add_argument("--request", default="", help="single JSON request for diagnostics")
    args = parser.parse_args()
    setup_logging(args.log_file)

    if args.request:
        response = send_request(args.port, json.loads(args.request))
        print(json.dumps(response, ensure_ascii=False))
        return 0 if response.get("ok") else 2
    if not args.server:
        parser.error("--server or --request is required")
    if server_available(args.port):
        return 0

    server = VisionServer(args.port, args.cache_limit)

    def stop(_signum: int, _frame: Any) -> None:
        server.should_stop = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        server.timeout = 1.0
        while not server.should_stop and (
            args.idle_timeout <= 0 or time.time() - server.state.last_activity < args.idle_timeout
        ):
            server.handle_request()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
