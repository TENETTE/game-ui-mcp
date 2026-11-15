"""Website export: canvases become real HTML / CSS pages.

Rects and ellipses become CSS boxes (gradients, borders as box-shadows, shadows, backdrop-filter), text
stays live, selectable text with the fonts copied next to it, other shapes become inline SVG, and only
what the web cannot draw the same way becomes a picture. Links navigate between pages (or open an
overlay), hover / pressed / focused states become CSS, transform and opacity tracks become CSS keyframe
animations, matching element ids animate between pages (View Transitions, Figma "smart animate"), and
pins become responsive anchors.
"""

from __future__ import annotations

import html
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from .animation import apply_animation
from .colors import parse_color
from .render import (LINE_TYPES, TextLayout, _is_fill_stack, _is_none, _scales, _stroke_specs, apply_state,
                     resolve_style)
from . import svgexport

_CSS_KEYS = {"fill", "stroke", "opacity", "shadow", "glow", "inner_shadow", "inner_glow", "blend", "blur",
             "backdrop_blur", "use", "bevel", "adjust", "mask", "mask_mode"}
_TEXT_KEYS = _CSS_KEYS | {"font", "weight", "size", "letter_spacing", "line_height", "uppercase", "italic", "case",
                          "features", "axes", "paragraph_spacing", "indent", "trim"}
_BLENDS = {"multiply", "screen", "overlay", "darken", "lighten", "color-dodge", "color-burn", "hard-light",
           "soft-light", "difference"}
_STATE_PSEUDO = {"hover": ":hover", "pressed": ":active", "focused": ":focus-within"}


def _n(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _ident(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", str(text)) or "x"


class _Ctx:
    def __init__(self, r, theme: dict, out: Path, scale: float) -> None:
        self.r, self.theme, self.out, self.scale = r, theme, out, scale
        self.colors = r.colors
        self.notes: set[str] = set()
        self.raster: list[str] = []
        self.fonts: dict[str, tuple[str, int, bool]] = {}      # css family -> (file, weight, variable)

    def color(self, value: Any) -> str:
        """CSS colour; theme tokens stay live as var(--color-x, fallback) so modes switch them."""
        text = str(value).strip()
        r, g, b, a = parse_color(value, self.colors)
        literal = f"#{r:02x}{g:02x}{b:02x}" if a == 255 else f"rgba({r}, {g}, {b}, {_n(a / 255)})"
        if text.startswith("$") and "@" not in text:
            return f"var(--color-{_ident(text[1:])}, {literal})"
        return literal

    def stops(self, raw: list) -> list[tuple[float, str]]:
        n = len(raw)
        out = []
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                off, col = item.get("offset", i / (n - 1)), item.get("color")
            elif isinstance(item, (list, tuple)) and len(item) == 2 and not isinstance(item[1], (int, float)):
                off, col = item
            else:
                off, col = i / (n - 1), item
            out.append((float(off), self.color(col)))
        return sorted(out)

    def paint(self, spec: Any, w: float, h: float) -> str | None:
        """A CSS background layer for a fill, or None if CSS cannot draw it the same way."""
        if _is_none(spec):
            return None
        if not isinstance(spec, dict):
            c = self.color(spec)
            return f"linear-gradient({c}, {c})"
        kind = str(spec.get("type", "linear")).lower()
        raw = spec.get("stops") or spec.get("colors") or ([spec["from"], spec["to"]] if "from" in spec else None)
        if kind in ("linear", "radial", "conic") and raw and len(raw) >= 2:
            st = self.stops(raw)
            if kind == "linear":
                deg = float(spec.get("angle", 90)) + 90
                return f"linear-gradient({_n(deg)}deg, " + ", ".join(f"{c} {_n(o * 100)}%" for o, c in st) + ")"
            cx, cy = float(spec.get("cx", 0.5)) * 100, float(spec.get("cy", 0.5)) * 100
            if kind == "radial":
                rad = float(spec.get("r", 0.5))
                rx, ry = (max(w, h) * rad,) * 2 if spec.get("circle") else (w * rad, h * rad)
                return (f"radial-gradient(ellipse {_n(rx)}px {_n(ry)}px at {_n(cx)}% {_n(cy)}%, "
                        + ", ".join(f"{c} {_n(o * 100)}%" for o, c in st) + ")")
            a0 = float(spec.get("from", 0))
            span = float(spec.get("to", a0 + 360)) - a0
            return (f"conic-gradient(from {_n(a0 + 90)}deg at {_n(cx)}% {_n(cy)}%, "
                    + ", ".join(f"{c} {_n(o * abs(span))}deg" for o, c in st) + ")")
        if kind == "stripes":
            size = float(spec.get("size", 8))
            ratio = max(0.02, min(0.98, float(spec.get("ratio", 0.5))))
            c1, c2 = self.color(spec.get("color", "#ffffff@0.15")), self.color(spec.get("color2", "transparent"))
            deg = float(spec.get("angle", 45)) + 90
            return (f"repeating-linear-gradient({_n(deg)}deg, {c1} 0 {_n(size * ratio)}px, "
                    f"{c2} {_n(size * ratio)}px {_n(size)}px)")
        return None

    def shadows(self, st: dict, stroke: dict | None) -> list[str] | None:
        out: list[str] = []
        if stroke:
            w, col = stroke["width"], self.color(stroke["paint"])
            if stroke["align"] == "inside":
                out.append(f"inset 0 0 0 {_n(w)}px {col}")
            elif stroke["align"] == "outside":
                out.append(f"0 0 0 {_n(w)}px {col}")
            else:
                out += [f"inset 0 0 0 {_n(w / 2)}px {col}", f"0 0 0 {_n(w / 2)}px {col}"]
        for key, inset in (("inner_shadow", True), ("inner_glow", True), ("shadow", False), ("glow", False)):
            items = st.get(key)
            if _is_none(items):
                continue
            for spec in (items if isinstance(items, list) and items and isinstance(items[0], dict) else [items]):
                if spec is True:
                    spec = {}
                if not isinstance(spec, dict):
                    spec = {"color": spec}
                if spec.get("knockout") or float(spec.get("strength", 1)) != 1:
                    return None
                glow = key in ("glow", "inner_glow")
                default = "#ffffff" if glow else "#000000@0.5"
                col = self.color(spec.get("color", default))
                blur = float(spec.get("blur", 12 if glow else 8))
                dx = 0.0 if glow else float(spec.get("dx", 0))
                dy = 0.0 if glow else float(spec.get("dy", 4 if key == "shadow" else 2))
                out.append(f"{'inset ' if inset else ''}{_n(dx)}px {_n(dy)}px {_n(blur)}px {_n(float(spec.get('spread', 0)))}px {col}")
        bevel = st.get("bevel")
        if bevel:
            b = bevel if isinstance(bevel, dict) else {}
            size = float(b.get("size", 2))
            out += [f"inset 0 {_n(size)}px {_n(size)}px {self.color(b.get('highlight', '#ffffff@0.35'))}",
                    f"inset 0 {_n(-size)}px {_n(size)}px {self.color(b.get('shadow', '#000000@0.35'))}"]
        return out

    def font_face(self, layout: TextLayout) -> str:
        family = re.sub(r"[^\w \-]", "", str(layout.family or "Inter")) or "Inter"   # no quotes / braces into CSS
        if not layout.path:
            return f"'{family}', system-ui, sans-serif"
        src = Path(layout.path)
        variable = layout.var_weight is not None
        css_name = f"{family}" if variable else f"{family} {layout.weight}"
        if css_name not in self.fonts:
            fonts = self.out / "assets" / "fonts"
            fonts.mkdir(parents=True, exist_ok=True)
            target = fonts / src.name
            if not target.exists():
                shutil.copyfile(src, target)
                for lic in ("OFL.txt", "LICENSE", "LICENSE.txt"):
                    if (src.parent / lic).is_file() and not (fonts / f"{src.stem}-{lic}").exists():
                        shutil.copyfile(src.parent / lic, fonts / f"{src.stem}-{lic}")
            self.fonts[css_name] = (src.name, layout.weight, variable)
        return f"'{css_name}', system-ui, sans-serif"


def _box_css(left: float, top: float, w: float, h: float) -> dict:
    return {"left": f"{_n(left)}px", "top": f"{_n(top)}px", "width": f"{_n(w)}px", "height": f"{_n(h)}px"}


def _transform(el: dict, box, pivot) -> dict:
    parts = []
    rot = float(el.get("rotation", 0) or 0)
    if rot:
        parts.append(f"rotate({_n(rot)}deg)")
    sx, sy = _scales(el)
    if sx != 1 or sy != 1:
        parts.append(f"scale({_n(sx)}, {_n(sy)})")
    skew = float(el.get("skew", 0) or 0)
    if skew and el["type"] != "text":
        parts.append(f"skewX({_n(-skew)}deg)")
    if not parts:
        return {}
    return {"transform": " ".join(parts),
            "transform-origin": f"{_n(pivot[0] - box[0])}px {_n(pivot[1] - box[1])}px"}


def _style_css(ctx: _Ctx, el: dict, st: dict, w: float, h: float, text: bool = False) -> dict | None:
    """CSS for fills, borders, shadows and filters - None if any part needs the picture fallback."""
    css: dict[str, str] = {}
    fills = st.get("fill", "#ffffff" if not st.get("stroke") else None) if not text else None
    if fills is not None:
        layers = []
        for one in (fills if _is_fill_stack(fills) else [fills]):
            if isinstance(one, dict) and "blend" in one:
                return None
            p = ctx.paint(one, w, h)
            if p is None and not _is_none(one):
                return None
            if p:
                layers.append(p)
        if layers:
            css["background"] = ", ".join(reversed(layers))          # CSS lists the top layer first
    strokes = _stroke_specs(st, ctx.colors) if not text else []
    if len(strokes) > 1 or any(s["dash"] or s["offset"] or s["sides"] or isinstance(s["paint"], dict) for s in strokes):
        return None
    shadows = ctx.shadows(st, strokes[0] if strokes else None) if not text else []
    if shadows is None:
        return None
    if shadows:
        css["box-shadow"] = ", ".join(shadows)
    if float(st.get("opacity", 1)) < 1:
        css["opacity"] = _n(float(st["opacity"]))
    blend = str(st.get("blend", "normal")).lower()
    if blend != "normal":
        if blend not in _BLENDS:
            return None
        css["mix-blend-mode"] = blend
    filters = []
    if st.get("blur"):
        if isinstance(st["blur"], dict):
            return None
        filters.append(f"blur({_n(float(st['blur']) / 2)}px)")
    adj = st.get("adjust")
    if adj:
        if not isinstance(adj, dict) or set(adj) - {"brightness", "contrast", "saturation", "hue", "invert"}:
            return None
        if adj.get("brightness"):
            filters.append(f"brightness({_n(1 + float(adj['brightness']))})")
        if adj.get("contrast"):
            filters.append(f"contrast({_n(1 + float(adj['contrast']))})")
        if adj.get("saturation"):
            filters.append(f"saturate({_n(1 + float(adj['saturation']))})")
        if adj.get("hue"):
            filters.append(f"hue-rotate({_n(float(adj['hue']))}deg)")
        if adj.get("invert"):
            filters.append("invert(1)")
    if filters:
        css["filter"] = " ".join(filters)
    if st.get("backdrop_blur"):
        bb = st["backdrop_blur"]
        radius = float(bb.get("radius", 8)) if isinstance(bb, dict) else float(bb)
        if isinstance(bb, dict):
            ctx.notes.add("progressive background blur is a uniform backdrop-filter on the web")
        css["backdrop-filter"] = css["-webkit-backdrop-filter"] = f"blur({_n(radius / 2)}px)"
    if not _is_none(st.get("mask")):
        if str(st.get("mask_mode", "alpha")) != "alpha":
            return None
        m = ctx.paint(st["mask"], w, h)
        if m is None:
            return None
        css["-webkit-mask-image"] = css["mask-image"] = m
    return css


def _text_html(ctx: _Ctx, el: dict, st: dict) -> tuple[str, dict] | None:
    r = ctx.r
    if el.get("arc") or el.get("path"):
        return None
    if set(st) - _TEXT_KEYS:
        return None
    layout = TextLayout(el, st, r.fonts, 1.0, r.text_of(el), r.text_lang(el))
    if layout.shaper and any(layout.shaper.color_runs.get(line) for line, _ in layout.lines):
        ctx.notes.add("colour emoji are left to the browser's emoji font")
    x0, y0, x1, y1 = layout.box_px
    lh = layout.size * layout.line_height
    top = layout.baselines[0] - (lh - (layout.ascent + layout.descent)) / 2 - layout.ascent
    css: dict[str, str] = {"left": f"{_n(x0)}px", "top": f"{_n(top)}px", "font-family": ctx.font_face(layout),
                           "font-size": f"{_n(layout.size)}px", "font-weight": str(layout.weight),
                           "line-height": f"{_n(lh)}px", "text-align": layout.align,
                           "white-space": "pre-wrap" if el.get("max_width") else "pre"}
    if el.get("max_width"):
        css["width"] = f"{_n(float(el['max_width']))}px"
    if layout.spacing:
        css["letter-spacing"] = f"{_n(layout.spacing)}px"
    if bool(el.get("italic", st.get("italic"))):
        css["font-style"] = "italic"
    fill = st.get("fill", el.get("fill", "#ffffff"))
    if isinstance(fill, dict):
        p = ctx.paint(fill, x1 - x0, y1 - y0)
        if p is None:
            return None
        css.update({"background": p, "-webkit-background-clip": "text", "background-clip": "text", "color": "transparent"})
    elif _is_fill_stack(fill):
        return None
    else:
        css["color"] = ctx.color(fill)
    strokes = _stroke_specs(st, ctx.colors)
    if len(strokes) > 1 or (strokes and isinstance(strokes[0]["paint"], dict)):
        return None
    if strokes:
        css["-webkit-text-stroke"] = f"{_n(strokes[0]['width'] * 2)}px {ctx.color(strokes[0]['paint'])}"
        css["paint-order"] = "stroke fill"
    shadows = []
    for key in ("shadow", "glow"):
        items = st.get(key)
        if _is_none(items):
            continue
        for spec in (items if isinstance(items, list) and items and isinstance(items[0], dict) else [items]):
            spec = spec if isinstance(spec, dict) else ({} if spec is True else {"color": spec})
            glow = key == "glow"
            col = ctx.color(spec.get("color", "#ffffff" if glow else "#000000@0.5"))
            shadows.append(f"{_n(0 if glow else float(spec.get('dx', 0)))}px {_n(0 if glow else float(spec.get('dy', 4)))}px "
                           f"{_n(float(spec.get('blur', 12 if glow else 8)))}px {col}")
    if shadows:
        css["text-shadow"] = ", ".join(shadows)
    case = str(el.get("case", st.get("case")) or "").lower()
    upper = el.get("uppercase", st.get("uppercase"))
    if upper or case in ("upper", "tr-upper"):
        css["text-transform"] = "uppercase"
    elif case in ("lower", "tr-lower"):
        css["text-transform"] = "lowercase"
    elif case in ("title", "capitalize"):
        css["text-transform"] = "capitalize"
    elif case in ("small-caps", "all-small-caps"):
        css["font-variant-caps"] = case
    if layout.decoration != "none":
        css["text-decoration"] = "underline" if layout.decoration == "underline" else "line-through"
    if layout.features:
        css["font-feature-settings"] = ", ".join(f'"{k}" {v}' for k, v in layout.features.items())
    if layout.axes:
        css["font-variation-settings"] = ", ".join(f'"{k}" {_n(v)}' for k, v in layout.axes)
    if el.get("max_lines"):
        css.update({"display": "-webkit-box", "-webkit-box-orient": "vertical",
                    "-webkit-line-clamp": str(int(el["max_lines"])), "overflow": "hidden"})
    if float(st.get("opacity", 1)) < 1:
        css["opacity"] = _n(float(st["opacity"]))
    raw = r.text_of(el)
    lang = layout.lang or ("tr" if str(upper).lower() == "tr" or case.startswith("tr") else None)
    attrs = f' lang="{html.escape(lang)}"' if lang else ""
    list_style = str(el.get("list") or "none")
    para_gap = float(el.get("paragraph_spacing", st.get("paragraph_spacing", 0)) or 0)
    indent = float(el.get("indent", st.get("indent", 0)) or 0)
    paras = raw.split("\n")
    if list_style in ("bullet", "number"):
        tag = "ul" if list_style == "bullet" else "ol"
        css.update({"margin": "0", "padding-left": "1.3em", "white-space": "normal"})
        items = "".join(f'<li style="margin-bottom:{_n(para_gap)}px">{html.escape(p)}</li>' for p in paras if p.strip())
        return f"<{tag}{attrs}>{items}</{tag}>", css
    if para_gap or indent:
        body = "".join(f'<p style="margin:0 0 {_n(para_gap)}px 0;text-indent:{_n(indent)}px">{html.escape(p)}</p>'
                       for p in paras)
        return f"<div{attrs}>{body}</div>", css
    return f"<div{attrs}>{html.escape(raw)}</div>", css


def _element(ctx: _Ctx, el: dict, svg_doc) -> tuple[str, dict, str] | None:
    """(tag-body, css, kind) for one element, or None for the picture fallback."""
    r = ctx.r
    el = r._nums(el)
    st = r._nums(resolve_style(el.get("style"), ctx.theme))
    if el.get("clip") or st.get("glass") or st.get("texture") or st.get("light") or st.get("noise"):
        return None
    t = el["type"]
    if t == "text":
        got = _text_html(ctx, el, st)
        if got is None:
            return None
        inner, css = got
        return inner, css, "text"
    if t in ("rect", "ellipse"):
        if set(st) - _CSS_KEYS:
            return None
        if t == "rect" and (str(el.get("corner", "round")) == "cut" or el.get("smoothing")):
            return _svg(ctx, el, st, svg_doc)
        box = r._geom_box(el, st)
        w, h = box[2] - box[0], box[3] - box[1]
        css = _style_css(ctx, el, st, w, h)
        if css is None:
            return None
        css.update(_box_css(box[0], box[1], w, h))
        if t == "ellipse":
            css["border-radius"] = "50%"
        elif el.get("radius"):
            rad = el["radius"]
            css["border-radius"] = " ".join(f"{_n(float(v))}px" for v in (rad if isinstance(rad, list) else [rad]))
        css.update(_transform(el, box, r._pivot(el, box)))
        return "", css, "box"
    if t == "image":
        box = r._geom_box(el, st)
        if set(st) - _CSS_KEYS or el.get("crop") or el.get("tint"):
            return None
        src = Path(str(el.get("path", ""))).expanduser()
        if not src.is_file():
            return None
        assets = ctx.out / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        name = f"{_ident(el['id'])}{src.suffix.lower()}"
        shutil.copyfile(src, assets / name)
        css = _style_css(ctx, el, {k: v for k, v in st.items() if k != "fill"}, box[2] - box[0], box[3] - box[1]) or {}
        css.update(_box_css(box[0], box[1], box[2] - box[0], box[3] - box[1]))
        css["object-fit"] = {"contain": "contain", "cover": "cover"}.get(str(el.get("fit", "stretch")), "fill")
        if el.get("radius"):
            css["border-radius"] = f"{_n(float(el['radius'] if not isinstance(el['radius'], list) else el['radius'][0]))}px"
        css.update(_transform(el, box, r._pivot(el, box)))
        return f'assets/{name}', css, "img"
    if t in ("polygon", "path", "boolean") or t in LINE_TYPES:
        return _svg(ctx, el, st, svg_doc)
    return None


def _svg(ctx: _Ctx, el: dict, st: dict, svg_doc) -> tuple[str, dict, str] | None:
    mark_body, mark_defs = len(svg_doc.body), len(svg_doc.defs)
    if not svgexport._vector_element(svg_doc, ctx.r, el, st):
        del svg_doc.body[mark_body:]
        del svg_doc.defs[mark_defs:]
        return None
    defs = "".join(svg_doc.defs[mark_defs:])
    body = "".join(svg_doc.body[mark_body:])
    del svg_doc.body[mark_body:]
    W, H = ctx.r.canvas["width"], ctx.r.canvas["height"]
    svg = (f'<svg viewBox="0 0 {_n(W)} {_n(H)}" width="{_n(W)}" height="{_n(H)}" aria-hidden="true">'
           + (f"<defs>{defs}</defs>" if defs else "") + body + "</svg>")
    return svg, {"left": "0", "top": "0", "width": f"{_n(W)}px", "height": f"{_n(H)}px"}, "svg"


def _picture(ctx: _Ctx, el: dict, big, beneath: list[str], include_background: bool) -> tuple[str, dict] | None:
    st = ctx.r._nums(resolve_style(el.get("style"), ctx.theme))
    result = big.render_element(el)
    if result is None:
        return None
    img, px0, py0, _ = result
    if st.get("glass"):
        from PIL import ImageChops
        scene = big.render(include_background=include_background, only=set(beneath))
        patch = scene.crop((px0, py0, px0 + img.width, py0 + img.height))
        foot = ImageChops.lighter(img.getchannel("A"), big.shape_mask(el, (px0, py0, img.width, img.height)))
        patch.putalpha(ImageChops.multiply(patch.getchannel("A"), foot))
        img = patch
    assets = ctx.out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    name = f"{_ident(ctx.r.canvas['id'])}-{_ident(el['id'])}.png"
    img.save(assets / name, optimize=True)
    ctx.raster.append(el["id"])
    s = ctx.scale
    return f"assets/{name}", _box_css(px0 / s, py0 / s, img.width / s, img.height / s)


def _anchor(css: dict, el: dict, W: float, H: float) -> None:
    """Responsive mode: pins become left / right / centre / stretch anchors."""
    pin = el.get("pin") or {}
    if not pin or not str(css.get("left", "")).endswith("px") or not str(css.get("width", "")).endswith("px"):
        return
    left, top = float(css["left"][:-2]), float(css["top"][:-2])
    w = float(css["width"][:-2])
    h = float(css["height"][:-2]) if css.get("height", "").endswith("px") else 0.0
    px, py = pin.get("x", "left"), pin.get("y", "top")
    if px == "right":
        css.pop("left")
        css["right"] = f"{_n(W - left - w)}px"
    elif px == "center":
        css["left"] = f"calc(50% + {_n(left - W / 2)}px)"
    elif px == "stretch":
        css["right"] = f"{_n(W - left - w)}px"
        css.pop("width")
    elif px == "scale":
        css["left"], css["width"] = f"{_n(left / W * 100)}%", f"{_n(w / W * 100)}%"
    if py == "bottom":
        css.pop("top")
        css["bottom"] = f"{_n(H - top - h)}px"
    elif py == "middle":
        css["top"] = f"calc(50% + {_n(top - H / 2)}px)"
    elif py == "stretch" and h:
        css["bottom"] = f"{_n(H - top - h)}px"
        css.pop("height", None)
    elif py == "scale" and h:
        css["top"], css["height"] = f"{_n(top / H * 100)}%", f"{_n(h / H * 100)}%"


def _css_decl(css: dict) -> str:
    return ";".join(f"{k}:{v}" for k, v in css.items())


def _keyframes(ctx: _Ctx, canvas: dict, name: str, anim: dict, ids: set[str]) -> tuple[str, dict[str, str]]:
    """CSS @keyframes for move / rotate / scale / fade tracks; returns (css, {element id: animation decl})."""
    tracks = anim.get("tracks", [])
    if not tracks:
        return "", {}
    duration = float(anim.get("duration", 1))
    steps = max(2, min(60, int(duration * 20) + 1))
    colors = ctx.colors
    poses = [apply_animation(canvas, anim, duration * i / (steps - 1), colors)[0] for i in range(steps)]
    base = {e["id"]: e for e in canvas["elements"]}
    rules, decls = [], {}
    for eid in ids:
        frames = [next((e for e in p["elements"] if e["id"] == eid), None) for p in poses]
        if any(f is None for f in frames) or all(f == base[eid] for f in frames):
            continue
        keep = {"x", "y", "cx", "cy", "points", "origin", "rotation", "scale", "scale_x", "scale_y", "style"}
        if any({k for k in set(f) | set(base[eid]) if f.get(k) != base[eid].get(k)} - keep for f in frames):
            ctx.notes.add(f"{eid}: only movement, rotation, scale and opacity animate on the web")
        b = base[eid]
        bx, by = (float(b["points"][0][0]), float(b["points"][0][1])) if b.get("points") else \
            ((float(b["cx"]), float(b["cy"])) if "cx" in b else (float(b.get("x", 0)), float(b.get("y", 0))))
        lines = []
        for i, f in enumerate(frames):
            fx, fy = (float(f["points"][0][0]), float(f["points"][0][1])) if f.get("points") else \
                ((float(f["cx"]), float(f["cy"])) if "cx" in f else (float(f.get("x", 0)), float(f.get("y", 0))))
            sx, sy = _scales(f)
            rot = float(f.get("rotation", 0) or 0)
            op = float(resolve_style(f.get("style"), ctx.theme).get("opacity", 1))
            lines.append(f"{_n(100 * i / (steps - 1))}%{{transform:translate({_n(fx - bx)}px,{_n(fy - by)}px) "
                         f"rotate({_n(rot)}deg) scale({_n(sx)},{_n(sy)});opacity:{_n(op)}}}")
        kf = f"a-{_ident(canvas['id'])}-{_ident(name)}-{_ident(eid)}"
        rules.append(f"@keyframes {kf}{{{''.join(lines)}}}")
        decls[eid] = f"{kf} {_n(duration)}s linear {'infinite' if anim.get('loop') else '1 both'}"
    return "\n".join(rules), decls


def build(store, canvases: list[dict], out: Path, layout: str, language: str | None, include_background: bool,
          image_scale: float) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    pages = {c["id"]: ("index.html" if i == 0 else f"{_ident(c['id'])}.html") for i, c in enumerate(canvases)}
    theme = store.theme
    css_rules: list[str] = []
    all_fonts: dict[str, tuple[str, int, bool]] = {}
    report = {"pages": [], "picture": [], "notes": set()}
    # theme tokens and modes as CSS custom properties, so data-theme switches them live
    root = [f"--color-{_ident(k)}:{_hex(v, theme)}" for k, v in theme.get("colors", {}).items()]
    css_rules.append(":root{" + ";".join(root) + "}")
    for mode, vals in (theme.get("modes") or {}).items():
        decl = [f"--color-{_ident(k)}:{_hex(v, {**theme.get('colors', {}), **vals})}" for k, v in vals.items()
                if not isinstance(v, (int, float))]
        if decl:
            css_rules.append(f'[data-theme="{_ident(mode)}"]{{' + ";".join(decl) + "}")
    for c in canvases:
        r = store.renderer(c, 1.0, language=language)
        big = r.derive(max(1.0, min(8.0, image_scale)))
        ctx = _Ctx(r, theme, out, max(1.0, min(8.0, image_scale)))
        svg_doc = svgexport._Doc()
        W, H = float(c["width"]), float(c["height"])
        links = c.get("links") or {}
        groups_with_links = {el.get("group") for el in c["elements"] if el["id"] in links and el.get("group")}
        body: list[str] = []
        beneath: list[str] = []
        page_css: list[str] = []
        states = c.get("states") or {}
        focus = {eid: i + 1 for i, eid in enumerate(c.get("focus_order") or [])}
        open_group = None
        for lay, elements in r.stack():
            props = r.layer_props(lay) if lay else None
            if props and not props["visible"]:
                continue
            if props and (props["style"] or props["clip"]):
                ids = {e["id"] for e in elements}
                beneath.extend(ids)
                img = big.render(include_background=False, only=ids)
                bbox = img.getchannel("A").getbbox()
                if bbox:
                    (out / "assets").mkdir(parents=True, exist_ok=True)
                    name = f"{_ident(c['id'])}-layer-{_ident(props['name'])}.png"
                    img.crop(bbox).save(out / "assets" / name, optimize=True)
                    s = ctx.scale
                    body.append(f'<img class="el" alt="" src="assets/{name}" style="{_css_decl(_box_css(bbox[0] / s, bbox[1] / s, (bbox[2] - bbox[0]) / s, (bbox[3] - bbox[1]) / s))}">')
                    report["picture"].append(f"{c['id']}:layer:{props['name']}")
                continue
            layer_open = bool(props and (props["opacity"] < 1 or props["blend"] != "normal"))
            if layer_open:
                lstyle = {"position": "absolute", "inset": "0", "pointer-events": "none"}
                if props["opacity"] < 1:
                    lstyle["opacity"] = _n(props["opacity"])
                if props["blend"] != "normal" and props["blend"] in _BLENDS:
                    lstyle["mix-blend-mode"] = props["blend"]
                body.append(f'<div class="layer" style="{_css_decl(lstyle)}">')
            for el in elements:
                beneath.append(el["id"])
                grp = el.get("group")
                if grp != open_group:
                    if open_group is not None:
                        body.append("</div>")
                    if grp:
                        attrs = ""
                        if grp in groups_with_links:
                            target = links[next(e["id"] for e in c["elements"] if e.get("group") == grp and e["id"] in links)]
                            attrs = _link_attrs(target, pages, focus.get(grp, 0))
                        body.append(f'<div class="g g-{_ident(grp)}"{attrs}>')
                    open_group = grp
                got = _element(ctx, el, svg_doc)
                eid = _ident(el["id"])
                vt = f"view-transition-name:v-{eid}"
                extra_attrs = ""
                if el["id"] in links and not grp:
                    extra_attrs = _link_attrs(links[el["id"]], pages, focus.get(el["id"], 0))
                if el.get("note"):
                    extra_attrs += f' title="{html.escape(str(el["note"]))}"'
                if got is None:
                    pic = _picture(ctx, el, big, beneath, include_background)
                    if pic is None:
                        continue
                    src, css = pic
                    if layout == "responsive":
                        _anchor(css, el, W, H)
                    if el.get("fixed"):
                        css["position"] = "fixed"
                    body.append(f'<img class="el" id="{eid}" alt="" src="{src}" style="{_css_decl(css)};{vt}"{extra_attrs}>')
                    continue
                inner, css, kind = got
                if layout == "responsive":
                    _anchor(css, el, W, H)
                if el.get("fixed"):
                    css["position"] = "fixed"
                decl = _css_decl(css) + ";" + vt
                if kind == "img":
                    body.append(f'<img class="el" id="{eid}" alt="" src="{inner}" style="{decl}"{extra_attrs}>')
                elif kind == "svg":
                    body.append(f'<div class="el vec" id="{eid}" style="{decl}"{extra_attrs}>{inner}</div>')
                else:
                    body.append(f'<div class="el {kind}" id="{eid}" style="{decl}"{extra_attrs}>{inner}</div>')
            if open_group is not None:
                body.append("</div>")
                open_group = None
            if layer_open:
                body.append("</div>")
        # interaction states -> CSS
        for state, pseudo in _STATE_PSEUDO.items():
            if state not in states:
                continue
            posed = apply_state(c, state)
            pr = store.renderer(posed, 1.0, language=language)
            pctx = _Ctx(pr, theme, out, ctx.scale)
            for eid in states[state]:
                base_el = next((e for e in c["elements"] if e["id"] == eid), None)
                new_el = next((e for e in posed["elements"] if e["id"] == eid), None)
                if base_el is None or new_el is None:
                    continue
                a, b = _element(ctx, base_el, svgexport._Doc()), _element(pctx, new_el, svgexport._Doc())
                if a is None or b is None or a[2] in ("svg", "img"):
                    report["notes"].add(f"{c['id']}: the {state} look of '{eid}' is not reproduced on the web")
                    continue
                diff = {k: v for k, v in b[1].items() if a[1].get(k) != v}
                if not diff:
                    continue
                sel = (f".g-{_ident(base_el['group'])}{pseudo} #{_ident(eid)}" if base_el.get("group")
                       else f"#{_ident(eid)}{pseudo}")
                page_css.append(f"{sel}{{{_css_decl(diff)};transition:all .15s ease}}")
        for name, anim in (c.get("animations") or {}).items():
            rules, decls = _keyframes(ctx, c, name, anim, {e["id"] for e in c["elements"]})
            if rules:
                page_css.append(rules)
                for eid, decl in decls.items():
                    page_css.append(f"#{_ident(eid)}{{animation:{decl}}}")
        bg = c.get("background") if include_background else None
        bg_css = ""
        if bg and not _is_none(bg):
            if isinstance(bg, str) and not bg.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                bg_css = f"background:{ctx.color(bg)};"
            else:
                (out / "assets").mkdir(parents=True, exist_ok=True)
                name = f"{_ident(c['id'])}-background.png"
                big.render(include_background=True, only=set()).save(out / "assets" / name, optimize=True)
                bg_css = f"background:url(assets/{name}) center/cover;"
        size = (f"width:{_n(W)}px;height:{_n(H)}px" if layout == "fit"
                else f"width:100vw;min-height:max(100vh,{_n(H)}px)")
        fonts_css = "".join(
            f"@font-face{{font-family:'{fam}';src:url(assets/fonts/{file});font-weight:{'100 900' if var else w};font-display:swap}}"
            for fam, (file, w, var) in ctx.fonts.items())
        all_fonts.update(ctx.fonts)
        doc = (f"<!doctype html>\n<html lang=\"{html.escape(language or 'en')}\"><head><meta charset=\"utf-8\">"
               f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
               f"<title>{html.escape(str(c.get('name') or c['id']))}</title>"
               f"<link rel=\"stylesheet\" href=\"styles.css\"><style>{fonts_css}"
               f".page{{{size};{bg_css}}}{''.join(page_css)}</style></head>"
               f"<body class=\"{layout}\"><main class=\"page\" data-w=\"{_n(W)}\" data-h=\"{_n(H)}\">"
               + "\n".join(body) + "</main><script src=\"app.js\"></script></body></html>\n")
        (out / pages[c["id"]]).write_text(doc, encoding="utf-8")
        report["pages"].append(pages[c["id"]])
        report["picture"] += [f"{c['id']}:{i}" for i in ctx.raster]
        report["notes"] |= ctx.notes
    css_rules.append(_BASE_CSS)
    (out / "styles.css").write_text("\n".join(css_rules) + "\n", encoding="utf-8")
    (out / "app.js").write_text(_APP_JS, encoding="utf-8")
    report["notes"] = sorted(report["notes"])
    return report


def _hex(value: Any, theme_colors: Any) -> str:
    colors = theme_colors.get("colors", theme_colors) if isinstance(theme_colors, dict) else {}
    r, g, b, a = parse_color(value, colors)
    return f"#{r:02x}{g:02x}{b:02x}" if a == 255 else f"rgba({r}, {g}, {b}, {_n(a / 255)})"


def _link_attrs(target: Any, pages: dict, tab: int) -> str:
    if isinstance(target, dict):
        mode, dest = str(target.get("mode", "navigate")), target.get("target") or target.get("url")
    else:
        mode, dest = "navigate", target
    if dest == "back":
        href = "#back"
    elif mode == "url":
        href = str(dest)
    else:
        href = pages.get(str(dest), f"{_ident(str(dest))}.html")
    attr = f' data-href="{html.escape(href)}" data-mode="{html.escape(mode)}" role="link" tabindex="{tab or 0}"'
    return attr


_BASE_CSS = """*{box-sizing:border-box}
html,body{margin:0;background:#0b0b10;color:#fff}
@view-transition{navigation:auto}
body.fit{display:grid;place-items:center;min-height:100vh;overflow:hidden}
.page{position:relative;overflow:hidden}
body.fit .page{transform-origin:center center}
.el{position:absolute;margin:0}
.text{overflow-wrap:break-word}
.vec svg{display:block;overflow:visible;pointer-events:none}
.vec svg *{pointer-events:visiblePainted}
.vec{pointer-events:none}
.g{display:contents}
[data-href]{cursor:pointer}
[data-href]:focus-visible{outline:2px solid #fff;outline-offset:2px}
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);display:grid;place-items:center;z-index:9999}
.overlay iframe{border:0;width:min(92vw,var(--ow));height:min(92vh,var(--oh));background:transparent}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

_APP_JS = """// game-ui-mcp web export: fit-to-window scaling, links, overlays
(function () {
  const page = document.querySelector('.page');
  function fit() {
    if (!document.body.classList.contains('fit') || !page) return;
    const w = +page.dataset.w, h = +page.dataset.h;
    const s = Math.min(innerWidth / w, innerHeight / h);
    page.style.transform = 'scale(' + s + ')';
  }
  addEventListener('resize', fit); fit();
  function go(el) {
    const href = el.dataset.href, mode = el.dataset.mode;
    if (href === '#back') { if (window.parent !== window) { parent.postMessage('close-overlay', '*'); } else { history.back(); } return; }
    if (mode === 'overlay') {
      const o = document.createElement('div'); o.className = 'overlay';
      const f = document.createElement('iframe'); f.src = href;
      f.addEventListener('load', () => { const p = f.contentDocument && f.contentDocument.querySelector('.page');
        if (p) { o.style.setProperty('--ow', p.dataset.w + 'px'); o.style.setProperty('--oh', p.dataset.h + 'px'); } });
      o.appendChild(f); o.addEventListener('click', e => { if (e.target === o) o.remove(); });
      document.body.appendChild(o); return;
    }
    if (mode === 'url') { window.open(href, '_blank', 'noopener'); return; }
    if (window.parent !== window && mode !== 'navigate-top') { location.href = href; return; }
    location.href = href;
  }
  addEventListener('message', e => { if (e.data === 'close-overlay') document.querySelectorAll('.overlay').forEach(o => o.remove()); });
  document.addEventListener('click', e => { const el = e.target.closest('[data-href]'); if (el) go(el); });
  document.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-href]')) { e.preventDefault(); go(e.target); } });
})();
"""
