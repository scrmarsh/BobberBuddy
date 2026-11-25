import unittest
import hashlib
import json
from collections import deque
from itertools import count
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import cv2
import numpy as np

from app_info import ASSETS
from bite import BiteDetector, FishingFrame
from calibrate import Calibration, automatic_capture_area, watch, water_in_cast_area
from hotbar import HotbarRod
from matcher import Match, Matcher
from navigation import guidance, world_key
from seed_map import SeedMap, map_displays, map_url, numeric_map_seed
from seed_map_view import PauseMap, editing_box, show_route
from updates import download_release, is_update, latest_version


class NavigationTests(unittest.TestCase):
    def test_compass_and_distance(self):
        self.assertEqual(guidance((0, 0), (-3, -4), 1), "↖  NW · 5 blocks")
        self.assertEqual(guidance((12, 12), (22, 12), 1), "→  E · 10 blocks")
        self.assertEqual(guidance((12, 12), (12, 22), 1), "↓  S · 10 blocks")
        self.assertEqual(guidance((0, 0), (3, 4), 5), "Destination reached!")

    def test_world_scope(self):
        overworld = dict(seed="12", edition="bedrock", dimension="Overworld")
        self.assertNotEqual(world_key(overworld), world_key(dict(overworld, dimension="Nether")))
        self.assertNotEqual(world_key(overworld), world_key(dict(overworld, seed="13")))


class MapTests(unittest.TestCase):
    @patch("seed_map_view.execute")
    def test_route_update_can_render_after_coordinate_read(self, execute):
        show_route(Mock(), (3, -4), {"waypoints": {"Home": [3, -4]}})
        script = execute.call_args.args[1]
        self.assertIn("width:100%;height:100%", script)
        self.assertIn('"Home": [3, -4]', script)

    def test_text_seed_is_sent_as_the_signed_minecraft_seed(self):
        config = dict(seed="IV", edition="bedrock", version="26.60–26.65", dimension="Overworld", x=None, z=None)
        self.assertEqual(numeric_map_seed("IV"), "2349")
        self.assertIn("seed=2349&", map_url(config))
        self.assertEqual(config["seed"], "IV")
        self.assertEqual(numeric_map_seed("-9223372036854775808"), "-9223372036854775808")

    def test_named_seed_persists_without_switching_the_current_map(self):
        active = {"seed": "27", "enabled": False, "mode": "overlay"}
        settings = {"seed_map": active}
        with TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            map_settings = SeedMap(None, settings, lambda: settings_path.write_text(json.dumps(settings)), None)
            config = dict(seed="-9223372036854775808", edition="bedrock",
                          version="26.60–26.65", dimension="Nether")
            self.assertEqual(map_settings.save_seed("  Ocean Base  ", dict(config, display="Secondary")), "Ocean Base")
            map_settings.save_seed("Village", dict(config, seed="+12", dimension="Overworld"))
            loaded = json.loads(settings_path.read_text())
        self.assertEqual(loaded["seed_map"], active)
        self.assertEqual(loaded["saved_seeds"]["Ocean Base"], config)
        self.assertEqual(loaded["saved_seeds"]["Village"]["seed"], "12")

    def test_seed_storage_failure_keeps_previous_profiles(self):
        previous = {"Home": {"seed": "28"}}
        settings = {"saved_seeds": previous}
        map_settings = SeedMap(None, settings, Mock(side_effect=OSError("Disk unavailable")), None)
        with self.assertRaisesRegex(OSError, "Disk unavailable"):
            map_settings.save_seed("Home", {"seed": "29"})
        self.assertEqual(settings["saved_seeds"], previous)

    @patch("seed_map.mss.MSS", side_effect=OSError("Display enumeration failed"))
    def test_map_remains_configurable_without_a_display_list(self, _):
        self.assertEqual(map_displays(), [])

    @patch("seed_map.webbrowser.open")
    @patch("seed_map.messagebox.askyesno", return_value=True)
    def test_failed_map_worker_offers_browser_fallback(self, prompt, browser):
        seed_map = SeedMap(Mock(), {}, None, None)
        process = Mock()
        process.poll.return_value = 1
        seed_map.process = process
        seed_map.check_worker(process, "https://www.chunkbase.com/apps/seed-map", 1)
        prompt.assert_called_once()
        browser.assert_called_once_with("https://www.chunkbase.com/apps/seed-map")
        self.assertIsNone(seed_map.process)

    def test_disabled_map_stays_closed(self):
        map_settings = SeedMap(None, {"seed_map": {"seed": "1", "enabled": False}}, None, None)
        map_settings.launch = Mock()
        map_settings.open_startup_overlay()
        map_settings.launch.assert_not_called()

    def test_map_process_commands(self):
        stream = StringIO()
        map_settings = SeedMap(None, {}, None, None)
        map_settings.process = Mock(stdin=stream)
        map_settings.process.poll.return_value = None
        map_settings.zoom("zoom_in")
        map_settings.send("show_window")
        self.assertEqual(stream.getvalue(), "zoom_in\nshow_window\n")

    def test_views_use_same_seed_and_position(self):
        config = dict(seed="-9223372036854775807", edition="bedrock", version="26.60–26.65",
                      dimension="Overworld", x=-12, z=8)
        for overlay in (True, False):
            self.assertIn("seed=-9223372036854775807", map_url(config, overlay=overlay))
            self.assertIn("x=-12&z=8", map_url(config, overlay=overlay))


class FishingDefaultsTests(unittest.TestCase):
    def test_recast_survives_a_brief_hotbar_drop_during_reel(self):
        bounds = dict(left=0, top=0, width=1280, height=720)
        game = Mock(identity=(1, 2))
        game.movement_pressed.return_value = False
        rod = Mock()
        rod.matches.side_effect = [True, True, False, False] + [True] * 30
        stop = Mock()
        stop.is_set.side_effect = [False] * 12 + [True]
        detector = Mock(stable=4)
        detector.update.side_effect = [FishingFrame(None, bite=True)] + [FishingFrame(None)] * 20
        frames = deque(maxlen=12)
        with (patch("calibrate.mss.MSS") as capture,
              patch("calibrate.client_area", return_value=bounds),
              patch("calibrate.read_selected_slot"),
              patch("calibrate.water_in_cast_area", return_value=True),
              patch("calibrate.time.monotonic", side_effect=count(1, 0.12))):
            capture.return_value.__enter__.return_value.grab.return_value = np.zeros((64, 64, 4), dtype=np.uint8)
            result = watch(None, detector, game, True, stop, frames, rod, Mock())
        self.assertEqual((result.reels, result.recasts), (1, 1))
        self.assertTrue(any(not frame.rod_selected for frame in frames))

    def test_automatic_cast_waits_for_stationary_rod_and_water(self):
        bounds = dict(left=0, top=0, width=1280, height=720)
        for moving, equipped, water, expected in (
            (False, True, True, 1), (True, True, True, 0),
            (False, False, True, 0), (False, True, False, 0),
        ):
            with self.subTest(moving=moving, equipped=equipped, water=water):
                game = Mock(identity=(1, 2))
                game.movement_pressed.return_value = moving
                rod = Mock()
                rod.matches.return_value = equipped
                stop = Mock()
                stop.is_set.side_effect = [False] * 30 + [True]
                detector = Mock(stable=0)
                detector.update.return_value = FishingFrame(None)
                frames = deque(maxlen=30)
                with (patch("calibrate.mss.MSS") as capture,
                      patch("calibrate.client_area", return_value=bounds),
                      patch("calibrate.read_selected_slot"),
                      patch("calibrate.water_in_cast_area", return_value=water),
                      patch("calibrate.time.monotonic", side_effect=count(1, 0.2))):
                    capture.return_value.__enter__.return_value.grab.return_value = np.zeros((64, 64, 4), dtype=np.uint8)
                    result = watch(None, detector, game, True, stop, frames, rod, Mock())
                self.assertEqual(result.casts, expected)
                self.assertEqual(game.use_rod.call_count, expected)

    def test_indicator_fades_after_five_seconds_of_standby(self):
        app = Mock(key="bedrock", settings={}, indicator_last_used=10, indicator_state=None)
        app.status_game.edition = app.key
        with patch("calibrate.client_area", return_value={"width": 800}), patch("calibrate.time.monotonic") as now:
            now.return_value = 14.99
            Calibration.refresh_fishing_indicator(app)
            app.fishing_indicator.show.assert_called_once()
            now.return_value = 15
            Calibration.refresh_fishing_indicator(app)
            Calibration.refresh_fishing_indicator(app)
        app.fishing_indicator.fade_out.assert_called_once()

    def test_splash_at_locked_bobber_requires_submersion(self):
        water = np.full((100, 120, 3), (38, 43, 26), dtype=np.uint8)
        floating = water.copy()
        floating[33:47, 44:56] = (18, 22, 108)
        matcher = Mock()
        matcher.find.return_value = Match(0.98, (44, 33, 56, 47))
        detector = BiteDetector(matcher, 0.82)
        for tick in range(9):
            self.assertFalse(detector.update(floating, tick / 5).bite)
        splash = floating.copy()
        splash[40:47, 57:63] = (220, 31, 34)
        for x, y in ((25, 37), (68, 30), (44, 14), (67, 58)):
            splash[y:y + 3, x:x + 3] = (220, 31, 34)
        visible = detector.update(splash, 1.8)
        self.assertTrue(visible.bubbles)
        self.assertFalse(visible.bite)
        splash[33:47, 44:56] = water[33:47, 44:56]
        matcher.find.return_value = None
        self.assertTrue(detector.update(splash, 2).bite)

    def test_vibrant_bobber_variants_stand_out_in_water(self):
        variants = ((ASSETS / "bedrock-vibrant-bobber.png", 0.89),
                    (ASSETS / "bedrock-vibrant-lit-bobber.png", 0.91),
                    (ASSETS / "bedrock-vibrant-level-bobber.png", 0.92),
                    (ASSETS / "bedrock-topdown-bobber.png", 0.89))
        detector = Matcher(ASSETS / "bedrock-bobber.png", variants=variants)
        empty = np.full((60, 90, 3), (37, 42, 23), dtype=np.uint8)
        self.assertLess(detector.find(empty).score, 0.82)
        for reference_path, _ in variants:
            with self.subTest(reference=reference_path.name):
                reference = cv2.imread(str(reference_path), cv2.IMREAD_UNCHANGED)
                x, y, width, height = cv2.boundingRect(reference[:, :, 3])
                target = reference[y:y + height, x:x + width]
                frame = empty.copy()
                region = frame[18:18 + height, 32:32 + width]
                region[target[:, :, 3] > 0] = target[:, :, :3][target[:, :, 3] > 0]
                match = detector.find(frame)
                self.assertGreater(match.score, 0.95)
                self.assertEqual(match.box, (32, 18, 32 + width, 18 + height))

    def test_first_cast_checks_vibrant_water_at_crosshair(self):
        warm_stone = (16, 100, 125)
        dark_teal_water = (80, 90, 28)
        pond = np.full((80, 120, 3), warm_stone, dtype=np.uint8)
        pond[26:, 20:100] = dark_teal_water
        self.assertTrue(water_in_cast_area(cv2.cvtColor(pond, cv2.COLOR_HSV2BGR)))
        reflected = pond.copy()
        reflected[29:46, 43:77] = warm_stone
        self.assertTrue(water_in_cast_area(cv2.cvtColor(reflected, cv2.COLOR_HSV2BGR)))
        pond[29:51, 43:77] = warm_stone
        self.assertFalse(water_in_cast_area(cv2.cvtColor(pond, cv2.COLOR_HSV2BGR)))
        classic = np.full_like(pond, (100, 150, 125))
        self.assertTrue(water_in_cast_area(cv2.cvtColor(classic, cv2.COLOR_HSV2BGR)))

    def test_hotbar_match_survives_a_durability_bar(self):
        reference = cv2.imread(str(ASSETS / "bedrock-hotbar-rod.png"))
        slot = reference.copy()
        slot[32:, :, :] = 0
        slot[35:38, 4:35] = (0, 255, 0)
        learned = HotbarRod(reference)
        bundled = HotbarRod(reference, reference)
        for rod, muted in ((learned, False), (bundled, False), (bundled, True)):
            self.assertTrue(rod.matches(slot, muted=muted))
            self.assertFalse(rod.matches(cv2.rotate(slot, cv2.ROTATE_90_CLOCKWISE), muted=muted))
        offset = np.float32([[1, 0, 2], [0, 1, -2]])
        shifted = cv2.warpAffine(slot, offset, (40, 40), borderMode=cv2.BORDER_REPLICATE)
        self.assertTrue(learned.matches(shifted, muted=True))

    def test_search_is_centered_and_capped_at_common_resolutions(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            bounds = dict(left=-width, top=20, width=width, height=height)
            area = automatic_capture_area(bounds)
            self.assertEqual(area["left"] + area["width"] // 2, -width // 2)
            crosshair = 20 + height // 2
            self.assertLess(area["top"], crosshair)
            self.assertGreater(area["top"] + area["height"], crosshair)
            self.assertLess(area["top"] + area["height"] // 2, crosshair)
            self.assertLessEqual(area["width"], 640)
            self.assertLessEqual(area["height"], 480)
        self.assertIsNone(automatic_capture_area(None))

    def test_laptop_far_float_and_crosshair_are_both_in_capture(self):
        area = automatic_capture_area(dict(left=0, top=0, width=1920, height=1080))
        self.assertLessEqual(area['top'], 320)
        self.assertGreaterEqual(area['top']+area['height'], 540)


class MapPauseTests(unittest.TestCase):
    def test_escape_waits_for_cursor_then_leaves_on_resume(self):
        pause = PauseMap()
        self.assertFalse(pause.update("pause_open", foreground=True, captured=True, now=0))
        self.assertTrue(pause.update(None, foreground=True, captured=False, now=0.1))
        self.assertTrue(pause.update(None, foreground=True, captured=False, now=2))
        self.assertFalse(pause.update(None, foreground=True, captured=True, now=3))

    def test_menu_cursor_alone_does_not_open_map(self):
        pause = PauseMap()
        self.assertFalse(pause.update(None, foreground=True, captured=False, now=0))
        self.assertFalse(pause.update("pause_open", foreground=True, captured=True, now=1))
        self.assertFalse(pause.update(None, foreground=True, captured=False, now=3))

    def test_escape_and_focus_loss_close_map(self):
        for command, foreground in (("pause_close", True), (None, False)):
            pause = PauseMap()
            self.assertTrue(pause.update("pause_open", foreground=True, captured=False, now=0))
            self.assertFalse(pause.update(command, foreground=foreground, captured=False, now=1))
            self.assertFalse(pause.update(None, foreground=True, captured=False, now=2))

    def test_editor_leaves_left_and_center_of_pause_menu_free(self):
        bounds = dict(left=-1280, top=0, width=1280, height=720)
        for edition, free_width in (("bedrock", 640), ("java", 820)):
            x, y, width, height = editing_box(bounds, edition)
            self.assertGreater(x, bounds["left"] + free_width)
            self.assertLessEqual(x + width, 0)
            self.assertGreater(y, bounds["top"])
            self.assertLess(y + height, bounds["height"])


class UpdateTests(unittest.TestCase):
    @patch("updates.urlopen")
    def test_stable_version(self, get):
        get.return_value.__enter__.return_value = StringIO('{"tag_name": "v2.0.0"}')
        self.assertTrue(is_update(latest_version()))
        self.assertIn("/repos/scrmarsh/BobberBuddy/releases/latest", get.call_args.args[0].full_url)

    @patch("updates.urlopen")
    def test_prerelease_is_ignored(self, get):
        get.return_value.__enter__.return_value = StringIO('{"tag_name": "v2.0.0", "prerelease": true}')
        self.assertIsNone(latest_version())

    @patch("updates.urlopen")
    def test_download_verifies_before_installation(self, get):
        body = b"bundle"
        url = "https://github.com/scrmarsh/BobberBuddy/releases/download/v2.0.0/Bobber-Buddy-v2.0.0-Windows-x64.zip"
        asset = dict(name=url.rsplit("/", 1)[1], browser_download_url=url,
                     digest=f"sha256:{hashlib.sha256(body).hexdigest()}", size=len(body))
        response = BytesIO(body)
        response.geturl = lambda: url
        get.return_value.__enter__.return_value = response
        with TemporaryDirectory() as temp, patch("updates.DATA_DIR", Path(temp)):
            result = download_release("2.0.0", {"assets": [asset]}, Mock())
            self.assertEqual(result.read_bytes(), body)
            response = BytesIO(b"broken")
            response.geturl = lambda: url
            get.return_value.__enter__.return_value = response
            asset["digest"] = "sha256:" + "a" * 64
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                download_release("2.0.0", {"assets": [asset]}, Mock())
            self.assertEqual(result.read_bytes(), body)
