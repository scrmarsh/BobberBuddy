import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app_info import DATA_DIR, FROZEN, RELEASE_REPOSITORY, UPDATER_NAME, VERSION


RELEASE_NOTES_URL = f"https://github.com/{RELEASE_REPOSITORY}/releases"


def latest_release():
    request = Request(f"https://api.github.com/repos/{RELEASE_REPOSITORY}/releases/latest",
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "Bobber-Buddy"})
    try:
        with urlopen(request, timeout=5) as response:
            release = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    tag = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", release.get("tag_name", ""))
    if tag and not release.get("draft") and not release.get("prerelease"):
        return ".".join(tag.groups()), release
    return None


def latest_version():
    release = latest_release()
    return release[0] if release else None


def is_update(version):
    return version is not None and tuple(map(int, version.split("."))) > tuple(map(int, VERSION.split(".")))


def release_asset(version, release):
    name = f"Bobber-Buddy-v{version}-Windows-x64.zip"
    asset = next((item for item in release.get("assets", []) if item.get("name") == name), None)
    if asset is None:
        raise ValueError("The Windows update archive is missing.")
    digest = re.fullmatch(r"sha256:([0-9a-f]{64})", asset.get("digest") or "")
    url = f"https://github.com/{RELEASE_REPOSITORY}/releases/download/v{version}/{name}"
    if not digest or asset.get("browser_download_url") != url:
        raise ValueError("The official download URL or SHA-256 digest is missing.")
    size = int(asset["size"])
    if not 0 < size <= 400 * 1024 * 1024:
        raise ValueError("The download size is invalid.")
    return name, digest[1], url, size


def downloads_directory():
    directory = DATA_DIR / "updates"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def download_release(version, release, progress):
    name, digest, url, size = release_asset(version, release)
    directory = downloads_directory()
    target = directory / name
    if target.is_file():
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() == digest:
                return target
    checksum = hashlib.sha256()
    received = 0
    partial = None
    try:
        with urlopen(Request(url, headers={"User-Agent": "Bobber-Buddy"}), timeout=10) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in {"github.com", "release-assets.githubusercontent.com"}:
                raise ValueError("GitHub returned an unexpected download host.")
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as output:
                partial = Path(output.name)
                while block := response.read(256 * 1024):
                    received += len(block)
                    if received > size:
                        raise ValueError("The update exceeded its published size.")
                    checksum.update(block)
                    output.write(block)
                    progress(received * 100 // size)
        if received != size or checksum.hexdigest() != digest:
            raise ValueError("The update failed its SHA-256 check.")
        partial.replace(target)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
    return target


def update_result():
    try:
        result = json.loads((DATA_DIR / "update-result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def start_installer(version, archive, digest):
    if not FROZEN:
        raise OSError("Automatic installation requires the Windows bundle.")
    install = Path(sys.executable).resolve().parent
    with tempfile.NamedTemporaryFile(dir=install, prefix=".update-write-check-"):
        pass
    directory = Path(tempfile.mkdtemp(prefix="installer-", dir=downloads_directory()))
    try:
        helper = directory / f"{UPDATER_NAME}.exe"
        shutil.copyfile(install / helper.name, helper)
        job = directory / "request.json"
        job.write_text(json.dumps(dict(version=version, sha256=digest, archive=str(archive.resolve()),
                                       install=str(install), parent=os.getpid(),
                                       result=str(DATA_DIR / "update-result.json"))), encoding="utf-8")
        subprocess.Popen([str(helper), str(job)], cwd=directory,
                         env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, ValueError):
        shutil.rmtree(directory, ignore_errors=True)
        raise
