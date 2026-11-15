"""Lottie (Bodymovin JSON) export of an animation, for apps, web (lottie-web), Unity / Unreal / Godot
Lottie players and Figma Motion style hand-off.

Every element becomes an image layer. Movement, rotation, scale and opacity stay real Lottie transform
animation (light and smooth at any frame rate); elements whose other properties animate (colour, glow,
arc sweep, text...) become one picture per frame so the result always matches the PNG. Runs of elements
that never move are merged into one picture.
"""

from __future__ import annotations

import base64
import hashlib
import io
from typing import Any

from .animation import apply_animation, frame_times
from .render import _scales, resolve_style

_MOVE_KEYS = {"x", "y", "cx", "cy", "points", "origin", "rotation", "scale", "scale_x", "scale_y", "flip_x", "flip_y"}


def _ref_point(el: dict) -> tuple[float, float]:
    if el.get("points") and el["type"] in ("polygon", "line"):
        return float(el["points"][0][0]), float(el["points"][0][1])
    if "cx" in el:
        return float(el["cx"]), float(el["cy"])
    return float(el.get("x", 0)), float(el.get("y", 0))


def _opacity(el: dict, theme: dict) -> float:
    return float(resolve_style(el.get("style"), theme).get("opacity", 1.0))


def _look(el: dict, theme: dict) -> Any:
    """Everything that changes the picture except position, rotation, scale and opacity."""
    st = dict(resolve_style(el.get("style"), theme))
    st.pop("opacity", None)
    return ({k: v for k, v in el.items() if k not in _MOVE_KEYS and k != "style"}, st)


def _prop(values: list) -> dict:
    if all(v == values[0] for v in values):
        return {"a": 0, "k": values[0]}
    dims = len(values[0]) if isinstance(values[0], list) else 1
    lin_o, lin_i = {"x": [0.0] * dims, "y": [0.0] * dims}, {"x": [1.0] * dims, "y": [1.0] * dims}
    keys = [{"t": i, "s": v if isinstance(v, list) else [v], "o": lin_o, "i": lin_i} for i, v in enumerate(values)]
    return {"a": 1, "k": keys}


class _Assets:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self._seen: dict[str, str] = {}

    def add(self, img) -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        key = hashlib.sha1(data).hexdigest()
        if key not in self._seen:
            aid = f"img_{len(self.items)}"
            self._seen[key] = aid
            self.items.append({"id": aid, "w": img.width, "h": img.height, "u": "", "e": 1,
                               "p": "data:image/png;base64," + base64.b64encode(data).decode("ascii")})
        return self._seen[key]


def build(store, canvas: dict, anim: dict, name: str, fps: float | None, scale: float, include_background: bool) -> tuple[dict, dict]:
    theme = store.theme
    colors = theme.get("colors", {})
    times = frame_times(anim, fps)
    rate = float(fps or anim.get("fps", 30))
    n = len(times)
    poses = [apply_animation(canvas, anim, t, colors)[0] for t in times]
    r0 = store.renderer(canvas, scale)
    k = scale
    assets = _Assets()
    layers: list[dict] = []
    report = {"transform": [], "frames": [], "static": []}

    def image_layer(asset: str, x: float, y: float, ip: int = 0, op: int | None = None, ks: dict | None = None, nm: str = ""):
        layers.append({"ddd": 0, "ty": 2, "nm": nm, "refId": asset, "sr": 1, "ao": 0, "bm": 0, "st": 0,
                       "ip": ip, "op": n if op is None else op,
                       "ks": ks or {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "p": {"a": 0, "k": [x, y, 0]},
                                    "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]}}})

    if include_background and canvas.get("background"):
        bg = r0.render(include_background=True, only=set())
        image_layer(assets.add(bg), 0, 0, nm="background")

    order = [el for _, els in r0.stack() for el in els]
    posed_by_id = [{e["id"]: e for e in p["elements"]} for p in poses]
    static_run: list[str] = []

    def flush() -> None:
        if not static_run:
            return
        img = r0.render(include_background=False, only=set(static_run))
        bbox = img.getchannel("A").getbbox()
        if bbox:
            image_layer(assets.add(img.crop(bbox)), bbox[0], bbox[1], nm="static: " + ", ".join(static_run[:4]))
        report["static"] += static_run
        static_run.clear()

    for el in order:
        frames = [pb.get(el["id"], el) for pb in posed_by_id]
        if all(f == el for f in frames):
            static_run.append(el["id"])
            continue
        flush()
        looks_same = all(_look(f, theme) == _look(el, theme) for f in frames)
        if looks_same:
            plain = {key: v for key, v in el.items() if key not in ("rotation", "scale", "scale_x", "scale_y",
                                                                   "flip_x", "flip_y")}
            st = resolve_style(el.get("style"), theme)
            if "opacity" in st:
                plain["style"] = {**st, "opacity": 1.0}
            result = r0.render_element(plain)
            if result is None:
                continue
            img, px0, py0, _ = result
            st0 = r0._nums(resolve_style(plain.get("style"), theme))
            pivot0 = r0._pivot(plain, r0._geom_box(r0._nums(plain), st0))
            ref0 = _ref_point(el)
            pos, rot, scl, opa = [], [], [], []
            for f in frames:
                rx, ry = _ref_point(f)
                dx, dy = rx - ref0[0], ry - ref0[1]
                sx, sy = _scales(f)
                pos.append([round((pivot0[0] + dx) * k, 3), round((pivot0[1] + dy) * k, 3), 0])
                rot.append(round(float(f.get("rotation", 0) or 0), 4))
                scl.append([round(sx * 100, 4), round(sy * 100, 4), 100])
                opa.append(round(_opacity(f, theme) * 100, 3))
            ks = {"o": _prop(opa), "r": _prop(rot), "p": _prop(pos),
                  "a": {"a": 0, "k": [round(pivot0[0] * k - px0, 3), round(pivot0[1] * k - py0, 3), 0]},
                  "s": _prop(scl)}
            image_layer(assets.add(img), 0, 0, ks=ks, nm=el["id"])
            report["transform"].append(el["id"])
        else:
            for i, (pose, f) in enumerate(zip(poses, frames)):
                result = store.renderer(pose, scale).render_element(f)
                if result is None:
                    continue
                img, px0, py0, _ = result
                image_layer(assets.add(img), px0, py0, ip=i, op=i + 1, nm=f"{el['id']} #{i}")
            report["frames"].append(el["id"])
    flush()
    for i, layer in enumerate(reversed(layers), 1):
        layer["ind"] = i
    doc = {"v": "5.7.4", "fr": rate, "ip": 0, "op": n, "w": round(canvas["width"] * k), "h": round(canvas["height"] * k),
           "nm": name, "ddd": 0, "assets": assets.items, "layers": list(reversed(layers))}
    return doc, report
