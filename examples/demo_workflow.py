"""The game / app designer workflow: a button component with states and per-copy labels, a rarity card
template filled from data rows, a HUD pinned for other screen sizes, a pixel-art icon and a baked
damage-number font.

Run: python examples/demo_workflow.py  ->  examples/workflow.png, examples/resolutions.png
"""

import io
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from game_ui_mcp import server as ui
from game_ui_mcp.store import Store

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp())
ui.set_store(Store(TMP / "ws"))

ui.set_theme(
    colors={"bg": "#0d0b14", "ink": "#f4f1ff", "muted": "#9a93b8", "violet": "hsl(262, 83%, 58%)",
            "common": "#94a3b8", "rare": "hsl(205, 90%, 55%)", "epic": "hsl(275, 85%, 62%)",
            "legendary": "hsl(38, 95%, 55%)", "mythic": "hsl(350, 90%, 58%)"},
    styles={"label": {"font": "Rajdhani", "weight": 700, "size": 26, "fill": "$ink", "letter_spacing": 2}},
)

# ---- 1. one button component: {label} text + interaction states
ui.create_canvas("button", 280, 84)
ui.batch("button", [
    {"op": "add_rect", "id": "face", "x": 10, "y": 10, "width": 260, "height": 60, "radius": 14,
     "fill": {"type": "linear", "angle": 90, "stops": ["hsl(262, 83%, 64%)", "hsl(262, 70%, 46%)"]},
     "stroke": {"color": "#ffffff@0.35", "width": 1.5},
     "style": {"shadow": {"color": "#000@0.55", "blur": 10, "dy": 5}, "bevel": {"size": 2}}},
    {"op": "add_text", "id": "label", "text": "{label}", "x": 140, "y": 40, "anchor": "center", "style": "label"},
])
ui.update_canvas("button", vars={"label": "PLAY"})
ui.set_state("button", "hover", "face", {"style": {"glow": {"color": "hsl(262, 90%, 70%)", "blur": 16}},
                                          "fill": {"type": "linear", "angle": 90, "stops": ["hsl(262, 90%, 72%)", "hsl(262, 75%, 52%)"]}})
ui.set_state("button", "pressed", "face", {"y": 13, "fill": "hsl(262, 70%, 42%)", "style": {"shadow": {"color": "#000@0.5", "blur": 3, "dy": 1}}})
ui.set_state("button", "pressed", "label", {"y": 43})
ui.set_state("button", "disabled", "face", {"fill": "#3a3550", "stroke": {"color": "#ffffff@0.1", "width": 1.5}})
ui.set_state("button", "disabled", "label", {"style": {"use": "label", "fill": "$muted"}})

ui.create_canvas("states", 1220, 100)
ui.batch("states", [
    {"op": "add_instance", "source": "button", "x": 10 + i * 300, "y": 8, "id": f"b_{st or 'normal'}",
     "vars": {"label": text}, **({"state": st} if st else {})}
    for i, (st, text) in enumerate([(None, "PLAY"), ("hover", "PLAY"), ("pressed", "PLAY"), ("disabled", "LOCKED")])
])

# ---- 2. rarity cards from one template + data rows
ICONS = {
    "sword": "M21 3L17 3.6L7.5 13L11 16.5L20.4 7ZM4.5 11.5L6 10L14 18L12.5 19.5ZM7 15.5L8.5 17L4.5 21L3 19.5Z",
    "shield": "M12 2.5L20 5.5V11.5C20 16.3 16.6 19.8 12 21.5C7.4 19.8 4 16.3 4 11.5V5.5Z",
    "potion": "M9.5 2.5H14.5V4.5H13.5V8.2C16.6 9.2 19 12 19 15.5A7 7 0 0 1 5 15.5C5 12 7.4 9.2 10.5 8.2V4.5H9.5Z",
    "bolt": "M13.5 2L4.5 13.5H11L10 22L19.5 10H13Z",
    "gem": "M6 3H18L22 9L12 21.5L2 9ZM2 9H22M9 3L7.5 9L12 21.5L16.5 9L15 3",
}
ui.create_canvas("card", 180, 240)
ui.update_canvas("card", colors={"rarity": "$common"})
ui.batch("card", [
    {"op": "add_rect", "id": "frame", "x": 10, "y": 10, "width": 160, "height": 220, "radius": 16,
     "fill": {"type": "linear", "angle": 90, "stops": [[0, "#1d1a2b"], [1, "#0f0d17"]]},
     "stroke": [{"color": "$rarity", "width": 2.5}, {"color": "$rarity@0.25", "width": 4, "align": "outside"}],
     "style": {"shadow": {"color": "#000@0.6", "blur": 12, "dy": 6}}},
    {"op": "add_rect", "id": "halo", "x": 12, "y": 12, "width": 156, "height": 130, "radius": 14,
     "fill": {"type": "radial", "cx": 0.5, "cy": 0.55, "r": 0.6, "stops": [[0, "$rarity@0.45"], [1, "$rarity@0"]]}},
    {"op": "add_path", "id": "icon", "d": ICONS["sword"], "x": 50, "y": 34, "scale": 80 / 24, "fill_rule": "nonzero",
     "fill": {"type": "linear", "angle": 90, "stops": [[0, "#ffffff"], [1, "$rarity"]]},
     "style": {"shadow": {"color": "#000@0.6", "blur": 6, "dy": 4}}},
    {"op": "add_text", "id": "name", "text": "{title}", "x": 90, "y": 168, "anchor": "center", "font": "Rajdhani",
     "weight": 700, "size": 20, "color": "$ink", "fit_width": 140},
    {"op": "add_text", "id": "tier", "text": "{tier}", "x": 90, "y": 194, "anchor": "center", "font": "Rajdhani",
     "weight": 600, "size": 13, "letter_spacing": 3, "color": "$rarity", "uppercase": True},
    {"op": "add_text", "id": "stat", "text": "{stat}", "x": 90, "y": 214, "anchor": "center", "size": 11, "color": "$muted"},
])
rows = [
    {"name": "c1", "title": "Iron Sword", "tier": "common", "stat": "ATK 12", "colors": {"rarity": "$common"}},
    {"name": "c2", "title": "Oak Shield", "tier": "rare", "stat": "DEF 18", "colors": {"rarity": "$rare"},
     "props": {"icon": {"d": ICONS["shield"]}}},
    {"name": "c3", "title": "Elixir", "tier": "epic", "stat": "+40 HP", "colors": {"rarity": "$epic"},
     "props": {"icon": {"d": ICONS["potion"]}}},
    {"name": "c4", "title": "Storm Core", "tier": "legendary", "stat": "SPD +25%", "colors": {"rarity": "$legendary"},
     "props": {"icon": {"d": ICONS["bolt"]}}},
    {"name": "c5", "title": "Heart of Void", "tier": "mythic", "stat": "ALL +10", "colors": {"rarity": "$mythic"},
     "props": {"icon": {"d": ICONS["gem"], "fill_rule": "evenodd"}}},
]
cards = ui.generate_from_template("card", rows, name_prefix="card")["canvases"]

# ---- 3. HUD pinned for other screens
ui.create_canvas("hud", preset="1080p")
ui.batch("hud", [
    {"op": "add_rect", "x": 40, "y": 40, "width": 420, "height": 96, "radius": 18, "fill": "#0d0b14@0.75",
     "stroke": {"color": "#ffffff@0.12", "width": 1.5}, "group": "status"},
    {"op": "add_rect", "x": 64, "y": 90, "width": 372, "height": 18, "radius": 9, "fill": "#000@0.5", "group": "status", "id": "hp_track"},
    {"op": "add_rect", "x": 64, "y": 90, "width": 280, "height": 18, "radius": 9, "clip": "hp_track", "group": "status",
     "fill": {"type": "linear", "angle": 0, "stops": ["#fb7185", "#e11d48"]}},
    {"op": "add_text", "text": "MUSTAFA  ·  LV 27", "x": 64, "y": 58, "size": 20, "font": "Rajdhani", "weight": 700,
     "color": "$ink", "group": "status"},
    {"op": "add_ellipse", "cx": 1760, "cy": 170, "rx": 128, "fill": {"type": "grid", "color": "$violet@0.3", "color2": "#0a0912", "size": 24},
     "stroke": [{"color": "#ffffff@0.25", "width": 2}], "group": "map"},
    {"op": "add_polygon", "cx": 1760, "cy": 170, "radius": 12, "sides": 3, "fill": "$violet", "group": "map"},
    {"op": "add_rect", "x": 660, "y": 960, "width": 600, "height": 88, "radius": 20, "fill": "#0d0b14@0.75",
     "stroke": {"color": "#ffffff@0.12", "width": 1.5}, "group": "hotbar"},
    {"op": "add_rect", "x": 0, "y": 0, "width": 1920, "height": 6, "fill": "$violet", "id": "topline"},
    {"op": "add_text", "text": "OBJECTIVE: REACH THE GATE", "x": 960, "y": 60, "anchor": "center", "size": 26,
     "font": "Rajdhani", "weight": 700, "letter_spacing": 3, "color": "$ink", "group": "objective",
     "stroke": {"color": "#000@0.6", "width": 2}},
])
for i in range(5):
    ui.add_rect("hud", 684 + i * 116, 972, 64, 64, radius=12, fill="#000@0.45",
                stroke={"color": "$violet" if i == 0 else "#ffffff@0.18", "width": 2}, group="hotbar")
ui.set_pin("hud", "right", "top", group="map")
ui.set_pin("hud", "center", "bottom", group="hotbar")
ui.set_pin("hud", "center", "top", group="objective")
ui.set_pin("hud", "stretch", "top", ids=["topline"])
res = ui.preview_resolutions("hud", sizes=[[1920, 1080], [2560, 1080], [1280, 800], "mobile-game-landscape"],
                             cell=440, guides=["safe"])
Image.open(io.BytesIO(res[0].data)).save(HERE / "resolutions.png")

# ---- 4. pixel-art potion (16x16, 4 colours)
ui.create_canvas("pixel_potion", 16, 16)
ui.batch("pixel_potion", [
    {"op": "add_path", "d": ICONS["potion"], "x": 0, "y": 0, "scale": 16 / 24, "fill": "#e11d48",
     "stroke": {"color": "#3b0718", "width": 1, "align": "outside"}},
    {"op": "add_rect", "x": 5, "y": 9, "width": 2, "height": 3, "fill": "#fda4af"},
])
ui.update_canvas("pixel_potion", pixel_art={"palette": ["#e11d48", "#3b0718", "#fda4af", "#9f1239"]})

# ---- 5. baked damage numbers
font_out = ui.export_bitmap_font("damage", "0123456789!", size=56, font="Bangers",
                                 fill={"type": "linear", "angle": 90, "stops": ["#fff7c2", "#f59e0b", "#b45309"]},
                                 stroke={"color": "#2a1003", "width": 5},
                                 style={"shadow": {"color": "#000@0.6", "blur": 0, "dy": 4}},
                                 out_dir=str(TMP / "font"), preview_text="1250!")

# ---- compose the overview image
def render(cid: str, scale: float = 1.0) -> Image.Image:
    st = ui.store()
    return st.renderer(st.get(cid), scale).render()


W = 1260
sheet = Image.new("RGB", (W, 580), (13, 11, 20))
d = ImageDraw.Draw(sheet)
states_img = render("states")
sheet.paste(states_img, (20, 40), states_img)
for i, lbl in enumerate(["normal", "hover", "pressed", "disabled"]):
    d.text((40 + i * 300, 18), lbl, fill=(154, 147, 184))
x = 20
for cid in cards:
    img = render(cid)
    sheet.paste(img, (x, 170), img)
    x += 196
pix = render("pixel_potion", 8)
sheet.paste(pix, (1030, 190), pix)
d.text((1030, 330), "pixel art 16x16, 4 colours", fill=(154, 147, 184))
font_img = Image.open(io.BytesIO(font_out[0].data))
sheet.paste(font_img, (20, 440))
d.text((24, 420), "baked bitmap font (damage numbers)", fill=(154, 147, 184))
d.text((20, 150), "one card template + 5 data rows", fill=(154, 147, 184))
sheet.save(HERE / "workflow.png")
print("wrote", HERE / "workflow.png", "and", HERE / "resolutions.png")
print(json.dumps(ui.review_canvas("hud")["issues"][:3], ensure_ascii=False))
