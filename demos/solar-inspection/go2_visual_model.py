"""Visual model specification for Unitree Go2.

The browser consumes this declarative spec and only performs mesh loading and
transform application. The kinematic structure and mesh assignment live here so
they can be tested and versioned outside index.html.
"""
from __future__ import annotations

GO2_VISUAL_MODEL = {
    "urdf_url": "./assets/go2/go2.urdf",
    "mesh_base": "./assets/go2/",
    "geometry_base": "./assets/go2/geometry",
    "geometries": ["base", "hip", "thigh", "thigh_mirror", "calf", "calf_mirror", "foot"],
    "materials": {
        "gray": {"color": 0xAAB0BD, "roughness": 0.45, "metalness": 0.15},
        "metal": {"color": 0xE6EEF0, "roughness": 0.35, "metalness": 0.25},
        "black": {"color": 0x050505, "roughness": 0.70, "metalness": 0.05},
    },
    "links": [
        {"name": "base", "mesh": "base", "material": "gray", "parent": None, "origin": [0, 0, 0]},
    ],
    "legs": [
        {"prefix": "FL", "side": 1, "front": 1, "thigh_mesh": "thigh", "calf_mesh": "calf"},
        {"prefix": "FR", "side": -1, "front": 1, "thigh_mesh": "thigh_mirror", "calf_mesh": "calf_mirror"},
        {"prefix": "RL", "side": 1, "front": -1, "thigh_mesh": "thigh", "calf_mesh": "calf"},
        {"prefix": "RR", "side": -1, "front": -1, "thigh_mesh": "thigh_mirror", "calf_mesh": "calf_mirror"},
    ],
    "joint_origins": {
        "hip": [0.1934, 0.0465, 0.0],
        "thigh": [0.0, 0.0955, 0.0],
        "calf": [0.0, 0.0, -0.213],
        "foot": [0.0, 0.0, -0.213],
    },
    "joint_axes": {
        "hip": [1, 0, 0],
        "thigh": [0, 1, 0],
        "calf": [0, 1, 0],
    },
}


def public_go2_visual_model() -> dict:
    return GO2_VISUAL_MODEL
