"""
Tesseract OCR Engine Module for MacroRelay Studio.

Wraps pytesseract with enhanced feature set like auto PSM selection,
structured output parsing, and candidate iteration.
"""

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys
from pathlib import Path
import numpy as np

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

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import pytesseract
except ImportError:
    pytesseract = None


__all__ = [
    "TesseractBox",
    "TesseractResult",
    "recognize",
    "recognize_candidates",
    "auto_select_psm",
    "is_available",
    "ensure_tesseract"
]


@dataclass
class TesseractBox:
    text: str
    confidence: float
    rect: Tuple[int, int, int, int]  # (x, y, w, h) in preprocessed image coords
    center: Tuple[int, int]


@dataclass
class TesseractResult:
    text: str
    normalized_text: str
    confidence: float
    boxes: List[TesseractBox]
    psm: int
    lang: str
    elapsed_ms: float


def is_available() -> bool:
    """Check if pytesseract and Tesseract executable are available."""
    if pytesseract is None or Image is None:
        return False
    try:
        return find_tesseract_path() is not None
    except Exception:
        return False


def find_tesseract_path() -> Optional[str]:
    """Find the path to the Tesseract executable."""
    helper_dir = Path(__file__).resolve().parent
    configured_files = [helper_dir / "tesseract_path.txt", helper_dir.parent / "tesseract_path.txt"]
    for configured_file in configured_files:
        try:
            raw_path = configured_file.read_text(encoding="utf-8-sig").strip()
        except (OSError, ValueError):
            continue
        if not raw_path:
            continue
        configured = Path(raw_path)
        if not configured.is_absolute():
            configured = helper_dir / configured
        if configured.exists():
            return str(configured)
            
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
            
    return shutil.which("tesseract")


def _resolve_tessdata_dir(tesseract_path: Path) -> Optional[Path]:
    """Find the tessdata directory relative to the tesseract executable."""
    base = tesseract_path.parent
    candidate = base / "tessdata"
    if candidate.exists():
        return candidate
    return None


def ensure_tesseract(lang: str = "eng+kor", tessdata_variant: str = "fast") -> Path:
    """Ensure Tesseract executable and language data are available."""
    if not is_available():
        raise RuntimeError("Tesseract OCR (or pytesseract/Pillow) is not installed.")
        
    cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "") or ""
    if not cmd or not Path(cmd).is_file():
        found = find_tesseract_path()
        if not found:
            raise RuntimeError("Tesseract OCR is not installed or not on PATH.")
        pytesseract.tesseract_cmd = found
        if hasattr(pytesseract, "pytesseract"):
            pytesseract.pytesseract.tesseract_cmd = found
        cmd = found

    wanted = [code.strip() for code in (lang or "").split("+") if code.strip()]
    candidates = []

    # The requested bundled data must win over a machine-wide installation.
    # Otherwise "best" silently kept using the smaller system/fast model.
    local_dir = Path(__file__).resolve().parent / ("tessdata_best" if tessdata_variant == "best" else "tessdata")
    if local_dir.exists():
        candidates.append(local_dir)
    
    env_dir = os.environ.get("TESSDATA_PREFIX")
    if env_dir:
        candidates.append(Path(env_dir))
        
    install_dir = _resolve_tessdata_dir(Path(cmd))
    if install_dir:
        candidates.append(install_dir)
        
    alt_local = Path(__file__).resolve().parent / "tessdata"
    if alt_local.exists() and alt_local not in candidates:
        candidates.append(alt_local)

    def has_all(folder: Path) -> bool:
        return all((folder / f"{code}.traineddata").exists() for code in wanted)

    for folder in candidates:
        if folder.exists() and has_all(folder):
            os.environ["TESSDATA_PREFIX"] = str(folder)
            return folder

    # Merge missing langs into local tessdata folder if possible
    if not local_dir.exists():
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
            
    if local_dir.exists():
        for code in wanted:
            target = local_dir / f"{code}.traineddata"
            if target.exists():
                continue
            for folder in candidates:
                src = folder / f"{code}.traineddata"
                if src.exists():
                    try:
                        shutil.copy2(src, target)
                    except OSError:
                        pass
                    break

        if has_all(local_dir):
            os.environ["TESSDATA_PREFIX"] = str(local_dir)
            return local_dir

    missing_all = ", ".join([code for code in wanted if not (local_dir / f"{code}.traineddata").exists()])
    raise RuntimeError(f"Tesseract language data missing: {missing_all}")


def auto_select_psm(image_height: int, whitelist: str = '', expect_text: str = '') -> int:
    """
    Auto-select Page Segmentation Mode (PSM) based on image characteristics and expectations.
    """
    if expect_text and len(expect_text.strip()) == 1:
        return 10
        
    if whitelist and whitelist.isdigit():
        return 7
        
    if image_height < 25:
        return 8
        
    if image_height < 40:
        return 7
        
    return 6


def normalize_text(text: str) -> str:
    """Normalize whitespace and line endings."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned = [line.rstrip() for line in lines if line.rstrip()]
    return "\n".join(cleaned)


def recognize(
    image: np.ndarray,
    lang: str = 'eng+kor',
    psm: Optional[int] = None,
    whitelist: str = '',
    expect_text: str = '',
    scale: float = 1.0,
    tessdata_variant: str = 'fast',
) -> TesseractResult:
    """
    Run Tesseract OCR on a single image array and return structured result.
    """
    if not is_available():
        raise RuntimeError("Tesseract is not available.")
        
    tessdata_dir = ensure_tesseract(lang=lang, tessdata_variant=tessdata_variant)
    
    start_time = time.perf_counter()
    
    if isinstance(image, np.ndarray):
        img_pil = Image.fromarray(image)
    else:
        img_pil = image
        
    width, height = img_pil.size
    
    actual_psm = psm if psm is not None else auto_select_psm(height, whitelist, expect_text)
    
    # TESSDATA_PREFIX is set by ensure_tesseract(). Passing a quoted Windows
    # path through pytesseract's config parser makes the quote part of the
    # directory name on some Tesseract builds.
    config = f"--psm {actual_psm} --oem 3 -c preserve_interword_spaces=1"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
        
    try:
        data = pytesseract.image_to_data(img_pil, lang=lang, config=config, output_type=pytesseract.Output.DICT)
    except Exception as e:
        raise RuntimeError(f"Tesseract execution failed: {e}")
        
    boxes = []
    confs = []
    lines: dict[tuple[int, int, int, int], list[str]] = {}
    scale = max(float(scale or 1.0), 0.01)
    
    n_boxes = len(data.get('level', []))
    for i in range(n_boxes):
        text_val = data['text'][i].strip()
        try:
            conf_val = float(data['conf'][i])
        except (ValueError, TypeError):
            conf_val = -1.0
            
        if conf_val >= 0 and text_val:
            x = data['left'][i]
            y = data['top'][i]
            w = data['width'][i]
            h = data['height'][i]
            
            x1 = int(round(x / scale))
            y1 = int(round(y / scale))
            x2 = int(round((x + w) / scale))
            y2 = int(round((y + h) / scale))
            center = ((x1 + x2) // 2, (y1 + y2) // 2)
            normalized_conf = max(0.0, min(conf_val / 100.0, 1.0))
            
            boxes.append(TesseractBox(
                text=text_val,
                confidence=normalized_conf,
                rect=(x1, y1, x2, y2),
                center=center
            ))
            confs.append(normalized_conf)
            line_key = tuple(int(data.get(key, [0] * n_boxes)[i]) for key in ("page_num", "block_num", "par_num", "line_num"))
            lines.setdefault(line_key, []).append(text_val)

    # image_to_data already performs recognition. Reusing it avoids running
    # Tesseract a second time merely to obtain the plain text.
    raw_text = "\n".join(" ".join(words) for words in lines.values())
        
    norm_text = normalize_text(raw_text)
    avg_conf = sum(confs) / len(confs) if confs else 0.0
    
    elapsed = (time.perf_counter() - start_time) * 1000.0
    
    return TesseractResult(
        text=raw_text,
        normalized_text=norm_text,
        confidence=avg_conf,
        boxes=boxes,
        psm=actual_psm,
        lang=lang,
        elapsed_ms=elapsed
    )


def recognize_candidates(
    candidates: List[Any],
    lang: str = 'eng+kor',
    psm: Optional[int] = None,
    whitelist: str = '',
    expect_text: str = '',
    tessdata_variant: str = 'fast',
) -> TesseractResult:
    """
    Run Tesseract OCR on a list of PreprocessResult candidates, returning the one with the highest confidence.
    """
    best_result = None
    best_score = -1.0
    
    for cand in candidates:
        try:
            if hasattr(cand, 'image'):
                img = cand.image
            else:
                img = cand
                
            scale = getattr(cand, 'scale', 1.0)
            
            result = recognize(
                image=img,
                lang=lang,
                psm=psm,
                whitelist=whitelist,
                expect_text=expect_text,
                scale=scale,
                tessdata_variant=tessdata_variant,
            )
            
            score = result.confidence
            
            if expect_text and expect_text.lower() in result.normalized_text.lower():
                score += 1000.0
                
            if score > best_score:
                best_score = score
                best_result = result
                
                if expect_text and expect_text.lower() == result.normalized_text.lower():
                    break
                    
        except Exception:
            continue
            
    if best_result is None:
        return TesseractResult(
            text="",
            normalized_text="",
            confidence=0.0,
            boxes=[],
            psm=psm or 6,
            lang=lang,
            elapsed_ms=0.0
        )
        
    return best_result
