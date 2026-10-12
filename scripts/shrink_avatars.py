"""Shrink agent portraits to web size before they are committed.

Generated portraits come out around 1254x1254 and ~1.8 MB, but they are shown at 64-96 px and at
most 640 px in the lightbox. 512 px with a 256-colour palette is visually identical there and about
150 KB. Images already at or below the target size are left alone, so running this twice is safe.

Usage: python scripts/shrink_avatars.py [FILE.png ...]   (default: every hagent/static/avatars/*.png)
Needs Pillow (pip install pillow).
"""

import sys
from pathlib import Path

from PIL import Image

SIZE = 512
AVATAR_DIR = Path(__file__).resolve().parent.parent / "hagent" / "static" / "avatars"


def shrink(path: Path) -> None:
    before = path.stat().st_size
    with Image.open(path) as image:
        if max(image.size) <= SIZE:
            print(f"{path.name}: already {image.size[0]}x{image.size[1]}, skipped")
            return
        small = image.convert("RGB")
        small.thumbnail((SIZE, SIZE), Image.LANCZOS)  # keeps the aspect ratio
    small.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG).save(path, "PNG", optimize=True)
    print(f"{path.name}: {before // 1024} KB -> {path.stat().st_size // 1024} KB")


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] or sorted(AVATAR_DIR.glob("*.png"))
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        print("not found: " + ", ".join(missing), file=sys.stderr)
        return 1
    for path in paths:
        shrink(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
