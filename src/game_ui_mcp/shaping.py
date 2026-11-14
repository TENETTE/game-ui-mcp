"""Complex text: bidi (python-bidi) + shaping (HarfBuzz) + per-character font fallback.

Pillow's own layout on Windows has no Raqm, so Arabic comes out disconnected and left-to-right,
Indic conjuncts never form and the language tag is ignored. Text that needs any of that goes
through here instead; plain Latin / Greek / Cyrillic text keeps Pillow's path.
Glyph outlines are drawn with the exact scanline rasteriser at the renderer's supersampling.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

import numpy as np
import uharfbuzz as hb
from PIL import Image
from bidi import algorithm as _bidi

from . import raster
from .fonts import BUNDLED_DIR

NOTO_DIR = BUNDLED_DIR / "noto"
_NOTO = ("Arabic", "Hebrew", "Devanagari", "Bengali", "Tamil", "Thai")

# Han glyphs differ per language, so the CJK system font is chosen by the language tag.
_CJK = {
    "ja": ["Yu Gothic", "Yu Gothic UI", "Meiryo", "MS Gothic", "Hiragino Sans", "Noto Sans CJK JP", "Noto Sans JP"],
    "zh-hans": ["Microsoft YaHei", "Microsoft YaHei UI", "SimSun", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"],
    "zh-hant": ["Microsoft JhengHei", "Microsoft JhengHei UI", "PingFang TC", "Noto Sans CJK TC", "Noto Sans TC"],
    "ko": ["Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans CJK KR", "Noto Sans KR"],
}
_SYSTEM = ["Segoe UI", "Nirmala UI", "Leelawadee UI", "Kohinoor Devanagari", "Thonburi", "Noto Sans",
           "DejaVu Sans", "Segoe UI Symbol", "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji"]

# Characters that may not start a CJK line (kinsoku) and ones that may not end it.
_NO_START = set("、。，．・：；？！ー）」』】〕〉》〙〗｝］’”ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ々〻…‥")
_NO_END = set("（「『【〔〈《〘〖｛［‘“")


def norm_lang(lang: str | None) -> str | None:
    if not lang:
        return None
    k = str(lang).strip().replace("_", "-").lower()
    if k in ("zh", "zh-cn", "zh-sg", "zh-hans-cn"):
        return "zh-hans"
    if k in ("zh-tw", "zh-hk", "zh-mo"):
        return "zh-hant"
    return k


def _is_cjk(cp: int) -> bool:
    return (0x3040 <= cp <= 0x30FF or 0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF
            or 0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF or 0x20000 <= cp <= 0x3FFFF)


def _is_han(cp: int) -> bool:
    return 0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x3FFFF


def _simple(cp: int) -> bool:
    """Scripts Pillow's basic layout draws correctly: Latin, Greek, Cyrillic, common punctuation."""
    return cp < 0x0590 or 0x1E00 <= cp <= 0x1FFF or 0x2000 <= cp <= 0x2BFF and not 0x2066 <= cp <= 0x2069 \
        and not 0x202A <= cp <= 0x202E and cp not in (0x200D, 0x200E, 0x200F)


def needs_shaping(text: str, covered) -> bool:
    return any(not ch.isspace() and (not _simple(ord(ch)) or not covered(ord(ch))) for ch in text)


def guess_lang(text: str, lang: str | None) -> tuple[str | None, str | None]:
    """(language to shape with, warning). Han without a language tag is ambiguous (JP vs SC vs TC)."""
    lang = norm_lang(lang)
    if lang and not lang.startswith("pseudo"):
        return lang, None
    if not any(_is_han(ord(c)) for c in text):
        return None, None
    if any(0x3040 <= ord(c) <= 0x30FF for c in text):
        return "ja", None
    if any(0xAC00 <= ord(c) <= 0xD7AF for c in text):
        return "ko", None
    return "zh-hans", ("Han characters without a language tag were drawn as Simplified Chinese; "
                       "set language ('ja', 'zh-Hans', 'zh-Hant', 'ko') so the right glyph forms are used")


# --------------------------------------------------------------------------- fonts

@lru_cache(maxsize=64)
def _face(path: str):
    face = hb.Face(hb.Blob.from_file_path(path), 0)
    return face, frozenset(face.unicodes)


class _Font:
    def __init__(self, path: str, var_weight: int | None, size_px: float):
        self.path = path
        self.face, self.cmap = _face(path)
        self.font = hb.Font(self.face)
        self.var = None
        if var_weight is not None:
            self.var = {"wght": float(var_weight), "opsz": float(size_px)}
            self.font.set_variations(self.var)
        self.scale = size_px / self.face.upem
        ext = self.font.get_font_extents("ltr")
        self.ascent, self.descent = ext.ascender * self.scale, -ext.descender * self.scale


@lru_cache(maxsize=128)
def fallback_chain(fonts, primary_path: str | None, var_weight: int | None, weight: int, size_px: float,
                   lang: str | None) -> tuple[_Font, ...]:
    chain = []
    if primary_path:
        chain.append(_Font(primary_path, var_weight, size_px))
    bold = weight >= 600
    for script in _NOTO:
        p = NOTO_DIR / f"NotoSans{script}-{'Bold' if bold else 'Regular'}.ttf"
        if p.is_file():
            chain.append(_Font(str(p), None, size_px))
    cjk = _CJK.get(lang or "", []) + [f for k, v in _CJK.items() if k != lang for f in v]
    for family in cjk + _SYSTEM:
        try:
            path, vw, _ = fonts.resolve(family, weight)
        except ValueError:
            continue
        if path and path not in {f.path for f in chain}:
            chain.append(_Font(path, vw, size_px))
    return tuple(chain)


# --------------------------------------------------------------------------- shaping

class _Pen:
    def __init__(self) -> None:
        self.contours: list[list[tuple[float, float]]] = []

    def moveTo(self, p):
        self.contours.append([p])

    def lineTo(self, p):
        self.contours[-1].append(p)

    def qCurveTo(self, c, p):
        p0 = self.contours[-1][-1]
        for t in np.linspace(0, 1, 9)[1:]:
            u = 1 - t
            self.contours[-1].append((u * u * p0[0] + 2 * u * t * c[0] + t * t * p[0],
                                      u * u * p0[1] + 2 * u * t * c[1] + t * t * p[1]))

    def curveTo(self, c1, c2, p):
        p0 = self.contours[-1][-1]
        for t in np.linspace(0, 1, 13)[1:]:
            u = 1 - t
            self.contours[-1].append((u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p[0],
                                      u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p[1]))

    def closePath(self):
        pass


@lru_cache(maxsize=4096)
def _outline(path: str, var: tuple | None, gid: int) -> tuple:
    font = hb.Font(_face(path)[0])
    if var:
        font.set_variations(dict(var))
    pen = _Pen()
    font.draw_glyph_with_pen(gid, pen)
    return tuple(np.asarray(c, dtype=np.float64) for c in pen.contours if len(c) >= 3)


def _levels(text: str) -> list[int]:
    st = _bidi.get_empty_storage()
    st["base_level"] = _bidi.get_base_level(text)
    st["base_dir"] = ("L", "R")[st["base_level"]]
    _bidi.get_embedding_levels(text, st, False, False)
    _bidi.explicit_embed_and_overrides(st, False)
    _bidi.resolve_weak_types(st, False)
    _bidi.resolve_neutral_types(st, False)
    _bidi.resolve_implicit_levels(st, False)
    levels = [0] * len(text)
    # explicit embedding controls are dropped from storage["chars"]; map the rest back by order
    it = iter(st["chars"])
    for i, ch in enumerate(text):
        if 0x202A <= ord(ch) <= 0x202E or 0x2066 <= ord(ch) <= 0x2069:
            levels[i] = levels[i - 1] if i else st["base_level"]
            continue
        levels[i] = next(it)["level"]
    return levels


class ShapedText:
    """Shapes lines of one text element through a fallback chain. Positions are in pixels."""

    def __init__(self, chain: list[_Font], lang: str | None, spacing_px: float):
        self.chain = chain
        self.lang = lang
        self.spacing = spacing_px
        self.missing: set[str] = set()
        self.used: set[int] = set()
        self.fallback: dict[int, set[str]] = {}       # chain index -> characters the primary font lacked
        self._cache: dict[str, tuple] = {}

    def _pick(self, text: str) -> list[int]:
        out: list[int] = []
        prev = None
        for ch in text:
            cp, cat = ord(ch), unicodedata.category(ch)
            # marks, joiners and covered neutrals (spaces, digits, punctuation) stay in the current run
            if prev is not None and (cat[0] == "M" or cat in ("Cf", "Cc")
                                     or (cat[0] != "L" and cp in self.chain[prev].cmap)):
                out.append(prev)
                continue
            idx = next((i for i, f in enumerate(self.chain) if cp in f.cmap), None)
            if idx is None:
                if not ch.isspace() and cat not in ("Cf", "Cc"):
                    self.missing.add(ch)
                idx = prev if prev is not None else 0
            elif idx and not ch.isspace():
                self.fallback.setdefault(idx, set()).add(ch)
            out.append(idx)
            prev = idx
        return out

    def shape(self, text: str) -> tuple[list[tuple[int, int, float, float]], float]:
        """Visual-order glyphs (font index, glyph id, x px, y offset px) and the advance width."""
        if text not in self._cache:
            self._cache[text] = self._shape(text)
        return self._cache[text]

    def _shape(self, text: str) -> tuple[list[tuple[int, int, float, float]], float]:
        if not text or not self.chain:
            return [], 0.0
        levels = _levels(text)
        picks = self._pick(text)
        runs: list[tuple[int, int, int, int]] = []        # (start, end, level, font)
        start = 0
        for i in range(1, len(text) + 1):
            if i == len(text) or levels[i] != levels[start] or picks[i] != picks[start]:
                runs.append((start, i, levels[start], picks[start]))
                start = i
        # rule L2: reverse every maximal sequence of runs at or above each odd level
        order = list(range(len(runs)))
        top = max(r[2] for r in runs)
        low_odd = min((r[2] for r in runs if r[2] % 2), default=top + 1)
        for lvl in range(top, low_odd - 1, -1):
            i = 0
            while i < len(order):
                if runs[order[i]][2] >= lvl:
                    j = i
                    while j < len(order) and runs[order[j]][2] >= lvl:
                        j += 1
                    order[i:j] = order[i:j][::-1]
                    i = j
                else:
                    i += 1
        glyphs: list[tuple[int, int, float, float]] = []
        x = 0.0
        for ri in order:
            s, e, lvl, fi = runs[ri]
            f = self.chain[fi]
            self.used.add(fi)
            buf = hb.Buffer()
            buf.add_str(text, s, e - s)
            buf.guess_segment_properties()
            buf.direction = "rtl" if lvl % 2 else "ltr"
            if self.lang:
                buf.language = self.lang
            hb.shape(f.font, buf, {})
            prev_cluster = None
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
                if info.codepoint == 0:
                    ch = text[info.cluster] if info.cluster < len(text) else ""
                    if ch and not ch.isspace() and unicodedata.category(ch) not in ("Cf", "Cc"):
                        self.missing.add(ch)
                if self.spacing and prev_cluster is not None and info.cluster != prev_cluster:
                    x += self.spacing          # shortcut: also spaces joined Arabic letters; per-script rule if asked
                prev_cluster = info.cluster
                glyphs.append((fi, info.codepoint, x + pos.x_offset * f.scale, pos.y_offset * f.scale))
                x += pos.x_advance * f.scale
        return glyphs, x

    def width(self, text: str) -> float:
        return self.shape(text)[1]

    # ---- line breaking

    @staticmethod
    def _segments(text: str) -> list[str]:
        """Pieces between break opportunities: after spaces, and between CJK characters (kinsoku-aware)."""
        segs: list[str] = []
        cur = ""
        for i, ch in enumerate(text):
            nxt = text[i + 1] if i + 1 < len(text) else ""
            cur += ch
            if not nxt:
                break
            if ch == " " and nxt != " ":
                segs.append(cur)
                cur = ""
            elif (_is_cjk(ord(ch)) or _is_cjk(ord(nxt))) and nxt not in _NO_START and ch not in _NO_END \
                    and not unicodedata.category(nxt).startswith("M") and nxt != " ":
                segs.append(cur)
                cur = ""
        if cur:
            segs.append(cur)
        return segs

    def _split_long(self, word: str, max_px: float) -> list[str]:
        """Emergency break inside a word at character-cluster boundaries (Thai, Lao, Khmer have no
        spaces; shortcut: no dictionary segmentation, breaks may fall mid-word - add one if Thai ships)."""
        parts, cur = [], ""
        for ch in word:
            joins = unicodedata.category(ch).startswith("M") or ch in "‍️"
            if cur and not joins and self.width((cur + ch).rstrip()) > max_px:
                parts.append(cur)
                cur = ch
            else:
                cur += ch
        return parts + ([cur] if cur else [])

    def wrap(self, para: str, max_px: float) -> list[str]:
        lines: list[str] = []
        cur = ""
        for seg in self._segments(para):
            trial = cur + seg
            if not cur or self.width(trial.rstrip()) <= max_px:
                cur = trial
            else:
                lines.append(cur.rstrip())
                cur = seg
            if self.width(cur.rstrip()) > max_px:
                pieces = self._split_long(cur, max_px)
                lines += [p.rstrip() for p in pieces[:-1]]
                cur = pieces[-1] if pieces else ""
        lines.append(cur.rstrip())
        return lines

    # ---- drawing

    def draw(self, img, text: str, x: float, baseline: float, stroke_px: int = 0) -> None:
        """OR the glyph coverage of ``text`` into the L-mode image ``img`` (already offset)."""
        glyphs, _ = self.shape(text)
        W, H = img.size
        pad = stroke_px + 2
        for fi, gid, gx, gy in glyphs:
            f = self.chain[fi]
            var = tuple(sorted(f.var.items())) if f.var else None
            contours = _outline(f.path, var, gid)
            if not contours:
                continue
            polys = [np.column_stack((x + gx + c[:, 0] * f.scale, baseline - gy - c[:, 1] * f.scale)) for c in contours]
            allp = np.vstack(polys)
            x0, y0 = int(np.floor(allp[:, 0].min())) - pad, int(np.floor(allp[:, 1].min())) - pad
            x1, y1 = int(np.ceil(allp[:, 0].max())) + pad, int(np.ceil(allp[:, 1].max())) + pad
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            local = [p - (x0, y0) for p in polys]
            cov = raster.fill_polygons(local, x1 - x0, y1 - y0, "nonzero")
            if stroke_px > 0:
                cov = np.maximum(cov, raster.stroke_polylines([(p, True, stroke_px, True) for p in local],
                                                               x1 - x0, y1 - y0))
            box = (x0, y0, x1, y1)
            under = np.asarray(img.crop(box))
            img.paste(Image.fromarray(np.maximum(under, cov), "L"), box)


# --------------------------------------------------------------------------- engine hand-off

def _needs(texts: list[str]) -> tuple[bool, bool, bool]:
    """(right-to-left, Indic / South-East Asian, CJK) present in any of the texts."""
    joined = "".join(texts)
    rtl = any(unicodedata.bidirectional(c) in ("R", "AL") for c in joined)
    complex_ = any(0x0900 <= ord(c) <= 0x0DFF or 0x0E00 <= ord(c) <= 0x0EFF or 0x1000 <= ord(c) <= 0x109F
                   or 0x1780 <= ord(c) <= 0x17FF for c in joined)
    cjk = any(_is_cjk(ord(c)) for c in joined)
    return rtl, complex_, cjk


def engine_notes(engine: str, texts: list[str]) -> list[str]:
    """What live text in these scripts needs on the engine side. engine "all" lists every engine."""
    rtl, complex_, cjk = _needs(texts)
    if not (rtl or complex_ or cjk):
        return []
    engines = ("unity", "unreal", "godot", "roblox") if engine == "all" else (engine,)
    out = []
    for e in engines:
        if e == "unity":
            if rtl or complex_:
                out.append("Unity: TextMeshPro on uGUI does not shape Arabic/Hebrew or Indic/Thai (letters come out "
                           "isolated, order reversed). Use UI Toolkit with the Advanced Text Generator "
                           "(-unity-text-generator: advanced; default from Unity 6.5) or RTLTMPro for uGUI.")
            if cjk:
                out.append("Unity: order CJK fallback font assets by language (a JP font above SC makes Chinese use "
                           "Japanese glyph forms); fallback glyphs take the root font's line height.")
        elif e == "unreal":
            if complex_:
                out.append("Unreal: set Text Shaping Method = Full Shaping on these text blocks - 'Auto' uses "
                           "kerning-only for left-to-right scripts, which breaks Devanagari, Bengali, Tamil and Thai.")
            if rtl or complex_ or cjk:
                out.append("Unreal: use a Composite Font with a sub-font per script range; add a Cultures filter so "
                           "Japanese and Chinese get their own Han glyphs.")
        elif e == "godot":
            if complex_ or cjk:
                out.append("Godot: enable Project Settings > internationalization/locale/include_text_server_data "
                           "(~4 MB ICU data) or Thai/CJK line breaking fails in exported builds.")
            if rtl:
                out.append("Godot: TextServerAdvanced shapes RTL; leave Label text_direction on Auto (or set RTL).")
            out.append("Godot: give the Label font fallbacks covering these scripts (FontFile.fallbacks).")
        elif e == "roblox":
            out.append("Roblox: few built-in fonts cover Arabic, Hebrew, Indic, Thai or CJK (Builder Sans is Latin "
                       "only) - check the text in Studio, or bake stylised headers as images (bake_text=true).")
            if rtl:
                out.append("Roblox: set TextDirection on RTL labels; mixed digits and punctuation have had bidi bugs.")
    return out
