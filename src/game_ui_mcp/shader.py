"""Shader fill: per-pixel formulas the model writes itself, evaluated with numpy.

{"type": "shader", "code": "...", "params": {...}} paints any procedural look (energy, plasma, scanlines,
holograms, wood, marble, rust, rings, ripples, distortion of a pattern...) that the fixed fill types
cannot. The code is a tiny expression language checked against a whitelist (no attributes, imports,
loops or calls outside FUNCS), so it can only compute numbers - it cannot touch files or the system.
"""

from __future__ import annotations

import ast
from functools import lru_cache

import numpy as np

from .colors import parse_color

MAX_CODE = 6000
MAX_STATEMENTS = 80
MAX_WORK = 85e6          # noise-call units x pixels, ~10 s: an 8-octave fbm still fits a full 4K fill


def _fract(a):
    return a - np.floor(a)


def _hash(ix, iy, seed=0.0):
    """Deterministic pseudo-random 0..1 per integer lattice point."""
    return _fract(np.sin(ix * 127.1 + iy * 311.7 + seed * 74.7) * 43758.5453123)


def _noise(x, y, seed=0.0):
    """Smooth value noise, 0..1, one feature per unit."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = x - ix, y - iy
    ux, uy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = _hash(ix, iy, seed), _hash(ix + 1, iy, seed)
    c, d = _hash(ix, iy + 1, seed), _hash(ix + 1, iy + 1, seed)
    return a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy


def _fbm(x, y, octaves=5, seed=0.0):
    """Fractal noise (sum of noise octaves), 0..1. Clouds, smoke, marble, terrain, grunge."""
    n = int(max(1, min(8, float(np.max(octaves)))))
    total, amp, norm, f = 0.0, 0.5, 0.0, 1.0
    for i in range(n):
        total = total + amp * _noise(x * f, y * f, seed + i * 17.0)
        norm += amp
        amp *= 0.5
        f *= 2.0
    return total / norm


def _voronoi(x, y, seed=0.0):
    """Distance to the nearest random cell point (0 at a point, ~0.7 max). Cells, scales, cracks, crystals."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    ix, iy = np.floor(x), np.floor(y)
    best = np.full(np.broadcast(x, y).shape, 9.0)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            cx, cy = ix + dx, iy + dy
            px = cx + _hash(cx, cy, seed + 1.3)
            py = cy + _hash(cx, cy, seed + 7.9)
            best = np.minimum(best, np.hypot(px - x, py - y))
    return best


def _smoothstep(e0, e1, v):
    t = np.clip((v - e0) / np.where(e1 - e0 == 0, 1e-9, e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


FUNCS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
    "atan2": np.arctan2, "sqrt": lambda a: np.sqrt(np.maximum(a, 0)), "abs": np.abs, "exp": np.exp,
    "log": lambda a: np.log(np.maximum(a, 1e-12)), "pow": np.power, "floor": np.floor, "ceil": np.ceil,
    "round": np.round, "fract": _fract, "mod": np.mod, "min": np.minimum, "max": np.maximum, "sign": np.sign,
    "clamp": lambda a, lo=0.0, hi=1.0: np.clip(a, lo, hi), "mix": lambda a, b, t: a + (b - a) * t,
    "step": lambda edge, a: (a >= edge).astype(np.float64), "smoothstep": _smoothstep,
    "length": lambda a, b: np.hypot(a, b), "hypot": np.hypot, "where": np.where,
    "noise": _noise, "fbm": _fbm, "voronoi": _voronoi, "rand": lambda a, b, seed=0.0: _hash(np.floor(a), np.floor(b), seed),
}
_NODES = (ast.Module, ast.Assign, ast.Expr, ast.Name, ast.Load, ast.Store, ast.Constant, ast.BinOp, ast.UnaryOp,
          ast.Compare, ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub,
          ast.UAdd, ast.BitAnd, ast.BitOr, ast.Invert, ast.Lt, ast.Gt, ast.LtE, ast.GtE, ast.Eq, ast.NotEq)
INPUTS = ("x", "y", "u", "v", "w", "h", "px", "pi")
OUTPUTS = ("r", "g", "b", "a")


@lru_cache(maxsize=256)
def compile_code(code: str):
    if len(code) > MAX_CODE:
        raise ValueError(f"shader code is over {MAX_CODE} characters")
    try:
        lines = (ln.strip() for ln in code.replace(";", "\n").splitlines())   # no blocks, so indent means nothing
        tree = ast.parse("\n".join(lines), mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"shader code: syntax error on line {exc.lineno}: {exc.msg}") from exc
    if len(tree.body) > MAX_STATEMENTS:
        raise ValueError(f"shader code: at most {MAX_STATEMENTS} statements")
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise ValueError(f"shader code: '{type(node).__name__}' is not allowed - use plain assignments, "
                             "arithmetic, comparisons and the listed functions (where() instead of if)")
        if isinstance(node, ast.Assign) and not all(isinstance(t, ast.Name) for t in node.targets):
            raise ValueError("shader code: assign to plain names only")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ValueError("shader code: names cannot start with '_'")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
                raise ValueError(f"shader code: unknown function. Available: {', '.join(sorted(FUNCS))}")
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise ValueError("shader code: only number constants (put colours in params)")
            node.value = float(node.value)          # float math: 9**9**9 overflows instead of hanging
    assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets}
    if not {"r", "g", "b"} <= assigned:
        raise ValueError("shader code must assign r, g and b (0..1); a (alpha 0..1) is optional")
    return compile(tree, "<shader>", "exec"), _cost(tree)


def _cost(tree) -> float:
    """Work per pixel in 'noise calls' (one is ~0.12 s per megapixel), so a heavy formula is refused, not run."""
    units = 0.1 * len(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id
            if name == "fbm":
                oct_arg = node.args[2] if len(node.args) > 2 else None
                units += min(8.0, max(1.0, oct_arg.value)) if isinstance(oct_arg, ast.Constant) else \
                    (5.0 if oct_arg is None else 8.0)
            elif name == "voronoi":
                units += 4.0
            elif name in ("noise", "rand"):
                units += 1.0
    return units


def _env(params: dict, colors: dict) -> dict:
    env: dict = {}
    for key, value in (params or {}).items():
        if not str(key).isidentifier() or key.startswith("_") or key in INPUTS or key in FUNCS:
            raise ValueError(f"shader param '{key}' must be a plain name not used by inputs or functions")
        if isinstance(value, str):                          # a colour: name_r, name_g, name_b, name_a
            r, g, b, a = parse_color(value, colors)
            env.update({f"{key}_r": r / 255, f"{key}_g": g / 255, f"{key}_b": b / 255, f"{key}_a": a / 255})
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            env[key] = float(value)
        else:
            raise ValueError(f"shader param '{key}' must be a number or a colour string, got {type(value).__name__}")
    return env


def evaluate(spec: dict, size: tuple[int, int], box_px, k: float, colors: dict) -> np.ndarray:
    """RGBA float array 0..255 of shape (h, w, 4) for the shader fill."""
    code, units = compile_code(str(spec.get("code", "")))
    w_px, h_px = size
    if units * w_px * h_px > MAX_WORK:
        raise ValueError(f"shader code is too heavy for a {w_px}x{h_px} px fill (~{units:.0f} noise calls per "
                         f"pixel, limit ~{MAX_WORK / (w_px * h_px):.0f}): use fewer fbm octaves or "
                         "voronoi/noise calls, or compute a value once and reuse it")
    x0, y0, x1, y1 = box_px
    ys, xs = np.mgrid[0:h_px, 0:w_px].astype(np.float64)
    xs += 0.5
    ys += 0.5
    ns = {"__builtins__": {}}
    ns.update(FUNCS)
    ns.update(_env(spec.get("params") or {}, colors))
    ns.update({"x": (xs - x0) / k, "y": (ys - y0) / k, "u": (xs - x0) / max(1e-6, x1 - x0),
               "v": (ys - y0) / max(1e-6, y1 - y0), "w": (x1 - x0) / k, "h": (y1 - y0) / k, "px": k, "pi": np.pi})
    with np.errstate(all="ignore"):
        try:
            exec(code, ns)        # noqa: S102 - whitelisted AST: arithmetic and FUNCS only
        except (TypeError, ValueError, ZeroDivisionError, OverflowError, NameError) as exc:
            raise ValueError(f"shader code failed: {exc}") from exc
    out = np.empty((h_px, w_px, 4))
    for i, name in enumerate(OUTPUTS):
        try:
            val = np.broadcast_to(np.asarray(ns.get(name, 1.0), dtype=np.float64), (h_px, w_px))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"shader output '{name}' must be one number per pixel (where() needs 3 arguments: "
                             "condition, value_if_true, value_if_false)") from exc
        out[..., i] = np.nan_to_num(np.clip(val, 0, 1))
    return out * 255.0
