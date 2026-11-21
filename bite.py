from dataclasses import dataclass
from math import hypot

import cv2
import numpy as np

from matcher import Match


@dataclass(frozen=True)
class FishingFrame:
    match: Match | None
    watching: bool = True
    bubbles: bool = False
    bite: bool = False
    reels: int = 0
    bites: int = 0
    recasts: int = 0
    casts: int = 0
    locked: bool = False
    rod_selected: bool = True
    rod_visible: bool = True


class BiteDetector:
    def __init__(self, matcher, threshold):
        self.matcher = matcher
        self.threshold = threshold
        self.reset()

    def reset(self):
        self.anchor = None
        self.stable = 0
        self.previous = None
        self.wake_distance = None
        self.wake_offset = None
        self.approaches = 0
        self.last_particle_at = float("-inf")
        self.wake_until = float("-inf")
        self.wake_arrived_at = float("-inf")
        self.drop_frames = 0
        self.weak_frames = 0
        self.rearm_at = None
        self.floating_since = None
        self.float_red_pixels = 0
        self.faded_frames = 0

    def update(self, frame, now, *, acquisition_region=None):
        previous = self.previous
        self.previous = frame
        if previous is not None:
            motion = cv2.absdiff(previous, frame)
            if np.mean(np.max(motion, axis=2) > 32) > 0.32:
                self.reset()
                self.previous = frame
                previous = None

        if self.anchor is None:
            hit = self.matcher.find(frame, acquisition_region)
            if hit and hit.score >= self.threshold:
                self.anchor = hit.box
                self.stable = 1
                self.floating_since = now
            return FishingFrame(hit)

        x1, y1, x2, y2 = self.anchor
        width, height = x2 - x1, y2 - y1
        x, y = (x1 + x2) / 2, (y1 + y2) / 2
        radius = max(13, width)
        region = (max(0, x1 - radius), max(0, y1 - radius),
                  min(frame.shape[1], x2 + radius), min(frame.shape[0], y2 + height + radius))
        hit = self.matcher.find(frame, region, width)
        visible = hit is not None and hit.score >= self.threshold
        if self.stable >= 4 and previous is not None and self.rearm_at is None:
            self.observe_wake(frame, previous, x, y, width, now)
        bubbles = now <= self.wake_until
        if visible:
            a, b, c, d = hit.box
            dx, dy = (a + c) / 2 - x, (b + d) / 2 - y
            aligned = abs(dx) <= max(4, width * 0.45)
            dipped = aligned and dy >= max(4, height * 0.27)
            deep_dip = aligned and dy >= max(8, height * 0.50)
            floating = aligned and abs(dy) <= max(3, height * 0.20)
        else:
            dipped = deep_dip = floating = False

        if self.rearm_at is not None:
            # A missed bite can end with the same float returning to the surface.
            if now >= self.rearm_at:
                self.reset()
            return FishingFrame(hit)

        recent_wake = now - self.wake_arrived_at <= 1.5
        settled = self.floating_since is not None and now - self.floating_since >= 0.55
        late_wake = recent_wake and not bubbles and self.float_red_pixels >= 8
        appeared_wake = (bubbles or late_wake) and self.float_red_pixels >= 8
        if floating or appeared_wake:
            patch = frame[y1:y2, x1:x2].astype(np.int16)
            blue, green, red = cv2.split(patch)
            red_pixels = np.count_nonzero((red - green > 17) & (red - blue > 16))
            if floating and not recent_wake:
                self.float_red_pixels = max(self.float_red_pixels, red_pixels)
            if (self.stable >= 4 and settled and bubbles and appeared_wake and visible and aligned and dy >= 5
                    and red_pixels <= self.float_red_pixels * 0.25):
                self.rearm_at = now + 0.9
                return FishingFrame(hit, bubbles=True, bite=True)
            if self.stable >= 4 and settled and late_wake:
                if red_pixels <= self.float_red_pixels * 0.25 and (not visible or (aligned and dy >= 3.5)):
                    self.faded_frames += 1
                    if self.faded_frames >= 2:
                        self.rearm_at = now + 0.9
                        return FishingFrame(hit, bubbles=True, bite=True)
                    return FishingFrame(hit, bubbles=True)
                self.faded_frames = 0

        if floating:
            self.drop_frames = 0
            self.weak_frames = 0
            self.stable += 1
            # Anchor only during acquisition so a real downward move is not absorbed.
            if self.stable <= 4:
                self.anchor = hit.box
            return FishingFrame(hit, bubbles=bubbles)

        wake_and_submerged = bubbles and not visible
        if (self.stable >= 4 and settled and visible and aligned and dy > 0
                and (bubbles and not dipped or late_wake and not deep_dip)):
            # A float can move through the gap between its resting tolerance
            # and the dip threshold. Keep the wake and anchor until it dips.
            return FishingFrame(hit, bubbles=bubbles)
        sustained_lock = settled and self.stable >= 8 and now - self.floating_since >= 1.2
        dip_confirmed = (bubbles and dipped) or (sustained_lock and deep_dip)
        if self.stable >= 4 and settled and (dip_confirmed or wake_and_submerged):
            self.drop_frames += 1
            # A wake reaching a locked float and then hiding it is already two signals.
            # Waiting for another capture can miss the reel window on a slow frame.
            if wake_and_submerged or (bubbles and deep_dip) or self.drop_frames >= 2:
                self.rearm_at = now + 0.9
                return FishingFrame(hit, bubbles=bubbles, bite=True)
        else:
            self.drop_frames = 0
            self.weak_frames += 1
            if visible or self.weak_frames > 2:
                self.reset()
                self.previous = frame
                self.anchor = hit.box if visible else None
                self.stable = int(visible)
                self.floating_since = now if visible else None
        return FishingFrame(hit, bubbles=bubbles)

    def observe_wake(self, frame, previous, x, y, width, now):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        blue = cv2.inRange(hsv, (92, 115, 145), (135, 255, 255))
        changed = np.max(cv2.absdiff(frame, previous), axis=2) >= 35
        blue[~changed] = 0
        count, _, stats, centers = cv2.connectedComponentsWithStats(blue)
        limit = max(75, width * 5)
        particles = [
            (hypot(cx - x, cy - y), cx - x, cy - y)
            for index, (cx, cy) in enumerate(centers[1:count], 1)
            if 2 <= stats[index, cv2.CC_STAT_AREA] <= max(35, width * width * 0.6)
            and abs(cy - y) <= limit * 0.65
            and stats[index, cv2.CC_STAT_HEIGHT] <= max(3, 1.6 * stats[index, cv2.CC_STAT_WIDTH])
        ]
        # The approach can be obscured by shaded water while the burst at
        # the hook remains visible. It still needs a bobber dip or submersion.
        splash_radius = max(32, width * 3.6)
        cx, cy = round(x), round(y)
        core = blue[max(0, cy - width):cy + width + 1, max(0, cx - width):cx + width + 1]
        if (now > self.wake_until and self.float_red_pixels >= 8
                and sum(distance <= splash_radius for distance, _, _ in particles) >= 4
                and cv2.countNonZero(core) >= max(12, width * width * 0.25)):
            self.arm_wake(now)
            return
        recent = now - self.last_particle_at <= 0.45
        if not recent:
            self.wake_distance = None
            self.wake_offset = None
            self.approaches = 0
        nearby = [(distance, dx, dy) for distance, dx, dy in particles if width * 0.5 <= distance <= limit]
        if not nearby:
            return
        if self.wake_offset is not None:
            wx, wy = self.wake_offset
            # A trail approaches from one direction. Separate rain splashes can
            # happen to land successively closer on unrelated sides of the float.
            nearby = [(distance, dx, dy) for distance, dx, dy in nearby
                      if dx * wx + dy * wy >= 0.45 * distance * self.wake_distance]
            if not nearby:
                return
        nearest, dx, dy = min(nearby)
        self.last_particle_at = now
        if self.wake_distance is None:
            self.wake_distance = nearest
            self.wake_offset = (dx, dy)
        elif nearest < self.wake_distance - max(2, width * 0.18):
            self.approaches += 1
            self.wake_distance = nearest
            self.wake_offset = (dx, dy)
        if self.approaches >= 2 and nearest <= max(24, width * 1.6) and now > self.wake_until:
            # Only the first arrival opens the window. Repeated splashes at the
            # same distance cannot keep the detector armed indefinitely.
            self.arm_wake(now)

    def arm_wake(self, now):
        self.wake_until = now + 0.65
        self.wake_arrived_at = now
        self.wake_distance = None
        self.wake_offset = None
        self.approaches = 0
