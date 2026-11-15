"""Ready-made materials: style {"material": "gold"} or {"material": {"name": "wood", "color": "#8b5a2b",
"scale": 1, "seed": 1, "t": 0}} expands into a procedural fill plus lighting; explicit style keys win."""
from __future__ import annotations

from typing import Any

_METAL = ("s = fbm(x/(60*sc), y/(60*sc), 3, seed); q = v*bands + (s-0.5)*0.5; k = 0.5+0.5*sin(q*6.2832+0.6); "
          "k = k*k*(3-2*k); gr = (noise(x*0.05/sc, y*2.5/sc, seed+7)-0.5)*grain; m = 0.5+0.62*k+gr; "
          "sp = pow(k, 10)*0.3; r = clamp(c_r*m+sp); g = clamp(c_g*m+sp); b = clamp(c_b*m+sp)")

_CODE = {
    "metal": _METAL,
    "wood": ("n = fbm(x/(120*sc), y/(14*sc), 4, seed); l = fract(n*6 + y/(30*sc)); "
             "l = smoothstep(0, 0.15, l)*smoothstep(1, 0.6, l); f = noise(x*0.6/sc, y*0.04/sc, seed+3); "
             "m = 0.55+0.35*l+0.15*f; r = clamp(c_r*m); g = clamp(c_g*m); b = clamp(c_b*m)"),
    "stone": ("d = voronoi(x/(26*sc), y/(26*sc), seed); n = fbm(x/(18*sc), y/(18*sc), 5, seed+1); "
              "m = (0.5+0.6*n+rand(x, y, seed)*0.08)*(0.75+0.35*(1-d)); "
              "r = clamp(c_r*m); g = clamp(c_g*m); b = clamp(c_b*m)"),
    "marble": ("n = fbm(x/(70*sc), y/(70*sc), 5, seed); s = abs(sin((x+y*0.6)/(22*sc) + n*5)); "
               "e = pow(1-s, 14)*0.75 + pow(1-s, 60)*0.25; m = 0.94+0.06*n; "
               "r = mix(c_r*m, vein_r, e); g = mix(c_g*m, vein_g, e); b = mix(c_b*m, vein_b, e)"),
    "leather": ("d = voronoi(x/(3.2*sc), y/(3.2*sc), seed); f = fbm(x/(40*sc), y/(40*sc), 3, seed+2); "
                "m = 0.62+0.28*smoothstep(0, 0.9, d)+0.18*f; r = clamp(c_r*m); g = clamp(c_g*m); b = clamp(c_b*m)"),
    "paper": ("n = fbm(x/(4*sc), y/(4*sc), 3, seed); f = fbm(x/(60*sc), y/(60*sc), 3, seed+1); "
              "m = 0.88+0.07*n+0.06*f; r = clamp(c_r*m); g = clamp(c_g*m); b = clamp(c_b*m)"),
    "fabric": ("wx = 0.5+0.5*sin(x*pi/(1.6*sc)); wy = 0.5+0.5*sin(y*pi/(1.6*sc)); "
               "o = where(mod(floor(x/(3.2*sc))+floor(y/(3.2*sc)), 2) < 1, wx, wy); f = fbm(x/(30*sc), y/(30*sc), 3, seed); "
               "m = 0.7+0.22*o+0.12*f; r = clamp(c_r*m); g = clamp(c_g*m); b = clamp(c_b*m)"),
    "rust": ("n = fbm(x/(45*sc), y/(45*sc), 5, seed); p = smoothstep(0.4, 0.55, n); "
             "gr = noise(x*0.05/sc, y*2/sc, seed+4)*0.12; ir = 0.32+gr; "
             "s = rand(x, y, seed)*0.1; r = mix(ir, c_r*(0.7+s+n*0.4), p); g = mix(ir*1.03, c_g*(0.7+s+n*0.3), p); "
             "b = mix(ir*1.08, c_b*(0.6+s), p)"),
    "lava": ("n = fbm(x/(50*sc)+t, y/(50*sc)-t*0.4, 5, seed); hh = smoothstep(0.42, 0.62, n); "
             "c2 = smoothstep(0.56, 0.72, n); k = 0.08+0.06*fbm(x/(8*sc), y/(8*sc), 2, seed+1); "
             "r = clamp(k+hh*c_r*1.2+c2*0.4); g = clamp(k*0.8+hh*c_g+c2*0.55); b = clamp(k*0.7+hh*c_b*0.6+c2*0.25)"),
    "ice": ("n = fbm(x/(50*sc), y/(50*sc), 4, seed); cr = pow(abs(sin(x/(23*sc) + n*9)), 60)*0.35; "
            "fr = (fbm(x/(6*sc), y/(6*sc), 3, seed+2)-0.5)*0.12; m = 0.72+0.3*(1-v)+fr; "
            "r = clamp(c_r*m+cr); g = clamp(c_g*m+cr); b = clamp(c_b*m+cr); a = 0.88"),
    "gem": ("ang = atan2(v-0.5, u-0.5); fac = floor((ang/6.2832+0.5)*8)/8; rr = length(u-0.5, v-0.5)*2; "
            "k = (0.35+0.65*fract(fac*3.7+0.3))*(1-rr*0.35)+where(rr < 0.45, 0.2, 0); "
            "sp = pow(clamp(1-length(u-0.36, v-0.3)*3), 6)*0.8; "
            "r = clamp(c_r*k+sp); g = clamp(c_g*k+sp); b = clamp(c_b*k+sp)"),
    "plastic": "k = 1.08-v*0.38; r = clamp(c_r*k); g = clamp(c_g*k); b = clamp(c_b*k)",
    "jelly": ("k = 1.15-v*0.5; n = fbm(x/(30*sc)+t, y/(30*sc), 3, seed)*0.12; "
              "r = clamp(c_r*k+n); g = clamp(c_g*k+n); b = clamp(c_b*k+n); a = 0.86"),
    "hologram": ("ln = 0.75+0.25*sin(y*pi/(2*sc) - t*20); n = noise(x/(40*sc)+t*3, y/(6*sc), seed)*0.3; "
                 "e = smoothstep(0, 0.12, u)*smoothstep(1, 0.88, u)*smoothstep(0, 0.12, v)*smoothstep(1, 0.88, v); "
                 "r = clamp(c_r*ln+n); g = clamp(c_g*ln+n); b = clamp(c_b*ln+n); a = clamp((0.35+0.4*ln)*(1.3-e*0.6))"),
}

_LIGHT_METAL = {"angle": 225, "size": 4, "depth": 1.2, "profile": "chisel", "gloss": 0.6}

_MATERIALS: dict[str, dict] = {
    "gold":     {"code": "metal", "color": "#e8b84a", "p": {"bands": 1.6, "grain": 0.05}, "light": _LIGHT_METAL},
    "silver":   {"code": "metal", "color": "#c9cfd6", "p": {"bands": 1.6, "grain": 0.05}, "light": _LIGHT_METAL},
    "chrome":   {"code": "metal", "color": "#dfe6ee", "p": {"bands": 2.6, "grain": 0.0},
                 "light": {**_LIGHT_METAL, "gloss": 0.9}},
    "bronze":   {"code": "metal", "color": "#b0763a", "p": {"bands": 1.3, "grain": 0.08}, "light": _LIGHT_METAL},
    "copper":   {"code": "metal", "color": "#c8714a", "p": {"bands": 1.4, "grain": 0.06}, "light": _LIGHT_METAL},
    "steel":    {"code": "metal", "color": "#a9b1ba", "p": {"bands": 0.7, "grain": 0.5},
                 "light": {**_LIGHT_METAL, "gloss": 0.35}},
    "iron":     {"code": "metal", "color": "#6d737b", "p": {"bands": 0.6, "grain": 0.3},
                 "light": {**_LIGHT_METAL, "gloss": 0.15}, "noise": 0.05},
    "rust":     {"code": "rust", "color": "#b5562a", "light": {"angle": 225, "size": 3, "depth": 0.8, "gloss": 0}},
    "wood":     {"code": "wood", "color": "#9a6234", "light": {"angle": 225, "size": 3, "depth": 0.8, "gloss": 0.1}},
    "stone":    {"code": "stone", "color": "#8a8780", "light": {"angle": 225, "size": 5, "depth": 1.0,
                                                               "profile": "chisel", "gloss": 0}},
    "marble":   {"code": "marble", "color": "#ece9e4", "p": {"vein": "#5b5f66"},
                 "light": {"angle": 225, "size": 3, "depth": 0.6, "gloss": 0.4}},
    "leather":  {"code": "leather", "color": "#7a4a2c", "light": {"angle": 225, "size": 3, "depth": 0.9, "gloss": 0.15},
                 "stroke": {"color": "#000000@0.35", "width": 1.5, "dash": [4, 3], "align": "inside", "offset": 4}},
    "paper":    {"code": "paper", "color": "#efe6d2", "light": None},
    "fabric":   {"code": "fabric", "color": "#3f5a8a", "light": {"angle": 225, "size": 3, "depth": 0.5, "gloss": 0}},
    "lava":     {"code": "lava", "color": "#ff5a1f", "glow": True, "light": {"angle": 225, "size": 4, "depth": 0.7,
                                                                             "gloss": 0}},
    "ice":      {"code": "ice", "color": "#bfe8ff", "light": {"angle": 225, "size": 4, "depth": 1.0, "gloss": 0.9},
                 "inner_glow": {"color": "#ffffff@0.45", "blur": 10}},
    "gem":      {"code": "gem", "color": "#3fb6ff", "light": {"angle": 225, "size": 5, "depth": 1.4,
                                                             "profile": "chisel", "gloss": 1}},
    "plastic":  {"code": "plastic", "color": "#3b82f6", "light": {"angle": 225, "size": 6, "depth": 1.0,
                                                                 "profile": "round", "gloss": 0.9}},
    "rubber":   {"code": "plastic", "color": "#2b2d31", "light": {"angle": 225, "size": 5, "depth": 0.8,
                                                                 "profile": "round", "gloss": 0.1}, "noise": 0.04},
    "jelly":    {"code": "jelly", "color": "#5ee06a", "light": {"angle": 225, "size": 8, "depth": 1.2,
                                                               "profile": "round", "gloss": 1},
                 "inner_glow": {"color": "#ffffff@0.35", "blur": 12}},
    "hologram": {"code": "hologram", "color": "#34e0ff", "glow": True, "light": None},
    "glass":    {"style": {"fill": "#ffffff@0.08", "backdrop_blur": 10,
                           "stroke": {"color": "#ffffff@0.35", "width": 1},
                           "inner_glow": {"color": "#ffffff@0.15", "blur": 12},
                           "shadow": {"color": "#000000@0.35", "blur": 20, "dy": 8, "knockout": True}}},
    "neon":     {"color": "#ff3df2"},
}

NAMES = sorted(_MATERIALS)


def expand(spec: Any) -> dict:
    """Style keys for a material reference (a name or {"name", "color", "scale", "seed", "t", ...})."""
    if isinstance(spec, str):
        spec = {"name": spec}
    if not isinstance(spec, dict) or not spec.get("name"):
        raise ValueError(f'material must be a name or {{"name": ...}}. Materials: {", ".join(NAMES)}')
    name = str(spec["name"]).lower()
    if name not in _MATERIALS:
        raise ValueError(f"Unknown material '{name}'. Materials: {', '.join(NAMES)}")
    m = _MATERIALS[name]
    color = spec.get("color", m.get("color"))
    if "style" in m:
        return dict(m["style"])
    if name == "neon":
        return {"fill": color, "glow": [{"color": f"{color}@0.9", "blur": 8}, {"color": f"{color}@0.6", "blur": 24}],
                "inner_glow": {"color": "#ffffff@0.7", "blur": 4}}
    params = {"c": color, "sc": max(0.05, float(spec.get("scale", 1))), "seed": float(spec.get("seed", 1)),
              "t": float(spec.get("t", 0)), **m.get("p", {})}
    params.update({k: v for k, v in spec.items() if k not in ("name", "color", "scale", "seed", "t")})
    out: dict = {"fill": {"type": "shader", "code": _CODE[m["code"]], "params": params}}
    if m.get("light"):
        out["light"] = dict(m["light"])
    for key in ("stroke", "inner_glow", "noise"):
        if m.get(key) is not None:
            out[key] = m[key]
    if m.get("glow"):
        out["glow"] = {"color": f"{color}@0.6", "blur": 16}
    return out
