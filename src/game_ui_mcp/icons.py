"""Built-in game UI icon set.

Original line icons drawn on a 24x24 grid (2 unit stroke, round caps and joins).
Each icon is one SVG path string. Closed subpaths are filled when the icon is
drawn with variant="solid".
"""

from __future__ import annotations

import math


def _circle(cx: float, cy: float, r: float) -> str:
    return f"M{cx - r} {cy}A{r} {r} 0 1 0 {cx + r} {cy}A{r} {r} 0 1 0 {cx - r} {cy}Z"


def _star(cx: float, cy: float, outer: float, inner: float, points: int = 5) -> str:
    parts = []
    for i in range(points * 2):
        r = outer if i % 2 == 0 else inner
        a = -math.pi / 2 + math.pi * i / points
        parts.append(f"{'M' if i == 0 else 'L'}{cx + r * math.cos(a):.2f} {cy + r * math.sin(a):.2f}")
    return "".join(parts) + "Z"


def _gear(cx: float, cy: float, outer: float, inner: float, teeth: int = 8) -> str:
    parts = []
    step = 2 * math.pi / teeth
    for i in range(teeth):
        base = i * step - math.pi / 2
        for j, (frac, r) in enumerate(((-0.30, inner), (-0.16, outer), (0.16, outer), (0.30, inner))):
            a = base + frac * step
            cmd = "M" if (i == 0 and j == 0) else "L"
            parts.append(f"{cmd}{cx + r * math.cos(a):.2f} {cy + r * math.sin(a):.2f}")
    return "".join(parts) + "Z"


ICONS: dict[str, str] = {
    # --- controls
    "close": "M6 6L18 18M18 6L6 18",
    "check": "M5 12.5L10 17.5L19 7",
    "plus": "M12 5V19M5 12H19",
    "minus": "M5 12H19",
    "arrow-left": "M19 12H5M11 6L5 12L11 18",
    "arrow-right": "M5 12H19M13 6L19 12L13 18",
    "arrow-up": "M12 19V5M6 11L12 5L18 11",
    "arrow-down": "M12 5V19M6 13L12 19L18 13",
    "chevron-left": "M15 5L8 12L15 19",
    "chevron-right": "M9 5L16 12L9 19",
    "chevron-up": "M5 15L12 8L19 15",
    "chevron-down": "M5 9L12 16L19 9",
    "play": "M8 5L19 12L8 19Z",
    "pause": "M7 5H10V19H7ZM14 5H17V19H14Z",
    "stop": "M6 6H18V18H6Z",
    "menu": "M4 7H20M4 12H20M4 17H20",
    "list": "M9 6.5H20M9 12H20M9 17.5H20M4.5 6.5V6.6M4.5 12V12.1M4.5 17.5V17.6",
    "grid": "M4 4H10.5V10.5H4ZM13.5 4H20V10.5H13.5ZM4 13.5H10.5V20H4ZM13.5 13.5H20V20H13.5Z",
    "search": _circle(10.5, 10.5, 6) + "M15 15L20 20",
    "refresh": "M19.5 12A7.5 7.5 0 1 1 16.5 6M17 2.5V6.5H13",
    "fullscreen": "M4 9V4H9M15 4H20V9M20 15V20H15M9 20H4V15",
    "exit": "M10 4H5V20H10M9.5 12H20M16 8L20 12L16 16",
    "power": "M12 3.5V12M7.2 6.8A7.5 7.5 0 1 0 16.8 6.8",
    "download": "M12 4V15M7 10.5L12 15.5L17 10.5M5 20H19",
    "upload": "M12 16V5M7 9.5L12 4.5L17 9.5M5 20H19",
    "save": "M5 4H16L19.5 7.5V20H5ZM8 4V9H15V4M8 20V14H16V20",
    "edit": "M5 19L5.8 15.2L16 5L19 8L8.8 18.2ZM14 7L17 10",
    "trash": "M7 7H17L16 20.5H8ZM5 7H19M9 7V4.5H15V7M10.5 11V16.5M13.5 11V16.5",
    "settings": _gear(12, 12, 9.2, 7.2) + _circle(12, 12, 3),
    "sliders": "M6 4V20M12 4V20M18 4V20M4 9H8M10 15H14M16 8H20",
    # --- status / info
    "info": _circle(12, 12, 9) + "M12 11V17M12 7.5V7.6",
    "warning": "M12 3.5L21.5 20H2.5ZM12 10V14.5M12 17.4V17.5",
    "question": _circle(12, 12, 9) + "M9.2 9.5A2.9 2.9 0 1 1 12 12.5V13.8M12 16.9V17",
    "bell": "M6 17L4 19H20L18 17V11A6 6 0 0 0 6 11ZM10 22H14",
    "mail": "M4 6H20V18H4ZM4 7L12 13L20 7",
    "chat": "M4 5H20V16H10L6 20V16H4Z",
    "eye": "M2.5 12C5 7.5 8.5 5.5 12 5.5C15.5 5.5 19 7.5 21.5 12C19 16.5 15.5 18.5 12 18.5C8.5 18.5 5 16.5 2.5 12Z"
           + _circle(12, 12, 3),
    "clock": _circle(12, 12, 9) + "M12 7V12L15.5 14",
    "calendar": "M4 6H20V20H4ZM4 10.5H20M8.5 3.5V7.5M15.5 3.5V7.5",
    "signal": "M5 19V16M10 19V12.5M15 19V9M20 19V5",
    "battery": "M3 8H18.5V16H3ZM21 10.5V13.5",
    "volume": "M4 9.5H7.5L12 5.5V18.5L7.5 14.5H4ZM15.5 9A4.5 4.5 0 0 1 15.5 15M18 6.5A8 8 0 0 1 18 17.5",
    "mute": "M4 9.5H7.5L12 5.5V18.5L7.5 14.5H4ZM16 9.5L21 14.5M21 9.5L16 14.5",
    "music": "M9 18V5L19 3.5V16" + _circle(6.5, 18, 2.5) + _circle(16.5, 16, 2.5),
    "camera": "M4 8H7.5L9 5.5H15L16.5 8H20V19H4Z" + _circle(12, 13, 3.5),
    # --- player / social
    "user": _circle(12, 8, 4) + "M4.5 20.5C4.5 16.5 8 14.5 12 14.5C16 14.5 19.5 16.5 19.5 20.5Z",
    "home": "M12 3.5L20.5 11V20.5H3.5V11ZM9.5 20.5V14H14.5V20.5",
    "map-pin": "M12 21C12 21 5.5 14.5 5.5 9.5A6.5 6.5 0 0 1 18.5 9.5C18.5 14.5 12 21 12 21Z" + _circle(12, 9.5, 2.2),
    "flag": "M6 5H18L15.5 9L18 13H6ZM6 21V4",
    "target": _circle(12, 12, 7.5) + "M12 2.5V7M12 17V21.5M2.5 12H7M17 12H21.5",
    "gamepad": "M7.5 7H16.5A5.5 5.5 0 0 1 16.5 18C14.8 18 14 16.5 12 16.5C10 16.5 9.2 18 7.5 18A5.5 5.5 0 0 1 7.5 7Z"
               "M8 10.5V14M6.25 12.25H9.75M15 11V11.1M17 13.4V13.5",
    # --- items / economy
    "heart": "M12 20C12 20 4 14.5 4 9A4.2 4.2 0 0 1 12 7.2A4.2 4.2 0 0 1 20 9C20 14.5 12 20 12 20Z",
    "star": _star(12, 12.6, 9.4, 3.9),
    "sparkle": "M12 3C12.6 8.5 15.5 11.4 21 12C15.5 12.6 12.6 15.5 12 21C11.4 15.5 8.5 12.6 3 12C8.5 11.4 11.4 8.5 12 3Z",
    "coin": _circle(12, 12, 8.5) + _circle(12, 12, 4.5),
    "gem": "M7 4H17L21 9L12 20L3 9ZM3 9H21M9.5 4L8 9L12 20M14.5 4L16 9L12 20",
    "shield": "M12 3L19 6V11.5C19 16 16 19.2 12 21C8 19.2 5 16 5 11.5V6Z",
    "sword": "M20 4L15.5 4.5L7.5 12.5L11.5 16.5L19.5 8.5ZM5.5 11.5L12.5 18.5M8 16L4.5 19.5",
    "bolt": "M13.5 3L5.5 13.5H11.5L10.5 21L18.5 10.5H12.5Z",
    "fire": "M12 21C8 21 5.5 18.2 5.5 14.8C5.5 11.5 8 9.5 9 6.5C9.3 5.5 9.3 4.2 9 3C12.5 4.2 14.5 7 14.5 9.8"
            "C15.4 9.3 15.9 8.4 16 7.4C17.6 9.2 18.5 11.6 18.5 14.5C18.5 18.2 16 21 12 21Z",
    "skull": "M5 11A7 7 0 0 1 19 11V14.5L17 16V19.5H7V16L5 14.5Z" + _circle(9, 11.5, 2) + _circle(15, 11.5, 2)
             + "M10.5 19.5V17.5M13.5 19.5V17.5",
    "health": "M9.5 4H14.5V9.5H20V14.5H14.5V20H9.5V14.5H4V9.5H9.5Z",
    "potion": "M10.5 3.5V8.5L6 16.5A3 3 0 0 0 8.6 20.5H15.4A3 3 0 0 0 18 16.5L13.5 8.5V3.5ZM9.5 3.5H14.5M8.2 14.5H15.8",
    "key": _circle(8, 15, 4) + "M11 12L19.5 3.5M16 7L18.5 9.5M13.5 9.5L15.5 11.5",
    "lock": "M6 11H18V20H6ZM8.5 11V8A3.5 3.5 0 0 1 15.5 8V11",
    "unlock": "M6 11H18V20H6ZM8.5 11V8A3.5 3.5 0 0 1 15.2 6.6",
    "bag": "M6 9H18L19 20H5ZM9 9V7A3 3 0 0 1 15 7V9",
    "cart": "M6.2 7H20L18 15H8ZM3 4H5.5L6.2 7" + _circle(9, 19, 1.3) + _circle(17, 19, 1.3),
    "gift": "M4 9H20V12.5H4ZM5.5 12.5H18.5V20.5H5.5ZM12 9V20.5"
            "M12 9C10.5 9 7.5 8.5 7.5 6.3A2 2 0 0 1 9.5 4.3C11 4.3 12 6.5 12 9Z"
            "M12 9C13.5 9 16.5 8.5 16.5 6.3A2 2 0 0 0 14.5 4.3C13 4.3 12 6.5 12 9Z",
    "trophy": "M7 4H17V9A5 5 0 0 1 7 9ZM7 5.5H4.5V7A3 3 0 0 0 7 10M17 5.5H19.5V7A3 3 0 0 1 17 10M12 14V19M8 19.5H16",
    "crown": "M4 18L3 8L8 12L12 5L16 12L21 8L20 18Z",
    "book": "M4 5.5C6.5 4.5 9.5 4.5 12 6C14.5 4.5 17.5 4.5 20 5.5V18.5C17.5 17.5 14.5 17.5 12 19"
            "C9.5 17.5 6.5 17.5 4 18.5ZM12 6V19",
}


def icon_names() -> list[str]:
    return sorted(ICONS)


def get_icon(name: str) -> str:
    key = name.strip().lower().replace("_", "-").replace(" ", "-")
    if key not in ICONS:
        raise ValueError(f"Unknown icon '{name}'. Call list_icons to see the built-in set, or draw a custom one with add_path.")
    return ICONS[key]
