"""Color parsing and theme-token resolution."""

from __future__ import annotations

import re
from typing import Any

RGBA = tuple[int, int, int, int]

NAMED = {
    "transparent": (0, 0, 0, 0),
    "none": (0, 0, 0, 0),
    "white": (255, 255, 255, 255),
    "black": (0, 0, 0, 255),
    "red": (255, 0, 0, 255),
    "green": (0, 200, 0, 255),
    "blue": (0, 0, 255, 255),
    "yellow": (255, 221, 0, 255),
    "orange": (255, 140, 0, 255),
    "purple": (150, 60, 220, 255),
    "cyan": (0, 220, 255, 255),
    "magenta": (255, 0, 220, 255),
    "gray": (128, 128, 128, 255),
    "grey": (128, 128, 128, 255),
}

_FUNC = re.compile(r"^rgba?\(([^)]+)\)$")


def _clamp8(v: float) -> int:
    return max(0, min(255, int(round(v))))


def parse_color(value: Any, theme_colors: dict[str, Any] | None = None, _depth: int = 0) -> RGBA:
    """Parse a color into an (r, g, b, a) tuple of 0-255 ints.

    Accepted forms:
      "#rgb", "#rgba", "#rrggbb", "#rrggbbaa", "rgb(r,g,b)", "rgba(r,g,b,a)",
      named colors, [r, g, b] / [r, g, b, a] lists, and "$token" theme references.
      Any string form may end with "@alpha" (0-1), e.g. "#ffffff@0.4", "$primary@0.5".
    """
    if value is None:
        return (0, 0, 0, 0)
    if isinstance(value, (list, tuple)):
        if len(value) not in (3, 4):
            raise ValueError(f"Color list must have 3 or 4 numbers, got {value!r}")
        r, g, b = (_clamp8(v) for v in value[:3])
        a = 255
        if len(value) == 4:
            av = float(value[3])
            a = _clamp8(av * 255 if av <= 1 else av)
        return (r, g, b, a)
    if not isinstance(value, str):
        raise ValueError(f"Unsupported color value: {value!r}")

    text = value.strip()
    alpha_mul = 1.0
    if "@" in text:
        text, _, alpha_text = text.rpartition("@")
        try:
            alpha_mul = max(0.0, min(1.0, float(alpha_text)))
        except ValueError as exc:
            raise ValueError(f"Bad alpha suffix in color {value!r}") from exc
        text = text.strip()

    if text.startswith("$"):
        if _depth > 8:
            raise ValueError(f"Theme color reference loop at {value!r}")
        key = text[1:]
        if not theme_colors or key not in theme_colors:
            known = ", ".join(sorted(theme_colors or {})) or "(theme has no colors)"
            raise ValueError(f"Unknown theme color '${key}'. Known: {known}")
        r, g, b, a = parse_color(theme_colors[key], theme_colors, _depth + 1)
    elif text.startswith("#"):
        h = text[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8) or not re.fullmatch(r"[0-9a-fA-F]+", h):
            raise ValueError(f"Bad hex color: {value!r}")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) if len(h) == 8 else 255
    elif (m := _FUNC.match(text.lower())) is not None:
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) not in (3, 4):
            raise ValueError(f"Bad rgb()/rgba() color: {value!r}")
        r, g, b = (_clamp8(float(p)) for p in parts[:3])
        a = 255
        if len(parts) == 4:
            av = float(parts[3])
            a = _clamp8(av * 255 if av <= 1 else av)
    elif text.lower() in NAMED:
        r, g, b, a = NAMED[text.lower()]
    else:
        raise ValueError(
            f"Unknown color {value!r}. Use #rrggbb, #rrggbbaa, rgba(r,g,b,a), a named color, or a $theme token."
        )
    return (r, g, b, _clamp8(a * alpha_mul))


def to_hex(color: RGBA) -> str:
    r, g, b, a = color
    return f"#{r:02x}{g:02x}{b:02x}" + (f"{a:02x}" if a != 255 else "")
