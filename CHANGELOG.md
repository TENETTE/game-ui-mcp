# Changelog

## 0.11.0 - 2026-11-16

- `set_interaction`: a state machine (hover / press / click / key / animation-end triggers; play, states, navigation) that runs in web and React output and is listed in the animation JSON.
- `export_react`: React components (JSX + CSS + runtime, images imported).
- `export_animation` MP4 / WebM video (ffmpeg; `[video]` extra).
- `make_variations`: alternative palette / shape / type directions with a comparison sheet.
- DESIGN.md import (`set_theme(tokens=...)`) and export (`export_theme("design.md")`).
- Custom materials: `set_theme(materials=...)`.
- Web: state looks now beat inline styles (hover / pressed CSS did not apply before).

## 0.10.0 - 2026-11-16

- 23 ready materials (`style.material`): metals, wood, stone, marble, leather, fabric, paper, rust, lava, ice, gem, plastic, rubber, jelly, glass, neon, hologram; tintable, seedable, animatable.
- Images: `crop`, `cutout` background removal, `zoom` / `focus` pan and zoom (also on image fills), animated GIF / APNG / WebP and sprite sheets via `frame`, sub-pixel placement.

## 0.9.0 - 2026-11-16

- Lottie: plain shapes and text export as vector shape layers (solid / gradient fill, stroke, animated colour).
- Animation: `motion` property moves along a path (optional rotate), `motion_blur` in export_animation.
- Style: `feather` softens edges.
- Skill rewritten; code comments removed.

## 0.8.0 - 2026-11-16

Layout, components, effects, text and export additions:

- Live auto layout (re-flows on every change), fill / min / max sizing, grid spans, group items.
- Component variants (states named prop=value), optional parts (hide / show), slots.
- Number tokens, W3C DTCG token import / export, letter case, spring easing, diamond gradients.
- Glass refraction, progressive blur, noise modes, edge texture, luminance masks and clips, per-fill blend,
  image crop and exposure / temperature / shadows / highlights.
- Line ends (arrows...), tapered / variable-width / wiggly strokes.
- Text: lists, paragraph spacing, indent, OpenType features, variable axes, vertical trim, colour emoji,
  text on any path, text to vector outlines.
- vectorize_image, path simplify, find_elements, restyle replace_text, version diff, notes, focus order.
- New outputs: vector PDF (export_pdf), Lottie (export_animation lottie) and websites (export_web: HTML / CSS
  with links, overlays, hover states, CSS animations, View Transitions, responsive pins).
- Fix: export_theme had lost its tool registration.

## 0.7.0 - 2026-11-15

- Server instructions cut to ~1.8k characters: clients truncate long instructions (Claude Code near 4k),
  which hid half the reference. The full reference now comes from the read-only `guide(topic)` tool.
- `make_palette`: OKLCH colour scales (50-950) with WCAG text contrast per step and accent hues.
- `restyle`: replace a colour or font across every canvas and theme style, or set props by filter.
- `path_ops`: flatten, offset (grow / shrink) and outline stroke, turning shapes and lines into paths.
- `review_canvas` design lint: near-miss alignment, radius / stroke / text-size sprawl, hard-coded colours.

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
