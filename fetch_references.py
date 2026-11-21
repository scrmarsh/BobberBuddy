from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

from PIL import Image, ImageDraw


LOCAL = Path(__file__).resolve().parent / ".local"
SOURCES = {
    "java.png": ("https://playerassist.com/wp-content/uploads/2021/11/1-28.png", (1363, 711)),
    "java-holdout.jpg": ("https://playerassist.com/wp-content/uploads/2021/11/1-105.jpg", (1365, 711)),
    "bedrock.jpg": ("https://assets.st-note.com/img/1697060140158-DuY38cofVa.jpg", (1280, 720)),
    "bedrock-holdout.jpg": (
        "https://stat.ameba.jp/user_images/20200911/11/zu-xxxxx13/55/41/j/o1080060714817725251.jpg",
        (1080, 607),
    ),
}

TEMPLATES = {
    "java": ("java-holdout.jpg", (678, 322, 693, 337),
             [(0, 0), (5, 0), (14, 10), (14, 14), (0, 14)]),
    "bedrock": ("bedrock-holdout.jpg", (525, 237, 540, 251),
                [(0, 3), (4, 1), (5, 4), (10, 4), (11, 1), (14, 3),
                 (14, 10), (10, 13), (4, 13), (1, 10)]),
}


def fetch():
    LOCAL.mkdir(exist_ok=True)
    for filename, (url, size) in SOURCES.items():
        path = LOCAL / filename
        if not path.exists():
            print(f"Downloading {filename}", flush=True)
            with urlopen(url, timeout=20) as response:
                data = response.read()
            with Image.open(BytesIO(data)) as image:
                if image.size != size:
                    raise ValueError(f"{filename} changed size to {image.size}. Recheck its crop.")
                image.verify()
            path.write_bytes(data)
        with Image.open(path) as image:
            if image.size != size:
                raise ValueError(f"{filename} changed size to {image.size}. Recheck its crop.")

    for edition, (filename, bounds, points) in TEMPLATES.items():
        with Image.open(LOCAL / filename) as screenshot:
            template = screenshot.crop(bounds).convert("RGBA")
        mask = Image.new("L", template.size)
        ImageDraw.Draw(mask).polygon(points, fill=255)
        template.putalpha(mask)
        template.save(LOCAL / f"{edition}-bobber.png")


if __name__ == "__main__":
    fetch()
