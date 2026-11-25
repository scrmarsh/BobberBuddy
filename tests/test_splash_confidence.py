import unittest
from unittest.mock import Mock
import numpy as np
from bite import BiteDetector
from matcher import Match


class SplashConfidenceTests(unittest.TestCase):
    def test_low_confidence_with_red_float_is_not_a_bite(self):
        matcher = Mock()
        matcher.find.return_value = Match(.80, (30, 34, 42, 50))
        detector = BiteDetector(matcher, .84)
        detector.anchor = (30, 30, 42, 46)
        detector.stable = 12
        detector.floating_since = 0
        detector.float_red_pixels = 40
        detector.wake_until = 10
        detector.observe_wake = Mock()
        frame = np.full((80, 80, 3), (90, 75, 50), dtype=np.uint8)
        frame[35:40, 33:41] = (30, 30, 180)
        detector.previous = frame.copy()
        self.assertFalse(detector.update(frame, 5).bite)
        self.assertIsNotNone(detector.anchor)
        submerged = frame.copy()
        submerged[35:40, 33:41] = (90,75,50)
        self.assertTrue(detector.update(submerged, 5.1).bite)
