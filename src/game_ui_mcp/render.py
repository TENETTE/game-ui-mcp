"""Scene renderer: turns canvas elements into antialiased RGBA pixels.

Shapes are rasterised as supersampled coverage masks (drawn at SSx and box
filtered down), then painted and composited with effects at output resolution.
All element coordinates are in canvas units; ``k`` is pixels per unit (1 = 1x).
"""

from __future__ import annotations

import math
import re
import zlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import effects as fxlib
from . import geometry as geo
from . import raster
from .colors import parse_color
from . import shaping
from .fonts import FontLibrary, load_font, missing_glyphs, parse_weight

SS = 4
MAX_MASK_PIXELS = 40_000_000
TEXT_STYLE_KEYS = ("font", "weight", "size", "letter_spacing", "line_height", "uppercase", "italic")
VECTOR_TYPES = {"rect", "ellipse", "polygon", "line", "path", "arc", "ticks"}
BITMAP_TYPES = {"image", "instance"}
MAX_INSTANCE_DEPTH = 6
LINE_TYPES = {"line", "arc", "ticks"}
BLENDS = ("normal", "add", "screen", "multiply", "overlay", "soft-light", "hard-light", "color-dodge",
          "color-burn", "darken", "lighten", "difference", "erase")
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


def _stroke_specs(st: dict, colors: dict) -> list[dict]:
    """Normalise style.stroke to a list. Each stroke covers the band between distances d0 and d1 from
    the shape edge (positive = outside), so several strokes with offsets stack into double outlines."""
    spec = st.get("stroke")
    if _is_none(spec):
        return []
    many = (isinstance(spec, (list, tuple)) and len(spec) > 0
            and not all(isinstance(i, (int, float)) for i in spec))
    out = []
    for item in (spec if many else [spec]):
        if _is_none(item):
            continue
        if isinstance(item, (str, list, tuple)):
            item = {"color": item}
        if not isinstance(item, dict):
            raise ValueError("stroke must be a color, an object like {color, width, align, offset}, or a list of those")
        width = float(item.get("width", 2))
        if width <= 0:
            continue
        align = str(item.get("align", "inside")).lower()
        if align not in ("inside", "center", "outside"):
            raise ValueError("stroke.align must be 'inside', 'center' or 'outside'")
        offset = float(item.get("offset", 0) or 0)
        if align == "inside":
            d0, d1 = -(offset + width), -offset
        elif align == "outside":
            d0, d1 = offset, offset + width
        else:
            d0, d1 = offset - width / 2, offset + width / 2
        join = item.get("join")
        if join is not None and str(join).lower() not in ("miter", "round", "bevel"):
            raise ValueError("stroke.join must be 'miter', 'round' or 'bevel'")
        dash = item.get("dash")
        if dash is not None and not _is_none(dash):
            if isinstance(dash, (int, float)):
                dash = [dash, dash]
            if (not isinstance(dash, (list, tuple)) or not dash
                    or any(not isinstance(v, (int, float)) or v < 0 for v in dash) or sum(dash) <= 0):
                raise ValueError("stroke.dash must be a number or a list like [dash, gap, ...] (canvas units)")
            dash = [float(v) for v in dash]
        else:
            dash = None
        out.append({"paint": item.get("fill", item.get("color", "#ffffff")), "width": width, "align": align,
                    "offset": offset, "d0": d0, "d1": d1, "join": str(join).lower() if join else None,
                    "miter_limit": max(1.0, float(item.get("miter_limit", 4))),
                    "dash": dash, "dash_offset": float(item.get("dash_offset", 0) or 0)})
    return out


# --------------------------------------------------------------------------- painting

def _stops(spec: dict, colors: dict, space: str = "srgb") -> tuple[np.ndarray, np.ndarray]:
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
        c0, c1, c2 = fxlib.encode_space(np.array([r, g, b], dtype=np.float64), space)
        cols.append((c0 * af, c1 * af, c2 * af, a))  # premultiplied, in the interpolation space
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
    cell = max(0.5, float(spec.get("size", 48 if kind == "clouds" else 16 if kind == "hex" else 8)))
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
    if kind == "hex":
        # honeycomb outlines; ``size`` is the distance between opposite sides of a cell
        lw = float(spec.get("line_width", 1))
        px_, py_ = rx / cell, ry / cell
        r3 = 1.7320508075688772
        ax, ay = np.mod(px_, 1.0) - 0.5, np.mod(py_, r3) - r3 / 2
        bx, by = np.mod(px_ - 0.5, 1.0) - 0.5, np.mod(py_ - r3 / 2, r3) - r3 / 2
        near_a = ax * ax + ay * ay < bx * bx + by * by
        gx, gy = np.abs(np.where(near_a, ax, bx)), np.abs(np.where(near_a, ay, by))
        edge = 0.5 - np.maximum(gx, gx * 0.5 + gy * (r3 / 2))            # 0 on the cell border
        return np.clip((lw / 2 - edge * cell) * k + 0.5, 0, 1)
    if kind == "clouds":
        # soft fractal noise: smoke, energy, marble, worn metal
        seed = float(spec.get("seed", 1))
        n = fxlib.fractal_noise(rx, ry, cell, int(spec.get("octaves", 4)), seed)
        contrast = float(spec.get("contrast", 1))
        return np.clip((n - 0.5) * contrast + 0.5, 0, 1)
    raise ValueError(f"Unknown fill type '{kind}'. Use linear, radial, conic, stripes, dots, grid, hex, carbon, "
                     "brushed, clouds or image.")


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
    if kind == "image":
        return _image_fill(mask, spec, box_px, k)
    if kind in ("linear", "radial", "conic"):
        space = str(spec.get("space", "srgb")).lower()
        offs, cols = _stops(spec, colors, space)
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
        if space != "srgb":
            rgba[..., :3] = fxlib.decode_space(rgba[..., :3], space)
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


_IMAGE_CACHE: dict[tuple[str, float], Image.Image] = {}


def _image_fill(mask: Image.Image, spec: dict, box_px: Box, k: float) -> Image.Image:
    """Use a picture as paint: {"type": "image", "path": ..., "fit": "cover" | "stretch" | "tile", "scale": 1}."""
    path = Path(str(spec.get("path", ""))).expanduser()
    if not path.is_file():
        raise ValueError(f"image fill: file not found: {path}")
    key = (str(path), path.stat().st_mtime)
    if key not in _IMAGE_CACHE:
        if len(_IMAGE_CACHE) > 16:
            _IMAGE_CACHE.clear()
        _IMAGE_CACHE[key] = Image.open(path).convert("RGBA")
    src = _IMAGE_CACHE[key]
    w, h = mask.size
    x0, y0, x1, y1 = box_px
    bw, bh = max(1, int(round(x1 - x0))), max(1, int(round(y1 - y0)))
    fit = str(spec.get("fit", "cover")).lower()
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if fit == "tile":
        scale = max(0.01, float(spec.get("scale", 1))) * k
        tile = src.resize((max(1, int(round(src.width * scale))), max(1, int(round(src.height * scale)))), Image.LANCZOS)
        ox, oy = int(round(x0)) % tile.width - tile.width, int(round(y0)) % tile.height - tile.height
        for ty in range(oy, h, tile.height):
            for tx in range(ox, w, tile.width):
                out.paste(tile, (tx, ty))
    elif fit in ("cover", "stretch"):
        if fit == "stretch":
            img = src.resize((bw, bh), Image.LANCZOS)
        else:
            ratio = max(bw / src.width, bh / src.height)
            img = src.resize((max(1, int(round(src.width * ratio))), max(1, int(round(src.height * ratio)))), Image.LANCZOS)
            left, top = (img.width - bw) // 2, (img.height - bh) // 2
            img = img.crop((left, top, left + bw, top + bh))
        out.paste(img, (int(round(x0)), int(round(y0))))
    else:
        raise ValueError("image fill: fit must be 'cover', 'stretch' or 'tile'")
    opacity = max(0.0, min(1.0, float(spec.get("opacity", 1))))
    alpha = ImageChops.multiply(out.getchannel("A"), mask)
    out.putalpha(alpha if opacity >= 1 else alpha.point(lambda v: int(v * opacity + 0.5)))
    return out


_DITHER: dict[tuple[int, int], np.ndarray] = {}


def _dither(shape: tuple[int, int]) -> np.ndarray:
    """Fixed blue-ish noise in [0, 1) used to break 8-bit banding in soft gradients (deterministic)."""
    noise = _DITHER.get(shape)
    if noise is None:
        noise = np.random.default_rng(7).random(shape)
        if len(_DITHER) > 32:
            _DITHER.clear()
        _DITHER[shape] = noise
    return noise


def _soft_tint(cov: np.ndarray, color: tuple[int, int, int, int], strength: float) -> Image.Image:
    """Float coverage (0-255) -> colour layer whose alpha is dithered, so wide soft shadows and glows
    fade smoothly instead of in visible 8-bit steps."""
    r, g, b, a = color
    alpha = cov * (a / 255.0 * strength)
    alpha = np.floor(np.clip(alpha, 0, 255) + _dither(alpha.shape))
    img = Image.new("RGBA", (cov.shape[1], cov.shape[0]), (r, g, b, 0))
    img.putalpha(Image.fromarray(np.clip(alpha, 0, 255).astype(np.uint8), "L"))
    return img


def _outer_effects(layer: Image.Image, sil: Image.Image, fx: "Effects", k: float) -> Image.Image:
    """Shadows and glows cast by the silhouette, composited onto ``layer`` (which is still empty)."""
    for e in fx.outer:
        m = sil
        spread = e["spread"] * k
        if spread > 0:
            m = Image.fromarray(fxlib.dilate(np.asarray(m), spread), "L")
        m = _shift(m, int(round(e["dx"] * k)), int(round(e["dy"] * k)))
        cov = np.asarray(m, dtype=np.float64)
        if e["blur"] > 0:
            cov = fxlib.blur_float(cov, e["blur"] * k)
        if e["knockout"]:
            cov = cov * (1.0 - np.asarray(sil, dtype=np.float64) / 255.0)
        layer = Image.alpha_composite(layer, _soft_tint(cov, e["color"], e["strength"]))
    return layer


def _inner_effects(layer: Image.Image, shape: Image.Image, fx: "Effects", k: float) -> Image.Image:
    """Inner shadows / glows / simple bevel, limited to the shape."""
    shape_f = np.asarray(shape, dtype=np.float64) / 255.0
    for e in fx.inner:
        m = ImageChops.invert(shape)
        m = _shift(m, int(round(e["dx"] * k)), int(round(e["dy"] * k)), fill=255)
        cov = np.asarray(m, dtype=np.float64)
        if e["blur"] > 0:
            # pad with "outside" so the canvas edge does not read as an edge of the shape
            pad = int(math.ceil(e["blur"] * k * 1.6)) + 1
            cov = fxlib.blur_float(np.pad(cov, pad, constant_values=255.0), e["blur"] * k)[pad:-pad, pad:-pad]
        layer = Image.alpha_composite(layer, _soft_tint(cov * shape_f, e["color"], e.get("strength", 1.0)))
    return layer


def _soft_mask(size: tuple[int, int], spec: Any, box_px: Box, colors: dict, k: float) -> Image.Image:
    """Alpha of a paint spec over the whole region: the fade mask for style.mask."""
    painted = paint(Image.new("L", size, 255), spec, box_px, colors, k)
    if painted is None:
        return Image.new("L", size, 255)
    return painted.getchannel("A")


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
    if blend == "erase":
        # destination-out: the element's alpha cuts a hole in what is already on this layer
        b = np.asarray(base, dtype=np.float64).copy()
        b[..., 3] *= 1.0 - np.asarray(part, dtype=np.float64)[..., 3] / 255.0
        merged = Image.fromarray((b + 0.5).astype(np.uint8), "RGBA")
    elif blend == "add":
        b = np.asarray(base, dtype=np.float64)
        s = np.asarray(part, dtype=np.float64)
        sa = s[..., 3:4] / 255.0
        ba = b[..., 3:4] / 255.0
        rgb = np.clip(b[..., :3] * ba + s[..., :3] * sa, 0, 255)
        alpha = np.clip(b[..., 3:4] + s[..., 3:4] * (1 - ba), 0, 255)
        safe = np.where(alpha > 0, alpha / 255.0, 1.0)
        out = np.concatenate([np.clip(rgb / safe, 0, 255), alpha], axis=-1)
        merged = Image.fromarray((out + 0.5).astype(np.uint8), "RGBA")
    elif blend != "normal":
        b = np.asarray(base, dtype=np.float64) / 255.0
        s = np.asarray(part, dtype=np.float64) / 255.0
        bc, sc, ba, sa = b[..., :3], s[..., :3], b[..., 3:4], s[..., 3:4]
        if blend == "multiply":
            mixed = bc * sc
        elif blend == "screen":
            mixed = 1 - (1 - bc) * (1 - sc)
        elif blend == "overlay":
            mixed = np.where(bc <= 0.5, 2 * bc * sc, 1 - 2 * (1 - bc) * (1 - sc))
        elif blend == "hard-light":
            mixed = np.where(sc <= 0.5, 2 * bc * sc, 1 - 2 * (1 - bc) * (1 - sc))
        elif blend == "soft-light":
            d = np.where(bc <= 0.25, ((16 * bc - 12) * bc + 4) * bc, np.sqrt(bc))
            mixed = np.where(sc <= 0.5, bc - (1 - 2 * sc) * bc * (1 - bc), bc + (2 * sc - 1) * (d - bc))
        elif blend == "color-dodge":
            mixed = np.where(sc >= 1, 1.0, np.minimum(1.0, bc / np.maximum(1e-6, 1 - sc)))
        elif blend == "color-burn":
            mixed = np.where(sc <= 0, 0.0, 1 - np.minimum(1.0, (1 - bc) / np.maximum(1e-6, sc)))
        elif blend == "darken":
            mixed = np.minimum(bc, sc)
        elif blend == "lighten":
            mixed = np.maximum(bc, sc)
        elif blend == "difference":
            mixed = np.abs(bc - sc)
        else:
            raise ValueError(f"blend must be one of {', '.join(BLENDS)}")
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

    def __init__(self, el: dict, st: dict, fonts: FontLibrary, ppu: float, text: str | None = None,
                 lang: str | None = None):
        text = str(el.get("text", "")) if text is None else text
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
        self.lang, lang_warn = shaping.guess_lang(text, lang or el.get("lang"))
        self.warnings = [f"{el.get('id')}: {lang_warn}"] if lang_warn else []
        self._fonts = fonts
        self._shaped = bool(self.path) and shaping.needs_shaping(text, shaping._face(self.path)[1].__contains__)

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
        top, bottom = self.cap, self.descent
        if self.shaper:          # fallback fonts (Thai, Devanagari...) reach higher and lower than the primary
            used = [self.shaper.chain[i] for i in self.shaper.used]
            top = max([top] + [f.ascent for f in used])
            bottom = max([bottom] + [f.descent for f in used])
        self.box_px = (bx, self.baselines[0] - top, bx + self.block_w, self.baselines[-1] + bottom)
        self.arc = None
        if el.get("arc"):
            if self.shaper:
                raise ValueError("text on an arc supports Latin, Greek and Cyrillic only; this text needs "
                                 "shaping or fallback fonts (draw it straight, or bake it as an image)")
            self._layout_arc(el["arc"])

    def _layout_arc(self, spec: Any) -> None:
        """Place each glyph upright along a circle. spec: {cx, cy, radius, angle, side}. ``radius`` is
        the baseline radius; ``angle`` (degrees, 0 = right, 90 = down) is where the anchor point of the
        text sits; side 'outer' reads clockwise over the top, 'inner' reads anticlockwise under the
        bottom, like the lower text on a badge."""
        if not isinstance(spec, dict):
            raise ValueError("arc must be an object like {cx, cy, radius, angle, side}")
        if len(self.lines) != 1:
            raise ValueError("text on an arc must be a single line (no newlines, no max_width wrapping)")
        try:
            cx, cy, radius = float(spec["cx"]), float(spec["cy"]), float(spec["radius"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("arc needs numeric cx, cy and radius") from exc
        if radius <= 0:
            raise ValueError("arc radius must be > 0")
        side = str(spec.get("side", "outer")).lower()
        if side not in ("outer", "inner"):
            raise ValueError("arc side must be 'outer' or 'inner'")
        angle = float(spec.get("angle", -90 if side == "outer" else 90))
        ppu = self.ppu
        R = radius * ppu
        text = self.lines[0][0]
        advances = []
        for ch in text:
            advances.append(float(self.font.getlength(ch)))
        total = sum(advances) + self.spacing * ppu * max(0, len(text) - 1)
        if total > 2 * math.pi * R * 0.98:
            raise ValueError(f"text is longer than the circle ({total / ppu:.0f} vs {2 * math.pi * radius:.0f} units); "
                             "use a bigger radius, smaller size or less letter_spacing")
        if self.h == "left":
            s0 = 0.0
        elif self.h == "center":
            s0 = -total / 2
        else:
            s0 = -total
        sign = 1.0 if side == "outer" else -1.0
        glyphs = []
        pos = s0
        pts = []
        # vertical anchor moves the baseline radius so top/middle/bottom refer to the glyph band
        if self.v == "top":
            shift = -self.cap
        elif self.v == "middle":
            shift = -self.cap / 2
        elif self.v == "bottom":
            shift = self.descent
        else:
            shift = 0.0
        Rb = R + shift if side == "outer" else R - shift          # glyph "up" is outward / inward
        for ch, adv in zip(text, advances):
            mid = pos + adv / 2
            theta = math.radians(angle) + sign * mid / max(1e-6, Rb)
            gx, gy = cx * ppu + Rb * math.cos(theta), cy * ppu + Rb * math.sin(theta)
            rot = math.degrees(theta) + (90.0 if side == "outer" else -90.0)
            glyphs.append((ch, gx, gy, rot, adv))
            # glyph box corners (baseline-centred, up = -y before rotation)
            ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            for lx, ly in ((-adv / 2, -self.cap * 1.25), (adv / 2, -self.cap * 1.25),
                           (-adv / 2, self.descent), (adv / 2, self.descent)):
                pts.append((gx + lx * ca - ly * sa, gy + lx * sa + ly * ca))
            pos += adv + self.spacing * ppu
        self.arc = glyphs
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        self.box_px = (min(xs), min(ys), max(xs), max(ys))

    def draw_arc(self, img: Image.Image, ox: float, oy: float, stroke_px: int = 0) -> None:
        """Draw arc-placed glyph coverage onto ``img`` (L mode) at offset (-ox, -oy)."""
        size_px = self.size * self.ppu
        tile = int(math.ceil(size_px * 2.4 + 4 * stroke_px)) + 4
        lean = math.tan(math.radians(self.skew)) if self.skew else 0.0
        for ch, gx, gy, rot, _ in self.arc:
            if ch.isspace():
                continue
            t = Image.new("L", (tile, tile), 0)
            d = ImageDraw.Draw(t)
            kwargs: dict = {"font": self.font, "fill": 255, "anchor": "ms"}
            if stroke_px > 0:
                kwargs.update(stroke_width=stroke_px, stroke_fill=255)
            d.text((tile / 2, tile / 2), ch, **kwargs)
            if lean:
                t = t.transform(t.size, Image.AFFINE, (1, lean, -lean * tile / 2, 0, 1, 0), resample=Image.BICUBIC)
            if rot:
                t = t.rotate(-rot, resample=Image.BICUBIC, center=(tile / 2, tile / 2))
            x0 = int(round(gx - ox - tile / 2))
            y0 = int(round(gy - oy - tile / 2))
            box = (x0, y0, x0 + tile, y0 + tile)
            under = img.crop(box)
            img.paste(ImageChops.lighter(under, t), box)

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
        self.shaper = None
        if self._shaped:
            chain = shaping.fallback_chain(self._fonts, self.path, self.var_weight, self.weight,
                                           self.size * self.ppu, self.lang)
            self.shaper = shaping.ShapedText(chain, self.lang, self.spacing * self.ppu)

    def fallback_note(self) -> str | None:
        """Which characters the chosen font lacked and which fallback font drew them."""
        if not self.shaper or not self.shaper.fallback:
            return None
        parts = [f"{''.join(sorted(chars))!r} with {Path(self.shaper.chain[i].path).stem}"
                 for i, chars in sorted(self.shaper.fallback.items())]
        return (f"font '{self.family or 'Inter'}' lacks some characters, drawn with fallback fonts: "
                f"{'; '.join(parts)}. The engine font needs these glyphs too.")

    def missing(self) -> list[str]:
        """Characters no font in the fallback chain can draw."""
        if self.shaper:
            for line, _ in self.lines:
                self.shaper.shape(line)
            return sorted(self.shaper.missing)
        return missing_glyphs(self.font, self.text)

    def _width(self, s: str) -> float:
        if not s:
            return 0.0
        if self.shaper:
            return self.shaper.width(s)
        return float(self.font.getlength(s)) + self.spacing * self.ppu * max(0, len(s) - 1)

    def _wrap(self, text: str, max_px: float | None) -> list[tuple[str, float]]:
        lines: list[tuple[str, float]] = []
        for para in text.split("\n"):
            if max_px is None:
                lines.append((para, self._width(para)))
                continue
            if self.shaper:
                lines += [(ln, self._width(ln)) for ln in self.shaper.wrap(para, max_px)]
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

    def draw(self, img: Image.Image, ox: float, oy: float, stroke_px: int = 0) -> None:
        """Draw glyph coverage (optionally dilated by stroke_px) onto ``img`` (L mode) at offset (-ox, -oy)."""
        if self.shaper:
            for (line, _), lx, by in zip(self.lines, self.line_x, self.baselines):
                if line:
                    self.shaper.draw(img, line, lx - ox, by - oy, stroke_px)
            return
        draw = ImageDraw.Draw(img)
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
    elif t == "path":
        scale = float(el.get("scale", 1))
        dx, dy = float(el.get("x", 0)), float(el.get("y", 0))
        subs = [(geo.transform(pts, scale, dx, dy), closed) for pts, closed in geo.parse_svg_path(el["d"], res * scale)]
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
    def __init__(self, canvas: dict, theme: dict, fonts: FontLibrary, scale: float = 1.0,
                 lookup=None, depth: int = 0, state: str | None = None, language: str | None = None,
                 strings: dict | None = None, extra_vars: dict | None = None, extra_colors: dict | None = None):
        if state:
            canvas = apply_state(canvas, state)
        self.canvas = canvas
        self.lookup = lookup            # canvas id -> canvas dict, for instance elements
        self.depth = depth
        self.theme = theme
        self.state = state
        self.language = language
        self.strings = strings or {}
        self.colors = {**theme.get("colors", {}), **(canvas.get("colors") or {}), **(extra_colors or {})}
        self.text_vars = text_vars(canvas, language, self.strings, extra_vars)
        self.fonts = fonts
        self.k = float(scale)
        self.width = max(1, int(round(canvas["width"] * self.k)))
        self.height = max(1, int(round(canvas["height"] * self.k)))
        self.warnings: list[str] = []
        self._by_id = {e["id"]: e for e in canvas["elements"]}

    # ---- public

    def render(self, include_background: bool = True, only: set[str] | None = None,
               only_layers: set[str] | None = None) -> Image.Image:
        pixel = self.canvas.get("pixel_art")
        if pixel and not getattr(self, "_pixel_pass", False):
            # pixel art: draw once at 1x without anti-aliasing, snap alpha and colours, then scale by
            # whole pixels (nearest neighbour) so every art pixel stays a crisp square
            base = self.derive(1.0)
            base._pixel_pass = True
            img = base.render(include_background, only, only_layers)
            self.warnings += base.warnings
            img = pixelate(img, pixel if isinstance(pixel, dict) else {}, self.colors)
            if (self.width, self.height) != img.size:
                img = img.resize((self.width, self.height), Image.NEAREST)
            return img
        img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        bg = self.canvas.get("background")
        if include_background and not _is_none(bg):
            full = Image.new("L", img.size, 255)
            layer = paint(full, bg, (0, 0, self.width, self.height), self.colors, self.k)
            if layer is not None:
                img = Image.alpha_composite(img, layer)
        return self.render_stack(img, only, only_layers)

    def element_box(self, el: dict) -> Box:
        """Bounds of the element geometry in canvas units (no effects), after rotation."""
        st = resolve_style(el.get("style"), self.theme)
        box = self._geom_box(el, st)
        pivot = self._pivot(el, box)
        sx, sy = _scales(el)
        if sx != 1 or sy != 1:
            xs = sorted((pivot[0] + (box[0] - pivot[0]) * sx, pivot[0] + (box[2] - pivot[0]) * sx))
            ys = sorted((pivot[1] + (box[1] - pivot[1]) * sy, pivot[1] + (box[3] - pivot[1]) * sy))
            box = (xs[0], ys[0], xs[1], ys[1])
        rot = float(el.get("rotation", 0) or 0)
        if (sx < 0) != (sy < 0):
            rot = -rot
        return geo.rotated_bbox(box, rot, pivot)

    def derive(self, scale: float) -> "Renderer":
        """Same canvas, context and colours at another resolution."""
        r = Renderer.__new__(Renderer)
        r.__dict__.update(self.__dict__)
        r.k = float(scale)
        r.width = max(1, int(round(self.canvas["width"] * r.k)))
        r.height = max(1, int(round(self.canvas["height"] * r.k)))
        r.warnings = []
        return r

    def text_of(self, el: dict) -> str:
        """The text an element shows: {placeholders} filled from vars / string tables, then the
        pseudo-localisation transform when language is "pseudo"."""
        raw = str(el.get("text", ""))
        missing: list[str] = []

        def sub(m) -> str:
            key = m.group(1)
            if key in self.text_vars:
                return str(self.text_vars[key])
            missing.append(key)
            return m.group(0)

        text = _PLACEHOLDER.sub(sub, raw)
        if missing:
            msg = f"{el.get('id')}: no value for {', '.join('{' + k + '}' for k in missing)}" + (
                f" in language '{self.language}'" if self.language else "")
            if msg not in self.warnings:
                self.warnings.append(msg)
        if self.language in PSEUDO_LANGS:
            text = pseudo_localize(text, self.language)
        return text

    def text_lang(self, el: dict) -> str | None:
        """Language tag for shaping: the element's own, else the render language, else the canvas'."""
        lang = self.language if self.language and not str(self.language).startswith("pseudo") else None
        return el.get("lang") or lang or self.canvas.get("language")

    def glyph_problems(self) -> dict[str, str]:
        """{element id: characters no font can draw} for every text element (exports refuse these)."""
        out = {}
        for el in self.canvas.get("elements", []):
            if el.get("type") != "text" or el.get("visible") is False:
                continue
            st = resolve_style(el.get("style"), self.theme)
            miss = TextLayout(el, st, self.fonts, 1.0, self.text_of(el), self.text_lang(el)).missing()
            if miss:
                out[el["id"]] = "".join(miss)
        return out

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
            layout = TextLayout(el, st, self.fonts, 4.0, self.text_of(el), self.text_lang(el))
            box = layout.box_units()
            lean = abs(math.tan(math.radians(layout.skew))) * (box[3] - box[1]) / 2
            return (box[0] - lean, box[1], box[2] + lean, box[3])
        if t in BITMAP_TYPES:
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            w, h = self._image_size(el)
            return (x, y, x + w, y + h)
        raise ValueError(f"Unknown element type '{t}'")

    def _source_canvas(self, el: dict) -> dict:
        """The canvas an instance element shows (posed at its animation time, if any)."""
        src_id = str(el.get("source", ""))
        if not src_id:
            raise ValueError("instance needs 'source' (the id of another canvas)")
        if src_id == self.canvas.get("id"):
            raise ValueError("an instance cannot show the canvas it is placed on")
        if self.lookup is None:
            raise ValueError("instances are not available here")
        src = self.lookup(src_id)
        if self.depth == 0:
            loop = _instance_cycle(src, self.canvas.get("id"), self.lookup)
            if loop:
                raise ValueError("instance loop: " + " -> ".join([str(self.canvas.get("id"))] + loop)
                                 + " (a canvas cannot end up containing itself)")
        if self.depth >= MAX_INSTANCE_DEPTH:
            raise ValueError(f"instances nest deeper than {MAX_INSTANCE_DEPTH} levels")
        if el.get("animation"):
            from .animation import apply_animation           # local import: animation imports render helpers
            anims = src.get("animations") or {}
            name = str(el["animation"])
            if name not in anims:
                known = ", ".join(sorted(anims)) or "(none)"
                raise ValueError(f"source canvas '{src_id}' has no animation '{name}'. Animations: {known}")
            anim = anims[name]
            t = float(el.get("time", 0) or 0)
            dur = float(anim.get("duration", 1) or 1)
            if anim.get("loop", True) and dur > 0:
                t = t % dur
            src, _ = apply_animation(src, anim, t, self.colors)
        return src

    def _image_size(self, el: dict) -> tuple[float, float]:
        w, h = el.get("width"), el.get("height")
        if w and h:
            return float(w), float(h)
        if el["type"] == "instance":
            src = self._source_canvas(el)
            iw, ih = float(src["width"]), float(src["height"])
        else:
            iw, ih = self._load_image(el).size
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

    def _pick_ss(self, w: int, h: int) -> int:
        if self.canvas.get("pixel_art"):
            return 1                       # hard pixel edges: sample each pixel centre once
        ss = SS
        while ss > 1 and w * h * ss * ss > MAX_MASK_PIXELS:
            ss -= 1
        return ss

    def _vector_masks(self, el: dict, st: dict, region, ss: int, strokes: list[dict]):
        """Return (shape_mask, [stroke_mask per stroke]) at region size."""
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

        rule = str(el.get("fill_rule", "evenodd")).lower() if el["type"] == "path" else "evenodd"
        if rule not in ("evenodd", "nonzero"):
            raise ValueError("fill_rule must be 'evenodd' or 'nonzero'")

        def fill(polys, how: str | None = None) -> np.ndarray:
            return raster.fill_polygons([px(p) for p in polys], bw, bh, how or rule)

        def lines(items) -> np.ndarray:
            return raster.stroke_polylines(
                [(px(pts), closed, width * f / 2.0, cap == "round") for pts, closed, width, cap in items], bw, bh)

        shape = fill(g.fills)
        if g.lines:
            shape = shape | lines(g.lines)

        bands: list[np.ndarray] = []
        if strokes and el["type"] not in LINE_TYPES:
            closed_paths = [pts for pts, c in g.outline if c]
            open_paths = [pts for pts, c in g.outline if not c]
            inside = fill(closed_paths) if closed_paths else None

            def grown(d: float, join: str = "round", limit: float = 4.0) -> np.ndarray:
                """The shape grown (d > 0) or shrunk (d < 0) by distance d."""
                if g.kind == "rect":
                    poly = _inset_rect(g.rect, d, f) if d else g.fills[0]
                    return fill([poly]) if poly else np.zeros((bh, bw), dtype=np.uint8)
                if g.kind == "ellipse":
                    cx, cy, rx, ry = g.ellipse
                    if rx + d <= 0 or ry + d <= 0:
                        return np.zeros((bh, bw), dtype=np.uint8)
                    return fill([geo.ellipse_path(cx, cy, rx + d, ry + d, f)])
                if inside is None:
                    return np.zeros((bh, bw), dtype=np.uint8)
                if d == 0:
                    return inside
                rim = lines([(pts, True, abs(d) * 2, "round") for pts in closed_paths])
                if join == "round":
                    return (inside | rim) if d > 0 else (inside & ~rim)
                # sharp corners: the exact round offset plus the corner wedges of the miter offset
                sharp = fill(geo.offset_shape(closed_paths, d, join, limit), "evenodd")
                if d > 0:
                    return inside | rim | sharp
                return inside & (~rim | sharp)

            for spec in strokes:
                if g.kind == "poly" and not g.fills and not g.outline:
                    bands.append(np.zeros((bh, bw), dtype=np.uint8))       # collapsed shape
                    continue
                join = spec["join"] or ("miter" if g.kind == "poly" else "round")
                band = (grown(spec["d1"], join, spec["miter_limit"])
                        & ~grown(spec["d0"], join, spec["miter_limit"]))
                if open_paths:
                    band = band | lines([(pts, False, spec["width"], "round") for pts in open_paths])
                if spec["dash"]:
                    # dashes run along the shape edge; each dash is a wide butt-capped strip across the band
                    edge = [(pts, c) for pts, c in g.outline] if g.outline else [(pts, True) for pts in g.fills]
                    reach = 4 * max(abs(spec["d0"]), abs(spec["d1"])) + 2.0 / f      # wide enough for corners
                    pieces = []
                    for pts, c in edge:
                        pieces += geo.dash_polyline(pts, c, spec["dash"], spec["dash_offset"])
                    band = band & lines([(pc, False, reach, "butt") for pc in pieces]) if pieces else band * 0
                bands.append(band)

        shape_img = Image.fromarray(shape, "L")
        stroke_imgs = [Image.fromarray(b, "L") for b in bands]
        rot = float(el.get("rotation", 0) or 0)
        if rot:
            pvx, pvy = self._pivot(el, g.box)
            cx = (pvx * k - px0) * ss
            cy = (pvy * k - py0) * ss
            shape_img = shape_img.rotate(-rot, resample=Image.BICUBIC, center=(cx, cy))
            stroke_imgs = [m.rotate(-rot, resample=Image.BICUBIC, center=(cx, cy)) for m in stroke_imgs]
        if ss > 1:
            shape_img = shape_img.reduce(ss)
            stroke_imgs = [m.reduce(ss) for m in stroke_imgs]
        return shape_img, stroke_imgs

    def _text_masks(self, el: dict, st: dict, region, ss: int, strokes: list[dict]):
        px0, py0, w, h = region
        k = self.k
        layout = TextLayout(el, st, self.fonts, k * ss, self.text_of(el), self.text_lang(el))
        self.warnings += [w for w in layout.warnings if w not in self.warnings]
        miss = layout.missing()
        if miss:
            self.warnings.append(
                f"{el['id']}: font '{layout.family or 'Inter'}' has no glyph for {''.join(miss)!r}; pick another font.")
        note = layout.fallback_note()
        if note and f"{el['id']}: {note}" not in self.warnings:
            self.warnings.append(f"{el['id']}: {note}")
        big = (w * ss, h * ss)

        def glyphs(grow_units: float) -> Image.Image:
            img = Image.new("L", big, 0)
            grow_px = int(round(grow_units * k * ss)) if grow_units > 0 else 0
            if layout.arc is not None:
                layout.draw_arc(img, px0 * ss, py0 * ss, grow_px)
            else:
                layout.draw(img, px0 * ss, py0 * ss, grow_px)
            return img

        shape = glyphs(0)
        masks = []
        for spec in strokes:
            # text outlines always sit outside the glyphs; an offset leaves a gap before the outline starts
            inner = max(0.0, spec["offset"])
            band = glyphs(inner + spec["width"])
            if inner > 0:
                band = ImageChops.subtract(band, glyphs(inner))
            masks.append(band)
        bx0, by0, bx1, by1 = layout.box_px
        if layout.skew and layout.arc is None:
            lean = math.tan(math.radians(layout.skew))
            mid = (by0 + by1) / 2 - py0 * ss
            matrix = (1, lean, -lean * mid, 0, 1, 0)       # output (x, y) samples input (x + lean * (y - mid), y)
            shape = shape.transform(big, Image.AFFINE, matrix, resample=Image.BICUBIC)
            masks = [m.transform(big, Image.AFFINE, matrix, resample=Image.BICUBIC) for m in masks]
        rot = float(el.get("rotation", 0) or 0)
        if rot:
            if el.get("origin"):
                pvx, pvy = self._pivot(el, (0, 0, 0, 0))
                cx, cy = (pvx * k - px0) * ss, (pvy * k - py0) * ss
            else:
                cx, cy = (bx0 + bx1) / 2 - px0 * ss, (by0 + by1) / 2 - py0 * ss
            shape = shape.rotate(-rot, resample=Image.BICUBIC, center=(cx, cy))
            masks = [m.rotate(-rot, resample=Image.BICUBIC, center=(cx, cy)) for m in masks]
        if ss > 1:
            shape = shape.reduce(ss)
            masks = [m.reduce(ss) for m in masks]
        return shape, masks

    def _instance_image(self, el: dict, tw: int, th: int) -> Image.Image:
        src = self._source_canvas(el)
        fit = str(el.get("fit", "stretch")).lower()
        sx, sy = tw / float(src["width"]), th / float(src["height"])
        scale = max(sx, sy) if fit in ("stretch", "cover") else min(sx, sy)
        sub = Renderer(src, self.theme, self.fonts, scale, self.lookup, self.depth + 1, state=el.get("state"),
                       language=self.language, strings=self.strings,
                       extra_vars={**{k: v for k, v in self.text_vars.items() if k in (el.get("pass_vars") or [])},
                                   **(el.get("vars") or {})},
                       extra_colors=el.get("colors"))
        img = sub.render(include_background=bool(el.get("background", True)))
        self.warnings += [f"{el['id']} -> {w}" for w in sub.warnings]
        return img

    def _image_layer(self, el: dict, region):
        """Return (alpha_mask, rgba_layer) for an image or instance element at region size."""
        px0, py0, w, h = region
        k = self.k
        uw, uh = self._image_size(el)
        tw, th = max(1, int(round(uw * k))), max(1, int(round(uh * k)))
        src = self._instance_image(el, tw, th) if el["type"] == "instance" else self._load_image(el)
        fit = str(el.get("fit", "stretch")).lower()
        if fit == "stretch":
            img = src if src.size == (tw, th) else src.resize((tw, th), Image.LANCZOS)
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
            return self._vector_masks(el, st, region, ss, [])[0]
        if t == "text":
            return self._text_masks(el, st, region, ss, [])[0]
        return self._image_layer(el, region)[0]

    def render_element(self, el: dict):
        """Render one element to (layer, x, y, blend). Handles scale_x / scale_y, which grow or shrink
        the finished element (effects included) around its pivot, the way an engine scales a widget."""
        sx, sy = _scales(el)
        if sx == 1 and sy == 1:
            return self._render_plain(el)
        if sx == 0 or sy == 0:
            return None
        ax, ay = abs(sx), abs(sy)
        if max(ax, ay) > 16:
            raise ValueError("scale is limited to 16")
        fx_, fy_ = sx < 0, sy < 0
        k = self.k
        m = max(ax, ay)
        drop = ("scale_x", "scale_y", "flip_x", "flip_y", "clip") + (() if el["type"] == "path" else ("scale",))
        plain = {key: value for key, value in el.items() if key not in drop}
        if fx_ or fy_:
            # mirror the shape, not the lighting: pre-mirror offsets and light so they come out unchanged
            plain["style"] = _mirror_style(resolve_style(el.get("style"), self.theme), fx_, fy_)
            if fx_ != fy_ and plain.get("rotation"):
                plain["rotation"] = -float(plain["rotation"])
        if m == 1:
            sub = self
            result = self._render_plain(plain, clip_region=False)
        else:
            sub = self.derive(k * m)                                     # true vector scale
            result = sub._render_plain(plain, clip_region=False)
            self.warnings += sub.warnings
        if result is None:
            return None
        layer, px0, py0, blend = result
        if ax != ay:
            layer = layer.resize((max(1, int(round(layer.width * ax / m))), max(1, int(round(layer.height * ay / m)))),
                                 Image.LANCZOS)
        st = resolve_style(el.get("style"), self.theme)
        pvx, pvy = self._pivot(el, self._geom_box(plain, st))
        lx = (pvx * k * m - px0) * ax / m           # pivot inside the (resized) layer
        ly = (pvy * k * m - py0) * ay / m
        if fx_:
            layer = layer.transpose(Image.FLIP_LEFT_RIGHT)
            lx = layer.width - lx
        if fy_:
            layer = layer.transpose(Image.FLIP_TOP_BOTTOM)
            ly = layer.height - ly
        ox = int(round(pvx * k - lx))
        oy = int(round(pvy * k - ly))
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
        strokes = _stroke_specs(st, self.colors)
        box = self._geom_box(el, st)
        if t == "text":
            grow = max((sp["offset"] + sp["width"] for sp in strokes), default=0.0)
        else:
            grow = 0.0
            for sp in strokes:
                reach = max(0.0, sp["d1"])
                if t in ("polygon", "path") and (sp["join"] or "miter") == "miter":
                    reach *= sp["miter_limit"]            # sharp corners can poke out up to the miter limit
                grow = max(grow, reach)
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
            shape, smasks = self._vector_masks(el, st, region, ss, strokes)
        elif t == "text":
            shape, smasks = self._text_masks(el, st, region, ss, strokes)
        elif t in BITMAP_TYPES:
            shape, color_layer = self._image_layer(el, region)
            smasks = []
        else:
            raise ValueError(f"Unknown element type '{t}'")
        drawn = list(zip(strokes, smasks))        # line-like elements ignore style.stroke, so this may be empty

        fill_spec = st.get("fill", "#ffffff" if (not strokes or t in LINE_TYPES or t == "text") else None)
        has_fill = color_layer is not None or not _is_none(fill_spec)
        if not has_fill and not drawn:
            return None
        sil = shape if has_fill else Image.new("L", (w, h), 0)
        for _, m in drawn:
            sil = ImageChops.lighter(sil, m)

        paint_ref = vector_geometry(el, 1.0).paint_box if t in ("arc", "ticks") else None
        rbox_plain = geo.rotated_bbox(paint_ref or box, rot, pivot)
        box_px = (rbox_plain[0] * k - px0, rbox_plain[1] * k - py0, rbox_plain[2] * k - px0, rbox_plain[3] * k - py0)
        layer = _outer_effects(Image.new("RGBA", (w, h), (0, 0, 0, 0)), sil, fx, k)

        def draw_strokes(target: Image.Image, items) -> Image.Image:
            for spec, m in items:
                img = paint(m, spec["paint"], box_px, self.colors, k)
                if img is not None:
                    target = Image.alpha_composite(target, img)
            return target

        if t == "text":
            layer = draw_strokes(layer, reversed(drawn))      # outlines sit behind the glyphs, widest at the back
            drawn = []

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
            if st.get("light"):
                layer = Image.alpha_composite(layer, fxlib.emboss(shape, st["light"], self.colors, k))
            layer = _inner_effects(layer, shape, fx, k)

        layer = draw_strokes(layer, drawn)

        if st.get("adjust"):
            layer = fxlib.adjust(layer, st["adjust"], self.colors)
        if soften > 0:
            # blur in premultiplied space so transparent pixels do not bleed dark fringes
            layer = layer.convert("RGBa").filter(ImageFilter.GaussianBlur(soften * k / 2)).convert("RGBA")
        if not _is_none(st.get("mask")):
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), _soft_mask((w, h), st["mask"], box_px, self.colors, k)))

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

    # ---- layers

    def layer_props(self, layer: dict) -> dict:
        """Validated compositing settings of a layer."""
        blend = str(layer.get("blend", "normal")).lower()
        if blend not in BLENDS:
            raise ValueError(f"layer blend must be one of {', '.join(BLENDS)}")
        return {"name": layer["name"], "opacity": max(0.0, min(1.0, float(layer.get("opacity", 1)))), "blend": blend,
                "clip": bool(layer.get("clip", False)), "visible": bool(layer.get("visible", True)),
                "style": resolve_style(layer.get("style"), self.theme)}

    def stack(self, only: set[str] | None = None) -> list[tuple[dict | None, list[dict]]]:
        """Layers bottom to top as (layer | None, elements). None is the implicit base layer that holds
        every element not assigned to a layer."""
        layers = self.canvas.get("layers") or []
        names = {layer["name"] for layer in layers}

        def keep(el: dict) -> bool:
            return el.get("visible", True) and (only is None or el["id"] in only)

        out: list[tuple[dict | None, list[dict]]] = []
        base = [e for e in self.canvas["elements"] if e.get("layer") not in names and keep(e)]
        if base:
            out.append((None, base))
        for layer in layers:
            out.append((layer, [e for e in self.canvas["elements"] if e.get("layer") == layer["name"] and keep(e)]))
        return out

    def _draw_elements(self, img: Image.Image, elements: list[dict], margin: int = 0) -> None:
        for el in elements:
            try:
                result = self.render_element(el)
            except ValueError as exc:
                if str(exc).startswith("Element '"):
                    raise
                raise ValueError(f"Element '{el['id']}' ({el['type']}): {exc}") from exc
            if result is None:
                continue
            layer, ox, oy, blend = result
            composite(img, layer, ox + margin, oy + margin, blend)

    def _style_layer(self, content: Image.Image, st: dict, box_px: Box) -> Image.Image:
        """Apply layer-level effects to the merged content of a layer, using its alpha as the silhouette."""
        k = self.k
        alpha = content.getchannel("A")
        fx = Effects(st, self.colors)
        strokes = _stroke_specs(st, self.colors)
        a_np = np.asarray(alpha)
        bands = []
        for spec in strokes:
            outer = fxlib.grow(a_np, spec["d1"] * k)
            inner = fxlib.grow(a_np, spec["d0"] * k)
            bands.append(Image.fromarray(np.clip(outer.astype(np.int16) - inner, 0, 255).astype(np.uint8), "L"))
        sil = alpha
        for m in bands:
            sil = ImageChops.lighter(sil, m)
        out = _outer_effects(Image.new("RGBA", content.size, (0, 0, 0, 0)), sil, fx, k)
        body = fxlib.adjust(content, st["adjust"], self.colors) if st.get("adjust") else content
        out = Image.alpha_composite(out, body)
        if st.get("light"):
            out = Image.alpha_composite(out, fxlib.emboss(alpha, st["light"], self.colors, k))
        out = _inner_effects(out, alpha, fx, k)
        for spec, m in zip(strokes, bands):
            img = paint(m, spec["paint"], box_px, self.colors, k)
            if img is not None:
                out = Image.alpha_composite(out, img)
        soften = max(0.0, float(st.get("blur", 0) or 0))
        if soften > 0:
            out = out.convert("RGBa").filter(ImageFilter.GaussianBlur(soften * k / 2)).convert("RGBA")
        if not _is_none(st.get("mask")):
            out.putalpha(ImageChops.multiply(out.getchannel("A"), _soft_mask(out.size, st["mask"], box_px, self.colors, k)))
        return out

    def layer_image(self, layer: dict | None, elements: list[dict], clip_alpha: Image.Image | None = None,
                    bake_opacity: bool = True) -> tuple[Image.Image, Image.Image]:
        """Render one layer on its own. Returns (finished layer, alpha of its raw content); the second
        is what a layer above clips to."""
        props = self.layer_props(layer) if layer else {"opacity": 1.0, "style": {}}
        st = props["style"]
        margin = 0
        if st:
            fx = Effects(st, self.colors)
            reach = max(0.0, max((sp["d1"] for sp in _stroke_specs(st, self.colors)), default=0.0))
            margin = int(math.ceil((fx.pad() + float(st.get("blur", 0) or 0) * 1.5 + reach + 2) * self.k))
        size = (self.width + 2 * margin, self.height + 2 * margin)
        content = Image.new("RGBA", size, (0, 0, 0, 0))
        self._draw_elements(content, elements, margin)
        frame = (margin, margin, margin + self.width, margin + self.height)
        content_alpha = content.getchannel("A").crop(frame)
        final = self._style_layer(content, st, tuple(float(v) for v in frame)) if st else content
        final = final.crop(frame)
        if clip_alpha is not None:
            final.putalpha(ImageChops.multiply(final.getchannel("A"), clip_alpha))
        if bake_opacity and props["opacity"] < 1:
            opacity = props["opacity"]
            final.putalpha(final.getchannel("A").point(lambda v: int(v * opacity + 0.5)))
        return final, content_alpha

    def render_stack(self, img: Image.Image, only: set[str] | None = None,
                     only_layers: set[str] | None = None) -> Image.Image:
        """Composite every layer onto ``img`` (bottom to top)."""
        items = self.stack(only)
        base_alpha: Image.Image | None = None
        base_visible = True
        for index, (layer, elements) in enumerate(items):
            name = layer["name"] if layer else "base"
            props = self.layer_props(layer) if layer else {"opacity": 1.0, "blend": "normal", "clip": False,
                                                            "visible": True, "style": {}}
            shown = only_layers is None or name in only_layers
            nxt = items[index + 1][0] if index + 1 < len(items) else None
            clipped_onto = bool(nxt and nxt.get("clip"))
            if not props["visible"]:
                if not props["clip"]:
                    base_alpha, base_visible = None, False
                continue
            erases = any(str(resolve_style(e.get("style"), self.theme).get("blend", "")).lower() == "erase"
                         for e in elements)
            plain = (not props["style"] and props["opacity"] >= 1 and props["blend"] == "normal"
                     and not props["clip"] and not erases)
            if plain and not clipped_onto:
                # nothing special about this layer: draw straight onto the picture, so element blend
                # modes see everything beneath them
                if shown:
                    self._draw_elements(img, elements)
                base_alpha, base_visible = None, True
                continue
            clip_alpha = base_alpha if props["clip"] else None
            if props["clip"] and (base_alpha is None or not base_visible):
                if not base_visible:
                    continue                       # its base is hidden, so the clipped layer is hidden too
                clip_alpha = None                  # nothing below to clip to: behaves like a normal layer
            final, content_alpha = self.layer_image(layer, elements, clip_alpha)
            if not props["clip"]:
                base_alpha, base_visible = content_alpha, True
            if shown:
                composite(img, final, 0, 0, props["blend"])
        return img


_PLACEHOLDER = re.compile(r"\{([A-Za-z_][\w.\-]*)\}")
_PSEUDO = str.maketrans("aeiouycnsAEIOUYCNS", "áéîöüýçñšÅÉÎÖÛÝÇÑŠ")


PSEUDO_LANGS = ("pseudo", "pseudo-long", "pseudo-rtl")


def pseudo_localize(text: str, mode: str = "pseudo") -> str:
    """Pseudo-localisation for clipping and missing glyphs: "pseudo" is accented and ~35% longer
    (typical translation growth), "pseudo-long" ~100% longer (short labels in German / Finnish /
    Russian), "pseudo-rtl" is "pseudo" forced right-to-left, like Godot's fake BiDi."""
    if not text.strip():
        return text
    grow = 1.0 if mode == "pseudo-long" else 0.35
    out = []
    for line in text.split("\n"):
        body = line.translate(_PSEUDO)
        pad = "·" * max(1, int(round(len(line) * grow)))
        item = f"[{body} {pad}]"
        out.append(f"‮{item}‬" if mode == "pseudo-rtl" else item)
    return "\n".join(out)


def text_vars(canvas: dict, language: str | None, strings: dict, extra: dict | None = None) -> dict:
    """Placeholder values: default-language strings < chosen language < canvas vars < instance vars."""
    tables = strings.get("tables", {}) if strings else {}
    default = strings.get("default") if strings else None
    out: dict = {}
    if default and default in tables:
        out.update(tables[default])
    lang = language or canvas.get("language")
    if lang and lang in tables:
        out.update(tables[lang])
    out.update(canvas.get("vars") or {})
    out.update(extra or {})
    return out


def apply_state(canvas: dict, state: str) -> dict:
    """A copy of ``canvas`` with the overrides of one interaction state (hover, pressed...) applied."""
    import copy as _copy
    states = canvas.get("states") or {}
    if state not in states:
        known = ", ".join(sorted(states)) or "(none)"
        raise ValueError(f"canvas '{canvas.get('id')}' has no state '{state}'. States: {known}")
    out = _copy.deepcopy(canvas)
    by_id = {e["id"]: e for e in out["elements"]}
    for eid, props in states[state].items():
        el = by_id.get(eid)
        if el is None:
            continue
        for key, value in props.items():
            if key == "style" and isinstance(value, dict):
                base = el.get("style")
                merged = dict(base) if isinstance(base, dict) else ({"use": base} if base else {})
                for sk, sv in value.items():
                    if sv is None:
                        merged.pop(sk, None)
                    else:
                        merged[sk] = sv
                el["style"] = merged
            elif value is None:
                el.pop(key, None)
            else:
                el[key] = value
    return out


def pixelate(img: Image.Image, spec: dict, colors: dict) -> Image.Image:
    """Binary alpha + limited palette for pixel-art canvases."""
    arr = np.asarray(img, dtype=np.uint8).copy()
    thr = int(spec.get("alpha_threshold", 128))
    opaque = arr[..., 3] >= thr
    arr[..., 3] = np.where(opaque, 255, 0)
    palette = spec.get("palette")
    if palette:
        pal = np.array([parse_color(c, colors)[:3] for c in palette], dtype=np.float64)
        rgb = arr[..., :3].reshape(-1, 3).astype(np.float64)
        idx = np.argmin(((rgb[:, None, :] - pal[None, :, :]) ** 2).sum(-1), axis=1)
        arr[..., :3] = pal[idx].reshape(arr.shape[:2] + (3,)).astype(np.uint8)
    elif spec.get("max_colors"):
        n = max(2, min(256, int(spec["max_colors"])))
        q = Image.fromarray(arr[..., :3], "RGB").quantize(colors=n, method=Image.Quantize.MAXCOVERAGE,
                                                          dither=Image.Dither.NONE).convert("RGB")
        arr[..., :3] = np.asarray(q)
    arr[~opaque] = 0
    return Image.fromarray(arr, "RGBA")


def _instance_cycle(src: dict, target: Any, lookup, seen: tuple = ()) -> list[str] | None:
    """Path of canvas ids from ``src`` back to ``target`` through instance elements, if any."""
    sid = src.get("id")
    if sid == target:
        return [str(sid)]
    if sid in seen or len(seen) > 32:
        return None
    for el in src.get("elements", []):
        if el.get("type") == "instance" and el.get("source"):
            try:
                child = lookup(str(el["source"]))
            except ValueError:
                continue
            path = _instance_cycle(child, target, lookup, seen + (sid,))
            if path:
                return [str(sid)] + path
    return None


def _scales(el: dict) -> tuple[float, float]:
    """Signed scale of an element: scale / scale_x / scale_y, with flip_x / flip_y mirroring.
    A path's own "scale" sizes its path data, so only scale_x / scale_y transform a path."""
    uniform = 1 if el.get("type") == "path" else el.get("scale", 1)
    sx = el.get("scale_x", uniform)
    sy = el.get("scale_y", uniform)
    sx = float(sx) if sx is not None else 1.0
    sy = float(sy) if sy is not None else 1.0
    if el.get("flip_x"):
        sx = -sx
    if el.get("flip_y"):
        sy = -sy
    return sx, sy


def _mirror_style(st: dict, fx: bool, fy: bool) -> dict:
    """Pre-mirror a style so that, after the rendered element is flipped, shadows, glows, bevels and
    surface light still fall the way the rest of the UI is lit."""
    st = dict(st)

    for key, default_dy in (("shadow", 4.0), ("inner_shadow", 2.0)):
        if key in st and not _is_none(st[key]):
            items = []
            for spec in _as_list(st[key]):
                if spec is True:
                    spec = {}
                elif isinstance(spec, (str, list, tuple)):
                    spec = {"color": spec}
                if isinstance(spec, dict):
                    spec = dict(spec)
                    if fx:
                        spec["dx"] = -float(spec.get("dx", 0) or 0)
                    if fy:
                        spec["dy"] = -float(spec.get("dy", default_dy))
                items.append(spec)
            st[key] = items
    if fy and st.get("bevel"):
        bevel = dict(st["bevel"]) if isinstance(st["bevel"], dict) else {}
        bevel["highlight"], bevel["shadow"] = bevel.get("shadow", "#000000@0.35"), bevel.get("highlight", "#ffffff@0.35")
        st["bevel"] = bevel
    if st.get("light"):
        light = dict(st["light"]) if isinstance(st["light"], dict) else {}
        a = float(light.get("angle", 225))
        if fx:
            a = 180.0 - a
        if fy:
            a = -a
        light["angle"] = a
        st["light"] = light
    st.pop("use", None)
    return st


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
    if isinstance(el.get("arc"), dict) and "cx" in el["arc"]:
        el["arc"] = dict(el["arc"], cx=float(el["arc"]["cx"]) + dx, cy=float(el["arc"]["cy"]) + dy)


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
