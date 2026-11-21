import cv2
import numpy as np

from game_window import client_area


def selected_slot(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 1]
    highlight = (gray >= 164) & (saturation < 100)
    height, width = gray.shape
    candidates = []
    bright = highlight & (gray >= 174)
    # A slot needs a continuous 13-pixel top edge. Reject rows with
    # no such edge before extracting individual horizontal runs.
    kernel = np.ones((1, 13), dtype=np.uint8)
    possible = cv2.erode(bright.astype(np.uint8), kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    for top in np.flatnonzero(possible.any(axis=1)):
        edges = np.flatnonzero(np.diff(np.r_[False, bright[top], False]))
        for left, right in edges.reshape(-1, 2):
            size = int(right - left)
            bottom = top + round(size * 0.88)
            if not (13 <= size <= min(150, width // 8) and 0 <= height - bottom <= max(34, height // 3)):
                continue
            side = max(1, size // 18)
            middle = highlight[top + size // 6:top + size * 4 // 5]
            if middle[:, left:left + side].mean() < 0.70 or middle[:, right - side:right].mean() < 0.70:
                continue
            candidates.append((size, top, int(left)))
    if not candidates:
        return None
    size, top, left = max(candidates, key=lambda candidate: (candidate[0], -candidate[1]))
    icon = frame[top + round(size * 0.14):top + round(size * 0.84),
                 left + round(size * 0.13):left + round(size * 0.87)]
    return cv2.resize(icon, (40, 40), interpolation=cv2.INTER_AREA)


def read_selected_slot(screen, hwnd):
    area = client_area(hwnd)
    if area is None or area["width"] < 240 or area["height"] < 160:
        return None
    width = min(area["width"], max(450, area["width"] * 3 // 5))
    height = max(105, area["height"] // 5)
    strip = dict(left=area["left"] + (area["width"] - width) // 2,
                 top=area["top"] + area["height"] - height, width=width, height=height)
    return selected_slot(cv2.cvtColor(np.asarray(screen.grab(strip)), cv2.COLOR_BGRA2BGR))


class HotbarRod:
    def __init__(self, reference):
        self.reference = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)

    def matches(self, slot):
        if slot is None:
            return False
        gray = cv2.cvtColor(slot, cv2.COLOR_BGR2GRAY)
        score = cv2.matchTemplate(gray, self.reference, cv2.TM_CCOEFF_NORMED).item()
        return score >= 0.84
