"""Build a portable Windows bundle and its SHA-256 checksum."""
import argparse
import hashlib
import shutil
from pathlib import Path

from PIL import Image, ImageDraw
from PyInstaller.__main__ import run

from app_info import APP_NAME, ASSETS, ROOT, VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    dist = parser.parse_args().dist_dir.resolve()
    stage = ROOT / "build" / "release-assets"
    stage.mkdir(parents=True, exist_ok=True)
    icon = stage / "bobber.ico"
    with Image.open(ASSETS / "night-fishing-bobber.png") as image:
        image.save(icon, sizes=[(32, 32), (48, 48), (256, 256)])
    bobber = stage / "java-bobber.png"
    if not (ASSETS / bobber.name).is_file():
        template = Image.new("RGBA", (16, 16))
        draw = ImageDraw.Draw(template)
        draw.rectangle((7, 0, 9, 4), fill="#24262a")
        draw.polygon([(2, 5), (13, 5), (15, 8), (15, 11), (12, 14), (3, 14), (0, 11), (0, 8)],
                     fill="#d0d4d8")
        draw.rectangle((1, 9, 14, 11), fill="#a92420")
        template.save(bobber)
    references = sorted(ASSETS.glob("*-bobber.png")) + sorted(ASSETS.glob("*-rod.png"))
    if not (ASSETS / bobber.name).is_file():
        references.append(bobber)
    version = tuple(map(int, VERSION.split("."))) + (0,)
    metadata = stage / "version.txt"
    metadata.write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={version}, prodvers={version}, mask=63, "
        "flags=0, OS=4, fileType=1, subtype=0, date=(0,0)), kids=["
        "StringFileInfo([StringTable('040904B0', ["
        f"StringStruct('FileDescription', '{APP_NAME}'), StringStruct('ProductName', '{APP_NAME}'), "
        f"StringStruct('FileVersion', '{VERSION}'), StringStruct('ProductVersion', '{VERSION}'), "
        f"StringStruct('OriginalFilename', '{APP_NAME}.exe')"
        "])]), VarFileInfo([VarStruct('Translation', [1033,1200])])])",
        encoding="utf-8",
    )
    run([
        "--noconfirm", "--onedir", "--windowed", "--name", APP_NAME,
        "--workpath", str(ROOT / "build" / "pyinstaller"), "--specpath", str(stage),
        "--distpath", str(dist), "--icon", str(icon), "--version-file", str(metadata),
        "--add-data", f"{icon}:assets",
        *[argument for reference in references for argument in ("--add-data", f"{reference}:assets")],
        "--collect-all", "webview", "--collect-submodules", "winrt",
        "--hidden-import", "clr", str(ROOT / "calibrate.py"),
    ])
    bundle = dist / APP_NAME
    shutil.copy2(ROOT / "README.md", bundle / "README.md")
    base = dist / f"Bobber-Buddy-v{VERSION}-Windows-x64"
    archive = Path(shutil.make_archive(str(base), "zip", root_dir=dist, base_dir=APP_NAME))
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(archive)


if __name__ == "__main__":
    main()
