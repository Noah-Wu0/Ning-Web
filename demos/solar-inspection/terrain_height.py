"""Shared procedural terrain height — matches frontend Three.js fbm in index.html."""
from __future__ import annotations

import math

TS = 200.0


def _n2d(x: float, y: float) -> float:
    v = math.sin(x * 12.9898 + y * 78.233) * 43758.5453
    return v - math.floor(v)


def _fbm(x: float, y: float, octaves: int = 6) -> float:
    value = 0.0
    amplitude = 0.5
    frequency = 1.0
    for _ in range(octaves):
        value += amplitude * _n2d(x * frequency, y * frequency)
        amplitude *= 0.5
        frequency *= 2.0
    return value


def terrain_height(x: float, z: float) -> float:
    """Return ground Y at world (x, z). Frontend uses x/z as horizontal axes."""
    wx = (x / TS + 0.5) * 255.0
    wz = (z / TS + 0.5) * 255.0
    px = int(wx)
    pz = int(wz)
    if px < 0 or px >= 256 or pz < 0 or pz >= 256:
        h = _fbm(x * 0.04, z * 0.04, 4) * 0.6
        h += _fbm(x * 0.12, z * 0.12, 3) * 0.3
        h += _n2d(x * 0.3, z * 0.3) * 0.1
        return h * 0.6
    # Bilinear sample from discrete height field (same as frontend canvas)
    h00 = _height_at_cell(px, pz)
    h10 = _height_at_cell(min(px + 1, 255), pz)
    h01 = _height_at_cell(px, min(pz + 1, 255))
    h11 = _height_at_cell(min(px + 1, 255), min(pz + 1, 255))
    fx = wx - px
    fz = wz - pz
    h0 = h00 * (1 - fx) + h10 * fx
    h1 = h01 * (1 - fx) + h11 * fx
    return h0 * (1 - fz) + h1 * fz


def _height_at_cell(px: int, pz: int) -> float:
    wx = (px / 255.0 - 0.5) * TS
    wz = (pz / 255.0 - 0.5) * TS
    h = _fbm(wx * 0.04, wz * 0.04, 4) * 0.6
    h += _fbm(wx * 0.12, wz * 0.12, 3) * 0.3
    h += _n2d(wx * 0.3, wz * 0.3) * 0.1
    return h * 0.6
