"""
OCR preprocessing pipeline module.
Prepares captured screen images for OCR recognition.
"""

import os
import math
from dataclasses import dataclass
from typing import List

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    try:
        from PIL import Image
        HAS_PIL = True
    except ImportError:
        HAS_PIL = False

__all__ = [
    'PreprocessResult',
    'preprocess',
    'preprocess_auto',
    'preprocess_fast',
    'preprocess_precise',
    'preprocess_number',
    'preprocess_game_ui',
]


@dataclass
class PreprocessResult:
    """Result of preprocessing an image for OCR."""
    image: np.ndarray
    name: str
    description: str
    scale: float
    is_inverted: bool
    is_grayscale: bool


def _save_debug(image: np.ndarray, name: str, debug_dir: str, step: str = "") -> None:
    if not debug_dir or not os.path.exists(debug_dir):
        return
    if HAS_CV2:
        filename = f"{name}_{step}.png" if step else f"{name}.png"
        filepath = os.path.join(debug_dir, filename)
        cv2.imwrite(filepath, image)


def _scale_image(image: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return image.copy()
    if HAS_CV2:
        height, width = image.shape[:2]
        new_size = (int(width * scale), int(height * scale))
        return cv2.resize(image, new_size, interpolation=cv2.INTER_CUBIC)
    elif HAS_PIL:
        img = Image.fromarray(image)
        new_size = (int(img.width * scale), int(img.height * scale))
        img = img.resize(new_size, Image.Resampling.BICUBIC)
        return np.array(img)
    return image.copy()


def _to_grayscale(image: np.ndarray) -> np.ndarray:
    if len(image.shape) < 3:
        return image.copy()
    if HAS_CV2:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    elif HAS_PIL:
        img = Image.fromarray(image[..., ::-1])
        return np.array(img.convert('L'))
    return image.mean(axis=2).astype(np.uint8)


def _invert(image: np.ndarray) -> np.ndarray:
    if HAS_CV2:
        return cv2.bitwise_not(image)
    return 255 - image


def _otsu_binarize(image: np.ndarray) -> np.ndarray:
    if not HAS_CV2:
        return image.copy()
    gray = _to_grayscale(image)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return binary


def _adaptive_binarize(image: np.ndarray, block_size: int = 11, c: int = 2) -> np.ndarray:
    if not HAS_CV2:
        return image.copy()
    gray = _to_grayscale(image)
    if block_size % 2 == 0:
        block_size += 1
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, c)


def _denoise(image: np.ndarray) -> np.ndarray:
    if not HAS_CV2:
        return image.copy()
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    opened = cv2.morphologyEx(image, cv2.MORPH_OPEN, kernel, iterations=1)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel, iterations=1)
    blurred = cv2.GaussianBlur(closed, (3, 3), 0)
    return blurred


def _sharpen(image: np.ndarray) -> np.ndarray:
    if not HAS_CV2:
        return image.copy()
    gaussian = cv2.GaussianBlur(image, (0, 0), 2.0)
    return cv2.addWeighted(image, 1.5, gaussian, -0.5, 0)


def _edge_enhance(image: np.ndarray) -> np.ndarray:
    if not HAS_CV2:
        return image.copy()
    gray = _to_grayscale(image)
    edges = cv2.Canny(gray, 100, 200)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    dilated_edges = cv2.dilate(edges, kernel, iterations=1)
    enhanced = cv2.addWeighted(gray, 0.8, dilated_edges, 0.2, 0)
    return enhanced


def _skew_correction(image: np.ndarray) -> np.ndarray:
    if not HAS_CV2:
        return image.copy()
    gray = _to_grayscale(image)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, 100, minLineLength=100, maxLineGap=10)
    
    if lines is not None:
        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            angles.append(angle)
        
        if angles:
            median_angle = float(np.median(angles))
            if abs(median_angle) > 0.5 and abs(median_angle) < 45:
                (h, w) = image.shape[:2]
                center = (w // 2, h // 2)
                m_matrix = cv2.getRotationMatrix2D(center, median_angle, 1.0)
                rotated = cv2.warpAffine(image, m_matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
                return rotated
    return image.copy()


def _separate_channels(image: np.ndarray) -> List[np.ndarray]:
    if not HAS_CV2 or len(image.shape) < 3:
        return [_to_grayscale(image)]
    b, g, r = cv2.split(image)
    return [b, g, r]


def _auto_spacing(image: np.ndarray) -> np.ndarray:
    if not HAS_CV2:
        return image.copy()
    binary = _otsu_binarize(image)
    inverted_binary = cv2.bitwise_not(binary)
    # Perform basic component analysis but use morphology for separation as placeholder
    _num_labels, _labels, _stats, _centroids = cv2.connectedComponentsWithStats(inverted_binary, connectivity=8)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    separated = cv2.erode(image, kernel, iterations=1)
    return separated


def preprocess_auto(image: np.ndarray) -> List[PreprocessResult]:
    """Analyze image characteristics, pick best combination."""
    if not HAS_CV2:
        return preprocess_fast(image)
    
    results = []
    
    gray = _to_grayscale(image)
    mean_val = cv2.mean(gray)[0]
    std_val = cv2.meanStdDev(gray)[1][0][0]
    
    h, w = image.shape[:2]
    
    is_dark = mean_val < 100
    is_low_contrast = std_val < 40
    is_small = h < 30
    
    scale_factor = 3.0 if is_small else 2.0
    scaled = _scale_image(image, scale_factor)
    
    # 1: Standard Grayscale + Otsu
    gray_scaled = _to_grayscale(scaled)
    _, otsu1 = cv2.threshold(gray_scaled, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    results.append(PreprocessResult(
        image=otsu1,
        name='auto_standard',
        description=f'Scale {scale_factor}x + Otsu',
        scale=scale_factor,
        is_inverted=False,
        is_grayscale=True
    ))
    
    # 2: Inverted
    if is_dark:
        inverted = _invert(scaled)
        gray_inv = _to_grayscale(inverted)
        _, otsu2 = cv2.threshold(gray_inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        results.append(PreprocessResult(
            image=otsu2,
            name='auto_inverted',
            description=f'Scale {scale_factor}x + Invert + Otsu',
            scale=scale_factor,
            is_inverted=True,
            is_grayscale=True
        ))
    
    # 3: Adaptive + Sharpen
    if is_low_contrast:
        sharpened = _sharpen(scaled)
        adapt = _adaptive_binarize(sharpened, block_size=21, c=5)
        results.append(PreprocessResult(
            image=adapt,
            name='auto_adaptive_sharpen',
            description=f'Scale {scale_factor}x + Sharpen + Adaptive',
            scale=scale_factor,
            is_inverted=False,
            is_grayscale=True
        ))
        
    # 4: Base Grayscale
    results.append(PreprocessResult(
        image=gray_scaled,
        name='auto_gray_only',
        description=f'Scale {scale_factor}x + Grayscale',
        scale=scale_factor,
        is_inverted=False,
        is_grayscale=True
    ))

    return results


def preprocess_fast(image: np.ndarray) -> List[PreprocessResult]:
    """2x scale + Otsu only"""
    scale = 2.0
    scaled = _scale_image(image, scale)
    otsu = _otsu_binarize(scaled)
    return [PreprocessResult(
        image=otsu,
        name='fast',
        description='2x Scale + Otsu',
        scale=scale,
        is_inverted=False,
        is_grayscale=True
    )]


def preprocess_precise(image: np.ndarray) -> List[PreprocessResult]:
    """고정밀 교차 판정을 위한 서로 다른 네 가지 후보를 만든다."""
    scale = 3.0
    scaled = _scale_image(image, scale)
    gray = _to_grayscale(scaled)
    sharpened = _sharpen(gray)
    otsu = _otsu_binarize(sharpened)
    adaptive = _adaptive_binarize(sharpened, block_size=31, c=7)
    results = [
        PreprocessResult(gray, 'precise_gray', '3x + Gray', scale, False, True),
        PreprocessResult(sharpened, 'precise_sharp', '3x + Gray + Sharpen', scale, False, True),
        PreprocessResult(otsu, 'precise_otsu', '3x + Sharpen + Otsu', scale, False, True),
        PreprocessResult(adaptive, 'precise_adaptive', '3x + Sharpen + Adaptive', scale, False, True),
    ]
    mean_val = cv2.mean(gray)[0] if HAS_CV2 else float(gray.mean())
    if mean_val < 105:
        results.append(PreprocessResult(_invert(otsu), 'precise_inverted', '3x + Otsu + Invert', scale, True, True))
    return results


def preprocess_number(image: np.ndarray) -> List[PreprocessResult]:
    """2x + Otsu, optimized for digits"""
    scale = 2.0
    scaled = _scale_image(image, scale)
    otsu = _otsu_binarize(scaled)
    
    if HAS_CV2:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        otsu = cv2.erode(otsu, kernel, iterations=1)
        
    return [PreprocessResult(
        image=otsu,
        name='number',
        description='2x Scale + Otsu + Dilate Text',
        scale=scale,
        is_inverted=False,
        is_grayscale=True
    )]


def preprocess_game_ui(image: np.ndarray) -> List[PreprocessResult]:
    """3x + edge enhance + channel separation + inversion"""
    results = []
    scale = 3.0
    scaled = _scale_image(image, scale)
    
    enhanced = _edge_enhance(scaled)
    results.append(PreprocessResult(
        image=enhanced,
        name='game_ui_edge',
        description='3x Scale + Edge Enhance',
        scale=scale,
        is_inverted=False,
        is_grayscale=True
    ))
    
    inverted = _invert(enhanced)
    results.append(PreprocessResult(
        image=inverted,
        name='game_ui_edge_inv',
        description='3x Scale + Edge Enhance + Invert',
        scale=scale,
        is_inverted=True,
        is_grayscale=True
    ))
    
    channels = _separate_channels(scaled)
    if len(channels) >= 3:
        green_channel = channels[1]
        _, otsu_g = cv2.threshold(green_channel, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        results.append(PreprocessResult(
            image=otsu_g,
            name='game_ui_green_otsu',
            description='3x Scale + Green Channel + Otsu',
            scale=scale,
            is_inverted=False,
            is_grayscale=True
        ))
        
    return results


def preprocess(image: np.ndarray, profile: str = 'auto', debug_dir: str = '') -> List[PreprocessResult]:
    """
    Preprocess image for OCR.
    
    Args:
        image: BGR numpy array
        profile: Profile name ('auto', 'fast', 'precise', 'number', 'game_ui')
        debug_dir: Directory to save debug images
        
    Returns:
        List of preprocessed candidates
    """
    if profile == 'fast':
        results = preprocess_fast(image)
    elif profile == 'precise':
        results = preprocess_precise(image)
    elif profile == 'number':
        results = preprocess_number(image)
    elif profile == 'game_ui':
        results = preprocess_game_ui(image)
    else:
        results = preprocess_auto(image)
        
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        for i, res in enumerate(results):
            _save_debug(res.image, f"candidate_{i}_{res.name}", debug_dir)
            
    return results
