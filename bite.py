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
        self.approaches = 0
        self.last_particle_at = float("-inf")
        self.wake_until = float("-inf")
        self.drop_frames = 0
        self.weak_frames = 0
        self.rearm_at = None

    def update(self, frame, now):
        previous = self.previous
        self.previous = frame
        if previous is not None:
            motion = cv2.absdiff(previous, frame)
            if np.mean(np.max(motion, axis=2) > 32) > 0.32:
                self.reset()
                self.previous = frame
                previous = None

        if self.anchor is None:
            hit = self.matcher.find(frame)
            if hit and hit.score >= self.threshold:
                self.anchor = hit.box
                self.stable = 1
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
            floating = aligned and abs(dy) <= max(3, height * 0.20)
        else:
            dipped = floating = False

        if self.rearm_at is not None:
            # A missed bite can end with the same float returning to the surface.
            if now >= self.rearm_at:
                self.reset()
            return FishingFrame(hit)

        if floating:
            self.drop_frames = 0
            self.weak_frames = 0
            self.stable += 1
            # Anchor only during acquisition so a real downward move is not absorbed.
            if self.stable <= 4:
                self.anchor = hit.box
            return FishingFrame(hit, bubbles=bubbles)

        if self.stable >= 4 and (dipped or (not visible and (bubbles or self.drop_frames))):
            self.drop_frames += 1
            if self.drop_frames >= 2:
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
        return FishingFrame(hit, bubbles=bubbles)

    def observe_wake(self, frame, previous, x, y, width, now):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        blue = cv2.inRange(hsv, (92, 115, 145), (135, 255, 255))
        changed = np.max(cv2.absdiff(frame, previous), axis=2) >= 35
        blue[~changed] = 0
        count, _, stats, centers = cv2.connectedComponentsWithStats(blue)
        limit = max(75, width * 5)
        distances = [
            hypot(cx - x, cy - y)
            for index, (cx, cy) in enumerate(centers[1:count], 1)
            if 2 <= stats[index, cv2.CC_STAT_AREA] <= max(35, width * width * 0.6)
            and abs(cy - y) <= limit * 0.65
        ]
        recent = now - self.last_particle_at <= 0.45
        if not recent:
            self.wake_distance = None
            self.approaches = 0
        nearest = min((distance for distance in distances if width * 0.5 <= distance <= limit), default=None)
        if nearest is None:
            return
        self.last_particle_at = now
        if self.wake_distance is None:
            self.wake_distance = nearest
        elif nearest < self.wake_distance - max(2, width * 0.18):
            self.approaches += 1
            self.wake_distance = nearest
        if self.approaches >= 2 and nearest <= max(24, width * 1.6):
            self.wake_until = now + 0.65
