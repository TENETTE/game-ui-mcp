"""In-memory document store with JSON persistence, theme and undo."""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

from .fonts import FontLibrary
from .render import Renderer, shift_element

ELEMENT_TYPES = ("rect", "ellipse", "polygon", "line", "path", "text", "icon", "image", "arc", "ticks")
MAX_CANVAS = 8192
MAX_UNDO = 40


def default_workspace() -> Path:
    env = os.environ.get("GAME_UI_MCP_DIR")
    return Path(env).expanduser() if env else Path.home() / "GameUI"


def slugify(name: str, fallback: str = "canvas") -> str:
    # keep letters of any alphabet (ğ, ş, é ...); only spaces and punctuation become underscores
    slug = re.sub(r"[^\w-]+", "_", str(name).strip().replace("İ", "i")).strip("_").lower()
    return slug or fallback


class Store:
    def __init__(self, workspace: Path | None = None, persist: bool = True):
        self.workspace = Path(workspace) if workspace else default_workspace()
        self.persist = persist
        self.projects_dir = self.workspace / "projects"
        self.exports_dir = self.workspace / "exports"
        self.fonts_dir = self.workspace / "fonts"
        if persist:
            for d in (self.projects_dir, self.exports_dir, self.fonts_dir):
                d.mkdir(parents=True, exist_ok=True)
        self.fonts = FontLibrary(self.fonts_dir if persist else None)
        self.canvases: dict[str, dict] = {}
        self.theme: dict[str, dict] = {"colors": {}, "styles": {}}
        self._undo: dict[str, list[dict]] = {}
        if persist:
            self._load()

    # ---- persistence

    def _load(self) -> None:
        theme_file = self.workspace / "theme.json"
        if theme_file.is_file():
            try:
                data = json.loads(theme_file.read_text(encoding="utf-8"))
                self.theme = {"colors": dict(data.get("colors", {})), "styles": dict(data.get("styles", {}))}
            except Exception:
                pass
        for path in sorted(self.projects_dir.glob("*.json")):
            try:
                canvas = json.loads(path.read_text(encoding="utf-8"))
                if {"id", "width", "height", "elements"} <= set(canvas):
                    self.canvases[canvas["id"]] = canvas
            except Exception:
                continue

    def save_canvas(self, canvas: dict) -> None:
        if self.persist:
            path = self.projects_dir / f"{canvas['id']}.json"
            path.write_text(json.dumps(canvas, ensure_ascii=False, indent=1), encoding="utf-8")

    def save_theme(self) -> None:
        if self.persist:
            (self.workspace / "theme.json").write_text(
                json.dumps(self.theme, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- canvases

    def get(self, canvas_id: str) -> dict:
        if canvas_id in self.canvases:
            return self.canvases[canvas_id]
        slug = slugify(canvas_id)
        if slug in self.canvases:
            return self.canvases[slug]
        known = ", ".join(sorted(self.canvases)) or "(none yet - call create_canvas)"
        raise ValueError(f"Canvas '{canvas_id}' not found. Existing canvases: {known}")

    def create_canvas(self, name: str, width: int, height: int, background: Any = None) -> dict:
        width, height = int(width), int(height)
        if not (1 <= width <= MAX_CANVAS and 1 <= height <= MAX_CANVAS):
            raise ValueError(f"Canvas size must be between 1 and {MAX_CANVAS} px per side")
        cid = slugify(name)
        if cid in self.canvases:
            raise ValueError(f"Canvas '{cid}' already exists. Pick another name, or delete_canvas / duplicate_canvas.")
        canvas = {"id": cid, "name": str(name), "width": width, "height": height, "background": background,
                  "elements": [], "counters": {}}
        self._validate_background(canvas)
        self.canvases[cid] = canvas
        self.save_canvas(canvas)
        return canvas

    def _validate_background(self, canvas: dict) -> None:
        probe = dict(canvas, width=min(canvas["width"], 8), height=min(canvas["height"], 8), elements=[])
        Renderer(probe, self.theme, self.fonts).render()

    def update_canvas(self, canvas: dict, **changes: Any) -> None:
        self.checkpoint(canvas)
        for key in ("width", "height"):
            if changes.get(key) is not None:
                value = int(changes[key])
                if not 1 <= value <= MAX_CANVAS:
                    raise ValueError(f"Canvas size must be between 1 and {MAX_CANVAS} px per side")
                canvas[key] = value
        if changes.get("name") is not None:
            canvas["name"] = str(changes["name"])
        if "background" in changes:
            old = canvas.get("background")
            canvas["background"] = changes["background"]
            try:
                self._validate_background(canvas)
            except Exception:
                canvas["background"] = old
                raise
        self.save_canvas(canvas)

    def delete_canvas(self, canvas: dict) -> None:
        self.canvases.pop(canvas["id"], None)
        self._undo.pop(canvas["id"], None)
        if self.persist:
            path = self.projects_dir / f"{canvas['id']}.json"
            if path.is_file():
                path.unlink()

    def duplicate_canvas(self, canvas: dict, new_name: str) -> dict:
        cid = slugify(new_name)
        if cid in self.canvases:
            raise ValueError(f"Canvas '{cid}' already exists")
        clone = copy.deepcopy(canvas)
        clone["id"], clone["name"] = cid, str(new_name)
        self.canvases[cid] = clone
        self.save_canvas(clone)
        return clone

    # ---- undo

    def checkpoint(self, canvas: dict) -> None:
        stack = self._undo.setdefault(canvas["id"], [])
        stack.append(copy.deepcopy(canvas))
        del stack[:-MAX_UNDO]

    def undo(self, canvas: dict) -> bool:
        stack = self._undo.get(canvas["id"])
        if not stack:
            return False
        previous = stack.pop()
        canvas.clear()
        canvas.update(previous)
        self.save_canvas(canvas)
        return True

    def drop_checkpoint(self, canvas: dict) -> None:
        stack = self._undo.get(canvas["id"])
        if stack:
            previous = stack.pop()
            canvas.clear()
            canvas.update(previous)

    # ---- elements

    def renderer(self, canvas: dict, scale: float = 1.0) -> Renderer:
        return Renderer(canvas, self.theme, self.fonts, scale)

    def find(self, canvas: dict, element_id: str) -> dict:
        for el in canvas["elements"]:
            if el["id"] == element_id:
                return el
        ids = ", ".join(e["id"] for e in canvas["elements"]) or "(canvas is empty)"
        raise ValueError(f"Element '{element_id}' not found on canvas '{canvas['id']}'. Elements: {ids}")

    def select(self, canvas: dict, ids: list[str] | None = None, group: str | None = None) -> list[dict]:
        if ids:
            return [self.find(canvas, i) for i in ids]
        if group:
            found = [e for e in canvas["elements"] if e.get("group") == group]
            if not found:
                groups = ", ".join(sorted({e["group"] for e in canvas["elements"] if e.get("group")})) or "(none)"
                raise ValueError(f"No elements in group '{group}'. Groups: {groups}")
            return found
        raise ValueError("Give either ids or group")

    def _new_id(self, canvas: dict, etype: str, wanted: str | None) -> str:
        existing = {e["id"] for e in canvas["elements"]}
        if wanted:
            eid = slugify(wanted, etype)
            if eid in existing:
                raise ValueError(f"Element id '{eid}' is already used on this canvas")
            return eid
        counters = canvas.setdefault("counters", {})
        while True:
            counters[etype] = counters.get(etype, 0) + 1
            eid = f"{etype}{counters[etype]}"
            if eid not in existing:
                return eid

    def _validate(self, canvas: dict, el: dict) -> list[str]:
        """Render the element once at low resolution so bad input fails immediately."""
        scale = min(1.0, 256.0 / max(canvas["width"], canvas["height"]))
        r = self.renderer(canvas, max(scale, 0.05))
        r.render_element(el)
        return r.warnings

    def add_element(self, canvas: dict, etype: str, props: dict, checkpoint: bool = True) -> tuple[dict, list[str]]:
        if etype not in ELEMENT_TYPES:
            raise ValueError(f"Unknown element type '{etype}'. Types: {', '.join(ELEMENT_TYPES)}")
        props = {k: v for k, v in props.items() if v is not None}
        el = {"id": self._new_id(canvas, etype, props.pop("id", None)), "type": etype}
        el.update(props)
        if el.get("clip"):
            self.find(canvas, el["clip"])
        if el.get("layer"):
            el["layer"] = self.get_layer(canvas, el["layer"])["name"]
        elif canvas.get("active_layer") and any(l["name"] == canvas["active_layer"] for l in canvas.get("layers", [])):
            el["layer"] = canvas["active_layer"]
        if checkpoint:
            self.checkpoint(canvas)
        canvas["elements"].append(el)
        try:
            warnings = self._validate(canvas, el)
        except Exception:
            canvas["elements"].remove(el)
            if checkpoint:
                self._undo[canvas["id"]].pop()
            raise
        self.save_canvas(canvas)
        return el, warnings

    def update_element(self, canvas: dict, el: dict, props: dict, checkpoint: bool = True) -> list[str]:
        if "id" in props or "type" in props:
            raise ValueError("id and type cannot be changed; duplicate or re-add the element instead")
        backup = copy.deepcopy(el)
        if checkpoint:
            self.checkpoint(canvas)
        for key, value in props.items():
            if key == "style" and isinstance(value, dict) and isinstance(el.get("style"), (dict, str)):
                merged = dict(el["style"]) if isinstance(el["style"], dict) else {"use": el["style"]}
                for sk, sv in value.items():
                    if sv is None:
                        merged.pop(sk, None)
                    else:
                        merged[sk] = sv
                el["style"] = merged
            elif value is None:
                el.pop(key, None)
            else:
                el[key] = value
        try:
            if el.get("clip"):
                self.find(canvas, el["clip"])
            if el.get("layer"):
                if str(el["layer"]).lower() == "base":
                    el.pop("layer")
                else:
                    el["layer"] = self.get_layer(canvas, el["layer"])["name"]
            warnings = self._validate(canvas, el)
        except Exception:
            el.clear()
            el.update(backup)
            if checkpoint:
                self._undo[canvas["id"]].pop()
            raise
        self.save_canvas(canvas)
        return warnings

    def delete_elements(self, canvas: dict, elements: list[dict], checkpoint: bool = True) -> list[str]:
        if checkpoint:
            self.checkpoint(canvas)
        ids = {e["id"] for e in elements}
        canvas["elements"] = [e for e in canvas["elements"] if e["id"] not in ids]
        for e in canvas["elements"]:
            if e.get("clip") in ids:
                e.pop("clip", None)
        for anim in canvas.get("animations", {}).values():
            anim["tracks"] = [t for t in anim["tracks"] if t["target"] not in ids]
        self.save_canvas(canvas)
        return sorted(ids)

    @staticmethod
    def shift(el: dict, dx: float, dy: float) -> None:
        shift_element(el, dx, dy)

    def move_elements(self, canvas: dict, elements: list[dict], dx: float, dy: float, checkpoint: bool = True) -> None:
        if checkpoint:
            self.checkpoint(canvas)
        for el in elements:
            self.shift(el, float(dx), float(dy))
        self.save_canvas(canvas)

    def reorder(self, canvas: dict, el: dict, position: Any, checkpoint: bool = True) -> int:
        others = [e for e in canvas["elements"] if e is not el]
        idx = canvas["elements"].index(el)
        pos = str(position).lower().strip()
        if pos == "front":
            new = len(others)
        elif pos == "back":
            new = 0
        elif pos == "forward":
            new = min(len(others), idx + 1)
        elif pos == "backward":
            new = max(0, idx - 1)
        elif pos.startswith(("above:", "below:")):
            target = str(position).split(":", 1)[1].strip()
            if target == el["id"]:
                raise ValueError("cannot reorder an element relative to itself")
            other = self.find(canvas, target)
            new = others.index(other) + (1 if pos.startswith("above:") else 0)
        else:
            try:
                new = max(0, min(len(others), int(position)))
            except (TypeError, ValueError):
                raise ValueError("position must be 'front', 'back', 'forward', 'backward', 'above:<id>', "
                                 "'below:<id>' or an index") from None
        if checkpoint:
            self.checkpoint(canvas)
        others.insert(new, el)
        canvas["elements"] = others
        self.save_canvas(canvas)
        return new

    def duplicate_element(self, canvas: dict, el: dict, dx: float, dy: float, new_id: str | None,
                          props: dict | None) -> tuple[dict, list[str]]:
        tmp = copy.deepcopy(el)
        tmp.pop("id")
        self.shift(tmp, float(dx), float(dy))
        etype = tmp.pop("type")
        if new_id:
            tmp["id"] = new_id
        new_el, warnings = self.add_element(canvas, etype, tmp)
        if props:
            warnings = self.update_element(canvas, new_el, props, checkpoint=False)
        return new_el, warnings

    # ---- layers

    def get_layer(self, canvas: dict, name: str) -> dict:
        layers = canvas.get("layers") or []
        key = slugify(name, "layer")
        for layer in layers:
            if layer["name"] in (name, key):
                return layer
        known = ", ".join(l["name"] for l in layers) or "(none yet - call create_layer)"
        raise ValueError(f"Layer '{name}' not found on canvas '{canvas['id']}'. Layers (bottom to top): {known}. "
                         "Elements without a layer sit in the implicit bottom layer 'base'.")

    def _layer_index(self, canvas: dict, position: Any, moving: dict | None = None) -> int:
        others = [l for l in canvas.get("layers", []) if l is not moving]
        if position is None:
            return len(others)
        pos = str(position).lower().strip()
        if pos in ("top", "front"):
            return len(others)
        if pos in ("bottom", "back"):
            return 0
        if pos.startswith(("above:", "below:")):
            target = self.get_layer(canvas, str(position).split(":", 1)[1].strip())
            if target is moving:
                raise ValueError("cannot place a layer relative to itself")
            return others.index(target) + (1 if pos.startswith("above:") else 0)
        try:
            return max(0, min(len(others), int(position)))
        except (TypeError, ValueError):
            raise ValueError("layer position must be 'top', 'bottom', 'above:<layer>', 'below:<layer>' or an index "
                             "(0 = lowest)") from None

    def _check_layer(self, canvas: dict, layer: dict) -> None:
        """Render a tiny probe through the layer so a bad style / blend fails immediately."""
        probe = {"id": "layer_probe", "width": 24, "height": 24, "background": None,
                 "layers": [dict(layer, clip=False, visible=True)],
                 "elements": [{"id": "p", "type": "rect", "x": 6, "y": 6, "width": 12, "height": 12,
                               "layer": layer["name"]}]}
        Renderer(probe, self.theme, self.fonts).render()

    def create_layer(self, canvas: dict, name: str, settings: dict, position: Any, checkpoint: bool = True) -> dict:
        key = slugify(name, "layer")
        if key == "base":
            raise ValueError("'base' is the name of the implicit bottom layer; pick another name")
        layers = canvas.setdefault("layers", [])
        if any(l["name"] == key for l in layers):
            raise ValueError(f"Layer '{key}' already exists; use update_layer to change it")
        layer = {"name": key}
        layer.update({k: v for k, v in settings.items() if v is not None})
        self._check_layer(canvas, layer)
        index = self._layer_index(canvas, position)
        if checkpoint:
            self.checkpoint(canvas)
        layers.insert(index, layer)
        canvas["active_layer"] = key
        self.save_canvas(canvas)
        return layer

    def update_layer(self, canvas: dict, layer: dict, changes: dict, position: Any = None,
                     activate: bool | None = None, checkpoint: bool = True) -> dict:
        updated = dict(layer)
        for key, value in changes.items():
            if key == "style" and isinstance(value, dict) and isinstance(updated.get("style"), (dict, str)):
                merged = dict(updated["style"]) if isinstance(updated["style"], dict) else {"use": updated["style"]}
                for sk, sv in value.items():
                    if sv is None:
                        merged.pop(sk, None)
                    else:
                        merged[sk] = sv
                updated["style"] = merged
            elif value is None:
                continue
            else:
                updated[key] = value
        if "name" in changes and changes["name"]:
            new_name = slugify(changes["name"], "layer")
            if new_name == "base" or any(l is not layer and l["name"] == new_name for l in canvas["layers"]):
                raise ValueError(f"Layer name '{new_name}' is already taken")
            updated["name"] = new_name
        self._check_layer(canvas, updated)
        index = self._layer_index(canvas, position, layer) if position is not None else None
        if checkpoint:
            self.checkpoint(canvas)
            layer = self.get_layer(canvas, layer["name"])          # the checkpoint copied nothing, same object
        old_name = layer["name"]
        layer.clear()
        layer.update(updated)
        if layer["name"] != old_name:
            for el in canvas["elements"]:
                if el.get("layer") == old_name:
                    el["layer"] = layer["name"]
            if canvas.get("active_layer") == old_name:
                canvas["active_layer"] = layer["name"]
        if index is not None:
            canvas["layers"] = [l for l in canvas["layers"] if l is not layer]
            canvas["layers"].insert(index, layer)
        if activate:
            canvas["active_layer"] = layer["name"]
        self.save_canvas(canvas)
        return layer

    def delete_layer(self, canvas: dict, layer: dict, keep_elements: bool) -> list[str]:
        self.checkpoint(canvas)
        layer = self.get_layer(canvas, layer["name"])
        name = layer["name"]
        affected = [e["id"] for e in canvas["elements"] if e.get("layer") == name]
        if keep_elements:
            for el in canvas["elements"]:
                if el.get("layer") == name:
                    el.pop("layer")
        else:
            ids = set(affected)
            canvas["elements"] = [e for e in canvas["elements"] if e["id"] not in ids]
            for e in canvas["elements"]:
                if e.get("clip") in ids:
                    e.pop("clip", None)
            for anim in canvas.get("animations", {}).values():
                anim["tracks"] = [t for t in anim["tracks"] if t["target"] not in ids]
        canvas["layers"] = [l for l in canvas["layers"] if l is not layer]
        if canvas.get("active_layer") == name:
            canvas["active_layer"] = canvas["layers"][-1]["name"] if canvas["layers"] else None
        self.save_canvas(canvas)
        return affected

    def assign_layer(self, canvas: dict, elements: list[dict], layer_name: str | None) -> None:
        self.checkpoint(canvas)
        ids = {e["id"] for e in elements}
        for el in canvas["elements"]:
            if el["id"] in ids:
                if layer_name:
                    el["layer"] = layer_name
                else:
                    el.pop("layer", None)
        self.save_canvas(canvas)

    # ---- animations

    def animations(self, canvas: dict) -> dict:
        return canvas.setdefault("animations", {})

    def get_animation(self, canvas: dict, name: str) -> tuple[str, dict]:
        anims = self.animations(canvas)
        key = name if name in anims else slugify(name, "anim")
        if key not in anims:
            known = ", ".join(sorted(anims)) or "(none yet - call create_animation)"
            raise ValueError(f"Animation '{name}' not found on canvas '{canvas['id']}'. Animations: {known}")
        return key, anims[key]

    def create_animation(self, canvas: dict, name: str, duration: float, loop: bool, fps: float) -> tuple[str, dict, bool]:
        duration, fps = float(duration), float(fps)
        if not 0.01 <= duration <= 120:
            raise ValueError("duration must be between 0.01 and 120 seconds")
        if not 1 <= fps <= 120:
            raise ValueError("fps must be between 1 and 120")
        key = slugify(name, "anim")
        anims = self.animations(canvas)
        existing = anims.get(key)
        if existing:
            latest = max((k["t"] for tr in existing["tracks"] for k in tr["keyframes"]), default=0.0)
            if latest > duration + 1e-9:
                raise ValueError(f"duration {duration:g}s is shorter than an existing keyframe at {latest:g}s")
        self.checkpoint(canvas)
        if existing:
            existing.update(duration=duration, loop=bool(loop), fps=fps)
        else:
            anims[key] = {"duration": duration, "loop": bool(loop), "fps": fps, "tracks": []}
        self.save_canvas(canvas)
        return key, anims[key], existing is None

    def set_track(self, canvas: dict, anim: dict, target: str, prop: str, keyframes: list[dict],
                  pivot: list[float] | None, checkpoint: bool = True) -> tuple[list[dict], list[str]]:
        from .animation import apply_animation, track_targets
        prop = str(prop).strip()
        if not prop or prop.split(".")[0] in ("id", "type", "group", "clip"):
            raise ValueError(f"'{prop}' cannot be animated")
        elements = track_targets(canvas, target)
        if not elements:
            ids = ", ".join(e["id"] for e in canvas["elements"]) or "(canvas is empty)"
            groups = ", ".join(sorted({e["group"] for e in canvas["elements"] if e.get("group")})) or "(none)"
            raise ValueError(f"Animation target '{target}' not found. Use an element id ({ids}) or "
                             f"'group:<name>' (groups: {groups}).")
        track = {"target": target, "property": prop, "keyframes": keyframes}
        if pivot:
            if len(pivot) != 2:
                raise ValueError("pivot must be [x, y]")
            track["pivot"] = [float(pivot[0]), float(pivot[1])]
        # pose the canvas at every keyframe and render the targets once, so bad values fail now
        warnings: list[str] = []
        probe = {"tracks": [track]}
        for key in keyframes:
            frame, _ = apply_animation(canvas, probe, key["t"], self.theme.get("colors", {}))
            for el in track_targets(frame, target):
                try:
                    warnings += self._validate(frame, el)
                except Exception as exc:
                    raise ValueError(f"keyframe at {key['t']:g}s ({prop} = {key['value']!r}) cannot be drawn: {exc}") from exc
        if checkpoint:
            self.checkpoint(canvas)
        anim["tracks"] = [t for t in anim["tracks"] if not (t["target"] == target and t["property"] == prop)]
        anim["tracks"].append(track)
        self.save_canvas(canvas)
        return elements, sorted(set(warnings))

    def delete_tracks(self, canvas: dict, anim: dict, target: str | None, prop: str | None) -> int:
        keep = [t for t in anim["tracks"]
                if not ((target is None or t["target"] == target) and (prop is None or t["property"] == prop))]
        removed = len(anim["tracks"]) - len(keep)
        if removed:
            self.checkpoint(canvas)
            anim["tracks"] = keep
            self.save_canvas(canvas)
        return removed

    def delete_animation(self, canvas: dict, key: str) -> None:
        self.checkpoint(canvas)
        self.animations(canvas).pop(key, None)
        self.save_canvas(canvas)

    # ---- theme

    def set_theme(self, colors: dict | None, styles: dict | None, replace: bool) -> None:
        old = copy.deepcopy(self.theme)
        if replace:
            self.theme = {"colors": {}, "styles": {}}
        for key, value in (colors or {}).items():
            if value is None:
                self.theme["colors"].pop(key, None)
            else:
                self.theme["colors"][key] = value
        for key, value in (styles or {}).items():
            if value is None:
                self.theme["styles"].pop(key, None)
            elif not isinstance(value, dict):
                self.theme = old
                raise ValueError(f"Theme style '{key}' must be an object")
            else:
                self.theme["styles"][key] = value
        try:
            from .colors import parse_color
            from .render import resolve_style
            for value in self.theme["colors"].values():
                parse_color(value, self.theme["colors"])
            probe = {"id": "theme_probe", "width": 16, "height": 16, "background": None, "elements": []}
            for name in self.theme["styles"]:
                st = resolve_style(name, self.theme)
                shape_style = {k: v for k, v in st.items() if k not in ("font", "weight", "size", "letter_spacing",
                                                                       "line_height", "uppercase", "italic")}
                el = {"id": "p", "type": "rect", "x": 2, "y": 2, "width": 12, "height": 12, "style": shape_style}
                probe["elements"] = [el]
                Renderer(probe, self.theme, self.fonts).render()
                if st.get("font"):
                    self.fonts.resolve(st["font"], 400)
        except Exception as exc:
            self.theme = old
            raise ValueError(f"Theme rejected: {exc}") from exc
        self.save_theme()
