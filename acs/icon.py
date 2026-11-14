"""App icon, drawn in code (supersampled). `python -m acs.icon out.ico` writes the .ico used for the exe."""
from __future__ import annotations

import sys

from PIL import Image, ImageDraw, ImageOps

GREEN = (48, 190, 90, 255)
_SS = 4  # supersampling factor


def render(size: int = 256, state: str = "on") -> Image.Image:
    """state: 'on' (normal), 'off' (greyed out), 'nolog' (orange warning badge)."""
    n = size * _SS
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))

    top, bot = (28, 42, 60), (10, 15, 24)
    grad = Image.new("RGBA", (n, n))
    gd = ImageDraw.Draw(grad)
    for y in range(n):
        t = y / (n - 1)
        gd.line([(0, y), (n, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, n - 1, n - 1), radius=int(n * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    c, r = n / 2, n * 0.27
    # crosshair ticks
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        a, b = r + n * 0.10, r + n * 0.17
        d.line([(c + dx * a, c + dy * a), (c + dx * b, c + dy * b)], fill=(190, 205, 220, 255), width=int(n * 0.025))
    d.ellipse((c - r - n * 0.045,) * 2 + (c + r + n * 0.045,) * 2, outline=(48, 190, 90, 110), width=int(n * 0.018))
    d.ellipse((c - r,) * 2 + (c + r,) * 2, fill=GREEN)
    # check mark
    pts = [(n * 0.37, n * 0.51), (n * 0.46, n * 0.60), (n * 0.64, n * 0.40)]
    w = int(n * 0.065)
    d.line(pts, fill="white", width=w, joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill="white")

    if state == "off":
        g = ImageOps.grayscale(img.convert("RGB")).point(lambda v: int(v * 0.8))
        img = Image.merge("RGBA", (g, g, g, img.getchannel("A")))
    elif state == "nolog":
        bx, by, br = n * 0.76, n * 0.76, n * 0.17
        d = ImageDraw.Draw(img)
        d.ellipse((bx - br - n * 0.02, by - br - n * 0.02, bx + br + n * 0.02, by + br + n * 0.02), fill=(10, 15, 24, 255))
        d.ellipse((bx - br, by - br, bx + br, by + br), fill=(235, 150, 20, 255))
        d.rounded_rectangle((bx - n * 0.018, by - br * 0.6, bx + n * 0.018, by + br * 0.15), radius=n * 0.01, fill="white")
        d.ellipse((bx - n * 0.02, by + br * 0.35, bx + n * 0.02, by + br * 0.35 + n * 0.04), fill="white")

    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "icon.ico"
    render(256).save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
