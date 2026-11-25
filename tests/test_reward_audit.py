import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from bite import FishingFrame
from fishing_audit import FishingAudit, xp_mask, loot_mask


class RewardAuditTests(unittest.TestCase):
    def test_enchantment_shimmer_and_fading_xp_are_not_rewards(self):
        for fading_xp in (False, True):
            with self.subTest(fading_xp=fading_xp), tempfile.TemporaryDirectory() as root:
                audit = FishingAudit(root)
                xp = np.zeros((24,400,3),np.uint8)
                baseline = xp.copy()
                if fading_xp:
                    baseline[:,:100] = (20,180,20)
                loot = np.zeros((40,400,3),np.uint8)
                shimmer = loot.copy()
                shimmer[20:35,330:350] = 255
                audit.hud = Mock(return_value=xp)
                audit.loot_hud = Mock(return_value=shimmer)
                audit.pending = (1,0,xp_mask(baseline),loot_mask(loot))
                with patch('fishing_audit.time.monotonic',return_value=10):
                    audit.tick(None,None,FishingFrame(None,reels=1))
                self.assertEqual(audit.confirmed,0)
                self.assertEqual(audit.unconfirmed,1)

    def test_recent_cached_hud_does_not_delay_reel_with_more_captures(self):
        with tempfile.TemporaryDirectory() as root:
            audit = FishingAudit(root)
            xp = np.zeros((24,400,3),np.uint8)
            loot = np.zeros((40,400,3),np.uint8)
            audit.cached_hud = (xp,loot,.5)
            audit.hud = Mock(side_effect=AssertionError('Unexpected capture during bite'))
            audit.loot_hud = Mock(side_effect=AssertionError('Unexpected capture during bite'))
            with patch('fishing_audit.time.monotonic',return_value=1):
                audit.before_reel(None,None,1)
            self.assertEqual(audit.pending[0],1)

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
