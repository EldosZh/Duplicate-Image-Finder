"""Generate a small reproducible collection for manual CLI checks."""

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    root = Path(".demo")
    root.mkdir(exist_ok=True)
    original = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(original)
    draw.rectangle((20, 20, 170, 170), fill="navy")
    draw.ellipse((100, 80, 290, 220), fill="orange")
    original.save(root / "original.jpg", quality=95)
    (root / "exact-copy.jpg").write_bytes((root / "original.jpg").read_bytes())
    original.resize((160, 120)).save(root / "resized.jpg", quality=85)
    original.save(root / "recompressed.jpg", quality=65)
    distinct = Image.new("RGB", (320, 240), "white")
    distinct_draw = ImageDraw.Draw(distinct)
    for y in range(0, 240, 16):
        distinct_draw.line((0, y, 320, y), fill="black", width=4)
    distinct.save(root / "different.png")


if __name__ == "__main__":
    main()
