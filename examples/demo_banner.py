"""The README banner, drawn with game-ui-mcp itself.

Run: python examples/demo_banner.py  ->  docs/banner.png
"""

import tempfile
from pathlib import Path

from game_ui_mcp import server as ui
from game_ui_mcp.store import Store

DOCS = Path(__file__).resolve().parent.parent / "docs"
ui.set_store(Store(Path(tempfile.mkdtemp()) / "ws"))
ui.set_theme(colors={"bg": "#0c0d14", "panel": "#171a26", "ink": "#f5f6fb", "muted": "#8d93ab",
                     "violet": "#7c5cff", "cyan": "#22d3ee", "gold": "#f5b83d", "hp": "#f43f5e"})

c = "banner"
ui.create_canvas(c, 1280, 400, background={"type": "radial", "cx": 0.78, "cy": 0.35, "r": 0.9,
                                           "stops": [[0, "#1b1640"], [1, "$bg"]]})
ui.batch(c, [
    {"op": "add_rect", "x": 0, "y": 0, "width": 1280, "height": 400,
     "fill": {"type": "grid", "color": "#ffffff@0.035", "size": 40, "line_width": 1}},
    # title block
    {"op": "add_text", "text": "game-ui-mcp", "x": 72, "y": 118, "size": 76, "weight": 800,
     "color": {"type": "linear", "angle": 0, "stops": [[0, "#ffffff"], [1, "#b9a8ff"]]}},
    {"op": "add_text", "text": "Let your AI agent design game & app UI", "x": 76, "y": 214,
     "size": 24, "weight": 500, "color": "$muted"},
    {"op": "add_text", "text": "DRAW   ·   ANIMATE   ·   REVIEW   ·   EXPORT", "x": 76, "y": 268,
     "size": 15, "weight": 700, "letter_spacing": 2, "color": "$cyan"},
    # a little HUD on the right
    {"op": "add_rect", "id": "card", "x": 820, "y": 70, "width": 380, "height": 260, "radius": 28,
     "smoothing": 0.6, "style": {"fill": "#ffffff@0.06", "backdrop_blur": 16,
                                 "stroke": {"color": "#ffffff@0.18", "width": 1.5},
                                 "shadow": {"blur": 40, "dy": 18, "color": "#000000@0.6", "knockout": True}}},
    {"op": "add_ellipse", "cx": 880, "cy": 130, "rx": 30, "fill": {"type": "linear", "angle": 135,
                                                                   "stops": [[0, "$violet"], [1, "$cyan"]]},
     "style": {"glow": {"color": "$violet@0.6", "blur": 20}}},
    {"op": "add_text", "text": "NOVA", "x": 926, "y": 106, "size": 22, "weight": 800, "color": "$ink",
     "letter_spacing": 2},
    {"op": "add_text", "text": "LV 27  ·  Vanguard", "x": 926, "y": 136, "size": 14, "color": "$muted"},
    {"op": "add_rect", "id": "hp_track", "x": 852, "y": 186, "width": 316, "height": 14, "radius": 7,
     "fill": "#000000@0.45"},
    {"op": "add_rect", "x": 852, "y": 186, "width": 236, "height": 14, "radius": 7, "clip": "hp_track",
     "fill": {"type": "linear", "angle": 0, "stops": [[0, "#fb7185"], [1, "$hp"]]},
     "style": {"glow": {"color": "$hp@0.5", "blur": 10}}},
    {"op": "add_rect", "id": "xp_track", "x": 852, "y": 210, "width": 316, "height": 8, "radius": 4,
     "fill": "#000000@0.45"},
    {"op": "add_rect", "x": 852, "y": 210, "width": 150, "height": 8, "radius": 4, "clip": "xp_track",
     "fill": "$gold"},
])
for i in range(5):
    ui.add_rect(c, 852 + i * 66, 238, 56, 56, radius=14, smoothing=0.6, id=f"slot{i}",
                style={"fill": "#000000@0.35",
                       "stroke": {"color": "$gold" if i == 0 else "#ffffff@0.16", "width": 2 if i == 0 else 1}})
ui.add_polygon(c, cx=880, cy=266, radius=16, sides=5, inner_radius=7, fill="$gold",
               style={"glow": {"color": "$gold@0.5", "blur": 10}})
ui.add_ellipse(c, 946, 266, 13, fill="$cyan")
ui.add_rect(c, 999, 253, 26, 26, radius=6, fill="$violet", rotation=45)

st = ui.store()
st.renderer(st.get(c), 1).render().convert("RGB").save(DOCS / "banner.png", optimize=True)
print("wrote", DOCS / "banner.png")
