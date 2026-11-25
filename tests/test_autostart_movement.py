import unittest
from collections import deque
from itertools import count
from unittest.mock import Mock, patch
import cv2
import numpy as np
from bite import FishingFrame
from calibrate import watch, water_in_cast_area


class AutostartMovementTests(unittest.TestCase):
    def run_watch(self, frames, stowed=True):
        game = Mock(identity=(1,2))
        game.focused.return_value = True
        game.movement_pressed.return_value = False
        game.use_rod.return_value = True
        rod, held, uncast = Mock(), Mock(), Mock()
        rod.matches.return_value = held.visible.return_value = True
        uncast.visible.side_effect = lambda *_: stowed and game.use_rod.call_count == 0
        stop = Mock()
        stop.is_set.side_effect = [False]*len(frames)+[True]
        detector = Mock(stable=0)
        detector.update.return_value = FishingFrame(None)
        latest = deque(maxlen=len(frames))
        with (patch('calibrate.mss.MSS') as capture,
              patch('calibrate.client_area',return_value=dict(left=0,top=0,width=640,height=480)),
              patch('calibrate.read_selected_slot'),
              patch('calibrate.water_in_cast_area',return_value=True),
              patch('calibrate.time.monotonic',side_effect=count(1,.2))):
            capture.return_value.__enter__.return_value.grab.side_effect = frames
            result = watch(None,detector,game,True,stop,latest,rod,held,uncast)
        return game,result,latest

    def test_camera_only_turning_waits_then_autostarts_when_still(self):
        a = np.zeros((64,64,4),np.uint8)
        b = np.full_like(a,150)
        game,result,latest = self.run_watch([a,b]*60)
        self.assertEqual(game.use_rod.call_count,0)
        self.assertTrue(any(frame.waiting=='Stand still to resume fishing.' for frame in latest))
        game,result,_ = self.run_watch([a,b]*20+[b]*80)
        self.assertEqual(result.casts,1)

    def test_unknown_line_gets_only_one_probe_without_a_visual_lock(self):
        frame = np.zeros((64,64,4),np.uint8)
        game,result,_ = self.run_watch([frame]*120,stowed=False)
        self.assertEqual(game.use_rod.call_count,1)
        self.assertEqual((result.casts,result.reels),(0,0))

    def test_water_elsewhere_does_not_override_dry_crosshair(self):
        hsv = np.full((100,120,3),(16,100,125),np.uint8)
        hsv[:55] = (100,150,125)
        self.assertFalse(water_in_cast_area(cv2.cvtColor(hsv,cv2.COLOR_HSV2BGR),.75))
        hsv[60:] = (100,150,125)
        self.assertTrue(water_in_cast_area(cv2.cvtColor(hsv,cv2.COLOR_HSV2BGR),.75))
