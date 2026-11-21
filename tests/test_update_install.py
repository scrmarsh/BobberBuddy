import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from zipfile import ZipFile

from calibrate import Calibration
from status_popup import FishingState
from update_install import APPLICATION, BUNDLE_ENTRIES, stage_update, swap_bundle


class InstallerTests(unittest.TestCase):
    def stage(self, directory, *, extra=None):
        archive = directory / "release.zip"
        with ZipFile(archive, "w") as package:
            for entry in BUNDLE_ENTRIES:
                path = f"Bobber Buddy/{entry}"
                package.writestr(path + "/runtime.dll" if entry == "_internal" else path, "new")
            if extra:
                package.writestr(extra, "unexpected")
        staged = directory / "staged"
        staged.mkdir()
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest(), staged

    def test_stage_requires_matching_digest_and_safe_paths(self):
        with TemporaryDirectory() as folder:
            archive, digest, staged = self.stage(Path(folder), extra="Bobber Buddy/_internal/../../outside.txt")
            with self.assertRaisesRegex(ValueError, "checksum"):
                stage_update(archive, "0" * 64, staged)
            with self.assertRaisesRegex(ValueError, "unexpected path"):
                stage_update(archive, digest, staged)
            self.assertFalse((Path(folder) / "outside.txt").exists())

    def test_swap_preserves_profile_and_restores_old_files_on_failed_restart(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            archive, digest, staged = self.stage(root)
            stage_update(archive, digest, staged)
            install = root / "install"
            install.mkdir()
            backup = root / "backup"
            backup.mkdir()
            (install / APPLICATION).write_text("old", encoding="ascii")
            (install / "_internal").mkdir()
            (install / "_internal" / "runtime.dll").write_text("old", encoding="ascii")
            profile = install / "profile"
            profile.mkdir()
            settings = profile / "settings.json"
            settings.write_text("saved", encoding="ascii")

            def failed_launch(exe):
                self.assertEqual(exe.read_text(), "new")
                raise RuntimeError("startup failed")

            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                swap_bundle(install, staged, backup, failed_launch)
            self.assertEqual((install / APPLICATION).read_text(), "old")
            self.assertEqual((install / "_internal" / "runtime.dll").read_text(), "old")
            self.assertEqual(settings.read_text(), "saved")
            launched = Mock()
            swap_bundle(install, staged, backup, launched)
            launched.assert_called_once_with(install / APPLICATION)
            self.assertEqual((install / APPLICATION).read_text(), "new")
            self.assertEqual(settings.read_text(), "saved")


class UpdateSchedulingTests(unittest.TestCase):
    @patch("calibrate.start_installer")
    def test_fishing_defers_install_until_it_is_idle(self, installer):
        pending = ("1.0.4", Path("update.zip"), "digest")
        app = Mock(closing=False, pending_update=pending, settings={}, fishing_state=FishingState.ACTIVE,
                   testing_bobber=False, busy=False, rebinding=False, detection=None)
        Calibration.install_when_idle(app, pending)
        installer.assert_not_called()
        app.root.after.assert_called_once()
        app.fishing_state = FishingState.PAUSED
        with patch("calibrate.time.monotonic", return_value=8):
            Calibration.install_when_idle(app, pending, idle_since=4)
        app.save_settings.assert_called_once()
        installer.assert_called_once_with(*pending)
        app.close.assert_called_once_with(for_update=True)
