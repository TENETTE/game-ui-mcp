# Changelog

## 0.6.0 - 2026-11-15

- SVG export keeps more as vector: stripes / dots / grid fills become SVG patterns, single-side strokes
  stay vector, shadows and glows use the standard filter chain Figma itself writes (several shadows on
  one shape no longer shadow each other), and frosted-glass panels carry the blurred scene behind them.
- `export_svg(image_scale=...)`: sharper embedded pictures for large prints.
- `export_png(preset="ios" | "android" | "web")`: the whole density set in one call.
- English README with a Turkish version, banner, badges, issue and pull-request templates, changelog.

## 0.5.0 - 2026-11-14

- Optional GPU acceleration: `pip install "game-ui-mcp[gpu]"` (CuPy, CUDA 12) runs shader fills and large
  blurs on an NVIDIA GPU, with an automatic CPU fallback.

## 0.4.0 - 2026-11-14

- Background blur (frosted glass), single-side strokes, ellipsis truncation, underline / strikethrough,
  named version history (`canvas_version`).
- Security policy, contributing guide, code of conduct, Windows CI, trademark notice.

## 0.3.0 - 2026-11-14

- Exact boolean shapes, smoothed corners, fill stacks, variable modes, auto-layout wrap, editable vector
  SVG export, parallel animation frames.
- One-line `uvx` install, tool annotations (read-only / destructive), layered JPG / WebP / SVG layer export.
- Every tool error explains how to recover; allow-listed values in generated engine scripts; render size limit.

## 0.2.0 - 2026-11-14

- Multilingual text engine (HarfBuzz shaping, bidi, Noto fallback fonts, CJK line breaking).
- Shader fills, designer workflow tools (states, templates, string tables, pins, prototypes, engine scenes).
- Components, arrays, cut-outs, SVG import, self-review.

## 0.1.0 - 2026-11-13

- First release: shapes, text, styles, layers and lighting, keyframe animation, 9-slice export for
  Unity, Unreal, Roblox and Godot.
