"""Solar inspection skill definitions and command dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


ALLOWED_SKILLS = {
    "idle",
    "stop",
    "inspect_solar_field",
    "drone_return",
    "inspect_panel",
    "inspect_row",
    "drone_scan_row",
    "drone_scan_panels",
    "go2_confirm_anomalies",
    "go2_inspect",
    "move_forward",
    "move_backward",
    "turn_left",
    "turn_right",
    "drone_navigate",
    "go2_navigate",
    "go2_return_base",
    "reset",
}


SKILL_CATALOG = [
    {"skill": "inspect_solar_field", "label": "Inspect Solar Field", "category": "drone",
     "examples": ["inspect solar field", "start patrol", "scan field"]},
    {"skill": "drone_return", "label": "Drone Return", "category": "drone",
     "examples": ["drone return", "recall drones", "rtl"]},
    {"skill": "drone_scan_row", "label": "Drone Scan Row", "category": "drone",
     "examples": ["scan row 8", "去检查一下第八行"]},
    {"skill": "drone_scan_panels", "label": "Drone Scan Panels", "category": "drone",
     "examples": ["scan panels 2,4 and 9,8"]},
    {"skill": "drone_navigate", "label": "Drone Navigate To Panel", "category": "navigation",
     "examples": ["send Drone-01 to panel 2,4", "drone navigate panel 6,3"]},
    {"skill": "move_forward", "label": "Move Forward", "category": "go2",
     "examples": ["move forward", "walk forward", "go ahead"]},
    {"skill": "move_backward", "label": "Move Backward", "category": "go2",
     "examples": ["move backward", "go back", "reverse"]},
    {"skill": "turn_left", "label": "Turn Left", "category": "go2",
     "examples": ["turn left", "rotate left"]},
    {"skill": "turn_right", "label": "Turn Right", "category": "go2",
     "examples": ["turn right", "rotate right"]},
    {"skill": "go2_inspect", "label": "Go2 Ground Inspect", "category": "go2",
     "examples": ["go2 inspect", "ground check", "confirm anomaly"]},
    {"skill": "go2_confirm_anomalies", "label": "Go2 Confirm Anomalies", "category": "go2",
     "examples": ["confirm anomalies in row 8", "派机器狗确认第八行的问题板"]},
    {"skill": "go2_navigate", "label": "Go2 Navigate To Panel", "category": "navigation",
     "examples": ["send Go2-01 to panel 2,4", "go2 navigate panel 6,3"]},
    {"skill": "go2_return_base", "label": "Go2 Return Base", "category": "go2",
     "examples": ["Go2-02 return base", "recall go2", "dogs return to base"]},
    {"skill": "inspect_panel", "label": "Inspect Panel", "category": "system",
     "examples": ["inspect panel 3,5", "check panel 2 4"]},
    {"skill": "inspect_row", "label": "Inspect Row", "category": "system",
     "examples": ["inspect row 8", "去检查一下第八行"]},
    {"skill": "stop", "label": "Stop / Abort", "category": "system",
     "examples": ["stop", "abort", "halt", "cancel mission"]},
    {"skill": "idle", "label": "Standby", "category": "system",
     "examples": ["standby", "idle", "hold"]},
    {"skill": "reset", "label": "Reset Simulation", "category": "system",
     "examples": ["reset", "restart"]},
]


@dataclass
class SolarSkillCommand:
    skill: str
    desc: str = ""
    robot: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None
    panels: list[tuple[int, int]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"skill": self.skill, "desc": self.desc}
        if self.robot:
            out["robot"] = self.robot
        if self.row is not None:
            out["row"] = self.row
        if self.col is not None:
            out["col"] = self.col
        if self.panels:
            out["panels"] = [list(p) for p in self.panels]
        if self.params:
            out["params"] = self.params
        return out
