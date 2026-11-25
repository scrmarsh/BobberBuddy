import unittest
from collections import deque
from itertools import count
from unittest.mock import Mock, patch

import numpy as np
from bite import FishingFrame
from calibrate import watch
from matcher import Match


class FirstCastTests(unittest.TestCase):
    def test_cast_float_wins_over_an_ambiguous_stowed_rod_match(self):
        game = Mock(identity=(1, 2))
        game.focused.return_value = True
        game.movement_pressed.return_value = False
        rod, held, uncast = Mock(), Mock(), Mock()
        rod.matches.return_value = held.visible.return_value = uncast.visible.return_value = True
        detector = Mock(stable=8)
        detector.update.return_value = FishingFrame(Match(.95, (20, 20, 32, 36)), locked=True)
        stop = Mock()
        stop.is_set.side_effect = [False] * 50 + [True]
        frame = np.zeros((64, 64, 4), np.uint8)
        frame[25:30, 22:30] = (20, 20, 180, 255)
        latest = deque(maxlen=1)
        with (patch('calibrate.mss.MSS') as screen,
              patch('calibrate.client_area', return_value=dict(left=0, top=0, width=640, height=480)),
              patch('calibrate.read_selected_slot'),
              patch('calibrate.water_in_cast_area', return_value=True),
              patch('calibrate.time.monotonic', side_effect=count(1, .2))):
            screen.return_value.__enter__.return_value.grab.return_value = frame
            result = watch(None, detector, game, True, stop, latest, rod, held, uncast)
        self.assertEqual(result.casts, 0)
        self.assertEqual(game.use_rod.call_count, 0)
        self.assertTrue(latest[-1].locked)

    def test_stowed_rod_casts_despite_false_float_in_water(self):
        game = Mock(identity=(1, 2))
        game.focused.return_value = True
        game.movement_pressed.return_value = False
        game.use_rod.return_value = True
        rod = Mock()
        rod.matches.return_value = True
        held = Mock()
        held.visible.return_value = True
        uncast = Mock()
        uncast.visible.return_value = True
        detector = Mock(stable=8)
        detector.update.return_value = FishingFrame(Match(.95, (80, 60, 90, 70)), locked=True)
        stop = Mock()
        stop.is_set.side_effect = [False] * 50 + [True]
        bounds = dict(left=0, top=0, width=640, height=480)
        with (patch('calibrate.mss.MSS') as screen,
              patch('calibrate.client_area', return_value=bounds),
              patch('calibrate.read_selected_slot'),
              patch('calibrate.water_in_cast_area', return_value=True),
              patch('calibrate.time.monotonic', side_effect=count(1, .2))):
            screen.return_value.__enter__.return_value.grab.return_value = np.zeros((64,64,4), np.uint8)
            result = watch(None, detector, game, True, stop, deque(maxlen=1), rod, held, uncast)
        self.assertGreater(result.casts, 0)
        self.assertEqual(result.reels, 0)
