"""Install a verified update after the Windows app exits."""

import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from zipfile import ZipFile

from app_info import APP_NAME, UPDATER_NAME


APPLICATION = f"{APP_NAME}.exe"
BUNDLE_ENTRIES = (APPLICATION, "_internal", f"{UPDATER_NAME}.exe", "README.md")


class RollbackFailed(RuntimeError):
    pass


def stage_update(archive, digest, staged):
    with archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != digest:
            raise ValueError("The downloaded update failed its checksum.")
        source.seek(0)
        with ZipFile(source) as bundle:
            if sum(entry.file_size for entry in bundle.infolist()) > 1024**3:
                raise ValueError("The unpacked update is larger than expected.")
            extracted = set()
            for entry in bundle.infolist():
                name = entry.filename.removesuffix("/") if entry.is_dir() else entry.filename
                parts = name.split("/")
                if parts == [APP_NAME] and entry.is_dir():
                    continue
                if (len(parts) < 2 or parts[0] != APP_NAME or parts[1] not in BUNDLE_ENTRIES
                        or any(not part or part in {".", ".."} or "\\" in part or ":" in part or os.path.isreserved(part)
                               for part in parts)
                        or stat.S_ISLNK(entry.external_attr >> 16)):
                    raise ValueError("The ZIP contains an unexpected path.")
                key = name.casefold()
                if key in extracted:
                    raise ValueError("The ZIP contains a duplicate path.")
                extracted.add(key)
                destination = staged.joinpath(*parts[1:])
                if entry.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(entry) as original, destination.open("xb") as output:
                        shutil.copyfileobj(original, output)
    if not (staged / "_internal").is_dir() or not all(
            (staged / name).is_file() for name in BUNDLE_ENTRIES if name != "_internal"):
        raise ValueError("The ZIP is missing required application files.")


def swap_bundle(install, staged, backup, launch):
    installed = []
    previous = []
    try:
        for name in BUNDLE_ENTRIES:
            target = install / name
            if target.exists():
                target.replace(backup / name)
                previous.append(name)
            (staged / name).replace(target)
            installed.append(name)
        launch(install / APPLICATION)
    except Exception:
        try:
            for name in reversed(installed):
                (install / name).replace(staged / name)
            for name in reversed(previous):
                (backup / name).replace(install / name)
        except OSError as error:
            raise RollbackFailed(f"Recovery files were saved in {backup}.") from error
        raise


def wait_for_exit(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        if ctypes.get_last_error() == 87:  # The process has already exited.
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, 45000) != 0:
            raise TimeoutError("The app did not exit in time to install the update.")
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def install_lock(path):
    import msvcrt

    with path.open("a+b") as handle:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def start_application(executable, *args):
    return subprocess.Popen([str(executable), *map(str, args)], cwd=executable.parent,
                            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})


def confirm_restart(executable, ready, expected_version):
    process = start_application(executable, "--update-ready", ready)
    try:
        deadline = time.monotonic() + 35
        while process.poll() is None and time.monotonic() < deadline:
            if ready.is_file():
                opened = ready.read_text(encoding="ascii")
                if opened == expected_version:
                    return
                if opened:
                    break
            time.sleep(0.15)
        raise RuntimeError(f"The updated app did not open as v{expected_version}.")
    except Exception:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise


def write_result(path, version, error=None):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
        pending = Path(stream.name)
        json.dump({"version": version, "error": error}, stream)
    try:
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def install_update(job):
    install = Path(job["install"])
    version = job["version"]
    result = Path(job["result"])
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not (install / APPLICATION).is_file():
        raise ValueError("The update request is invalid.")
    try:
        wait_for_exit(job["parent"])
    except OSError as error:
        write_result(result, version, str(error))
        return False
    try:
        lock_name = hashlib.sha256(os.fsencode(install.resolve())).hexdigest() + ".lock"
        with install_lock(result.parent / lock_name):
            work = None
            preserve_backup = False
            try:
                work = Path(tempfile.mkdtemp(prefix=".bobber-update-", dir=install))
                staged, backup = work / "new", work / "backup"
                staged.mkdir()
                backup.mkdir()
                stage_update(Path(job["archive"]), job["sha256"], staged)
                swap_bundle(install, staged, backup,
                            lambda executable: confirm_restart(executable, work / "ready", version))
            except RollbackFailed as error:
                preserve_backup = True
                write_result(result, version, str(error))
                return False  # Leave the backup intact for recovery.
            except Exception as error:
                write_result(result, version, str(error))
                start_application(install / APPLICATION)
                return False
            else:
                write_result(result, version)
                return True
            finally:
                if work is not None and not preserve_backup:
                    shutil.rmtree(work, ignore_errors=True)
    except OSError as error:
        write_result(result, version, str(error))
        return False


if __name__ == "__main__":
    request = Path(sys.argv[1])
    job = json.loads(request.read_text(encoding="utf-8"))
    raise SystemExit(0 if install_update(job) else 1)
