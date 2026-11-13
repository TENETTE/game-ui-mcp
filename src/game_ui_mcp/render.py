"""Scene renderer: turns canvas elements into antialiased RGBA pixels.

Shapes are rasterised as supersampled coverage masks (drawn at SSx and box
filtered down), then painted and composited with effects at output resolution.
All element coordinates are in canvas units; ``k`` is pixels per unit (1 = 1x).
"""

from __future__ import annotations

import math
import zlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import geometry as geo
from . import raster
from .colors import parse_color
from .fonts import FontLibrary, load_font, missing_glyphs, parse_weight
from .icons import get_icon

SS = 4
MAX_MASK_PIXELS = 40_000_000
TEXT_STYLE_KEYS = ("font", "weight", "size", "letter_spacing", "line_height", "uppercase", "italic")
VECTOR_TYPES = {"rect", "ellipse", "polygon", "line", "path", "icon", "arc", "ticks"}
LINE_TYPES = {"line", "icon", "arc", "ticks"}
BLENDS = ("normal", "add", "screen", "multiply", "overlay")
CUT_K = math.tan(math.pi / 8)

Box = tuple[float, float, float, float]


# --------------------------------------------------------------------------- style

def resolve_style(style: Any, theme: dict) -> dict:
    """Expand a style reference. ``style`` is None, a theme style name, or a dict
    (optionally with ``"use": "<theme style name>"`` as its base)."""
    styles = theme.get("styles", {})

    def lookup(name: str, depth: int = 0) -> dict:
        if name not in styles:
            known = ", ".join(sorted(styles)) or "(theme has no styles)"
            raise ValueError(f"Unknown theme style '{name}'. Known: {known}")
        if depth > 8:
            raise ValueError(f"Theme style reference loop at '{name}'")
        base = dict(styles[name])
        parent = base.pop("use", None)
        if parent:
            merged = lookup(parent, depth + 1)
            merged.update(base)
            return merged
        return base

    if style is None:
        return {}
    if isinstance(style, str):
        return lookup(style)
    if not isinstance(style, dict):
        raise ValueError("style must be a theme style name or an object")
    out: dict = {}
    spec = dict(style)
    parent = spec.pop("use", None)
    if parent:
        out.update(lookup(parent))
    for key, value in spec.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def _as_list(value: Any) -> list:
    if value is None or value is False:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _is_none(spec: Any) -> bool:
    return spec is None or spec is False or (isinstance(spec, str) and spec.strip().lower() in ("none", ""))


class Effects:
    def __init__(self, st: dict, colors: dict):
        self.outer: list[dict] = []
        self.inner: list[dict] = []
        for sh in _as_list(st.get("shadow")):
            self.outer.append(self._fx(sh, colors, "#000000@0.5", blur=8, dy=4))
        for gl in _as_list(st.get("glow")):
            fx = self._fx(gl, colors, "#ffffff", blur=12)
            fx["dx"] = fx["dy"] = 0.0
            self.outer.append(fx)
        bevel = st.get("bevel")
        if bevel:
            if not isinstance(bevel, dict):
                bevel = {}
            size = float(bevel.get("size", 2))
            soft = float(bevel.get("soft", size))
            self.inner.append({"color": parse_color(bevel.get("highlight", "#ffffff@0.35"), colors),
                               "blur": soft, "dx": 0.0, "dy": size, "strength": 1.0})
            self.inner.append({"color": parse_color(bevel.get("shadow", "#000000@0.35"), colors),
                               "blur": soft, "dx": 0.0, "dy": -size, "strength": 1.0})
        for sh in _as_list(st.get("inner_shadow")):
            self.inner.append(self._fx(sh, colors, "#000000@0.5", blur=6, dy=2))
        for gl in _as_list(st.get("inner_glow")):
            fx = self._fx(gl, colors, "#ffffff@0.6", blur=8)
            fx["dx"] = fx["dy"] = 0.0
            self.inner.append(fx)

    @staticmethod
    def _fx(spec: Any, colors: dict, default_color: str, blur: float = 8, dy: float = 0) -> dict:
        if isinstance(spec, (str, list, tuple)):
            spec = {"color": spec}
        if spec is True:
            spec = {}
        if not isinstance(spec, dict):
            raise ValueError(f"Effect must be an object like {{color, blur, dx, dy}}, got {spec!r}")
        return {
            "color": parse_color(spec.get("color", default_color), colors),
            "blur": max(0.0, float(spec.get("blur", blur))),
            "dx": float(spec.get("dx", 0)),
            "dy": float(spec.get("dy", dy)),
            "spread": max(0.0, float(spec.get("spread", 0))),
            "strength": max(0.0, float(spec.get("strength", 1))),
            "knockout": bool(spec.get("knockout", False)),
        }

    def pad(self) -> float:
        pad = 0.0
        for fx in self.outer:
            pad = max(pad, fx["blur"] * 1.5 + max(abs(fx["dx"]), abs(fx["dy"])) + fx.get("spread", 0))
        return pad


def _stroke_spec(st: dict, colors: dict) -> dict | None:
    spec = st.get("stroke")
    if _is_none(spec):
        return None
    if isinstance(spec, (str, list, tuple)):
        spec = {"color": spec}
    if not isinstance(spec, dict):
        raise ValueError("stroke must be a color or an object like {color, width, align}")
    width = float(spec.get("width", 2))
    if width <= 0:
        return None
    align = str(spec.get("align", "inside")).lower()
    if align not in ("inside", "center", "outside"):
        raise ValueError("stroke.align must be 'inside', 'center' or 'outside'")
    paint = spec.get("fill", spec.get("color", "#ffffff"))
    return {"paint": paint, "width": width, "align": align}


# --------------------------------------------------------------------------- painting

def _stops(spec: dict, colors: dict) -> tuple[np.ndarray, np.ndarray]:
    raw = spec.get("stops") or spec.get("colors")
    if raw is None and "from" in spec and "to" in spec:
        raw = [spec["from"], spec["to"]]
    if not raw or len(raw) < 2:
        raise ValueError("Gradient needs at least 2 stops: {'stops': [[0, '#111'], [1, '#333']]} or {'from':..., 'to':...}")
    offs, cols = [], []
    n = len(raw)
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            off, col = item.get("offset", i / (n - 1)), item.get("color")
        elif isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], (int, float)) \
                and not isinstance(item[1], (int, float)):
            off, col = item
        else:
            off, col = i / (n - 1), item
        offs.append(float(off))
        r, g, b, a = parse_color(col, colors)
        af = a / 255.0
        cols.append((r * af, g * af, b * af, a))  # premultiplied
    order = np.argsort(np.array(offs), kind="stable")
    return np.array(offs)[order], np.array(cols, dtype=np.float64)[order]


def _ramp(t: np.ndarray, offs: np.ndarray, cols: np.ndarray) -> np.ndarray:
    out = np.empty(t.shape + (4,), dtype=np.float64)
    for c in range(4):
        out[..., c] = np.interp(t, offs, cols[:, c])
    alpha = out[..., 3:4]
    safe = np.where(alpha > 0, alpha / 255.0, 1.0)
    out[..., :3] = out[..., :3] / safe
    return out


def _pattern_cov(spec: dict, kind: str, size: tuple[int, int], origin: tuple[float, float], k: float) -> np.ndarray:
    w, h = size
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    ux = (xs + 0.5) / k - origin[0]
    uy = (ys + 0.5) / k - origin[1]
    cell = max(0.5, float(spec.get("size", 8)))
    ang = math.radians(float(spec.get("angle", 45 if kind == "stripes" else 0)))
    ca, sa = math.cos(ang), math.sin(ang)
    rx, ry = ux * ca + uy * sa, -ux * sa + uy * ca
    if kind == "stripes":
        ratio = max(0.02, min(0.98, float(spec.get("ratio", 0.5))))
        f = np.mod(rx / cell, 1.0)
        f = np.where(f > (1 + ratio) / 2, f - 1.0, f)
        return np.clip(np.minimum(f, ratio - f) * cell * k + 0.5, 0, 1)
    if kind == "dots":
        radius = float(spec.get("radius", cell * 0.25))
        fx = (np.mod(rx / cell, 1.0) - 0.5) * cell
        fy = (np.mod(ry / cell, 1.0) - 0.5) * cell
        return np.clip((radius - np.hypot(fx, fy)) * k + 0.5, 0, 1)
    if kind == "grid":
        lw = float(spec.get("line_width", 1))
        dx = np.abs((np.mod(rx / cell + 0.5, 1.0) - 0.5) * cell)
        dy = np.abs((np.mod(ry / cell + 0.5, 1.0) - 0.5) * cell)
        return np.clip((lw / 2 - np.minimum(dx, dy)) * k + 0.5, 0, 1)
    if kind == "carbon":
        # 2x2 twill weave: alternating cells shaded along x or along y
        cu, cv = rx / cell, ry / cell
        fu, fv = np.mod(cu, 1.0), np.mod(cv, 1.0)
        horizontal = (np.floor(cu) + np.floor(cv)) % 2 == 0
        shade = np.where(horizontal, fu, fv)
        return np.clip(0.15 + 0.85 * shade, 0, 1)
    if kind == "brushed":
        # brushed metal: fine streaks running along the angle direction
        seed = zlib.crc32(repr(sorted((str(a), str(b)) for a, b in spec.items())).encode())
        rng = np.random.default_rng(seed)
        amount = max(0.0, min(1.0, float(spec.get("amount", 0.5))))
        span = max(h, w) * 2 + 8
        fine = rng.normal(0, 1, span * 2)
        coarse = np.repeat(rng.normal(0, 1, span // 6 + 2), 12)[: span * 2]
        streak = 0.65 * fine + 0.35 * coarse
        idx = np.clip((ry * k + span).astype(np.int64), 0, span * 2 - 1)
        grain = rng.normal(0, 0.25, (h, w))
        return np.clip(0.5 + (streak[idx] + grain) * 0.22 * amount, 0, 1)
    raise ValueError(f"Unknown fill type '{kind}'. Use linear, radial, conic, stripes, dots, grid, carbon or brushed.")


def paint(mask: Image.Image, spec: Any, box_px: Box, colors: dict, k: float) -> Image.Image | None:
    """Return an RGBA image: ``spec`` (color / gradient / pattern) limited to ``mask``."""
    if _is_none(spec):
        return None
    w, h = mask.size
    if not isinstance(spec, dict):
        r, g, b, a = parse_color(spec, colors)
        img = Image.new("RGBA", (w, h), (r, g, b, 0))
        img.putalpha(mask if a == 255 else mask.point(lambda v: v * a // 255))
        return img
    kind = str(spec.get("type", "linear")).lower()
    x0, y0, x1, y1 = box_px
    bw, bh = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
    if kind in ("linear", "radial", "conic"):
        offs, cols = _stops(spec, colors)
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
        xs += 0.5
        ys += 0.5
        if kind == "linear":
            ang = math.radians(float(spec.get("angle", 90)))
            ca, sa = math.cos(ang), math.sin(ang)
            length = abs(bw * ca) + abs(bh * sa)
            t = ((xs - (x0 + x1) / 2) * ca + (ys - (y0 + y1) / 2) * sa) / max(1e-6, length) + 0.5
        elif kind == "conic":
            cx = x0 + bw * float(spec.get("cx", 0.5))
            cy = y0 + bh * float(spec.get("cy", 0.5))
            a_from = float(spec.get("from", 0))
            a_to = float(spec.get("to", a_from + 360))
            span = a_to - a_from
            if abs(span) < 1e-6:
                raise ValueError("conic gradient: 'from' and 'to' angles must differ")
            ang = np.degrees(np.arctan2(ys - cy, xs - cx))            # 0 = 3 o'clock, clockwise
            rel = np.mod((ang - a_from) * (1 if span > 0 else -1), 360.0)
            width = min(360.0, abs(span))
            t = rel / width
            if width < 360:
                dead = 360.0 - width                                   # split the unused sector between both ends
                t = np.where(rel <= width, t, np.where(rel - width < dead / 2, 1.0, 0.0))
        else:
            cx = x0 + bw * float(spec.get("cx", 0.5))
            cy = y0 + bh * float(spec.get("cy", 0.5))
            rad = float(spec.get("r", 0.5))
            if spec.get("circle"):
                t = np.hypot(xs - cx, ys - cy) / max(1e-6, max(bw, bh) * rad)
            else:
                t = np.hypot((xs - cx) / max(1e-6, bw * rad), (ys - cy) / max(1e-6, bh * rad))
        rgba = _ramp(np.clip(t, 0, 1), offs, cols)
        if spec.get("dither", True):
            # half-a-level noise before rounding hides 8-bit banding in soft gradients
            rng = np.random.default_rng(zlib.crc32(repr(spec.get("stops") or spec.get("colors") or "").encode()))
            rgba[..., :3] += rng.uniform(-0.5, 0.5, (h, w, 1))
            soft = (rgba[..., 3] > 0.5) & (rgba[..., 3] < 254.5)
            rgba[..., 3] += np.where(soft, rng.uniform(-0.5, 0.5, (h, w)), 0.0)
    else:
        c1 = np.array(parse_color(spec.get("color", "#ffffff@0.15"), colors), dtype=np.float64)
        c2 = np.array(parse_color(spec.get("color2", spec.get("background", "transparent")), colors), dtype=np.float64)
        cov = _pattern_cov(spec, kind, (w, h), (x0 / k, y0 / k), k)[..., None]
        a1, a2 = c1[3] / 255.0, c2[3] / 255.0
        pre = cov * np.append(c1[:3] * a1, c1[3]) + (1 - cov) * np.append(c2[:3] * a2, c2[3])
        alpha = pre[..., 3:4]
        safe = np.where(alpha > 0, alpha / 255.0, 1.0)
        rgba = np.concatenate([pre[..., :3] / safe, alpha], axis=-1)
    m = np.asarray(mask, dtype=np.float64) / 255.0
    rgba[..., 3] *= m
    return Image.fromarray(np.clip(rgba + 0.5, 0, 255).astype(np.uint8), "RGBA")


def _tint(mask: Image.Image, color: tuple[int, int, int, int], strength: float = 1.0) -> Image.Image:
    r, g, b, a = color
    if a == 255 and strength == 1.0:
        alpha = mask
    else:
        f = a / 255.0 * strength
        alpha = mask.point(lambda v: min(255, int(v * f + 0.5)))
    img = Image.new("RGBA", mask.size, (r, g, b, 0))
    img.putalpha(alpha)
    return img


def _shift(mask: Image.Image, dx: int, dy: int, fill: int = 0) -> Image.Image:
    if not dx and not dy:
        return mask
    out = Image.new("L", mask.size, fill)
    out.paste(mask, (dx, dy))
    return out


def composite(dst: Image.Image, src: Image.Image, ox: int, oy: int, blend: str = "normal") -> None:
    """Composite ``src`` onto ``dst`` in place at (ox, oy), cropping to bounds."""
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(dst.width, ox + src.width), min(dst.height, oy + src.height)
    if x1 <= x0 or y1 <= y0:
        return
    part = src.crop((x0 - ox, y0 - oy, x1 - ox, y1 - oy))
    base = dst.crop((x0, y0, x1, y1))
    if blend == "add":
        b = np.asarray(base, dtype=np.float64)
        s = np.asarray(part, dtype=np.float64)
        sa = s[..., 3:4] / 255.0
        ba = b[..., 3:4] / 255.0
        rgb = np.clip(b[..., :3] * ba + s[..., :3] * sa, 0, 255)
        alpha = np.clip(b[..., 3:4] + s[..., 3:4] * (1 - ba), 0, 255)
        safe = np.where(alpha > 0, alpha / 255.0, 1.0)
        out = np.concatenate([np.clip(rgb / safe, 0, 255), alpha], axis=-1)
        merged = Image.fromarray((out + 0.5).astype(np.uint8), "RGBA")
    elif blend in ("screen", "multiply", "overlay"):
        b = np.asarray(base, dtype=np.float64) / 255.0
        s = np.asarray(part, dtype=np.float64) / 255.0
        bc, sc, ba, sa = b[..., :3], s[..., :3], b[..., 3:4], s[..., 3:4]
        if blend == "multiply":
            mixed = bc * sc
        elif blend == "screen":
            mixed = 1 - (1 - bc) * (1 - sc)
        else:
            mixed = np.where(bc <= 0.5, 2 * bc * sc, 1 - 2 * (1 - bc) * (1 - sc))
        pre = (1 - sa) * bc * ba + (1 - ba) * sc * sa + ba * sa * mixed
        alpha = ba + sa - ba * sa
        safe = np.where(alpha > 0, alpha, 1.0)
        out = np.concatenate([np.clip(pre / safe, 0, 1), alpha], axis=-1) * 255.0
        merged = Image.fromarray((out + 0.5).astype(np.uint8), "RGBA")
    else:
        merged = Image.alpha_composite(base, part)
    dst.paste(merged, (x0, y0))


# --------------------------------------------------------------------------- text layout

def parse_anchor(anchor: str | None) -> tuple[str, str]:
    """Parse an anchor like 'top-left', 'center', 'middle-right', 'baseline-center' into (h, v)."""
    tokens = [t for t in str(anchor or "top-left").lower().replace("_", "-").replace(" ", "-").split("-") if t]
    h = v = None
    centers = 0
    for t in tokens:
        if t in ("left", "right"):
            h = t
        elif t in ("top", "bottom", "middle", "baseline"):
            v = t
        elif t == "center":
            centers += 1
        else:
            raise ValueError(
                f"Bad anchor '{anchor}'. Examples: 'top-left', 'center', 'middle-left', 'bottom-right', 'baseline-center'.")
    if tokens in (["center"], ["middle"]):
        return "center", "middle"
    if centers and h is None:
        h = "center"
        centers -= 1
    if centers and v is None:
        v = "middle"
    return h or "left", v or "top"


def _tr_upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()


class TextLayout:
    """Lays out a text element at a given pixel density (px per canvas unit)."""

    def __init__(self, el: dict, st: dict, fonts: FontLibrary, ppu: float):
        text = str(el.get("text", ""))
        upper = el.get("uppercase", st.get("uppercase"))
        if upper:
            text = _tr_upper(text) if str(upper).lower() == "tr" else text.upper()
        self.family = el.get("font") or st.get("font")
        self.weight = parse_weight(el.get("weight", st.get("weight")))
        self.size = float(el.get("size") or st.get("size") or 24)
        self.spacing = float(el.get("letter_spacing", st.get("letter_spacing", 0)) or 0)
        self.line_height = float(el.get("line_height", st.get("line_height", 1.2)) or 1.2)
        self.ppu = ppu
        self.h, self.v = parse_anchor(el.get("anchor"))
        self.align = str(el.get("align") or self.h).lower()
        max_width = el.get("max_width")
        fit_width = el.get("fit_width")
        italic = bool(el.get("italic", st.get("italic")))
        self.path, self.var_weight, faux = fonts.resolve(self.family, self.weight, italic)
        self.skew = float(el.get("skew", 0) or 0) + (12.0 if faux else 0.0)

        self._load()
        if fit_width:
            min_size = float(el.get("min_size", 8))
            for _ in range(40):
                lines = self._wrap(text, float(max_width) * ppu if max_width else None)
                if max((w for _, w in lines), default=0) <= float(fit_width) * ppu or self.size <= min_size:
                    break
                self.size = max(min_size, self.size * 0.95)
                self._load()
        self.lines = self._wrap(text, float(max_width) * ppu if max_width else None)
        self.text = text

        n = max(1, len(self.lines))
        step = self.size * ppu * self.line_height
        x, y = float(el.get("x", 0)) * ppu, float(el.get("y", 0)) * ppu
        if self.v == "top":
            b0 = y + self.cap
        elif self.v == "middle":
            b0 = y + self.cap / 2 - (n - 1) * step / 2
        elif self.v == "bottom":
            b0 = y - (n - 1) * step - self.descent
        else:
            b0 = y - (n - 1) * step
        self.baselines = [b0 + i * step for i in range(n)]
        self.block_w = max((w for _, w in self.lines), default=0.0)
        if self.h == "left":
            bx = x
        elif self.h == "center":
            bx = x - self.block_w / 2
        else:
            bx = x - self.block_w
        self.block_x = bx
        self.line_x = []
        for _, w in self.lines:
            if self.align == "center":
                self.line_x.append(bx + (self.block_w - w) / 2)
            elif self.align == "right":
                self.line_x.append(bx + self.block_w - w)
            else:
                self.line_x.append(bx)
        # visual box in px at this density
        self.box_px = (bx, self.baselines[0] - self.cap, bx + self.block_w, self.baselines[-1] + self.descent)

    def _load(self) -> None:
        self.font = load_font(self.path, int(round(self.size * self.ppu)), self.var_weight)
        ascent, descent = self.font.getmetrics()
        self.ascent, self.descent = float(ascent), float(descent)
        try:
            self.cap = float(-self.font.getbbox("H", anchor="ls")[1])
        except Exception:
            self.cap = self.ascent * 0.72
        if self.cap <= 0:
            self.cap = self.ascent * 0.72

    def _width(self, s: str) -> float:
        if not s:
            return 0.0
        return float(self.font.getlength(s)) + self.spacing * self.ppu * max(0, len(s) - 1)

    def _wrap(self, text: str, max_px: float | None) -> list[tuple[str, float]]:
        lines: list[tuple[str, float]] = []
        for para in text.split("\n"):
            if max_px is None:
                lines.append((para, self._width(para)))
                continue
            cur = ""
            for word in para.split(" "):
                trial = word if not cur else cur + " " + word
                if cur and self._width(trial) > max_px:
                    lines.append((cur, self._width(cur)))
                    cur = word
                else:
                    cur = trial
            lines.append((cur, self._width(cur)))
        return lines

    def box_units(self) -> Box:
        x0, y0, x1, y1 = self.box_px
        return (x0 / self.ppu, y0 / self.ppu, x1 / self.ppu, y1 / self.ppu)

    def draw(self, draw: ImageDraw.ImageDraw, ox: float, oy: float, stroke_px: int = 0) -> None:
        """Draw glyph coverage (optionally dilated by stroke_px) at offset (-ox, -oy)."""
        kwargs: dict = {"font": self.font, "fill": 255, "anchor": "ls"}
        if stroke_px > 0:
            kwargs.update(stroke_width=stroke_px, stroke_fill=255)
        for (line, _), lx, by in zip(self.lines, self.line_x, self.baselines):
            if not line:
                continue
            if self.spacing:
                cx = lx
                for ch in line:
                    draw.text((cx - ox, by - oy), ch, **kwargs)
                    cx += float(self.font.getlength(ch)) + self.spacing * self.ppu
            else:
                draw.text((lx - ox, by - oy), line, **kwargs)


# --------------------------------------------------------------------------- vector geometry

class Geometry:
    def __init__(self) -> None:
        self.fills: list[list[geo.Point]] = []                                  # even-odd filled outlines
        self.lines: list[tuple[list[geo.Point], bool, float, str]] = []          # intrinsic strokes (pts, closed, width, cap)
        self.outline: list[tuple[list[geo.Point], bool]] = []                    # for style.stroke on polygons/paths
        self.knock: list[tuple[list[geo.Point], bool, float, str]] = []          # detail strokes cut out of solid icons
        self.kind = "poly"
        self.rect: tuple | None = None
        self.ellipse: tuple | None = None
        self.box: Box = (0, 0, 0, 0)
        self.paint_box: Box | None = None      # reference box for gradients when it differs from ``box``


def vector_geometry(el: dict, res: float) -> Geometry:
    g = Geometry()
    t = el["type"]
    if t == "rect":
        x, y, w, h = (float(el[k]) for k in ("x", "y", "width", "height"))
        if w < 0 or h < 0:
            raise ValueError("rect width and height cannot be negative")
        corner = str(el.get("corner", "round")).lower()
        if corner not in ("round", "cut"):
            raise ValueError("rect corner must be 'round' or 'cut'")
        g.box = (x, y, x + w, y + h)
        if w == 0 or h == 0:
            return g                      # collapsed (e.g. a bar animating from zero): draws nothing
        g.kind, g.rect = "rect", (x, y, w, h, el.get("radius", 0), corner)
        g.fills = [geo.rect_path(x, y, w, h, el.get("radius", 0), corner, res)]
    elif t == "ellipse":
        cx, cy, rx = float(el["cx"]), float(el["cy"]), float(el["rx"])
        ry = float(rx if el.get("ry") is None else el["ry"])
        if rx < 0 or ry < 0:
            raise ValueError("ellipse radii cannot be negative")
        g.box = (cx - rx, cy - ry, cx + rx, cy + ry)
        if rx == 0 or ry == 0:
            return g
        g.kind, g.ellipse = "ellipse", (cx, cy, rx, ry)
        g.fills = [geo.ellipse_path(cx, cy, rx, ry, res)]
    elif t == "polygon":
        if el.get("points"):
            pts = [(float(p[0]), float(p[1])) for p in el["points"]]
            if len(pts) < 3:
                raise ValueError("polygon needs at least 3 points")
        else:
            pts = geo.regular_polygon(float(el["cx"]), float(el["cy"]), float(el["radius"]), int(el.get("sides", 6)),
                                      float(el["inner_radius"]) if el.get("inner_radius") else None,
                                      float(el.get("point_rotation", 0)))
        if el.get("corner_radius"):
            pts = geo.round_corners(pts, float(el["corner_radius"]), res)
        g.fills = [pts]
        g.outline = [(pts, True)]
        g.box = geo.bbox_of([pts])
    elif t == "line":
        pts = [(float(p[0]), float(p[1])) for p in el.get("points", [])]
        if len(pts) < 2:
            raise ValueError("line needs at least 2 points")
        width = max(0.25, float(el.get("width", 2)))
        closed = bool(el.get("closed", False))
        g.lines = _dashed(el, pts, closed, width, str(el.get("cap", "round")).lower())
        x0, y0, x1, y1 = geo.bbox_of([pts])
        g.box = (x0 - width / 2, y0 - width / 2, x1 + width / 2, y1 + width / 2)
    elif t == "arc":
        cx, cy, radius = float(el["cx"]), float(el["cy"]), float(el["radius"])
        start, end = float(el.get("start_angle", 0)), float(el.get("end_angle", 360))
        width = max(0.25, float(el.get("thickness", 4)))
        if radius <= 0:
            raise ValueError("arc radius must be > 0")
        # gradients (conic sweeps above all) refer to the whole circle, whatever part of it is drawn
        g.paint_box = (cx - radius - width / 2, cy - radius - width / 2, cx + radius + width / 2, cy + radius + width / 2)
        if abs(end - start) < 1e-6:
            g.box = (cx, cy, cx, cy)
            return g                      # empty sweep (a value arc at zero): draws nothing
        full = abs(end - start) >= 360 - 1e-6
        pts = geo.arc_path(cx, cy, radius, start, start + 360 if full else end, res)
        if full:
            pts = pts[:-1]
        g.lines = _dashed(el, pts, full, width, str(el.get("cap", "butt")).lower())
        x0, y0, x1, y1 = geo.bbox_of([pts])
        g.box = (x0 - width / 2, y0 - width / 2, x1 + width / 2, y1 + width / 2)
    elif t == "ticks":
        cx, cy, radius = float(el["cx"]), float(el["cy"]), float(el["radius"])
        start, end = float(el.get("start_angle", 0)), float(el.get("end_angle", 360))
        count = int(el.get("count", 11))
        length = float(el.get("length", 8))
        width = max(0.25, float(el.get("width", 2)))
        if count < 2 or radius <= 0 or length <= 0:
            raise ValueError("ticks need count >= 2, radius > 0 and length > 0")
        g.paint_box = (cx - radius, cy - radius, cx + radius, cy + radius)
        every = int(el.get("every", 1) or 1)
        skip_every = int(el.get("skip_every", 0) or 0)
        full = abs(end - start) >= 360 - 1e-6
        step = (end - start) / (count if full else count - 1)
        cap = str(el.get("cap", "butt")).lower()
        for i in range(count):
            if i % every or (skip_every and i % skip_every == 0):
                continue
            a = math.radians(start + step * i)
            ca, sa = math.cos(a), math.sin(a)
            g.lines.append(([(cx + radius * ca, cy + radius * sa),
                             (cx + (radius - length) * ca, cy + (radius - length) * sa)], False, width, cap))
        if not g.lines:
            raise ValueError("ticks: every / skip_every removed every tick")
        x0, y0, x1, y1 = geo.bbox_of([pts for pts, _, _, _ in g.lines])
        g.box = (x0 - width / 2, y0 - width / 2, x1 + width / 2, y1 + width / 2)
    elif t in ("path", "icon"):
        if t == "icon":
            size = float(el.get("size", 24))
            scale = size / 24.0
            d = get_icon(el["name"])
            dx, dy = float(el.get("x", 0)), float(el.get("y", 0))
        else:
            scale = float(el.get("scale", 1))
            d = el["d"]
            dx, dy = float(el.get("x", 0)), float(el.get("y", 0))
        subs = [(geo.transform(pts, scale, dx, dy), closed) for pts, closed in geo.parse_svg_path(d, res * scale)]
        if t == "icon":
            width = max(0.25, float(el.get("stroke_width", 2)) * scale)
            solid = str(el.get("variant", "line")).lower() == "solid"
            for pts, closed in subs:
                g.lines.append((pts, closed, width, "round"))
                if solid and closed:
                    g.fills.append(pts)
                elif solid:
                    g.knock.append((pts, False, width, "round"))
            g.box = (dx, dy, dx + 24 * scale, dy + 24 * scale)
        else:
            g.fills = [pts for pts, _ in subs if len(pts) >= 3]
            g.outline = subs
            g.box = geo.bbox_of([pts for pts, _ in subs])
    else:
        raise ValueError(f"Not a vector element type: {t}")
    return g


def _dashed(el: dict, pts: list, closed: bool, width: float, cap: str) -> list:
    dash = el.get("dash")
    if not dash:
        return [(pts, closed, width, cap)]
    if isinstance(dash, (int, float)):
        dash = [dash, dash]
    pieces = geo.dash_polyline(pts, closed, dash, float(el.get("dash_offset", 0) or 0))
    if not pieces:
        raise ValueError("dash pattern left nothing to draw")
    return [(piece, False, width, cap) for piece in pieces]


def _skew_tan(el: dict) -> float:
    skew = float(el.get("skew", 0) or 0)
    if abs(skew) > 75:
        raise ValueError("skew must be between -75 and 75 degrees")
    return math.tan(math.radians(skew))


def _inset_rect(rect: tuple, delta: float, res: float) -> list[geo.Point] | None:
    """Rect outline grown (delta > 0) or shrunk (delta < 0) by ``delta`` on every side."""
    x, y, w, h, radius, corner = rect
    nw, nh = w + 2 * delta, h + 2 * delta
    if nw <= 0 or nh <= 0:
        return None
    radii = geo.normalize_radii(radius, w, h)
    factor = CUT_K if corner == "cut" else 1.0
    radii = [max(0.0, r + delta * factor) if r > 0 else 0.0 for r in radii]
    return geo.rect_path(x - delta, y - delta, nw, nh, radii, corner, res)


# --------------------------------------------------------------------------- renderer

class Renderer:
    def __init__(self, canvas: dict, theme: dict, fonts: FontLibrary, scale: float = 1.0):
        self.canvas = canvas
        self.theme = theme
        self.colors = theme.get("colors", {})
        self.fonts = fonts
        self.k = float(scale)
        self.width = max(1, int(round(canvas["width"] * self.k)))
        self.height = max(1, int(round(canvas["height"] * self.k)))
        self.warnings: list[str] = []
        self._by_id = {e["id"]: e for e in canvas["elements"]}

    # ---- public

    def render(self, include_background: bool = True, only: set[str] | None = None) -> Image.Image:
        img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        bg = self.canvas.get("background")
        if include_background and not _is_none(bg):
            full = Image.new("L", img.size, 255)
            layer = paint(full, bg, (0, 0, self.width, self.height), self.colors, self.k)
            if layer is not None:
                img = Image.alpha_composite(img, layer)
        for el in self.canvas["elements"]:
            if not el.get("visible", True):
                continue
            if only is not None and el["id"] not in only:
                continue
            try:
                result = self.render_element(el)
            except ValueError as exc:
                raise ValueError(f"Element '{el['id']}' ({el['type']}): {exc}") from exc
            if result is None:
                continue
            layer, ox, oy, blend = result
            composite(img, layer, ox, oy, blend)
        return img

    def element_box(self, el: dict) -> Box:
        """Bounds of the element geometry in canvas units (no effects), after rotation."""
        st = resolve_style(el.get("style"), self.theme)
        box = self._geom_box(el, st)
        pivot = self._pivot(el, box)
        sx = float(el.get("scale_x", el.get("scale", 1)) or 0)
        sy = float(el.get("scale_y", el.get("scale", 1)) or 0)
        if sx != 1 or sy != 1:
            box = (pivot[0] + (box[0] - pivot[0]) * sx, pivot[1] + (box[1] - pivot[1]) * sy,
                   pivot[0] + (box[2] - pivot[0]) * sx, pivot[1] + (box[3] - pivot[1]) * sy)
        return geo.rotated_bbox(box, float(el.get("rotation", 0) or 0), pivot)

    @staticmethod
    def _pivot(el: dict, box: Box) -> tuple[float, float]:
        """Rotation centre: the element's ``origin`` [x, y] if given, else the middle of its box."""
        origin = el.get("origin")
        if origin:
            try:
                return float(origin[0]), float(origin[1])
            except (TypeError, IndexError, ValueError) as exc:
                raise ValueError("origin must be [x, y]") from exc
        return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

    # ---- internals

    def _geom_box(self, el: dict, st: dict) -> Box:
        t = el["type"]
        if t in VECTOR_TYPES:
            box = vector_geometry(el, 1.0).box
            lean = abs(_skew_tan(el)) * (box[3] - box[1]) / 2
            return (box[0] - lean, box[1], box[2] + lean, box[3])
        if t == "text":
            layout = TextLayout(el, st, self.fonts, 4.0)
            box = layout.box_units()
            lean = abs(math.tan(math.radians(layout.skew))) * (box[3] - box[1]) / 2
            return (box[0] - lean, box[1], box[2] + lean, box[3])
        if t == "image":
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            w, h = self._image_size(el)
            return (x, y, x + w, y + h)
        raise ValueError(f"Unknown element type '{t}'")

    def _image_size(self, el: dict) -> tuple[float, float]:
        w, h = el.get("width"), el.get("height")
        if w and h:
            return float(w), float(h)
        src = self._load_image(el)
        iw, ih = src.size
        if w:
            return float(w), float(w) * ih / iw
        if h:
            return float(h) * iw / ih, float(h)
        return float(iw), float(ih)

    def _load_image(self, el: dict) -> Image.Image:
        path = Path(str(el.get("path", ""))).expanduser()
        if not path.is_file():
            raise ValueError(f"Image file not found: {path}")
        return Image.open(path).convert("RGBA")

    def _region(self, box: Box, pad: float, clip: bool = True) -> tuple[int, int, int, int] | None:
        """Pixel region (x0, y0, w, h) covering ``box`` + pad, limited to the padded canvas."""
        k = self.k
        x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
        if clip:
            x0, y0 = max(x0, -pad - 2), max(y0, -pad - 2)
            x1, y1 = min(x1, self.canvas["width"] + pad + 2), min(y1, self.canvas["height"] + pad + 2)
        if x1 <= x0 or y1 <= y0:
            return None
        px0, py0 = int(math.floor(x0 * k)) - 1, int(math.floor(y0 * k)) - 1
        px1, py1 = int(math.ceil(x1 * k)) + 1, int(math.ceil(y1 * k)) + 1
        return px0, py0, px1 - px0, py1 - py0

    @staticmethod
    def _pick_ss(w: int, h: int) -> int:
        ss = SS
        while ss > 1 and w * h * ss * ss > MAX_MASK_PIXELS:
            ss -= 1
        return ss

    def _vector_masks(self, el: dict, st: dict, region, ss: int, stroke: dict | None):
        """Return (shape_mask, stroke_mask|None) at region size."""
        px0, py0, w, h = region
        k = self.k
        f = k * ss
        g = vector_geometry(el, f)
        bw, bh = w * ss, h * ss
        offset = np.array([px0 * ss, py0 * ss], dtype=np.float64)

        lean = _skew_tan(el)
        mid_y = (g.box[1] + g.box[3]) / 2

        def px(pts) -> np.ndarray:
            arr = np.asarray(pts, dtype=np.float64)
            if lean:
                arr = arr.copy()
                arr[:, 0] += (mid_y - arr[:, 1]) * lean
            return arr * f - offset

        def fill(polys) -> np.ndarray:
            return raster.fill_polygons([px(p) for p in polys], bw, bh, "evenodd")

        def lines(items) -> np.ndarray:
            return raster.stroke_polylines(
                [(px(pts), closed, width * f / 2.0, cap == "round") for pts, closed, width, cap in items], bw, bh)

        shape = fill(g.fills)
        if g.lines:
            interior = None
            if g.knock and g.fills:
                # detail lines that sit inside a filled area become cut-outs instead of vanishing
                rim = [(pts, True, width * 1.5, "round") for pts, closed, width, _ in g.lines if closed]
                interior = shape & ~lines(rim)
            shape = shape | lines(g.lines)
            if interior is not None:
                shape = shape & ~(lines(g.knock) & interior)

        smask = None
        if stroke and el["type"] not in LINE_TYPES:
            sw, align = stroke["width"], stroke["align"]
            if g.kind in ("rect", "ellipse"):
                grow = {"inside": 0.0, "center": sw / 2, "outside": sw}[align]
                shrink = {"inside": sw, "center": sw / 2, "outside": 0.0}[align]
                if g.kind == "rect":
                    outer = _inset_rect(g.rect, grow, f) if grow else g.fills[0]
                    inner = _inset_rect(g.rect, -shrink, f) if shrink else g.fills[0]
                else:
                    cx, cy, rx, ry = g.ellipse
                    outer = geo.ellipse_path(cx, cy, rx + grow, ry + grow, f)
                    inner = geo.ellipse_path(cx, cy, rx - shrink, ry - shrink, f) if min(rx, ry) > shrink else None
                smask = fill([outer, inner] if inner else [outer])
            elif align == "center":
                smask = lines([(pts, c, sw, "round") for pts, c in g.outline])
            else:
                smask = np.zeros((bh, bw), dtype=np.uint8)
                closed_paths = [pts for pts, c in g.outline if c]
                if closed_paths:
                    band = lines([(pts, True, sw * 2, "round") for pts in closed_paths])
                    inside = fill(closed_paths)
                    smask = (band & inside) if align == "inside" else (band & ~inside)
                open_items = [(pts, False, sw, "round") for pts, c in g.outline if not c]
                if open_items:
                    smask = smask | lines(open_items)

        shape_img = Image.fromarray(shape, "L")
        stroke_img = Image.fromarray(smask, "L") if smask is not None else None
        rot = float(el.get("rotation", 0) or 0)
        if rot:
            pvx, pvy = self._pivot(el, g.box)
            cx = (pvx * k - px0) * ss
            cy = (pvy * k - py0) * ss
            shape_img = shape_img.rotate(-rot, resample=Image.BICUBIC, center=(cx, cy))
            if stroke_img is not None:
                stroke_img = stroke_img.rotate(-rot, resample=Image.BICUBIC, center=(cx, cy))
        if ss > 1:
            shape_img = shape_img.reduce(ss)
            if stroke_img is not None:
                stroke_img = stroke_img.reduce(ss)
        return shape_img, stroke_img

    def _text_masks(self, el: dict, st: dict, region, ss: int, stroke: dict | None):
        px0, py0, w, h = region
        k = self.k
        layout = TextLayout(el, st, self.fonts, k * ss)
        miss = missing_glyphs(layout.font, layout.text)
        if miss:
            self.warnings.append(
                f"{el['id']}: font '{layout.family or 'Inter'}' has no glyph for {''.join(miss)!r}; pick another font.")
        big = (w * ss, h * ss)
        shape = Image.new("L", big, 0)
        layout.draw(ImageDraw.Draw(shape), px0 * ss, py0 * ss)
        smask = None
        if stroke:
            smask = Image.new("L", big, 0)
            layout.draw(ImageDraw.Draw(smask), px0 * ss, py0 * ss, max(1, int(round(stroke["width"] * k * ss))))
        bx0, by0, bx1, by1 = layout.box_px
        if layout.skew:
            lean = math.tan(math.radians(layout.skew))
            mid = (by0 + by1) / 2 - py0 * ss
            matrix = (1, lean, -lean * mid, 0, 1, 0)       # output (x, y) samples input (x + lean * (y - mid), y)
            shape = shape.transform(big, Image.AFFINE, matrix, resample=Image.BICUBIC)
            if smask is not None:
                smask = smask.transform(big, Image.AFFINE, matrix, resample=Image.BICUBIC)
        rot = float(el.get("rotation", 0) or 0)
        if rot:
            if el.get("origin"):
                pvx, pvy = self._pivot(el, (0, 0, 0, 0))
                cx, cy = (pvx * k - px0) * ss, (pvy * k - py0) * ss
            else:
                cx, cy = (bx0 + bx1) / 2 - px0 * ss, (by0 + by1) / 2 - py0 * ss
            shape = shape.rotate(-rot, resample=Image.BICUBIC, center=(cx, cy))
            if smask is not None:
                smask = smask.rotate(-rot, resample=Image.BICUBIC, center=(cx, cy))
        if ss > 1:
            shape = shape.reduce(ss)
            if smask is not None:
                smask = smask.reduce(ss)
        return shape, smask

    def _image_layer(self, el: dict, region):
        """Return (alpha_mask, rgba_layer) for an image element at region size."""
        px0, py0, w, h = region
        k = self.k
        src = self._load_image(el)
        uw, uh = self._image_size(el)
        tw, th = max(1, int(round(uw * k))), max(1, int(round(uh * k)))
        fit = str(el.get("fit", "stretch")).lower()
        if fit == "stretch":
            img = src.resize((tw, th), Image.LANCZOS)
        elif fit in ("contain", "cover"):
            ratio = (min if fit == "contain" else max)(tw / src.width, th / src.height)
            rs = src.resize((max(1, int(round(src.width * ratio))), max(1, int(round(src.height * ratio)))), Image.LANCZOS)
            img = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            img.paste(rs, ((tw - rs.width) // 2, (th - rs.height) // 2))
        else:
            raise ValueError("image fit must be 'stretch', 'contain' or 'cover'")
        if el.get("radius"):
            ss = 4
            pts = geo.rect_path(0, 0, tw / k, th / k, el["radius"], str(el.get("corner", "round")), k * ss)
            m = Image.fromarray(raster.fill_polygons([np.asarray(pts) * k * ss], tw * ss, th * ss), "L")
            img.putalpha(ImageChops.multiply(img.getchannel("A"), m.reduce(ss)))
        if not _is_none(el.get("tint")):
            r, g, b, a = parse_color(el["tint"], self.colors)
            alpha = img.getchannel("A")
            img = Image.new("RGBA", img.size, (r, g, b, 0))
            img.putalpha(alpha if a == 255 else alpha.point(lambda v: v * a // 255))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ox = int(round(float(el.get("x", 0)) * k)) - px0
        oy = int(round(float(el.get("y", 0)) * k)) - py0
        layer.paste(img, (ox, oy))
        rot = float(el.get("rotation", 0) or 0)
        if rot:
            centre = (ox + tw / 2, oy + th / 2)
            if el.get("origin"):
                pvx, pvy = self._pivot(el, (0, 0, 0, 0))
                centre = (pvx * k - px0, pvy * k - py0)
            layer = layer.rotate(-rot, resample=Image.BICUBIC, center=centre)
        return layer.getchannel("A"), layer

    def shape_mask(self, el: dict, region) -> Image.Image:
        """Filled-shape coverage of an element inside ``region`` (used for clipping)."""
        st = resolve_style(el.get("style"), self.theme)
        t = el["type"]
        ss = min(2, self._pick_ss(region[2], region[3]))
        if t in VECTOR_TYPES:
            return self._vector_masks(el, st, region, ss, None)[0]
        if t == "text":
            return self._text_masks(el, st, region, ss, None)[0]
        return self._image_layer(el, region)[0]

    def render_element(self, el: dict):
        """Render one element to (layer, x, y, blend). Handles scale_x / scale_y, which grow or shrink
        the finished element (effects included) around its pivot, the way an engine scales a widget."""
        sx = float(el.get("scale_x", el.get("scale", 1)) or 0)
        sy = float(el.get("scale_y", el.get("scale", 1)) or 0)
        if sx == 1 and sy == 1:
            return self._render_plain(el)
        if sx <= 0 or sy <= 0:
            return None
        if max(sx, sy) > 16:
            raise ValueError("scale is limited to 16")
        k = self.k
        m = max(sx, sy)
        plain = {key: value for key, value in el.items() if key not in ("scale", "scale_x", "scale_y", "clip")}
        sub = Renderer(self.canvas, self.theme, self.fonts, k * m)      # true vector scale, not a resized bitmap
        result = sub._render_plain(plain, clip_region=False)
        self.warnings += sub.warnings
        if result is None:
            return None
        layer, px0, py0, blend = result
        if sx != sy:
            layer = layer.resize((max(1, int(round(layer.width * sx / m))), max(1, int(round(layer.height * sy / m)))),
                                 Image.LANCZOS)
        st = resolve_style(el.get("style"), self.theme)
        pvx, pvy = self._pivot(el, self._geom_box(plain, st))
        ox = int(round(pvx * k - (pvx * k * m - px0) * sx / m))
        oy = int(round(pvy * k - (pvy * k * m - py0) * sy / m))
        clip_id = el.get("clip")
        if clip_id:
            target = self._by_id.get(clip_id)
            if target is None:
                raise ValueError(f"clip target '{clip_id}' does not exist on this canvas")
            cm = self.shape_mask(target, (ox, oy, layer.width, layer.height))
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), cm))
        return layer, ox, oy, blend

    def _render_plain(self, el: dict, clip_region: bool = True):
        st = resolve_style(el.get("style"), self.theme)
        t = el["type"]
        k = self.k
        opacity = float(st.get("opacity", 1.0))
        if opacity <= 0:
            return None
        fx = Effects(st, self.colors)
        stroke = _stroke_spec(st, self.colors)
        box = self._geom_box(el, st)
        grow = 0.0
        if stroke:
            grow = stroke["width"] if (t == "text" or stroke["align"] == "outside") else (
                stroke["width"] / 2 if stroke["align"] == "center" else 0.0)
        gbox = (box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow)
        rot = float(el.get("rotation", 0) or 0)
        soften = max(0.0, float(st.get("blur", 0) or 0))
        pad = fx.pad() + soften * 1.5 + (3 if t == "text" else 1)
        pivot = self._pivot(el, box)
        if rot:
            # the unrotated shape is drawn first, so the region must hold every orientation
            half = max(math.hypot(px_ - pivot[0], py_ - pivot[1])
                       for px_ in (gbox[0], gbox[2]) for py_ in (gbox[1], gbox[3]))
            cx, cy = pivot
            region = self._region((cx - half, cy - half, cx + half, cy + half), pad, clip=False)
        else:
            region = self._region(gbox, pad, clip=clip_region)
        if region is None:
            return None
        px0, py0, w, h = region
        ss = self._pick_ss(w, h)

        color_layer = None
        if t in VECTOR_TYPES:
            shape, smask = self._vector_masks(el, st, region, ss, stroke)
        elif t == "text":
            shape, smask = self._text_masks(el, st, region, ss, stroke)
        elif t == "image":
            shape, color_layer = self._image_layer(el, region)
            smask = None
        else:
            raise ValueError(f"Unknown element type '{t}'")

        fill_spec = st.get("fill", "#ffffff" if (stroke is None or t in LINE_TYPES or t == "text") else None)
        has_fill = color_layer is not None or not _is_none(fill_spec)
        if has_fill and smask is not None:
            sil = ImageChops.lighter(shape, smask)
        elif has_fill:
            sil = shape
        elif smask is not None:
            sil = smask
        else:
            return None

        paint_ref = vector_geometry(el, 1.0).paint_box if t in ("arc", "ticks") else None
        rbox_plain = geo.rotated_bbox(paint_ref or box, rot, pivot)
        box_px = (rbox_plain[0] * k - px0, rbox_plain[1] * k - py0, rbox_plain[2] * k - px0, rbox_plain[3] * k - py0)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        for e in fx.outer:
            m = sil
            spread = int(round(e["spread"] * k))
            if spread > 0:
                m = m.filter(ImageFilter.MaxFilter(spread * 2 + 1))
            m = _shift(m, int(round(e["dx"] * k)), int(round(e["dy"] * k)))
            if e["blur"] > 0:
                m = m.filter(ImageFilter.GaussianBlur(e["blur"] * k / 2))
            if e["knockout"]:
                m = ImageChops.multiply(m, ImageChops.invert(sil))
            layer = Image.alpha_composite(layer, _tint(m, e["color"], e["strength"]))

        stroke_img = paint(smask, stroke["paint"], box_px, self.colors, k) if (smask is not None and stroke) else None
        if t == "text" and stroke_img is not None:
            layer = Image.alpha_composite(layer, stroke_img)
            stroke_img = None

        if has_fill:
            if color_layer is not None:
                fill_img = color_layer
            else:
                fill_img = paint(shape, fill_spec, box_px, self.colors, k)
            noise = float(st.get("noise", 0) or 0)
            if noise > 0 and fill_img is not None:
                arr = np.asarray(fill_img, dtype=np.float64).copy()
                rng = np.random.default_rng(zlib.crc32(el["id"].encode()))
                arr[..., :3] = np.clip(arr[..., :3] + rng.normal(0, noise * 128, (h, w, 1)), 0, 255)
                fill_img = Image.fromarray(arr.astype(np.uint8), "RGBA")
            if fill_img is not None:
                layer = Image.alpha_composite(layer, fill_img)
            for e in fx.inner:
                m = ImageChops.invert(shape)
                m = _shift(m, int(round(e["dx"] * k)), int(round(e["dy"] * k)), fill=255)
                if e["blur"] > 0:
                    m = m.filter(ImageFilter.GaussianBlur(e["blur"] * k / 2))
                m = ImageChops.multiply(m, shape)
                layer = Image.alpha_composite(layer, _tint(m, e["color"], e.get("strength", 1.0)))

        if stroke_img is not None:
            layer = Image.alpha_composite(layer, stroke_img)

        if soften > 0:
            # blur in premultiplied space so transparent pixels do not bleed dark fringes
            layer = layer.convert("RGBa").filter(ImageFilter.GaussianBlur(soften * k / 2)).convert("RGBA")

        clip_id = el.get("clip")
        if clip_id:
            target = self._by_id.get(clip_id)
            if target is None:
                raise ValueError(f"clip target '{clip_id}' does not exist on this canvas")
            cm = self.shape_mask(target, region)
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), cm))
        if opacity < 1:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * opacity + 0.5)))
        blend = str(st.get("blend", "normal")).lower()
        if blend not in BLENDS:
            raise ValueError(f"style.blend must be one of {', '.join(BLENDS)}")
        return layer, px0, py0, blend


def shift_element(el: dict, dx: float, dy: float) -> None:
    """Move an element (and its rotation pivot) by (dx, dy) in place."""
    if "points" in el and el["type"] in ("polygon", "line"):
        el["points"] = [[float(p[0]) + dx, float(p[1]) + dy] for p in el["points"]]
    elif "cx" in el:
        el["cx"], el["cy"] = float(el["cx"]) + dx, float(el["cy"]) + dy
    else:
        el["x"], el["y"] = float(el.get("x", 0)) + dx, float(el.get("y", 0)) + dy
    if el.get("origin"):
        el["origin"] = [float(el["origin"][0]) + dx, float(el["origin"][1]) + dy]


# --------------------------------------------------------------------------- preview helpers

def checkerboard(size: tuple[int, int], cell: int = 12, dark: bool = True) -> Image.Image:
    w, h = size
    a, b = ((38, 38, 44), (52, 52, 60)) if dark else ((235, 235, 238), (255, 255, 255))
    ys, xs = np.mgrid[0:h, 0:w]
    pick = ((xs // cell + ys // cell) % 2).astype(bool)
    arr = np.empty((h, w, 4), dtype=np.uint8)
    arr[..., 3] = 255
    for c in range(3):
        arr[..., c] = np.where(pick, a[c], b[c])
    return Image.fromarray(arr, "RGBA")


def backdrop(size: tuple[int, int], background: str, colors: dict) -> Image.Image:
    key = (background or "checker").lower()
    if key in ("checker", "checker-dark"):
        return checkerboard(size, dark=True)
    if key == "checker-light":
        return checkerboard(size, dark=False)
    if key == "dark":
        return Image.new("RGBA", size, (24, 24, 30, 255))
    if key == "light":
        return Image.new("RGBA", size, (240, 240, 244, 255))
    if key == "game":
        # a busy mid-tone gradient standing in for a game scene behind the HUD
        w, h = size
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
        arr = np.empty((h, w, 4), dtype=np.uint8)
        t = ys / max(1, h - 1)
        wave = 12 * np.sin(xs / 37.0) * np.cos(ys / 23.0)
        arr[..., 0] = np.clip(70 + 60 * t + wave, 0, 255)
        arr[..., 1] = np.clip(110 + 30 * (1 - t) + wave, 0, 255)
        arr[..., 2] = np.clip(150 - 70 * t + wave, 0, 255)
        arr[..., 3] = 255
        return Image.fromarray(arr, "RGBA")
    r, g, b, _ = parse_color(background, colors)
    return Image.new("RGBA", size, (r, g, b, 255))
