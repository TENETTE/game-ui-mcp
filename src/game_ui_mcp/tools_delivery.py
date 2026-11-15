"""Hand-off tools: 9-slice stretch test, developer spec, design tokens, clickable prototype and
engine scene export (Godot .tscn, Roblox Luau, Unity UI Toolkit UXML/USS)."""

from __future__ import annotations

import base64
import copy
import html
import io
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL import ImageDraw

from . import export as ex
from . import shaping
from .colors import parse_color, to_hex
from .render import TextLayout, _stroke_specs, parse_anchor, resolve_style
from .server import (Image, _gradient_blocks, _label_font, _maybe_json, _out_dir, _out_path, _png_bytes,
                     backdrop, store, tool)
from .store import slugify

__all__ = ["preview_nine_slice", "export_spec", "export_theme", "set_link", "export_prototype", "export_screen"]


# --------------------------------------------------------------------------- 9-slice check

def _borders_for(c: dict, img: PILImage.Image, nine_slice: Any) -> dict:
    nine = _maybe_json(nine_slice)
    if isinstance(nine, dict):
        return {k: int(round(float(nine[k]))) for k in ("left", "top", "right", "bottom")}
    bx, by = _gradient_blocks(c, (c["width"] / 2, c["height"] / 2))
    return ex.detect_nine_slice(img, block_x=bx, block_y=by)


@tool
def preview_nine_slice(canvas: str, sizes: list[list[int]] | None = None, nine_slice: str | dict = "auto",
                       background: str = "checker", without_text: bool = False):
    """Show how a 9-sliced asset looks when the engine stretches it to other sizes - before exporting.
    Corners must stay crisp, edges must not smear, gradients must not band or skew. Red lines mark the
    slice borders on the original. sizes default: wider, taller, much bigger and smaller versions.
    nine_slice: "auto" (same detection as export_asset) or {"left", "top", "right", "bottom"}.
    without_text: test the frame alone, the way it is used when the label is engine text (recommended
    for buttons and panels: text baked into a 9-slice smears when stretched)."""
    st = store()
    c = st.get(canvas)
    if without_text:
        c = dict(c, elements=[e for e in c["elements"] if e["type"] != "text"])
    img = st.renderer(c).render(include_background=False)
    b = _borders_for(c, img, nine_slice)
    w, h = img.size
    wanted = _maybe_json(sizes) or [[w * 2, h], [w, int(h * 1.8)], [int(w * 2.5), int(h * 1.6)],
                                    [max(b["left"] + b["right"] + 4, int(w * 0.6)), h]]
    tiles = [("original", img)]
    for size in wanted:
        tw, th = int(size[0]), int(size[1])
        if tw < 1 or th < 1 or tw > 4096 or th > 4096:
            raise ValueError("sizes must be between 1 and 4096")
        tiles.append((f"{tw}x{th}", ex.nine_slice_stretch(img, b, tw, th)))
    pad, label_h = 12, 18
    scale = min(1.0, 900 / max(t[1].width for t in tiles))
    shown = [(n, t.resize((max(1, int(t.width * scale)), max(1, int(t.height * scale))), PILImage.LANCZOS)) for n, t in tiles]
    W = sum(t.width for _, t in shown) + pad * (len(shown) + 1)
    H = max(t.height for _, t in shown) + label_h + 2 * pad
    sheet = PILImage.new("RGB", (W, H), (18, 18, 22))
    d = ImageDraw.Draw(sheet)
    font = _label_font(12)
    x = pad
    for i, (name, t) in enumerate(shown):
        tile = PILImage.alpha_composite(backdrop(t.size, background, st.theme.get("colors", {})), t)
        sheet.paste(tile.convert("RGB"), (x, pad))
        if i == 0:
            dd = ImageDraw.Draw(sheet)
            for vx in (b["left"], w - b["right"]):
                dd.line([(x + vx * scale, pad), (x + vx * scale, pad + t.height)], fill=(255, 60, 60), width=1)
            for vy in (b["top"], h - b["bottom"]):
                dd.line([(x, pad + vy * scale), (x + t.width, pad + vy * scale)], fill=(255, 60, 60), width=1)
        d.text((x, pad + t.height + 3), name, fill="#ffffff", font=font)
        x += t.width + pad
    info = {"canvas": c["id"], "borders": {k: b[k] for k in ("left", "top", "right", "bottom")},
            "stretch_x": b.get("stretch_x", True), "stretch_y": b.get("stretch_y", True)}
    if b.get("stretch_x") is False or b.get("stretch_y") is False:
        info["note"] = ("auto detection found an axis that cannot stretch cleanly (gradient or content across the "
                        "centre); the stretched tiles show what happens anyway")
    return [Image(data=_png_bytes(sheet), format="png"), json.dumps(info)]


# --------------------------------------------------------------------------- spec / tokens

def _paint_hex(spec: Any, colors: dict) -> Any:
    if spec is None or spec == "none":
        return None
    if isinstance(spec, dict):
        stops = spec.get("stops") or []
        out = []
        for stop in stops:
            col = stop[1] if isinstance(stop, (list, tuple)) and len(stop) == 2 else stop
            try:
                out.append(to_hex(parse_color(col, colors)))
            except ValueError:
                pass
        return {"type": spec.get("type", "linear"), "stops": out} if out else {"type": spec.get("type")}
    try:
        return to_hex(parse_color(spec, colors))
    except ValueError:
        return str(spec)


def _first_color(spec: Any, colors: dict) -> tuple[int, int, int, int]:
    if isinstance(spec, dict):
        stops = spec.get("stops") or []
        if stops:
            first = stops[0]
            col = first[1] if isinstance(first, (list, tuple)) and len(first) == 2 else first
            return parse_color(col, colors)
        return parse_color(spec.get("color", "#ffffff"), colors)
    if spec is None:
        return (255, 255, 255, 255)
    return parse_color(spec, colors)


def _text_info(r, el: dict) -> dict:
    st = store()
    style = resolve_style(el.get("style"), st.theme)
    layout = TextLayout(el, style, st.fonts, 1.0, r.text_of(el))
    fill = style.get("fill", el.get("fill", "#ffffff"))
    info: dict = {"text": layout.text, "font": layout.family or "Inter", "weight": layout.weight,
                  "size": round(layout.size, 2), "letter_spacing": layout.spacing,
                  "line_height": layout.line_height, "align": layout.align,
                  "color": _paint_hex(fill, r.colors), "font_file": Path(layout.path).name if layout.path else None,
                  "_font_path": layout.path}
    strokes = _stroke_specs(style, r.colors)
    if strokes:
        info["outline"] = {"width": strokes[0]["width"], "color": _paint_hex(strokes[0]["paint"], r.colors)}
    sh = style.get("shadow")
    if isinstance(sh, list):
        sh = sh[0] if sh else None
    if isinstance(sh, dict):
        info["shadow"] = {"color": _paint_hex(sh.get("color", "#000000@0.5"), r.colors),
                          "dx": float(sh.get("dx", 0)), "dy": float(sh.get("dy", 4)), "blur": float(sh.get("blur", 8))}
    return info


@tool
def export_spec(canvas: str, out_dir: str | None = None, name: str | None = None, language: str | None = None):
    """Developer hand-off: a redline image (every element boxed with id, size and position; text with
    font / size / colour) plus <name>.spec.json with exact numbers - bounds, pins, colours (hex), text
    styles, states, animations, theme tokens - so whoever builds the screen in the engine does not guess."""
    st = store()
    c = st.get(canvas)
    st.require_glyphs(c, language)
    r = st.renderer(c, language=language)
    folder = _out_dir(out_dir, "spec")
    base = slugify(name or c["id"], "spec")
    colors = r.colors
    items = []
    for el in c["elements"]:
        if not el.get("visible", True):
            continue
        x0, y0, x1, y1 = r.element_box(el)
        style = resolve_style(el.get("style"), st.theme)
        item: dict = {"id": el["id"], "type": el["type"], "x": round(x0, 2), "y": round(y0, 2),
                      "width": round(x1 - x0, 2), "height": round(y1 - y0, 2)}
        for key in ("group", "layer", "pin", "radius", "rotation", "note"):
            if el.get(key):
                item[key] = el[key]
        if el["type"] == "text":
            info = _text_info(r, el)
            info.pop("_font_path", None)
            item["text"] = info
        else:
            if style.get("fill") is not None:
                item["fill"] = _paint_hex(style.get("fill"), colors)
            strokes = _stroke_specs(style, colors)
            if strokes:
                item["stroke"] = [{"width": s_["width"], "align": s_["align"], "color": _paint_hex(s_["paint"], colors)}
                                  for s_ in strokes]
            for fx in ("shadow", "glow", "inner_shadow", "inner_glow"):
                if style.get(fx):
                    item[fx] = style[fx]
            if style.get("opacity") is not None:
                item["opacity"] = style["opacity"]
        if el["type"] == "instance":
            item["source"] = el.get("source")
        items.append(item)
    spec = {"canvas": {"id": c["id"], "width": c["width"], "height": c["height"],
                       "safe_area_ltrb": c.get("safe_area"), "device": c.get("device")},
            "colors": {k: to_hex(parse_color(v, colors)) for k, v in colors.items()},
            "elements": items,
            "states": c.get("states") or {},
            "animations": {k: {"duration": a["duration"], "loop": a.get("loop", False),
                               "tracks": len(a.get("tracks", []))} for k, a in (c.get("animations") or {}).items()},
            "links": c.get("links") or {}}
    engine_text = shaping.engine_notes("all", [i["text"]["text"] for i in items if i["type"] == "text"])
    if engine_text:
        spec["engine_text_notes"] = engine_text
    (folder / f"{base}.spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")

    scale = min(2.0, 1600 / max(c["width"], c["height"]))
    img = st.renderer(c, scale, language=language).render()
    view = PILImage.alpha_composite(backdrop(img.size, "dark", {}), img)
    margin = 0
    d = ImageDraw.Draw(view, "RGBA")
    font = _label_font(11)
    palette = [(255, 77, 109), (77, 210, 255), (255, 210, 77), (125, 255, 122), (213, 155, 255)]
    for i, item in enumerate(items):
        col = palette[i % len(palette)]
        bx = [item["x"] * scale, item["y"] * scale, (item["x"] + item["width"]) * scale - 1,
              (item["y"] + item["height"]) * scale - 1]
        d.rectangle(bx, outline=col + (220,), width=1)
        label = f"{item['id']}  {item['width']:g}x{item['height']:g} @ {item['x']:g},{item['y']:g}"
        if item["type"] == "text":
            t = item["text"]
            label += f"  {t['font']} {t['weight']} {t['size']:g}px {t['color'] if isinstance(t['color'], str) else ''}"
        tw = d.textlength(label, font=font)
        lx, ly = max(0, bx[0]), max(0, bx[1] - 13)
        d.rectangle([lx, ly, lx + tw + 4, ly + 13], fill=(0, 0, 0, 170))
        d.text((lx + 2, ly), label, fill=col + (255,), font=font)
    out_png = folder / f"{base}.spec.png"
    view.convert("RGB").save(out_png)
    info = {"spec_json": str(folder / f"{base}.spec.json"), "spec_png": str(out_png), "elements": len(items)}
    if engine_text:
        info["engine_text_notes"] = engine_text
    shown = view.copy()
    shown.thumbnail((1200, 1200))
    return [Image(data=_png_bytes(shown.convert("RGB")), format="png"), json.dumps(info)]


def _ident(name: str, case: str) -> str:
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", name) if p]
    if not parts:
        parts = ["token"]
    if case == "pascal":
        out = "".join(p[:1].upper() + p[1:] for p in parts)
    elif case == "upper":
        out = "_".join(p.upper() for p in parts)
    elif case == "kebab":
        out = "-".join(p.lower() for p in parts)
    else:
        out = "_".join(p.lower() for p in parts)
    return ("_" + out) if out[0].isdigit() else out


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _rgba_hex(v) -> str:
    return "#" + "".join(f"{c:02x}" for c in v[:4])


@tool
def export_theme(format: str = "json", path: str | None = None):
    """Export the theme as design tokens for the engine or a web / app codebase, so code uses exactly
    the colours and text styles the art was drawn with.

    format: "json", "css" (custom properties + text classes), "uss" (Unity UI Toolkit variables +
    classes), "csharp" (static Color32 class), "gdscript" (Godot constants), "luau" (Roblox module with
    Color3 + transparency), "dtcg" (W3C design tokens JSON for Tokens Studio, Penpot, Style Dictionary;
    modes go into $extensions). Text styles (theme styles with font / size / weight) become typography tokens.
    """
    st = store()
    fmt = format.lower()
    ext = {"json": ".json", "css": ".css", "uss": ".uss", "csharp": ".cs", "gdscript": ".gd", "luau": ".luau",
           "dtcg": ".tokens.json"}
    if fmt not in ext:
        raise ValueError(f"format must be one of {', '.join(ext)}")
    colors = st.theme.get("colors", {})
    rgba = {k: parse_color(v, colors) for k, v in colors.items()}
    numbers = st.theme.get("numbers") or {}
    modes = {name: {k: parse_color(v, {**colors, **vals}) for k, v in vals.items() if not _num(v)}
             for name, vals in (st.theme.get("modes") or {}).items()}
    mode_numbers = {name: {k: v for k, v in vals.items() if _num(v)}
                    for name, vals in (st.theme.get("modes") or {}).items()}
    text_styles = {}
    for name in st.theme.get("styles", {}):
        sty = resolve_style(name, st.theme)
        if any(k in sty for k in ("font", "size", "weight")):
            text_styles[name] = {k: sty[k] for k in ("font", "size", "weight", "letter_spacing", "uppercase", "italic")
                                 if k in sty}
            if "fill" in sty and not isinstance(sty["fill"], dict):
                try:
                    text_styles[name]["color"] = to_hex(parse_color(sty["fill"], colors))
                except ValueError:
                    pass
    lines: list[str] = []
    if fmt == "dtcg":
        from .tokens import write_dtcg
        text = json.dumps(write_dtcg({k: to_hex(v) if v[3] == 255 else _rgba_hex(v) for k, v in rgba.items()}, numbers,
                                     text_styles, {m: {k: to_hex(v) for k, v in vals.items()} for m, vals in modes.items()},
                                     mode_numbers), ensure_ascii=False, indent=1)
    elif fmt == "json":
        text = json.dumps({"colors": {k: to_hex(v) for k, v in rgba.items()}, "numbers": numbers, "text_styles": text_styles,
                           "modes": {m: {k: to_hex(v) for k, v in vals.items()} for m, vals in modes.items()}},
                          ensure_ascii=False, indent=1)
    elif fmt in ("css", "uss"):
        lines.append(":root {")
        for k, v in rgba.items():
            lines.append(f"    --color-{_ident(k, 'kebab')}: {to_hex(v) if fmt == 'css' or v[3] == 255 else f'rgba({v[0]}, {v[1]}, {v[2]}, {round(v[3] / 255, 3)})'};")
        for k, v in numbers.items():
            lines.append(f"    --{_ident(k, 'kebab')}: {v:g}px;")
        lines.append("}")
        for mname, vals in modes.items():               # css: <html data-theme="light">, uss: add class theme-light
            sel = f'[data-theme="{_ident(mname, "kebab")}"]' if fmt == "css" else f".theme-{_ident(mname, 'kebab')}"
            lines.append(sel + " {")
            for k, v in vals.items():
                lines.append(f"    --color-{_ident(k, 'kebab')}: {to_hex(v) if fmt == 'css' or v[3] == 255 else f'rgba({v[0]}, {v[1]}, {v[2]}, {round(v[3] / 255, 3)})'};")
            lines.append("}")
        for name, ts in text_styles.items():
            lines.append(f".text-{_ident(name, 'kebab')} {{")
            if fmt == "css" and ts.get("font"):
                lines.append(f"    font-family: '{ts['font']}';")
            if ts.get("size"):
                lines.append(f"    font-size: {ts['size']}px;")
            weight = str(ts.get("weight", ""))
            if fmt == "css" and weight:
                lines.append(f"    font-weight: {weight};")
            elif fmt == "uss" and (weight in ("bold", "black", "semibold", "extrabold") or (weight.isdigit() and int(weight) >= 600)):
                lines.append("    -unity-font-style: bold;")
            if ts.get("letter_spacing"):
                lines.append(f"    letter-spacing: {ts['letter_spacing']}px;")
            if ts.get("color"):
                lines.append(f"    color: {ts['color']};")
            if fmt == "css" and ts.get("uppercase"):
                lines.append("    text-transform: uppercase;")
            lines.append("}")
        text = "\n".join(lines) + "\n"
    elif fmt == "csharp":
        lines += ["// Generated by game-ui-mcp - design tokens", "using UnityEngine;", "",
                  "public static class UiTheme", "{"]
        for k, v in rgba.items():
            lines.append(f"    public static readonly Color32 {_ident(k, 'pascal')} = new Color32({v[0]}, {v[1]}, {v[2]}, {v[3]});")
        for name, ts in text_styles.items():
            if ts.get("size"):
                lines.append(f"    public const float {_ident(name, 'pascal')}FontSize = {float(ts['size'])}f;")
        lines.append("}")
        text = "\n".join(lines) + "\n"
    elif fmt == "gdscript":
        lines += ["# Generated by game-ui-mcp - design tokens", "class_name UiTheme", ""]
        for k, v in rgba.items():
            lines.append(f"const {_ident(k, 'upper')} := Color({v[0] / 255:.4f}, {v[1] / 255:.4f}, {v[2] / 255:.4f}, {v[3] / 255:.4f})")
        for name, ts in text_styles.items():
            lines.append(f"const TEXT_{_ident(name, 'upper')} := {json.dumps(ts, ensure_ascii=False)}")
        text = "\n".join(lines) + "\n"
    else:
        lines += ["-- Generated by game-ui-mcp - design tokens", "local Theme = {}", "", "Theme.colors = {"]
        for k, v in rgba.items():
            lines.append(f"\t{_ident(k, 'snake')} = Color3.fromRGB({v[0]}, {v[1]}, {v[2]}),")
        lines += ["}", "", "-- 0 = opaque, 1 = invisible (Roblox convention)", "Theme.transparency = {"]
        for k, v in rgba.items():
            lines.append(f"\t{_ident(k, 'snake')} = {round(1 - v[3] / 255, 3)},")
        lines += ["}", "", "Theme.text = {"]
        for name, ts in text_styles.items():
            lines.append(f"\t{_ident(name, 'snake')} = {{ size = {ts.get('size', 16)}, font = \"{ts.get('font', 'Inter')}\" }},")
        lines += ["}", "", "return Theme"]
        text = "\n".join(lines) + "\n"
    out = _out_path(path, "ui_theme" + ext[fmt], suffixes=tuple(ext.values()))
    out.write_text(text, encoding="utf-8")
    result = {"file": str(out), "colors": len(rgba), "text_styles": len(text_styles), "modes": sorted(modes)}
    if modes and fmt in ("csharp", "gdscript", "luau"):
        result["note"] = "this format holds the base colours only; use json, css or uss for every mode"
    return result


# --------------------------------------------------------------------------- prototype

@tool
def set_link(canvas: str, id: str, target: str | None, mode: str = "navigate"):
    """Make an element clickable in export_prototype and export_web: target is another canvas id (a screen),
    "back", a URL (mode "url"), or null to remove the link. mode: "navigate" (go to the screen), "overlay"
    (open the screen on top - menus, dialogs, tooltips; its "back" link closes it) or "url". Hover / pressed looks come from the element's states automatically
    (an instance whose source has "hover" / "pressed" states, or this canvas's "hover" / "pressed"
    states that override this element)."""
    st = store()
    c = st.get(canvas)
    el = st.find(c, id)
    links = c.setdefault("links", {})
    mode = str(mode).lower()
    if mode not in ("navigate", "overlay", "url"):
        raise ValueError("mode must be 'navigate', 'overlay' or 'url'")
    if target is None:
        links.pop(el["id"], None)
    elif mode == "url":
        if not re.match(r"^(https?://|mailto:|#)", str(target)):
            raise ValueError("a url link must start with https://, http://, mailto: or #")
        links[el["id"]] = {"target": str(target), "mode": "url"}
    else:
        if target != "back":
            st.get(target)
            target = slugify(target)
        links[el["id"]] = target if mode == "navigate" else {"target": target, "mode": mode}
    if not links:
        c.pop("links", None)
    st.save_canvas(c)
    return {"canvas": c["id"], "links": c.get("links", {})}


def _data_uri(img: PILImage.Image) -> str:
    return "data:image/png;base64," + base64.b64encode(_png_bytes(img)).decode("ascii")


def _state_render(st, c: dict, el: dict, state: str, scale: float, language: str | None):
    """The page rendered with one element in an interaction state, or None if it has none."""
    if el["type"] == "instance":
        try:
            src = st.get(str(el.get("source")))
        except ValueError:
            return None
        if state in (src.get("states") or {}):
            page = copy.deepcopy(c)
            for e in page["elements"]:
                if e["id"] == el["id"]:
                    e["state"] = state
            return st.renderer(page, scale, language=language).render()
    states = c.get("states") or {}
    for name in (f"{state}_{el['id']}", f"{el['id']}_{state}"):
        if name in states:
            return st.renderer(c, scale, state=name, language=language).render()
    if state in states and el["id"] in states[state]:
        return st.renderer(c, scale, state=state, language=language).render()
    return None


@tool
def export_prototype(start: str, path: str | None = None, scale: float = 1, language: str | None = None,
                     pages: list[str] | None = None):
    """Export a clickable prototype: one self-contained HTML file (open it in any browser, share it)
    with every screen reachable from `start` through set_link links, plus `pages`. Linked elements react
    to hover and click with their own hover / pressed states. Esc or "back" links go back.
    Use it to test a menu flow (title -> play -> settings -> back) before building it in the engine."""
    st = store()
    first = st.get(start)
    queue = [first["id"]] + [slugify(p) for p in (_maybe_json(pages) or [])]
    seen: list[str] = []
    while queue:
        cid = queue.pop(0)
        if cid in seen:
            continue
        c = st.get(cid)
        seen.append(cid)
        for target in (c.get("links") or {}).values():
            if target != "back" and target not in seen:
                queue.append(target)
    scale = max(0.25, min(3.0, float(scale)))
    page_html = []
    for cid in seen:
        c = st.get(cid)
        r = st.renderer(c, scale, language=language)
        img = r.render()
        spots = []
        for eid, target in (c.get("links") or {}).items():
            el = next((e for e in c["elements"] if e["id"] == eid), None)
            if el is None:
                continue
            x0, y0, x1, y1 = r.element_box(el)
            pad = 16
            box = [max(0, x0 - pad), max(0, y0 - pad), min(c["width"], x1 + pad), min(c["height"], y1 + pad)]
            crop = tuple(int(round(v * scale)) for v in box)
            layers = ""
            for state in ("hover", "pressed"):
                full = _state_render(st, c, el, state, scale, language)
                if full is not None:
                    layers += (f'<img class="{state}" src="{_data_uri(full.crop(crop))}" '
                               f'style="left:{box[0] - x0}px;top:{box[1] - y0}px;width:{box[2] - box[0]}px;'
                               f'height:{box[3] - box[1]}px">')
            cls = "spot" + ("" if layers else " plain")
            spots.append(f'<a class="{cls}" data-go="{html.escape(target)}" style="left:{x0}px;top:{y0}px;'
                         f'width:{x1 - x0}px;height:{y1 - y0}px">{layers}</a>')
        page_html.append(f'<section class="page" id="p-{html.escape(cid)}" data-w="{c["width"]}" data-h="{c["height"]}" '
                         f'style="width:{c["width"]}px;height:{c["height"]}px">'
                         f'<img class="base" src="{_data_uri(img)}" style="width:{c["width"]}px;height:{c["height"]}px">'
                         + "".join(spots) + "</section>")
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(first.get('name') or first['id'])} - prototype</title>
<style>
html,body{{margin:0;height:100%;background:#0d0d10;overflow:hidden;font:12px system-ui,sans-serif;color:#9aa0aa}}
#stage{{position:absolute;left:50%;top:50%;transform-origin:0 0}}
.page{{position:absolute;left:0;top:0;display:none;background:repeating-conic-gradient(#26262c 0 25%,#1d1d22 0 50%) 0 0/24px 24px}}
.page.on{{display:block}}
.base{{position:absolute;left:0;top:0;image-rendering:auto}}
.spot{{position:absolute;cursor:pointer;display:block}}
.spot img{{position:absolute;display:none;pointer-events:none}}
.spot:hover img.hover{{display:block}}
.spot:active img.hover{{display:none}}
.spot:active img.pressed{{display:block}}
.spot.plain:hover{{outline:2px solid rgba(255,255,255,.55);outline-offset:3px;border-radius:6px}}
.spot.plain:active{{outline-color:#fff;background:rgba(255,255,255,.08)}}
#bar{{position:fixed;left:8px;bottom:6px}}
</style></head><body><div id="stage">{''.join(page_html)}</div>
<div id="bar"></div>
<script>
const stage=document.getElementById('stage'),bar=document.getElementById('bar');let hist=[],cur=null;
function fit(){{const p=document.getElementById('p-'+cur);if(!p)return;const w=+p.dataset.w,h=+p.dataset.h;
const s=Math.min(innerWidth/w,(innerHeight-24)/h);stage.style.width=w+'px';stage.style.height=h+'px';
stage.style.transform='scale('+s+') translate(-50%,-50%)';}}
function show(id,push){{if(push&&cur)hist.push(cur);document.querySelectorAll('.page').forEach(p=>p.classList.toggle('on',p.id==='p-'+id));
cur=id;bar.textContent=id+(hist.length?'  ·  Esc = back':'');fit();}}
document.querySelectorAll('.spot').forEach(a=>a.addEventListener('click',()=>{{const t=a.dataset.go;
if(t==='back'){{if(hist.length)show(hist.pop(),false);}}else show(t,true);}}));
addEventListener('keydown',e=>{{if(e.key==='Escape'&&hist.length)show(hist.pop(),false);}});
addEventListener('resize',fit);show({json.dumps(first['id'])},false);
</script></body></html>
"""
    out = Path(path).expanduser() if path else st.exports_dir / f"{first['id']}_prototype.html"
    if not out.is_absolute():
        out = st.exports_dir / out
    if out.suffix.lower() != ".html":
        raise ValueError("path must end with .html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    unlinked = [cid for cid in seen if not st.get(cid).get("links")]
    result: dict = {"file": str(out), "screens": seen, "size_kb": round(out.stat().st_size / 1024)}
    if unlinked:
        result["dead_ends"] = unlinked
    return result


# --------------------------------------------------------------------------- engine scene export

ROBLOX_FONTS = {"inter": "BuilderSans", "rajdhani": "TitilliumWeb", "orbitron": "Michroma", "michroma": "Michroma",
                "bangers": "Bangers", "fredoka": "FredokaOne", "bebas neue": "Oswald", "barlow condensed": "Oswald",
                "jetbrains mono": "RobotoMono", "cinzel": "Merriweather"}
ROBLOX_WEIGHT = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold",
                 700: "Bold", 800: "ExtraBold", 900: "Heavy"}


def _pieces(c: dict, scale: float, bake_text: bool, language: str | None):
    """Split a screen into image pieces (one per group, or per ungrouped element) and text nodes,
    in draw order."""
    st = store()
    r = st.renderer(c, scale, language=language)
    r1 = st.renderer(c, 1.0, language=language)
    nodes = []
    groups_done: set[str] = set()
    for el in c["elements"]:
        if not el.get("visible", True):
            continue
        if el["type"] == "text" and not bake_text and not el.get("arc"):
            x0, y0, x1, y1 = r1.element_box(el)
            info = _text_info(r1, el)
            # engine labels need room: fonts render a little differently there, and translations run longer
            extra = max(8.0, (x1 - x0) * 0.4)
            if info["align"] == "center":
                x0, x1 = x0 - extra / 2, x1 + extra / 2
            elif info["align"] == "right":
                x0 -= extra
            else:
                x1 += extra
            cy, line = (y0 + y1) / 2, float(info["size"]) * 1.4
            y0, y1 = cy - max(line, y1 - y0) / 2, cy + max(line, y1 - y0) / 2
            nodes.append({"kind": "text", "id": el["id"], "box": (x0, y0, x1, y1), "pin": el.get("pin") or {},
                          "el": el, "info": info})
            continue
        if el.get("group"):
            if el["group"] in groups_done:
                continue
            groups_done.add(el["group"])
            members = [e for e in c["elements"] if e.get("group") == el["group"] and e.get("visible", True)
                       and (bake_text or e["type"] != "text" or e.get("arc"))]
            name = el["group"]
        else:
            members = [el]
            name = el["id"]
        img = r.render(include_background=False, only={e["id"] for e in members})
        bbox = img.getchannel("A").getbbox()
        if bbox is None:
            continue
        piece = img.crop(bbox)
        nodes.append({"kind": "image", "id": slugify(name, "piece"), "img": piece,
                      "box": (bbox[0] / scale, bbox[1] / scale, bbox[2] / scale, bbox[3] / scale),
                      "pin": members[0].get("pin") or {}, "members": [e["id"] for e in members]})
    return nodes, r.warnings


def _godot_color(rgba) -> str:
    return f"Color({rgba[0] / 255:.4f}, {rgba[1] / 255:.4f}, {rgba[2] / 255:.4f}, {rgba[3] / 255:.4f})"


def _anchor_axis(pin: str, a0: float, a1: float, size: float) -> tuple[float, float, float, float]:
    """(anchor_start, anchor_end, offset_start, offset_end) for one axis, Godot style."""
    if pin in ("right", "bottom"):
        return 1.0, 1.0, a0 - size, a1 - size
    if pin in ("center", "middle"):
        return 0.5, 0.5, a0 - size / 2, a1 - size / 2
    if pin == "stretch":
        return 0.0, 1.0, a0, a1 - size
    if pin == "scale":
        return a0 / size, a1 / size, 0.0, 0.0
    return 0.0, 0.0, a0, a1


def _write_godot(c: dict, nodes: list, folder: Path, res: str, name: str, colors: dict) -> Path:
    res = res.rstrip("/")
    ext_lines, body = [], []
    ids: dict[str, str] = {}
    n = 0

    def ext(kind: str, path: str) -> str:
        nonlocal n
        if path not in ids:
            n += 1
            ids[path] = f"{n}_{kind[:4].lower()}"
            ext_lines.append(f'[ext_resource type="{kind}" path="{res}/{path}" id="{ids[path]}"]')
        return ids[path]

    W, H = float(c["width"]), float(c["height"])
    body += [f'[node name="{_ident_node(name)}" type="Control"]', "layout_mode = 3", "anchors_preset = 15",
             "anchor_right = 1.0", "anchor_bottom = 1.0", "grow_horizontal = 2", "grow_vertical = 2",
             "mouse_filter = 2", ""]
    for node in nodes:
        x0, y0, x1, y1 = node["box"]
        pin = node["pin"]
        al, ar, ol, orr = _anchor_axis(pin.get("x", "left"), x0, x1, W)
        at, ab, ot, ob = _anchor_axis(pin.get("y", "top"), y0, y1, H)
        anchors = [f"anchor_left = {al:.4f}", f"anchor_top = {at:.4f}", f"anchor_right = {ar:.4f}",
                   f"anchor_bottom = {ab:.4f}", f"offset_left = {ol:.2f}", f"offset_top = {ot:.2f}",
                   f"offset_right = {orr:.2f}", f"offset_bottom = {ob:.2f}"]
        if node["kind"] == "image":
            tex = ext("Texture2D", node["file"])
            if node.get("nine"):
                b = node["nine"]
                body += [f'[node name="{_ident_node(node["id"])}" type="NinePatchRect" parent="."]', "layout_mode = 1",
                         *anchors, "mouse_filter = 2", f'texture = ExtResource("{tex}")',
                         f"patch_margin_left = {b['left']}", f"patch_margin_top = {b['top']}",
                         f"patch_margin_right = {b['right']}", f"patch_margin_bottom = {b['bottom']}", ""]
            else:
                body += [f'[node name="{_ident_node(node["id"])}" type="TextureRect" parent="."]', "layout_mode = 1",
                         *anchors, "mouse_filter = 2", f'texture = ExtResource("{tex}")', "expand_mode = 1", ""]
        else:
            t = node["info"]
            props = [f'[node name="{_ident_node(node["id"])}" type="Label" parent="."]', "layout_mode = 1", *anchors]
            col = _first_color_hexlike(t["color"], colors)
            props.append(f"theme_override_colors/font_color = {_godot_color(col)}")
            if t.get("outline"):
                props.append(f"theme_override_colors/font_outline_color = {_godot_color(_first_color_hexlike(t['outline']['color'], colors))}")
                props.append(f"theme_override_constants/outline_size = {int(round(t['outline']['width'] * 2))}")
            if t.get("shadow"):
                props.append(f"theme_override_colors/font_shadow_color = {_godot_color(_first_color_hexlike(t['shadow']['color'], colors))}")
                props.append(f"theme_override_constants/shadow_offset_x = {int(round(t['shadow']['dx']))}")
                props.append(f"theme_override_constants/shadow_offset_y = {int(round(t['shadow']['dy']))}")
            if node.get("font_file"):
                props.append(f'theme_override_fonts/font = ExtResource("{ext("FontFile", node["font_file"])}")')
            props.append(f"theme_override_font_sizes/font_size = {int(round(t['size']))}")
            props.append(f"text = {json.dumps(t['text'], ensure_ascii=False)}")
            props.append(f"horizontal_alignment = {({'left': 0, 'center': 1, 'right': 2}).get(t['align'], 0)}")
            props.append("vertical_alignment = 1")
            body += props + [""]
    head = f"[gd_scene load_steps={len(ext_lines) + 1} format=3]\n\n" + "\n".join(ext_lines) + "\n\n"
    out = folder / f"{name}.tscn"
    out.write_text(head + "\n".join(body), encoding="utf-8")
    return out


def _ident_node(name: str) -> str:
    return re.sub(r"[.:@/\"%]", "_", name) or "node"


def _first_color_hexlike(value: Any, colors: dict) -> tuple[int, int, int, int]:
    if isinstance(value, dict):
        stops = value.get("stops") or []
        return parse_color(stops[0], colors) if stops else (255, 255, 255, 255)
    return parse_color(value or "#ffffff", colors)


def _udim_axis(pin: str, a0: float, a1: float, size: float) -> tuple[str, str]:
    """(position UDim, size UDim) for one axis, Roblox style."""
    length = a1 - a0
    if pin in ("right", "bottom"):
        return f"1, {a0 - size:.1f}", f"0, {length:.1f}"
    if pin in ("center", "middle"):
        return f"0.5, {a0 - size / 2:.1f}", f"0, {length:.1f}"
    if pin == "stretch":
        return f"0, {a0:.1f}", f"1, {-(a0 + size - a1):.1f}"
    if pin == "scale":
        return f"{a0 / size:.4f}, 0", f"{length / size:.4f}, 0"
    return f"0, {a0:.1f}", f"0, {length:.1f}"


def _write_roblox(c: dict, nodes: list, folder: Path, name: str, colors: dict) -> Path:
    W, H = float(c["width"]), float(c["height"])
    lines = [f"-- {name}  -  generated by game-ui-mcp from canvas '{c['id']}' ({int(W)}x{int(H)})",
             "-- LocalScript: put it in StarterPlayerScripts (or StarterGui). Upload the PNGs in Studio and",
             "-- paste each asset id into IMAGES. Layout keeps the design's pins and scales with screen height.",
             "", "local Players = game:GetService(\"Players\")", "local player = Players.LocalPlayer",
             "", "local IMAGES = {"]
    for node in nodes:
        if node["kind"] == "image":
            lines.append(f'\t["{node["id"]}"] = "rbxassetid://0", -- {node["file"]}')
    lines += ["}", "", f"local DESIGN = Vector2.new({int(W)}, {int(H)})", "",
              "local gui = Instance.new(\"ScreenGui\")", f'gui.Name = "{name}"', "gui.IgnoreGuiInset = true",
              "gui.ResetOnSpawn = false", "gui.ZIndexBehavior = Enum.ZIndexBehavior.Sibling", "",
              "local root = Instance.new(\"Frame\")", 'root.Name = "Root"', "root.BackgroundTransparency = 1",
              "root.Parent = gui", "local uiScale = Instance.new(\"UIScale\")", "uiScale.Parent = root", "",
              "local function fit()", "\tlocal vp = workspace.CurrentCamera.ViewportSize",
              "\tlocal s = vp.Y / DESIGN.Y", "\tuiScale.Scale = s",
              "\troot.Size = UDim2.fromOffset(vp.X / s, vp.Y / s)", "end", "fit()",
              "workspace.CurrentCamera:GetPropertyChangedSignal(\"ViewportSize\"):Connect(fit)", ""]
    z = 1
    for node in nodes:
        x0, y0, x1, y1 = node["box"]
        pin = node["pin"]
        px, sx = _udim_axis(pin.get("x", "left"), x0, x1, W)
        py, sy = _udim_axis(pin.get("y", "top"), y0, y1, H)
        var = "n" + str(z)
        if node["kind"] == "image":
            lines += [f'local {var} = Instance.new("ImageLabel")', f'{var}.Name = "{node["id"]}"',
                      f'{var}.Image = IMAGES["{node["id"]}"]', f"{var}.BackgroundTransparency = 1"]
            if node.get("nine"):
                b = node["nine"]
                iw, ih = node["img"].size
                lines += [f"{var}.ScaleType = Enum.ScaleType.Slice",
                          f"{var}.SliceCenter = Rect.new({b['left']}, {b['top']}, {iw - b['right']}, {ih - b['bottom']})",
                          f"{var}.SliceScale = {round(1 / node['scale'], 4)}"]
        else:
            t = node["info"]
            col = _first_color_hexlike(t["color"], colors)
            family = ROBLOX_FONTS.get(str(t["font"]).lower(), "BuilderSans")
            weight = ROBLOX_WEIGHT.get(int(round(int(t["weight"] or 400) / 100.0) * 100), "Regular")
            lines += [f'local {var} = Instance.new("TextLabel")', f'{var}.Name = "{node["id"]}"',
                      f"{var}.BackgroundTransparency = 1", f"{var}.Text = {json.dumps(t['text'], ensure_ascii=False)}",
                      f"{var}.TextColor3 = Color3.fromRGB({col[0]}, {col[1]}, {col[2]})",
                      f"{var}.TextTransparency = {round(1 - col[3] / 255, 3)}",
                      f"{var}.TextSize = {int(round(t['size']))}",
                      f'{var}.FontFace = Font.fromName("{family}", Enum.FontWeight.{weight})  -- designed with {t["font"]}',
                      f"{var}.TextXAlignment = Enum.TextXAlignment.{({'left': 'Left', 'center': 'Center', 'right': 'Right'}).get(t['align'], 'Left')}",
                      f"{var}.TextYAlignment = Enum.TextYAlignment.Center"]
            if t.get("outline"):
                oc = _first_color_hexlike(t["outline"]["color"], colors)
                lines += [f'local {var}s = Instance.new("UIStroke")', f"{var}s.Thickness = {round(t['outline']['width'], 2)}",
                          f"{var}s.Color = Color3.fromRGB({oc[0]}, {oc[1]}, {oc[2]})",
                          f"{var}s.Transparency = {round(1 - oc[3] / 255, 3)}", f"{var}s.Parent = {var}"]
        lines += [f"{var}.Position = UDim2.new({px}, {py})", f"{var}.Size = UDim2.new({sx}, {sy})",
                  f"{var}.ZIndex = {z}", f"{var}.Parent = root", ""]
        z += 1
    lines += ['gui.Parent = player:WaitForChild("PlayerGui")', ""]
    out = folder / f"{name}.client.luau"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _css_axis(pin: str, a0: float, a1: float, size: float, start: str, end: str, length: str) -> list[str]:
    span = a1 - a0
    if pin in ("right", "bottom"):
        return [f"{end}: {size - a1:.1f}px", f"{length}: {span:.1f}px"]
    if pin in ("center", "middle"):
        return [f"{start}: 50%", f"margin-{start}: {a0 - size / 2:.1f}px", f"{length}: {span:.1f}px"]
    if pin == "stretch":
        return [f"{start}: {a0:.1f}px", f"{end}: {size - a1:.1f}px"]
    if pin == "scale":
        return [f"{start}: {a0 / size * 100:.3f}%", f"{length}: {span / size * 100:.3f}%"]
    return [f"{start}: {a0:.1f}px", f"{length}: {span:.1f}px"]


def _write_unity(c: dict, nodes: list, folder: Path, name: str, colors: dict) -> list[Path]:
    W, H = float(c["width"]), float(c["height"])
    uss = [f"/* {name} - generated by game-ui-mcp. PanelSettings: Scale With Screen Size, reference "
           f"resolution {int(W)}x{int(H)}, match 1 (height). */",
           f".{name}-root {{ position: absolute; left: 0; top: 0; right: 0; bottom: 0; }}"]
    uxml = ['<ui:UXML xmlns:ui="UnityEngine.UIElements">', f'    <Style src="{name}.uss" />',
            f'    <ui:VisualElement name="{name}" class="{name}-root" picking-mode="Ignore">']
    for node in nodes:
        x0, y0, x1, y1 = node["box"]
        pin = node["pin"]
        cls = f"{name}-{node['id']}".replace("_", "-")
        rules = ["position: absolute"] + _css_axis(pin.get("x", "left"), x0, x1, W, "left", "right", "width") + \
            _css_axis(pin.get("y", "top"), y0, y1, H, "top", "bottom", "height")
        if node["kind"] == "image":
            rules.append(f'background-image: url("{node["file"]}")')
            if node.get("nine"):
                b = node["nine"]
                rules += [f"-unity-slice-left: {b['left']}", f"-unity-slice-top: {b['top']}",
                          f"-unity-slice-right: {b['right']}", f"-unity-slice-bottom: {b['bottom']}",
                          f"-unity-slice-scale: {round(1 / node['scale'], 4)}px"]
            uxml.append(f'        <ui:VisualElement name="{node["id"]}" class="{cls}" picking-mode="Ignore" />')
        else:
            t = node["info"]
            col = _first_color_hexlike(t["color"], colors)
            align_h = {"left": "left", "center": "center", "right": "right"}.get(t["align"], "left")
            rules += [f"font-size: {t['size']:.0f}px", f"color: rgba({col[0]}, {col[1]}, {col[2]}, {col[3] / 255:.3f})",
                      f"-unity-text-align: middle-{align_h}", "white-space: nowrap", "margin: 0", "padding: 0"]
            if node.get("font_file"):
                rules.append(f'-unity-font: url("{node["font_file"]}")')
            if int(t["weight"] or 400) >= 600:
                rules.append("-unity-font-style: bold")
            if t.get("letter_spacing"):
                rules.append(f"letter-spacing: {t['letter_spacing']}px")
            if t.get("outline"):
                oc = _first_color_hexlike(t["outline"]["color"], colors)
                rules += [f"-unity-text-outline-width: {t['outline']['width']}px",
                          f"-unity-text-outline-color: rgba({oc[0]}, {oc[1]}, {oc[2]}, {oc[3] / 255:.3f})"]
            if t.get("shadow"):
                sc = _first_color_hexlike(t["shadow"]["color"], colors)
                rules.append(f"text-shadow: {t['shadow']['dx']}px {t['shadow']['dy']}px {t['shadow']['blur']}px "
                             f"rgba({sc[0]}, {sc[1]}, {sc[2]}, {sc[3] / 255:.3f})")
            uxml.append(f'        <ui:Label name="{node["id"]}" class="{cls}" text="{html.escape(t["text"], quote=True)}" '
                        f'picking-mode="Ignore" />')
        uss.append(f".{cls} {{ " + "; ".join(rules) + "; }")
    uxml += ["    </ui:VisualElement>", "</ui:UXML>"]
    a = folder / f"{name}.uxml"
    b = folder / f"{name}.uss"
    a.write_text("\n".join(uxml) + "\n", encoding="utf-8")
    b.write_text("\n".join(uss) + "\n", encoding="utf-8")
    return [a, b]


@tool
def export_screen(canvas: str, engine: str, out_dir: str | None = None, name: str | None = None,
                  scale: float = 1, bake_text: bool = False, language: str | None = None,
                  godot_res_path: str | None = None):
    """Turn a screen mockup into a real engine UI: image pieces + a scene / script that rebuilds the
    layout with native text, keeping each element's pin (set_pin) so it adapts to other resolutions.

    engine: "godot" (.tscn with TextureRect / NinePatchRect / Label nodes and anchors), "roblox"
      (a LocalScript building a ScreenGui with ImageLabels, TextLabels + UIStroke, scaled to screen
      height), "unity" (UI Toolkit .uxml + .uss with absolute positions, 9-slice and text styles),
      or "json" (pieces + layout only).
    Pieces: every element group becomes one image (so group each widget), ungrouped elements one image
    each. Text stays live text (translatable, crisp) unless bake_text=true; arc text is always baked.
    Pieces pinned "stretch" get 9-slice margins when their centre allows it. Fonts used are copied next
    to the scene. scale: export pieces at 2x for high-DPI. godot_res_path: where the folder will live
    in the Godot project (default res://ui/<name>).
    """
    st = store()
    c = st.get(canvas)
    st.require_glyphs(c, language)
    engine = engine.lower()
    if engine not in ("godot", "roblox", "unity", "json"):
        raise ValueError("engine must be 'godot', 'roblox', 'unity' or 'json'")
    scale = max(0.5, min(4.0, float(scale)))
    base = slugify(name or c["id"], "screen")
    folder = _out_dir(out_dir, f"screens/{base}")
    nodes, warnings = _pieces(c, scale, bake_text, language)
    colors = st.renderer(c).colors
    fonts_copied = []
    for node in nodes:
        if node["kind"] == "image":
            node["file"] = f"{node['id']}.png"
            node["scale"] = scale
            node["img"].save(folder / node["file"])
            pin = node["pin"]
            if pin.get("x") == "stretch" or pin.get("y") == "stretch":
                b = ex.detect_nine_slice(node["img"])
                if b["stretch_x"] or b["stretch_y"]:
                    small, b = ex.minimize_nine_slice(node["img"], b)       # tiny texture, engine stretches it
                    node["img"] = small
                    small.save(folder / node["file"])
                    node["nine"] = {k: b[k] for k in ("left", "top", "right", "bottom")}
        else:
            src = node["info"].get("_font_path")
            if src and Path(src).is_file():
                dst = folder / Path(src).name
                if not dst.exists():
                    shutil.copyfile(src, dst)
                    fonts_copied.append(dst.name)
                node["font_file"] = dst.name
    layout = {"canvas": {"id": c["id"], "width": c["width"], "height": c["height"]}, "scale": scale,
              "nodes": [{k: v for k, v in {
                  "kind": n["kind"], "id": n["id"], "x": round(n["box"][0], 2), "y": round(n["box"][1], 2),
                  "width": round(n["box"][2] - n["box"][0], 2), "height": round(n["box"][3] - n["box"][1], 2),
                  "pin": n["pin"] or {"x": "left", "y": "top"}, "file": n.get("file"), "nine_slice": n.get("nine"),
                  "members": n.get("members"),
                  "text": {k: v for k, v in n["info"].items() if not k.startswith("_")} if n["kind"] == "text" else None,
              }.items() if v is not None} for n in nodes]}
    (folder / f"{base}.screen.json").write_text(json.dumps(layout, ensure_ascii=False, indent=1), encoding="utf-8")
    files = [f"{base}.screen.json"]
    nxt = "Use the pieces and layout JSON in your engine."
    if engine == "godot":
        out = _write_godot(c, nodes, folder, godot_res_path or f"res://ui/{base}", base, colors)
        files.append(out.name)
        nxt = (f"Copy the folder to {godot_res_path or f'res://ui/{base}'} in your Godot 4 project and instance "
               f"{out.name} under a CanvasLayer. Labels are real text; anchors follow the pins.")
    elif engine == "roblox":
        out = _write_roblox(c, nodes, folder, base, colors)
        files.append(out.name)
        nxt = ("Upload the PNGs in Studio, paste their ids into IMAGES at the top of the script, and put the "
               "LocalScript in StarterPlayerScripts. Fonts are mapped to the closest built-in Roblox family.")
    elif engine == "unity":
        outs = _write_unity(c, nodes, folder, base, colors)
        files += [o.name for o in outs]
        nxt = (f"Put the folder under Assets/, create a UIDocument with {base}.uxml and a PanelSettings set to "
               f"Scale With Screen Size, reference {c['width']}x{c['height']}, match = 1.")
    result: dict = {"folder": str(folder), "files": files,
                    "pieces": [n["id"] for n in nodes if n["kind"] == "image"],
                    "texts": [n["id"] for n in nodes if n["kind"] == "text"], "next": nxt}
    if fonts_copied:
        result["fonts_copied"] = fonts_copied
    notes = []
    for n in nodes:
        if n["kind"] == "text" and isinstance(n["info"]["color"], dict):
            notes.append(f"{n['id']}: gradient text is exported with its first colour; bake_text=true keeps the gradient")
    if any(n["kind"] == "text" and n["info"].get("weight") and str(n["info"].get("font", "")).lower() in ("inter",)
           for n in nodes):
        notes.append("Inter is a variable font: set the weight in the engine's font settings if it shows regular")
    notes = shaping.engine_notes(engine, [n["info"]["text"] for n in nodes if n["kind"] == "text"]) + notes
    if notes:
        result["notes"] = notes[:10]
    if warnings:
        result["warnings"] = sorted(set(warnings))[:10]
    return result
