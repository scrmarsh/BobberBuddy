import cv2
import numpy as np

from game_window import client_area


VIEW_SIZE = (640, 360)


def reference_from_region(image, box):
    width, height = image.size
    x1, y1, x2, y2 = box
    left, right = (round(x * VIEW_SIZE[0] / width) for x in (x1, x2))
    top, bottom = (round(y * VIEW_SIZE[1] / height) for y in (y1, y2))
    w, h = right - left, bottom - top
    if not (12 <= w <= 120 and 12 <= h <= 150):
        raise ValueError("Select a tight box around the reel and handle.")
    if left < VIEW_SIZE[0] / 2 < right or bottom < VIEW_SIZE[1] * 0.40:
        raise ValueError("Select the held rod to one side of the crosshair.")
    view = cv2.resize(np.asarray(image.convert("RGB")), VIEW_SIZE, interpolation=cv2.INTER_AREA)
    reference = cv2.cvtColor(view, cv2.COLOR_RGB2BGRA)
    mask = np.zeros(reference.shape[:2], np.uint8)
    cv2.ellipse(mask, (left + w // 2, top + h // 2), (max(1, w // 2 - 1), max(1, h // 2 - 1)),
                0, 0, 360, 255, -1)
    reference[:, :, 3] = mask
    reference[mask == 0] = 0
    return reference


class HeldRod:
    """Match the first-person rod independently of the hotbar."""

    def __init__(self, reference):
        if reference is None or reference.shape != (VIEW_SIZE[1], VIEW_SIZE[0], 4):
            raise ValueError("Learn the held rod from a first-person screenshot.")
        x, y, width, height = cv2.boundingRect(reference[:, :, 3])
        if not (12 <= width <= 120 and 12 <= height <= 150):
            raise ValueError("The held-rod reference is incomplete.")
        self.bounds = (max(0, x - 48), max(0, y - 55),
                       min(VIEW_SIZE[0], x + width + 48), min(VIEW_SIZE[1], y + height + 55))
        crop = reference[y:y + height, x:x + width]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGRA2GRAY)
        alpha = crop[:, :, 3]
        if np.std(gray[alpha > 0]) < 10:
            raise ValueError("Choose a region showing both the reel and the handle.")
        self.templates = []
        for scale in (1.0, 0.86, 1.16):
            size = (round(width * scale), round(height * scale))
            mask = cv2.resize(alpha, size, interpolation=cv2.INTER_NEAREST)
            template = cv2.resize(gray, size, interpolation=cv2.INTER_LINEAR)
            self.templates.append((template, mask))

    def matches(self, view):
        gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
        for template, mask in self.templates:
            if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
                continue
            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED, mask=mask)
            if np.max(result, where=np.isfinite(result), initial=-1.0) >= 0.82:
                return True
        return False

    def visible(self, screen, hwnd):
        area = client_area(hwnd)
        if area is None or area["width"] < 240 or area["height"] < 160:
            return False
        left, top, right, bottom = self.bounds
        x1, x2 = (round(x * area["width"] / VIEW_SIZE[0]) for x in (left, right))
        y1, y2 = (round(y * area["height"] / VIEW_SIZE[1]) for y in (top, bottom))
        region = dict(left=area["left"] + x1, top=area["top"] + y1, width=x2 - x1, height=y2 - y1)
        pixels = cv2.cvtColor(np.asarray(screen.grab(region)), cv2.COLOR_BGRA2BGR)
        view = cv2.resize(pixels, (right - left, bottom - top), interpolation=cv2.INTER_AREA)
        return self.matches(view)
