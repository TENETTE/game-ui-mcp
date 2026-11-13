"""game-ui-mcp: a local MCP server that lets an AI draw game UI assets with drawing tools.

No AI image generation and no network access: every pixel comes from the shapes,
text, icons and effects the model asks for. Run with ``python -m game_ui_mcp``.
"""

from __future__ import annotations

import functools
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Image, MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image
    try:
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError:  # very old 1.x
        ToolError = RuntimeError

from . import export as ex
from . import engines
from .icons import icon_names
from .render import TextLayout, backdrop, resolve_style
from .store import Store, slugify

INSTRUCTIONS = """\
Draw game UI assets (panels, buttons, bars, icons, HUD pieces, full screen mockups) as layered vector
elements on canvases, look at them with `preview`, then export PNGs for Unity / Unreal / Roblox.
Everything is local and deterministic: no image generation model is involved.

Workflow: set_theme (colors + reusable styles) -> create_canvas -> add_* / batch -> preview ->
fix with update_element -> export_asset / export_png / export_atlas.

Coordinates are canvas units = pixels at 1x, origin top-left, y down. Elements draw in the order
they were added (later = on top); use reorder_element to change that.

Colors: "#rrggbb", "#rrggbbaa", "rgba(r,g,b,a)", names, or "$token" from the theme. Append "@0.5"
to any color for alpha, e.g. "#ffffff@0.2", "$primary@0.6".

Fill: a color, or a gradient/pattern object:
  {"type":"linear","angle":90,"stops":[[0,"#3b82f6"],[1,"#1d4ed8"]]}   (angle 0 = left->right, 90 = top->bottom)
  {"type":"radial","cx":0.5,"cy":0.5,"r":0.5,"stops":[...]}            (cx/cy/r relative to the element box)
  {"type":"conic","from":135,"to":405,"stops":[...]}                   (sweep around the box centre; angles as in add_arc)
  {"type":"stripes"|"dots"|"grid","color":"#fff@0.08","color2":"transparent","size":8,"angle":45}
  {"type":"carbon","color":"#2a2d33","color2":"#0d0e10","size":4}      (carbon-fibre weave)
  {"type":"brushed","color":"#9aa3ad","color2":"#5c646e","angle":0,"amount":0.5}   (brushed metal)

Style object (every key optional; `style` may also be a theme style name, or {"use":"name", ...overrides}):
  fill, stroke {color, width, align: inside|center|outside}, opacity, noise: 0-1, blur: px (softens the
  whole element: light blooms, haze), blend: normal|add|screen|multiply|overlay,
  shadow {color, blur, dx, dy, spread, knockout}, glow {color, blur, strength, spread},
  inner_shadow {color, blur, dx, dy}, inner_glow {color, blur}, bevel {size, highlight, shadow}.
  shadow / glow / inner_shadow / inner_glow also accept a list for stacked effects.

Angles (add_arc, add_ticks, conic): degrees, 0 = 3 o'clock, clockwise. A classic gauge sweeps 135 -> 405.
Any element takes rotation (degrees clockwise) and origin [x, y] as the pivot (default: its centre) -
e.g. a needle pivoting on the gauge hub. Shapes and text take skew (degrees) for slanted, italic looks.

Always call `preview` after a group of changes and actually inspect the image before exporting.
"""

mcp = _Server("game-ui-mcp", instructions=INSTRUCTIONS)


def tool(fn):
    """Register ``fn`` as an MCP tool.

    The registered wrapper turns expected failures into ToolError so the model reads the real
    message (SDK 2.x hides the text of any other exception), and returns compact JSON.
    The plain function is returned unchanged so it can be called and tested directly.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise ToolError(str(exc)) from exc
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        return result

    mcp.tool()(wrapper)
    return fn

_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def set_store(new_store: Store | None) -> None:
    """Swap the document store (used by tests)."""
    global _store
    _store = new_store


# --------------------------------------------------------------------------- helpers

def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[" and text[-1:] in "}]":
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def _merge_style(style: Any, fill: Any = None, stroke: Any = None) -> Any:
    style, fill, stroke = _maybe_json(style), _maybe_json(fill), _maybe_json(stroke)
    if fill is None and stroke is None:
        return style
    if style is None:
        out: dict = {}
    elif isinstance(style, str):
        out = {"use": style}
    elif isinstance(style, dict):
        out = dict(style)
    else:
        raise ValueError("style must be a theme style name or an object")
    if fill is not None:
        out["fill"] = fill
    if stroke is not None:
        out["stroke"] = stroke
    return out


def _round(v: float) -> float:
    return round(float(v), 2)


def _box(st: Store, canvas: dict, el: dict) -> list[float]:
    x0, y0, x1, y1 = st.renderer(canvas).element_box(el)
    return [_round(x0), _round(y0), _round(x1 - x0), _round(y1 - y0)]


def _summary(st: Store, canvas: dict, el: dict, warnings: list[str] | None = None) -> dict:
    out = {"id": el["id"], "type": el["type"], "bbox_xywh": _box(st, canvas, el)}
    x, y, w, h = out["bbox_xywh"]
    if x < -0.5 or y < -0.5 or x + w > canvas["width"] + 0.5 or y + h > canvas["height"] + 0.5:
        out["note"] = "extends outside the canvas and will be cut off on export"
    if warnings:
        out["warnings"] = warnings
    return out


_SHORTHAND = ("fill", "stroke", "color")


def _prepare(etype: str, args: dict) -> dict:
    """Turn tool arguments into stored element properties."""
    props = {k: _maybe_json(v) for k, v in args.items() if v is not None}
    fill = props.pop("fill", None)
    stroke = props.pop("stroke", None)
    color = props.pop("color", None)
    if color is not None and fill is None:
        fill = color
    if etype in ("line", "icon", "arc", "ticks"):
        stroke = None
    style = _merge_style(props.pop("style", None), fill, stroke)
    if style is not None:
        props["style"] = style
    return props


def _add(canvas_id: str, etype: str, args: dict, checkpoint: bool = True) -> dict:
    st = store()
    canvas = st.get(canvas_id)
    el, warnings = st.add_element(canvas, etype, _prepare(etype, args), checkpoint=checkpoint)
    return _summary(st, canvas, el, warnings)


def _png_bytes(img: PILImage.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _out_path(path: str | None, default_name: str, subdir: str | None = None) -> Path:
    st = store()
    if path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = st.exports_dir / p
    else:
        p = st.exports_dir / (subdir or "") / default_name
    if p.suffix.lower() != ".png":
        raise ValueError("Output file must end with .png")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _out_dir(out_dir: str | None, sub: str = "") -> Path:
    st = store()
    if out_dir:
        p = Path(out_dir).expanduser()
        if not p.is_absolute():
            p = st.exports_dir / p
    else:
        p = st.exports_dir / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def _scale_suffix(scale: float) -> str:
    if abs(scale - 1) < 1e-6:
        return ""
    text = f"{scale:g}".replace(".", "_")
    return f"_{text}x"


# --------------------------------------------------------------------------- canvas tools

@tool
def create_canvas(name: str, width: int, height: int, background: str | dict | None = None):
    """Create a new canvas (one canvas = one asset or one screen mockup).

    Size the canvas to the asset: e.g. 64x64 icon, 320x96 button, 256x256 panel, 1920x1080 screen.
    Leave room for shadows/glows (they are cut off at the canvas edge).
    background: omit for transparent (normal for assets); a color or gradient object for mockups.
    Returns the canvas id used by every other tool.
    """
    canvas = store().create_canvas(name, width, height, _maybe_json(background))
    return {"canvas": canvas["id"], "width": canvas["width"], "height": canvas["height"],
            "workspace": str(store().workspace)}


@tool
def list_canvases():
    """List all canvases in the workspace (they persist between sessions)."""
    st = store()
    return {"workspace": str(st.workspace),
            "canvases": [{"canvas": c["id"], "name": c.get("name"), "width": c["width"], "height": c["height"],
                          "elements": len(c["elements"])} for c in st.canvases.values()]}


@tool
def get_canvas(canvas: str, details: bool = False, ids: list[str] | None = None):
    """Describe a canvas: element ids in draw order (bottom first) with type, group and bounding box.

    details=true also returns every stored property (use it before editing an element you did not just create).
    ids limits the result to those elements.
    """
    st = store()
    c = st.get(canvas)
    items = []
    for el in c["elements"]:
        if ids and el["id"] not in ids:
            continue
        item: dict = {"id": el["id"], "type": el["type"], "bbox_xywh": _box(st, c, el)}
        for key in ("group", "clip"):
            if el.get(key):
                item[key] = el[key]
        if el.get("visible") is False:
            item["visible"] = False
        if el["type"] == "text":
            item["text"] = el.get("text")
        if el["type"] == "icon":
            item["name"] = el.get("name")
        if details:
            item["props"] = {k: v for k, v in el.items() if k not in ("id", "type")}
        items.append(item)
    return {"canvas": c["id"], "name": c.get("name"), "width": c["width"], "height": c["height"],
            "background": c.get("background"), "elements": items}


@tool
def update_canvas(canvas: str, name: str | None = None, width: int | None = None, height: int | None = None,
                  background: str | dict | None = None, clear_background: bool = False):
    """Rename or resize a canvas, or change its background. Elements keep their coordinates when resizing."""
    st = store()
    c = st.get(canvas)
    changes: dict = {"name": name, "width": width, "height": height}
    if clear_background:
        changes["background"] = None
    elif background is not None:
        changes["background"] = _maybe_json(background)
    st.update_canvas(c, **changes)
    return {"canvas": c["id"], "name": c["name"], "width": c["width"], "height": c["height"],
            "background": c.get("background")}


@tool
def duplicate_canvas(canvas: str, new_name: str):
    """Copy a canvas with all its elements. Use it for button states (normal -> hover/pressed/disabled)
    or size variants: duplicate, then update_element only what differs."""
    st = store()
    clone = st.duplicate_canvas(st.get(canvas), new_name)
    return {"canvas": clone["id"], "elements": [e["id"] for e in clone["elements"]]}


@tool
def delete_canvas(canvas: str):
    """Delete a canvas and its saved project file. Exported PNGs are not touched."""
    st = store()
    c = st.get(canvas)
    st.delete_canvas(c)
    return {"deleted": c["id"]}


# --------------------------------------------------------------------------- drawing tools

@tool
def add_rect(canvas: str, x: float, y: float, width: float, height: float, radius: float | list[float] = 0,
             corner: str = "round", fill: str | dict | None = None, stroke: str | dict | None = None,
             style: str | dict | None = None, rotation: float = 0, skew: float = 0,
             origin: list[float] | None = None, id: str | None = None,
             group: str | None = None, clip: str | None = None):
    """Add a rectangle (panels, buttons, bars, slots, dividers).

    x, y: top-left corner. radius: one number or [top-left, top-right, bottom-right, bottom-left].
    corner: "round" (default) or "cut" (chamfered corners, sci-fi / military look).
    fill / stroke: shortcuts for style.fill / style.stroke (see server instructions for the style object).
    skew: slant in degrees (top edge moves right) -> parallelogram tabs and banners, racing-style.
    rotation turns it clockwise around origin [x, y] (default: its centre).
    clip: id of another element whose shape masks this one (e.g. a bar fill clipped to its rounded track).
    Default look with no fill, stroke or style is solid white.
    """
    return _add(canvas, "rect", dict(x=x, y=y, width=width, height=height, radius=radius or None,
                                     corner=None if corner == "round" else corner, fill=fill, stroke=stroke,
                                     style=style, rotation=rotation or None, skew=skew or None, origin=origin,
                                     id=id, group=group, clip=clip))


@tool
def add_ellipse(canvas: str, cx: float, cy: float, rx: float, ry: float | None = None,
                fill: str | dict | None = None, stroke: str | dict | None = None,
                style: str | dict | None = None, rotation: float = 0, id: str | None = None,
                group: str | None = None, clip: str | None = None):
    """Add a circle or ellipse centred at (cx, cy). Omit ry for a circle.
    Good for avatar frames, orbs, round buttons, radial glows (use a radial gradient fill)."""
    return _add(canvas, "ellipse", dict(cx=cx, cy=cy, rx=rx, ry=ry, fill=fill, stroke=stroke, style=style,
                                        rotation=rotation or None, id=id, group=group, clip=clip))


@tool
def add_line(canvas: str, points: list[list[float]], width: float = 2, color: str | dict | None = None,
             cap: str = "round", closed: bool = False, dash: list[float] | None = None,
             style: str | dict | None = None,
             id: str | None = None, group: str | None = None, clip: str | None = None):
    """Add a line or polyline through points [[x, y], ...] (dividers, accents, brackets, ticks).

    width: thickness. cap: "round" or "butt". color may be a color or a gradient object.
    dash [on, off] cuts it into segments: with cap "butt" and a thick width that gives LED rows,
    shift lights and segmented bars (draw a dim full row first, then a shorter bright one on top).
    style can add shadow/glow/opacity; style.stroke does not apply to lines.
    """
    return _add(canvas, "line", dict(points=points, width=width, cap=None if cap == "round" else cap,
                                     closed=closed or None, dash=dash, color=color, style=style, id=id,
                                     group=group, clip=clip))


@tool
def add_polygon(canvas: str, points: list[list[float]] | None = None, cx: float | None = None,
                cy: float | None = None, radius: float | None = None, sides: int = 6,
                inner_radius: float | None = None, point_rotation: float = 0, corner_radius: float = 0,
                fill: str | dict | None = None, stroke: str | dict | None = None,
                style: str | dict | None = None, rotation: float = 0, origin: list[float] | None = None,
                id: str | None = None, group: str | None = None, clip: str | None = None):
    """Add a polygon. Either give explicit points [[x, y], ...], or a regular shape with
    cx, cy, radius, sides (3 = triangle, 6 = hexagon, ...). The first vertex points up;
    point_rotation turns it. inner_radius makes a star (sides = number of points).
    corner_radius rounds the corners. rotation + origin [x, y] spin it around a pivot
    (a gauge needle: thin polygon, origin at the hub)."""
    if not points and (cx is None or cy is None or radius is None):
        raise ValueError("Give either points, or cx + cy + radius (+ sides) for a regular polygon")
    return _add(canvas, "polygon", dict(points=points, cx=cx, cy=cy, radius=radius,
                                        sides=None if points else sides, inner_radius=inner_radius,
                                        point_rotation=point_rotation or None, corner_radius=corner_radius or None,
                                        fill=fill, stroke=stroke, style=style, rotation=rotation or None,
                                        origin=origin, id=id, group=group, clip=clip))


@tool
def add_path(canvas: str, d: str, x: float = 0, y: float = 0, scale: float = 1,
             fill: str | dict | None = None, stroke: str | dict | None = None,
             style: str | dict | None = None, rotation: float = 0, skew: float = 0,
             origin: list[float] | None = None, id: str | None = None,
             group: str | None = None, clip: str | None = None):
    """Add a free-form shape from SVG path data (commands M L H V C S Q T A Z, absolute or relative).

    Use it for custom icons, banners, ribbons, angled HUD frames, curved bars. The path is scaled by
    `scale` and then moved by (x, y). Overlapping subpaths cut holes (even-odd).
    For line art: fill "none" plus stroke {"color": ..., "width": 2, "align": "center"}.
    """
    return _add(canvas, "path", dict(d=d, x=x or None, y=y or None, scale=None if scale == 1 else scale,
                                     fill=fill, stroke=stroke, style=style, rotation=rotation or None,
                                     skew=skew or None, origin=origin, id=id, group=group, clip=clip))


@tool
def add_arc(canvas: str, cx: float, cy: float, radius: float, start_angle: float, end_angle: float,
            thickness: float = 6, color: str | dict | None = None, cap: str = "butt",
            dash: list[float] | None = None, style: str | dict | None = None, id: str | None = None,
            group: str | None = None, clip: str | None = None):
    """Add a circular arc (gauge tracks and fills, rev bands, circular progress, radar rings).

    radius is the centre line of the band, thickness its width. Angles in degrees, 0 = 3 o'clock,
    clockwise; a classic gauge sweeps from 135 to 405. end - start = 360 gives a full ring.
    cap: "butt" (flat ends) or "round". dash [on, off] splits it into segments (LED bands, segmented bars).
    color may be a conic gradient with the same from/to angles, e.g. a rev band going green -> red:
      {"type":"conic","from":135,"to":405,"stops":[[0,"#22c55e"],[0.7,"#facc15"],[1,"#ef4444"]]}
    For a value fill, draw the track first, then a second arc ending at start + (end - start) * value.
    """
    return _add(canvas, "arc", dict(cx=cx, cy=cy, radius=radius, start_angle=start_angle, end_angle=end_angle,
                                    thickness=thickness, color=color, cap=None if cap == "butt" else cap,
                                    dash=dash, style=style, id=id, group=group, clip=clip))


def _ticks(c: dict, args: dict) -> dict:
    """Create minor ticks, major ticks and labels for a dial. The caller owns the undo checkpoint."""
    st = store()
    a = {k: _maybe_json(v) for k, v in args.items() if v is not None}
    try:
        cx, cy, radius = float(a["cx"]), float(a["cy"]), float(a["radius"])
        start, end, count = float(a["start_angle"]), float(a["end_angle"]), int(a["count"])
    except KeyError as exc:
        raise ValueError(f"add_ticks is missing {exc}") from exc
    length, width = float(a.get("length", 8)), float(a.get("width", 2))
    major_every = int(a.get("major_every", 0) or 0)
    major_length = float(a.get("major_length", length * 1.8))
    major_width = float(a.get("major_width", width * 1.5))
    base = slugify(a["id"], "ticks") if a.get("id") else None
    if base is None:
        counters = c.setdefault("counters", {})
        counters["dial"] = counters.get("dial", 0) + 1
        base = f"dial{counters['dial']}"
    group = a.get("group") or base
    common = dict(cx=cx, cy=cy, radius=radius, start_angle=start, end_angle=end, count=count, group=group,
                  clip=a.get("clip"), cap=a.get("cap"))
    created = []
    minor = dict(common, length=length, width=width, id=f"{base}_minor", color=a.get("color"), style=a.get("style"))
    if major_every > 1:
        minor["skip_every"] = major_every
    if major_every != 1:
        el, _ = st.add_element(c, "ticks", _prepare("ticks", minor), checkpoint=False)
        created.append(el["id"])
    if major_every >= 1:
        major = dict(common, length=major_length, width=major_width, every=major_every, id=f"{base}_major",
                     color=a.get("major_color", a.get("color")), style=a.get("style"))
        el, _ = st.add_element(c, "ticks", _prepare("ticks", major), checkpoint=False)
        created.append(el["id"])
    warnings: list[str] = []
    labels = a.get("labels")
    if labels:
        import math
        every = major_every if major_every >= 1 else 1
        full = abs(end - start) >= 360 - 1e-6
        positions = [i for i in range(count) if i % every == 0]
        if len(labels) != len(positions):
            raise ValueError(f"labels: got {len(labels)} but there are {len(positions)} "
                             f"{'major ' if major_every >= 1 else ''}ticks to label")
        size = float(a.get("label_size", 14))
        label_radius = float(a.get("label_radius", radius - (major_length if major_every >= 1 else length) - size * 0.95))
        step = (end - start) / (count if full else count - 1)
        for n, (i, text) in enumerate(zip(positions, labels)):
            ang = math.radians(start + step * i)
            label = dict(text=str(text), x=round(cx + label_radius * math.cos(ang), 2),
                         y=round(cy + label_radius * math.sin(ang), 2), anchor="center", size=size,
                         font=a.get("label_font"), weight=a.get("label_weight"), italic=a.get("label_italic"),
                         color=a.get("label_color", a.get("color")), id=f"{base}_label{n}", group=group)
            el, warn = st.add_element(c, "text", _prepare("text", label), checkpoint=False)
            warnings += warn
            created.append(el["id"])
    out: dict = {"group": group, "created": created}
    if warnings:
        out["warnings"] = sorted(set(warnings))
    return out


@tool
def add_ticks(canvas: str, cx: float, cy: float, radius: float, start_angle: float, end_angle: float, count: int,
              length: float = 8, width: float = 2, color: str | dict | None = None, major_every: int = 0,
              major_length: float | None = None, major_width: float | None = None,
              major_color: str | dict | None = None, labels: list[str] | None = None,
              label_radius: float | None = None, label_size: float = 14, label_font: str | None = None,
              label_weight: int | str | None = None, label_italic: bool | None = None,
              label_color: str | dict | None = None, style: str | dict | None = None,
              id: str | None = None, group: str | None = None):
    """Add dial tick marks (speedometer, tachometer, compass, clock) in one call.

    `count` ticks are spread evenly from start_angle to end_angle (both ends included), each drawn from
    `radius` inwards by `length`. Angles: 0 = 3 o'clock, clockwise; gauges usually run 135 -> 405.
    major_every=N makes every Nth tick a major tick (longer / thicker / own color); 0 = all the same.
    labels: one string per major tick (or per tick when there are no majors), placed upright at
    label_radius (default: just inside the ticks). Example, a 0-8 x1000 rpm dial with 4 minor steps
    between numbers: count=33, major_every=4, labels=["0","1","2","3","4","5","6","7","8"].
    Creates elements "<id>_minor", "<id>_major", "<id>_label0..N" in one group, so move_elements(group=...)
    and delete_elements(group=...) treat the dial as one piece.
    """
    st = store()
    c = st.get(canvas)
    st.checkpoint(c)
    try:
        result = _ticks(c, dict(cx=cx, cy=cy, radius=radius, start_angle=start_angle, end_angle=end_angle, count=count,
                                length=length, width=width, color=color, major_every=major_every,
                                major_length=major_length, major_width=major_width, major_color=major_color,
                                labels=labels, label_radius=label_radius, label_size=label_size,
                                label_font=label_font, label_weight=label_weight, label_italic=label_italic,
                                label_color=label_color, style=style, id=id, group=group))
    except Exception:
        st.drop_checkpoint(c)
        st.save_canvas(c)
        raise
    st.save_canvas(c)
    return result


@tool
def add_text(canvas: str, text: str, x: float, y: float, size: float | None = None, font: str | None = None,
             weight: int | str | None = None, color: str | dict | None = None, anchor: str = "top-left",
             align: str | None = None, max_width: float | None = None, fit_width: float | None = None,
             line_height: float | None = None, letter_spacing: float | None = None,
             uppercase: bool | str | None = None, italic: bool | None = None, skew: float = 0,
             stroke: str | dict | None = None, style: str | dict | None = None, rotation: float = 0,
             origin: list[float] | None = None, id: str | None = None,
             group: str | None = None, clip: str | None = None):
    """Add text.

    (x, y) is where `anchor` sits: "top-left", "center" (centre of a button), "middle-left",
    "middle-right", "top-center", "bottom-right", "baseline-left", ... Vertical centring uses the
    capital-letter height, so "center" at a button's centre looks optically centred.
    size: font size in px (default 24). font: family name (see list_fonts; default Inter).
    weight: 100-900 or "regular"/"medium"/"semibold"/"bold"/"black".
    color: color or gradient. stroke: outline drawn behind the glyphs, e.g. {"color":"#000","width":3}.
    max_width: wrap lines at this width. fit_width: shrink the size until the text fits this width.
    letter_spacing: extra px between letters. uppercase: true, or "tr" for Turkish i -> İ rules.
    italic: use the family's italic (Barlow Condensed has one); other fonts are slanted automatically.
    skew: extra slant in degrees.
    style may carry shadow / glow / opacity and text defaults (font, weight, size, letter_spacing).
    The result's bbox_xywh tells you how much room the text really takes.
    """
    return _add(canvas, "text", dict(text=text, x=x, y=y, size=size, font=font, weight=weight, color=color,
                                     anchor=None if anchor == "top-left" else anchor, align=align,
                                     max_width=max_width, fit_width=fit_width, line_height=line_height,
                                     letter_spacing=letter_spacing, uppercase=uppercase or None,
                                     italic=italic or None, skew=skew or None, stroke=stroke, style=style,
                                     rotation=rotation or None, origin=origin, id=id, group=group, clip=clip))


@tool
def add_icon(canvas: str, name: str, x: float, y: float, size: float = 24, color: str | dict | None = None,
             variant: str = "line", stroke_width: float = 2, style: str | dict | None = None,
             rotation: float = 0, id: str | None = None, group: str | None = None, clip: str | None = None):
    """Add a built-in icon (see list_icons) inside the square at (x, y) with side `size`.

    variant: "line" (outline) or "solid" (closed shapes filled). stroke_width is in 24-grid units
    (2 = normal, 1.5 = light, 2.5 = bold) and scales with size. color may be a gradient.
    style can add shadow / glow / inner_shadow. Need an icon that is not in the set? Draw it with add_path.
    """
    if variant not in ("line", "solid"):
        raise ValueError("variant must be 'line' or 'solid'")
    return _add(canvas, "icon", dict(name=name, x=x, y=y, size=size, color=color,
                                     variant=None if variant == "line" else variant,
                                     stroke_width=None if stroke_width == 2 else stroke_width, style=style,
                                     rotation=rotation or None, id=id, group=group, clip=clip))


@tool
def add_image(canvas: str, path: str, x: float, y: float, width: float | None = None, height: float | None = None,
              fit: str = "stretch", radius: float | list[float] = 0, tint: str | None = None,
              style: str | dict | None = None, rotation: float = 0, id: str | None = None,
              group: str | None = None, clip: str | None = None):
    """Place an existing image file (PNG/JPG/WebP) from disk, e.g. a character portrait, a logo, or a
    PNG exported earlier. Give width and/or height (the missing one keeps the aspect ratio).
    fit: "stretch", "contain" or "cover". radius rounds the corners. tint recolors using the alpha
    (handy for white icons). style can add shadow / glow / opacity."""
    return _add(canvas, "image", dict(path=str(Path(path).expanduser()), x=x, y=y, width=width, height=height,
                                      fit=None if fit == "stretch" else fit, radius=radius or None, tint=tint,
                                      style=style, rotation=rotation or None, id=id, group=group, clip=clip))


# --------------------------------------------------------------------------- editing tools

@tool
def update_element(canvas: str, id: str, props: dict):
    """Change properties of an element. `props` uses the same names as the add_* tools
    (x, y, width, radius, text, size, points, rotation, origin, skew, dash, group, clip, visible, ...).
    A "style" object is merged into the existing style key by key; set a key to null to remove it.
    Shortcuts "fill", "stroke" and "color" work here too. Example:
    {"width": 240, "style": {"glow": {"color": "$accent", "blur": 16}, "shadow": null}}"""
    st = store()
    c = st.get(canvas)
    el = st.find(c, id)
    warnings = st.update_element(c, el, _prepare_update(el, _maybe_json(props)))
    return _summary(st, c, el, warnings)


def _prepare_update(el: dict, props: dict) -> dict:
    if not isinstance(props, dict):
        raise ValueError("props must be an object")
    props = {k: _maybe_json(v) for k, v in props.items()}
    fill = props.pop("fill", None)
    stroke = props.pop("stroke", None)
    color = props.pop("color", None)
    if color is not None and fill is None:
        fill = color
    if fill is not None or stroke is not None:
        patch = props.get("style")
        if isinstance(patch, str):
            patch = {"use": patch}
        patch = dict(patch or {})
        if fill is not None:
            patch["fill"] = fill
        if stroke is not None:
            patch["stroke"] = stroke
        if isinstance(el.get("style"), str):
            patch = {"use": el["style"], **patch}
            el["style"] = {"use": el["style"]}
        props["style"] = patch
    return props


@tool
def delete_elements(canvas: str, ids: list[str] | None = None, group: str | None = None):
    """Delete elements by ids, or every element in a group."""
    st = store()
    c = st.get(canvas)
    return {"deleted": st.delete_elements(c, st.select(c, ids, group))}


@tool
def duplicate_element(canvas: str, id: str, dx: float = 0, dy: float = 0, new_id: str | None = None,
                      props: dict | None = None):
    """Copy an element, offset by (dx, dy), optionally overriding properties (same format as
    update_element). Use it for repeated things: inventory slots, list rows, bar segments."""
    st = store()
    c = st.get(canvas)
    src = st.find(c, id)
    patch = _prepare_update(dict(src), _maybe_json(props)) if props else None
    el, warnings = st.duplicate_element(c, src, dx, dy, new_id, patch)
    return _summary(st, c, el, warnings)


@tool
def move_elements(canvas: str, dx: float, dy: float, ids: list[str] | None = None, group: str | None = None):
    """Move elements (by ids, or a whole group) by (dx, dy)."""
    st = store()
    c = st.get(canvas)
    elements = st.select(c, ids, group)
    st.move_elements(c, elements, dx, dy)
    return {"moved": [_summary(st, c, e) for e in elements]}


@tool
def reorder_element(canvas: str, id: str, position: str):
    """Change draw order. position: "front" (top), "back" (bottom), "forward", "backward",
    "above:<other id>", "below:<other id>", or a zero-based index (0 = bottom)."""
    st = store()
    c = st.get(canvas)
    index = st.reorder(c, st.find(c, id), position)
    return {"id": id, "index": index, "order": [e["id"] for e in c["elements"]]}


@tool
def align_elements(canvas: str, ids: list[str], align: str | None = None, distribute: str | None = None,
                   relative_to: str = "canvas", gap: float | None = None):
    """Align and/or evenly space elements using their real bounding boxes.

    align: "left", "center-x", "right", "top", "center-y", "bottom", or "center" (both axes).
    relative_to: "canvas", "selection" (bounds of the ids), or the id of a reference element (not moved).
    distribute: "horizontal" or "vertical" - keeps the first and last in place and spaces the rest evenly,
    or uses a fixed `gap` between neighbours starting from the first.
    """
    st = store()
    c = st.get(canvas)
    elements = st.select(c, ids)
    r = st.renderer(c)
    boxes = {e["id"]: r.element_box(e) for e in elements}
    st.checkpoint(c)
    if align:
        if relative_to == "canvas":
            ref = (0.0, 0.0, float(c["width"]), float(c["height"]))
        elif relative_to == "selection":
            ref = (min(b[0] for b in boxes.values()), min(b[1] for b in boxes.values()),
                   max(b[2] for b in boxes.values()), max(b[3] for b in boxes.values()))
        else:
            ref = r.element_box(st.find(c, relative_to))
        modes = ["center-x", "center-y"] if align == "center" else [align]
        for mode in modes:
            for e in elements:
                if e["id"] == relative_to:
                    continue
                b = boxes[e["id"]]
                dx = dy = 0.0
                if mode == "left":
                    dx = ref[0] - b[0]
                elif mode == "right":
                    dx = ref[2] - b[2]
                elif mode == "center-x":
                    dx = (ref[0] + ref[2]) / 2 - (b[0] + b[2]) / 2
                elif mode == "top":
                    dy = ref[1] - b[1]
                elif mode == "bottom":
                    dy = ref[3] - b[3]
                elif mode == "center-y":
                    dy = (ref[1] + ref[3]) / 2 - (b[1] + b[3]) / 2
                else:
                    st.drop_checkpoint(c)
                    raise ValueError("align must be left, center-x, right, top, center-y, bottom or center")
                st.shift(e, dx, dy)
                boxes[e["id"]] = (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)
    if distribute:
        axis = 0 if distribute == "horizontal" else 1 if distribute == "vertical" else None
        if axis is None:
            st.drop_checkpoint(c)
            raise ValueError("distribute must be 'horizontal' or 'vertical'")
        ordered = sorted(elements, key=lambda e: boxes[e["id"]][axis])
        if len(ordered) >= 2:
            sizes = [boxes[e["id"]][axis + 2] - boxes[e["id"]][axis] for e in ordered]
            start = boxes[ordered[0]["id"]][axis]
            if gap is None:
                end = boxes[ordered[-1]["id"]][axis + 2]
                gap_value = (end - start - sum(sizes)) / (len(ordered) - 1)
            else:
                gap_value = float(gap)
            pos = start
            for e, size in zip(ordered, sizes):
                delta = pos - boxes[e["id"]][axis]
                st.shift(e, delta if axis == 0 else 0.0, delta if axis == 1 else 0.0)
                pos += size + gap_value
    st.save_canvas(c)
    return {"elements": [_summary(st, c, e) for e in elements]}


_BATCH_ADD = {"add_rect": "rect", "add_ellipse": "ellipse", "add_line": "line", "add_polygon": "polygon",
              "add_arc": "arc",
              "add_path": "path", "add_text": "text", "add_icon": "icon", "add_image": "image"}


@tool
def batch(canvas: str, operations: list[dict]):
    """Run many drawing operations in one call (much faster than one tool call per shape).

    Each operation is {"op": "<tool name>", ...the same arguments that tool takes, without canvas}.
    Supported ops: add_rect, add_ellipse, add_line, add_polygon, add_path, add_arc, add_ticks, add_text,
    add_icon, add_image, update_element, delete_elements, move_elements, reorder_element, duplicate_element.
    Give elements explicit "id"s so later operations (clip, update, reorder) can refer to them.
    The batch is atomic: if any operation fails nothing is changed, and the error names the failing op.
    """
    st = store()
    c = st.get(canvas)
    operations = _maybe_json(operations)
    if not isinstance(operations, list) or not operations:
        raise ValueError("operations must be a non-empty list")
    st.checkpoint(c)
    results = []
    try:
        for index, raw in enumerate(operations):
            op_args = dict(_maybe_json(raw))
            op = op_args.pop("op", None)
            op_args.pop("canvas", None)
            try:
                if op in _BATCH_ADD:
                    etype = _BATCH_ADD[op]
                    if etype == "image" and op_args.get("path"):
                        op_args["path"] = str(Path(op_args["path"]).expanduser())
                    el, warnings = st.add_element(c, etype, _prepare(etype, op_args), checkpoint=False)
                    results.append(_summary(st, c, el, warnings))
                elif op == "add_ticks":
                    results.append(_ticks(c, op_args))
                elif op == "update_element":
                    el = st.find(c, op_args["id"])
                    warnings = st.update_element(c, el, _prepare_update(el, op_args.get("props", {})), checkpoint=False)
                    results.append(_summary(st, c, el, warnings))
                elif op == "delete_elements":
                    targets = st.select(c, op_args.get("ids"), op_args.get("group"))
                    results.append({"deleted": st.delete_elements(c, targets, checkpoint=False)})
                elif op == "move_elements":
                    targets = st.select(c, op_args.get("ids"), op_args.get("group"))
                    st.move_elements(c, targets, op_args.get("dx", 0), op_args.get("dy", 0), checkpoint=False)
                    results.append({"moved": [e["id"] for e in targets]})
                elif op == "reorder_element":
                    st.reorder(c, st.find(c, op_args["id"]), op_args["position"], checkpoint=False)
                    results.append({"reordered": op_args["id"]})
                elif op == "duplicate_element":
                    src = st.find(c, op_args["id"])
                    tmp = dict(src)
                    patch = _prepare_update(tmp, op_args["props"]) if op_args.get("props") else None
                    clone = json.loads(json.dumps(src))
                    clone.pop("id")
                    st.shift(clone, float(op_args.get("dx", 0)), float(op_args.get("dy", 0)))
                    etype = clone.pop("type")
                    if op_args.get("new_id"):
                        clone["id"] = op_args["new_id"]
                    el, warnings = st.add_element(c, etype, clone, checkpoint=False)
                    if patch:
                        warnings = st.update_element(c, el, patch, checkpoint=False)
                    results.append(_summary(st, c, el, warnings))
                else:
                    raise ValueError(f"unknown op '{op}'")
            except KeyError as exc:
                raise ValueError(f"operation {index} ({op}): missing argument {exc}") from exc
            except (ValueError, TypeError) as exc:
                raise ValueError(f"operation {index} ({op}): {exc}") from exc
    except Exception:
        st.drop_checkpoint(c)
        st.save_canvas(c)
        raise
    st.save_canvas(c)
    return {"canvas": c["id"], "results": results, "element_count": len(c["elements"])}


@tool
def undo(canvas: str):
    """Undo the last change on a canvas (up to 40 steps; a whole batch counts as one step)."""
    st = store()
    c = st.get(canvas)
    if not st.undo(c):
        return {"undone": False, "reason": "nothing to undo"}
    return {"undone": True, "elements": [e["id"] for e in c["elements"]]}


# --------------------------------------------------------------------------- theme / info tools

@tool
def set_theme(colors: dict | None = None, styles: dict | None = None, replace: bool = False):
    """Define the design tokens shared by every canvas, so a whole UI kit stays consistent.

    colors: {"bg": "#0d0b14", "primary": "#8b5cf6", ...} -> use anywhere as "$primary" or "$primary@0.5".
    styles: named style objects, usable as style="panel" or {"use": "panel", ...overrides}. A style can
      hold any style key plus text defaults (font, weight, size, letter_spacing, uppercase), e.g.
      {"panel": {"fill": "$surface", "stroke": {"color": "$primary@0.6", "width": 2}, "shadow": {"blur": 24, "dy": 8}},
       "title": {"font": "Rajdhani", "weight": 700, "size": 32, "fill": "$text", "uppercase": true}}
    Entries are merged into the existing theme (set a key to null to remove it); replace=true starts over.
    Changing the theme restyles every element that references it.
    """
    st = store()
    st.set_theme(_maybe_json(colors), _maybe_json(styles), replace)
    return {"colors": st.theme["colors"], "styles": sorted(st.theme["styles"])}


@tool
def get_theme():
    """Return the current theme (color tokens and named styles)."""
    return store().theme


@tool
def list_fonts(include_system: bool = False):
    """List available font families. Bundled: Inter (clean, modern AAA menus), Barlow Condensed (motorsport /
    realistic HUD, has italics), Bebas Neue (tall display caps), Michroma (wide dashboard lettering),
    JetBrains Mono (fixed-width digits for timers and telemetry), Rajdhani and Orbitron (sci-fi/tech),
    Cinzel (fantasy/medieval), Fredoka (rounded, casual/cartoon), Bangers (comic/arcade).
    include_system=true also scans fonts installed on this computer. Extra .ttf/.otf files dropped
    into the workspace "fonts" folder are picked up as well."""
    st = store()
    st.fonts.rescan_user()
    return {"fonts": st.fonts.families(include_system), "user_font_folder": str(st.fonts_dir)}


@tool
def list_icons():
    """List the names of the built-in icons usable with add_icon."""
    return {"icons": icon_names(), "grid": "24x24, line style; variant='solid' fills closed shapes"}


@tool
def measure_text(text: str, size: float = 24, font: str | None = None, weight: int | str | None = None,
                 letter_spacing: float = 0, max_width: float | None = None, uppercase: bool | str | None = None,
                 line_height: float = 1.2):
    """Measure text without drawing it: width, height and wrapped lines. Use it to size a button or
    panel around a label before drawing."""
    st = store()
    el = {"id": "measure", "type": "text", "text": text, "x": 0, "y": 0, "size": size, "font": font,
          "weight": weight, "letter_spacing": letter_spacing, "max_width": max_width, "uppercase": uppercase,
          "line_height": line_height}
    layout = TextLayout({k: v for k, v in el.items() if v is not None}, {}, st.fonts, 4.0)
    x0, y0, x1, y1 = layout.box_units()
    return {"width": _round(x1 - x0), "height": _round(y1 - y0), "cap_height": _round(layout.cap / 4.0),
            "line_step": _round(layout.size * layout.line_height),
            "lines": [{"text": line, "width": _round(w / 4.0)} for line, w in layout.lines]}


# --------------------------------------------------------------------------- preview tools

def _label_font(size: int = 12):
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


@tool
def preview(canvas: str, background: str = "checker", max_size: int = 1024, show_ids: bool = False,
            region: list[float] | None = None, zoom: float | None = None):
    """Render a canvas and return the picture so you can check your work. Look at it critically:
    alignment, spacing, contrast, clipped shadows, text overflow.

    background (only behind transparent areas, never exported): "checker", "checker-light", "dark",
    "light", "game" (a busy scene stand-in, to test HUD readability) or any color.
    show_ids=true outlines every element with its id - use it to find what to fix.
    region [x, y, width, height] zooms into part of the canvas for detail checks.
    zoom: render scale; by default the view is scaled to fit max_size (small assets are enlarged up to 4x).
    """
    st = store()
    c = st.get(canvas)
    if region:
        rx, ry, rw, rh = (float(v) for v in region)
        if rw <= 0 or rh <= 0:
            raise ValueError("region width and height must be > 0")
    else:
        rx, ry, rw, rh = 0.0, 0.0, float(c["width"]), float(c["height"])
    max_size = max(64, min(2048, int(max_size)))
    if zoom:
        scale = max(0.05, min(8.0, float(zoom)))
        if max(rw, rh) * scale > 2048:
            scale = 2048 / max(rw, rh)
    else:
        scale = min(4.0, max_size / max(rw, rh))
    r = st.renderer(c, scale)
    img = r.render()
    if region:
        crop = (int(round(rx * scale)), int(round(ry * scale)), int(round((rx + rw) * scale)), int(round((ry + rh) * scale)))
        img = img.crop(crop)
    view = backdrop(img.size, background, st.theme.get("colors", {}))
    view = PILImage.alpha_composite(view, img)
    if show_ids:
        draw = ImageDraw.Draw(view)
        font = _label_font(12)
        palette = ["#ff4d6d", "#4dd2ff", "#ffd24d", "#7dff7a", "#d59bff", "#ff9d4d"]
        for i, el in enumerate(c["elements"]):
            if not el.get("visible", True):
                continue
            x0, y0, x1, y1 = r.element_box(el)
            col = palette[i % len(palette)]
            box = [(x0 - rx) * scale, (y0 - ry) * scale, (x1 - rx) * scale - 1, (y1 - ry) * scale - 1]
            if box[2] < 0 or box[3] < 0 or box[0] > view.width or box[1] > view.height:
                continue
            draw.rectangle(box, outline=col, width=1)
            label = el["id"]
            tw = draw.textlength(label, font=font)
            lx, ly = max(0, box[0]), max(0, box[1] - 14)
            draw.rectangle([lx, ly, lx + tw + 6, ly + 14], fill=col)
            draw.text((lx + 3, ly), label, fill="#000000", font=font)
    info = {"canvas": c["id"], "canvas_size": [c["width"], c["height"]], "shown_scale": round(scale, 3),
            "image_size": list(view.size), "elements": len(c["elements"])}
    if r.warnings:
        info["warnings"] = r.warnings
    return [Image(data=_png_bytes(view.convert("RGB")), format="png"), json.dumps(info)]


@tool
def contact_sheet(canvases: list[str] | None = None, columns: int = 4, cell: int = 256,
                  background: str = "checker", save_path: str | None = None):
    """Render several canvases side by side in one labelled image (all canvases if none are given).
    Use it to check that a kit is consistent (same radii, stroke weights, colors, lighting) and to
    compare button states. save_path optionally writes the sheet as a PNG too."""
    st = store()
    ids = canvases or list(st.canvases)
    if not ids:
        raise ValueError("No canvases to show")
    items = [st.get(i) for i in ids]
    columns = max(1, min(8, int(columns), len(items)))
    cell = max(96, min(512, int(cell)))
    rows = (len(items) + columns - 1) // columns
    pad, label_h = 12, 30
    sheet = PILImage.new("RGBA", (columns * (cell + pad) + pad, rows * (cell + label_h + pad) + pad), (18, 18, 22, 255))
    draw = ImageDraw.Draw(sheet)
    font = _label_font(12)
    warnings: list[str] = []
    for i, c in enumerate(items):
        scale = min(4.0, cell / max(c["width"], c["height"]))
        r = st.renderer(c, scale)
        img = r.render()
        warnings += r.warnings
        tile = backdrop((cell, cell), background, st.theme.get("colors", {}))
        tile.alpha_composite(img, ((cell - img.width) // 2, (cell - img.height) // 2))
        col, row = i % columns, i // columns
        ox, oy = pad + col * (cell + pad), pad + row * (cell + label_h + pad)
        sheet.paste(tile, (ox, oy))
        draw.text((ox, oy + cell + 2), c["id"][:38], fill="#ffffff", font=font)
        draw.text((ox, oy + cell + 15), f"{c['width']}x{c['height']}", fill="#9aa0aa", font=font)
    info: dict = {"canvases": [c["id"] for c in items], "image_size": list(sheet.size)}
    if save_path:
        out = _out_path(save_path, "contact_sheet.png")
        sheet.convert("RGB").save(out)
        info["saved"] = str(out)
    if warnings:
        info["warnings"] = warnings
    return [Image(data=_png_bytes(sheet.convert("RGB")), format="png"), json.dumps(info)]


# --------------------------------------------------------------------------- export tools

def _paint_axes(spec: Any) -> tuple[bool, bool]:
    """Whether a fill/stroke paint changes along x / along y (gradients only)."""
    if not isinstance(spec, dict):
        return False, False
    kind = str(spec.get("type", "linear")).lower()
    if kind == "radial":
        return True, True
    if kind == "linear":
        import math
        ang = math.radians(float(spec.get("angle", 90)))
        return abs(math.cos(ang)) > 0.01, abs(math.sin(ang)) > 0.01
    return False, False


def _gradient_blocks(c: dict, centre: tuple[float, float]) -> tuple[bool, bool]:
    """Axes that must not be 9-slice stretched because a gradient crosses the centre lines."""
    st = store()
    r = st.renderer(c)
    block_x = block_y = False
    for el in c["elements"]:
        if not el.get("visible", True):
            continue
        style = resolve_style(el.get("style"), st.theme)
        paints = [style.get("fill")]
        stroke = style.get("stroke")
        if isinstance(stroke, dict):
            paints.append(stroke.get("fill", stroke.get("color")))
        x0, y0, x1, y1 = r.element_box(el)
        for paint_spec in paints:
            along_x, along_y = _paint_axes(paint_spec)
            if along_x and x0 < centre[0] < x1:
                block_x = True
            if along_y and y0 < centre[1] < y1:
                block_y = True
    return block_x, block_y


def _render_export(c: dict, scale: float, include_background: bool, ids: list[str] | None, group: str | None):
    st = store()
    only = None
    if ids or group:
        only = {e["id"] for e in st.select(c, ids, group)}
    r = st.renderer(c, scale)
    return r.render(include_background=include_background, only=only), r.warnings


@tool
def export_png(canvas: str, path: str | None = None, scales: list[float] | None = None, trim: bool = False,
               padding: int = 0, include_background: bool = False, ids: list[str] | None = None,
               group: str | None = None):
    """Save a canvas as PNG (transparent unless include_background=true).

    path: file path ending in .png; relative paths go to the workspace "exports" folder
    (default "<canvas>.png"). scales: e.g. [1, 2] also writes "<name>_2x.png".
    trim crops transparent margins (padding keeps a few px). ids / group export only those elements,
    e.g. one layer of a mockup. For engine-ready assets with 9-slice data use export_asset instead.
    """
    st = store()
    c = st.get(canvas)
    base = _out_path(path, f"{c['id']}.png")
    files = []
    warnings: list[str] = []
    for scale in (scales or [1]):
        scale = float(scale)
        if not 0.1 <= scale <= 8:
            raise ValueError("scale must be between 0.1 and 8")
        img, warn = _render_export(c, scale, include_background, ids, group)
        warnings += warn
        offset = (0, 0)
        if trim:
            img, offset = ex.trim(img, int(round(padding * scale)))
        out = base.with_name(base.stem + _scale_suffix(scale) + ".png")
        img.save(out)
        files.append({"file": str(out), "scale": scale, "width": img.width, "height": img.height,
                      "trim_offset": list(offset)})
    result: dict = {"files": files}
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


@tool
def export_layers(canvas: str, out_dir: str | None = None, name: str | None = None,
                  groups: list[str] | None = None, scales: list[float] | None = None,
                  trim: bool = True, padding: int = 2):
    """Split one canvas into separate PNGs, one per element group, plus a layout JSON that says where
    each piece sits. Use it when the engine must animate parts independently: a gauge (dial / needle /
    value arc), a HUD mockup (each widget), a button (background / icon).

    Give elements a `group` while drawing. Elements without a group go to a layer called "base".
    groups: export only these (default: all, in draw order). trim crops each layer to its pixels and
    records its x, y in the layout, so the engine can place it exactly.
    Writes "<name>_<group>.png" and "<name>.layout.json":
      {"canvas": {"width", "height"}, "layers": [{"name", "file", "x", "y", "width", "height", "order"}]}
    (x, y, width, height in canvas units at 1x; rotating parts list their "origin" pivot too).
    """
    st = store()
    c = st.get(canvas)
    folder = _out_dir(out_dir, "layers")
    base = slugify(name or c["id"], "layers")
    order: list[str] = []
    members: dict[str, list[dict]] = {}
    for el in c["elements"]:
        if not el.get("visible", True):
            continue
        key = el.get("group") or "base"
        if key not in members:
            members[key] = []
            order.append(key)
        members[key].append(el)
    wanted = groups or order
    for g in wanted:
        if g not in members:
            raise ValueError(f"No visible elements in group '{g}'. Groups: {', '.join(order) or '(none)'}")
    layers = []
    warnings: list[str] = []
    scale_list = [float(v) for v in (scales or [1])]
    for g in wanted:
        ids = {e["id"] for e in members[g]}
        entry: dict = {"name": g, "order": order.index(g), "elements": sorted(ids)}
        pivots = [e["origin"] for e in members[g] if e.get("origin")]
        if pivots:
            entry["origin"] = pivots[0]
        files = []
        for scale in scale_list:
            if not 0.1 <= scale <= 8:
                raise ValueError("scale must be between 0.1 and 8")
            r = st.renderer(c, scale)
            img = r.render(include_background=False, only=ids)
            warnings += r.warnings
            offset = (0, 0)
            if trim:
                img, offset = ex.trim(img, int(round(padding * scale)))
            filename = f"{base}_{slugify(g, 'layer')}{_scale_suffix(scale)}.png"
            img.save(folder / filename)
            files.append({"file": filename, "scale": scale, "pixel_width": img.width, "pixel_height": img.height})
            if scale == scale_list[0]:
                entry.update(file=filename, x=_round(offset[0] / scale), y=_round(offset[1] / scale),
                             width=_round(img.width / scale), height=_round(img.height / scale))
        entry["files"] = files
        layers.append(entry)
    layout = {"canvas": {"name": c["id"], "width": c["width"], "height": c["height"]}, "layers": layers}
    layout_path = folder / f"{base}.layout.json"
    ex.write_json(layout_path, layout)
    result: dict = {"folder": str(folder), "layout": str(layout_path),
                    "layers": [{k: v for k, v in layer.items() if k != "elements"} for layer in layers]}
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


@tool
def export_asset(canvas: str, engine: str = "generic", out_dir: str | None = None, name: str | None = None,
                 nine_slice: str | dict | None = None, minimize: bool = True, scales: list[float] | None = None,
                 trim: bool = False, padding: int = 0):
    """Export a canvas as an engine-ready UI asset: PNG + "<name>.gameui.json" metadata + manifest entry.

    engine: "unity", "unreal", "roblox" or "generic" (metadata for all three).
    out_dir: target folder, e.g. "<UnityProject>/Assets/UI/Generated"; default is the workspace exports folder.
    nine_slice: omit for fixed-size images (icons, logos). "auto" detects the stretchable centre, or give
      {"left": 24, "top": 24, "right": 24, "bottom": 24} in canvas px. Use it for panels, buttons, bars,
      tooltips - anything the engine should resize without distorting corners.
    minimize: shrink the stretchable centre to a few px so the texture stays tiny (9-slice only).
    The result contains the exact numbers per engine: Unity sprite border (L,B,R,T), Unreal brush
    Margin (L,T,R,B as fractions), Roblox SliceCenter. Run write_engine_helpers once per project folder
    to get the import scripts that apply them automatically.
    """
    st = store()
    c = st.get(canvas)
    engine = engine.lower()
    if engine not in ex.ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ex.ENGINES)}")
    folder = _out_dir(out_dir, engine)
    asset = slugify(name or c["id"], "asset")
    nine = _maybe_json(nine_slice)
    files = []
    notes: list[str] = []
    warnings: list[str] = []
    for scale in (scales or [1]):
        scale = float(scale)
        if not 0.1 <= scale <= 8:
            raise ValueError("scale must be between 0.1 and 8")
        img, warn = _render_export(c, scale, False, None, None)
        warnings += warn
        offset = (0, 0)
        if trim:
            img, offset = ex.trim(img, int(round(padding * scale)))
        borders = None
        if nine:
            if isinstance(nine, str):
                if nine.lower() != "auto":
                    raise ValueError("nine_slice must be 'auto' or an object with left/top/right/bottom")
                centre = ((offset[0] + img.width / 2) / scale, (offset[1] + img.height / 2) / scale)
                block_x, block_y = _gradient_blocks(c, centre)
                borders = ex.detect_nine_slice(img, block_x=block_x, block_y=block_y)
                if not borders["stretch_x"] and not borders["stretch_y"]:
                    note = ("auto 9-slice found no stretchable area: the centre is not uniform (gradient, pattern, "
                            "noise, or content such as text/icons in the middle). Keep the centre flat, or pass "
                            "explicit borders.")
                    if note not in notes:
                        notes.append(note)
                elif not borders["stretch_x"] or not borders["stretch_y"]:
                    axis = "horizontally" if borders["stretch_x"] else "vertically"
                    note = (f"asset only stretches {axis}; the other axis is not uniform (a gradient or content "
                            "runs along it). That is fine for fixed-height buttons/bars; use a flat fill if it "
                            "must stretch both ways.")
                    if note not in notes:
                        notes.append(note)
            else:
                try:
                    base = {k: float(nine[k]) for k in ("left", "top", "right", "bottom")}
                except (KeyError, TypeError) as exc:
                    raise ValueError("nine_slice object needs left, top, right and bottom") from exc
                borders = ex.scale_borders(base, scale)
                borders.update(stretch_x=True, stretch_y=True)
                if borders["left"] + borders["right"] >= img.width or borders["top"] + borders["bottom"] >= img.height:
                    raise ValueError("nine_slice borders are larger than the image")
            if minimize:
                img, borders = ex.minimize_nine_slice(img, borders)
        filename = asset + _scale_suffix(scale) + ".png"
        img.save(folder / filename)
        meta = ex.engine_metadata(engine, img.size, borders, scale)
        entry = {"file": filename, "scale": scale, "width": img.width, "height": img.height, **meta}
        sidecar: dict = {"name": asset, "file": filename, "width": img.width, "height": img.height, "scale": scale,
                         "has_border": bool(borders), "pixels_per_unit": 100 * scale,
                         "border_left": 0, "border_bottom": 0, "border_right": 0, "border_top": 0}
        if borders:
            entry["nine_slice"] = {k: borders[k] for k in ("left", "top", "right", "bottom")}
            sidecar.update(border_left=borders["left"], border_bottom=borders["bottom"],
                           border_right=borders["right"], border_top=borders["top"])
        sidecar.update(meta)
        ex.write_json(folder / (Path(filename).stem + ".gameui.json"), sidecar)
        files.append(entry)
        rb_warn = meta.get("roblox", {}).get("warning")
        if rb_warn and engine in ("roblox",):
            notes.append(rb_warn)
    manifest = folder / "game_ui_manifest.json"
    previous = {}
    if manifest.is_file():
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8")).get("assets", {}).get(asset, {})
        except Exception:
            previous = {}
    record: dict = {"canvas": c["id"], "files": files}
    if previous.get("roblox_id"):
        record["roblox_id"] = previous["roblox_id"]
    ex.update_manifest(folder, asset, record)
    result: dict = {"asset": asset, "folder": str(folder), "files": files, "manifest": str(manifest)}
    if notes:
        result["notes"] = notes
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


@tool
def export_atlas(canvases: list[str], path: str | None = None, scale: float = 1, padding: int = 2,
                 power_of_two: bool = False, max_size: int = 4096, trim: bool = False):
    """Pack several canvases into one atlas PNG + JSON frame list (icon sets, small HUD pieces).
    One atlas = one upload on Roblox (use ImageRectOffset / ImageRectSize) and one texture elsewhere.
    Writes "<name>.png" and "<name>.json" ({"frames": {canvas: {x, y, w, h}}})."""
    st = store()
    images = []
    warnings: list[str] = []
    for cid in canvases:
        c = st.get(cid)
        img, warn = _render_export(c, float(scale), False, None, None)
        warnings += warn
        if trim:
            img, _ = ex.trim(img)
        images.append((c["id"], img))
    atlas, frames = ex.pack_atlas(images, int(padding), int(max_size), power_of_two)
    out = _out_path(path, "atlas.png")
    atlas.save(out)
    data = {"image": out.name, "size": {"w": atlas.width, "h": atlas.height}, "scale": scale, "frames": frames}
    ex.write_json(out.with_suffix(".json"), data)
    result: dict = {"file": str(out), "json": str(out.with_suffix(".json")), "size": [atlas.width, atlas.height],
                    "frames": frames}
    if max(atlas.size) > ex.ROBLOX_MAX:
        result["note"] = f"larger than {ex.ROBLOX_MAX}px: Roblox would downscale it. Split it or lower the scale for Roblox."
    if warnings:
        result["warnings"] = sorted(set(warnings))
    return result


@tool
def write_engine_helpers(engine: str, out_dir: str, roblox_ids: dict | None = None,
                         unreal_content_path: str = "/Game/UI/Generated"):
    """Write the engine-side helper that applies exported metadata automatically.

    unity:  GameUiImporter.cs - out_dir must be an "Editor" folder inside Assets. It imports every PNG that
            has a .gameui.json next to it as a UI sprite with the 9-slice border set.
    unreal: import_game_ui.py - written into out_dir (the folder that holds game_ui_manifest.json). Run it in
            the editor's Python console to import textures with UI settings; it logs each brush Margin.
    roblox: GameUiAssets.luau - a ModuleScript built from game_ui_manifest.json in out_dir, with
            GameUi.apply(imageLabel, "asset_name"). After uploading the PNGs in Studio pass
            roblox_ids={"asset_name": 1234567890} to fill in the asset ids.
    """
    engine = engine.lower()
    folder = Path(out_dir).expanduser()
    if not folder.is_absolute():
        folder = store().exports_dir / folder
    folder.mkdir(parents=True, exist_ok=True)
    if engine == "unity":
        if folder.name != "Editor" and "Editor" not in folder.parts:
            raise ValueError("For Unity, out_dir must be (inside) a folder named 'Editor', e.g. <Project>/Assets/Editor")
        target = folder / "GameUiImporter.cs"
        target.write_text(engines.UNITY_IMPORTER, encoding="utf-8")
        return {"file": str(target), "next": "Unity compiles it automatically. Export assets with export_asset into "
                                             "any Assets folder; each PNG with a .gameui.json is set up on import."}
    manifest_path = folder / "game_ui_manifest.json"
    if engine == "unreal":
        target = folder / "import_game_ui.py"
        target.write_text(engines.UNREAL_IMPORT.replace("__DEST_PATH__", unreal_content_path), encoding="utf-8")
        note = "" if manifest_path.is_file() else " (no game_ui_manifest.json here yet - export_asset into this folder first)"
        return {"file": str(target), "next": f'In Unreal: Output Log -> Python ->  py "{target.as_posix()}"{note}'}
    if engine == "roblox":
        if not manifest_path.is_file():
            raise ValueError(f"No game_ui_manifest.json in {folder}. Run export_asset(engine='roblox', out_dir=...) first.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for asset, asset_id in (_maybe_json(roblox_ids) or {}).items():
            key = slugify(asset, "asset")
            if key not in manifest.get("assets", {}):
                raise ValueError(f"roblox_ids: asset '{asset}' is not in the manifest ({', '.join(sorted(manifest['assets']))})")
            manifest["assets"][key]["roblox_id"] = str(asset_id)
        ex.write_json(manifest_path, manifest)
        target = folder / "GameUiAssets.luau"
        target.write_text(engines.roblox_module(manifest), encoding="utf-8")
        missing = [a for a, e in manifest["assets"].items() if not e.get("roblox_id")]
        return {"file": str(target), "assets_without_id": missing,
                "next": "Upload the PNGs in Roblox Studio, then call this again with roblox_ids to fill them in."}
    raise ValueError("engine must be 'unity', 'unreal' or 'roblox'")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
