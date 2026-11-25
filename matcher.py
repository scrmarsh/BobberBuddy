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
    def __init__(self, path: Path, *, variants=(), fast_acquisition=False, require_red=False):
        self.templates = []
        self.preferred = None
        self.fast_acquisition = fast_acquisition
        self.require_red = require_red
        self.gray_templates = {}
        self._add_reference(path, 0)
        for variant, minimum in variants:
            self._add_reference(variant, minimum)

    def _add_reference(self, path: Path, minimum: float):
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
        prepared = []
        for width in sorted(widths):
            height = max(4, round(image.height * width / image.width))
            rgb = image.resize((width, height), Image.Resampling.NEAREST)
            mask = np.asarray(visible.resize((width, height), Image.Resampling.NEAREST))
            count = np.count_nonzero(mask)
            if count >= 12:
                prepared.append((bgr(rgb), mask, count, minimum))
        if not prepared:
            raise ValueError("The template needs more visible pixels.")
        self.templates.extend(prepared)
        for template, _, _, _ in prepared:
            self.gray_templates[id(template)] = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    def acquire(self, frame, left, top):
        """Shortlist positions cheaply, then score with the original masked metric."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        integral = None
        if self.require_red:
            blue, green, red = cv2.split(frame.astype(np.int16))
            colored = ((red-green > 17) & (red-blue > 16) & (green < red*.72)).astype(np.uint8)
            integral = cv2.integral(colored)
        best = None
        for candidate in self.templates:
            template, mask, count, minimum = candidate
            height, width = mask.shape
            if height > gray.shape[0] or width > gray.shape[1]:
                continue
            coarse = cv2.matchTemplate(gray, self.gray_templates[id(template)], cv2.TM_CCOEFF_NORMED)
            np.nan_to_num(coarse, copy=False, nan=-1, posinf=-1, neginf=-1)
            if integral is not None:
                reds = integral[height:,width:] - integral[:-height,width:] - integral[height:,:-width] + integral[:-height,:-width]
                coarse[reds < max(6, height*width*.015)] = -2
            for _ in range(3):
                _, confidence, _, (x,y) = cv2.minMaxLoc(coarse)
                if confidence < .1:
                    break
                patch = frame[y:y+height,x:x+width]
                error = float(cv2.matchTemplate(patch,template,cv2.TM_SQDIFF,mask=mask)[0,0])
                contrast = float(cv2.matchTemplate(patch,template,cv2.TM_CCOEFF_NORMED,mask=mask)[0,0])
                if not np.isfinite(contrast):
                    contrast = 0
                score = .5*(1-np.sqrt(max(0,error)/(count*3))/255)+.25*(1+np.clip(contrast,-1,1))
                if score >= minimum and (best is None or score > best.score):
                    best = Match(float(score),(x+left,y+top,x+left+width,y+top+height))
                    self.preferred = candidate
                radius = max(3,width//3)
                coarse[max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1] = -2
        return best

    def find(self, frame, region=None, expected_width=None) -> Match | None:
        left, top = 0, 0
        if region is not None:
            left, top, right, bottom = region
            frame = frame[top:bottom, left:right]
        if self.fast_acquisition and expected_width is None:
            return self.acquire(frame,left,top)
        best = None
        # Once a float is acquired, tracking stays on its reference and scale.
        # Rechecking every variant during the bite frame can consume the reel
        # window. A lost lock returns to the full acquisition search.
        candidates = [self.preferred] if expected_width is not None and self.preferred is not None else self.templates
        for candidate in candidates:
            template, mask, count, minimum = candidate
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
            if score >= minimum and (best is None or score > best.score):
                best = Match(score, (x + left, y + top, x + left + width, y + top + height))
                preferred = candidate
        if best is not None:
            self.preferred = preferred
        return best
