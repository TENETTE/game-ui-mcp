"""Minimal Photoshop (.psd) writer: 8-bit RGB with alpha, one raster layer per canvas layer.

Layers keep their name, opacity, blend mode, visibility and clipping flag, so the file opens as an
editable stack in Photoshop, Photopea, Krita, GIMP and Affinity. Pixels are stored uncompressed.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from PIL import Image

BLEND_KEYS = {
    "normal": b"norm", "multiply": b"mul ", "screen": b"scrn", "overlay": b"over", "add": b"lddg",
    "soft-light": b"sLit", "hard-light": b"hLit", "color-dodge": b"div ", "color-burn": b"idiv",
    "darken": b"dark", "lighten": b"lite", "difference": b"diff",
}


def _pascal(name: str, multiple: int) -> bytes:
    raw = name.encode("ascii", "replace")[:255]
    data = bytes([len(raw)]) + raw
    return data + b"\0" * (-len(data) % multiple)


def _unicode_name(name: str) -> bytes:
    data = struct.pack(">I", len(name)) + name.encode("utf-16-be")
    data += b"\0" * (-len(data) % 4)
    return b"8BIM" + b"luni" + struct.pack(">I", len(data)) + data


def write_psd(path: Path, width: int, height: int, layers: list[dict], merged: Image.Image) -> None:
    """layers: bottom to top, each {"name", "image" (RGBA, canvas size), "opacity" 0-1, "blend", "clip", "visible"}.
    merged: the flattened picture (RGBA, canvas size) stored as the file's preview/composite."""
    records = bytearray()
    pixels = bytearray()
    for layer in layers:
        img = layer["image"].convert("RGBA")
        box = img.getchannel("A").getbbox()
        if box is None:
            left = top = right = bottom = 0
            planes = [b"", b"", b"", b""]
        else:
            left, top, right, bottom = box
            arr = np.asarray(img.crop(box))
            planes = [arr[..., 3].tobytes(), arr[..., 0].tobytes(), arr[..., 1].tobytes(), arr[..., 2].tobytes()]
        rec = struct.pack(">4i", top, left, bottom, right) + struct.pack(">H", 4)
        for channel_id, plane in zip((-1, 0, 1, 2), planes):
            rec += struct.pack(">hI", channel_id, len(plane) + 2)
            pixels += struct.pack(">H", 0) + plane
        blend = BLEND_KEYS.get(str(layer.get("blend", "normal")), b"norm")
        opacity = max(0, min(255, int(round(float(layer.get("opacity", 1)) * 255))))
        flags = 8 | (0 if layer.get("visible", True) else 2)
        rec += b"8BIM" + blend + struct.pack(">4B", opacity, 1 if layer.get("clip") else 0, flags, 0)
        extra = struct.pack(">I", 0) + struct.pack(">I", 0)
        extra += _pascal(layer["name"], 4) + _unicode_name(layer["name"])
        rec += struct.pack(">I", len(extra)) + extra
        records += rec

    layer_info = struct.pack(">h", -len(layers)) + bytes(records) + bytes(pixels)
    layer_info += b"\0" * (len(layer_info) % 2)
    section = struct.pack(">I", len(layer_info)) + layer_info + struct.pack(">I", 0)

    flat = np.asarray(merged.convert("RGBA"))
    with open(path, "wb") as handle:
        handle.write(b"8BPS" + struct.pack(">H", 1) + b"\0" * 6)
        handle.write(struct.pack(">HIIHH", 4, height, width, 8, 3))
        handle.write(struct.pack(">I", 0))
        handle.write(struct.pack(">I", 0))
        handle.write(struct.pack(">I", len(section)) + section)
        handle.write(struct.pack(">H", 0))
        for c in range(4):
            handle.write(flat[..., c].tobytes())
