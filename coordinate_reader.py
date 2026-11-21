from decimal import Decimal, ROUND_FLOOR
import re

import cv2
import numpy as np

from game_window import client_area
from seed_map import coordinate


INTEGER = r"-?\d{1,8}"
DECIMAL = r"-?\d{1,8}(?:\.\d{1,8})?"
LABEL_END = r"[^\w-]{1,8}"
POSITION = re.compile(rf"\bPosition{LABEL_END}({INTEGER})\s*,\s*({INTEGER})\s*,\s*({INTEGER})\b", re.I)
XYZ = re.compile(rf"\bXYZ{LABEL_END}({DECIMAL})\s*/[^/\r\n]{{1,20}}/\s*({DECIMAL})(?![.\d])", re.I)


def parse_coordinates(text, edition):
    text = re.sub(r"[−–—]\s*|-(?=\s)\s+", "-", text)
    match = (POSITION if edition == "bedrock" else XYZ).search(text)
    if not match:
        return None
    x = int(Decimal(match[1]).to_integral_value(rounding=ROUND_FLOOR))
    z = int(Decimal(match[3 if edition == "bedrock" else 2]).to_integral_value(rounding=ROUND_FLOOR))
    try:
        return coordinate(x), coordinate(z)
    except ValueError:
        return None


class CoordinateReader:
    def __init__(self, edition):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        self.edition = edition
        self.ocr = OcrEngine.try_create_from_language(Language("en-US"))
        if self.ocr is None:
            self.ocr = OcrEngine.try_create_from_user_profile_languages()
        if self.ocr is None:
            raise RuntimeError("Windows OCR language support is unavailable.")

    async def read(self, screen, hwnd):
        area = client_area(hwnd)
        if area is None or area["width"] < 240 or area["height"] < 160:
            return None
        area["width"] = min(800, area["width"] // 2)
        area["height"] = min(475, area["height"] // 3)
        frame = np.asarray(screen.grab(area))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        _, high_contrast = cv2.threshold(gray, 152, 255, cv2.THRESH_BINARY_INV)
        for candidate in (high_contrast, gray):
            position = await self.recognize(candidate)
            if position is not None:
                return position
        return None

    async def recognize(self, grayscale):
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import Buffer

        pixels = cv2.cvtColor(grayscale, cv2.COLOR_GRAY2BGRA)
        buffer = Buffer(pixels.nbytes)
        buffer.length = pixels.nbytes
        memoryview(buffer)[:] = pixels.tobytes()
        height, width = grayscale.shape
        with SoftwareBitmap(BitmapPixelFormat.BGRA8, width, height) as bitmap:
            bitmap.copy_from_buffer(buffer)
            result = await self.ocr.recognize_async(bitmap)
        return parse_coordinates(result.text, self.edition)
