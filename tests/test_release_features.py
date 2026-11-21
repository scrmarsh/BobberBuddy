import unittest
import hashlib
import json
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from calibrate import automatic_capture_area
from navigation import guidance, world_key
from seed_map import SeedMap, map_displays, map_url
from seed_map_view import PauseMap, editing_box
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
    def test_search_is_centered_and_capped_at_common_resolutions(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            bounds = dict(left=-width, top=20, width=width, height=height)
            area = automatic_capture_area(bounds)
            self.assertEqual(area["left"] + area["width"] // 2, -width // 2)
            self.assertEqual(area["top"] + area["height"] // 2, 20 + height // 2)
            self.assertLessEqual(area["width"], 640)
            self.assertLessEqual(area["height"], 480)
        self.assertIsNone(automatic_capture_area(None))


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
