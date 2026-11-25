"""Local evidence for sustained fishing tests; reel inputs are not item counts."""
import json
import time
from datetime import datetime
from pathlib import Path
from collections import deque

import cv2
import numpy as np
import mss


def xp_mask(image):
    pixels = cv2.resize(image[:, :, :3], (400, 24), interpolation=cv2.INTER_AREA).astype(np.int16)
    blue, green, red = cv2.split(pixels)
    return (green > 90) & (green - red > 45) & (green - blue > 35)


def loot_mask(image):
    # Stack counts in reward slots 2–5; ignore enchanted books/rods in
    # later slots, whose shimmer can change without a catch.
    height, width = image.shape[:2]
    counts = image[height//2:, :width*4//7, :3]
    pixels = cv2.resize(counts, (240, 24), interpolation=cv2.INTER_AREA).astype(np.int16)
    return (pixels.min(axis=2) > 185) & (pixels.max(axis=2)-pixels.min(axis=2) < 40)


class FishingAudit:
    def __init__(self, root):
        self.root = Path(root) / 'fishing-audit' / datetime.now().strftime('%Y%m%d-%H%M%S')
        self.root.mkdir(parents=True, exist_ok=True)
        self.started = time.monotonic()
        self.last_write = 0
        self.last_sample = 0
        self.pending = None
        self.confirmed = self.streak = self.best_streak = self.unconfirmed = 0
        self.xp_confirmed = self.loot_confirmed = 0
        self.before_images = None
        self.cached_hud = None
        self.state = None
        self.frames = deque(maxlen=50)
        self.bite_frames = None
        self.saved_search_frame = False
        self.write('start', note='Reels require XP or visible hotbar reward evidence; inputs alone are not catches.')

    def write(self, kind, **fields):
        try:
            with (self.root / 'events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(time=datetime.now().astimezone().isoformat(),
                                             elapsed=round(time.monotonic()-self.started, 2),
                                             event=kind, **fields)) + '\n')
        except OSError:
            pass

    def hud(self, screen, bounds):
        if not bounds:
            return None
        region = dict(left=bounds['left'] + round(bounds['width'] * .30),
                      top=bounds['top'] + round(bounds['height'] * .885),
                      width=round(bounds['width'] * .40), height=max(8, round(bounds['height'] * .022)))
        return np.asarray(screen.grab(region))[:, :, :3]

    def before_reel(self, screen, bounds, number):
        try:
            if self.cached_hud is not None and time.monotonic()-self.cached_hud[2] <= 1:
                image, loot, _ = self.cached_hud
            else:
                image = self.hud(screen, bounds)
                loot = self.loot_hud(screen, bounds)
            if image is not None and loot is not None:
                if self.pending is not None:
                    self.write('reward_unconfirmed_before_next_reel', number=self.pending[0])
                    self.unconfirmed += 1
                    self.streak = 0
                self.pending = (number, time.monotonic(), xp_mask(image), loot_mask(loot))
                self.before_images = (image, loot)
                self.bite_frames = list(self.frames)
        except (OSError, ValueError, mss.ScreenShotError, cv2.error):
            self.pending = None

    def loot_hud(self, screen, bounds):
        if not bounds:
            return None
        # Monitor reward slots between the selected rod and the final food
        # slot, without opening or changing the inventory.
        region = dict(left=bounds['left']+round(bounds['width']*.355),
                      top=bounds['top']+round(bounds['height']*.94),
                      width=round(bounds['width']*.29), height=max(8,round(bounds['height']*.05)))
        return np.asarray(screen.grab(region))[:, :, :3]

    def observe(self, frame, event, detector):
        if not self.saved_search_frame:
            cv2.imwrite(str(self.root / 'initial-search.png'), frame)
            self.saved_search_frame = True
        self.frames.append((frame.copy(), dict(score=event.match.score if event.match else None,
                           box=event.match.box if event.match else None, bite=event.bite,
                           bubbles=event.bubbles, stable=detector.stable, anchor=detector.anchor)))

    def reel(self, number, clicked, **timing):
        self.write('reel', number=number, clicked=clicked, **timing)
        if clicked and self.before_images is not None:
            for label, image in zip(('xp', 'loot'), self.before_images):
                cv2.imwrite(str(self.root / f'{number:04d}-before-{label}.png'), image)
        if not clicked:
            self.pending = None
            self.streak = 0

    def tick(self, screen, bounds, event):
        now = time.monotonic()
        state = ('focus_paused' if not event.watching else 'rod_not_selected' if not event.rod_selected
                 else 'held_rod_not_visible' if not event.rod_visible else event.waiting
                 or ('locked' if event.locked else 'scanning'))
        if state != self.state:
            self.state = state
            self.write('state', state=state)
        if not self.pending and event.watching and now-self.last_sample >= .25:
            self.last_sample = now
            try:
                image, loot = self.hud(screen, bounds), self.loot_hud(screen, bounds)
                if image is not None and loot is not None:
                    self.cached_hud = (image.copy(), loot.copy(), now)
            except (OSError, ValueError, mss.ScreenShotError, cv2.error):
                self.cached_hud = None
        if self.pending and event.watching and now-self.last_sample >= .25:
            self.last_sample = now
            number, began, baseline, baseline_loot = self.pending
            if now-began >= .6:
                try:
                    image = self.hud(screen, bounds)
                    changed = max(0, int(np.count_nonzero(xp_mask(image)))-int(np.count_nonzero(baseline))) if image is not None else 0
                    loot = self.loot_hud(screen, bounds)
                    loot_changed = int(np.count_nonzero(loot_mask(loot) != baseline_loot)) if loot is not None else 0
                    if changed >= 20 or loot_changed >= 8:
                        cv2.imwrite(str(self.root / f'{number:04d}-after.png'), image)
                        cv2.imwrite(str(self.root / f'{number:04d}-after-loot.png'), loot)
                        self.confirmed += 1
                        self.xp_confirmed += int(changed >= 20)
                        self.loot_confirmed += int(loot_changed >= 8)
                        self.streak += 1
                        self.best_streak = max(self.streak, self.best_streak)
                        self.write('reward_corroborated', number=number, xp_changed_pixels=changed,
                                   loot_changed_pixels=loot_changed, streak=self.streak)
                        self.pending = None
                    elif now-began > 8:
                        self.unconfirmed += 1
                        self.streak = 0
                        self.write('reward_unconfirmed', number=number)
                        if self.bite_frames:
                            np.savez_compressed(self.root / f'{number:04d}-unconfirmed.npz',
                                                frames=np.array([item[0] for item in self.bite_frames]),
                                                metadata=json.dumps([item[1] for item in self.bite_frames]))
                        if image is not None:
                            cv2.imwrite(str(self.root / f'{number:04d}-unconfirmed-hud.png'), image)
                        self.pending = None
                except (OSError, ValueError, mss.ScreenShotError, cv2.error):
                    pass
        if now-self.last_write >= 1:
            self.last_write = now
            live = dict(elapsed=round(now-self.started), state=state, reels=event.reels,
                        match_score=event.match.score if event.match else None,
                        match_box=event.match.box if event.match else None,
                        bites=event.bites, recasts=event.recasts, initial_casts=event.casts,
                        reward_corroborated_reels=self.confirmed, reward_streak=self.streak,
                        best_reward_streak=self.best_streak, xp_corroborated_reels=self.xp_confirmed,
                        loot_corroborated_reels=self.loot_confirmed, reward_unconfirmed=self.unconfirmed)
            try:
                pending = self.root / 'live.tmp'
                pending.write_text(json.dumps(live, indent=2), encoding='utf-8')
                pending.replace(self.root / 'live.json')
            except OSError:
                pass
