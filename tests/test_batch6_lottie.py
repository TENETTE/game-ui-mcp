"""Lottie export: transform tracks stay Lottie animation, look changes are baked per frame, and a frame
rebuilt from the Lottie layers matches our own render."""
import base64
import io
import json
import math

import numpy as np
import pytest
from PIL import Image

from game_ui_mcp import animation as anim_mod
from game_ui_mcp import server as s
from game_ui_mcp.store import Store


@pytest.fixture()
def ws(tmp_path):
    s.set_store(Store(tmp_path / "ws"))
    yield tmp_path
    s.set_store(None)


def _value(prop, frame):
    if prop["a"] == 0:
        return prop["k"]
    keys = prop["k"]
    return keys[min(frame, len(keys) - 1)]["s"]


def _compose(doc, frame):
    """Minimal Lottie image-layer compositor (anchor, position, scale, rotation, opacity)."""
    canvas = Image.new("RGBA", (doc["w"], doc["h"]), (0, 0, 0, 0))
    assets = {a["id"]: Image.open(io.BytesIO(base64.b64decode(a["p"].split(",", 1)[1]))).convert("RGBA")
              for a in doc["assets"]}
    for layer in reversed(doc["layers"]):
        if not layer["ip"] <= frame < layer["op"]:
            continue
        ks = layer["ks"]
        img = assets[layer["refId"]]
        ax, ay = _value(ks["a"], frame)[:2]
        px, py = _value(ks["p"], frame)[:2]
        sx, sy = (v / 100 for v in _value(ks["s"], frame)[:2])
        rot = _value(ks["r"], frame)
        rot = rot[0] if isinstance(rot, list) else rot
        opa = _value(ks["o"], frame)
        opa = (opa[0] if isinstance(opa, list) else opa) / 100
        c, s_ = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        # image pixel q -> comp: p + R * S * (q - a); PIL wants the inverse
        a11, a12, a21, a22 = c * sx, -s_ * sy, s_ * sx, c * sy
        det = a11 * a22 - a12 * a21
        i11, i12, i21, i22 = a22 / det, -a12 / det, -a21 / det, a11 / det
        cx0, cy0 = -px, -py
        out = img.transform(canvas.size, Image.AFFINE,
                            (i11, i12, i11 * cx0 + i12 * cy0 + ax, i21, i22, i21 * cx0 + i22 * cy0 + ay),
                            resample=Image.BICUBIC)
        if opa < 1:
            out.putalpha(out.getchannel("A").point(lambda v: int(v * opa)))
        canvas.alpha_composite(out)
    return canvas


def test_lottie_transform_and_baked_layers_match_our_render(ws):
    s.create_canvas("c", 200, 100)
    s.add_rect("c", 0, 0, 200, 100, fill="#202030", id="bg")
    s.add_rect("c", 20, 30, 40, 40, radius=6, fill="#ff4040", id="box")
    s.add_ellipse("c", 150, 50, 20, fill="#40ff40", id="dot")
    s.create_animation("c", "go", duration=1, fps=10)
    s.set_keyframes("c", "go", "box", "dx", [{"t": 0, "value": 0}, {"t": 1, "value": 80}])
    s.set_keyframes("c", "go", "box", "rotation", [{"t": 0, "value": 0}, {"t": 1, "value": 90}])
    s.set_keyframes("c", "go", "box", "opacity", [{"t": 0, "value": 1}, {"t": 1, "value": 0.5}])
    s.set_keyframes("c", "go", "dot", "style.fill", [{"t": 0, "value": "#40ff40"}, {"t": 1, "value": "#4040ff"}])
    res = s.export_animation("c", "go", format="lottie", path=str(ws / "go.json"))
    assert res["animated_by_transform"] == ["box"] and res["baked_per_frame"] == ["dot"] and res["static"] == ["bg"]
    doc = json.loads((ws / "go.json").read_text(encoding="utf-8"))
    assert doc["fr"] == 10 and doc["op"] == 11 and doc["w"] == 200
    frame = 5
    st = s.store()
    c = st.get("c")
    pose, _ = anim_mod.apply_animation(c, c["animations"]["go"], 0.5, {})
    ours = np.asarray(st.renderer(pose).render(include_background=False)).astype(int)
    theirs = np.asarray(_compose(doc, frame)).astype(int)
    diff = np.abs(ours[..., :3] - theirs[..., :3]).mean()
    assert diff < 4, diff
