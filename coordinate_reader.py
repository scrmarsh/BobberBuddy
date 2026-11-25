from decimal import Decimal, ROUND_FLOOR
import re

import cv2
import numpy as np

from game_window import client_area
from seed_map import coordinate


INTEGER = r"-?\d{1,8}"
DECIMAL = r"-?\d{1,8}(?:\.\d{1,8})?"
LABEL_END = r"[^\w-]{1,8}"
# Bedrock's pixel font makes HUD commas look like periods to Windows OCR.
POSITION = re.compile(
    rf"\bPosition{LABEL_END}({INTEGER})\s*(?:,\s*|\.\s+)({INTEGER})\s*(?:,\s*|\.\s+)({INTEGER})(?![\w.])",
    re.I,
)
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


def bedrock_hud_rows(gray):
    # Isolate bright text on the left before smoothing. OCR can otherwise
    # drop the last number in Bedrock's pixel font against a detailed scene.
    bright = gray >= 220
    rows = (bright[:, :min(135, gray.shape[1])].sum(axis=1) >= 7)
    bands = np.flatnonzero(np.diff(np.r_[False, rows, False])).reshape(-1, 2)
    for top, bottom in bands:
        height = bottom - top
        if not 6 <= height <= 42:
            continue
        columns = np.flatnonzero(bright[top:bottom].any(axis=0))
        if not len(columns) or columns[0] > 24:
            continue
        # Keep the whole coordinate line. Wide spaces between numbers can
        # otherwise be mistaken for the end of the HUD and discard Z.
        padding = max(8, round(height * 0.6))
        a, b = max(0, top - padding), min(gray.shape[0], bottom + padding)
        line = gray[a:b]
        for factor in (1.0, 0.85, 1.15):
            smooth = cv2.GaussianBlur(line, (0, 0), max(0.6, height / 23 * factor))
            yield smooth
            yield cv2.threshold(smooth, 150, 255, cv2.THRESH_BINARY_INV)[1]
        gaps = np.flatnonzero(np.diff(columns) > max(12, height * 0.8))
        end = columns[gaps[0]] if len(gaps) else columns[-1]
        if end < height * 8:
            continue
        for padding in (height // 3, height // 4, height // 2):
            a, b = max(0, top - padding), min(gray.shape[0], bottom + padding)
            strip = cv2.threshold(gray[a:b, :end + 4], 150, 255, cv2.THRESH_BINARY_INV)[1]
            padded = cv2.copyMakeBorder(strip, 12, 12, 22, 22, cv2.BORDER_CONSTANT, value=255)
            yield cv2.resize(padded, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_LINEAR)


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

    async def read(self, screen, hwnd, *, confirm=False):
        area = client_area(hwnd)
        if area is None or area["width"] < 240 or area["height"] < 160:
            return None
        area["width"] = min(800, area["width"] // 2)
        area["height"] = min(475, area["height"] // 3)
        frame = np.asarray(screen.grab(area))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        votes = {}
        for candidate in self.candidates(gray):
            position = await self.recognize(candidate)
            if position is not None:
                if not confirm or self.edition != "bedrock":
                    return position
                votes[position] = votes.get(position, 0) + 1
                if votes[position] >= 2:
                    return position
        return None

    def candidates(self, gray):
        if self.edition == "bedrock":
            yield from bedrock_hud_rows(gray)
        scaled = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        yield cv2.threshold(scaled, 152, 255, cv2.THRESH_BINARY_INV)[1]
        yield scaled

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
