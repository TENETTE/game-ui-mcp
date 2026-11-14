"""Vector SVG export that Figma, Penpot, Illustrator and Inkscape open as editable shapes and text.

Shapes, gradients, strokes, drop shadows / glows and text become real SVG. Anything SVG cannot show
the same way (shader and pattern fills, light, inner effects, blur, special blends, clipped or styled
layers, images) is embedded as a picture of just that element, so the file always looks like the PNG.
"""

from __future__ import annotations

import base64
import io
import math
from html import escape
from typing import Any

from .colors import parse_color
from .render import (LINE_TYPES, VECTOR_TYPES, Renderer, TextLayout, _is_fill_stack, _is_none, _skew_tan,
                     _stroke_specs, resolve_style, vector_geometry)
from . import geometry as geo

RASTER_SCALE = 2.0                      # embedded fallbacks are drawn at 2x so they stay crisp when zoomed
_VECTOR_KEYS = {"fill", "stroke", "opacity", "shadow", "glow", "blend", "use"}
_TEXT_KEYS = _VECTOR_KEYS | {"font", "weight", "size", "letter_spacing", "line_height", "uppercase", "italic"}


def _num(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _rgba(value: Any, colors: dict) -> tuple[str, float]:
    r, g, b, a = parse_color(value, colors)
    return f"#{r:02x}{g:02x}{b:02x}", a / 255


class _Doc:
    def __init__(self) -> None:
        self.defs: list[str] = []
        self.body: list[str] = []
        self.notes: set[str] = set()
        self.raster: list[str] = []
        self._n = 0

    def uid(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"


def _paint(doc: _Doc, spec: Any, box, colors: dict) -> tuple[str, float] | None:
    """SVG paint (colour or gradient url) for a fill spec, or None if SVG cannot express it."""
    if _is_none(spec):
        return "none", 1.0
    if not isinstance(spec, dict):
        return _rgba(spec, colors)
    kind = str(spec.get("type", "linear")).lower()
    if kind not in ("linear", "radial"):
        return None
    raw = spec.get("stops") or spec.get("colors") or ([spec["from"], spec["to"]] if "from" in spec else None)
    if not raw or len(raw) < 2:
        return None
    n = len(raw)
    stops = []
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            off, col = item.get("offset", i / (n - 1)), item.get("color")
        elif isinstance(item, (list, tuple)) and len(item) == 2 and not isinstance(item[1], (int, float)):
            off, col = item
        else:
            off, col = i / (n - 1), item
        hexc, a = _rgba(col, colors)
        stops.append(f'<stop offset="{_num(float(off))}" stop-color="{hexc}"'
                     + (f' stop-opacity="{_num(a)}"' if a < 1 else "") + "/>")
    x0, y0, x1, y1 = box
    bw, bh = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
    gid = doc.uid("g")
    if kind == "linear":
        ang = math.radians(float(spec.get("angle", 90)))
        ca, sa = math.cos(ang), math.sin(ang)
        half = (abs(bw * ca) + abs(bh * sa)) / 2
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        doc.defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{_num(cx - ca * half)}" '
                        f'y1="{_num(cy - sa * half)}" x2="{_num(cx + ca * half)}" y2="{_num(cy + sa * half)}">'
                        + "".join(stops) + "</linearGradient>")
    else:
        cx, cy = x0 + bw * float(spec.get("cx", 0.5)), y0 + bh * float(spec.get("cy", 0.5))
        rad = float(spec.get("r", 0.5))
        rx, ry = (max(bw, bh) * rad,) * 2 if spec.get("circle") else (bw * rad, bh * rad)
        doc.defs.append(f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="1" '
                        f'gradientTransform="translate({_num(cx)} {_num(cy)}) scale({_num(rx)} {_num(ry)})">'
                        + "".join(stops) + "</radialGradient>")
    if str(spec.get("space", "srgb")).lower() != "srgb":
        doc.notes.add("OKLab / linear gradients are written as plain sRGB gradients in SVG (slightly duller middles)")
    return f"url(#{gid})", 1.0


def _filter(doc: _Doc, st: dict, colors: dict) -> str | None:
    """Drop shadows and glows as an SVG filter; '' when there are none, None when SVG cannot match them."""
    parts = []
    for key in ("shadow", "glow"):
        items = st.get(key)
        if _is_none(items):
            continue
        for spec in items if isinstance(items, list) and items and isinstance(items[0], dict) else [items]:
            if spec is True:
                spec = {}
            if not isinstance(spec, dict):
                spec = {"color": spec}
            if spec.get("spread") or spec.get("knockout") or float(spec.get("strength", 1)) != 1:
                return None
            hexc, a = _rgba(spec.get("color", "#000000@0.5" if key == "shadow" else "#ffffff"), colors)
            blur = float(spec.get("blur", 8 if key == "shadow" else 12))
            dx = float(spec.get("dx", 0)) if key == "shadow" else 0.0
            dy = float(spec.get("dy", 4)) if key == "shadow" else 0.0
            parts.append(f'<feDropShadow dx="{_num(dx)}" dy="{_num(dy)}" stdDeviation="{_num(blur / 2)}" '
                         f'flood-color="{hexc}" flood-opacity="{_num(a)}"/>')
    if not parts:
        return ""
    fid = doc.uid("f")
    doc.defs.append(f'<filter id="{fid}" x="-50%" y="-50%" width="200%" height="200%" '
                    f'color-interpolation-filters="sRGB">{"".join(parts)}</filter>')
    return fid


def _points_d(polys) -> str:
    out = []
    for p in polys:
        if len(p) < 2:
            continue
        out.append("M" + " L".join(f"{_num(x)} {_num(y)}" for x, y in p) + " Z")
    return " ".join(out)


def _open_d(pts, closed: bool) -> str:
    return "M" + " L".join(f"{_num(x)} {_num(y)}" for x, y in pts) + (" Z" if closed else "")


def _placed(el: dict, polys, box):
    """Apply the element's skew and rotation to its points (gradients then line up like the PNG)."""
    lean = _skew_tan(el)
    if lean:
        mid = (box[1] + box[3]) / 2
        polys = [[(x + (mid - y) * lean, y) for x, y in p] for p in polys]
    rot = float(el.get("rotation", 0) or 0)
    if rot:
        cx, cy = Renderer._pivot(el, box)
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        polys = [[(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in p] for p in polys]
    return polys


def _vector_element(doc: _Doc, r: Renderer, el: dict, st: dict) -> bool:
    """Write one shape as SVG. False = it needs the raster fallback."""
    if set(st) - _VECTOR_KEYS or el.get("clip") or any(el.get(k) for k in ("scale_x", "scale_y", "flip_x", "flip_y")):
        return False
    if str(st.get("blend", "normal")).lower() != "normal":
        return False
    colors = r.colors
    g = vector_geometry(el, 4.0)
    fills = st.get("fill", "#ffffff" if (el["type"] in LINE_TYPES or not st.get("stroke")) else None)
    fill_list = fills if _is_fill_stack(fills) else [fills]
    filt = _filter(doc, st, colors)
    if filt is None:
        return False
    opacity = float(st.get("opacity", 1))
    rule = str(el.get("fill_rule", "evenodd")).lower() if el["type"] == "path" else "evenodd"
    shapes = _placed(el, g.fills, g.box)
    rbox = geo.bbox_of(shapes) if shapes else g.box
    lines = [(_placed(el, [pts], g.box)[0], closed, width, cap) for pts, closed, width, cap in g.lines]
    if lines and not shapes:
        rbox = geo.bbox_of([p for p, _, _, _ in lines])
    paint_ref = g.paint_box or rbox
    out: list[str] = []
    d = _points_d(shapes)
    if el["type"] in LINE_TYPES:
        paint = _paint(doc, fill_list[0], paint_ref, colors)
        if paint is None or len(fill_list) > 1:
            return False
        for pts, closed, width, cap in lines:
            out.append(f'<path d="{_open_d(pts, closed)}" fill="none" stroke="{paint[0]}" stroke-width="{_num(width)}" '
                       f'stroke-linecap="{ "round" if cap == "round" else "butt"}" stroke-linejoin="round"'
                       + (f' stroke-opacity="{_num(paint[1])}"' if paint[1] < 1 else "") + "/>")
    else:
        strokes = _stroke_specs(st, colors)
        if any(s["offset"] for s in strokes):
            return False
        under, over = [], []
        clip_id = None
        for s in strokes:
            paint = _paint(doc, s["paint"], paint_ref, colors)
            if paint is None:
                return False
            width = s["width"] * (1 if s["align"] == "center" else 2)
            dash = f' stroke-dasharray="{" ".join(_num(v) for v in s["dash"])}"' if s["dash"] else ""
            join = s["join"] or ("miter" if g.kind == "poly" else "round")
            attrs = (f'd="{d}" fill="none" stroke="{paint[0]}" stroke-width="{_num(width)}" stroke-linejoin="{join}"'
                     f' stroke-miterlimit="{_num(s["miter_limit"])}"{dash}'
                     + (f' stroke-opacity="{_num(paint[1])}"' if paint[1] < 1 else ""))
            if s["align"] == "outside":
                under.append(f"<path {attrs}/>")              # the fill hides the inner half
            elif s["align"] == "inside":
                if clip_id is None:
                    clip_id = doc.uid("c")
                    doc.defs.append(f'<clipPath id="{clip_id}"><path d="{d}" fill-rule="{rule}"/></clipPath>')
                over.append(f'<path {attrs} clip-path="url(#{clip_id})"/>')
            else:
                over.append(f"<path {attrs}/>")
        out += under
        for one in fill_list:
            paint = _paint(doc, one, paint_ref, colors)
            if paint is None:
                return False
            if paint[0] != "none":
                out.append(f'<path d="{d}" fill="{paint[0]}" fill-rule="{rule}"'
                           + (f' fill-opacity="{_num(paint[1])}"' if paint[1] < 1 else "") + "/>")
        out += over
    attrs = f' id="{escape(el["id"], quote=True)}"'
    if filt:
        attrs += f' filter="url(#{filt})"'
    if opacity < 1:
        attrs += f' opacity="{_num(opacity)}"'
    doc.body.append(f"<g{attrs}>" + "".join(out) + "</g>")
    return True


def _text_element(doc: _Doc, r: Renderer, el: dict, st: dict) -> bool:
    if set(st) - _TEXT_KEYS or el.get("arc") or el.get("skew"):
        return False
    if el.get("clip") or str(st.get("blend", "normal")).lower() != "normal" or el.get("fit_width"):
        return False
    colors = r.colors
    layout = TextLayout(el, st, r.fonts, 1.0, r.text_of(el), r.text_lang(el))
    if layout.skew:
        return False
    fill = st.get("fill", "#ffffff")
    if _is_fill_stack(fill):
        return False
    paint = _paint(doc, fill, layout.box_px, colors)
    filt = _filter(doc, st, colors)
    if paint is None or filt is None:
        return False
    strokes = _stroke_specs(st, colors)
    if len(strokes) > 1 or any(s["offset"] for s in strokes):
        return False
    family = layout.family or "Inter"
    attrs = [f'font-family="{escape(family, quote=True)}, sans-serif"', f'font-size="{_num(layout.size)}"',
             f'font-weight="{layout.weight}"', f'fill="{paint[0]}"']
    if paint[1] < 1:
        attrs.append(f'fill-opacity="{_num(paint[1])}"')
    if layout.spacing:
        attrs.append(f'letter-spacing="{_num(layout.spacing)}"')
    if bool(el.get("italic", st.get("italic"))):
        attrs.append('font-style="italic"')
    if strokes:
        sp = _paint(doc, strokes[0]["paint"], layout.box_px, colors)
        if sp is None:
            return False
        attrs += [f'stroke="{sp[0]}"', f'stroke-width="{_num(strokes[0]["width"] * 2)}"', 'stroke-linejoin="round"',
                  'paint-order="stroke"']                     # our text outlines sit outside the glyphs
    anchor = {"center": "middle", "right": "end"}.get(layout.align, "start")
    attrs.append(f'text-anchor="{anchor}"')
    if layout.lang:
        attrs.append(f'xml:lang="{escape(layout.lang, quote=True)}"')
    spans = []
    for (line, width), lx, base in zip(layout.lines, layout.line_x, layout.baselines):
        ax = lx + (width / 2 if anchor == "middle" else width if anchor == "end" else 0)
        spans.append(f'<tspan x="{_num(ax)}" y="{_num(base)}">{escape(line)}</tspan>')
    gattrs = f' id="{escape(el["id"], quote=True)}"'
    rot = float(el.get("rotation", 0) or 0)
    if rot:
        cx, cy = r._pivot(el, layout.box_px)
        gattrs += f' transform="rotate({_num(rot)} {_num(cx)} {_num(cy)})"'
    if filt:
        gattrs += f' filter="url(#{filt})"'
    if float(st.get("opacity", 1)) < 1:
        gattrs += f' opacity="{_num(float(st["opacity"]))}"'
    doc.body.append(f'<g{gattrs}><text {" ".join(attrs)} xml:space="preserve">{"".join(spans)}</text></g>')
    if family not in ("Inter",):
        doc.notes.add("text stays editable; install the same fonts (see list_fonts / the fonts folder) on the "
                      "machine that opens the SVG, or it falls back to another typeface")
    return True


def _image(doc: _Doc, img, x: float, y: float, name: str, blend: str = "normal") -> None:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    style = f' style="mix-blend-mode:{blend}"' if blend not in ("normal", "erase") else ""
    doc.body.append(f'<image id="{escape(name, quote=True)}" x="{_num(x / RASTER_SCALE)}" y="{_num(y / RASTER_SCALE)}" '
                    f'width="{_num(img.width / RASTER_SCALE)}" height="{_num(img.height / RASTER_SCALE)}"{style} '
                    f'href="data:image/png;base64,{data}"/>')
    doc.raster.append(name)


def canvas_svg(r: Renderer, include_background: bool = True) -> tuple[str, dict]:
    """The whole canvas as an SVG document plus a report of what stayed vector and what became pixels."""
    doc = _Doc()
    c = r.canvas
    big = r.derive(r.k * RASTER_SCALE)
    if include_background and not _is_none(c.get("background")):
        bg = c["background"]
        if isinstance(bg, str) and not bg.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            hexc, a = _rgba(bg, r.colors)
            doc.body.append(f'<rect id="background" width="{_num(c["width"])}" height="{_num(c["height"])}" '
                            f'fill="{hexc}"' + (f' fill-opacity="{_num(a)}"' if a < 1 else "") + "/>")
        else:
            _image(doc, big.render(include_background=True, only=set()), 0, 0, "background")
    vector: list[str] = []
    units: list[tuple[dict | None, list[dict], list[dict]]] = []      # (layer, elements, clipped layers' elements)
    for layer, elements in r.stack():
        props = r.layer_props(layer) if layer else None
        if props and not props["visible"]:
            continue
        if props and props["clip"] and units:
            units[-1][2].extend(elements)
            continue
        units.append((layer, list(elements), []))
    for layer, elements, clipped in units:
        props = r.layer_props(layer) if layer else None
        plain = props is None or (not props["style"] and props["blend"] == "normal")
        if not plain or clipped:
            ids = {e["id"] for e in elements + clipped}
            img = big.render(include_background=False, only=ids)
            bbox = img.getchannel("A").getbbox()
            if bbox:
                _image(doc, img.crop(bbox), bbox[0], bbox[1], f"layer-{props['name'] if props else 'base'}")
            doc.notes.add("styled, blended or clipped layers are embedded as pictures (one per layer)")
            continue
        start = len(doc.body)
        for el in elements:
            st = resolve_style(el.get("style"), r.theme)
            done = False
            if el["type"] in VECTOR_TYPES:
                done = _vector_element(doc, r, el, st)
            elif el["type"] == "text":
                done = _text_element(doc, r, el, st)
            if done:
                vector.append(el["id"])
                continue
            result = big.render_element(el)
            if result is not None:
                img, px0, py0, blend = result
                if blend == "erase":
                    doc.notes.add(f"{el['id']}: 'erase' has no SVG equivalent and was left out; use boolean_elements "
                                  "(subtract) for holes that stay vector")
                    continue
                _image(doc, img, px0, py0, el["id"], blend)
        if props and props["opacity"] < 1:
            inner = doc.body[start:]
            del doc.body[start:]
            doc.body.append(f'<g id="{escape(props["name"], quote=True)}" opacity="{_num(props["opacity"])}">'
                            + "".join(inner) + "</g>")
        elif props:
            inner = doc.body[start:]
            del doc.body[start:]
            doc.body.append(f'<g id="{escape(props["name"], quote=True)}">' + "".join(inner) + "</g>")
    r.warnings += big.warnings
    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(c["width"])}" height="{_num(c["height"])}" '
           f'viewBox="0 0 {_num(c["width"])} {_num(c["height"])}">\n'
           + (f"<defs>{''.join(doc.defs)}</defs>\n" if doc.defs else "")
           + "\n".join(doc.body) + "\n</svg>\n")
    return svg, {"vector": vector, "raster": doc.raster, "notes": sorted(doc.notes)}
