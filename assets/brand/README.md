# kappajudge brand assets

The mark is a **3x3 rater-agreement matrix**. The diagonal (judge and human gave the same score)
is in the accent colour, the cells one step off the diagonal are half-tone, and the far corners are
only outlined. That is how quadratic-weighted kappa scores disagreement: a near miss costs little,
a far miss costs a lot.

| File | Use |
|---|---|
| `logo.svg` / `logo-dark.svg` | Lockup (mark + wordmark) for light / dark backgrounds. README header. |
| `icon.svg` / `icon-dark.svg` | Mark alone, transparent background. |
| `favicon.svg`, `favicon-32.png` | Browser tab / docs favicon (own tile, works on light and dark tabs). |
| `favicon-16.svg`, `favicon-16.png` | Hand-hinted 16 px variant: 4 px cells on the pixel grid. |
| `avatar-512.png` | Social / org avatar. |
| `social-preview.png` (from `social-preview.svg`) | Repository social preview (Settings → Social preview), 1280×640, opaque. |

## Colors

| Token | Hex | Contrast | Use |
|---|---|---|---|
| Ink | `#120E1A` | 19.0:1 on `#FFFFFF` | Mark and wordmark on light backgrounds; favicon tile; social background |
| Snow | `#F4F1FA` | 17.0:1 on `#0D1117` | Mark and wordmark on dark backgrounds |
| Violet (light) | `#6D28D9` | 7.1:1 on `#FFFFFF` | Diagonal and "judge" on light backgrounds |
| Violet (dark) | `#B197FC` | 7.8:1 on `#0D1117` | Diagonal and "judge" on dark backgrounds |
| Muted | `#A49DB8` | 7.3:1 on Ink | Secondary text on the social preview |

The build script checks every pairing against GitHub's light (`#FFFFFF`) and dark (`#0D1117`)
backgrounds and fails below WCAG's 3:1 minimum for graphics.

## Usage

- Keep clear space around the lockup of at least one matrix cell.
- Below 120 px wide, use the mark alone; below 24 px, use the favicon variants.
- Do not recolor, stretch, rotate or re-set the wordmark in another font.

## Rebuilding

All assets come from `tools/build_brand.py`, so they are reproducible and reviewable as code:

```sh
cargo install resvg --locked --version 0.48.1   # PNG export
uv run assets/brand/tools/build_brand.py
```

The script fetches JetBrains Mono v2.304 from its official release, verifies its SHA-256,
outlines the wordmark, checks colour contrast, rejects any SVG containing scripts, event handlers,
`foreignObject` or external references, and renders the PNGs with resvg.

## Licensing

- The wordmark uses outlines of **JetBrains Mono** (SIL Open Font License 1.1, © The JetBrains
  Mono Project Authors), which permits use in artwork such as logos. The font is not redistributed.
- The code is MIT-licensed. The MIT license does not grant trademark rights: the kappajudge name
  and logo identify this project.
