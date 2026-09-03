"""Solar farm world map with base stations, multi-drone waypoints, anomaly tracking."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ASSETS_DIR = Path(__file__).parent / "assets" / "solar_field"


@dataclass
class SolarPanel:
    row: int; col: int
    x: float; y: float
    is_defective: bool = False
    true_is_defective: bool = False
    inspected: bool = False
    anomaly_confirmed: bool = False
    anomaly_type: str = ""
    status: str = "unknown"
    last_update_ts: float = 0.0
    last_update_source: str = "system"
    last_update_note: str = ""


@dataclass
class DroneWaypoint:
    x: float; y: float; z: float
    row: int; col: int
    reached: bool = False


@dataclass
class BaseStation:
    name: str; x: float; y: float; z: float = 0.0
    kind: str = "drone"  # "drone" or "go2"


@dataclass
class SolarFarmMap:
    panels: list[SolarPanel] = field(default_factory=list)
    waypoints: list[list[DroneWaypoint]] = field(default_factory=list)  # per-drone scan lanes
    bases: list[BaseStation] = field(default_factory=list)
    home: BaseStation = field(default_factory=lambda: BaseStation("Home", 0, 0))
    boundary_x_min: float = -40; boundary_x_max: float = 40
    boundary_y_min: float = -40; boundary_y_max: float = 40
    drone_scan_altitude: float = 6.0
    current_waypoint_indices: dict[str, int] = field(default_factory=dict)
    anomalies_found: list[tuple[int, int]] = field(default_factory=list)
    anomalies_inspected: list[tuple[int, int]] = field(default_factory=list)


def load_solar_farm() -> SolarFarmMap:
    config_path = ASSETS_DIR / "field_config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Solar field config not found: {config_path}")
    with open(config_path) as f:
        data = json.load(f)

    farm = SolarFarmMap()
    farm.home = BaseStation("Home", data["home"]["x"], data["home"]["y"], data["home"]["z"], "home")
    farm.drone_scan_altitude = 6.0

    defects = {tuple(p) for p in data.get("defective_panels", [])}
    ox, oy = data["origin"]["x"], data["origin"]["y"]
    cs, rs = data["spacing"]["col"], data["spacing"]["row"]

    for r in range(data["panels"]["rows"]):
        for c in range(data["panels"]["cols"]):
            farm.panels.append(SolarPanel(r, c, ox + c * cs, oy + r * rs, False, (r, c) in defects))

    # Place bases OUTSIDE the panel area
    xs = [p.x for p in farm.panels]; ys = [p.y for p in farm.panels]
    margin = 8.0
    farm.boundary_x_min = min(xs) - margin; farm.boundary_x_max = max(xs) + margin
    farm.boundary_y_min = min(ys) - margin; farm.boundary_y_max = max(ys) + margin

    # Put drone pads and Go2 bases on the same service row.
    N_DRONES, N_GO2 = 5, 5
    farm.bases = []
    base_y = max(ys) + 5
    row_start_x = (min(xs) + max(xs)) / 2 - (N_DRONES + N_GO2 - 1) * 1.5
    for i in range(N_DRONES):
        farm.bases.append(BaseStation(f"DronePad-{i+1}", row_start_x + i*3, base_y, 0.05, "drone"))
    for i in range(N_GO2):
        farm.bases.append(BaseStation(f"Go2Base-{i+1}", row_start_x + (N_DRONES + i)*3, base_y, 0.0, "go2"))

    # Generate per-drone scan lanes (5 drones, split waypoints evenly)
    all_waypoints = data["drone_waypoints"]
    N_DRONES = 5
    chunk = max(1, len(all_waypoints) // N_DRONES)
    farm.waypoints = []
    for i in range(N_DRONES):
        start = i * chunk
        end = start + chunk if i < N_DRONES - 1 else len(all_waypoints)
        farm.waypoints.append([DroneWaypoint(w["x"], w["y"], w["z"], w["row"], w["col"]) for w in all_waypoints[start:end]])

    farm.current_waypoint_indices = {f"Drone-0{i+1}": 0 for i in range(N_DRONES)}
    farm.anomalies_found = []
    farm.anomalies_inspected = []

    # Reset all panels
    for wp_list in farm.waypoints:
        for wp in wp_list:
            wp.reached = False
    for p in farm.panels:
        p.is_defective = False; p.inspected = False; p.anomaly_confirmed = False; p.anomaly_type = ""
        p.status = "unknown"; p.last_update_ts = 0.0
        p.last_update_source = "system"; p.last_update_note = ""

    return farm


def check_panel_at_position(farm: SolarFarmMap, x: float, y: float, radius: float = 1.5) -> Optional[SolarPanel]:
    best, best_dist = None, float("inf")
    for p in farm.panels:
        d = math.hypot(x - p.x, y - p.y)
        if d < radius and d < best_dist:
            best, best_dist = p, d
    return best


def mark_panel_inspected(farm: SolarFarmMap, panel: SolarPanel) -> bool:
    panel.inspected = True
    panel.is_defective = panel.true_is_defective
    if panel.status == "confirmed_fault":
        panel.is_defective = True
        panel.anomaly_confirmed = True
        return False
    if panel.true_is_defective:
        panel.anomaly_confirmed = False
        if (panel.row, panel.col) not in farm.anomalies_found:
            farm.anomalies_found.append((panel.row, panel.col))
        return True
    return False


def is_within_boundary(farm: SolarFarmMap, x: float, y: float) -> bool:
    return (farm.boundary_x_min <= x <= farm.boundary_x_max and
            farm.boundary_y_min <= y <= farm.boundary_y_max)


def public_farm_state(farm: SolarFarmMap) -> dict:
    return {
        "panels": [{"row": p.row, "col": p.col, "x": p.x, "y": p.y,
                     "is_defective": p.is_defective, "inspected": p.inspected,
                     "anomaly_confirmed": p.anomaly_confirmed, "anomaly_type": p.anomaly_type,
                     "status": p.status, "last_update_ts": p.last_update_ts,
                     "last_update_source": p.last_update_source,
                     "last_update_note": p.last_update_note}
                    for p in farm.panels],
        "waypoints": [[{"x": w.x, "y": w.y, "z": w.z, "row": w.row, "col": w.col, "reached": w.reached}
                        for w in wp_list] for wp_list in farm.waypoints],
        "bases": [{"name": b.name, "x": b.x, "y": b.y, "z": b.z, "kind": b.kind} for b in farm.bases],
        "home": {"name": farm.home.name, "x": farm.home.x, "y": farm.home.y},
        "boundary": {"x_min": farm.boundary_x_min, "x_max": farm.boundary_x_max,
                     "y_min": farm.boundary_y_min, "y_max": farm.boundary_y_max},
        "anomalies_found": farm.anomalies_found,
        "anomalies_inspected": farm.anomalies_inspected,
        "scan_altitude": farm.drone_scan_altitude,
        "progress": {"panels_inspected": sum(1 for p in farm.panels if p.inspected),
                     "total_panels": len(farm.panels)},
    }
