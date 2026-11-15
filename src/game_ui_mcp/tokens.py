"""W3C Design Tokens (DTCG) JSON: read colour / number tokens in, write the theme out.

Format: nested groups; a token is an object with "$value" (and "$type", possibly inherited from its group).
Aliases look like "{group.token}". Used by Tokens Studio, Penpot, Style Dictionary.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_ALIAS = re.compile(r"^\{([^{}]+)\}$")


def _name(parts: list[str]) -> str:
    return "-".join(re.sub(r"[^\w-]+", "-", p).strip("-").lower() for p in parts if p)


def _dimension(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict) and "value" in value:
        scale = 16.0 if str(value.get("unit", "px")) == "rem" else 1.0
        return float(value["value"]) * scale
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(px|rem|em)?\s*", value)
        if m:
            return float(m.group(1)) * (16.0 if m.group(2) in ("rem", "em") else 1.0)
    return None


def _color(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        comps = value.get("components")
        if isinstance(comps, list) and len(comps) >= 3:
            rgb = "".join(f"{max(0, min(255, round(float(c) * 255))):02x}" for c in comps[:3])
            alpha = float(value.get("alpha", 1))
            return f"#{rgb}" + (f"{round(alpha * 255):02x}" if alpha < 1 else "")
        if "hex" in value:
            return str(value["hex"])
    return None


def read_dtcg(data: dict | str) -> tuple[dict, dict]:
    """(colours, numbers) from a DTCG document or a path to one."""
    if isinstance(data, str):
        path = Path(data).expanduser()
        if not path.is_file():
            raise ValueError(f"tokens file not found: {path.resolve()} - pass the JSON object or an absolute path")
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("tokens must be a DTCG JSON object: nested groups of {\"$value\": ..., \"$type\": ...}")
    colors: dict[str, str] = {}
    numbers: dict[str, float] = {}

    def walk(node: dict, path: list[str], inherited: str | None) -> None:
        kind = node.get("$type", inherited)
        if "$value" in node:
            name, value = _name(path), node["$value"]
            alias = _ALIAS.match(value) if isinstance(value, str) else None
            if alias:
                target = _name(alias.group(1).split("."))
                (colors if kind == "color" else numbers if kind in ("dimension", "number") else colors)[name] = f"${target}"
            elif kind == "color" and _color(value):
                colors[name] = _color(value)
            elif kind in ("dimension", "number", "fontWeight", "duration") and _dimension(value) is not None:
                numbers[name] = _dimension(value)
            return
        for key, child in node.items():
            if not key.startswith("$") and isinstance(child, dict):
                walk(child, path + [key], kind)

    walk(data, [], None)
    for name, value in list(numbers.items()):
        seen = set()
        while isinstance(value, str) and value.startswith("$") and value[1:] in numbers and value not in seen:
            seen.add(value)
            value = numbers[value[1:]]
        if isinstance(value, str):
            numbers.pop(name)
        else:
            numbers[name] = value
    if not colors and not numbers:
        raise ValueError("no colour or number tokens found - check that tokens have \"$value\" and a "
                         "\"$type\" of color / dimension / number (on the token or its group)")
    return colors, numbers


def write_dtcg(colors: dict, numbers: dict, text_styles: dict, modes: dict, mode_numbers: dict) -> dict:
    doc: dict[str, Any] = {}
    if colors:
        doc["color"] = {k: {"$type": "color", "$value": v} for k, v in colors.items()}
    if numbers:
        doc["size"] = {k: {"$type": "dimension", "$value": f"{v:g}px"} for k, v in numbers.items()}
    if text_styles:
        doc["typography"] = {
            k: {"$type": "typography", "$value": {
                key: val for key, val in (("fontFamily", ts.get("font")), ("fontSize", f"{ts['size']:g}px" if ts.get("size") else None),
                                          ("fontWeight", ts.get("weight")),
                                          ("letterSpacing", f"{ts['letter_spacing']:g}px" if ts.get("letter_spacing") else None))
                if val is not None}}
            for k, ts in text_styles.items()}
    if modes or any(mode_numbers.values()):
        doc["$extensions"] = {"game-ui-mcp": {"modes": {
            m: {**{k: v for k, v in modes.get(m, {}).items()}, **{k: v for k, v in mode_numbers.get(m, {}).items()}}
            for m in sorted(set(modes) | set(mode_numbers))}}}
    return doc
