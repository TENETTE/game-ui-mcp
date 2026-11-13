"""Draws a small sci-fi UI kit and a HUD mockup with the same tool calls an AI would make.

    python examples/demo.py

Writes kit_sheet.png, hud_mockup.png and icons.png next to this file. Nothing is saved
to your normal workspace (a temporary one is used).
"""

import tempfile
from pathlib import Path

from game_ui_mcp import server as ui
from game_ui_mcp.icons import icon_names
from game_ui_mcp.store import Store

OUT = Path(__file__).parent
ui.set_store(Store(Path(tempfile.mkdtemp()) / "ws"))

ui.set_theme(
    colors={"bg": "#0b0a12", "primary": "#8b5cf6", "accent": "#22d3ee", "text": "#f4f1ff", "muted": "#9a93b8",
            "danger": "#f43f5e", "gold": "#fbbf24"},
    styles={
        "panel": {"fill": {"type": "linear", "angle": 90, "stops": [[0, "#241f3aee"], [1, "#151226ee"]]},
                  "stroke": {"color": "$primary@0.55", "width": 2},
                  "shadow": {"color": "#000000@0.55", "blur": 24, "dy": 10},
                  "inner_glow": {"color": "$primary@0.18", "blur": 18}},
        "title": {"font": "Rajdhani", "weight": 700, "size": 30, "fill": "$text", "uppercase": "tr", "letter_spacing": 2},
    },
)

# --- button (cut corners, gradient, glow, clipped shine + stripes)
ui.create_canvas("btn_primary", 320, 96)
ui.batch("btn_primary", [
    {"op": "add_rect", "id": "body", "x": 16, "y": 14, "width": 288, "height": 60, "radius": 14, "corner": "cut",
     "fill": {"type": "linear", "angle": 90, "stops": [[0, "#a78bfa"], [0.5, "#7c3aed"], [1, "#5b21b6"]]},
     "stroke": {"color": "#ddd6fe@0.9", "width": 2},
     "style": {"glow": {"color": "$primary@0.7", "blur": 22}, "shadow": {"color": "#000@0.5", "blur": 10, "dy": 6},
               "bevel": {"size": 2}}},
    {"op": "add_rect", "id": "shine", "x": 22, "y": 18, "width": 276, "height": 24, "clip": "body",
     "fill": {"type": "linear", "angle": 90, "stops": [[0, "#ffffff@0.28"], [1, "#ffffff@0"]]}},
    {"op": "add_rect", "id": "stripes", "x": 16, "y": 14, "width": 288, "height": 60, "clip": "body",
     "fill": {"type": "stripes", "color": "#ffffff@0.06", "size": 10, "angle": 45}},
    {"op": "add_text", "id": "label", "text": "oyuna başla", "x": 160, "y": 44, "anchor": "center", "color": "#ffffff",
     "size": 28, "style": {"use": "title", "shadow": {"color": "#2e1065@0.9", "blur": 4, "dy": 2}}},
])

# --- 9-slice panel
ui.create_canvas("panel", 256, 256)
ui.batch("panel", [
    {"op": "add_rect", "id": "bg", "x": 28, "y": 22, "width": 200, "height": 200, "radius": 18, "style": "panel"},
    {"op": "add_line", "points": [[48, 23], [110, 23]], "width": 3, "color": "$accent",
     "style": {"glow": {"color": "$accent", "blur": 10}}},
    {"op": "add_polygon", "cx": 212, "cy": 38, "radius": 5, "sides": 4, "fill": "$accent"},
])

# --- health bar (fill clipped to the rounded track)
ui.create_canvas("hp_bar", 360, 64)
ui.batch("hp_bar", [
    {"op": "add_rect", "id": "track", "x": 56, "y": 18, "width": 288, "height": 28, "radius": 14, "fill": "#0b0a12cc",
     "stroke": {"color": "#ffffff@0.25", "width": 2}, "style": {"inner_shadow": {"color": "#000@0.7", "blur": 8, "dy": 3}}},
    {"op": "add_rect", "id": "fill", "x": 56, "y": 18, "width": 200, "height": 28, "clip": "track",
     "fill": {"type": "linear", "angle": 0, "stops": [[0, "#fb7185"], [1, "#e11d48"]]},
     "style": {"bevel": {"size": 3, "highlight": "#fff@0.45"}}},
    {"op": "add_ellipse", "id": "badge", "cx": 34, "cy": 32, "rx": 24,
     "fill": {"type": "radial", "stops": [[0, "#3b1d2a"], [1, "#190b12"]]},
     "stroke": {"color": "#fb7185", "width": 2}, "style": {"shadow": {"blur": 8, "dy": 3}}},
    {"op": "add_icon", "name": "heart", "variant": "solid", "x": 20, "y": 18, "size": 28, "color": "#fb7185",
     "style": {"glow": {"color": "#fb7185@0.8", "blur": 8}}},
    {"op": "add_text", "text": "72 / 100", "x": 200, "y": 32, "anchor": "center", "font": "Rajdhani", "weight": 700,
     "size": 18, "color": "#fff", "stroke": {"color": "#000@0.6", "width": 2}},
])

# --- inventory slot
ui.create_canvas("slot", 96, 96)
ui.batch("slot", [
    {"op": "add_rect", "id": "s", "x": 8, "y": 8, "width": 80, "height": 80, "radius": 12,
     "fill": {"type": "radial", "stops": [[0, "#2a2540"], [1, "#15121f"]]}, "stroke": {"color": "$gold@0.8", "width": 2},
     "style": {"inner_shadow": {"color": "#000@0.6", "blur": 10, "dy": 2}, "glow": {"color": "$gold@0.35", "blur": 10}}},
    {"op": "add_icon", "name": "sword", "x": 24, "y": 24, "size": 48, "variant": "solid",
     "color": {"type": "linear", "angle": 135, "stops": [[0, "#fde68a"], [1, "#d97706"]]},
     "style": {"shadow": {"blur": 4, "dy": 2}}},
    {"op": "add_text", "text": "x3", "x": 82, "y": 82, "anchor": "bottom-right", "weight": 800, "size": 14,
     "color": "#fff", "stroke": {"color": "#000", "width": 2}},
])

# --- cartoon button (Roblox style: chunky outline, flat depth, comic font)
ui.create_canvas("btn_cartoon", 280, 110)
ui.batch("btn_cartoon", [
    {"op": "add_rect", "id": "depth", "x": 14, "y": 22, "width": 252, "height": 72, "radius": 26, "fill": "#15803d",
     "stroke": {"color": "#052e16", "width": 4, "align": "outside"}},
    {"op": "add_rect", "id": "face", "x": 14, "y": 12, "width": 252, "height": 70, "radius": 26,
     "fill": {"type": "linear", "angle": 90, "stops": [[0, "#86efac"], [1, "#22c55e"]]},
     "stroke": {"color": "#052e16", "width": 4, "align": "outside"}},
    {"op": "add_rect", "x": 30, "y": 20, "width": 220, "height": 18, "radius": 9, "fill": "#ffffff@0.35"},
    {"op": "add_text", "text": "PLAY!", "x": 140, "y": 47, "anchor": "center", "font": "Bangers", "size": 44,
     "color": "#fff", "stroke": {"color": "#052e16", "width": 5}, "letter_spacing": 2,
     "style": {"shadow": {"color": "#052e16", "blur": 0, "dy": 4}}},
])

kit = ["btn_primary", "panel", "hp_bar", "slot", "btn_cartoon"]
ui.contact_sheet(kit, columns=3, cell=300, save_path=str(OUT / "kit_sheet.png"))

# --- full-screen HUD mockup
ui.create_canvas("hud", 1920, 1080, {"type": "linear", "angle": 90, "stops": ["#1b2a4a", "#0b0a12"]})
ops = [
    {"op": "add_rect", "x": 0, "y": 0, "width": 1920, "height": 1080,
     "fill": {"type": "radial", "r": 0.75, "stops": [[0, "#000000@0"], [1, "#000000@0.6"]]}},
    {"op": "add_rect", "x": 40, "y": 32, "width": 520, "height": 120, "radius": 20, "style": "panel"},
    {"op": "add_ellipse", "cx": 110, "cy": 92, "rx": 44, "fill": {"type": "radial", "stops": ["#4c3a8a", "#1a1530"]},
     "stroke": {"color": "$primary", "width": 3}},
    {"op": "add_icon", "name": "user", "x": 82, "y": 64, "size": 56, "variant": "solid", "color": "$text"},
    {"op": "add_text", "text": "mustafa", "x": 176, "y": 58, "style": "title"},
    {"op": "add_rect", "id": "hpt", "x": 176, "y": 98, "width": 350, "height": 20, "radius": 10, "fill": "#000000@0.5",
     "stroke": {"color": "#ffffff@0.2", "width": 1.5}},
    {"op": "add_rect", "x": 176, "y": 98, "width": 250, "height": 20, "clip": "hpt",
     "fill": {"type": "linear", "angle": 0, "stops": ["#fb7185", "#e11d48"]}, "style": {"bevel": {"size": 2}}},
    {"op": "add_rect", "x": 660, "y": 950, "width": 600, "height": 96, "radius": 22, "style": "panel"},
]
for i, name in enumerate(["sword", "shield", "potion", "bolt", "key"]):
    x = 684 + i * 112
    ops += [
        {"op": "add_rect", "x": x, "y": 962, "width": 72, "height": 72, "radius": 12, "fill": "#0b0a12cc",
         "stroke": {"color": "$gold" if i == 0 else "$primary@0.5", "width": 2},
         "style": {"inner_shadow": {"blur": 8, "dy": 2}}},
        {"op": "add_icon", "name": name, "x": x + 14, "y": 976, "size": 44, "variant": "solid",
         "color": "$gold" if i == 0 else "$text"},
        {"op": "add_text", "text": str(i + 1), "x": x + 8, "y": 968, "size": 12, "weight": 700, "color": "$muted"},
    ]
ops += [
    {"op": "add_ellipse", "cx": 1750, "cy": 162, "rx": 130, "style": "panel"},
    {"op": "add_ellipse", "cx": 1750, "cy": 162, "rx": 112,
     "fill": {"type": "grid", "color": "$accent@0.25", "color2": "#06131a", "size": 22}},
    {"op": "add_polygon", "cx": 1750, "cy": 162, "radius": 12, "sides": 3, "fill": "$accent",
     "style": {"glow": {"color": "$accent", "blur": 10}}},
    {"op": "add_text", "text": "görev: kristali bul", "x": 960, "y": 70, "anchor": "center", "size": 36,
     "stroke": {"color": "#000@0.7", "width": 3}, "style": {"use": "title", "glow": {"color": "$primary", "blur": 18}}},
]
ui.batch("hud", ops)
ui.export_png("hud", str(OUT / "hud_mockup.png"), scales=[0.5], include_background=True)
(OUT / "hud_mockup_0_5x.png").replace(OUT / "hud_mockup.png")

# --- every built-in icon (line on top, solid below)
names = icon_names()
cols, cell = 10, 72
rows = (len(names) + cols - 1) // cols
ui.create_canvas("icons", cols * cell, rows * cell * 2, "#15131c")
ops = []
for i, name in enumerate(names):
    x, y = (i % cols) * cell + 12, (i // cols) * cell * 2 + 8
    ops += [
        {"op": "add_icon", "name": name, "x": x, "y": y, "size": 48},
        {"op": "add_icon", "name": name, "x": x, "y": y + cell - 8, "size": 48, "color": "#c4a1ff", "variant": "solid"},
        {"op": "add_text", "text": name, "x": x + 24, "y": y + cell * 2 - 22, "size": 9, "anchor": "top-center",
         "color": "#aaaaaa"},
    ]
ui.batch("icons", ops)
ui.export_png("icons", str(OUT / "icons.png"), include_background=True)
print("written:", ", ".join(p.name for p in sorted(OUT.glob("*.png"))))
