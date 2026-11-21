import unittest
import hashlib
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from navigation import guidance, world_key
from seed_map import SeedMap, map_url
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


class UpdateTests(unittest.TestCase):
    @patch("updates.urlopen")
    def test_stable_version(self, get):
        get.return_value.__enter__.return_value = StringIO('{"tag_name": "v2.0.0"}')
        self.assertTrue(is_update(latest_version()))
        self.assertIn("/repos/scrmarsh/Bobber-Buddy/releases/latest", get.call_args.args[0].full_url)

    @patch("updates.urlopen")
    def test_prerelease_is_ignored(self, get):
        get.return_value.__enter__.return_value = StringIO('{"tag_name": "v2.0.0", "prerelease": true}')
        self.assertIsNone(latest_version())

    @patch("updates.urlopen")
    def test_download_verifies_before_installation(self, get):
        body = b"bundle"
        url = "https://github.com/scrmarsh/Bobber-Buddy/releases/download/v2.0.0/Bobber-Buddy-v2.0.0-Windows-x64.zip"
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
