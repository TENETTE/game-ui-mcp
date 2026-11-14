"""Optional GPU backend.

`pip install "game-ui-mcp[gpu]"` adds CuPy (NVIDIA CUDA 12). When it is installed and a CUDA device works,
the heavy per-pixel math (shader fills, shadow / glow / light blurs) runs on the GPU; everything else and
every machine without it stays on numpy, with identical results within float rounding.
Set GAME_UI_MCP_GPU=0 to force the CPU even when CuPy is installed.
"""

from __future__ import annotations

import os
from functools import lru_cache

import numpy as np

MIN_PIXELS = 250_000          # below this the copy to and from the GPU costs more than it saves


@lru_cache(maxsize=1)
def _cupy():
    """The CuPy module if it is installed and a CUDA device answers, else None (checked once)."""
    if os.environ.get("GAME_UI_MCP_GPU", "1").strip().lower() in ("0", "false", "off", "no", "cpu"):
        return None
    try:
        import warnings
        with warnings.catch_warnings():         # CUDA comes from the pip wheels, so "no CUDA_PATH" is noise
            warnings.simplefilter("ignore")
            import cupy
        cupy.cuda.runtime.getDeviceCount()
        cupy.asarray([1.0]).sum().item()      # compiles / loads a kernel: fails here, not mid-render
        return cupy
    except Exception:                          # not installed, no driver, no device, broken CUDA libraries
        return None


def available() -> bool:
    return _cupy() is not None


def module_for(pixels: int):
    """numpy or cupy for an array of this many pixels."""
    cp = _cupy()
    return cp if cp is not None and pixels >= MIN_PIXELS else np


def array_module(a):
    """The module that owns array ``a`` (cupy for GPU arrays, numpy otherwise)."""
    cp = _cupy()
    if cp is not None and isinstance(a, cp.ndarray):
        return cp
    return np


def to_numpy(a) -> np.ndarray:
    cp = _cupy()
    if cp is not None and isinstance(a, cp.ndarray):
        return cp.asnumpy(a)
    return np.asarray(a)


def info() -> dict:
    cp = _cupy()
    if cp is None:
        return {"gpu": False, "hint": 'install with pip install "game-ui-mcp[gpu]" on a machine with an NVIDIA GPU'}
    props = cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)
    name = props["name"].decode() if isinstance(props["name"], bytes) else str(props["name"])
    return {"gpu": True, "device": name, "cupy": cp.__version__}
