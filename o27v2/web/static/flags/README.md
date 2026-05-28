# Custom country flags

PNG / SVG files for fictional countries. Filename → country mapping lives in
`o27v2/web/formatters.py:_CUSTOM_FLAGS`. Anything registered there is served
by Flask's built-in static route and rendered as an inline `<img>` next to
player names in lieu of the regional-indicator emoji pair (which doesn't
exist for non-ISO codes).

## Conventions

- Size: small (e.g. 64×48 or 80×60); the template scales to 1em height.
- Format: PNG (sRGB, transparent background optional) or SVG. PNG renders
  reliably across all browsers; SVG is sharper on hi-DPI but heavier to
  author.
- Aspect ratio: roughly 4:3 or 5:3 (standard flag proportions). Anything
  off-shape will still render — the template constrains height only.

## Current registry

| Code | Country     | File   |
|------|-------------|--------|
| ZR   | Zaryanovia  | zr.png |

`zr.png` is a 171×96 palettized PNG (~7 KB). Resized from the 1456×816 source
art and quantized to 64 colors — the flag is flat color so the palette
conversion is lossless to the eye. Inline use renders at 1em (~24-30 px tall);
171 px wide gives clean 2x density for hi-DPI screens. If you swap the art,
mirror this sizing (96 px tall is the sweet spot).

To replace a placeholder, just overwrite the file with the same filename.
