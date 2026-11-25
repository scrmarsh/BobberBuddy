import unittest
from unittest.mock import patch
import cv2
import numpy as np
from app_info import ASSETS
from matcher import Matcher


class TrackingLatencyTests(unittest.TestCase):
    def test_fast_acquisition_ignores_gray_decoy_and_finds_red_float(self):
        reference = cv2.imread(str(ASSETS/'bedrock-topdown-bobber.png'),cv2.IMREAD_UNCHANGED)
        frame = np.full((110,180,3),(60,50,40),np.uint8)
        mask = reference[:,:,3] > 0
        height,width = reference.shape[:2]
        color = frame[20:20+height,110:110+width]
        color[mask] = reference[:,:,:3][mask]
        gray = cv2.cvtColor(cv2.cvtColor(reference[:,:,:3],cv2.COLOR_BGR2GRAY),cv2.COLOR_GRAY2BGR)
        decoy = frame[20:20+height,20:20+width]
        decoy[mask] = gray[mask]
        matcher = Matcher(ASSETS/'bedrock-topdown-bobber.png',fast_acquisition=True,require_red=True)
        match = matcher.find(frame)
        self.assertGreater(match.score,.95)
        self.assertGreater(match.box[0],100)
        color[:] = (60,50,40)
        self.assertIsNone(matcher.find(frame))

    def test_locked_tracking_uses_acquired_reference_and_preserves_location(self):
        reference = cv2.imread(str(ASSETS/'bedrock-topdown-bobber.png'), cv2.IMREAD_UNCHANGED)
        frame = np.full((100,120,3), (60,50,40), np.uint8)
        target = frame[20:20+reference.shape[0],40:40+reference.shape[1]]
        mask = reference[:,:,3] > 0
        target[mask] = reference[:,:,:3][mask]
        matcher = Matcher(ASSETS/'bedrock-bobber.png',
                          variants=((ASSETS/'bedrock-topdown-bobber.png',.89),))
        with patch('matcher.cv2.matchTemplate', wraps=cv2.matchTemplate) as correlate:
            acquired = matcher.find(frame)
            acquisition_calls = correlate.call_count
            correlate.reset_mock()
            tracked = matcher.find(frame, (30,10,90,80), acquired.box[2]-acquired.box[0])
            self.assertEqual(tracked.box, acquired.box)
            self.assertGreater(tracked.score,.95)
            self.assertLess(correlate.call_count, acquisition_calls//2)

