"""Ready materials and design variations, drawn with game-ui-mcp itself.

Run: python examples/demo_materials.py  ->  examples/materials.png, examples/variations.png
"""

import tempfile
from pathlib import Path

from game_ui_mcp import server as ui
from game_ui_mcp import tools_design as td
from game_ui_mcp.materials import NAMES
from game_ui_mcp.store import Store

OUT = Path(__file__).resolve().parent
ui.set_store(Store(Path(tempfile.mkdtemp()) / "ws"))
ui.set_theme(colors={"bg": "#0e1016", "surface": "#1b1830", "primary": "#8b5cf6", "text": "#f4f1ff",
                     "muted": "#9a93b8"})

cols, cw, ch = 6, 170, 132
rows = (len(NAMES) + cols - 1) // cols
ui.create_canvas("materials", cols * cw + 24, rows * ch + 24, background="#20232c")
for i, name in enumerate(NAMES):
    x, y = 12 + (i % cols) * cw + 15, 12 + (i // cols) * ch + 12
    ui.add_rect("materials", x, y, 140, 86, radius=14, style={"material": name})
    ui.add_text("materials", name, x + 70, y + 106, anchor="center", size=14, weight=600, color="#e6e8ee")
st = ui.store()
st.renderer(st.get("materials"), 1).render().convert("RGB").save(OUT / "materials.png", optimize=True)

ui.create_canvas("card", 360, 220, background="$bg")
ui.add_rect("card", 20, 20, 320, 180, radius=18,
            style={"fill": "$surface", "stroke": {"color": "$primary@0.6", "width": 2},
                   "shadow": {"blur": 20, "dy": 8, "color": "#000000@0.5"}})
ui.add_text("card", "VICTORY", 180, 70, anchor="center", size=40, weight=700, color="$text")
ui.add_text("card", "Rank up! +250 XP", 180, 110, anchor="center", size=16, color="$muted")
ui.add_rect("card", 110, 140, 140, 40, radius=12,
            style={"fill": {"type": "linear", "angle": 90, "stops": ["$primary", "#5b21b6"]}})
ui.add_text("card", "CONTINUE", 180, 160, anchor="center", size=16, weight=700, color="#ffffff")
sheet = td.make_variations("card", count=3)
(OUT / "variations.png").write_bytes(sheet[0].data)
print("wrote", OUT / "materials.png", OUT / "variations.png")
