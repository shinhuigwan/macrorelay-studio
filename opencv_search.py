#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optimized OpenCV template search for AHK integration."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time


def enable_physical_pixel_coordinates() -> None:
    """Avoid DPI-virtualized captures and coordinates on mixed-scale monitors."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


enable_physical_pixel_coordinates()


def parse_region(text: str) -> tuple[int, int, int, int]:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError("region must be left,top,right,bottom")
    left, top, right, bottom = (int(float(p)) for p in parts)
    return left, top, right, bottom


def capture_region(left: int, top: int, right: int, bottom: int, grabber=None):
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    try:
        import mss  # type: ignore
        import numpy as np  # type: ignore

        if grabber is None:
            with mss.mss() as sct:
                img = sct.grab({"left": left, "top": top, "width": width, "height": height})
        else:
            img = grabber.grab({"left": left, "top": top, "width": width, "height": height})
        frame = np.array(img)[:, :, :3]  # BGRA -> BGR
        return frame
    except Exception:
        pass
    try:
        from PIL import ImageGrab  # type: ignore
        import numpy as np  # type: ignore

        img = ImageGrab.grab(bbox=(left, top, right, bottom))
        frame = np.array(img)[:, :, ::-1]  # RGB -> BGR
        return frame
    except Exception:
        return None


def prepare_templates(
    template,
    profile: str,
    cv2,
    mask=None,
    scales=None,
    *,
    crop_origin=(0, 0),
    canvas_size=None,
):
    if scales is None:
        if profile == "fast":
            scales = (1.0,)
        elif profile == "precise":
            scales = (1.0, 0.90, 1.10, 0.80, 1.20, 0.70, 1.30, 1.40, 1.50)
        else:
            scales = (1.0, 0.95, 1.05)
    prepared = []
    prepared_sizes = set()
    canvas_width, canvas_height = canvas_size or (template.shape[1], template.shape[0])
    for scale in scales:
        if scale == 1.0:
            scaled = template
            scaled_mask = mask
        else:
            scaled = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            scaled_mask = (
                cv2.resize(mask, (int(scaled.shape[1]), int(scaled.shape[0])), interpolation=cv2.INTER_NEAREST)
                if mask is not None
                else None
            )
        size = (int(scaled.shape[1]), int(scaled.shape[0]))
        if scaled.shape[0] >= 2 and scaled.shape[1] >= 2 and size not in prepared_sizes:
            gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
            edge = cv2.Canny(gray, 60, 150) if profile == "precise" else None
            origin_x = int(round(crop_origin[0] * scale))
            origin_y = int(round(crop_origin[1] * scale))
            full_width = max(2, int(round(canvas_width * scale)))
            full_height = max(2, int(round(canvas_height * scale)))
            prepared.append((scaled, gray, edge, scaled_mask, origin_x, origin_y, full_width, full_height))
            prepared_sizes.add(size)
    return prepared


def match_frame(
    frame,
    templates,
    threshold: float,
    profile: str,
    cv2,
    np,
    *,
    return_best: bool = False,
    stop_on_threshold: bool = True,
):
    best = None
    gray_img = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edge_img = cv2.Canny(gray_img, 60, 150) if profile in {"precise", "edge"} else None
    for prepared in templates:
        template, gray_tpl, edge_tpl, template_mask = prepared[:4]
        if len(prepared) >= 8:
            origin_x, origin_y, full_width, full_height = prepared[4:8]
        else:
            origin_x = origin_y = 0
            full_width, full_height = template.shape[1], template.shape[0]
        tpl_h, tpl_w = template.shape[:2]
        img_h, img_w = frame.shape[:2]
        if tpl_h > img_h or tpl_w > img_w:
            continue
        method = cv2.TM_CCORR_NORMED if template_mask is not None else cv2.TM_CCOEFF_NORMED
        if profile == "edge" and edge_img is not None and edge_tpl is not None:
            edge_result = cv2.matchTemplate(edge_img, edge_tpl, method, mask=template_mask)
            if np.isfinite(edge_result).any():
                score_map = edge_result
            else:
                score_map = cv2.matchTemplate(gray_img, gray_tpl, method, mask=template_mask)
        else:
            gray_result = cv2.matchTemplate(gray_img, gray_tpl, method, mask=template_mask)
            if profile == "fast":
                score_map = gray_result
            else:
                color_result = cv2.matchTemplate(frame, template, method, mask=template_mask)
                score_map = gray_result * 0.58 + color_result * 0.42
                if profile == "precise" and edge_img is not None:
                    if int(np.count_nonzero(edge_tpl)) >= 8:
                        edge_result = cv2.matchTemplate(edge_img, edge_tpl, method, mask=template_mask)
                        if np.isfinite(edge_result).any():
                            edge_result = np.nan_to_num(edge_result, nan=0.0, posinf=0.0, neginf=0.0)
                            score_map = score_map * 0.78 + edge_result * 0.22
        score_map = np.nan_to_num(score_map, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(score_map)
        full_location = (max_loc[0] - origin_x, max_loc[1] - origin_y)
        candidate = (float(max_val), full_location, full_width, full_height)
        if best is None or candidate[0] > best[0]:
            best = candidate
        # A score over the requested threshold is already a valid result.
        # Returning immediately is substantially faster for exact-scale hits.
        if stop_on_threshold and candidate[0] >= threshold:
            return candidate, candidate[0]
    if return_best and best is not None:
        return best, best[0]
    if best is None or best[0] < threshold:
        return None, best[0] if best else 0.0
    return best, best[0]


def adaptive_precise_match(
    frame,
    template,
    mask,
    threshold: float,
    cv2,
    np,
    cache=None,
    *,
    crop_origin=(0, 0),
    canvas_size=None,
):
    """Find 70-150% templates with a cheap coarse pass and a local 1% refinement."""
    cache = cache if cache is not None else {}
    cropped_h, cropped_w = template.shape[:2]
    template_w, template_h = canvas_size or (cropped_w, cropped_h)
    if min(template_w, template_h) <= 32:
        reduction = 0.75
    elif min(template_w, template_h) <= 64:
        reduction = 0.60
    else:
        reduction = 0.50
    reduced_frame = cv2.resize(frame, None, fx=reduction, fy=reduction, interpolation=cv2.INTER_AREA)
    coarse_scales = (1.00, 0.90, 1.10, 0.80, 1.20, 0.70, 1.30, 1.40, 1.50)
    coarse_key = ("coarse", reduction)
    if coarse_key not in cache:
        cache[coarse_key] = prepare_templates(
            template,
            "precise",
            cv2,
            mask,
            scales=tuple(scale * reduction for scale in coarse_scales),
            crop_origin=crop_origin,
            canvas_size=(template_w, template_h),
        )
    coarse_match, coarse_score = match_frame(
        reduced_frame,
        cache[coarse_key],
        threshold,
        "edge",
        cv2,
        np,
        return_best=True,
        stop_on_threshold=False,
    )
    if coarse_match is None:
        return None, float(coarse_score)

    _score, coarse_location, coarse_w, coarse_h = coarse_match
    coarse_scale_x = coarse_w / max(1.0, template_w * reduction)
    coarse_scale_y = coarse_h / max(1.0, template_h * reduction)
    coarse_scale = max(0.70, min(1.50, (coarse_scale_x + coarse_scale_y) / 2.0))
    # Refine scale on the reduced frame first.  Masked icons can give a high
    # coarse score one bucket away, so overlap neighbouring 10% buckets.
    fine_start = max(0.70, coarse_scale - 0.16)
    fine_end = min(1.50, coarse_scale + 0.16)
    fine_scales = tuple(
        round(fine_start + index * 0.01, 2)
        for index in range(int(round((fine_end - fine_start) / 0.01)) + 1)
    )
    reduced_fine_key = ("fine-reduced", reduction, fine_scales)
    if reduced_fine_key not in cache:
        cache[reduced_fine_key] = prepare_templates(
            template,
            "precise",
            cv2,
            mask,
            scales=tuple(scale * reduction for scale in fine_scales),
            crop_origin=crop_origin,
            canvas_size=(template_w, template_h),
        )

    reduced_center_x = coarse_location[0] + coarse_w / 2.0
    reduced_center_y = coarse_location[1] + coarse_h / 2.0
    max_reduced_scale = max(fine_scales) * reduction
    reduced_padding_x = max(24, int(template_w * max_reduced_scale * 1.20))
    reduced_padding_y = max(24, int(template_h * max_reduced_scale * 1.20))
    reduced_left = max(0, int(reduced_center_x - reduced_padding_x))
    reduced_top = max(0, int(reduced_center_y - reduced_padding_y))
    reduced_right = min(reduced_frame.shape[1], int(reduced_center_x + reduced_padding_x + 1))
    reduced_bottom = min(reduced_frame.shape[0], int(reduced_center_y + reduced_padding_y + 1))
    reduced_roi = reduced_frame[reduced_top:reduced_bottom, reduced_left:reduced_right]
    fine_match, fine_score = match_frame(
        reduced_roi,
        cache[reduced_fine_key],
        threshold,
        "edge",
        cv2,
        np,
        return_best=True,
        stop_on_threshold=False,
    )
    if fine_match is None:
        return None, max(float(coarse_score), float(fine_score))

    _fine_confidence, fine_location, fine_w, fine_h = fine_match
    fine_scale_x = fine_w / max(1.0, template_w * reduction)
    fine_scale_y = fine_h / max(1.0, template_h * reduction)
    fine_scale = max(0.70, min(1.50, (fine_scale_x + fine_scale_y) / 2.0))
    full_center_x = (reduced_left + fine_location[0] + fine_w / 2.0) / reduction
    full_center_y = (reduced_top + fine_location[1] + fine_h / 2.0) / reduction

    # Only the best scale and its two 1% neighbours need full-resolution
    # colour/edge verification.  This keeps large transparent templates fast.
    final_scales = tuple(
        sorted({max(0.70, min(1.50, round(fine_scale + delta, 2))) for delta in (-0.01, 0.0, 0.01)})
    )
    final_key = ("fine-full", final_scales)
    if final_key not in cache:
        cache[final_key] = prepare_templates(
            template,
            "precise",
            cv2,
            mask,
            scales=final_scales,
            crop_origin=crop_origin,
            canvas_size=(template_w, template_h),
        )
    max_scale = max(final_scales)
    padding_x = max(36, int(template_w * max_scale * 0.80))
    padding_y = max(36, int(template_h * max_scale * 0.80))
    left = max(0, int(full_center_x - padding_x))
    top = max(0, int(full_center_y - padding_y))
    right = min(frame.shape[1], int(full_center_x + padding_x + 1))
    bottom = min(frame.shape[0], int(full_center_y + padding_y + 1))
    roi = frame[top:bottom, left:right]
    final_match, final_score = match_frame(
        roi,
        cache[final_key],
        threshold,
        "precise",
        cv2,
        np,
        return_best=True,
        stop_on_threshold=False,
    )
    if final_match is None or final_score < threshold:
        return None, max(float(coarse_score), float(fine_score), float(final_score))
    confidence, location, width, height = final_match
    translated = (confidence, (location[0] + left, location[1] + top), width, height)
    return translated, float(confidence)


def write_result(path: str, text: str) -> None:
    destination = os.path.abspath(path)
    parent = os.path.dirname(destination)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temporary = f"{destination}.{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def safe_error_text(exc: BaseException) -> str:
    """Keep the AHK result protocol on one comma-safe line."""
    text = f"{type(exc).__name__}: {exc}".replace("\r", " ").replace("\n", " ").replace(",", ";")
    return text[:300]


def read_image_unicode(path: str, cv2, np):
    """Decode an image without cv2.imread's Windows Unicode-path limitation."""
    encoded = np.fromfile(os.path.abspath(path), dtype=np.uint8)
    if encoded.size == 0:
        return None
    return cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)


def trim_transparent_template(template, mask, np, padding: int = 3):
    """Trim transparent work while retaining metadata for the original canvas centre."""
    canvas_size = (int(template.shape[1]), int(template.shape[0]))
    if mask is None:
        return template, mask, (0, 0), canvas_size
    ys, xs = np.where(mask > 8)
    if xs.size == 0 or ys.size == 0:
        return template, mask, (0, 0), canvas_size
    left = max(0, int(xs.min()) - padding)
    top = max(0, int(ys.min()) - padding)
    right = min(canvas_size[0], int(xs.max()) + padding + 1)
    bottom = min(canvas_size[1], int(ys.max()) + padding + 1)
    if left == 0 and top == 0 and right == canvas_size[0] and bottom == canvas_size[1]:
        return template, mask, (0, 0), canvas_size
    return template[top:bottom, left:right], mask[top:bottom, left:right], (left, top), canvas_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--region", action="append", required=True)
    parser.add_argument("--threshold", type=float, default=0.86)
    parser.add_argument("--profile", choices=("fast", "balanced", "precise"), default="balanced")
    parser.add_argument("--timeout", type=int, default=0, help="milliseconds")
    parser.add_argument("--poll", type=int, default=60, help="milliseconds")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        required = ("imdecode", "matchTemplate", "cvtColor", "minMaxLoc")
        missing = [name for name in required if not hasattr(cv2, name)]
        if missing:
            raise ImportError("incomplete cv2 module; missing " + "/".join(missing))
    except Exception as exc:
        write_result(args.out, f"ERROR,IMPORT_FAILED,{safe_error_text(exc)}\n")
        return 2

    image_path = os.path.abspath(args.image)
    if not os.path.exists(image_path):
        write_result(args.out, f"ERROR,IMAGE_MISSING,{image_path.replace(',', ';')}\n")
        return 3
    try:
        template = read_image_unicode(image_path, cv2, np)
    except Exception as exc:
        write_result(args.out, f"ERROR,IMAGE_DECODE,{safe_error_text(exc)}\n")
        return 4
    if template is None:
        write_result(args.out, f"ERROR,IMAGE_DECODE,{image_path.replace(',', ';')}\n")
        return 4
    template_mask = None
    if template.ndim == 3 and template.shape[2] == 4:
        alpha = template[:, :, 3]
        if int(np.count_nonzero(alpha > 8)):
            template_mask = np.where(alpha > 8, 255, 0).astype(np.uint8)
        template = template[:, :, :3]
    elif template.ndim == 2:
        template = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)
    template, template_mask, crop_origin, canvas_size = trim_transparent_template(template, template_mask, np)

    regions = [parse_region(text) for text in args.region]
    threshold = max(0.5, min(0.99, float(args.threshold)))
    templates = (
        None
        if args.profile == "precise"
        else prepare_templates(
            template,
            args.profile,
            cv2,
            template_mask,
            crop_origin=crop_origin,
            canvas_size=canvas_size,
        )
    )
    precise_cache = {}
    deadline = time.perf_counter() + max(0, args.timeout) / 1000.0
    best_score = 0.0
    grabber = None
    try:
        try:
            import mss  # type: ignore

            grabber = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
        except Exception:
            grabber = None
        while True:
            cycle_started = time.perf_counter()
            for left, top, right, bottom in regions:
                frame = capture_region(left, top, right, bottom, grabber)
                if frame is None:
                    continue
                if args.profile == "precise":
                    match, score = adaptive_precise_match(
                        frame,
                        template,
                        template_mask,
                        threshold,
                        cv2,
                        np,
                        precise_cache,
                        crop_origin=crop_origin,
                        canvas_size=canvas_size,
                    )
                else:
                    match, score = match_frame(frame, templates, threshold, args.profile, cv2, np)
                best_score = max(best_score, float(score))
                if match is not None:
                    confidence, location, tpl_w, tpl_h = match
                    found_x = left + location[0] + tpl_w // 2
                    found_y = top + location[1] + tpl_h // 2
                    write_result(args.out, f"FOUND,{found_x},{found_y},{confidence:.4f},{tpl_w},{tpl_h}\n")
                    return 0
            if args.timeout <= 0 or time.perf_counter() >= deadline:
                break
            remaining = max(0.0, args.poll / 1000.0 - (time.perf_counter() - cycle_started))
            if remaining:
                time.sleep(remaining)
    finally:
        if grabber is not None:
            try:
                grabber.close()
            except Exception:
                pass
    write_result(args.out, f"NOTFOUND,{best_score:.4f}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
