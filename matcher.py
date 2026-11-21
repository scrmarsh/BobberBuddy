from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Match:
    score: float
    box: tuple[int, int, int, int]


def bgr(image):
    return cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)


class Matcher:
    def __init__(self, path: Path):
        with Image.open(path) as source:
            image = source.convert("RGBA")
        visible = image.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0)
        bounds = visible.getbbox()
        if bounds is None:
            raise ValueError("The template is fully transparent.")
        image = image.crop(bounds)
        visible = visible.crop(bounds)
        if min(image.size) < 4 or max(image.size) > 256:
            raise ValueError("Crop the template to the bobber (4 to 256 pixels per side).")
        widths = {6, 8, 10, 12, 15, 18, 22, 27, 33, 40, 48, image.width}
        self.templates = []
        for width in sorted(widths):
            height = max(4, round(image.height * width / image.width))
            rgb = image.resize((width, height), Image.Resampling.NEAREST)
            mask = np.asarray(visible.resize((width, height), Image.Resampling.NEAREST))
            count = np.count_nonzero(mask)
            if count >= 12:
                self.templates.append((bgr(rgb), mask, count))
        if not self.templates:
            raise ValueError("The template needs more visible pixels.")

    def find(self, frame, region=None, expected_width=None) -> Match | None:
        left, top = 0, 0
        if region is not None:
            left, top, right, bottom = region
            frame = frame[top:bottom, left:right]
        best = None
        for template, mask, count in self.templates:
            height, width = mask.shape
            if expected_width is not None and abs(width - expected_width) > expected_width * 0.35 + 3:
                continue
            if height > frame.shape[0] or width > frame.shape[1]:
                continue
            error = cv2.matchTemplate(frame, template, cv2.TM_SQDIFF, mask=mask)
            color = 1 - np.sqrt(np.maximum(error, 0) / (count * 3)) / 255
            contrast = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED, mask=mask)
            np.nan_to_num(contrast, copy=False, nan=0, posinf=0, neginf=0)
            similarity = 0.5 * color + 0.25 * (1 + np.clip(contrast, -1, 1))
            _, score, _, (x, y) = cv2.minMaxLoc(similarity)
            if best is None or score > best.score:
                best = Match(score, (x + left, y + top, x + left + width, y + top + height))
        return best
