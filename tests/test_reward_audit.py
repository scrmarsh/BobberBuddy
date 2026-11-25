import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from bite import FishingFrame
from fishing_audit import FishingAudit, xp_mask, loot_mask


class RewardAuditTests(unittest.TestCase):
    def test_visible_loot_can_confirm_without_an_xp_change_once(self):
        with tempfile.TemporaryDirectory() as root:
            audit = FishingAudit(root)
            xp = np.zeros((24,400,3), np.uint8)
            before = np.zeros((40,400,3), np.uint8)
            after = before.copy()
            after[20:30,200:210] = 255
            audit.hud = Mock(return_value=xp)
            audit.loot_hud = Mock(return_value=after)
            audit.pending = (1, 0, xp_mask(xp), loot_mask(before))
            with patch('fishing_audit.time.monotonic', return_value=1):
                audit.tick(None, None, FishingFrame(None, reels=1))
            with patch('fishing_audit.time.monotonic', return_value=2):
                audit.tick(None, None, FishingFrame(None, reels=1))
            self.assertEqual(audit.confirmed, 1)
            self.assertEqual(audit.loot_confirmed, 1)
            self.assertEqual(audit.xp_confirmed, 0)

    def test_reel_counter_without_reward_does_not_pass(self):
        with tempfile.TemporaryDirectory() as root:
            audit = FishingAudit(root)
            xp = np.zeros((24,400,3), np.uint8)
            loot = np.zeros((40,400,3), np.uint8)
            audit.hud = Mock(return_value=xp)
            audit.loot_hud = Mock(return_value=loot)
            audit.pending = (1, 0, xp_mask(xp), loot_mask(loot))
            with patch('fishing_audit.time.monotonic', return_value=10):
                audit.tick(None, None, FishingFrame(None, reels=1))
            self.assertEqual(audit.confirmed, 0)
            self.assertEqual(audit.unconfirmed, 1)
            self.assertEqual(audit.streak, 0)
