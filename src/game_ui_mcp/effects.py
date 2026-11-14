"""Pixel effects that need more precision than 8-bit filters: float blur, round dilation,
surface lighting (emboss), color adjustment, procedural noise and OKLab conversion."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from . import gpu
from .colors import parse_color


# --------------------------------------------------------------------------- blur / morphology

def _box(a, r: int, axis: int):
    """Box blur of half-width r along one axis; outside the array counts as zero (numpy or cupy)."""
    if r <= 0:
        return a
    xp = gpu.array_module(a)
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = xp.cumsum(xp.pad(a, pad), axis=axis, dtype=xp.float64)
    n = a.shape[axis]
    hi = xp.take(c, xp.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = xp.take(c, xp.arange(0, n), axis=axis)
    return (hi - lo) / (2 * r + 1)


def blur_float(a: np.ndarray, radius: float, passes: int = 3) -> np.ndarray:
    """Smooth float blur. passes=3 is close to a Gaussian of sigma ~ radius / 2; passes=1 is a plain
    box of half-width ``radius`` (turns a hard edge into a linear ramp)."""
    if radius <= 0:
        return a
    if passes == 1:
        r = max(1, int(round(radius)))
    else:
        sigma = radius / 2.0
        r = max(1, int(round((math.sqrt(12 * sigma * sigma / passes + 1) - 1) / 2)))
    xp = gpu.module_for(a.size)                    # big shadows / glows blur on the GPU when there is one
    out = xp.asarray(a, dtype=xp.float64)
    for _ in range(passes):
        out = _box(_box(out, r, 0), r, 1)
    return gpu.to_numpy(out)


def dilate(alpha: np.ndarray, radius: float) -> np.ndarray:
    """Grow a coverage mask by ``radius`` pixels with a round (disc) brush. uint8 in, uint8 out."""
    if radius <= 0:
        return alpha
    big = int(math.ceil(radius))
    h, w = alpha.shape
    levels = max(1, int(math.floor(math.log2(2 * big + 1))) + 1)
    padded = np.pad(alpha, ((0, 0), (big, big + (1 << levels))))
    tables = [padded]                                   # tables[j][:, i] = max of padded[:, i : i + 2**j]
    for j in range(levels):
        prev = tables[-1]
        shifted = np.zeros_like(prev)
        shifted[:, : prev.shape[1] - (1 << j)] = prev[:, (1 << j):]
        tables.append(np.maximum(prev, shifted))
    out = np.zeros((h + 2 * big, w), dtype=np.uint8)
    rows: dict[int, np.ndarray] = {}
    for dy in range(-big, big + 1):
        hw = int(math.floor(math.sqrt(max(0.0, radius * radius - dy * dy))))
        if dy * dy > radius * radius:
            continue
        if hw not in rows:
            length = 2 * hw + 1
            j = int(math.floor(math.log2(length)))
            t = tables[j]
            start = big - hw                            # window [i - hw, i + hw] in padded coordinates
            a = t[:, start: start + w]
            b = t[:, start + length - (1 << j): start + length - (1 << j) + w]
            rows[hw] = np.maximum(a, b)
        np.maximum(out[big + dy: big + dy + h], rows[hw], out=out[big + dy: big + dy + h])
    return out[big: big + h]


def erode(alpha: np.ndarray, radius: float) -> np.ndarray:
    if radius <= 0:
        return alpha
    return 255 - dilate(255 - alpha, radius)


def grow(alpha: np.ndarray, distance: float) -> np.ndarray:
    """Positive distance grows the mask, negative shrinks it."""
    return dilate(alpha, distance) if distance > 0 else erode(alpha, -distance) if distance < 0 else alpha


# --------------------------------------------------------------------------- lighting

def emboss(alpha: Image.Image, spec: dict, colors: dict, k: float) -> Image.Image:
    """Light a shape as if it were raised: the alpha edge becomes a bevel whose slopes catch light
    from ``angle``. Returns an RGBA overlay (highlights and shadows) limited to the shape."""
    if not isinstance(spec, dict):
        spec = {}
    size = max(0.5, float(spec.get("size", 6))) * k
    depth = max(0.0, float(spec.get("depth", 1)))
    angle = math.radians(float(spec.get("angle", 225)))             # where the light comes from
    altitude = math.radians(max(5.0, min(85.0, float(spec.get("altitude", 40)))))
    profile = str(spec.get("profile", "round")).lower()
    if profile not in ("round", "chisel"):
        raise ValueError("light.profile must be 'round' or 'chisel'")
    hi_color = parse_color(spec.get("highlight", "#ffffff@0.7"), colors)
    sh_color = parse_color(spec.get("shadow", "#000000@0.6"), colors)
    gloss = max(0.0, float(spec.get("gloss", 0)))
    gloss_power = max(1.0, float(spec.get("gloss_size", 0.25)) ** -1 * 8)

    a = np.asarray(alpha, dtype=np.float64) / 255.0
    pad = int(math.ceil(size)) + 2
    ap = np.pad(a, pad)
    height = blur_float(ap, size, 1 if profile == "chisel" else 3)
    height = np.clip((height - 0.5) * 2.0, 0.0, 1.0)                # 0 at the edge, 1 once `size` inside
    soften = float(spec.get("soften", 0)) * k
    if soften > 0:
        height = blur_float(height, soften, 3)
    gy, gx = np.gradient(height)
    slope = size * depth * (1.0 if profile == "chisel" else 1.6)
    nx, ny = -gx * slope, -gy * slope
    norm = np.sqrt(nx * nx + ny * ny + 1.0)
    nx, ny, nz = nx / norm, ny / norm, 1.0 / norm
    lx, ly, lz = math.cos(angle) * math.cos(altitude), math.sin(angle) * math.cos(altitude), math.sin(altitude)
    lit = nx * lx + ny * ly + nz * lz
    hi = np.clip((lit - lz) / max(1e-6, 1.0 - lz), 0.0, 1.0)
    sh = np.clip((lz - lit) / max(1e-6, lz), 0.0, 1.0)
    if gloss > 0:
        hx, hy, hz = lx, ly, lz + 1.0
        hn = math.sqrt(hx * hx + hy * hy + hz * hz)
        hx, hy, hz = hx / hn, hy / hn, hz / hn
        spec_term = np.clip(nx * hx + ny * hy + nz * hz, 0.0, 1.0) ** gloss_power - hz ** gloss_power
        hi = np.clip(hi + np.clip(spec_term, 0.0, 1.0) * gloss * 2.0, 0.0, 1.0)
    crop = (slice(pad, pad + a.shape[0]), slice(pad, pad + a.shape[1]))
    ha = hi[crop] * (hi_color[3] / 255.0) * a
    sa = sh[crop] * (sh_color[3] / 255.0) * a
    total = ha + sa
    safe = np.where(total > 0, total, 1.0)
    out = np.empty(a.shape + (4,), dtype=np.float64)
    for c in range(3):
        out[..., c] = (hi_color[c] * ha + sh_color[c] * sa) / safe
    out[..., 3] = np.clip(total, 0.0, 1.0) * 255.0
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA")


# --------------------------------------------------------------------------- color adjustment

def adjust(img: Image.Image, spec: dict, colors: dict) -> Image.Image:
    """brightness, contrast, saturation (-1..1), hue (degrees), tint (color; its alpha is the amount), invert."""
    if not isinstance(spec, dict):
        raise ValueError("adjust must be an object like {brightness, contrast, saturation, hue, tint}")
    arr = np.asarray(img, dtype=np.float64).copy()
    rgb = arr[..., :3]
    hue = float(spec.get("hue", 0) or 0)
    if hue:
        t = math.radians(hue)
        c, s = math.cos(t), math.sin(t)
        m = np.array([[0.299 + 0.701 * c + 0.168 * s, 0.587 - 0.587 * c + 0.330 * s, 0.114 - 0.114 * c - 0.497 * s],
                      [0.299 - 0.299 * c - 0.328 * s, 0.587 + 0.413 * c + 0.035 * s, 0.114 - 0.114 * c + 0.292 * s],
                      [0.299 - 0.300 * c + 1.250 * s, 0.587 - 0.588 * c - 1.050 * s, 0.114 + 0.886 * c - 0.203 * s]])
        rgb = rgb @ m.T
    sat = float(spec.get("saturation", 0) or 0)
    if sat:
        lum = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2])[..., None]
        rgb = lum + (rgb - lum) * max(0.0, 1.0 + sat)
    bright = float(spec.get("brightness", 0) or 0)
    if bright:
        rgb = rgb + bright * 255.0
    contrast = float(spec.get("contrast", 0) or 0)
    if contrast:
        rgb = (rgb - 127.5) * max(0.0, 1.0 + contrast) + 127.5
    if spec.get("invert"):
        rgb = 255.0 - rgb
    if spec.get("tint"):
        r, g, b, a = parse_color(spec["tint"], colors)
        amount = a / 255.0
        rgb = rgb * (1 - amount) + np.array([r, g, b], dtype=np.float64) * amount
    arr[..., :3] = np.clip(rgb, 0, 255)
    return Image.fromarray((arr + 0.5).astype(np.uint8), "RGBA")


# --------------------------------------------------------------------------- noise

def _lattice(ix: np.ndarray, iy: np.ndarray, seed: float) -> np.ndarray:
    v = np.sin(ix * 127.1 + iy * 311.7 + seed * 74.7) * 43758.5453123
    return v - np.floor(v)


def fractal_noise(ux: np.ndarray, uy: np.ndarray, scale: float, octaves: int, seed: float) -> np.ndarray:
    """Smooth cloud-like noise in 0..1 at coordinates (ux, uy); ``scale`` is the size of the largest blobs."""
    total = np.zeros(ux.shape, dtype=np.float64)
    weight, amp, cell = 0.0, 1.0, max(0.5, scale)
    for o in range(max(1, min(8, int(octaves)))):
        x, y = ux / cell, uy / cell
        x0, y0 = np.floor(x), np.floor(y)
        tx, ty = x - x0, y - y0
        tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
        s = seed + o * 17.0
        top = _lattice(x0, y0, s) * (1 - tx) + _lattice(x0 + 1, y0, s) * tx
        bottom = _lattice(x0, y0 + 1, s) * (1 - tx) + _lattice(x0 + 1, y0 + 1, s) * tx
        total += (top * (1 - ty) + bottom * ty) * amp
        weight += amp
        amp *= 0.5
        cell /= 2.0
    return total / weight


# --------------------------------------------------------------------------- color spaces

def _to_linear(c: np.ndarray) -> np.ndarray:
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _from_linear(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055) * 255.0


_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])
_M1_INV = np.linalg.inv(_M1)
_M2_INV = np.linalg.inv(_M2)


def encode_space(rgb: np.ndarray, space: str) -> np.ndarray:
    """sRGB 0-255 (…, 3) -> coordinates of the interpolation space."""
    if space == "srgb":
        return rgb.astype(np.float64)
    lin = _to_linear(rgb.astype(np.float64))
    if space == "linear":
        return lin
    if space == "oklab":
        return np.cbrt(lin @ _M1.T) @ _M2.T
    raise ValueError("gradient space must be 'srgb', 'linear' or 'oklab'")


def decode_space(values: np.ndarray, space: str) -> np.ndarray:
    if space == "srgb":
        return values
    if space == "linear":
        return _from_linear(values)
    lms = values @ _M2_INV.T
    return _from_linear((lms ** 3) @ _M1_INV.T)
