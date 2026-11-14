"""Figma / Penpot style features: boolean shapes, corner smoothing, fill stacks, variable modes,
auto-layout wrap, vector SVG export and parallel animation frames."""
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from game_ui_mcp import server as s
from game_ui_mcp.store import Store


@pytest.fixture()
def ws(tmp_path):
    s.set_store(Store(tmp_path / "ws"))
    yield tmp_path
    s.set_store(None)


def alpha(canvas="c", scale=1.0):
    st = s.store()
    return np.asarray(st.renderer(st.get(canvas), scale).render(include_background=False))[..., 3]


def test_boolean_ops_cut_merge_and_keep_overlap(ws):
    s.create_canvas("c", 100, 60)
    s.add_rect("c", 10, 10, 40, 40, id="a")
    s.add_rect("c", 30, 10, 40, 40, id="b")
    s.boolean_elements("c", ["a", "b"], op="subtract", id="r")
    a = alpha()
    assert a[30, 20] == 255 and a[30, 40] == 0 and a[30, 60] == 0        # b removed from a, b itself gone
    s.update_element("c", "r", {"op": "intersect"})
    a = alpha()
    assert a[30, 20] == 0 and a[30, 40] == 255 and a[30, 60] == 0
    s.update_element("c", "r", {"op": "exclude"})
    a = alpha()
    assert a[30, 20] == 255 and a[30, 40] == 0 and a[30, 60] == 255
    s.update_element("c", "r", {"op": "union"})
    assert alpha()[30, 40] == 255


def test_boolean_stroke_follows_the_cut_and_moves_with_children(ws):
    s.create_canvas("c", 100, 60)
    s.add_rect("c", 10, 10, 60, 40, id="a")
    s.add_ellipse("c", 70, 30, 15, id="hole")
    s.boolean_elements("c", ["a", "hole"], op="subtract", id="r",
                       style={"fill": "#000000", "stroke": {"color": "#ff0000", "width": 3, "align": "inside"}})
    st = s.store()
    img = np.asarray(st.renderer(st.get("c")).render(include_background=False))
    assert img[30, 54, 0] > 200 and img[30, 54, 3] > 200                   # red rim along the round cut edge
    s.move_elements("c", ids=["r"], dx=10, dy=0)
    assert s.store().get("c")["elements"][0]["children"][1]["cx"] == 80


def test_boolean_rejects_lines_and_works_in_batch(ws):
    s.create_canvas("c", 100, 60)
    s.add_rect("c", 10, 10, 40, 40, id="a")
    s.add_line("c", [[0, 0], [90, 50]], id="l")
    with pytest.raises(ValueError, match="only filled shapes"):
        s.boolean_elements("c", ["a", "l"])
    assert [e["id"] for e in s.store().get("c")["elements"]] == ["a", "l"]          # failed call changed nothing
    s.batch("c", [{"op": "add_ellipse", "id": "e", "cx": 50, "cy": 30, "rx": 10},
                  {"op": "boolean_elements", "ids": ["a", "e"], "operation": "union", "id": "u"}])
    assert [e["id"] for e in s.store().get("c")["elements"]] == ["u", "l"]


def test_corner_smoothing_eases_corners(ws):
    s.create_canvas("c", 120, 120)
    s.add_rect("c", 10, 10, 100, 100, radius=30, id="r")
    plain = alpha().astype(int).sum()
    s.update_element("c", "r", {"smoothing": 1})
    smooth = alpha().astype(int).sum()
    assert smooth != plain and abs(smooth - plain) < 0.05 * plain


def test_fill_stack_paints_bottom_to_top(ws):
    s.create_canvas("c", 40, 40)
    s.add_rect("c", 0, 0, 40, 40, style={"fill": ["#0000ff", "#ff000080"]})
    st = s.store()
    px = np.asarray(st.renderer(st.get("c")).render(include_background=False))[20, 20]
    assert px[0] > 100 and px[2] > 100 and px[3] == 255                    # red at half alpha over blue


def test_variable_modes_switch_tokens(ws):
    s.set_theme(colors={"bg": "#000000"}, modes={"light": {"bg": "#ffffff"}})
    s.create_canvas("c", 10, 10)
    s.add_rect("c", 0, 0, 10, 10, fill="$bg")
    st = s.store()

    def red():
        return np.asarray(st.renderer(st.get("c")).render(include_background=False))[5, 5, 0]

    assert red() == 0
    s.set_theme(mode="light")
    assert red() == 255
    s.update_canvas("c", mode="light")
    s.set_theme(mode="")
    assert red() == 255                                                     # the canvas keeps its own mode
    with pytest.raises(ValueError, match="no mode"):
        s.set_theme(mode="sepia")
    css = s.export_theme("css", path=str(ws / "t.css"))
    text = (ws / "t.css").read_text(encoding="utf-8")
    assert css["modes"] == ["light"] and '[data-theme="light"]' in text and "#ffffff" in text


def test_wrap_flows_items_onto_new_lines(ws):
    s.create_canvas("c", 200, 200)
    s.add_rect("c", 0, 0, 150, 200, id="box")      # inner width 140 = exactly 3 items
    for i in range(5):
        s.add_rect("c", 0, 0, 40, 30, id=f"i{i}", group="items")
    s.arrange_elements("c", group="items", container="box", gap=10, padding=5, wrap=True, align="start")
    els = {e["id"]: e for e in s.store().get("c")["elements"]}
    assert [els[f"i{i}"]["y"] for i in range(5)] == [5, 5, 5, 45, 45]
    assert [els[f"i{i}"]["x"] for i in (0, 1, 2, 3)] == [5, 55, 105, 5]


def test_svg_export_keeps_shapes_and_text_editable(ws):
    s.create_canvas("c", 200, 100, background="#101018")
    s.add_rect("c", 10, 10, 80, 40, radius=8, id="panel",
               style={"fill": {"type": "linear", "stops": [[0, "#38bdf8"], [1, "#1d4ed8"]]},
                      "stroke": {"color": "#ffffff", "width": 2}, "shadow": {"blur": 6, "dy": 3}})
    s.add_text("c", "PLAY", x=50, y=30, size=16, anchor="center", id="label")
    s.add_rect("c", 110, 10, 80, 40, id="fancy", style={"fill": {"type": "shader", "code": "r = u; g = v; b = 1"}})
    res = s.export_svg("c", path=str(ws / "c.svg"))
    assert set(res["vector"]) == {"panel", "label"} and res["embedded_as_image"] == ["fancy"]
    root = ET.parse(ws / "c.svg").getroot()
    ns = "{http://www.w3.org/2000/svg}"
    assert root.find(f".//{ns}linearGradient") is not None
    assert root.find(f".//{ns}feDropShadow") is not None
    assert [t.text for t in root.iter(f"{ns}tspan")] == ["PLAY"]


def test_parallel_frames_match_sequential(ws):
    s.create_canvas("c", 80, 40)
    s.add_rect("c", 0, 0, 20, 20, id="r")
    s.create_animation("c", "a", duration=1)
    s.set_keyframes("c", "a", "r", "dx", [{"t": 0, "value": 0}, {"t": 1, "value": 60}])
    c = s.store().get("c")
    anim = c["animations"]["a"]
    times = [i / 6 for i in range(6)]
    par, _ = s._poses(c, anim, times, 1.0)
    seq = [s._pose(c, anim, t, 1.0)[0] for t in times]
    assert all(a.tobytes() == b.tobytes() for a, b in zip(par, seq))
