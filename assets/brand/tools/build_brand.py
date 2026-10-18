# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools==4.66.1"]
# ///
"""Build every kappajudge brand asset from source, reproducibly.

    uv run assets/brand/tools/build_brand.py

Requires `resvg` on PATH for the PNG exports (`cargo install resvg --locked --version 0.48.1`).

The mark is a 3x3 rater-agreement matrix: the diagonal (both raters gave the same score) in the
accent colour, the cells one step off the diagonal half-tone, and the far corners only outlined,
which is how quadratic-weighted kappa treats disagreement: near misses cost little, far ones a lot.

The wordmark is set in JetBrains Mono ExtraBold and converted to outlines, so the logos render
identically without the font installed. The font is fetched from the pinned upstream release and
verified by SHA-256 (it is not vendored). JetBrains Mono is licensed under the SIL Open Font
License 1.1, which permits using its outlines in artwork such as a logo.
"""

from __future__ import annotations

import hashlib
import io
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = pathlib.Path(__file__).resolve().parents[1]
FONT_URL = "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip"
FONT_SHA256 = "6f6376c6ed2960ea8a963cd7387ec9d76e3f629125bc33d1fdcd7eb7012f7bbf"

# Palette. One accent (violet), two tones so it passes contrast on both GitHub themes.
INK = "#120E1A"  # mark on light backgrounds; tile / social background
SNOW = "#F4F1FA"  # mark on dark backgrounds
VIOLET_ON_LIGHT = "#6D28D9"
VIOLET_ON_DARK = "#B197FC"
MUTED_ON_DARK = "#A49DB8"
GITHUB_LIGHT_BG = "#FFFFFF"
GITHUB_DARK_BG = "#0D1117"


# ----------------------------------------------------------------- contrast


def _lum(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ----------------------------------------------------------------- fonts


def load_fonts() -> dict[str, TTFont]:
    cache = OUT / "tools" / ".cache" / "JetBrainsMono-2.304.zip"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(FONT_URL, timeout=60) as r:  # pinned https URL, checksum-verified below
            cache.write_bytes(r.read())
    data = cache.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != FONT_SHA256:
        cache.unlink()
        sys.exit(f"font archive checksum mismatch: {digest}")
    z = zipfile.ZipFile(io.BytesIO(data))
    return {w: TTFont(io.BytesIO(z.read(f"fonts/ttf/JetBrainsMono-{w}.ttf"))) for w in ("ExtraBold", "Medium")}


def _ntos(n: float) -> str:
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def text_path(
    font: TTFont, text: str, size: float, x: float, baseline: float, tracking: float = 0.0
) -> tuple[str, float]:
    """Outline `text` as an SVG path. Returns (d, advance width)."""
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    scale = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs, ntos=_ntos)
    cursor = x
    for ch in text:
        name = cmap[ord(ch)]
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, baseline)))
        cursor += font["hmtx"][name][0] * scale + tracking
    return pen.getCommands(), cursor - x - tracking


def x_height(font: TTFont, size: float) -> float:
    return font["OS/2"].sxHeight * size / font["head"].unitsPerEm


# ----------------------------------------------------------------- the mark


def _grid(fg: str, accent: str, *, origin: float, cell: float, gap: float, rx: float, stroke: float) -> str:
    """3x3 agreement matrix. Diagonal = accent, |i-j| = 1 -> half-tone fg, |i-j| = 2 -> outline."""
    parts = []
    for i in range(3):
        for j in range(3):
            x = origin + j * (cell + gap)
            y = origin + i * (cell + gap)
            d = abs(i - j)
            if d == 0:
                parts.append(
                    f'<rect x="{_ntos(x)}" y="{_ntos(y)}" width="{_ntos(cell)}" height="{_ntos(cell)}" '
                    f'rx="{_ntos(rx)}" fill="{accent}"/>'
                )
            elif d == 1:
                parts.append(
                    f'<rect x="{_ntos(x)}" y="{_ntos(y)}" width="{_ntos(cell)}" height="{_ntos(cell)}" '
                    f'rx="{_ntos(rx)}" fill="{fg}" fill-opacity="0.42"/>'
                )
            else:
                h = stroke / 2
                parts.append(
                    f'<rect x="{_ntos(x + h)}" y="{_ntos(y + h)}" width="{_ntos(cell - stroke)}" '
                    f'height="{_ntos(cell - stroke)}" rx="{_ntos(max(rx - h, 0))}" fill="none" stroke="{fg}" '
                    f'stroke-opacity="0.55" stroke-width="{_ntos(stroke)}"/>'
                )
    return "".join(parts)


def mark(fg: str, accent: str, *, x: float = 0, y: float = 0, size: float = 64) -> str:
    """The agreement-matrix mark on a 64-unit grid."""
    s = size / 64
    body = _grid(fg, accent, origin=4, cell=16, gap=4, rx=3.5, stroke=2.5)
    return f'<g transform="translate({_ntos(x)} {_ntos(y)}) scale({_ntos(s)})">{body}</g>'


def favicon_mark() -> str:
    """32px variant: own tile, so it works on light and dark browser tabs."""
    return f'<rect width="32" height="32" rx="7" fill="{INK}"/>' + _grid(
        SNOW, VIOLET_ON_DARK, origin=5, cell=6, gap=2, rx=1.2, stroke=1.4
    )


def favicon16_mark() -> str:
    """Hand-hinted 16px variant: 4px cells with 1px gaps on the pixel grid."""
    return (
        f'<rect width="16" height="16" rx="3" fill="{INK}"/>'
        f'<g shape-rendering="crispEdges">{_grid(SNOW, VIOLET_ON_DARK, origin=1, cell=4, gap=1, rx=0, stroke=1)}</g>'
    )


def svg(width: float, height: float, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_ntos(width)}" height="{_ntos(height)}" '
        f'viewBox="0 0 {_ntos(width)} {_ntos(height)}" role="img" aria-label="{title}">'
        f"<title>{title}</title>{body}</svg>\n"
    )


def lockup(fonts: dict[str, TTFont], fg: str, accent: str) -> str:
    size, gap = 46.0, 16.0
    xh = x_height(fonts["ExtraBold"], size)
    baseline = 32 + xh / 2
    d_a, w_a = text_path(fonts["ExtraBold"], "kappa", size, 64 + gap, baseline, tracking=-0.6)
    d_b, w_b = text_path(fonts["ExtraBold"], "judge", size, 64 + gap + w_a - 0.6, baseline, tracking=-0.6)
    width = 64 + gap + w_a - 0.6 + w_b + 2
    body = mark(fg, accent) + f'<path fill="{fg}" d="{d_a}"/><path fill="{accent}" d="{d_b}"/>'
    return svg(width, 64, body, "kappajudge")


def social(fonts: dict[str, TTFont]) -> str:
    W, H, left = 1280, 640, 96
    eb, md = fonts["ExtraBold"], fonts["Medium"]
    parts = [f'<rect width="{W}" height="{H}" fill="{INK}"/>']
    parts.append(mark(SNOW, VIOLET_ON_DARK, x=left - 8, y=96, size=128))
    wsize = 92
    base = 96 + 64 + x_height(eb, wsize) / 2
    d1, w1 = text_path(eb, "kappa", wsize, left + 152, base, tracking=-1.2)
    d2, _ = text_path(eb, "judge", wsize, left + 152 + w1 - 1.2, base, tracking=-1.2)
    parts.append(f'<path fill="{SNOW}" d="{d1}"/><path fill="{VIOLET_ON_DARK}" d="{d2}"/>')
    for i, line in enumerate(("An LLM judge whose agreement", "with humans is measured.")):
        d, _ = text_path(md, line, 54, left, 340 + i * 70)
        parts.append(f'<path fill="{SNOW}" d="{d}"/>')
    d, _ = text_path(md, "Quadratic-weighted kappa · bias tests · CI release gate", 27, left, 480)
    parts.append(f'<path fill="{MUTED_ON_DARK}" d="{d}"/>')
    d, _ = text_path(md, "github.com/niravpatidar37/kappajudge", 24, left, 566)
    parts.append(f'<path fill="{VIOLET_ON_DARK}" d="{d}"/>')
    return svg(W, H, "".join(parts), "kappajudge: an LLM judge whose agreement with humans is measured")


# ----------------------------------------------------------------- safety gate

FORBIDDEN = [
    (re.compile(r"<\s*script", re.I), "script element"),
    (re.compile(r"\son[a-z]+\s*=", re.I), "event handler attribute"),
    (re.compile(r"<\s*foreignObject", re.I), "foreignObject"),
    (re.compile(r"(?:xlink:)?href\s*=\s*\"(?!#)", re.I), "external reference"),
    (re.compile(r"url\(\s*['\"]?(?!#)", re.I), "external url()"),
    (re.compile(r"javascript:", re.I), "javascript: URL"),
]


def check_safe(name: str, text: str) -> None:
    for pattern, what in FORBIDDEN:
        if pattern.search(text):
            sys.exit(f"{name}: unsafe SVG content ({what})")


# ----------------------------------------------------------------- main


def main() -> None:
    checks = {
        "accent on GitHub light": contrast(VIOLET_ON_LIGHT, GITHUB_LIGHT_BG),
        "accent on GitHub dark": contrast(VIOLET_ON_DARK, GITHUB_DARK_BG),
        "ink on GitHub light": contrast(INK, GITHUB_LIGHT_BG),
        "snow on GitHub dark": contrast(SNOW, GITHUB_DARK_BG),
        "favicon diagonal on tile": contrast(VIOLET_ON_DARK, INK),
        "muted text on social bg": contrast(MUTED_ON_DARK, INK),
    }
    for label, ratio in checks.items():
        print(f"contrast {label}: {ratio:.2f}:1")
        if ratio < 3.0:
            sys.exit(f"contrast below WCAG 3:1 for graphics: {label}")

    fonts = load_fonts()
    files = {
        "icon.svg": svg(64, 64, mark(INK, VIOLET_ON_LIGHT), "kappajudge"),
        "icon-dark.svg": svg(64, 64, mark(SNOW, VIOLET_ON_DARK), "kappajudge"),
        "favicon.svg": svg(32, 32, favicon_mark(), "kappajudge"),
        "favicon-16.svg": svg(16, 16, favicon16_mark(), "kappajudge"),
        "logo.svg": lockup(fonts, INK, VIOLET_ON_LIGHT),
        "logo-dark.svg": lockup(fonts, SNOW, VIOLET_ON_DARK),
        "social-preview.svg": social(fonts),
    }
    for name, text in files.items():
        check_safe(name, text)
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {name} ({len(text)} bytes)")

    resvg = shutil.which("resvg")
    if not resvg:
        sys.exit("resvg not found on PATH; install it to export PNGs")
    renders = [
        ("favicon-16.svg", "favicon-16.png", 16),
        ("favicon.svg", "favicon-32.png", 32),
        ("favicon.svg", "avatar-512.png", 512),
        ("social-preview.svg", "social-preview.png", 1280),
    ]
    for src, dst, width in renders:
        # List-form argv, no shell: every argument is a constant from `renders` plus the absolute
        # resvg path from shutil.which, so no untrusted input reaches this call.
        argv = [resvg, "-w", str(width), str(OUT / src), str(OUT / dst)]
        subprocess.run(argv, check=True, shell=False)  # noqa: S603  # nosec B603
        print(f"rendered {dst} ({(OUT / dst).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
