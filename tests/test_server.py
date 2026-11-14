import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from game_ui_mcp import export as ex
from game_ui_mcp import geometry as geo
from game_ui_mcp import server as s
from game_ui_mcp.colors import parse_color
from game_ui_mcp.icons import icon_names
from game_ui_mcp.render import parse_anchor
from game_ui_mcp.store import Store


@pytest.fixture()
def ws(tmp_path):
    s.set_store(Store(tmp_path / "ws"))
    yield tmp_path
    s.set_store(None)


def render(canvas: str, scale: float = 1.0, background: bool = False) -> np.ndarray:
    st = s.store()
    return np.asarray(st.renderer(st.get(canvas), scale).render(include_background=background))


# ---------------------------------------------------------------- units

def test_parse_color_forms():
    assert parse_color("#fff") == (255, 255, 255, 255)
    assert parse_color("#11223380") == (17, 34, 51, 128)
    assert parse_color("rgba(10, 20, 30, 0.5)") == (10, 20, 30, 128)
    assert parse_color("#ffffff@0.5")[3] == 128
    assert parse_color("$primary@0.5", {"primary": "#ff0000"}) == (255, 0, 0, 128)
    assert parse_color([1, 2, 3]) == (1, 2, 3, 255)
    with pytest.raises(ValueError):
        parse_color("$nope", {})
    with pytest.raises(ValueError):
        parse_color("#12")


def test_svg_path_parser():
    subs = geo.parse_svg_path("M0 0H10V10H0Z")
    assert len(subs) == 1 and subs[0][1] is True
    assert geo.bbox_of([subs[0][0]]) == (0, 0, 10, 10)
    rel = geo.parse_svg_path("m5 5 l10 0 0 10 z")
    assert geo.bbox_of([rel[0][0]]) == (5, 5, 15, 15)
    arc = geo.parse_svg_path("M0 10A10 10 0 0 1 20 10")
    x0, y0, x1, y1 = geo.bbox_of([arc[0][0]])
    assert abs(y0 - 0) < 0.05 and abs(x1 - 20) < 1e-6
    with pytest.raises(ValueError):
        geo.parse_svg_path("")


def test_anchor_parsing():
    assert parse_anchor("center") == ("center", "middle")
    assert parse_anchor("top-center") == ("center", "top")
    assert parse_anchor("middle-left") == ("left", "middle")
    assert parse_anchor("center-right") == ("right", "middle")
    assert parse_anchor("bottom-right") == ("right", "bottom")
    with pytest.raises(ValueError):
        parse_anchor("sideways")


# ---------------------------------------------------------------- drawing

def test_rect_pixels_and_transparency(ws):
    s.create_canvas("a", 40, 40)
    s.add_rect("a", 10, 10, 20, 20, fill="#ff0000")
    px = render("a")
    assert tuple(px[20, 20]) == (255, 0, 0, 255)
    assert px[2, 2][3] == 0
    assert tuple(px[10, 10]) == (255, 0, 0, 255)   # sharp corner is filled
    assert px[9, 9][3] == 0


def test_rounded_rect_is_antialiased(ws):
    s.create_canvas("a", 40, 40)
    s.add_rect("a", 4, 4, 32, 32, radius=12, fill="#ffffff")
    px = render("a")
    assert px[4, 4][3] == 0                       # corner cut away
    assert px[20, 20][3] == 255
    alphas = set(px[..., 3].flatten().tolist())
    assert any(0 < a < 255 for a in alphas)       # soft edge pixels exist


def test_stroke_alignment(ws):
    s.create_canvas("a", 60, 60)
    s.add_rect("a", 20, 20, 20, 20, fill="none", stroke={"color": "#00ff00", "width": 4, "align": "inside"}, id="in")
    px = render("a")
    assert px[21, 21][3] == 255 and px[30, 30][3] == 0 and px[18, 30][3] == 0
    s.update_element("a", "in", {"stroke": {"color": "#00ff00", "width": 4, "align": "outside"}})
    px = render("a")
    assert px[18, 30][3] == 255 and px[21, 30][3] == 0


def test_gradient_direction(ws):
    s.create_canvas("a", 100, 20)
    s.add_rect("a", 0, 0, 100, 20, fill={"type": "linear", "angle": 0, "stops": [[0, "#000000"], [1, "#ffffff"]]})
    px = render("a")
    assert px[10, 2][0] < 20 and px[10, 97][0] > 235
    assert abs(int(px[10, 50][0]) - 128) < 6


def test_shadow_extends_outside_shape(ws):
    s.create_canvas("a", 80, 80)
    s.add_rect("a", 20, 20, 40, 30, fill="#ffffff", style={"shadow": {"color": "#000000", "blur": 8, "dy": 8}})
    px = render("a")
    assert px[58, 40][3] > 0 and px[58, 40][0] < 40     # below the rect: dark shadow
    assert px[5, 5][3] == 0


def test_clip_masks_child(ws):
    s.create_canvas("a", 60, 30)
    s.add_ellipse("a", 30, 15, 12, id="mask", fill="#000000@0")
    s.add_rect("a", 0, 0, 60, 30, fill="#ff0000", clip="mask")
    px = render("a")
    assert px[15, 30][0] == 255 and px[15, 30][3] == 255
    assert px[15, 3][3] == 0


def test_text_anchor_center_and_measure(ws):
    s.create_canvas("a", 200, 60)
    r = s.add_text("a", "PLAY", 100, 30, size=28, weight="bold", anchor="center", color="#ffffff")
    x, y, w, h = r["bbox_xywh"]
    assert abs((x + w / 2) - 100) < 1.0
    px = render("a")
    ys, xs = np.nonzero(px[..., 3] > 128)
    assert abs((xs.min() + xs.max()) / 2 - 100) < 2.5
    assert abs((ys.min() + ys.max()) / 2 - 30) < 2.5        # optically centred on cap height
    m = s.measure_text("PLAY", size=28, weight="bold")
    assert abs(m["width"] - w) < 1.0


def test_text_wrap_fit_and_turkish_upper(ws):
    s.create_canvas("a", 300, 200)
    r = s.add_text("a", "one two three four five six seven", 10, 10, size=20, max_width=120, id="wrap")
    assert r["bbox_xywh"][2] <= 121 and r["bbox_xywh"][3] > 40
    r2 = s.add_text("a", "A very long button label", 10, 150, size=40, fit_width=100, id="fit")
    assert r2["bbox_xywh"][2] <= 101
    m = s.measure_text("ilk ışık", uppercase="tr")
    assert m["lines"][0]["text"] == "İLK IŞIK"


def test_missing_glyph_warning(ws):
    s.create_canvas("a", 100, 40)
    r = s.add_text("a", "Ğ", 5, 5, font="Orbitron")
    assert "warnings" in r


def test_every_icon_renders(ws):
    s.create_canvas("a", 48, 48)
    for name in icon_names():
        for variant in ("line", "solid"):
            r = s.add_icon("a", name, 0, 0, 48, variant=variant, id="i")
            assert r["bbox_xywh"] == [0, 0, 48, 48]
            assert render("a")[..., 3].max() == 255
            s.delete_elements("a", ids=["i"])


def test_rotation_keeps_shape_area(ws):
    s.create_canvas("a", 200, 200)
    s.add_rect("a", 20, 90, 160, 20, fill="#ffffff", id="r")
    before = (render("a")[..., 3] > 128).sum()
    s.update_element("a", "r", {"rotation": 90})
    px = render("a")
    after = (px[..., 3] > 128).sum()
    assert abs(int(before) - int(after)) < 60
    assert px[30, 100][3] == 255 and px[100, 30][3] == 0     # now vertical


def test_scale_2x_doubles_resolution(ws):
    s.create_canvas("a", 50, 30)
    s.add_rect("a", 5, 5, 40, 20, radius=6, fill="#3366ff")
    assert render("a", 2.0).shape[:2] == (60, 100)


# ---------------------------------------------------------------- editing

def test_errors_do_not_leave_garbage(ws):
    s.create_canvas("a", 50, 50)
    with pytest.raises(ValueError):
        s.add_rect("a", 0, 0, 10, 10, fill="#zzz")
    with pytest.raises(ValueError):
        s.add_text("a", "x", 0, 0, font="NoSuchFontFamily123")
    with pytest.raises(ValueError):
        s.add_rect("a", 0, 0, 10, 10, clip="ghost")
    assert s.get_canvas("a")["elements"] == []
    with pytest.raises(ValueError):
        s.get_canvas("missing")


def test_batch_is_atomic(ws):
    s.create_canvas("a", 50, 50)
    s.add_rect("a", 0, 0, 10, 10, id="keep")
    with pytest.raises(ValueError) as err:
        s.batch("a", [{"op": "add_rect", "x": 0, "y": 0, "width": 5, "height": 5, "id": "one"},
                      {"op": "add_icon", "name": "not-an-icon", "x": 0, "y": 0}])
    assert "operation 1" in str(err.value)
    assert [e["id"] for e in s.get_canvas("a")["elements"]] == ["keep"]


def test_update_duplicate_move_reorder_undo(ws):
    s.create_canvas("a", 200, 100)
    s.set_theme(colors={"primary": "#ff00ff"}, styles={"slot": {"fill": "$primary", "stroke": {"color": "#000", "width": 2}}})
    s.add_rect("a", 10, 10, 30, 30, style="slot", id="s1")
    s.update_element("a", "s1", {"style": {"glow": {"color": "#fff", "blur": 4}}})
    props = s.get_canvas("a", details=True)["elements"][0]["props"]
    assert props["style"]["use"] == "slot" and "glow" in props["style"]   # named base is kept
    d = s.duplicate_element("a", "s1", dx=40, new_id="s2", props={"fill": "#00ff00"})
    assert d["bbox_xywh"][0] == 50
    assert tuple(render("a")[25, 65][:3]) == (0, 255, 0)
    s.move_elements("a", 0, 20, ids=["s1", "s2"])
    assert s.get_canvas("a", ids=["s2"])["elements"][0]["bbox_xywh"][1] == 30
    assert s.reorder_element("a", "s2", "back")["order"] == ["s2", "s1"]
    assert s.reorder_element("a", "s2", "above:s1")["order"] == ["s1", "s2"]
    assert s.undo("a")["undone"] is True
    assert [e["id"] for e in s.get_canvas("a")["elements"]] == ["s2", "s1"]


def test_align_and_distribute(ws):
    s.create_canvas("a", 300, 100)
    for i, x in enumerate((3, 40, 200)):
        s.add_rect("a", x, 10 + i * 7, 20, 20, id=f"r{i}")
    out = s.align_elements("a", ["r0", "r1", "r2"], align="center-y", distribute="horizontal", gap=10)
    boxes = {e["id"]: e["bbox_xywh"] for e in out["elements"]}
    assert all(b[1] == 40 for b in boxes.values())
    assert [boxes[k][0] for k in ("r0", "r1", "r2")] == [3, 33, 63]
    s.align_elements("a", ["r0"], align="center")
    assert s.get_canvas("a", ids=["r0"])["elements"][0]["bbox_xywh"][:2] == [140, 40]


def test_theme_restyles_and_rejects_bad_values(ws):
    s.create_canvas("a", 20, 20)
    s.set_theme(colors={"c": "#ff0000"})
    s.add_rect("a", 0, 0, 20, 20, fill="$c")
    assert tuple(render("a")[10, 10][:3]) == (255, 0, 0)
    s.set_theme(colors={"c": "#0000ff"})
    assert tuple(render("a")[10, 10][:3]) == (0, 0, 255)
    with pytest.raises(ValueError):
        s.set_theme(colors={"bad": "#nothex"})
    assert "bad" not in s.get_theme()["colors"]


def test_persistence_between_sessions(ws):
    s.create_canvas("keepme", 30, 30)
    s.add_rect("keepme", 1, 1, 10, 10, id="x")
    s.set_theme(colors={"k": "#123456"})
    s.set_store(Store(ws / "ws"))
    assert s.get_canvas("keepme")["elements"][0]["id"] == "x"
    assert s.get_theme()["colors"]["k"] == "#123456"


# ---------------------------------------------------------------- preview / export

def test_preview_returns_png(ws):
    s.create_canvas("a", 64, 64)
    s.add_ellipse("a", 32, 32, 20, fill="#ff0000", id="dot")
    image, info = s.preview("a", show_ids=True, background="game")
    assert image.data[:8] == b"\x89PNG\r\n\x1a\n"
    meta = json.loads(info)
    assert meta["shown_scale"] == 4.0 and meta["image_size"] == [256, 256]
    sheet, _ = s.contact_sheet()
    assert sheet.data[:4] == b"\x89PNG"


def test_export_png_scales_and_trim(ws):
    s.create_canvas("a", 100, 100)
    s.add_rect("a", 30, 40, 20, 10, fill="#ffffff")
    out = s.export_png("a", "a.png", scales=[1, 2], trim=True)
    one, two = out["files"]
    assert (one["width"], one["height"]) == (20, 10) and one["trim_offset"] == [30, 40]
    assert (two["width"], two["height"]) == (40, 20) and two["file"].endswith("a_2x.png")
    assert Image.open(one["file"]).mode == "RGBA"
    with pytest.raises(ValueError):
        s.export_png("a", "a.jpg")


def test_nine_slice_auto_and_metadata(ws):
    s.create_canvas("panel", 120, 80)
    s.add_rect("panel", 0, 0, 120, 80, radius=16, fill="#223344", stroke={"color": "#ffffff", "width": 2})
    out = s.export_asset("panel", engine="generic", out_dir=str(ws / "out"), nine_slice="auto")
    f = out["files"][0]
    b = f["nine_slice"]
    assert 16 <= b["left"] <= 19 and 16 <= b["top"] <= 19 and b["left"] == b["right"]
    assert f["width"] == b["left"] + b["right"] + 4 and f["height"] == b["top"] + b["bottom"] + 4   # minimised
    assert f["unity"]["spriteBorder_LBRT"] == [b["left"], b["bottom"], b["right"], b["top"]]
    assert f["roblox"]["SliceCenter"] == [b["left"], b["top"], f["width"] - b["right"], f["height"] - b["bottom"]]
    assert abs(f["unreal"]["margin_LTRB"][0] - b["left"] / f["width"]) < 1e-4
    side = json.loads((ws / "out" / "panel.gameui.json").read_text())
    assert side["has_border"] is True and side["border_left"] == b["left"]
    manifest = json.loads((ws / "out" / "game_ui_manifest.json").read_text())
    assert "panel" in manifest["assets"]


def test_nine_slice_reports_gradient_axis(ws):
    s.create_canvas("btn", 120, 40)
    s.add_rect("btn", 0, 0, 120, 40, radius=8, fill={"type": "linear", "angle": 90, "stops": ["#ffffff", "#000000"]})
    out = s.export_asset("btn", out_dir=str(ws / "out"), nine_slice="auto")
    assert "horizontally" in out["notes"][0]
    assert out["files"][0]["height"] == 40          # vertical axis left untouched
    with pytest.raises(ValueError):
        s.export_asset("btn", out_dir=str(ws / "out"), nine_slice={"left": 80, "top": 4, "right": 80, "bottom": 4})


def test_minimized_nine_slice_reconstructs_original():
    img = Image.new("RGBA", (60, 40), (0, 0, 0, 0))
    px = img.load()
    for y in range(40):
        for x in range(60):
            edge = min(x, y, 59 - x, 39 - y)
            px[x, y] = (255, 255, 255, 255) if edge < 3 else (20, 30, 40, 255)
    borders = ex.detect_nine_slice(img)
    assert borders["left"] == 4 and borders["top"] == 4
    small, b = ex.minimize_nine_slice(img, borders)
    assert small.size == (12, 12)
    assert small.getpixel((0, 0)) == (255, 255, 255, 255) and small.getpixel((6, 6)) == (20, 30, 40, 255)


def test_atlas_and_engine_helpers(ws):
    for i, size in enumerate((32, 48, 64)):
        s.create_canvas(f"ic{i}", size, size)
        s.add_icon(f"ic{i}", "star", 0, 0, size, variant="solid")
    out = s.export_atlas(["ic0", "ic1", "ic2"], str(ws / "out" / "icons.png"))
    frames = out["frames"]
    assert set(frames) == {"ic0", "ic1", "ic2"} and frames["ic2"]["w"] == 64
    atlas = Image.open(out["file"])
    boxes = [(f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]) for f in frames.values()]
    for i, a in enumerate(boxes):
        assert a[2] <= atlas.width and a[3] <= atlas.height
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]      # no overlap

    out_dir = ws / "rb"
    s.create_canvas("panel", 64, 64)
    s.add_rect("panel", 0, 0, 64, 64, radius=10, fill="#111111")
    s.export_asset("panel", engine="roblox", out_dir=str(out_dir), nine_slice="auto")
    res = s.write_engine_helpers("roblox", str(out_dir), roblox_ids={"panel": 123456})
    lua = Path(res["file"]).read_text()
    assert 'id = "rbxassetid://123456"' in lua and "Rect.new(" in lua and res["assets_without_id"] == []
    s.export_asset("panel", engine="roblox", out_dir=str(out_dir), nine_slice="auto")       # re-export keeps the id
    assert json.loads((out_dir / "game_ui_manifest.json").read_text())["assets"]["panel"]["roblox_id"] == "123456"

    unity = s.write_engine_helpers("unity", str(ws / "Assets" / "Editor"))
    assert "AssetPostprocessor" in Path(unity["file"]).read_text()
    with pytest.raises(ValueError):
        s.write_engine_helpers("unity", str(ws / "Assets" / "Scripts"))
    unreal = s.write_engine_helpers("unreal", str(out_dir), unreal_content_path="/Game/UI/Kit")
    assert 'DEST_PATH = "/Game/UI/Kit"' in Path(unreal["file"]).read_text()


def test_image_element_roundtrip(ws):
    s.create_canvas("src", 20, 20)
    s.add_rect("src", 0, 0, 20, 20, fill="#00ff00")
    path = s.export_png("src", str(ws / "green.png"))["files"][0]["file"]
    s.create_canvas("dst", 60, 60)
    s.add_image("dst", path, 10, 10, width=40, radius=20)
    px = render("dst")
    assert tuple(px[30, 30]) == (0, 255, 0, 255)
    assert px[11, 11][3] == 0                         # rounded corner removed
    with pytest.raises(ValueError):
        s.add_image("dst", str(ws / "nope.png"), 0, 0)


def test_slow_gradient_is_not_mistaken_for_a_stretchable_centre(ws):
    s.create_canvas("g", 200, 200)
    s.add_rect("g", 0, 0, 200, 200, radius=10,
               fill={"type": "linear", "angle": 90, "stops": ["#202030", "#1a1a28"]})
    out = s.export_asset("g", out_dir=str(ws / "out"), nine_slice="auto")
    assert out["files"][0]["height"] == 200            # rows were not collapsed
    assert out["files"][0]["width"] < 200              # columns still are
    assert "horizontally" in out["notes"][0]


def test_pixel_aligned_shapes_do_not_bleed(ws):
    s.create_canvas("a", 40, 40)
    s.add_rect("a", 10, 12, 20, 10, fill="#ffffff", stroke={"color": "#ff0000", "width": 2})
    alpha = render("a")[..., 3]
    assert int((alpha > 0).sum()) == 200 and int((alpha == 255).sum()) == 200


# ---------------------------------------------------------------- realistic / gauge features

def test_arc_covers_only_its_sweep(ws):
    s.create_canvas("a", 200, 200)
    s.add_arc("a", 100, 100, 80, 180, 360, thickness=10, color="#ffffff")     # top half ring
    alpha = render("a")[..., 3]
    assert alpha[20, 100] == 255                 # 12 o'clock is on the band
    assert alpha[180, 100] == 0                  # 6 o'clock is not
    assert alpha[100, 100] == 0                  # centre is empty
    assert alpha[102, 20] == 0 and alpha[98, 20] == 255      # flat end exactly at the start angle


def test_dashed_butt_line_leaves_clean_gaps(ws):
    s.create_canvas("a", 100, 20)
    s.add_line("a", [[0, 10], [100, 10]], width=10, cap="butt", dash=[10, 10], color="#ffffff")
    row = render("a")[10, :, 3]
    assert row[5] == 255 and row[15] == 0 and row[25] == 255 and row[35] == 0
    assert int((row == 255).sum()) == 50


def test_ticks_with_labels_form_one_group(ws):
    s.create_canvas("a", 300, 300)
    out = s.add_ticks("a", 150, 150, 120, 135, 405, count=9, major_every=4, labels=["0", "50", "100"], id="spd")
    assert out["group"] == "spd"
    assert out["created"] == ["spd_minor", "spd_major", "spd_label0", "spd_label1", "spd_label2"]
    alpha = render("a")[..., 3]
    assert alpha[35, 150] == 255                 # the middle major tick points straight up
    assert alpha[150, 150] == 0
    with pytest.raises(ValueError):
        s.add_ticks("a", 150, 150, 120, 135, 405, count=9, major_every=4, labels=["only", "two"])
    assert len(s.get_canvas("a")["elements"]) == 5            # failed call left nothing behind
    s.move_elements("a", 10, 0, group="spd")
    assert s.get_canvas("a", ids=["spd_label1"])["elements"][0]["bbox_xywh"][0] > 150


def test_rotation_around_origin_pivot(ws):
    s.create_canvas("a", 200, 200)
    s.add_rect("a", 100, 98, 80, 4, fill="#ff0000", origin=[100, 100], rotation=90, id="needle")
    alpha = render("a")[..., 3]
    assert alpha[150, 100] == 255                # now points down from the pivot
    assert alpha[100, 150] == 0
    box = s.get_canvas("a")["elements"][0]["bbox_xywh"]
    assert abs(box[0] - 98) < 0.6 and abs(box[1] - 100) < 0.6 and abs(box[3] - 80) < 0.6


def test_skew_makes_a_parallelogram(ws):
    s.create_canvas("a", 200, 100)
    s.add_rect("a", 50, 20, 100, 60, fill="#ffffff", skew=20)
    alpha = render("a")[..., 3]
    top, bottom = np.nonzero(alpha[22] == 255)[0], np.nonzero(alpha[78] == 255)[0]
    assert top.min() > bottom.min() + 15 and top.max() > bottom.max() + 15      # top edge shifted right
    assert abs(len(top) - len(bottom)) <= 1


def test_conic_gradient_and_textures(ws):
    s.create_canvas("a", 100, 100)
    s.add_rect("a", 0, 0, 100, 100, fill={"type": "conic", "stops": [[0, "#000000"], [1, "#ffffff"]], "dither": False})
    px = render("a")
    assert px[50, 90][0] < 20 or px[50, 90][0] > 235          # seam sits at 3 o'clock
    assert abs(int(px[90, 50][0]) - 64) < 8                   # 6 o'clock = a quarter of the way round
    assert abs(int(px[50, 10][0]) - 128) < 8                  # 9 o'clock = half way
    for kind in ("carbon", "brushed"):
        s.create_canvas(kind, 64, 64)
        s.add_rect(kind, 0, 0, 64, 64, fill={"type": kind, "color": "#888888", "color2": "#222222"})
        values = render(kind)[..., 0]
        assert values.min() >= 0x22 and values.max() <= 0x88 and values.std() > 3


def test_italic_real_and_faux(ws):
    s.create_canvas("a", 300, 120)
    real = s.add_text("a", "187", 10, 10, size=60, font="Barlow Condensed", weight=700, italic=True)
    faux = s.add_text("a", "187", 10, 60, size=40, font="Inter", weight=700, italic=True)
    upright = s.measure_text("187", size=40, font="Inter", weight=700)
    assert "warnings" not in real
    assert faux["bbox_xywh"][2] > upright["width"] + 2        # slanted box is wider


def test_blur_and_blend_modes(ws):
    s.create_canvas("a", 100, 100, "#808080")
    s.add_rect("a", 40, 40, 20, 20, fill="#ffffff", style={"blur": 12})
    px = render("a", background=True)
    assert px[50, 50][0] > px[50, 64][0] > 0x80 and px[50, 50][0] < 255       # light spreads past the shape, softened
    for mode, expected in (("multiply", 0x40), ("screen", 0xC0)):
        s.create_canvas(mode, 10, 10, "#808080")
        s.add_rect(mode, 0, 0, 10, 10, fill="#808080", style={"blend": mode})
        assert abs(int(render(mode, background=True)[5, 5][0]) - expected) <= 2
    with pytest.raises(ValueError):
        s.add_rect("a", 0, 0, 5, 5, style={"blend": "dissolve"})


def test_export_layers_writes_parts_and_layout(ws):
    s.create_canvas("gauge", 200, 200)
    s.add_ellipse("gauge", 100, 100, 90, fill="#111111", group="dial")
    s.add_rect("gauge", 100, 98, 70, 4, fill="#ff0000", group="needle", origin=[100, 100])
    s.add_text("gauge", "x", 5, 5, size=10)
    out = s.export_layers("gauge", out_dir=str(ws / "layers"))
    layers = {layer["name"]: layer for layer in out["layers"]}
    assert set(layers) == {"dial", "needle", "base"}
    assert layers["needle"]["origin"] == [100, 100]
    assert layers["needle"]["x"] == 98 and layers["needle"]["width"] == 74        # trimmed with 2px padding
    needle = Image.open(ws / "layers" / layers["needle"]["file"])
    assert needle.size == (74, 8)
    layout = json.loads(Path(out["layout"]).read_text())
    assert layout["canvas"]["width"] == 200 and [l["name"] for l in layout["layers"]] == ["dial", "needle", "base"]
    with pytest.raises(ValueError):
        s.export_layers("gauge", out_dir=str(ws / "layers"), groups=["ghost"])


def test_batch_supports_arc_and_ticks(ws):
    s.create_canvas("a", 200, 200)
    out = s.batch("a", [
        {"op": "add_arc", "cx": 100, "cy": 100, "radius": 80, "start_angle": 135, "end_angle": 405, "id": "track"},
        {"op": "add_ticks", "cx": 100, "cy": 100, "radius": 70, "start_angle": 135, "end_angle": 405, "count": 5,
         "id": "t"},
    ])
    assert out["results"][0]["id"] == "track" and out["results"][1]["created"] == ["t_minor"]
    assert s.undo("a")["elements"] == []


def test_conic_on_partial_arc_is_centred_on_the_circle(ws):
    conic = {"type": "conic", "from": 180, "to": 360, "stops": [[0, "#000000"], [1, "#ffffff"]], "dither": False}
    s.create_canvas("a", 200, 200)
    s.add_arc("a", 100, 100, 80, 180, 225, thickness=20, color=conic, id="arc")       # first quarter of the sweep
    start = render("a")[100 - 14, 100 - 79]
    assert start[3] == 255 and start[0] < 40           # near the start of the sweep: still dark
    s.update_element("a", "arc", {"end_angle": 360})
    px = render("a")
    assert abs(int(px[100 - 14, 100 - 79][0]) - int(start[0])) <= 2     # same colour whatever the sweep length
    assert abs(int(px[20, 100][0]) - 128) < 8                           # 12 o'clock is half way


# ---------------------------------------------------------------- animation

from game_ui_mcp import animation as am


def test_easing_endpoints_and_shapes():
    for style in am.STYLES:
        for direction in ("in", "out", "in-out"):
            name = f"{style}-{direction}"
            assert abs(am.ease(name, 0)) < 1e-9 and abs(am.ease(name, 1) - 1) < 1e-9, name
    assert am.ease("quad-in", 0.5) == 0.25 and am.ease("quad-out", 0.5) == 0.75
    assert am.ease("back-out", 0.6) > 1                         # overshoots
    assert am.ease("step", 0.99) == 0 and am.ease("step", 1) == 1
    assert abs(am.ease([0, 0, 1, 1], 0.3) - 0.3) < 1e-6          # linear cubic-bezier
    assert am.ease_name("OutBack") == "back-out" and am.ease_name("InOutQuad") == "quad-in-out"
    assert am.ease_name("ease-out") == "cubic-out" and am.ease_name("in-expo") == "expo-in"
    assert am.engine_ease("expo-out") == {"roblox": {"EasingStyle": "Exponential", "EasingDirection": "Out"},
                                          "dotween": "OutExpo"}
    with pytest.raises(ValueError):
        am.parse_ease("wobbly")


def test_value_interpolation():
    assert am.lerp_value(10, 20, 0.25, {}) == 12.5
    assert am.lerp_value([0, 10], [10, 30], 0.5, {}) == [5, 20]
    assert am.lerp_value("#000000", "#ffffff", 0.5, {}, True) == "#808080"
    assert am.lerp_value("#ff0000@0", "#ff0000", 0.5, {}, True) == "#ff000080"      # fade keeps the hue
    assert am.lerp_value("a", "b", 0.5, {}) == "a" and am.lerp_value("a", "b", 1, {}) == "b"
    grad = am.lerp_value({"type": "linear", "angle": 0, "stops": [[0, "#000000"], [1, "#000000"]]},
                         {"type": "linear", "angle": 90, "stops": [[0, "#ffffff"], [1, "#000000"]]}, 0.5, {}, True)
    assert grad["angle"] == 45 and grad["stops"][0] == [0, "#808080"]


def test_keyframes_move_scale_and_fade(ws):
    s.create_canvas("a", 200, 100)
    s.add_rect("a", 20, 40, 20, 20, fill="#ffffff", id="box")
    s.create_animation("a", "slide", 1.0)
    out = s.set_keyframes("a", "slide", "box", "dx", [[0, 0], [1, 100]])
    assert out["elements"] == ["box"] and out["keyframes"][1] == {"t": 1.0, "value": 100}
    s.set_keyframes("a", "slide", "box", "opacity", [{"t": 0, "value": 1}, {"t": 1, "value": 0.5}])
    st = s.store()
    c = st.get("a")
    _, anim = st.get_animation(c, "slide")

    def frame(t):
        posed, _ = am.apply_animation(c, anim, t, {})
        return np.asarray(st.renderer(posed).render())

    assert frame(0)[50, 30][3] == 255 and frame(0)[50, 80][3] == 0
    half = frame(0.5)
    assert half[50, 80][3] == 191 and half[50, 30][3] == 0          # moved 50 px, 75% opaque
    assert frame(1)[50, 130][3] == 128
    assert render("a")[50, 30][3] == 255                             # the canvas itself is untouched

    s.create_animation("a", "pop", 0.5)
    s.set_keyframes("a", "pop", "box", "scale", [[0, 1], [0.5, 2, "linear"]])
    _, pop = st.get_animation(c, "pop")
    posed, _ = am.apply_animation(c, pop, 0.5, {})
    alpha = np.asarray(st.renderer(posed).render())[..., 3]
    assert int((alpha == 255).sum()) == 1600                         # 40x40, grown around its own centre
    assert alpha[31, 11] == 255 and alpha[69, 49] == 255 and alpha[50, 9] == 0


def test_group_target_pivot_and_offset(ws):
    s.create_canvas("a", 200, 200)
    s.add_rect("a", 100, 98, 60, 4, fill="#ff0000", group="hand", id="h1")
    s.create_animation("a", "spin", 2.0, loop=True)
    out = s.set_keyframes("a", "spin", "group:hand", "rotation", [[0, 0], [1, 90]], pivot=[100, 100], offset=0.5)
    assert [k["t"] for k in out["keyframes"]] == [0.5, 1.5]
    image, info = s.preview("a", animation="spin", time=1.5)
    assert json.loads(info)["time"] == 1.5
    st = s.store()
    posed, _ = am.apply_animation(st.get("a"), st.get_animation(st.get("a"), "spin")[1], 1.5, {})
    alpha = np.asarray(st.renderer(posed).render())[..., 3]
    assert alpha[140, 100] == 255 and alpha[100, 140] == 0           # swung down around the pivot


def test_animation_validation_and_cleanup(ws):
    s.create_canvas("a", 100, 100)
    s.add_rect("a", 10, 10, 20, 20, id="r")
    with pytest.raises(ValueError):
        s.set_keyframes("a", "nope", "r", "dx", [[0, 0]])
    s.create_animation("a", "x", 1.0)
    with pytest.raises(ValueError):
        s.set_keyframes("a", "x", "ghost", "dx", [[0, 0], [1, 5]])
    with pytest.raises(ValueError):
        s.set_keyframes("a", "x", "r", "dx", [[0, 0], [2, 5]])              # past the end
    with pytest.raises(ValueError):
        s.set_keyframes("a", "x", "r", "style.fill", [[0, "#fff"], [1, "#notacolor"]])
    with pytest.raises(ValueError):
        s.set_keyframes("a", "x", "r", "dx", [[0, 0], [1, 5, "wobble"]])
    assert s.get_animations("a")["animations"][0]["tracks"] == []
    s.set_keyframes("a", "x", "r", "width", [[0, 0], [1, 20]])               # collapsing to zero is allowed
    s.set_keyframes("a", "x", "r", "dx", [[0, 0], [1, 5]])
    assert s.delete_keyframes("a", "x", target="r", property="dx")["tracks_left"] == 1
    with pytest.raises(ValueError):
        s.create_animation("a", "x", 0.5)                                    # shorter than its keyframes
    s.duplicate_canvas("a", "b")
    assert len(s.get_animations("b", "x")["tracks"]) == 1                    # animations are copied
    s.delete_elements("a", ids=["r"])
    assert s.get_animations("a", "x")["tracks"] == []                        # dead tracks are pruned
    assert s.delete_animation("a", "x")["animations_left"] == []


def test_preview_and_export_animation(ws):
    s.create_canvas("a", 64, 64)
    s.add_ellipse("a", 16, 32, 8, fill="#00ff00", id="dot")
    s.create_animation("a", "bounce", 0.5, loop=True, fps=10)
    s.set_keyframes("a", "bounce", "dot", "dx", [[0, 0], [0.25, 32, "quad-out"], [0.5, 0, "quad-in"]])
    image, info = s.preview_animation("a", "bounce", frames=4)
    meta = json.loads(info)
    assert image.data[:4] == b"\x89PNG" and len(meta["times"]) == 4 and meta["times"][-1] < 0.5

    sheet = s.export_animation("a", "bounce", format="spritesheet", path=str(ws / "o" / "b.png"))
    fw, fh = sheet["frame_size"]
    assert sheet["frames"] == 5 and sheet["columns"] == 3
    assert fh == 16 and 44 <= fw <= 48                 # trimmed to the area the dot sweeps at the sampled times
    data = json.loads(Path(sheet["json"]).read_text())
    assert data["offset"] == [8.0, 24.0] and data["loop"] is True and data["fps"] == 10
    assert Image.open(sheet["file"]).size == (fw * 3, 32)

    frames = s.export_animation("a", "bounce", format="frames", path=str(ws / "o" / "seq"), trim=False)
    assert len(list((ws / "o" / "seq").glob("*.png"))) == 5 and frames["frame_size"] == [64, 64]

    for fmt, ext in (("gif", "gif"), ("apng", "png"), ("webp", "webp")):
        out = s.export_animation("a", "bounce", format=fmt, path=str(ws / "o" / f"b_anim.{ext}"))
        im = Image.open(out["file"])
        assert getattr(im, "n_frames", 1) in (4, 5), fmt      # writers may merge two identical neighbours

    js = s.export_animation("a", "bounce", format="json", path=str(ws / "o" / "b.anim.json"))
    desc = json.loads(Path(js["file"]).read_text())
    key = desc["tracks"][0]["keyframes"][1]
    assert key["ease"] == "quad-out" and key["roblox"]["EasingStyle"] == "Quad" and key["dotween"] == "OutQuad"
    assert desc["tracks"][0]["layers"] == ["base"]
    with pytest.raises(ValueError):
        s.export_animation("a", "bounce", format="mp4")


def test_batch_animation_ops_and_undo(ws):
    s.create_canvas("a", 50, 50)
    out = s.batch("a", [
        {"op": "add_rect", "id": "r", "x": 5, "y": 5, "width": 10, "height": 10},
        {"op": "create_animation", "name": "go", "duration": 0.3},
        {"op": "set_keyframes", "animation": "go", "target": "r", "property": "dx", "keyframes": [[0, 0], [0.3, 20]]},
    ])
    assert out["results"][1]["animation"] == "go" and out["results"][2]["property"] == "dx"
    assert s.undo("a")["undone"] and s.get_animations("a")["animations"] == []


# ---------------------------------------------------------------- quality tools: strokes, light, masks, fills

from game_ui_mcp import effects as fxlib


def test_round_dilation_and_float_blur():
    dot = np.zeros((21, 21), dtype=np.uint8)
    dot[10, 10] = 255
    grown = fxlib.dilate(dot, 3)
    assert int((grown == 255).sum()) == 29                    # every pixel within 3 px: a disc, not a square
    assert grown[10, 13] == 255 and grown[13, 13] == 0 and np.array_equal(grown, grown.T)
    assert np.array_equal(fxlib.erode(fxlib.dilate(dot, 2), 2), dot)
    field = np.zeros((40, 40))
    field[10:30, 10:30] = 1.0
    soft = fxlib.blur_float(field, 6)
    assert abs(soft.sum() - field.sum()) < 1e-6 and 0.4 < soft[10, 20] < 0.6


def test_multiple_strokes_with_offset(ws):
    s.create_canvas("a", 100, 100)
    s.add_rect("a", 30, 30, 40, 40, fill="#0000ff", id="r",
               stroke=[{"color": "#ff0000", "width": 4, "align": "outside"},
                       {"color": "#00ff00", "width": 3, "align": "outside", "offset": 6}])
    px = render("a")
    assert tuple(px[50, 50][:3]) == (0, 0, 255)
    assert tuple(px[50, 28]) == (255, 0, 0, 255)               # 0-4 px outside: red
    assert px[50, 25][3] == 0                                  # 4-6 px: the gap
    assert tuple(px[50, 22]) == (0, 255, 0, 255)               # 6-9 px: green
    assert px[50, 19][3] == 0
    assert s.get_canvas("a")["elements"][0]["bbox_xywh"] == [30, 30, 40, 40]


def test_text_double_outline(ws):
    s.create_canvas("a", 200, 80)
    s.add_text("a", "GO", 100, 40, size=40, weight="bold", anchor="center", color="#ffffff",
               stroke=[{"color": "#ff0000", "width": 3}, {"color": "#0000ff", "width": 3, "offset": 3}])
    px = render("a")
    colors = {tuple(c) for c in px[px[..., 3] == 255][:, :3].tolist()}
    assert (255, 255, 255) in colors and (255, 0, 0) in colors and (0, 0, 255) in colors


def test_light_shades_towards_and_away_from_the_source(ws):
    s.create_canvas("a", 120, 120)
    s.add_rect("a", 20, 20, 80, 80, radius=10, fill="#808080",
               style={"light": {"angle": 225, "size": 8, "depth": 1.5}})        # light from the top-left
    px = render("a").astype(int)
    assert px[60, 23][0] > 0x80 + 20 and px[23, 60][0] > 0x80 + 20              # left and top edges catch light
    assert px[60, 97][0] < 0x80 - 20 and px[97, 60][0] < 0x80 - 20              # right and bottom fall away
    assert abs(px[60, 60][0] - 0x80) <= 2                                        # the flat middle is untouched
    assert px[60, 60][3] == 255 and px[5, 5][3] == 0
    with pytest.raises(ValueError):
        s.add_rect("a", 0, 0, 10, 10, style={"light": {"profile": "wavy"}})


def test_mask_fades_and_adjust_recolours(ws):
    s.create_canvas("a", 100, 20)
    s.add_rect("a", 0, 0, 100, 20, fill="#ff0000",
               style={"mask": {"type": "linear", "angle": 0, "stops": ["#000000", "#000000@0"], "dither": False}})
    alpha = render("a")[10, :, 3]
    assert alpha[2] > 245 and abs(int(alpha[50]) - 128) < 6 and alpha[97] < 10
    s.create_canvas("b", 20, 20)
    s.add_rect("b", 0, 0, 20, 20, fill="#cc3311", style={"adjust": {"saturation": -1}}, id="g")
    r, g, b, _ = render("b")[10, 10]
    assert r == g == b
    s.update_element("b", "g", {"style": {"adjust": {"brightness": 0.2}}})
    assert render("b")[10, 10][0] == 0xCC + 51


def test_gradient_spaces_and_new_fills(ws):
    def mid(space):
        s.create_canvas(space, 100, 10)
        s.add_rect(space, 0, 0, 100, 10, fill={"type": "linear", "angle": 0, "space": space, "dither": False,
                                               "stops": ["#0000ff", "#ffff00"]})
        px = render(space)
        return px[5, 0], px[5, 50], px[5, 99]

    mids = {}
    for space in ("srgb", "linear", "oklab"):
        first, mids[space], last = mid(space)
        assert first[2] > 240 and first[0] < 30 and last[0] > 240 and last[2] < 30      # both ends keep their colour
    srgb_mid, oklab_mid = mids["srgb"].astype(int), mids["oklab"].astype(int)
    assert abs(int(srgb_mid[0]) - int(srgb_mid[2])) < 12                                 # sRGB passes through grey
    assert max(oklab_mid[:3]) - min(oklab_mid[:3]) > 60                                  # OKLab keeps colour
    for kind in ("hex", "clouds"):
        s.create_canvas(kind, 64, 64)
        s.add_rect(kind, 0, 0, 64, 64, fill={"type": kind, "color": "#ffffff", "color2": "#000000", "size": 16})
        values = render(kind)[..., 0]
        assert values.min() < 40 and values.max() > 200
    with pytest.raises(ValueError):
        s.add_rect("hex", 0, 0, 5, 5, fill={"type": "linear", "space": "cmyk", "stops": ["#000", "#fff"]})


def test_image_fill_cover_and_tile(ws):
    s.create_canvas("tex", 4, 4)
    s.add_rect("tex", 0, 0, 2, 4, fill="#ff0000")
    s.add_rect("tex", 2, 0, 2, 4, fill="#0000ff")
    path = s.export_png("tex", str(ws / "tex.png"))["files"][0]["file"]
    s.create_canvas("a", 40, 20)
    s.add_ellipse("a", 20, 10, 18, 9, fill={"type": "image", "path": path, "fit": "cover"}, id="e")
    px = render("a")
    assert px[10, 8][0] > 200 and px[10, 32][2] > 200 and px[1, 1][3] == 0
    s.update_element("a", "e", {"fill": {"type": "image", "path": path, "fit": "tile"}})
    row = render("a")[10, 6:34, :3]
    reds = int((row[:, 0] > 200).sum())
    assert 10 <= reds <= 18                                    # the 4px texture repeats: about half red
    with pytest.raises(ValueError):
        s.add_rect("a", 0, 0, 5, 5, fill={"type": "image", "path": str(ws / "missing.png")})


def test_extra_blend_modes(ws):
    expected = {"darken": 0x40, "lighten": 0xC0, "difference": 0x80, "hard-light": 0x80, "color-dodge": 0xFF}
    for mode, value in expected.items():
        s.create_canvas(mode, 4, 4, "#c0c0c0")
        s.add_rect(mode, 0, 0, 4, 4, fill="#404040", style={"blend": mode})
        got = int(render(mode, background=True)[2, 2][0])
        want = value if mode not in ("hard-light", "color-dodge") else None
        if mode == "hard-light":
            want = round(2 * 0xC0 / 255 * 0x40 / 255 * 255)
        if mode == "color-dodge":
            want = min(255, round(0xC0 / 255 / (1 - 0x40 / 255) * 255))
        assert abs(got - want) <= 2, mode


# ---------------------------------------------------------------- layers

def test_layers_stack_and_active_layer(ws):
    s.create_canvas("a", 60, 60)
    s.add_rect("a", 0, 0, 60, 60, fill="#ff0000", id="early")              # before any layer: goes to base
    out = s.create_layer("a", "top")
    assert out["active_layer"] == "top"
    s.add_rect("a", 10, 10, 40, 40, fill="#00ff00", id="g")
    s.create_layer("a", "under", position="below:top")
    s.add_rect("a", 0, 0, 60, 60, fill="#0000ff", id="b")                  # added last but on a lower layer
    info = s.get_canvas("a")
    assert [l["layer"] for l in info["layers_bottom_to_top"]] == ["base", "under", "top"]
    assert info["active_layer"] == "under"
    assert {e["id"]: e.get("layer") for e in info["elements"]} == {"early": None, "g": "top", "b": "under"}
    px = render("a")
    assert tuple(px[30, 30][:3]) == (0, 255, 0) and tuple(px[3, 3][:3]) == (0, 0, 255)
    s.update_layer("a", "under", position="top")
    assert tuple(render("a")[30, 30][:3]) == (0, 0, 255)
    s.set_layer("a", "top", ids=["early"])
    assert s.get_canvas("a", ids=["early"])["elements"][0]["layer"] == "top"
    with pytest.raises(ValueError):
        s.create_layer("a", "base")
    with pytest.raises(ValueError):
        s.add_rect("a", 0, 0, 5, 5, layer="nope")
    with pytest.raises(ValueError):
        s.create_layer("a", "bad", blend="sparkle")
    assert len(s.get_canvas("a")["layers_bottom_to_top"]) == 2             # failed calls added nothing


def test_layer_opacity_treats_the_layer_as_one_image(ws):
    s.create_canvas("a", 60, 30)
    s.create_layer("a", "ghost", opacity=0.5)
    s.add_rect("a", 0, 0, 40, 30, fill="#ffffff")
    s.add_rect("a", 20, 0, 40, 30, fill="#ffffff")
    alpha = render("a")[15, :, 3]
    assert alpha[10] == 128 and alpha[30] == 128 and alpha[50] == 128      # the overlap is not doubled


def test_clipped_layer_paints_only_inside_its_base(ws):
    s.create_canvas("a", 100, 100)
    s.create_layer("a", "body")
    s.add_ellipse("a", 50, 50, 30, fill="#808080")
    s.create_layer("a", "shade", clip=True, blend="multiply")
    s.add_rect("a", 50, 0, 50, 100, fill="#808080")                        # darkens the right half only
    px = render("a")
    assert tuple(px[50, 35]) == (128, 128, 128, 255)
    assert abs(int(px[50, 65][0]) - 64) <= 1 and px[50, 65][3] == 255
    assert px[50, 90][3] == 0 and px[10, 90][3] == 0                       # nothing spills outside the body
    s.update_layer("a", "body", visible=False)
    assert render("a")[..., 3].max() == 0                                  # hiding the base hides what clips to it
    s.update_layer("a", "body", visible=True)
    image, info = s.preview("a", layers=["shade"])
    assert image.data[:4] == b"\x89PNG"


def test_layer_style_outlines_the_merged_shape(ws):
    s.create_canvas("a", 120, 80)
    s.create_layer("a", "blob", style={"stroke": {"color": "#ff0000", "width": 3, "align": "outside"}})
    s.add_ellipse("a", 45, 40, 25, fill="#ffffff")
    s.add_ellipse("a", 75, 40, 25, fill="#ffffff")
    px = render("a")
    assert tuple(px[40, 60][:3]) == (255, 255, 255)                        # where the circles overlap: no outline
    assert tuple(px[40, 18][:3]) == (255, 0, 0) and tuple(px[40, 102][:3]) == (255, 0, 0)
    assert px[40, 14][3] == 0
    s.update_layer("a", "blob", style={"stroke": None, "shadow": {"color": "#000000", "blur": 0, "dx": 6, "dy": 0}})
    px = render("a")
    assert tuple(px[40, 103]) == (0, 0, 0, 255) and tuple(px[40, 60][:3]) == (255, 255, 255)


def test_layer_rename_delete_and_undo(ws):
    s.create_canvas("a", 40, 40)
    s.create_layer("a", "one")
    s.add_rect("a", 0, 0, 10, 10, id="r1")
    s.create_layer("a", "two")
    s.add_rect("a", 20, 20, 10, 10, id="r2")
    s.update_layer("a", "one", name="first", activate=True)
    info = s.get_canvas("a")
    assert info["active_layer"] == "first" and info["elements"][0]["layer"] == "first"
    out = s.delete_layer("a", "two", keep_elements=True)
    assert out["elements_kept"] == ["r2"] and "layer" not in s.get_canvas("a", ids=["r2"])["elements"][0]
    out = s.delete_layer("a", "first")
    assert out["elements_deleted"] == ["r1"]
    assert [e["id"] for e in s.get_canvas("a")["elements"]] == ["r2"]
    assert s.undo("a")["undone"]
    assert [l["layer"] for l in s.get_canvas("a")["layers_bottom_to_top"]] == ["base", "first"]


def test_batch_layers_and_layer_animation_target(ws):
    s.create_canvas("a", 100, 40)
    s.batch("a", [
        {"op": "create_layer", "name": "back"},
        {"op": "add_rect", "id": "bg", "x": 0, "y": 0, "width": 100, "height": 40, "fill": "#000000"},
        {"op": "create_layer", "name": "front", "opacity": 0.5},
        {"op": "add_rect", "id": "a1", "x": 0, "y": 10, "width": 10, "height": 10},
        {"op": "add_rect", "id": "a2", "x": 0, "y": 25, "width": 10, "height": 10},
        {"op": "create_animation", "name": "slide", "duration": 1},
        {"op": "set_keyframes", "animation": "slide", "target": "layer:front", "property": "dx",
         "keyframes": [[0, 0], [1, 50]]},
    ])
    desc = s.get_animations("a", "slide")
    assert desc["tracks"][0]["elements"] == ["a1", "a2"]
    st = s.store()
    posed, _ = am.apply_animation(st.get("a"), st.get_animation(st.get("a"), "slide")[1], 1.0, {})
    px = np.asarray(st.renderer(posed).render())
    assert px[15, 55][0] == 128 and px[30, 55][0] == 128 and px[15, 5][0] == 0


def test_export_layers_by_layer(ws):
    s.create_canvas("a", 100, 100)
    s.create_layer("a", "body")
    s.add_ellipse("a", 50, 50, 30, fill="#808080")
    s.create_layer("a", "shade", clip=True, blend="multiply")
    s.add_rect("a", 50, 0, 50, 100, fill="#808080")
    s.create_layer("a", "badge")
    s.add_rect("a", 5, 5, 10, 10, fill="#ff0000", origin=[10, 10])
    merged = s.export_layers("a", out_dir=str(ws / "m"))
    assert merged["split_by"] == "layer" and [l["name"] for l in merged["layers"]] == ["body", "badge"]
    body = merged["layers"][0]
    assert body["merged"] == ["shade"] and body["x"] == 18 and body["width"] == 64
    img = np.asarray(Image.open(ws / "m" / body["file"]))
    assert abs(int(img[32, 45][0]) - 64) <= 1 and img[32, 15][0] == 128          # shading is baked into the body
    assert merged["layers"][1]["origin"] == [10, 10]
    split = s.export_layers("a", out_dir=str(ws / "s"), merge_clipped=False, trim=False)
    assert [l["name"] for l in split["layers"]] == ["body", "shade", "badge"]
    assert split["layers"][1]["blend"] == "multiply" and "multiply" in split["notes"][0]
    shade = np.asarray(Image.open(ws / "s" / split["layers"][1]["file"]))
    assert shade[50, 65][3] == 255 and shade[50, 90][3] == 0                    # exported already clipped
    groups = s.export_layers("a", out_dir=str(ws / "g"), by="group")
    assert groups["split_by"] == "group"
    with pytest.raises(ValueError):
        s.export_layers("a", out_dir=str(ws / "x"), layers=["ghost"])


def test_export_psd_keeps_the_stack(ws):
    psd_tools = pytest.importorskip("psd_tools")
    s.create_canvas("a", 80, 60, "#202020")
    s.add_rect("a", 0, 0, 20, 20, fill="#ffffff")                               # implicit base layer
    s.create_layer("a", "Gövde")
    s.add_ellipse("a", 40, 30, 20, fill="#ff0000")
    s.create_layer("a", "shade", clip=True, blend="multiply", opacity=0.5)
    s.add_rect("a", 40, 0, 40, 60, fill="#000000")
    s.create_layer("a", "hidden", visible=False)
    s.add_rect("a", 60, 40, 10, 10, fill="#00ff00")
    out = s.export_psd("a", str(ws / "a.psd"), scale=2)
    assert out["size"] == [160, 120]
    psd = psd_tools.PSDImage.open(out["file"])
    assert psd.size == (160, 120) and [l.name for l in psd] == ["background", "base", "gövde", "shade", "hidden"]
    shade = psd[3]
    assert shade.blend_mode.name == "MULTIPLY" and shade.opacity == 128 and shade.clipping
    assert not psd[4].visible and psd[2].bbox == (40, 20, 120, 100)
    stored = np.asarray(psd.topil().convert("RGBA")).astype(int)
    ours = render("a", 2.0, background=True).astype(int)
    assert np.abs(stored - ours).max() <= 1                                     # the flattened preview matches
    body = np.asarray(psd[2].topil().convert("RGBA"))
    assert tuple(body[40, 40]) == (255, 0, 0, 255)
    with pytest.raises(ValueError):
        s.export_psd("a", str(ws / "a.png"))
