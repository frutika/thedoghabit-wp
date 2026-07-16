#!/usr/bin/env python3
"""make_site_icon.py — generira assets/site-icon.png (512x512 WP site icon).

Šapa u krem boji na terakota pozadini (brand paleta iz style.css:
#C96F4A accent, #FAF7F2 pozadina sajta). Crta se na 4x platnu pa
downsampla LANCZOS-om za glatke rubove (PIL ellipse nema anti-aliasing).
Jednokratno: output se commita u repo, na WP ide preko 'wp media import'
+ 'wp option update site_icon <id>'.
"""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512
SS = 4  # supersample faktor
BG = "#C96F4A"
PAW = "#FAF7F2"

OUT = Path(__file__).resolve().parent.parent / "assets" / "site-icon.png"


def ellipse(draw, cx, cy, rx, ry, angle=0.0):
    """Rotirana elipsa: nacrtaj na vlastitom sloju, zarotiraj, zalijepi."""
    pad = 8
    layer = Image.new("RGBA", (int(2 * rx) + 2 * pad, int(2 * ry) + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([pad, pad, pad + 2 * rx, pad + 2 * ry], fill=PAW)
    if angle:
        layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    draw._image.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), layer)


def main():
    canvas = SIZE * SS
    img = Image.new("RGBA", (canvas, canvas), BG)
    draw = ImageDraw.Draw(img)
    draw._image = img  # za ellipse() paste

    s = canvas / 1000  # koordinate ispod su u 1000x1000 prostoru

    # glavni jastučić — široka elipsa, malo spljoštena
    ellipse(draw, 500 * s, 655 * s, 200 * s, 150 * s)
    # 4 prsta u luku iznad, s razmakom od jastučića i međusobno;
    # vanjski niže i nagnuti prema van
    ellipse(draw, 295 * s, 390 * s, 64 * s, 88 * s, angle=22)
    ellipse(draw, 425 * s, 295 * s, 66 * s, 92 * s, angle=7)
    ellipse(draw, 575 * s, 295 * s, 66 * s, 92 * s, angle=-7)
    ellipse(draw, 705 * s, 390 * s, 64 * s, 88 * s, angle=-22)

    img = img.resize((SIZE, SIZE), Image.LANCZOS).convert("RGB")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, format="PNG", optimize=True)
    print(f"OK: {OUT} ({OUT.stat().st_size} bajtova)")


if __name__ == "__main__":
    main()
