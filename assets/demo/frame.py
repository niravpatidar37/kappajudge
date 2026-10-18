#!/usr/bin/env python3
"""Add window chrome (padding, three dots, title, rounded corners) to an agg gif.

    python assets/demo/frame.py raw.gif demo.gif "kappajudge  —  real session"

Adapted from mcpsum's frame.py; background is the brand Ink (#120E1A).
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

src, dst = sys.argv[1], sys.argv[2]
title = sys.argv[3] if len(sys.argv) > 3 else ""
BG, PAD, BAR, RADIUS = (18, 14, 26), 24, 44, 18
DOTS = [(255, 95, 87), (254, 188, 46), (40, 200, 64)]
FONTS = [
    Path(__file__).resolve().parents[1] / "brand/tools/.cache/fonts/JetBrainsMono-Regular.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]

im = Image.open(src)
w, h = im.size
W, H = w + 2 * PAD, h + BAR + PAD


def font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for f in FONTS:
        if f.is_file():
            return ImageFont.truetype(str(f), 16)
    return ImageFont.load_default()


def canvas() -> Image.Image:
    c = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(c)
    d.rounded_rectangle((0, 0, W - 1, H - 1), RADIUS, fill=(*BG, 255))
    for i, col in enumerate(DOTS):
        x = PAD + i * 24
        d.ellipse((x, BAR // 2 - 7, x + 14, BAR // 2 + 7), fill=col)
    if title:
        f = font()
        tw = d.textlength(title, font=f)
        d.text(((W - tw) / 2, BAR // 2 - 10), title, font=f, fill=(150, 142, 170))
    return c


frames, durations = [], []
base = canvas()
for i in range(im.n_frames):
    im.seek(i)
    c = base.copy()
    c.alpha_composite(im.convert("RGBA"), (PAD, BAR))
    frames.append(c)
    durations.append(im.info.get("duration", 40))

# One shared palette (255 colours + 1 transparent) so frames don't flicker.
sample = Image.new("RGB", (W, 3 * H))
for k, fr in enumerate((frames[0], frames[len(frames) // 2], frames[-1])):
    sample.paste(fr.convert("RGB"), (0, k * H))
pal = sample.quantize(255, method=Image.Quantize.MEDIANCUT)
out = []
for fr in frames:
    rgb = fr.convert("RGB").quantize(palette=pal, dither=Image.Dither.NONE)
    mask = fr.getchannel("A").point(lambda a: 255 if a < 128 else 0)
    rgb.paste(255, mask=mask)  # index 255 = transparent (outside the corners)
    out.append(rgb)
out[0].save(
    dst, save_all=True, append_images=out[1:], duration=durations, loop=0, transparency=255, disposal=1, optimize=False
)
print(dst, out[0].size, len(out), "frames", sum(durations) / 1000, "s")
