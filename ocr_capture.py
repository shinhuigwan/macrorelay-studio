"""High-speed screen capture module for OCR engine."""

import ctypes
import sys
from typing import Any

__all__ = [
    "capture_screen_region",
    "capture_window",
    "capture_client_area",
    "find_window_by_title",
    "get_all_monitors",
    "region_to_image"
]

try:
    import numpy as np
except ImportError:
    np = None

def _enable_physical_pixel_coordinates() -> None:
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

_enable_physical_pixel_coordinates()

# Win32 API Definitions for Window Capture
if sys.platform == "win32":
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [
            ("x", ctypes.c_long),
            ("y", ctypes.c_long),
        ]

    SRCCOPY = 0x00CC0020
    PW_RENDERFULLCONTENT = 2

def get_all_monitors() -> list[dict]:
    """Get bounding coordinates for all connected monitors."""
    monitors = []
    try:
        import mss
        with mss.mss() as sct:
            for i, monitor in enumerate(sct.monitors[1:], 1):
                monitors.append({
                    "id": i,
                    "left": monitor["left"],
                    "top": monitor["top"],
                    "width": monitor["width"],
                    "height": monitor["height"],
                    "right": monitor["left"] + monitor["width"],
                    "bottom": monitor["top"] + monitor["height"]
                })
    except Exception:
        if sys.platform == "win32":
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            monitors.append({
                "id": 1,
                "left": 0, "top": 0, "width": w, "height": h,
                "right": w, "bottom": h
            })
    return monitors


def get_virtual_screen_region() -> list[int]:
    """Return the physical-pixel bounds of the complete virtual desktop."""
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            left = int(monitor["left"])
            top = int(monitor["top"])
            return [left, top, left + int(monitor["width"]), top + int(monitor["height"])]
    except Exception:
        monitors = get_all_monitors()
        if monitors:
            return [
                min(int(item["left"]) for item in monitors),
                min(int(item["top"]) for item in monitors),
                max(int(item["right"]) for item in monitors),
                max(int(item["bottom"]) for item in monitors),
            ]
    if sys.platform == "win32":
        left = int(user32.GetSystemMetrics(76))
        top = int(user32.GetSystemMetrics(77))
        return [left, top, left + int(user32.GetSystemMetrics(78)), top + int(user32.GetSystemMetrics(79))]
    raise RuntimeError("Virtual desktop bounds are unavailable")

def capture_screen_region(left: int, top: int, right: int, bottom: int) -> Any:
    """Capture a region of the virtual desktop."""
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid capture region: {left},{top} to {right},{bottom}")
        
    # 1. Try mss (fastest multi-monitor)
    try:
        import mss
        with mss.mss() as sct:
            img = sct.grab({"left": left, "top": top, "width": width, "height": height})
            if np is not None:
                return np.array(img)[:, :, :3]  # BGRA -> BGR
            return img
    except Exception:
        pass
        
    # 2. Try Win32 BitBlt (native Windows API, 0 external dependencies)
    if sys.platform == "win32" and np is not None:
        try:
            import struct
            hdeskdc = user32.GetDC(0)
            hmemdc = gdi32.CreateCompatibleDC(hdeskdc)
            hbitmap = gdi32.CreateCompatibleBitmap(hdeskdc, width, height)
            gdi32.SelectObject(hmemdc, hbitmap)
            gdi32.BitBlt(hmemdc, 0, 0, width, height, hdeskdc, left, top, 0x00CC0020 | 0x40000000)
            bmi = ctypes.c_buffer(40)
            struct.pack_into('<IiiHHIIiiII', bmi, 0, 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ctypes.c_buffer(width * height * 4)
            gdi32.GetDIBits(hmemdc, hbitmap, 0, height, buf, bmi, 0)
            gdi32.DeleteObject(hbitmap)
            gdi32.DeleteDC(hmemdc)
            user32.ReleaseDC(0, hdeskdc)
            arr = np.frombuffer(buf, dtype=np.uint8).reshape((height, width, 4))
            return arr[:, :, :3]  # BGRA -> BGR
        except Exception:
            pass

    # 3. Fallback to PIL ImageGrab
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=(left, top, right, bottom))
        if np is not None:
            return np.array(img)[:, :, ::-1]  # RGB -> BGR
        return img
    except Exception as e:
        raise RuntimeError(f"Screen capture failed: {e}")

def find_window_by_title(title: str) -> int | None:
    """Find a window HWND by its title."""
    if sys.platform != "win32":
        return None
    hwnd = user32.FindWindowW(None, title)
    return hwnd if hwnd else None

def _capture_hwnd_internal(hwnd: int, client_only: bool = False) -> Any:
    if sys.platform != "win32":
        raise NotImplementedError("Window capture requires Windows")
    if not hwnd:
        raise ValueError("Invalid HWND")
        
    rect = RECT()
    if client_only:
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
    else:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top

    if width <= 0 or height <= 0:
        raise ValueError("Window width or height is zero or negative")

    hdc_window = user32.GetWindowDC(hwnd)
    hdc_mem_dc = gdi32.CreateCompatibleDC(hdc_window)
    h_bit_map = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    
    gdi32.SelectObject(hdc_mem_dc, h_bit_map)
    
    if client_only:
        hdc_client = user32.GetDC(hwnd)
        gdi32.BitBlt(hdc_mem_dc, 0, 0, width, height, hdc_client, 0, 0, SRCCOPY)
        user32.ReleaseDC(hwnd, hdc_client)
    else:
        result = user32.PrintWindow(hwnd, hdc_mem_dc, PW_RENDERFULLCONTENT)
        if not result:
            gdi32.BitBlt(hdc_mem_dc, 0, 0, width, height, hdc_window, 0, 0, SRCCOPY)
            
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]
        
    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", ctypes.c_ulong * 3)
        ]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bmi.bmiHeader.biSizeImage = 0

    buffer_len = width * height * 4
    buffer = ctypes.create_string_buffer(buffer_len)
    
    gdi32.GetDIBits(hdc_mem_dc, h_bit_map, 0, height, ctypes.byref(buffer), ctypes.byref(bmi), 0)

    gdi32.DeleteObject(h_bit_map)
    gdi32.DeleteDC(hdc_mem_dc)
    user32.ReleaseDC(hwnd, hdc_window)

    if np is not None:
        img_arr = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width, 4))
        return img_arr[:, :, :3]  # BGRA to BGR
        
    try:
        from PIL import Image
        return Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
    except ImportError:
        return buffer.raw

def capture_window(hwnd: int) -> Any:
    """Capture the entire window including non-client area (borders, title)."""
    return _capture_hwnd_internal(hwnd, client_only=False)

def capture_client_area(hwnd: int) -> Any:
    """Capture only the client area of the window."""
    return _capture_hwnd_internal(hwnd, client_only=True)

def region_to_image(region: list[int], capture_mode: str = 'screen', window_title: str = '', window_hwnd: int = 0) -> tuple[Any, dict]:
    """
    Main entry point for screen/window capture.
    Returns (image, metadata). Image is a numpy BGR array (if available).
    """
    metadata = {
        "mode": capture_mode,
        "region_requested": region,
        "hwnd": window_hwnd,
        "title": window_title,
    }
    
    if capture_mode in ("window", "client"):
        hwnd = window_hwnd
        if not hwnd and window_title:
            hwnd = find_window_by_title(window_title)
            metadata["hwnd"] = hwnd
            
        if not hwnd:
            raise ValueError(f"Could not find window by hwnd ({window_hwnd}) or title ({window_title})")
            
        img = _capture_hwnd_internal(hwnd, client_only=(capture_mode == "client"))
        
        rect = RECT()
        if capture_mode == "client":
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            pt = POINT(rect.left, rect.top)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            rect.left += pt.x
            rect.right += pt.x
            rect.top += pt.y
            rect.bottom += pt.y
        else:
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            
        metadata["actual_region"] = [rect.left, rect.top, rect.right, rect.bottom]
        
        if region and len(region) == 4 and region != [0, 0, 0, 0]:
            l, t, r, b = (int(value) for value in region)
            image_height, image_width = img.shape[:2] if np is not None and isinstance(img, np.ndarray) else (img.height, img.width)
            l = max(0, min(l, image_width))
            r = max(l, min(r, image_width))
            t = max(0, min(t, image_height))
            b = max(t, min(b, image_height))
            if r <= l or b <= t:
                raise ValueError(f"Invalid window-relative OCR region: {region}")
            if np is not None and isinstance(img, np.ndarray):
                img = img[t:b, l:r]
            else:
                img = img.crop((l, t, r, b))
            metadata["actual_region"] = [rect.left + l, rect.top + t, rect.left + r, rect.top + b]
                
        return img, metadata
        
    if not region or len(region) != 4:
        raise ValueError("Region [left, top, right, bottom] is required for screen capture")
        
    if list(region) == [0, 0, 0, 0]:
        region = get_virtual_screen_region()
        metadata["region_requested"] = [0, 0, 0, 0]
        metadata["full_virtual_screen"] = True

    left, top, right, bottom = (int(value) for value in region)
    img = capture_screen_region(left, top, right, bottom)
    metadata["actual_region"] = [left, top, right, bottom]
    
    return img, metadata
