"""Solar inspection demo backend — FastAPI + WebSocket on port 8011."""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from go2_visual_model import public_go2_visual_model
from sim_solar import SolarSimulation, VelocityCmd
from solar_llm import (
    ai_settings_snapshot,
    configure_ai_parser,
    llm_available,
    public_ai_settings,
    reset_ai_parser_runtime,
    restore_ai_settings_snapshot,
    test_ai_connection,
)
from solar_map import load_solar_farm, public_farm_state
from solar_orchestrator import Mission, State, update_mission
from solar_semantic import parse_command_async
from solar_skills import SKILL_CATALOG, SolarSkillCommand
from terrain_height import terrain_height

ROOT_DIR = os.path.dirname(__file__)
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
UPDATE_SNAPSHOT_PATH = os.path.join(ROOT_DIR, ".runtime_update_snapshot.json")
BOOT_ID = f"boot-{int(time.time() * 1000)}-{os.getpid()}"
GO2_PANEL_YAW = -1.5707963267948966
DRONE_PARK_WORLD_Y = 0.655

app = FastAPI(title="Solar Inspection Demo")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

farm = load_solar_farm()
sim = SolarSimulation()
go2_base = farm.bases[5] if len(farm.bases) > 5 else farm.bases[-1]
mission = Mission(farm=farm)
clients: set[WebSocket] = set()
sim_speed = 1.0
sim_paused = False
sim_clock_ts = time.time()
last_panel_truth_mutation_ts = sim_clock_ts
path_settings = {"mode": "static", "replan_interval_s": 3.0, "retreat_distance_m": 0.6, "show_target_marker": True}
robot_settings = {
    "disabled_go2_names": [],
    "go2_battery_redline_pct": 20.0,
    "drone_battery_redline_pct": 20.0,
}
panel_settings = {"healthy_ttl_s": 300.0, "degradation_interval_s": 120.0}
automation_settings = {
    "auto_inspect_enabled": False,
    "auto_inspect_interval_s": 360.0,
    "last_auto_inspect_ts": sim_clock_ts,
    "auto_inspect_pending": False,
}
PANEL_BUFFER_X = 0.42
PANEL_BUFFER_Y = 0.26
DRONE_BASE_BUFFER_RADIUS = 1.15
DRONE_RETURN_MIN_AGL = 4.0


@dataclass
class CommandTrace:
    id: str
    text: str
    source: str = "none"
    llm_available: bool = False
    actions: list[dict] = field(default_factory=list)
    dispatch_log: list[str] = field(default_factory=list)
    ok: bool = False
    error: str = ""
    ts: float = field(default_factory=time.time)
    task_id: Optional[str] = None
    selected_robot: Optional[str] = None
    llm_request_summary: dict[str, Any] = field(default_factory=dict)
    llm_raw_json: dict[str, Any] = field(default_factory=dict)
    normalized_json: dict[str, Any] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    generated_tasks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TaskStep:
    id: str
    label: str
    actions: list[SolarSkillCommand]
    status: str = "pending"
    expected_duration: float = 0.2
    remaining_duration: float = 0.2
    started_ts: Optional[float] = None
    completed_ts: Optional[float] = None
    logs: list[str] = field(default_factory=list)

    def progress(self) -> float:
        if self.status == "done":
            return 1.0
        if self.status == "pending":
            return 0.0
        if self.expected_duration <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - (self.remaining_duration / self.expected_duration)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "expected_duration": self.expected_duration,
            "remaining_duration": self.remaining_duration,
            "progress": self.progress(),
            "targets": _step_targets(self.actions),
            "actions": [a.to_dict() for a in self.actions],
            "logs": self.logs[-10:],
            "started_ts": self.started_ts,
            "completed_ts": self.completed_ts,
        }


@dataclass
class TaskRecord:
    id: str
    text: str
    source: str
    trace_id: str
    steps: list[TaskStep]
    status: str = "queued"
    created_ts: float = field(default_factory=time.time)
    started_ts: Optional[float] = None
    completed_ts: Optional[float] = None
    current_step_index: int = 0
    logs: list[str] = field(default_factory=list)

    def progress(self) -> float:
        if not self.steps:
            return 1.0 if self.status == "done" else 0.0
        done = sum(1 for s in self.steps if s.status == "done")
        active = next((s.progress() for s in self.steps if s.status in {"active", "blocked"}), 0.0)
        return max(0.0, min(1.0, (done + active) / len(self.steps)))

    def current_step(self) -> Optional[TaskStep]:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "trace_id": self.trace_id,
            "status": self.status,
            "progress": self.progress(),
            "current_step_index": self.current_step_index,
            "created_ts": self.created_ts,
            "started_ts": self.started_ts,
            "completed_ts": self.completed_ts,
            "steps": [s.to_dict() for s in self.steps],
            "logs": self.logs[-20:],
        }


command_traces: list[CommandTrace] = []
task_records: list[TaskRecord] = []
ground_check_queue: list[tuple[int, int]] = []
ground_check_assignments: dict[str, dict[str, Any]] = {}
drone_nav_assignments: dict[str, dict[str, Any]] = {}
drone_row_scan_assignments: dict[str, dict[str, Any]] = {}
go2_return_assignments: dict[str, dict[str, Any]] = {}
drone_return_assignments: dict[str, dict[str, Any]] = {}
manual_motion_tasks: dict[str, dict[str, Any]] = {}


def _trace_id() -> str:
    return f"cmd-{int(time.time() * 1000)}"


def _task_id() -> str:
    return f"task-{int(time.time() * 1000)}-{len(task_records) + 1}"


def _step_id(task_id: str, index: int) -> str:
    return f"{task_id}-step-{index + 1}"


def sim_now() -> float:
    return sim_clock_ts


def _disabled_go2_names() -> set[str]:
    return set(robot_settings.get("disabled_go2_names") or [])


def set_go2_available(robot_name: str, available: bool) -> bool:
    if robot_name not in {g.name for g in sim.go2s}:
        return False
    disabled = set(robot_settings.get("disabled_go2_names") or [])
    if available:
        disabled.discard(robot_name)
    else:
        disabled.add(robot_name)
    robot_settings["disabled_go2_names"] = sorted(disabled)
    robot = next((g for g in sim.go2s if g.name == robot_name), None)
    if robot and not available:
        robot.task_desc = "Unavailable for automatic tasks"
    return True


def full_reset():
    global farm, sim, mission, sim_clock_ts, last_panel_truth_mutation_ts
    farm = load_solar_farm()
    sim = SolarSimulation()
    sim_clock_ts = time.time()
    last_panel_truth_mutation_ts = sim_clock_ts
    automation_settings["last_auto_inspect_ts"] = sim_clock_ts
    automation_settings["auto_inspect_pending"] = False
    # Abort all active tasks so they don't permanently block auto-inspect.
    # A full reset reloads the farm and repositions all robots; any in-flight
    # long-running step (e.g. inspect_solar_field) can never complete against
    # the fresh farm state.
    for task in task_records:
        if task.status in {"queued", "running", "blocked"}:
            task.status = "done"
            task.completed_ts = time.time()
            for step in task.steps:
                if step.status in {"pending", "active"}:
                    step.status = "done"
            task.logs.append(f"{time.strftime('%H:%M:%S')} Task aborted by system reset.")
    ground_check_queue.clear()
    ground_check_assignments.clear()
    drone_nav_assignments.clear()
    drone_row_scan_assignments.clear()
    go2_return_assignments.clear()
    drone_return_assignments.clear()
    manual_motion_tasks.clear()
    for i, d in enumerate(sim.drones):
        pad = farm.bases[i] if i < len(farm.bases) else farm.bases[0]
        d.pose.x, d.pose.y = pad.x, pad.y
        d.pose.z = terrain_height(pad.x, pad.y) + DRONE_PARK_WORLD_Y
        d.status = "idle"
    for i, g in enumerate(sim.go2s):
        idx = 5 + i
        gb = farm.bases[idx] if idx < len(farm.bases) else farm.bases[-1]
        g.pose.x, g.pose.y = gb.x, gb.y
        g.pose.yaw = GO2_PANEL_YAW
        g.status = "idle"
    mission = Mission(farm=farm)
    mission.log("System ready. Awaiting inspection command.")


def reset_inspection_state_preserve_fleet() -> None:
    """Start a fresh aerial inspection without teleporting robots."""
    ground_check_queue.clear()
    ground_check_assignments.clear()
    drone_nav_assignments.clear()
    drone_row_scan_assignments.clear()
    drone_return_assignments.clear()
    legacy_drone_scan_synced.clear()
    for panel in farm.panels:
        panel.inspected = False
        panel.is_defective = panel.status in {"drone_anomaly", "confirmed_fault"}
    farm.anomalies_found = [
        (p.row, p.col) for p in farm.panels if p.status in {"drone_anomaly", "confirmed_fault"}
    ]
    farm.anomalies_inspected = [
        (p.row, p.col) for p in farm.panels if p.status == "confirmed_fault"
    ]
    for wp_list in farm.waypoints:
        for wp in wp_list:
            wp.reached = False
    farm.current_waypoint_indices = {f"Drone-0{i+1}": 0 for i in range(len(sim.drones))}
    mission.farm = farm
    mission.state = State.IDLE
    mission.drone_states.clear()
    mission.drone_scan_indices.clear()
    mission.drone_photo_cooldowns.clear()
    mission.anomaly_queue.clear()
    mission.inspected_list.clear()
    mission.go2_target = None
    mission.go2_inspect_dwell = 0.0
    mission.go2_inspect_done = False


def _pending_ground_panels() -> list[tuple[int, int]]:
    pending: list[tuple[int, int]] = []
    for panel in farm.panels:
        if panel.status == "drone_anomaly":
            item = (panel.row, panel.col)
            if item not in pending:
                pending.append(item)
    for item in mission.anomaly_queue:
        if item not in pending:
            pending.append(item)
    return pending


def _ground_confirmation_counts() -> tuple[int, int]:
    fault_keys = {
        (panel.row, panel.col)
        for panel in farm.panels
        if panel.inspected and panel.is_defective
    }
    fault_keys.update(farm.anomalies_found)
    fault_keys.update(ground_check_queue)
    fault_keys.update(a.get("panel") for a in ground_check_assignments.values() if a.get("panel"))
    total = len(fault_keys)
    confirmed = sum(
        1
        for panel in farm.panels
        if (panel.row, panel.col) in fault_keys and panel.anomaly_confirmed
    )
    return confirmed, total


def _axis_spacing(values: list[float], fallback: float = 3.0) -> float:
    ordered = sorted(set(round(v, 3) for v in values))
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b - a > 0.1]
    return min(gaps) if gaps else fallback


def _panel_observation_point(row: int, col: int) -> tuple[float, float]:
    panel = next((p for p in farm.panels if p.row == row and p.col == col), None)
    if not panel:
        return 0.0, 0.0
    col_spacing = _axis_spacing([p.x for p in farm.panels], 3.0)
    row_spacing = _axis_spacing([p.y for p in farm.panels], 2.0)
    max_row = max((p.row for p in farm.panels), default=row)
    if row == 0:
        aisle_y = panel.y - row_spacing * 0.5
    elif row == max_row:
        aisle_y = panel.y + row_spacing * 0.5
    else:
        aisle_y = panel.y + row_spacing * 0.5
    # Observe from the aisle centerline in front of the panel, not from panel center.
    side = 1 if panel.x <= (farm.boundary_x_min + farm.boundary_x_max) / 2 else -1
    obs_x = panel.x + side * min(col_spacing * 0.45, 1.8)
    obs_y = aisle_y
    return obs_x, obs_y


def _is_walkable_point(x: float, y: float) -> bool:
    if not (farm.boundary_x_min <= x <= farm.boundary_x_max and farm.boundary_y_min <= y <= farm.boundary_y_max):
        return False
    for obs in _navigation_obstacles():
        if obs["type"] == "rect":
            if abs(x - float(obs["x"])) <= float(obs["half_x"]) and abs(y - float(obs["y"])) <= float(obs["half_y"]):
                return False
        elif obs["type"] == "circle":
            if math.hypot(x - float(obs["x"]), y - float(obs["y"])) <= float(obs["radius"]):
                return False
    return True


def _navigation_obstacles() -> list[dict[str, Any]]:
    obstacles: list[dict[str, Any]] = []
    panel_half_x = 1.0 + PANEL_BUFFER_X
    panel_half_y = 0.5 + PANEL_BUFFER_Y
    for panel in farm.panels:
        obstacles.append({
            "type": "rect",
            "kind": "panel_buffer",
            "x": panel.x,
            "y": panel.y,
            "half_x": panel_half_x,
            "half_y": panel_half_y,
        })
    for base in farm.bases:
        if base.kind != "drone":
            continue
        obstacles.append({
            "type": "circle",
            "kind": "drone_base_buffer",
            "x": base.x,
            "y": base.y,
            "radius": DRONE_BASE_BUFFER_RADIUS,
        })
    return obstacles


def _walkable_regions_public() -> dict[str, Any]:
    return {
        "boundary": {
            "x_min": farm.boundary_x_min,
            "x_max": farm.boundary_x_max,
            "y_min": farm.boundary_y_min,
            "y_max": farm.boundary_y_max,
        },
        "obstacles": _navigation_obstacles(),
        "buffers": {
            "panel_x": PANEL_BUFFER_X,
            "panel_y": PANEL_BUFFER_Y,
            "drone_base_radius": DRONE_BASE_BUFFER_RADIUS,
        },
    }


def _nearest_walkable_point(x: float, y: float) -> dict[str, float]:
    if _is_walkable_point(x, y):
        return {"x": x, "y": y}
    best: Optional[dict[str, float]] = None
    best_d = float("inf")
    for radius in [i * 0.35 for i in range(1, 90)]:
        samples = max(16, int(radius * 18))
        for idx in range(samples):
            ang = math.tau * idx / samples
            px = x + math.cos(ang) * radius
            py = y + math.sin(ang) * radius
            if not _is_walkable_point(px, py):
                continue
            d = math.hypot(px - x, py - y)
            if d < best_d:
                best = {"x": px, "y": py}
                best_d = d
        if best is not None:
            return best
    return {"x": x, "y": y}


def _line_is_walkable(a: dict[str, float], b: dict[str, float]) -> bool:
    return _line_is_walkable_with_obstacles(a, b, _navigation_obstacles())


def _line_is_walkable_with_obstacles(a: dict[str, float], b: dict[str, float], obstacles: list[dict[str, Any]]) -> bool:
    ax, ay = float(a["x"]), float(a["y"])
    bx, by = float(b["x"]), float(b["y"])
    if not _is_walkable_point(ax, ay) or not _is_walkable_point(bx, by):
        return False
    if _segment_crosses_panel_row_band(ax, ay, bx, by):
        return False
    for obs in obstacles:
        if obs["type"] == "rect":
            if _segment_intersects_rect(
                ax, ay, bx, by,
                float(obs["x"]) - float(obs["half_x"]),
                float(obs["y"]) - float(obs["half_y"]),
                float(obs["x"]) + float(obs["half_x"]),
                float(obs["y"]) + float(obs["half_y"]),
            ):
                return False
        elif obs["type"] == "circle":
            if _segment_distance_to_point(ax, ay, bx, by, float(obs["x"]), float(obs["y"])) <= float(obs["radius"]):
                return False
    return True


def _segment_crosses_panel_row_band(ax: float, ay: float, bx: float, by: float) -> bool:
    """Treat each solar-panel row as a continuous no-crossing band for Go2 routing."""
    if not farm.panels:
        return False
    panel_half_x = 1.0 + PANEL_BUFFER_X
    panel_half_y = 0.5 + PANEL_BUFFER_Y
    min_x = min(p.x for p in farm.panels) - panel_half_x
    max_x = max(p.x for p in farm.panels) + panel_half_x
    for row_y in sorted(set(round(p.y, 6) for p in farm.panels)):
        if _segment_intersects_rect(ax, ay, bx, by, min_x, row_y - panel_half_y, max_x, row_y + panel_half_y):
            return True
    return False


def _segment_intersects_rect(ax: float, ay: float, bx: float, by: float, min_x: float, min_y: float, max_x: float, max_y: float) -> bool:
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - min_x), (dx, max_x - ax), (-dy, ay - min_y), (dy, max_y - ay)):
        if abs(p) < 1e-9:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return False
            t0 = max(t0, r)
        else:
            if r < t0:
                return False
            t1 = min(t1, r)
    return t0 <= t1


def _segment_distance_to_point(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> float:
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _candidate_visibility_points(start: dict[str, float], goal: dict[str, float], corridor: float) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    margin = 0.45
    for obs in _navigation_obstacles():
        if obs["type"] == "rect":
            near_dist = math.hypot(float(obs["half_x"]), float(obs["half_y"])) + corridor
            if _segment_distance_to_point(start["x"], start["y"], goal["x"], goal["y"], float(obs["x"]), float(obs["y"])) > near_dist:
                continue
        elif obs["type"] == "circle":
            if _segment_distance_to_point(start["x"], start["y"], goal["x"], goal["y"], float(obs["x"]), float(obs["y"])) > float(obs["radius"]) + corridor:
                continue
        if obs["type"] == "rect":
            hx = float(obs["half_x"]) + margin
            hy = float(obs["half_y"]) + margin
            for sx in (-1, 1):
                for sy in (-1, 1):
                    p = {"x": float(obs["x"]) + sx * hx, "y": float(obs["y"]) + sy * hy}
                    if _is_walkable_point(p["x"], p["y"]):
                        points.append(p)
        elif obs["type"] == "circle":
            radius = float(obs["radius"]) + margin
            for i in range(12):
                ang = math.tau * i / 12
                p = {"x": float(obs["x"]) + math.cos(ang) * radius, "y": float(obs["y"]) + math.sin(ang) * radius}
                if _is_walkable_point(p["x"], p["y"]):
                    points.append(p)
    deduped: list[dict[str, float]] = []
    seen: set[tuple[int, int]] = set()
    for p in points:
        key = (round(p["x"] * 10), round(p["y"] * 10))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped


def _visibility_route(start: dict[str, float], goal: dict[str, float]) -> list[dict[str, float]]:
    import heapq
    if _line_is_walkable(start, goal):
        return [start, goal]
    obstacles = _navigation_obstacles()
    for corridor in (4.0, 8.0, 80.0):
        nodes = [start, goal] + _candidate_visibility_points(start, goal, corridor)
        graph: list[list[tuple[int, float]]] = [[] for _ in nodes]
        edges: set[tuple[int, int]] = set()
        for i in range(len(nodes)):
            distances = sorted(
                (
                    (math.hypot(nodes[i]["x"] - nodes[j]["x"], nodes[i]["y"] - nodes[j]["y"]), j)
                    for j in range(len(nodes))
                    if j != i
                ),
                key=lambda item: item[0],
            )
            for _, j in distances[:32]:
                a, b = (i, j) if i < j else (j, i)
                edges.add((a, b))
            if i in (0, 1):
                for _, j in distances[:80]:
                    a, b = (i, j) if i < j else (j, i)
                    edges.add((a, b))
        for i, j in edges:
            if not _line_is_walkable_with_obstacles(nodes[i], nodes[j], obstacles):
                continue
            cost = math.hypot(nodes[i]["x"] - nodes[j]["x"], nodes[i]["y"] - nodes[j]["y"])
            graph[i].append((j, cost))
            graph[j].append((i, cost))
        for limit in (math.radians(60), math.radians(75), math.radians(90)):
            route = _angle_limited_route(nodes, graph, limit)
            if route:
                return _round_route_corners(route)
    return [start, goal]


def _round_route_corners(route: list[dict[str, float]], max_step_angle: float = math.radians(45)) -> list[dict[str, float]]:
    if len(route) < 3:
        return route
    rounded = [route[0]]
    for prev, corner, nxt in zip(route, route[1:], route[2:]):
        turn = _route_turn_angle(prev, corner, nxt)
        if turn <= max_step_angle:
            rounded.append(corner)
            continue
        ux1, uy1, len1 = _unit_from_to(corner, prev)
        ux2, uy2, len2 = _unit_from_to(corner, nxt)
        trim = min(1.2, len1 * 0.45, len2 * 0.45)
        if trim <= 0.2:
            rounded.append(corner)
            continue
        p0 = {"x": corner["x"] + ux1 * trim, "y": corner["y"] + uy1 * trim}
        p2 = {"x": corner["x"] + ux2 * trim, "y": corner["y"] + uy2 * trim}
        samples = [p0]
        sample_count = max(2, int(math.ceil(turn / max_step_angle)) + 1)
        for i in range(1, sample_count):
            t = i / sample_count
            mt = 1.0 - t
            samples.append({
                "x": mt * mt * p0["x"] + 2 * mt * t * corner["x"] + t * t * p2["x"],
                "y": mt * mt * p0["y"] + 2 * mt * t * corner["y"] + t * t * p2["y"],
            })
        samples.append(p2)
        candidate = [rounded[-1]] + samples + [nxt]
        if all(_line_is_walkable(a, b) for a, b in zip(candidate, candidate[1:])):
            rounded.extend(samples)
        else:
            rounded.append(corner)
    rounded.append(route[-1])
    return _dedupe_route_points(rounded)


def _unit_from_to(a: dict[str, float], b: dict[str, float]) -> tuple[float, float, float]:
    dx, dy = float(b["x"]) - float(a["x"]), float(b["y"]) - float(a["y"])
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return 0.0, 0.0, 0.0
    return dx / length, dy / length, length


def _dedupe_route_points(route: list[dict[str, float]]) -> list[dict[str, float]]:
    deduped: list[dict[str, float]] = []
    for point in route:
        if deduped and math.hypot(deduped[-1]["x"] - point["x"], deduped[-1]["y"] - point["y"]) < 0.1:
            continue
        deduped.append(point)
    return deduped


def _angle_limited_route(
    nodes: list[dict[str, float]],
    graph: list[list[tuple[int, float]]],
    turn_limit: float,
) -> Optional[list[dict[str, float]]]:
    import heapq
    start_state = (-1, 0)
    open_heap: list[tuple[float, int, tuple[int, int]]] = [(0.0, 0, start_state)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    dist = {start_state: 0.0}
    seq = 0
    while open_heap:
        _, _, state = heapq.heappop(open_heap)
        prev, current = state
        if current == 1:
            path_states = [state]
            while state in came_from:
                state = came_from[state]
                path_states.append(state)
            idxs = [s[1] for s in reversed(path_states)]
            return [nodes[i] for i in idxs]
        for nxt, edge_cost in graph[current]:
            if nxt == prev:
                continue
            turn = _route_turn_angle(nodes[prev], nodes[current], nodes[nxt]) if prev >= 0 else 0.0
            if turn > turn_limit + 1e-6:
                continue
            # Prefer routes with smaller steering changes even when length is similar.
            turn_penalty = 1.2 * turn
            next_state = (current, nxt)
            tentative = dist[state] + edge_cost + turn_penalty
            if tentative >= dist.get(next_state, float("inf")):
                continue
            came_from[next_state] = state
            dist[next_state] = tentative
            seq += 1
            heuristic = math.hypot(nodes[1]["x"] - nodes[nxt]["x"], nodes[1]["y"] - nodes[nxt]["y"])
            heapq.heappush(open_heap, (tentative + heuristic, seq, next_state))
    return None


def _route_turn_angle(a: dict[str, float], b: dict[str, float], c: dict[str, float]) -> float:
    v1x, v1y = float(b["x"]) - float(a["x"]), float(b["y"]) - float(a["y"])
    v2x, v2y = float(c["x"]) - float(b["x"]), float(c["y"]) - float(b["y"])
    n1, n2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
    if n1 <= 1e-9 or n2 <= 1e-9:
        return 0.0
    dot = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (n1 * n2)))
    return math.acos(dot)


def _plan_go2_route(robot_name: str, panel_key: tuple[int, int]) -> list[dict[str, float]]:
    robot = next((g for g in sim.go2s if g.name == robot_name), None)
    if not robot:
        return []
    obs_x, obs_y = _panel_observation_point(*panel_key)
    start = _nearest_walkable_point(robot.pose.x, robot.pose.y)
    goal = _nearest_walkable_point(obs_x, obs_y)
    if _same_panel_aisle(start["y"], goal["y"]) and _line_is_walkable(start, goal):
        return [start, goal]
    if _same_outer_panel_row_lane(start["y"], goal["y"]) and _line_is_walkable(start, goal):
        return [start, goal]
    # Panel visits must use the perimeter/service lane strategy. This avoids
    # shortcutting through panel rows even when a geometric line check passes.
    outer_route = _outer_ring_route(start, goal)
    if outer_route:
        return outer_route
    return []


def _outer_ring_route(start: dict[str, float], goal: dict[str, float]) -> list[dict[str, float]]:
    if not farm.panels:
        return [start, goal] if _line_is_walkable(start, goal) else []
    panel_min_x = min(p.x for p in farm.panels)
    panel_max_x = max(p.x for p in farm.panels)
    panel_min_y = min(p.y for p in farm.panels)
    panel_max_y = max(p.y for p in farm.panels)
    row_spacing = _axis_spacing([p.y for p in farm.panels], 5.0)
    panel_half_x = 1.0 + PANEL_BUFFER_X
    lane_clearance = 1.0
    left_lane_x = max(farm.boundary_x_min + 1.0, panel_min_x - panel_half_x - lane_clearance)
    right_lane_x = min(farm.boundary_x_max - 1.0, panel_max_x + panel_half_x + lane_clearance)
    target_row_y = min(max(goal["y"], panel_min_y - row_spacing * 0.5), panel_max_y + row_spacing * 0.5)

    candidates: list[list[dict[str, float]]] = []
    in_panel_area = (
        panel_min_x - panel_half_x <= start["x"] <= panel_max_x + panel_half_x
        and panel_min_y - row_spacing * 0.6 <= start["y"] <= panel_max_y + row_spacing * 0.6
    )
    start_nudge_y = -2.8 if start["y"] >= panel_max_y else 2.8
    start_departure = _nearest_walkable_point(start["x"], start["y"] + start_nudge_y)
    if in_panel_area:
        current_y = _nearest_aisle_center_y(start["y"])
        start_on_line = _nearest_walkable_point(start["x"], current_y)
        for lane_x in (left_lane_x, right_lane_x):
            route = _dedupe_route_points([
                start,
                start_on_line,
                _nearest_walkable_point(lane_x, current_y),
                _nearest_walkable_point(lane_x, target_row_y),
                goal,
            ])
            if all(_line_is_walkable(a, b) for a, b in zip(route, route[1:])):
                candidates.append(route)
        if candidates:
            return min(candidates, key=_route_length)

    if not in_panel_area:
        outer_y = panel_max_y + row_spacing * 0.6 if start["y"] >= panel_max_y else panel_min_y - row_spacing * 0.6 if start["y"] <= panel_min_y else start["y"]
        outer_y = max(farm.boundary_y_min + 1.0, min(farm.boundary_y_max - 1.0, outer_y))
        nearest_lane_x = min((left_lane_x, right_lane_x), key=lambda x: abs(x - start["x"]))
        outer_entry = _nearest_walkable_point(nearest_lane_x, outer_y)
        if _line_is_walkable(start, start_departure) and _line_is_walkable(start_departure, outer_entry):
            for lane_x in (nearest_lane_x, left_lane_x if nearest_lane_x == right_lane_x else right_lane_x):
                route = _dedupe_route_points([
                    start,
                    start_departure,
                    outer_entry,
                    _nearest_walkable_point(lane_x, outer_y),
                    _nearest_walkable_point(lane_x, target_row_y),
                    goal,
                ])
                if all(_line_is_walkable(a, b) for a, b in zip(route, route[1:])):
                    candidates.append(route)
            if candidates:
                return min(candidates, key=_route_length)

    for lane_x in (left_lane_x, right_lane_x):
        entry = _nearest_walkable_point(lane_x, target_row_y)
        route = [
            start,
            start_departure,
            _nearest_walkable_point(lane_x, start_departure["y"]),
            entry,
            goal,
        ]
        route = _dedupe_route_points(route)
        if all(_line_is_walkable(a, b) for a, b in zip(route, route[1:])):
            candidates.append(route)
    if not candidates:
        for lane_x in (left_lane_x, right_lane_x):
            outer_y = max(farm.boundary_y_min + 1.0, min(farm.boundary_y_max - 1.0, start["y"]))
            route = [
                start,
                start_departure,
                _nearest_walkable_point(lane_x, outer_y),
                _nearest_walkable_point(lane_x, target_row_y),
                goal,
            ]
            route = _dedupe_route_points(route)
            if all(_line_is_walkable(a, b) for a, b in zip(route, route[1:])):
                candidates.append(route)
    if not candidates:
        return []
    return min(candidates, key=_route_length)


def _same_outer_panel_row_lane(a_y: float, b_y: float) -> bool:
    if not farm.panels:
        return False
    row_spacing = _axis_spacing([p.y for p in farm.panels], 5.0)
    panel_min_y = min(p.y for p in farm.panels)
    panel_max_y = max(p.y for p in farm.panels)
    outer_top = panel_max_y + row_spacing * 0.5
    outer_bottom = panel_min_y - row_spacing * 0.5
    return (
        abs(a_y - b_y) <= 0.35
        and (
            abs(b_y - outer_top) <= 0.35
            or abs(b_y - outer_bottom) <= 0.35
        )
    )


def _same_panel_aisle(a_y: float, b_y: float) -> bool:
    if not farm.panels:
        return True
    row_spacing = _axis_spacing([p.y for p in farm.panels], 5.0)
    return abs(_nearest_aisle_center_y(a_y) - _nearest_aisle_center_y(b_y)) <= row_spacing * 0.15


def _nearest_aisle_center_y(y: float) -> float:
    rows = sorted(set(round(p.y, 6) for p in farm.panels))
    if len(rows) < 2:
        return y
    centers = [(a + b) / 2 for a, b in zip(rows, rows[1:])]
    return min(centers, key=lambda c: abs(c - y))


def _route_length(route: list[dict[str, float]]) -> float:
    return sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in zip(route, route[1:]))


def _panel_route_uses_perimeter(route: list[dict[str, float]]) -> bool:
    if not route or not farm.panels:
        return False
    panel_min_x = min(p.x for p in farm.panels)
    panel_max_x = max(p.x for p in farm.panels)
    panel_half_x = 1.0 + PANEL_BUFFER_X
    lane_clearance = 1.0
    left_lane_x = max(farm.boundary_x_min + 1.0, panel_min_x - panel_half_x - lane_clearance)
    right_lane_x = min(farm.boundary_x_max - 1.0, panel_max_x + panel_half_x + lane_clearance)
    return any(point["x"] <= left_lane_x + 0.35 or point["x"] >= right_lane_x - 0.35 for point in route[1:-1])


def _valid_panel_route(route: list[dict[str, float]]) -> bool:
    return (
        len(route) >= 3
        and _panel_route_uses_perimeter(route)
        and all(_line_is_walkable(a, b) for a, b in zip(route, route[1:]))
    )


def _go2_base_for(name: str):
    try:
        idx = int(name.split("-")[1]) - 1
    except (IndexError, ValueError):
        idx = 0
    base_idx = 5 + idx
    return farm.bases[base_idx] if base_idx < len(farm.bases) else farm.bases[-1]


def _drone_base_for(name: str):
    try:
        idx = int(name.split("-")[1]) - 1
    except (IndexError, ValueError):
        idx = 0
    return farm.bases[idx] if idx < len(farm.bases) else farm.bases[0]


def _go2_near_base(robot, radius: float = 1.2) -> bool:
    base = _go2_base_for(robot.name)
    return math.hypot(robot.pose.x - base.x, robot.pose.y - base.y) <= radius


def _drone_near_base(drone, radius: float = 1.2) -> bool:
    base = _drone_base_for(drone.name)
    landing_z = terrain_height(base.x, base.y) + DRONE_PARK_WORLD_Y
    return math.hypot(drone.pose.x - base.x, drone.pose.y - base.y) <= radius and abs(drone.pose.z - landing_z) <= 0.8


def _plan_go2_point_route(robot_name: str, target: dict[str, float]) -> list[dict[str, float]]:
    robot = next((g for g in sim.go2s if g.name == robot_name), None)
    if not robot:
        return []
    start = _nearest_walkable_point(robot.pose.x, robot.pose.y)
    goal = _nearest_walkable_point(float(target["x"]), float(target["y"]))
    if _line_is_walkable(start, goal):
        return [start, goal]
    return _visibility_route(start, goal)


def _sanitize_go2_route(robot, route: list[dict[str, float]]) -> list[dict[str, float]]:
    sanitized = [
        _nearest_go2_body_walkable_point(float(point["x"]), float(point["y"]), robot.pose.yaw)
        for point in route
    ]
    return _dedupe_route_points(sanitized)


def _plan_go2_return_route(robot_name: str, target: dict[str, float]) -> list[dict[str, float]]:
    robot = next((g for g in sim.go2s if g.name == robot_name), None)
    if not robot:
        return []
    start = _nearest_go2_body_walkable_point(robot.pose.x, robot.pose.y, robot.pose.yaw)
    goal = _nearest_go2_body_walkable_point(float(target["x"]), float(target["y"]), robot.pose.yaw)
    if not farm.panels:
        route = [start, goal] if _line_is_walkable(start, goal) else _visibility_route(start, goal)
        return _sanitize_go2_route(robot, route)

    panel_min_x = min(p.x for p in farm.panels)
    panel_max_x = max(p.x for p in farm.panels)
    panel_max_y = max(p.y for p in farm.panels)
    row_spacing = _axis_spacing([p.y for p in farm.panels], 5.0)
    panel_half_x = 1.0 + PANEL_BUFFER_X
    lane_clearance = 1.0
    left_lane_x = max(farm.boundary_x_min + 1.0, panel_min_x - panel_half_x - lane_clearance)
    right_lane_x = min(farm.boundary_x_max - 1.0, panel_max_x + panel_half_x + lane_clearance)
    service_y = min(farm.boundary_y_max - 1.0, panel_max_y + row_spacing * 0.65)
    current_y = _nearest_aisle_center_y(start["y"])

    candidates: list[list[dict[str, float]]] = []
    for lane_x in (left_lane_x, right_lane_x):
        route = _dedupe_route_points([
            start,
            _nearest_go2_body_walkable_point(lane_x, current_y, robot.pose.yaw),
            _nearest_go2_body_walkable_point(lane_x, service_y, robot.pose.yaw),
            _nearest_go2_body_walkable_point(goal["x"], service_y, robot.pose.yaw),
            goal,
        ])
        if all(_line_is_walkable(a, b) for a, b in zip(route, route[1:])):
            candidates.append(route)
    if candidates:
        return min(candidates, key=_route_length)

    route = _plan_go2_point_route(robot_name, target)
    return _sanitize_go2_route(robot, route)


def _route_segment_points(assignment: dict[str, Any]) -> Optional[tuple[dict[str, float], dict[str, float]]]:
    route = assignment.get("route") or []
    if len(route) < 2:
        return None
    idx = max(1, min(int(assignment.get("route_index", 1)), len(route) - 1))
    return route[idx - 1], route[idx]


def _route_segment_key(assignment: dict[str, Any]) -> Optional[tuple[int, int, int, int]]:
    seg = _route_segment_points(assignment)
    if not seg:
        return None
    a, b = seg
    return (
        round(float(a["x"]) * 2),
        round(float(a["y"]) * 2),
        round(float(b["x"]) * 2),
        round(float(b["y"]) * 2),
    )


def _segments_conflict(a: tuple[dict[str, float], dict[str, float]], b: tuple[dict[str, float], dict[str, float]]) -> bool:
    a0, a1 = a
    b0, b1 = b
    a_key = _route_segment_key({"route": [a0, a1], "route_index": 1})
    b_key = _route_segment_key({"route": [b0, b1], "route_index": 1})
    b_reverse_key = _route_segment_key({"route": [b1, b0], "route_index": 1})
    if a_key == b_key or a_key == b_reverse_key:
        return True
    if _segment_intersects(a0["x"], a0["y"], a1["x"], a1["y"], b0["x"], b0["y"], b1["x"], b1["y"]):
        return True
    return min(
        _segment_distance_to_point(a0["x"], a0["y"], a1["x"], a1["y"], b0["x"], b0["y"]),
        _segment_distance_to_point(a0["x"], a0["y"], a1["x"], a1["y"], b1["x"], b1["y"]),
        _segment_distance_to_point(b0["x"], b0["y"], b1["x"], b1["y"], a0["x"], a0["y"]),
        _segment_distance_to_point(b0["x"], b0["y"], b1["x"], b1["y"], a1["x"], a1["y"]),
    ) < 1.0


def _segment_intersects(ax: float, ay: float, bx: float, by: float, cx: float, cy: float, dx: float, dy: float) -> bool:
    def orient(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> float:
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)
    o1 = orient(ax, ay, bx, by, cx, cy)
    o2 = orient(ax, ay, bx, by, dx, dy)
    o3 = orient(cx, cy, dx, dy, ax, ay)
    o4 = orient(cx, cy, dx, dy, bx, by)
    return o1 * o2 < 0 and o3 * o4 < 0


def _assignment_route_progress(data: dict[str, Any]) -> float:
    route = data.get("route") or []
    if len(route) <= 1:
        return 0.0
    route_index = int(data.get("route_index", 0))
    if route_index >= len(route):
        return 1.0
    reached_segments = max(0, route_index - 1)
    return max(0.0, min(1.0, reached_segments / max(1, len(route) - 1)))


def _route_target(assignment: dict[str, Any]) -> tuple[float, float]:
    route = assignment.get("route") or []
    idx = int(assignment.get("route_index", 0))
    if route and idx < len(route):
        point = route[idx]
        return float(point["x"]), float(point["y"])
    row, col = assignment["panel"]
    return _panel_observation_point(row, col)


def _route_follow_target(assignment: dict[str, Any], robot) -> tuple[float, float]:
    route = assignment.get("route") or []
    if not route:
        return _route_target(assignment)
    idx = max(0, min(int(assignment.get("route_index", 0)), len(route) - 1))
    while idx < len(route) and math.hypot(robot.pose.x - route[idx]["x"], robot.pose.y - route[idx]["y"]) < 0.75:
        idx += 1
    assignment["route_index"] = idx
    if idx >= len(route):
        point = route[-1]
        return float(point["x"]), float(point["y"])
    point = route[idx]
    return float(point["x"]), float(point["y"])


def _go2_body_walkable(x: float, y: float, yaw: float) -> bool:
    samples = [
        (0.0, 0.0),
        (0.42, 0.0),
        (-0.35, 0.0),
        (0.0, 0.28),
        (0.0, -0.28),
    ]
    cy, sy = math.cos(yaw), math.sin(yaw)
    for forward, side in samples:
        sx = x + forward * cy - side * sy
        syy = y + forward * sy + side * cy
        if not _is_walkable_point(sx, syy):
            return False
    return True


def _nearest_go2_body_walkable_point(x: float, y: float, yaw: float) -> dict[str, float]:
    if _go2_body_walkable(x, y, yaw):
        return {"x": x, "y": y}
    best: Optional[dict[str, float]] = None
    best_d = float("inf")
    for radius in [i * 0.35 for i in range(1, 100)]:
        samples = max(18, int(radius * 20))
        for idx in range(samples):
            ang = math.tau * idx / samples
            px = x + math.cos(ang) * radius
            py = y + math.sin(ang) * radius
            if not _go2_body_walkable(px, py, yaw):
                continue
            d = math.hypot(px - x, py - y)
            if d < best_d:
                best = {"x": px, "y": py}
                best_d = d
        if best is not None:
            return best
    return _nearest_walkable_point(x, y)


def _retreat_go2_cmd(robot, assignment: dict[str, Any], dt: float) -> VelocityCmd:
    assignment["phase"] = "retreating"
    if "retreat_remaining_m" not in assignment:
        assignment["retreat_remaining_m"] = float(path_settings.get("retreat_distance_m", 0.6))
    robot.status = "moving"
    robot.task_desc = "Obstacle buffer hit; backing out"
    yaw = robot.pose.yaw
    back_x = robot.pose.x - math.cos(yaw) * 0.45
    back_y = robot.pose.y - math.sin(yaw) * 0.45
    if _go2_body_walkable(back_x, back_y, yaw):
        speed = 0.28
        assignment["retreat_remaining_m"] = max(0.0, float(assignment.get("retreat_remaining_m", 0.0)) - speed * dt)
        return VelocityCmd(-speed * math.cos(yaw), -speed * math.sin(yaw), 0.0)
    safe = assignment.get("retreat_target")
    if not safe:
        safe = _nearest_go2_body_walkable_point(robot.pose.x, robot.pose.y, robot.pose.yaw)
        assignment["retreat_target"] = safe
    return robot.move_to(float(safe["x"]), float(safe["y"]), dt)


def _guard_go2_navigation_cmd(robot, cmd: VelocityCmd, assignment: dict[str, Any], dt: float) -> VelocityCmd:
    if assignment.get("phase") == "retreating":
        if float(assignment.get("retreat_remaining_m", 0.0)) <= 0 and _go2_body_walkable(robot.pose.x, robot.pose.y, robot.pose.yaw):
            assignment["phase"] = "moving"
            assignment.pop("retreat_target", None)
            assignment.pop("retreat_remaining_m", None)
            return VelocityCmd()
        return _retreat_go2_cmd(robot, assignment, dt)
    moving = math.hypot(cmd.linear_x, cmd.linear_y)
    if moving < 1e-4:
        return cmd
    dir_x, dir_y = cmd.linear_x / moving, cmd.linear_y / moving
    checks = []
    for horizon in (0.25, 0.55, 0.9):
        checks.append((robot.pose.x + dir_x * horizon, robot.pose.y + dir_y * horizon))
    checks.append((robot.pose.x + cmd.linear_x * max(dt, 0.1), robot.pose.y + cmd.linear_y * max(dt, 0.1)))
    if not _go2_body_walkable(robot.pose.x, robot.pose.y, robot.pose.yaw) or any(
        not _go2_body_walkable(x, y, robot.pose.yaw) for x, y in checks
    ):
        assignment["retreat_remaining_m"] = float(path_settings.get("retreat_distance_m", 0.6))
        assignment.pop("retreat_target", None)
        return _retreat_go2_cmd(robot, assignment, dt)
    return cmd


def _align_go2_to_target(robot, assignment: dict[str, Any], dt: float) -> tuple[VelocityCmd, bool]:
    target = assignment.get("target_point") or {}
    tx, ty = float(target.get("x", robot.pose.x)), float(target.get("y", robot.pose.y))
    desired = math.atan2(ty - robot.pose.y, tx - robot.pose.x)
    err = (desired - robot.pose.yaw + math.pi) % (2 * math.pi) - math.pi
    if abs(err) <= math.radians(5):
        robot.pose.yaw = desired
        robot.camera_active = True
        return VelocityCmd(), True
    turn = max(-0.45, min(0.45, err * 1.2))
    robot.status = "aligning"
    robot.camera_active = True
    robot.task_desc = "Aligning camera to target"
    return VelocityCmd(0.0, 0.0, turn), False


def _panel_by_key(row: int, col: int):
    return next((p for p in farm.panels if p.row == row and p.col == col), None)


PANEL_STATUSES = {"unknown", "healthy", "drone_anomaly", "confirmed_fault", "repaired"}


def update_panel_status(row: int, col: int, status: str, source: str, note: str = "") -> bool:
    panel = _panel_by_key(row, col)
    if not panel or status not in PANEL_STATUSES:
        return False
    panel.status = status
    panel.last_update_ts = sim_now()
    panel.last_update_source = source
    panel.last_update_note = note
    if status == "unknown":
        panel.is_defective = False
        panel.inspected = False
        panel.anomaly_confirmed = False
        panel.anomaly_type = ""
        _remove_panel_key(farm.anomalies_found, (row, col))
        _remove_panel_key(farm.anomalies_inspected, (row, col))
        _remove_panel_key(mission.anomaly_queue, (row, col))
        _remove_panel_key(ground_check_queue, (row, col))
    elif status in {"healthy", "repaired"}:
        panel.is_defective = False
        if status == "repaired" and source == "manual":
            panel.true_is_defective = False
        panel.inspected = True
        panel.anomaly_confirmed = False
        panel.anomaly_type = ""
        _remove_panel_key(farm.anomalies_found, (row, col))
        _remove_panel_key(farm.anomalies_inspected, (row, col))
        _remove_panel_key(mission.anomaly_queue, (row, col))
        _remove_panel_key(ground_check_queue, (row, col))
    elif status == "drone_anomaly":
        panel.is_defective = True
        panel.inspected = True
        panel.anomaly_confirmed = False
        panel.anomaly_type = "suspected"
        if (row, col) not in farm.anomalies_found:
            farm.anomalies_found.append((row, col))
        if (row, col) not in mission.anomaly_queue:
            mission.anomaly_queue.append((row, col))
    elif status == "confirmed_fault":
        panel.is_defective = True
        if source == "manual":
            panel.true_is_defective = True
        panel.inspected = True
        panel.anomaly_confirmed = True
        panel.anomaly_type = "hotspot"
        if (row, col) not in farm.anomalies_found:
            farm.anomalies_found.append((row, col))
        if (row, col) not in farm.anomalies_inspected:
            farm.anomalies_inspected.append((row, col))
        if (row, col) not in mission.inspected_list:
            mission.inspected_list.append((row, col))
        _remove_panel_key(mission.anomaly_queue, (row, col))
        _remove_panel_key(ground_check_queue, (row, col))
    return True


def _remove_panel_key(items: list, key: tuple[int, int]) -> None:
    items[:] = [tuple(item) for item in items if tuple(item) != key]


def expire_panel_statuses(now: Optional[float] = None) -> None:
    now = sim_now() if now is None else now
    ttl = float(panel_settings.get("healthy_ttl_s", 300.0))
    if ttl <= 0:
        return
    for panel in farm.panels:
        if panel.status in {"healthy", "repaired"} and panel.last_update_ts and now - panel.last_update_ts >= ttl:
            update_panel_status(panel.row, panel.col, "unknown", "system", "Healthy status TTL expired")


def panel_status_public(panel) -> dict[str, Any]:
    return {
        "status": panel.status,
        "last_update_ts": panel.last_update_ts,
        "last_update_source": panel.last_update_source,
        "last_update_note": panel.last_update_note,
    }


legacy_drone_scan_synced: set[tuple[int, int]] = set()


def reconcile_legacy_panel_statuses() -> None:
    """Keep older orchestrator scan paths aligned with the unified panel status model."""
    newly_reached = {
        (wp.row, wp.col)
        for wp_list in farm.waypoints
        for wp in wp_list
        if wp.reached and (wp.row, wp.col) not in legacy_drone_scan_synced
    }
    for panel in farm.panels:
        key = (panel.row, panel.col)
        if panel.anomaly_confirmed and panel.status != "confirmed_fault":
            update_panel_status(panel.row, panel.col, "confirmed_fault", "go2", "Legacy Go2 confirmation sync")
        elif key in newly_reached and panel.status != "confirmed_fault":
            update_panel_status(
                panel.row,
                panel.col,
                "drone_anomaly" if panel.true_is_defective else "healthy",
                "drone",
                "Aerial inspection sync",
            )
        if key in newly_reached:
            legacy_drone_scan_synced.add(key)


def mutate_hidden_panel_truth(now: Optional[float] = None) -> None:
    global last_panel_truth_mutation_ts
    now = sim_now() if now is None else now
    interval = float(panel_settings.get("degradation_interval_s", 120.0))
    if interval <= 0 or now - last_panel_truth_mutation_ts < interval:
        return
    last_panel_truth_mutation_ts = now
    healthy_truth = [p for p in farm.panels if not p.true_is_defective]
    if not healthy_truth:
        return
    panel = random.choice(healthy_truth)
    panel.true_is_defective = True
    mission.log(f"Hidden fault developed at panel ({panel.row},{panel.col}); awaiting next inspection.")


def record_drone_scan_result(panel, drone_name: str, note: str) -> None:
    if panel.status == "confirmed_fault":
        panel.inspected = True
        panel.is_defective = True
        return
    update_panel_status(
        panel.row,
        panel.col,
        "drone_anomaly" if panel.true_is_defective else "healthy",
        "drone",
        f"{drone_name} {note}",
    )


def start_go2_navigation(robot_name: str, row: int, col: int, *, confirm: bool = False) -> bool:
    robot = next((g for g in sim.go2s if g.name == robot_name), None)
    panel = _panel_by_key(row, col)
    if not robot or not panel:
        return False
    obs_x, obs_y = _panel_observation_point(row, col)
    already_at_target = math.hypot(robot.pose.x - obs_x, robot.pose.y - obs_y) <= max(0.8, robot.pos_tol * 1.5)
    route = [] if already_at_target else _plan_go2_route(robot_name, (row, col))
    robot.manual_vx = robot.manual_vy = robot.manual_va = 0.0
    robot.manual_timer = 0.0
    robot.nav_vx = robot.nav_vy = robot.nav_va = 0.0
    ground_check_assignments[robot_name] = {
        "panel": (row, col),
        "target_point": {"x": panel.x, "y": panel.y},
        "phase": "aligning" if already_at_target else "moving",
        "dwell": 0.0,
        "route": route,
        "route_index": 1,
        "last_replan": time.time(),
        "confirm": confirm,
        "last_pos": (robot.pose.x, robot.pose.y),
        "last_progress_ts": time.time(),
        "stuck_replans": 0,
    }
    robot.status = "moving"
    robot.camera_active = False
    if already_at_target:
        robot.task_desc = f"At panel ({row},{col}); aligning camera"
        mission.log(f"{robot_name} already at panel ({row},{col}); aligning camera.")
    elif route:
        robot.task_desc = f"Navigating perimeter route to panel ({row},{col})"
        mission.log(f"{robot_name} navigating perimeter route to panel ({row},{col})")
    else:
        robot.status = "waiting"
        robot.task_desc = f"No perimeter route to panel ({row},{col})"
        mission.log(f"{robot_name} has no valid perimeter route to panel ({row},{col}); holding position.")
    return True


def start_go2_return_base(robot_name: str, reason: str = "Returning to base") -> bool:
    robot = next((g for g in sim.go2s if g.name == robot_name), None)
    if not robot:
        return False
    base = _go2_base_for(robot_name)
    target = {"x": base.x, "y": base.y}
    safe = _nearest_go2_body_walkable_point(robot.pose.x, robot.pose.y, robot.pose.yaw)
    if math.hypot(robot.pose.x - float(safe["x"]), robot.pose.y - float(safe["y"])) > 0.05:
        robot.pose.x = float(safe["x"])
        robot.pose.y = float(safe["y"])
    route = _plan_go2_return_route(robot_name, target)
    robot.manual_vx = robot.manual_vy = robot.manual_va = 0.0
    robot.manual_timer = 0.0
    robot.nav_vx = robot.nav_vy = robot.nav_va = 0.0
    go2_return_assignments[robot_name] = {
        "target": target,
        "phase": "returning_base",
        "route": route,
        "route_index": 1,
        "last_pos": (robot.pose.x, robot.pose.y),
        "last_progress_ts": time.time(),
    }
    ground_check_assignments.pop(robot_name, None)
    robot.status = "returning"
    robot.task_desc = reason
    mission.log(f"{robot_name} returning to base: {reason}")
    return True


def start_drone_navigation(drone_name: str, row: int, col: int) -> bool:
    drone = next((d for d in sim.drones if d.name == drone_name), None)
    panel = _panel_by_key(row, col)
    if not drone or not panel:
        return False
    target_z = terrain_height(panel.x, panel.y) + farm.drone_scan_altitude
    drone_nav_assignments[drone_name] = {
        "panel": (row, col),
        "target": {"x": panel.x, "y": panel.y, "z": target_z},
    }
    drone.status = "flying"
    drone.camera_active = False
    drone.task_desc = f"Navigating to panel ({row},{col})"
    mission.log(f"{drone_name} navigating to panel ({row},{col})")
    return True


def _idle_drone_name() -> Optional[str]:
    busy = set(drone_nav_assignments) | set(drone_row_scan_assignments)
    for drone in sim.drones:
        if drone.name not in busy and drone.status in {"idle", "landed", "hovering"}:
            return drone.name
    for drone in sim.drones:
        if drone.name not in busy:
            return drone.name
    return sim.drones[0].name if sim.drones else None


def _idle_go2_name() -> Optional[str]:
    busy = set(ground_check_assignments) | set(go2_return_assignments)
    disabled = _disabled_go2_names()
    candidates = [g for g in sim.go2s if g.name != "Go2-01" and g.name not in disabled]
    candidates.extend(g for g in sim.go2s if g.name == "Go2-01" and g.name not in disabled)
    for robot in candidates:
        if robot.battery <= float(robot_settings.get("go2_battery_redline_pct", 20.0)):
            continue
        if robot.name not in busy and robot.status in {"idle", "landed"} and robot.manual_timer <= 0:
            return robot.name
    for robot in candidates:
        if robot.battery <= float(robot_settings.get("go2_battery_redline_pct", 20.0)):
            continue
        if robot.name not in busy:
            return robot.name
    return None


def start_drone_row_scan(drone_name: Optional[str], row: int) -> bool:
    panels = sorted((p for p in farm.panels if p.row == row), key=lambda p: p.col)
    if not panels:
        return False
    target_drone = drone_name if drone_name and drone_name != "auto" else _idle_drone_name()
    drone = next((d for d in sim.drones if d.name == target_drone), None)
    if not drone:
        return False
    drone_row_scan_assignments[drone.name] = {
        "row": row,
        "panels": [(p.row, p.col) for p in panels],
        "index": 0,
        "inspected": 0,
    }
    drone.status = "flying"
    drone.task_desc = f"Scanning row {row}"
    mission.log(f"{drone.name} assigned to scan row {row}")
    return True


def start_drone_panel_scan(drone_name: Optional[str], panels: list[tuple[int, int]]) -> bool:
    valid = [(r, c) for r, c in panels if _panel_by_key(r, c)]
    if not valid:
        return False
    target_drone = drone_name if drone_name and drone_name != "auto" else _idle_drone_name()
    drone = next((d for d in sim.drones if d.name == target_drone), None)
    if not drone:
        return False
    drone_row_scan_assignments[drone.name] = {
        "row": None,
        "panels": valid,
        "index": 0,
        "inspected": 0,
    }
    drone.status = "flying"
    drone.task_desc = "Scanning selected panels"
    mission.log(f"{drone.name} assigned to scan {len(valid)} panel(s)")
    return True


def queue_go2_confirm_anomalies(row: Optional[int] = None, panels: Optional[list[tuple[int, int]]] = None) -> bool:
    candidates: list[tuple[int, int]] = []
    explicit = set(panels or [])
    for panel in farm.panels:
        key = (panel.row, panel.col)
        if explicit and key not in explicit:
            continue
        if row is not None and panel.row != row:
            continue
        if panel.status == "drone_anomaly":
            candidates.append(key)
    for key in candidates:
        if key not in mission.anomaly_queue and key not in ground_check_queue:
            mission.anomaly_queue.append(key)
    if candidates:
        start_ground_confirmation()
        mission.log(f"Queued Go2 confirmation for {len(candidates)} anomaly panel(s).")
        return True
    mission.log("No discovered anomalies need Go2 confirmation.")
    return True


def start_ground_confirmation() -> None:
    for item in _pending_ground_panels():
        if item not in ground_check_queue and all(a.get("panel") != item for a in ground_check_assignments.values()):
            ground_check_queue.append(item)
    mission.anomaly_queue.clear()
    if ground_check_queue or ground_check_assignments:
        mission.log(f"Ground confirmation queued for {len(ground_check_queue) + len(ground_check_assignments)} panel(s).")


def _assign_ground_checks() -> None:
    disabled = _disabled_go2_names()
    ordered = [g for g in sim.go2s if g.name != "Go2-01" and g.name not in disabled]
    ordered.extend(g for g in sim.go2s if g.name == "Go2-01" and g.name not in disabled)
    for robot in ordered:
        if not ground_check_queue:
            return
        if robot.name in ground_check_assignments:
            continue
        if robot.name in go2_return_assignments:
            continue
        if robot.battery <= float(robot_settings.get("go2_battery_redline_pct", 20.0)):
            continue
        ranked = sorted(
            list(ground_check_queue),
            key=lambda key: math.hypot(_panel_observation_point(*key)[0] - robot.pose.x, _panel_observation_point(*key)[1] - robot.pose.y),
        )
        for panel_key in ranked:
            route = _plan_go2_route(robot.name, panel_key)
            if not route:
                continue
            ground_check_queue.remove(panel_key)
            start_go2_navigation(robot.name, panel_key[0], panel_key[1], confirm=True)
            mission.log(f"{robot.name} assigned nearest panel ({panel_key[0]},{panel_key[1]})")
            break


def _background_drone_rtb(dt: float) -> Optional[list[dict]]:
    if not sim.drones:
        return None
    airborne = any(d.status not in {"idle", "landed"} for d in sim.drones)
    if not airborne:
        return None
    cmds = []
    for i, d in enumerate(sim.drones):
        if d.status in {"idle", "landed"}:
            cmds.append({"vx": 0, "vy": 0, "vz": 0, "yaw": d.pose.yaw, "dist": 0, "dz": 0})
            continue
        assignment = {"reason": "Returning to pad", "phase": d.task_desc if d.task_desc == "Descending to pad" else "cruise"}
        cmd = _compute_safe_drone_return_cmd(d, assignment, dt)
        cmds.append(cmd)
    return cmds


def _drone_return_altitude(drone, pad) -> float:
    ground = terrain_height(pad.x, pad.y)
    return max(
        float(drone.pose.z),
        ground + max(float(getattr(farm, "drone_scan_altitude", 0.0)), DRONE_RETURN_MIN_AGL),
        ground + DRONE_PARK_WORLD_Y + DRONE_RETURN_MIN_AGL,
    )


def _land_drone_on_pad(drone, pad) -> dict:
    ground = terrain_height(pad.x, pad.y)
    drone.pose.x = pad.x
    drone.pose.y = pad.y
    drone.pose.z = ground + DRONE_PARK_WORLD_Y
    drone.prev_z_err = 0.0
    drone._vx_smooth = drone._vy_smooth = drone._vz_smooth = 0.0
    drone.propeller_rpm = 0
    drone.status = "landed"
    drone.task_desc = "Landed on pad"
    return {"vx": 0, "vy": 0, "vz": 0, "yaw": drone.pose.yaw, "dist": 0, "dz": 0}


def _compute_safe_drone_return_cmd(drone, assignment: dict[str, Any], dt: float) -> dict:
    pad = _drone_base_for(drone.name)
    hd = math.hypot(drone.pose.x - pad.x, drone.pose.y - pad.y)
    phase = assignment.get("phase") or "cruise"
    if phase != "descend" and hd > 0.7:
        target_z = float(assignment.get("cruise_z") or _drone_return_altitude(drone, pad))
        assignment["cruise_z"] = target_z
        drone.status = "returning"
        drone.task_desc = assignment.get("reason", "Returning to pad")
        cmd = drone.compute_flight(pad.x, pad.y, target_z, dt)
        cmd["dist"] = max(cmd.get("dist", hd), 1.0)
        return cmd

    assignment["phase"] = "descend"
    ground = terrain_height(pad.x, pad.y)
    target_z = ground + DRONE_PARK_WORLD_Y
    drone.pose.x = pad.x
    drone.pose.y = pad.y
    drone.status = "landing"
    drone.task_desc = "Descending to pad"
    drone.pose.z = max(target_z, drone.pose.z - drone.max_v_speed * dt)
    drone._vx_smooth = drone._vy_smooth = drone._vz_smooth = 0.0
    if drone.pose.z <= target_z + 0.02:
        return _land_drone_on_pad(drone, pad)
    return {"vx": 0, "vy": 0, "vz": 0, "yaw": drone.pose.yaw, "dist": 1.0, "dz": 0}


def start_drone_return_base(drone_name: str, reason: str = "Returning to pad") -> bool:
    drone = next((d for d in sim.drones if d.name == drone_name), None)
    if not drone:
        return False
    drone_nav_assignments.pop(drone_name, None)
    drone_row_scan_assignments.pop(drone_name, None)
    mission.drone_states.pop(drone_name, None)
    mission.drone_scan_indices.pop(drone_name, None)
    mission.drone_photo_cooldowns.pop(drone_name, None)
    drone_return_assignments[drone_name] = {"reason": reason, "phase": "cruise"}
    drone.status = "returning"
    drone.task_desc = reason
    mission.log(f"{drone_name} returning to pad: {reason}")
    return True


def update_drone_returns(dt: float, drone_cmds: list[dict]) -> list[dict]:
    for idx, drone in enumerate(sim.drones):
        assignment = drone_return_assignments.get(drone.name)
        if not assignment:
            continue
        cmd = _compute_safe_drone_return_cmd(drone, assignment, dt)
        if drone.status == "landed":
            del drone_return_assignments[drone.name]
        if idx < len(drone_cmds):
            drone_cmds[idx] = cmd
    return drone_cmds


def update_battery_and_redlines(dt: float) -> None:
    go2_redline = float(robot_settings.get("go2_battery_redline_pct", 20.0))
    drone_redline = float(robot_settings.get("drone_battery_redline_pct", 20.0))
    for robot in sim.go2s:
        if _go2_near_base(robot):
            robot.battery = min(100.0, robot.battery + dt / 30.0)
            continue
        moving = robot.name in ground_check_assignments or robot.name in go2_return_assignments or robot.status in {"moving", "returning", "aligning", "inspecting"}
        off_base_idle = robot.status in {"idle", "waiting"} and not _go2_near_base(robot)
        drain = (dt / 30.0 if moving else dt / 120.0 if off_base_idle else 0.0)
        if drain > 0:
            robot.battery = max(0.0, robot.battery - drain)
        if robot.battery <= go2_redline and not _go2_near_base(robot) and robot.name not in go2_return_assignments:
            assignment = ground_check_assignments.get(robot.name)
            if assignment and assignment.get("confirm", True):
                panel = assignment.get("panel")
                if panel and panel not in ground_check_queue:
                    ground_check_queue.insert(0, panel)
            start_go2_return_base(robot.name, "Low battery return")

    for drone in sim.drones:
        if _drone_near_base(drone):
            drone.battery = min(100.0, drone.battery + dt / 30.0)
            continue
        if drone.battery <= drone_redline and not _drone_near_base(drone) and drone.name not in drone_return_assignments:
            start_drone_return_base(drone.name, "Low battery return")


def stop_blocked_task(task_id: str) -> tuple[bool, str]:
    task = next((t for t in task_records if t.id == task_id), None)
    if not task:
        return False, "task not found"
    if task.status != "blocked":
        return False, f"task is not blocked: {task.status}"
    step = task.current_step()
    task.status = "cancelled"
    task.completed_ts = time.time()
    if step and step.status == "blocked":
        step.status = "cancelled"
        step.completed_ts = time.time()
    for target, meta in list(manual_motion_tasks.items()):
        if meta.get("task_id") == task_id:
            manual_motion_tasks.pop(target, None)
            robot = next((g for g in sim.go2s if g.name == target), None)
            if robot:
                robot.manual_vx = robot.manual_vy = robot.manual_va = 0.0
                robot.manual_timer = 0.0
                robot.status = "idle"
                robot.task_desc = "Stopped after blocked task"
    _append_task_log(task, step, "Blocked task stopped by operator.")
    mission.log(f"Blocked task stopped: {task_id}")
    return True, "Blocked task stopped."


def retry_blocked_task(task_id: str) -> tuple[bool, str]:
    task = next((t for t in task_records if t.id == task_id), None)
    if not task:
        return False, "task not found"
    if task.status != "blocked":
        return False, f"task is not blocked: {task.status}"
    step = task.current_step()
    if not step:
        return False, "no current step"
    for action in step.actions:
        target = action.robot or _default_target(action.skill)
        manual_motion_tasks.pop(target, None)
        robot = next((g for g in sim.go2s if g.name == target), None)
        if robot:
            robot.manual_vx = robot.manual_vy = robot.manual_va = 0.0
            robot.manual_timer = 0.0
            robot.status = "idle"
            robot.task_desc = "Retrying blocked task"
    step.status = "pending"
    step.started_ts = None
    step.completed_ts = None
    task.status = "running"
    _append_task_log(task, step, "Retry requested for blocked step.")
    mission.log(f"Retry requested for blocked task: {task_id}")
    return True, "Retry queued for blocked step."


def _update_go2_return_assignment(
    robot,
    assignment: dict[str, Any],
    dt: float,
) -> VelocityCmd:
    route = assignment.get("route") or []
    if _go2_near_base(robot):
        target = assignment.get("target") or {}
        robot.pose.x = float(target.get("x", robot.pose.x))
        robot.pose.y = float(target.get("y", robot.pose.y))
        robot.status = "idle"
        robot.task_desc = "Returned to base"
        robot.nav_vx = robot.nav_vy = robot.nav_va = 0.0
        assignment["done"] = True
        return VelocityCmd()
    if assignment.get("phase") == "retreating":
        return _guard_go2_navigation_cmd(robot, VelocityCmd(), assignment, dt)
    assignment["phase"] = "returning_base"
    if route:
        tx, ty = _route_follow_target(assignment, robot)
    else:
        target = assignment.get("target") or {}
        tx, ty = float(target.get("x", robot.pose.x)), float(target.get("y", robot.pose.y))
    if route and int(assignment.get("route_index", 0)) >= len(route):
        target = assignment.get("target") or {}
        tx, ty = float(target.get("x", robot.pose.x)), float(target.get("y", robot.pose.y))
    robot.status = "returning"
    robot.task_desc = "Returning to base"
    cmd = robot.move_to(tx, ty, dt)
    cmd = _guard_go2_navigation_cmd(robot, cmd, assignment, dt)
    _check_go2_stuck(robot, assignment)
    return cmd


def _check_go2_stuck(robot, assignment: dict[str, Any]) -> None:
    now = time.time()
    last_pos = assignment.get("last_pos") or (robot.pose.x, robot.pose.y)
    moved = math.hypot(robot.pose.x - float(last_pos[0]), robot.pose.y - float(last_pos[1]))
    progress_ts = float(assignment.get("last_progress_ts") or now)
    if moved >= 0.12:
        assignment["last_pos"] = (robot.pose.x, robot.pose.y)
        assignment["last_progress_ts"] = now
        return
    if now - progress_ts < 5.0:
        return
    row, col = assignment.get("panel") or (None, None)
    assignment["stuck_replans"] = int(assignment.get("stuck_replans", 0)) + 1
    if row is not None and col is not None:
        safe = _nearest_go2_body_walkable_point(robot.pose.x, robot.pose.y, robot.pose.yaw)
        robot.pose.x = float(safe["x"])
        robot.pose.y = float(safe["y"])
        assignment["route"] = _plan_go2_route(robot.name, (row, col))
        assignment["route_index"] = min(1, max(0, len(assignment["route"]) - 1))
        assignment["last_replan"] = now
        assignment["last_pos"] = (robot.pose.x, robot.pose.y)
        assignment["last_progress_ts"] = now
        robot.task_desc = "Recovered from stall; replanned route"
        return
    target = assignment.get("target")
    if isinstance(target, dict):
        safe = _nearest_go2_body_walkable_point(robot.pose.x, robot.pose.y, robot.pose.yaw)
        robot.pose.x = float(safe["x"])
        robot.pose.y = float(safe["y"])
        assignment["route"] = _plan_go2_point_route(robot.name, target)
        assignment["route_index"] = min(1, max(0, len(assignment["route"]) - 1))
        assignment["last_replan"] = now
        assignment["last_pos"] = (robot.pose.x, robot.pose.y)
        assignment["last_progress_ts"] = now
        assignment["phase"] = "returning_base"
        robot.task_desc = "Recovered from stall; replanned return route"


def update_ground_confirmations(dt: float, go2_cmds: list[VelocityCmd]) -> list[VelocityCmd]:
    _assign_ground_checks()
    # Navigation assignments are authoritative here. Ignore legacy mission Go2 commands
    # so an untargeted Go2 cannot move on someone else's task.
    go2_cmds = [VelocityCmd() for _ in sim.go2s]
    for idx, robot in enumerate(sim.go2s):
        return_assignment = go2_return_assignments.get(robot.name)
        if return_assignment:
            cmd = _update_go2_return_assignment(robot, return_assignment, dt)
            if idx < len(go2_cmds):
                go2_cmds[idx] = cmd
            if return_assignment.get("done"):
                del go2_return_assignments[robot.name]
            continue
        assignment = ground_check_assignments.get(robot.name)
        if not assignment:
            robot.nav_vx = robot.nav_vy = robot.nav_va = 0.0
            if robot.manual_timer <= 0 and robot.status not in {"inspecting"}:
                robot.status = "idle"
                robot.task_desc = "Standby"
            continue
        row, col = assignment["panel"]
        panel = next((p for p in farm.panels if p.row == row and p.col == col), None)
        if not panel:
            del ground_check_assignments[robot.name]
            continue
        now = time.time()
        obs_x, obs_y = _panel_observation_point(row, col)
        if assignment["phase"] == "moving" and math.hypot(robot.pose.x - obs_x, robot.pose.y - obs_y) <= max(0.85, robot.pos_tol * 1.8):
            assignment["phase"] = "aligning"
            assignment["route_index"] = len(assignment.get("route") or [])
            robot.nav_vx = robot.nav_vy = robot.nav_va = 0.0
            route = assignment.get("route") or []
            route_index = int(assignment.get("route_index", 0))
        else:
            route = assignment.get("route") or []
            route_index = int(assignment.get("route_index", 0))
        if (
            assignment["phase"] == "moving"
            and path_settings.get("mode") == "dynamic"
            and now - float(assignment.get("last_replan", 0.0)) >= float(path_settings.get("replan_interval_s", 3.0))
        ):
            assignment["route"] = _plan_go2_route(robot.name, (row, col))
            assignment["route_index"] = min(1, max(0, len(assignment["route"]) - 1))
            assignment["last_replan"] = now
            route = assignment.get("route") or []
            route_index = int(assignment.get("route_index", 0))
        if assignment["phase"] == "moving" and route and len(route) > 2 and not _valid_panel_route(route):
            assignment["route"] = _plan_go2_route(robot.name, (row, col))
            assignment["route_index"] = min(1, max(0, len(assignment["route"]) - 1))
            assignment["last_replan"] = now
            route = assignment.get("route") or []
            route_index = int(assignment.get("route_index", 0))
        if assignment["phase"] == "moving" and not route:
            assignment["route"] = _plan_go2_route(robot.name, (row, col))
            assignment["route_index"] = min(1, max(0, len(assignment["route"]) - 1))
            assignment["last_replan"] = now
            route = assignment.get("route") or []
            route_index = int(assignment.get("route_index", 0))
            if not route:
                robot.status = "waiting"
                robot.nav_vx = robot.nav_vy = robot.nav_va = 0.0
                robot.task_desc = f"No perimeter route to panel ({row},{col}); holding"
                if idx < len(go2_cmds):
                    go2_cmds[idx] = VelocityCmd()
                continue
        if assignment["phase"] == "retreating":
            cmd = _guard_go2_navigation_cmd(robot, VelocityCmd(), assignment, dt)
            if idx < len(go2_cmds):
                go2_cmds[idx] = cmd
            continue
        if assignment["phase"] == "aligning":
            cmd, aligned = _align_go2_to_target(robot, assignment, dt)
            if idx < len(go2_cmds):
                go2_cmds[idx] = cmd
            if not aligned:
                continue
            if assignment.get("confirm", True):
                assignment["phase"] = "inspecting"
            else:
                robot.status = "idle"
                robot.task_desc = f"Arrived at panel ({row},{col}); camera aligned"
                del ground_check_assignments[robot.name]
            continue
        if assignment["phase"] == "moving" and route_index < len(route):
            robot.status = "moving"
            robot.task_desc = f"Navigating planned route to panel ({row},{col})"
            assignment["phase"] = "moving"
            tx, ty = _route_follow_target(assignment, robot)
            if route and int(assignment.get("route_index", 0)) >= len(route):
                if idx < len(go2_cmds):
                    go2_cmds[idx] = VelocityCmd()
                continue
            cmd = robot.move_to(tx, ty, dt)
            cmd = _guard_go2_navigation_cmd(robot, cmd, assignment, dt)
            _check_go2_stuck(robot, assignment)
            if idx < len(go2_cmds):
                go2_cmds[idx] = cmd
            continue
        if assignment["phase"] == "moving":
            assignment["phase"] = "aligning"
            cmd, aligned = _align_go2_to_target(robot, assignment, dt)
            if idx < len(go2_cmds):
                go2_cmds[idx] = cmd
            if not aligned:
                continue
        if not assignment.get("confirm", True):
            if idx < len(go2_cmds):
                go2_cmds[idx] = VelocityCmd()
            robot.status = "idle"
            robot.camera_active = True
            robot.task_desc = f"Arrived at panel ({row},{col}); standing by"
            del ground_check_assignments[robot.name]
            mission.log(f"{robot.name} arrived at panel ({row},{col})")
            continue
        assignment["phase"] = "inspecting"
        assignment["dwell"] = float(assignment.get("dwell", 0.0)) + dt
        if idx < len(go2_cmds):
            go2_cmds[idx] = VelocityCmd()
        robot.status = "inspecting"
        robot.camera_active = True
        robot.task_desc = f"Confirming panel ({row},{col}) {assignment['dwell']:.1f}s"
        if assignment["dwell"] >= 2.5:
            update_panel_status(row, col, "confirmed_fault", "go2", f"{robot.name} confirmed fault")
            robot.camera_active = False
            robot.status = "idle"
            robot.task_desc = "Ground confirmation complete"
            del ground_check_assignments[robot.name]
            mission.log(f"{robot.name} confirmed anomaly at ({row},{col})")
    return go2_cmds


def update_drone_navigation(dt: float, drone_cmds: list[dict]) -> list[dict]:
    for idx, drone in enumerate(sim.drones):
        row_assignment = drone_row_scan_assignments.get(drone.name)
        if row_assignment:
            panels = row_assignment.get("panels") or []
            panel_index = int(row_assignment.get("index", 0))
            if panel_index >= len(panels):
                drone.status = "hovering"
                drone.camera_active = True
                row = row_assignment.get("row")
                mission.log(f"{drone.name} completed scan" + (f" for row {row}" if row is not None else ""))
                del drone_row_scan_assignments[drone.name]
                if idx < len(drone_cmds):
                    drone_cmds[idx] = {"vx": 0, "vy": 0, "vz": 0, "yaw": drone.pose.yaw, "dist": 0, "dz": 0}
                continue
            row, col = panels[panel_index]
            panel = _panel_by_key(row, col)
            if not panel:
                row_assignment["index"] = panel_index + 1
                continue
            target_z = terrain_height(panel.x, panel.y) + farm.drone_scan_altitude
            cmd = drone.compute_flight(panel.x, panel.y, target_z, dt)
            drone.status = "flying"
            drone.task_desc = f"Scanning panel ({row},{col})"
            drone.camera_active = False
            if cmd["dist"] < 0.8 and cmd["dz"] < 0.5:
                record_drone_scan_result(panel, drone.name, "row scan")
                row_assignment["index"] = panel_index + 1
                row_assignment["inspected"] = int(row_assignment.get("inspected", 0)) + 1
                drone.camera_active = True
                mission.log(f"{drone.name} scanned panel ({row},{col})")
                cmd = {"vx": 0, "vy": 0, "vz": 0, "yaw": drone.pose.yaw, "dist": 0, "dz": 0}
            if idx < len(drone_cmds):
                drone_cmds[idx] = cmd
            continue
        assignment = drone_nav_assignments.get(drone.name)
        if not assignment:
            continue
        row, col = assignment["panel"]
        target = assignment["target"]
        cmd = drone.compute_flight(float(target["x"]), float(target["y"]), float(target["z"]), dt)
        drone.status = "flying"
        drone.task_desc = f"Navigating to panel ({row},{col})"
        drone.camera_active = False
        if cmd["dist"] < 0.8 and cmd["dz"] < 0.5:
            panel = _panel_by_key(row, col)
            if panel:
                record_drone_scan_result(panel, drone.name, "panel navigation")
            drone.status = "hovering"
            drone.task_desc = f"Arrived over panel ({row},{col})"
            drone.camera_active = True
            cmd = {"vx": 0, "vy": 0, "vz": 0, "yaw": drone.pose.yaw, "dist": 0, "dz": 0}
            del drone_nav_assignments[drone.name]
            mission.log(f"{drone.name} arrived over panel ({row},{col})")
        if idx < len(drone_cmds):
            drone_cmds[idx] = cmd
    return drone_cmds


def _enforce_go2_walkable_positions() -> None:
    for idx, robot in enumerate(sim.go2s):
        if _go2_body_walkable(robot.pose.x, robot.pose.y, robot.pose.yaw):
            continue
        safe = _nearest_go2_body_walkable_point(robot.pose.x, robot.pose.y, robot.pose.yaw)
        robot.pose.x = float(safe["x"])
        robot.pose.y = float(safe["y"])
        robot.manual_vx = robot.manual_vy = robot.manual_va = 0.0
        robot.nav_vx = robot.nav_vy = robot.nav_va = 0.0
        robot.task_desc = "Recovered to nearest walkable area"
        assignment = ground_check_assignments.get(robot.name)
        if assignment:
            assignment["phase"] = "retreating"
            assignment["retreat_remaining_m"] = min(0.8, float(path_settings.get("retreat_distance_m", 0.6)))
            assignment["retreat_target"] = safe
def _mark_manual_motion_blocked(robot_name: str, reason: str) -> None:
    meta = manual_motion_tasks.pop(robot_name, None)
    robot = next((g for g in sim.go2s if g.name == robot_name), None)
    if robot:
        robot.manual_vx = robot.manual_vy = robot.manual_va = 0.0
        robot.manual_timer = 0.0
        robot.nav_vx = robot.nav_vy = robot.nav_va = 0.0
        robot.status = "blocked"
        robot.task_desc = reason
    if not meta:
        mission.log(f"{robot_name} motion blocked: {reason}")
        return
    task = next((t for t in task_records if t.id == meta.get("task_id")), None)
    if not task:
        return
    step = next((s for s in task.steps if s.id == meta.get("step_id")), task.current_step())
    task.status = "blocked"
    if step:
        step.status = "blocked"
        step.remaining_duration = max(0.0, step.remaining_duration)
    _append_task_log(task, step, f"{robot_name} blocked: {reason}. Choose Stop or Retry.")
    mission.log(f"{robot_name} task blocked: {reason}")


def guard_manual_go2_motion(dt: float) -> None:
    for idx, robot in enumerate(sim.go2s):
        if robot.name not in manual_motion_tasks:
            continue
        moving = abs(robot.manual_vx) + abs(robot.manual_vy)
        turning_only = moving <= 1e-4 and abs(robot.manual_va) > 1e-4
        if turning_only:
            continue
        if moving <= 1e-4:
            manual_motion_tasks.pop(robot.name, None)
            continue
        checks = []
        speed = math.hypot(robot.manual_vx, robot.manual_vy)
        dir_x, dir_y = robot.manual_vx / speed, robot.manual_vy / speed
        for horizon in (0.35, 0.8, max(1.2, speed * max(dt, 0.1))):
            checks.append((robot.pose.x + dir_x * horizon, robot.pose.y + dir_y * horizon))
        if not _go2_body_walkable(robot.pose.x, robot.pose.y, robot.pose.yaw) or any(
            not _go2_body_walkable(x, y, robot.pose.yaw) for x, y in checks
        ):
            _mark_manual_motion_blocked(robot.name, "Manual motion reached the walkable boundary")


@app.on_event("startup")
async def startup():
    full_reset()
    restore_update_snapshot()
    asyncio.create_task(simulation_loop())


def _has_active_tasks() -> bool:
    return any(task.status in {"queued", "running", "blocked"} for task in task_records)


def _go2_assignments_active() -> bool:
    """True when any Go2 has a navigation, ground-check, or return assignment."""
    return bool(ground_check_assignments or go2_return_assignments or ground_check_queue)


def _automation_can_start_inspection() -> bool:
    if _has_active_tasks():
        return False
    # Mission ends in COMPLETE after a full inspection cycle; allow
    # auto-inspect to start a fresh cycle from either idle state.
    if mission.state not in {State.IDLE, State.COMPLETE}:
        return False
    return not any(
        (
            ground_check_assignments,
            drone_nav_assignments,
            drone_row_scan_assignments,
            go2_return_assignments,
            drone_return_assignments,
            manual_motion_tasks,
        )
    )


async def _dispatch_auto_inspection() -> None:
    try:
        trace = await dispatch_command_text("inspect solar field")
        if trace.ok:
            mission.log("Automatic timed inspection triggered.")
        else:
            mission.log(f"Automatic timed inspection failed: {trace.error or 'parse error'}")
    finally:
        automation_settings["auto_inspect_pending"] = False


def maybe_trigger_auto_inspection() -> None:
    if not automation_settings.get("auto_inspect_enabled", False):
        return
    interval = float(automation_settings.get("auto_inspect_interval_s", 360.0))
    if interval <= 0 or automation_settings.get("auto_inspect_pending"):
        return
    now = sim_now()
    raw_last = automation_settings.get("last_auto_inspect_ts")
    last = float(raw_last) if raw_last is not None else now
    if now - last < interval:
        return
    if not _automation_can_start_inspection():
        return
    automation_settings["last_auto_inspect_ts"] = now
    automation_settings["auto_inspect_pending"] = True
    asyncio.create_task(_dispatch_auto_inspection())


async def simulation_loop():
    global sim_clock_ts
    target = 1.0 / 30.0
    while True:
        t0 = time.perf_counter()
        if sim_paused:
            dt = 0.0
        else:
            dt = target * sim_speed
            sim_clock_ts += dt
        go2_cmds, drone_cmds = update_mission(mission, sim.drones, sim.go2s, dt)
        if _go2_assignments_active():
            rtb_cmds = _background_drone_rtb(dt)
            if rtb_cmds is not None:
                drone_cmds = rtb_cmds
        update_battery_and_redlines(dt)
        go2_cmds = update_ground_confirmations(dt, go2_cmds)
        drone_cmds = update_drone_navigation(dt, drone_cmds)
        drone_cmds = update_drone_returns(dt, drone_cmds)
        guard_manual_go2_motion(dt)

        for g, gc in zip(sim.go2s, go2_cmds):
            g.step(gc, dt)
        for d, c in zip(sim.drones, drone_cmds):
            d.step(c, dt)

        _enforce_go2_walkable_positions()
        reconcile_legacy_panel_statuses()
        mutate_hidden_panel_truth()
        expire_panel_statuses()
        advance_task_executor(dt)
        maybe_trigger_auto_inspection()

        await broadcast(build_envelope())
        await asyncio.sleep(max(0, target - (time.perf_counter() - t0)))


def build_envelope() -> dict:
    t = sim.telemetry()
    disabled_go2 = _disabled_go2_names()
    for robot in t.get("go2s") or []:
        name = robot.get("name")
        robot["available"] = name not in disabled_go2
        robot["disabled"] = name in disabled_go2
    t["farm"] = public_farm_state(farm)
    t["mission"] = {
        "state": mission.state.value,
        "anomaly_queue": mission.anomaly_queue,
        "inspected_list": mission.inspected_list,
        "log": mission.log_lines[-15:],
    }
    t["command_traces"] = [
        {
            "id": tr.id,
            "text": tr.text,
            "source": tr.source,
            "llm_available": tr.llm_available,
            "actions": tr.actions,
            "dispatch_log": tr.dispatch_log,
            "ok": tr.ok,
            "error": tr.error,
            "ts": tr.ts,
            "task_id": tr.task_id,
            "selected_robot": tr.selected_robot,
            "llm_request_summary": tr.llm_request_summary,
            "llm_raw_json": tr.llm_raw_json,
            "normalized_json": tr.normalized_json,
            "validation_errors": tr.validation_errors,
            "generated_tasks": tr.generated_tasks,
        }
        for tr in command_traces[-10:]
    ]
    t["tasks"] = [task.to_dict() for task in task_records[-30:]]
    t["active_tasks"] = [
        task.to_dict() for task in task_records
        if task.status in {"queued", "running", "blocked"}
    ]
    t["go2_routes"] = {
        name: {
            "panel": data.get("panel"),
            "phase": data.get("phase"),
            "route": data.get("route") or [],
            "route_index": data.get("route_index", 0),
            "progress": _assignment_route_progress(data),
            "target_point": data.get("target_point"),
        }
        for name, data in ground_check_assignments.items()
    }
    for name, data in go2_return_assignments.items():
        t["go2_routes"][name] = {
            "panel": None,
            "phase": data.get("phase"),
            "route": data.get("route") or [],
            "route_index": data.get("route_index", 0),
            "progress": _assignment_route_progress(data),
            "target_point": data.get("target"),
        }
    t["go2_walkable_regions"] = _walkable_regions_public()
    ai_settings = public_ai_settings()
    t["settings"] = {
        "path_planning": path_settings,
        "robot_safety": robot_settings,
        "panel_status": panel_settings,
        "automation": automation_settings,
        "ai_parser": ai_settings,
    }
    t["skills"] = SKILL_CATALOG
    t["ai_parser"] = ai_settings
    t["llm_available"] = llm_available()
    t["go2_visual_model"] = public_go2_visual_model()
    t["sim_speed"] = sim_speed
    t["sim_paused"] = sim_paused
    t["sim_time_ts"] = sim_clock_ts
    t["boot_id"] = BOOT_ID
    return t


def restart_system_state() -> None:
    global sim_speed, sim_paused
    os.environ.pop("SOLAR_UPDATE_SNAPSHOT", None)
    try:
        os.remove(UPDATE_SNAPSHOT_PATH)
    except OSError:
        pass
    full_reset()
    command_traces.clear()
    task_records.clear()
    reset_ai_parser_runtime()
    panel_settings["healthy_ttl_s"] = 300.0
    panel_settings["degradation_interval_s"] = 120.0
    automation_settings["auto_inspect_enabled"] = False
    automation_settings["auto_inspect_interval_s"] = 360.0
    automation_settings["last_auto_inspect_ts"] = sim_clock_ts
    automation_settings["auto_inspect_pending"] = False
    robot_settings["disabled_go2_names"] = []
    sim_speed = 1.0
    sim_paused = False
    mission.log_lines = ["System restarted. Awaiting command."]


def _command_from_dict(data: dict[str, Any]) -> SolarSkillCommand:
    return SolarSkillCommand(
        skill=data.get("skill", ""),
        desc=data.get("desc", ""),
        robot=data.get("robot"),
        row=data.get("row"),
        col=data.get("col"),
        panels=[tuple(p) for p in data.get("panels") or [] if isinstance(p, (list, tuple)) and len(p) >= 2],
        params=data.get("params") or {},
    )


def _trace_from_dict(data: dict[str, Any]) -> CommandTrace:
    return CommandTrace(
        id=data.get("id", _trace_id()),
        text=data.get("text", ""),
        source=data.get("source", "none"),
        llm_available=bool(data.get("llm_available", False)),
        actions=data.get("actions") or [],
        dispatch_log=data.get("dispatch_log") or [],
        ok=bool(data.get("ok", False)),
        error=data.get("error", ""),
        ts=float(data.get("ts") or time.time()),
        task_id=data.get("task_id"),
        selected_robot=data.get("selected_robot"),
        llm_request_summary=data.get("llm_request_summary") or {},
        llm_raw_json=data.get("llm_raw_json") or {},
        normalized_json=data.get("normalized_json") or {},
        validation_errors=data.get("validation_errors") or [],
        generated_tasks=data.get("generated_tasks") or [],
    )


def _task_from_dict(data: dict[str, Any]) -> TaskRecord:
    steps: list[TaskStep] = []
    for step_data in data.get("steps") or []:
        steps.append(TaskStep(
            id=step_data.get("id", ""),
            label=step_data.get("label", "Task Step"),
            actions=[_command_from_dict(a) for a in step_data.get("actions") or []],
            status=step_data.get("status", "pending"),
            expected_duration=float(step_data.get("expected_duration") or 0.2),
            remaining_duration=float(step_data.get("remaining_duration") or 0.2),
            started_ts=step_data.get("started_ts"),
            completed_ts=step_data.get("completed_ts"),
            logs=step_data.get("logs") or [],
        ))
    return TaskRecord(
        id=data.get("id", _task_id()),
        text=data.get("text", ""),
        source=data.get("source", "none"),
        trace_id=data.get("trace_id", ""),
        steps=steps,
        status=data.get("status", "done"),
        created_ts=float(data.get("created_ts") or time.time()),
        started_ts=data.get("started_ts"),
        completed_ts=data.get("completed_ts"),
        current_step_index=int(data.get("current_step_index") or 0),
        logs=data.get("logs") or [],
    )


def _runtime_state_snapshot() -> dict[str, Any]:
    return {
        "farm_panels": [
            {
                "row": p.row,
                "col": p.col,
                "inspected": p.inspected,
                "is_defective": p.is_defective,
                "true_is_defective": p.true_is_defective,
                "anomaly_confirmed": p.anomaly_confirmed,
                "anomaly_type": p.anomaly_type,
                "status": p.status,
                "last_update_ts": p.last_update_ts,
                "last_update_source": p.last_update_source,
                "last_update_note": p.last_update_note,
            }
            for p in farm.panels
        ],
        "farm_anomalies_found": farm.anomalies_found,
        "farm_anomalies_inspected": farm.anomalies_inspected,
        "waypoints": [
            [w.reached for w in wp_list]
            for wp_list in farm.waypoints
        ],
        "current_waypoint_indices": farm.current_waypoint_indices,
        "legacy_drone_scan_synced": [list(p) for p in legacy_drone_scan_synced],
        "mission": {
            "state": mission.state.value,
            "anomaly_queue": mission.anomaly_queue,
            "inspected_list": mission.inspected_list,
            "go2_target": mission.go2_target,
        },
        "go2s": [
            {
                "name": g.name,
                "pose": {"x": g.pose.x, "y": g.pose.y, "yaw": g.pose.yaw},
                "battery": g.battery,
                "status": g.status,
                "task_desc": g.task_desc,
                "camera_active": g.camera_active,
                "leg_phase": g.leg_phase,
                "manual_vx": g.manual_vx,
                "manual_vy": g.manual_vy,
                "manual_va": g.manual_va,
                "manual_timer": g.manual_timer,
                "nav_vx": g.nav_vx,
                "nav_vy": g.nav_vy,
                "nav_va": g.nav_va,
            }
            for g in sim.go2s
        ],
        "drones": [
            {
                "name": d.name,
                "pose": {"x": d.pose.x, "y": d.pose.y, "z": d.pose.z, "roll": d.pose.roll, "pitch": d.pose.pitch, "yaw": d.pose.yaw},
                "battery": d.battery,
                "status": d.status,
                "task_desc": d.task_desc,
                "camera_active": d.camera_active,
                "photo_count": d.photo_count,
                "propeller_rpm": d.propeller_rpm,
                "propeller_angle": d.propeller_angle,
            }
            for d in sim.drones
        ],
        "assignments": {
            "ground_check_queue": ground_check_queue,
            "ground_check_assignments": ground_check_assignments,
            "drone_nav_assignments": drone_nav_assignments,
            "drone_row_scan_assignments": drone_row_scan_assignments,
            "go2_return_assignments": go2_return_assignments,
            "drone_return_assignments": drone_return_assignments,
            "manual_motion_tasks": manual_motion_tasks,
        },
        "sim_clock_ts": sim_clock_ts,
        "last_panel_truth_mutation_ts": last_panel_truth_mutation_ts,
    }


def _restore_runtime_state(data: dict[str, Any]) -> None:
    global sim_clock_ts, last_panel_truth_mutation_ts
    if not isinstance(data, dict):
        return
    panel_state = {(p.get("row"), p.get("col")): p for p in data.get("farm_panels") or []}
    for panel in farm.panels:
        saved = panel_state.get((panel.row, panel.col))
        if not saved:
            continue
        panel.inspected = bool(saved.get("inspected", False))
        panel.is_defective = bool(saved.get("is_defective", panel.is_defective))
        panel.true_is_defective = bool(saved.get("true_is_defective", panel.true_is_defective))
        panel.anomaly_confirmed = bool(saved.get("anomaly_confirmed", False))
        panel.anomaly_type = saved.get("anomaly_type", "")
        legacy_status = "unknown"
        if panel.anomaly_confirmed:
            legacy_status = "confirmed_fault"
        elif panel.inspected and panel.is_defective:
            legacy_status = "drone_anomaly"
        elif panel.inspected:
            legacy_status = "healthy"
        panel.status = saved.get("status", legacy_status)
        panel.last_update_ts = float(saved.get("last_update_ts") or 0.0)
        panel.last_update_source = saved.get("last_update_source", "system")
        panel.last_update_note = saved.get("last_update_note", "")
    farm.anomalies_found = [tuple(x) for x in data.get("farm_anomalies_found") or []]
    farm.anomalies_inspected = [tuple(x) for x in data.get("farm_anomalies_inspected") or []]
    for wp_list, states in zip(farm.waypoints, data.get("waypoints") or []):
        for waypoint, reached in zip(wp_list, states):
            waypoint.reached = bool(reached)
    if isinstance(data.get("current_waypoint_indices"), dict):
        farm.current_waypoint_indices = data["current_waypoint_indices"]
    legacy_drone_scan_synced.clear()
    legacy_drone_scan_synced.update(
        tuple(p) for p in data.get("legacy_drone_scan_synced") or []
        if isinstance(p, (list, tuple)) and len(p) >= 2
    )

    mission_data = data.get("mission") or {}
    try:
        mission.state = State(mission_data.get("state", mission.state.value))
    except ValueError:
        pass
    mission.anomaly_queue = [tuple(x) for x in mission_data.get("anomaly_queue") or []]
    mission.inspected_list = [tuple(x) for x in mission_data.get("inspected_list") or []]
    mission.go2_target = tuple(mission_data["go2_target"]) if mission_data.get("go2_target") else None

    for saved in data.get("go2s") or []:
        robot = next((g for g in sim.go2s if g.name == saved.get("name")), None)
        if not robot:
            continue
        pose = saved.get("pose") or {}
        robot.pose.x = float(pose.get("x", robot.pose.x))
        robot.pose.y = float(pose.get("y", robot.pose.y))
        robot.pose.yaw = float(pose.get("yaw", robot.pose.yaw))
        robot.battery = float(saved.get("battery", robot.battery))
        robot.status = saved.get("status", robot.status)
        robot.task_desc = saved.get("task_desc", robot.task_desc)
        robot.camera_active = bool(saved.get("camera_active", robot.camera_active))
        robot.leg_phase = float(saved.get("leg_phase", robot.leg_phase))
        robot.manual_vx = float(saved.get("manual_vx", 0.0))
        robot.manual_vy = float(saved.get("manual_vy", 0.0))
        robot.manual_va = float(saved.get("manual_va", 0.0))
        robot.manual_timer = float(saved.get("manual_timer", 0.0))
        robot.nav_vx = float(saved.get("nav_vx", 0.0))
        robot.nav_vy = float(saved.get("nav_vy", 0.0))
        robot.nav_va = float(saved.get("nav_va", 0.0))
    for saved in data.get("drones") or []:
        drone = next((d for d in sim.drones if d.name == saved.get("name")), None)
        if not drone:
            continue
        pose = saved.get("pose") or {}
        drone.pose.x = float(pose.get("x", drone.pose.x))
        drone.pose.y = float(pose.get("y", drone.pose.y))
        drone.pose.z = float(pose.get("z", drone.pose.z))
        drone.pose.roll = float(pose.get("roll", drone.pose.roll))
        drone.pose.pitch = float(pose.get("pitch", drone.pose.pitch))
        drone.pose.yaw = float(pose.get("yaw", drone.pose.yaw))
        drone.battery = float(saved.get("battery", drone.battery))
        drone.status = saved.get("status", drone.status)
        drone.task_desc = saved.get("task_desc", drone.task_desc)
        drone.camera_active = bool(saved.get("camera_active", drone.camera_active))
        drone.photo_count = int(saved.get("photo_count", drone.photo_count))
        drone.propeller_rpm = float(saved.get("propeller_rpm", drone.propeller_rpm))
        drone.propeller_angle = float(saved.get("propeller_angle", drone.propeller_angle))
    assignments = data.get("assignments") or {}
    ground_check_queue[:] = [tuple(x) for x in assignments.get("ground_check_queue") or []]
    ground_check_assignments.clear(); ground_check_assignments.update(assignments.get("ground_check_assignments") or {})
    drone_nav_assignments.clear(); drone_nav_assignments.update(assignments.get("drone_nav_assignments") or {})
    drone_row_scan_assignments.clear(); drone_row_scan_assignments.update(assignments.get("drone_row_scan_assignments") or {})
    go2_return_assignments.clear(); go2_return_assignments.update(assignments.get("go2_return_assignments") or {})
    drone_return_assignments.clear(); drone_return_assignments.update(assignments.get("drone_return_assignments") or {})
    manual_motion_tasks.clear(); manual_motion_tasks.update(assignments.get("manual_motion_tasks") or {})
    sim_clock_ts = float(data.get("sim_clock_ts") or time.time())
    last_panel_truth_mutation_ts = float(data.get("last_panel_truth_mutation_ts") or time.time())


def save_update_snapshot() -> None:
    now = time.time()
    snapshot_tasks = []
    for task in task_records:
        data = task.to_dict()
        if data["status"] in {"queued", "running", "blocked"}:
            data["logs"] = [*data.get("logs", []), f"{time.strftime('%H:%M:%S')} Task preserved through update."]
        snapshot_tasks.append(data)
    payload = {
        "kind": "system_update",
        "created_ts": now,
        "command_traces": [
            {
                "id": tr.id,
                "text": tr.text,
                "source": tr.source,
                "llm_available": tr.llm_available,
                "actions": tr.actions,
                "dispatch_log": tr.dispatch_log,
                "ok": tr.ok,
                "error": tr.error,
                "ts": tr.ts,
                "task_id": tr.task_id,
                "selected_robot": tr.selected_robot,
                "llm_request_summary": tr.llm_request_summary,
                "llm_raw_json": tr.llm_raw_json,
                "normalized_json": tr.normalized_json,
                "validation_errors": tr.validation_errors,
                "generated_tasks": tr.generated_tasks,
            }
            for tr in command_traces
        ],
        "task_records": snapshot_tasks,
        "runtime_state": _runtime_state_snapshot(),
        "mission_log_lines": mission.log_lines[-50:],
        "path_settings": path_settings,
        "robot_settings": robot_settings,
        "panel_settings": panel_settings,
        "automation_settings": automation_settings,
        "ai_settings": ai_settings_snapshot(),
        "sim_speed": sim_speed,
    }
    with open(UPDATE_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.environ["SOLAR_UPDATE_SNAPSHOT"] = UPDATE_SNAPSHOT_PATH


def restore_update_snapshot() -> None:
    path = os.environ.pop("SOLAR_UPDATE_SNAPSHOT", "")
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("kind") != "system_update":
            return
        command_traces[:] = [_trace_from_dict(t) for t in payload.get("command_traces") or []]
        task_records[:] = [_task_from_dict(t) for t in payload.get("task_records") or []]
        if isinstance(payload.get("runtime_state"), dict):
            _restore_runtime_state(payload["runtime_state"])
        mission.log_lines = payload.get("mission_log_lines") or mission.log_lines
        if isinstance(payload.get("path_settings"), dict):
            path_settings.update(payload["path_settings"])
        if isinstance(payload.get("robot_settings"), dict):
            robot_settings.update(payload["robot_settings"])
        if isinstance(payload.get("panel_settings"), dict):
            panel_settings.update(payload["panel_settings"])
        if isinstance(payload.get("automation_settings"), dict):
            automation_settings.update(payload["automation_settings"])
            automation_settings.setdefault("auto_inspect_enabled", False)
            automation_settings["auto_inspect_pending"] = False
        if isinstance(payload.get("ai_settings"), dict):
            restore_ai_settings_snapshot(payload["ai_settings"])
        global sim_speed
        sim_speed = float(payload.get("sim_speed") or sim_speed)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def restart_process_later(delay: float = 0.35) -> None:
    await asyncio.sleep(delay)
    os.execv(sys.executable, [sys.executable, *sys.argv])


async def broadcast(packet: dict):
    if not clients:
        return
    msg = json.dumps(packet, default=str)
    stale = []
    for c in list(clients):
        try:
            await c.send_text(msg)
        except Exception:
            stale.append(c)
    for c in stale:
        clients.discard(c)


def dispatch_skill(cmd: SolarSkillCommand, trace: CommandTrace) -> bool:
    skill = cmd.skill
    target = cmd.robot or ("Go2-01" if skill in {
        "move_forward", "move_backward", "turn_left", "turn_right", "go2_inspect", "inspect_panel", "go2_navigate", "go2_confirm_anomalies"
    } else "Go2 Fleet" if skill == "go2_return_base" else "Drone-01" if skill in {"drone_navigate", "drone_scan_row", "drone_scan_panels"} else "Fleet")
    trace.dispatch_log.append(f"{target} accepted task: {skill} — {cmd.desc}")

    if skill == "stop":
        full_reset()
        mission.state = State.IDLE
        mission.log("Mission aborted. All units returning to standby.")
        return True

    if skill == "reset":
        full_reset()
        trace.dispatch_log.append("Simulation reset complete.")
        return True

    if skill == "idle":
        mission.log("Standby.")
        return True

    if skill == "inspect_solar_field":
        reset_inspection_state_preserve_fleet()
        mission.state = State.TAKEOFF
        mission.log("Starting full solar field inspection with 5 drones.")
        return True

    if skill == "drone_return":
        active_drones = [d for d in sim.drones if d.status not in {"idle", "landed"} or not _drone_near_base(d)]
        for drone in active_drones:
            start_drone_return_base(drone.name, "Returning after inspection")
        if mission.state in (State.TAKEOFF, State.SCANNING):
            mission.state = State.RTB
        if active_drones:
            mission.log(f"Recall: {len(active_drones)} drone(s) returning to base.")
        else:
            mission.log("Drones already on ground or idle.")
        return True

    if skill == "go2_return_base":
        target_names: list[str]
        if cmd.robot and cmd.robot not in {"auto", "Fleet", "Go2 Fleet"}:
            target_names = [cmd.robot]
        else:
            target_names = [g.name for g in sim.go2s if not _go2_near_base(g) or g.status not in {"idle", "landed"}]
        if not target_names:
            mission.log("Go2 units already at base or idle.")
            return True
        ok_count = 0
        for name in target_names:
            robot = next((g for g in sim.go2s if g.name == name), None)
            if not robot:
                continue
            robot.manual_vx = robot.manual_vy = robot.manual_va = 0.0
            robot.manual_timer = 0.0
            if start_go2_return_base(name, "Go2 return base skill"):
                ok_count += 1
        mission.log(f"Recall: {ok_count} Go2 unit(s) returning to base.")
        return ok_count > 0

    if skill == "inspect_panel":
        if cmd.row is None or cmd.col is None:
            trace.dispatch_log.append("Missing row/col for inspect_panel.")
            return False
        if (cmd.row, cmd.col) not in mission.anomaly_queue:
            mission.anomaly_queue.append((cmd.row, cmd.col))
        mission.log(f"Manual inspection: panel ({cmd.row},{cmd.col})")
        start_ground_confirmation()
        return True

    if skill == "inspect_row":
        if cmd.row is None:
            trace.dispatch_log.append("Missing row for inspect_row.")
            return False
        if not start_drone_row_scan("auto", cmd.row):
            trace.dispatch_log.append(f"Could not start row scan for row {cmd.row}.")
            return False
        return True

    if skill == "drone_scan_row":
        if cmd.row is None:
            trace.dispatch_log.append("Missing row for drone_scan_row.")
            return False
        if not start_drone_row_scan(cmd.robot, cmd.row):
            trace.dispatch_log.append(f"Could not start drone scan for row {cmd.row}.")
            return False
        return True

    if skill == "drone_scan_panels":
        panels = cmd.panels or ([(cmd.row, cmd.col)] if cmd.row is not None and cmd.col is not None else [])
        if not panels:
            trace.dispatch_log.append("Missing panels for drone_scan_panels.")
            return False
        if not start_drone_panel_scan(cmd.robot, panels):
            trace.dispatch_log.append("Could not start drone panel scan.")
            return False
        return True

    if skill == "go2_inspect":
        pending = _pending_ground_panels()
        if pending:
            start_ground_confirmation()
            mission.log("Go2 fleet dispatched for ground inspection.")
        else:
            mission.log("No anomalies in queue for Go2 inspection.")
        return True

    if skill == "go2_confirm_anomalies":
        panels = cmd.panels or None
        return queue_go2_confirm_anomalies(row=cmd.row, panels=panels)

    if skill == "go2_navigate":
        if cmd.row is None or cmd.col is None:
            trace.dispatch_log.append("Missing row/col for go2_navigate.")
            return False
        target = cmd.robot or "Go2-01"
        if not start_go2_navigation(target, cmd.row, cmd.col, confirm=False):
            trace.dispatch_log.append(f"Could not navigate {target} to panel ({cmd.row},{cmd.col}).")
            return False
        return True

    if skill == "drone_navigate":
        if cmd.row is None or cmd.col is None:
            trace.dispatch_log.append("Missing row/col for drone_navigate.")
            return False
        target = cmd.robot or "Drone-01"
        if not start_drone_navigation(target, cmd.row, cmd.col):
            trace.dispatch_log.append(f"Could not navigate {target} to panel ({cmd.row},{cmd.col}).")
            return False
        return True

    go2_motion = {
        "move_forward": "forward",
        "move_backward": "backward",
        "turn_left": "left",
        "turn_right": "right",
    }
    if skill in go2_motion:
        if skill in {"move_forward", "move_backward"}:
            distance_m = float(cmd.params.get("distance_m") or 1.0)
            duration = distance_m / 0.3
            _dispatch_go2_motion(target, go2_motion[skill], duration)
            mission.log(f"{target} accepted task: {skill.replace('_', ' ')} {distance_m:g}m")
        else:
            angle_deg = float(cmd.params.get("angle_deg") or 90.0)
            duration = (angle_deg * 3.141592653589793 / 180.0) / 0.8
            _dispatch_go2_motion(target, go2_motion[skill], duration)
            mission.log(f"{target} accepted task: {skill.replace('_', ' ')} {angle_deg:g}deg")
        return True

    trace.dispatch_log.append(f"Unknown or unsupported skill: {skill}")
    return False


def _dispatch_go2_motion(target: str, direction: str, duration: float) -> None:
    robot = next((g for g in sim.go2s if g.name == target), None)
    if not robot:
        return

    speed = 0.3
    turn_rate = 0.8
    if direction == "forward":
        robot.manual_vx = speed * math.cos(robot.pose.yaw)
        robot.manual_vy = speed * math.sin(robot.pose.yaw)
        robot.manual_va = 0.0
    elif direction == "backward":
        robot.manual_vx = -speed * math.cos(robot.pose.yaw)
        robot.manual_vy = -speed * math.sin(robot.pose.yaw)
        robot.manual_va = 0.0
    elif direction == "left":
        robot.manual_vx = 0.0
        robot.manual_vy = 0.0
        robot.manual_va = turn_rate
    elif direction == "right":
        robot.manual_vx = 0.0
        robot.manual_vy = 0.0
        robot.manual_va = -turn_rate
    else:
        robot.manual_vx = robot.manual_vy = robot.manual_va = 0.0
    robot.manual_timer = max(0.2, duration)
    robot.status = "moving"
    robot.task_desc = f"Moving {direction}"


def _split_task_clauses(raw: str) -> list[str]:
    primary = [
        c.strip()
        for c in re.split(r"\b(?:and then|then|next|再|然后)\b", raw, flags=re.IGNORECASE)
        if c.strip()
    ]
    clauses: list[str] = []
    for clause in primary or [raw.strip()]:
        comma_parts = [c.strip(" ,，;；") for c in re.split(r"\s*[,，;；]\s*", clause) if c.strip(" ,，;；")]
        if len(comma_parts) > 1 and all(_looks_like_task_clause(part) for part in comma_parts):
            clauses.extend(comma_parts)
        else:
            clauses.append(clause.strip(" ,，;；"))
    return clauses or [raw.strip()]


def _looks_like_task_clause(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False
    patterns = (
        "move ", "go ", "walk ", "turn ", "rotate ", "forward", "backward", "reverse",
        "left", "right", "inspect ", "check ", "scan ", "patrol ", "drone return",
        "return home", "recall", "rtl", "stop", "reset",
        "standby", "idle", "go2 inspect", "dog inspect", "ground check", "navigate", "send ",
        "dispatch ", "fly to", "move to panel", "检查", "巡检", "看一下",
    )
    if any(p in t for p in patterns):
        return True
    return bool(re.search(r"\b(?:go2|drone)-\d{2}\b.*\b(?:move|go|walk|turn|rotate|inspect|navigate|send)\b", t))


def _apply_selected_robot(commands: list[SolarSkillCommand], selected_robot: Optional[str]) -> None:
    if not selected_robot:
        return
    for command in commands:
        if command.robot is not None:
            continue
        if selected_robot.startswith("Go2-") and command.skill in {
            "move_forward", "move_backward", "turn_left", "turn_right", "go2_inspect", "inspect_panel", "go2_navigate", "go2_confirm_anomalies", "go2_return_base"
        }:
            command.robot = selected_robot
        elif selected_robot.startswith("Drone-") and command.skill in {"drone_navigate", "drone_scan_row", "drone_scan_panels"}:
            command.robot = selected_robot


def _robot_mentions(text: str) -> list[str]:
    found: list[str] = []
    for m in re.finditer(r"\b(drone-\d{2}|go2-\d{2})\b", text, re.IGNORECASE):
        parts = m.group(1).split("-")
        robot = f"{parts[0].title()}-{parts[1]}"
        if robot not in found:
            found.append(robot)
    return found


def _skill_accepts_robot(skill: str, robot: str) -> bool:
    if robot.startswith("Go2-"):
        return skill in {"move_forward", "move_backward", "turn_left", "turn_right", "go2_inspect", "inspect_panel", "go2_navigate", "go2_confirm_anomalies", "go2_return_base"}
    if robot.startswith("Drone-"):
        return skill in {"drone_navigate", "drone_scan_row", "drone_scan_panels"}
    return False


def _apply_inherited_robots(
    commands: list[SolarSkillCommand],
    inherited_robots: list[str],
) -> list[SolarSkillCommand]:
    if not inherited_robots:
        return commands
    expanded: list[SolarSkillCommand] = []
    for command in commands:
        if command.robot is not None:
            expanded.append(command)
            continue
        targets = [robot for robot in inherited_robots if _skill_accepts_robot(command.skill, robot)]
        if targets:
            expanded.extend(replace(command, robot=target) for target in targets)
        else:
            expanded.append(command)
    return expanded


def _step_targets(actions: list[SolarSkillCommand]) -> list[str]:
    targets: list[str] = []
    for action in actions:
        target = action.robot or _default_target(action.skill)
        if target not in targets:
            targets.append(target)
    return targets


def _default_target(skill: str) -> str:
    if skill in {"drone_navigate", "drone_scan_row", "drone_scan_panels"}:
        return "Drone-01"
    if skill == "go2_return_base":
        return "Go2 Fleet"
    if skill in {"move_forward", "move_backward", "turn_left", "turn_right", "go2_inspect", "inspect_panel", "go2_navigate", "go2_confirm_anomalies"}:
        return "Go2-01"
    return "Fleet"


def _estimate_action_duration(cmd: SolarSkillCommand) -> float:
    if cmd.skill in {"move_forward", "move_backward"}:
        return float(cmd.params.get("distance_m") or 1.0) / 0.3
    if cmd.skill in {"turn_left", "turn_right"}:
        angle_deg = float(cmd.params.get("angle_deg") or 90.0)
        return (angle_deg * math.pi / 180.0) / 0.8
    if cmd.skill in {"inspect_solar_field", "inspect_row", "drone_return", "go2_return_base", "go2_inspect", "go2_confirm_anomalies", "inspect_panel", "go2_navigate", "drone_navigate", "drone_scan_row", "drone_scan_panels"}:
        # Long-running steps are completed from mission state/progress.
        return 0.0
    return 0.2


def _is_long_running_step(step: TaskStep) -> bool:
    return any(a.skill in {"inspect_solar_field", "inspect_row", "drone_return", "go2_return_base", "go2_inspect", "go2_confirm_anomalies", "inspect_panel", "go2_navigate", "drone_navigate", "drone_scan_row", "drone_scan_panels"} for a in step.actions)


def _step_label(actions: list[SolarSkillCommand], index: int) -> str:
    if not actions:
        return f"Step {index + 1}"
    if len(actions) == 1:
        return actions[0].desc or actions[0].skill
    skills = ", ".join(a.desc or a.skill for a in actions)
    return f"Parallel: {skills}"


def _expand_composite_steps(steps: list[TaskStep]) -> list[TaskStep]:
    if len(steps) != 1:
        return steps
    actions = steps[0].actions
    if len(actions) == 1 and actions[0].skill == "inspect_solar_field":
        return [
            TaskStep(
                id="",
                label="Drone aerial inspection",
                actions=[actions[0]],
            ),
            TaskStep(
                id="",
                label="Go2 ground confirmation",
                actions=[SolarSkillCommand(
                    "go2_inspect",
                    "Go2 ground confirmation",
                    robot="Go2 Fleet",
                )],
            ),
        ]
    if len(actions) == 1 and actions[0].skill == "inspect_row":
        row = actions[0].row
        return [
            TaskStep(
                id="",
                label=f"Drone scan row {row}",
                actions=[SolarSkillCommand(
                    "drone_scan_row",
                    f"Drone scan row {row}",
                    robot="auto",
                    row=row,
                )],
            ),
            TaskStep(
                id="",
                label=f"Drone return and Go2 confirm row {row}",
                actions=[
                    SolarSkillCommand(
                        "drone_return",
                        "Return drones to base",
                    ),
                    SolarSkillCommand(
                        "go2_confirm_anomalies",
                        f"Go2 confirm anomalies from row {row}",
                        robot="auto",
                        row=row,
                    ),
                ],
            ),
        ]
    return steps


def _steps_from_normalized_json(data: dict[str, Any]) -> list[TaskStep]:
    steps: list[TaskStep] = []
    for task in data.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        for step in task.get("steps") or []:
            if not isinstance(step, dict):
                continue
            actions = [_command_from_dict(a) for a in step.get("actions") or [] if isinstance(a, dict)]
            if not actions:
                continue
            steps.append(TaskStep(
                id="",
                label=str(step.get("label") or _step_label(actions, len(steps))),
                actions=actions,
            ))
    return steps


def _validate_commands(commands: list[SolarSkillCommand]) -> list[str]:
    errors: list[str] = []
    max_row = max((p.row for p in farm.panels), default=-1)
    max_col = max((p.col for p in farm.panels), default=-1)
    for command in commands:
        if command.row is not None and not (0 <= command.row <= max_row):
            errors.append(f"{command.skill}: row {command.row} out of range 0..{max_row}")
        if command.col is not None and not (0 <= command.col <= max_col):
            errors.append(f"{command.skill}: col {command.col} out of range 0..{max_col}")
        for row, col in command.panels:
            if not (0 <= row <= max_row and 0 <= col <= max_col):
                errors.append(f"{command.skill}: panel ({row},{col}) out of range")
        if command.robot and command.robot != "auto":
            names = {d.name for d in sim.drones} | {g.name for g in sim.go2s} | {"Fleet", "Go2 Fleet"}
            if command.robot not in names:
                errors.append(f"{command.skill}: unknown robot {command.robot}")
    return errors


def _append_task_log(task: TaskRecord, step: Optional[TaskStep], line: str) -> None:
    ts = time.strftime("%H:%M:%S")
    msg = f"{ts} {line}"
    task.logs.append(msg)
    task.logs = task.logs[-100:]
    if step is not None:
        step.logs.append(msg)
        step.logs = step.logs[-50:]


def _start_step(task: TaskRecord, step: TaskStep) -> None:
    now = time.time()
    if task.status == "queued":
        task.status = "running"
        task.started_ts = now
    step.status = "active"
    step.started_ts = now
    durations = [_estimate_action_duration(action) for action in step.actions]
    step.expected_duration = max(durations) if durations else 0.2
    step.remaining_duration = step.expected_duration
    _append_task_log(task, step, f"Step {task.current_step_index + 1} started: {step.label}")
    trace = next((tr for tr in command_traces if tr.id == task.trace_id), None)
    if trace is None:
        trace = CommandTrace(id=task.trace_id, text=task.text, source=task.source)
    trace.dispatch_log.append(f"{task.id} step {task.current_step_index + 1} started: {step.label}")
    for action in step.actions:
        before = len(trace.dispatch_log)
        ok = dispatch_skill(action, trace)
        new_lines = trace.dispatch_log[before:]
        for line in new_lines:
            _append_task_log(task, step, line)
        if not ok:
            step.status = "failed"
            task.status = "failed"
            task.completed_ts = time.time()
            _append_task_log(task, step, f"Step failed: {action.skill}")
        elif action.skill in {"move_forward", "move_backward", "turn_left", "turn_right"}:
            target = action.robot or _default_target(action.skill)
            manual_motion_tasks[target] = {
                "task_id": task.id,
                "step_id": step.id,
                "skill": action.skill,
                "action": action.to_dict(),
            }


def _long_running_step_done(step: TaskStep) -> bool:
    skills = {a.skill for a in step.actions}
    if "inspect_solar_field" in skills:
        return bool(farm.panels) and all(p.inspected for p in farm.panels)
    if "drone_return" in skills:
        drones_home = not drone_return_assignments and all(
            d.status in {"idle", "landed"} and _drone_near_base(d) for d in sim.drones
        )
        if "go2_confirm_anomalies" in skills or "go2_inspect" in skills or "inspect_panel" in skills:
            go2_done = (
                not mission.anomaly_queue
                and not ground_check_queue
                and not ground_check_assignments
                and not any(p.status == "drone_anomaly" for p in farm.panels)
            )
            return drones_home and go2_done
        return drones_home
    if "go2_return_base" in skills:
        for action in step.actions:
            if action.skill != "go2_return_base":
                continue
            target = action.robot or _default_target(action.skill)
            if target in {"auto", "Fleet", "Go2 Fleet"}:
                if go2_return_assignments or not all(_go2_near_base(g) for g in sim.go2s):
                    return False
                continue
            robot = next((g for g in sim.go2s if g.name == target), None)
            if not robot or target in go2_return_assignments or not _go2_near_base(robot):
                return False
        return True
    if "drone_scan_row" in skills or "drone_scan_panels" in skills:
        if "drone_scan_panels" in skills and drone_row_scan_assignments:
            return False
        return all(
            not (
                ((action.robot or _default_target(action.skill)) != "auto"
                 and (action.robot or _default_target(action.skill)) in drone_row_scan_assignments)
                or any(
                    assignment.get("row") == action.row
                    for assignment in drone_row_scan_assignments.values()
                    if action.skill == "drone_scan_row"
                )
            )
            for action in step.actions
            if action.skill in {"drone_scan_row", "drone_scan_panels"}
        )
    if "go2_inspect" in skills or "go2_confirm_anomalies" in skills or "inspect_panel" in skills:
        return (
            not mission.anomaly_queue
            and mission.go2_target is None
            and not ground_check_queue
            and not ground_check_assignments
            and not any(p.status == "drone_anomaly" for p in farm.panels)
        )
    if "go2_navigate" in skills:
        return all(
            not (
                (action.robot or _default_target(action.skill)) in ground_check_assignments
                and ground_check_assignments[action.robot or _default_target(action.skill)].get("panel") == (action.row, action.col)
            )
            for action in step.actions
            if action.skill == "go2_navigate"
        )
    if "drone_navigate" in skills:
        return all(
            not (
                (action.robot or _default_target(action.skill)) in drone_nav_assignments
                and drone_nav_assignments[action.robot or _default_target(action.skill)].get("panel") == (action.row, action.col)
            )
            for action in step.actions
            if action.skill == "drone_navigate"
        )
    return False


def _refresh_long_running_progress(step: TaskStep) -> None:
    skills = {a.skill for a in step.actions}
    if "inspect_solar_field" in skills and farm.panels:
        inspected = sum(1 for p in farm.panels if p.inspected)
        scan_progress = inspected / len(farm.panels)
        step.expected_duration = 1.0
        step.remaining_duration = max(0.0, 1.0 - scan_progress)
    elif "drone_return" in skills:
        drones_home = sum(1 for d in sim.drones if d.status in {"idle", "landed"} and _drone_near_base(d))
        drone_progress = drones_home / (len(sim.drones) or 1)
        if "go2_confirm_anomalies" in skills or "go2_inspect" in skills or "inspect_panel" in skills:
            confirmed, total = _ground_confirmation_counts()
            go2_progress = 1.0 if total == 0 else confirmed / total
            progress = (drone_progress + go2_progress) / 2.0
        else:
            progress = drone_progress
        step.expected_duration = 1.0
        step.remaining_duration = max(0.0, 1.0 - progress)
    elif "go2_return_base" in skills:
        actions = [a for a in step.actions if a.skill == "go2_return_base"]
        if any((a.robot or _default_target(a.skill)) in {"auto", "Fleet", "Go2 Fleet"} for a in actions):
            total = len(sim.go2s) or 1
            home = sum(1 for g in sim.go2s if _go2_near_base(g) and g.name not in go2_return_assignments)
        else:
            names = [a.robot or _default_target(a.skill) for a in actions]
            total = len(names) or 1
            home = sum(1 for name in names for g in sim.go2s if g.name == name and _go2_near_base(g) and name not in go2_return_assignments)
        step.expected_duration = 1.0
        step.remaining_duration = max(0.0, 1.0 - home / total)
    elif "drone_scan_row" in skills or "drone_scan_panels" in skills:
        progress_values: list[float] = []
        for action in step.actions:
            if action.skill not in {"drone_scan_row", "drone_scan_panels"}:
                continue
            assignment = None
            target = action.robot if action.robot and action.robot != "auto" else None
            if target:
                assignment = drone_row_scan_assignments.get(target)
            elif action.skill == "drone_scan_row":
                assignment = next((a for a in drone_row_scan_assignments.values() if a.get("row") == action.row), None)
            else:
                assignment = next(iter(drone_row_scan_assignments.values()), None)
            if assignment:
                total = len(assignment.get("panels") or []) or 1
                progress_values.append(min(1.0, int(assignment.get("inspected", 0)) / total))
            else:
                progress_values.append(1.0)
        progress = sum(progress_values) / (len(progress_values) or 1)
        step.expected_duration = 1.0
        step.remaining_duration = max(0.0, 1.0 - progress)
    elif "go2_inspect" in skills or "go2_confirm_anomalies" in skills:
        remaining = len(ground_check_queue) + len(ground_check_assignments)
        if not hasattr(step, '_confirm_initial'):
            step._confirm_initial = remaining
        initial = max(step._confirm_initial, remaining)
        confirmed = max(0, initial - remaining)
        step.expected_duration = 1.0
        step.remaining_duration = 0.0 if initial == 0 else max(0.0, 1.0 - confirmed / initial)
    elif "inspect_panel" in skills:
        row_col = {(a.row, a.col) for a in step.actions if a.row is not None and a.col is not None}
        total = len(row_col) or 1
        confirmed = sum(
            1 for panel in farm.panels
            if (panel.row, panel.col) in row_col and panel.anomaly_confirmed
        )
        step.expected_duration = 1.0
        step.remaining_duration = max(0.0, 1.0 - confirmed / total)
    elif "go2_navigate" in skills:
        progresses: list[float] = []
        for action in step.actions:
            if action.skill != "go2_navigate":
                continue
            target = action.robot or _default_target(action.skill)
            assignment = ground_check_assignments.get(target)
            progresses.append(_assignment_route_progress(assignment) if assignment else 1.0)
        total = len(progresses) or 1
        progress = sum(progresses) / total if progresses else 1.0
        step.expected_duration = 1.0
        step.remaining_duration = max(0.0, 1.0 - progress)
    elif "drone_navigate" in skills:
        active = sum(1 for a in step.actions if a.skill == "drone_navigate" and (a.robot or _default_target(a.skill)) in drone_nav_assignments)
        total = sum(1 for a in step.actions if a.skill == "drone_navigate") or 1
        step.expected_duration = 1.0
        step.remaining_duration = active / total


def _complete_step(task: TaskRecord, step: TaskStep) -> None:
    step.status = "done"
    step.remaining_duration = 0.0
    step.completed_ts = time.time()
    _append_task_log(task, step, f"Step {task.current_step_index + 1} completed: {step.label}")
    task.current_step_index += 1
    if task.current_step_index >= len(task.steps):
        task.status = "done"
        task.completed_ts = time.time()
        _append_task_log(task, None, "Task completed.")


def advance_task_executor(dt: float) -> None:
    for task in task_records:
        if task.status == "blocked":
            continue
        if task.status not in {"queued", "running"}:
            continue
        step = task.current_step()
        if not step:
            task.status = "done"
            task.completed_ts = time.time()
            continue
        if step.status == "pending":
            _start_step(task, step)
            if task.status == "failed":
                continue
        if step.status != "active":
            continue
        if _is_long_running_step(step):
            _refresh_long_running_progress(step)
            if _long_running_step_done(step):
                _complete_step(task, step)
        else:
            step.remaining_duration -= dt
            if step.remaining_duration <= 0:
                _complete_step(task, step)


async def dispatch_command_text(raw: str, selected_robot: Optional[str] = None) -> CommandTrace:
    trace = CommandTrace(id=_trace_id(), text=raw, llm_available=llm_available(), selected_robot=selected_robot)
    clauses = _split_task_clauses(raw)
    source = "none"
    steps: list[TaskStep] = []
    all_commands: list[SolarSkillCommand] = []
    failed_clauses: list[str] = []
    inherited_robots: list[str] = []
    for index, clause in enumerate(clauses):
        parse_result = await parse_command_async(clause)
        commands, clause_source = parse_result.commands, parse_result.source
        if clause_source != "none":
            source = clause_source if source == "none" else source
        if parse_result.raw_json:
            trace.llm_raw_json = parse_result.raw_json
            trace.normalized_json = parse_result.normalized_json
            trace.validation_errors.extend(parse_result.validation_errors)
            trace.llm_request_summary = parse_result.llm_request_summary
        if clause_source == "llm" and parse_result.normalized_json:
            llm_steps = _steps_from_normalized_json(parse_result.normalized_json)
            for llm_step in llm_steps:
                explicit_robots = [a.robot for a in llm_step.actions if a.robot and a.robot != "auto"]
                if explicit_robots:
                    inherited_robots = list(dict.fromkeys(explicit_robots))
                llm_step.actions = _apply_inherited_robots(llm_step.actions, inherited_robots)
                _apply_selected_robot(llm_step.actions, selected_robot)
                validation_errors = _validate_commands(llm_step.actions)
                if validation_errors:
                    trace.validation_errors.extend(validation_errors)
                    failed_clauses.append(clause)
                    continue
                all_commands.extend(llm_step.actions)
                steps.append(llm_step)
            if llm_steps:
                continue
        explicit_robots = _robot_mentions(clause)
        if explicit_robots:
            inherited_robots = explicit_robots
        commands = _apply_inherited_robots(commands, inherited_robots)
        _apply_selected_robot(commands, selected_robot)
        if not commands:
            failed_clauses.append(clause)
            continue
        validation_errors = _validate_commands(commands)
        if validation_errors:
            trace.validation_errors.extend(validation_errors)
            failed_clauses.append(clause)
            continue
        all_commands.extend(commands)
        step = TaskStep(
            id="",
            label=_step_label(commands, index),
            actions=commands,
        )
        steps.append(step)
    commands = all_commands
    steps = _expand_composite_steps(steps)
    all_task_actions = [action for step in steps for action in step.actions]
    trace.source = source
    trace.actions = [c.to_dict() for c in all_task_actions]
    trace.generated_tasks = [{"label": step.label, "actions": [a.to_dict() for a in step.actions]} for step in steps]

    if failed_clauses:
        trace.ok = False
        detail = "; ".join(trace.validation_errors[-5:]) if trace.validation_errors else "; ".join(failed_clauses)
        trace.error = "Could not parse task step(s): " + detail
        mission.log(f"Unparsed command: {raw}")
        command_traces.append(trace)
        command_traces[:] = command_traces[-20:]
        return trace

    trace.ok = True
    task = TaskRecord(id=_task_id(), text=raw, source=source, trace_id=trace.id, steps=steps)
    for index, step in enumerate(task.steps):
        step.id = _step_id(task.id, index)
    trace.task_id = task.id
    trace.dispatch_log.append(f"{task.id} queued with {len(task.steps)} step(s).")
    task.logs.append(f"{time.strftime('%H:%M:%S')} Task queued from command: {raw}")
    task_records.append(task)
    task_records[:] = task_records[-50:]
    command_traces.append(trace)
    command_traces[:] = command_traces[-20:]
    return trace


@app.get("/")
async def index():
    with open(os.path.join(ROOT_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), headers={"Cache-Control": "no-store"})


@app.get("/three.min.js")
async def threejs():
    with open(os.path.join(ROOT_DIR, "three.min.js"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), media_type="application/javascript")


@app.get("/GLTFLoader.js")
async def gltf_loader():
    with open(os.path.join(ROOT_DIR, "GLTFLoader.js"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), media_type="application/javascript")


@app.get("/go2_visual.js")
async def go2_visual_js():
    with open(os.path.join(ROOT_DIR, "go2_visual.js"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), media_type="application/javascript")


@app.get("/ColladaLoader.js")
async def collada_loader():
    with open(os.path.join(ROOT_DIR, "ColladaLoader.js"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), media_type="application/javascript")


@app.get("/urdf-loader.min.js")
async def urdf_loader():
    with open(os.path.join(ROOT_DIR, "urdf-loader.min.js"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), media_type="application/javascript")


@app.post("/api/ai-command")
async def ai_command(req: dict):
    text = req.get("text", "")
    if not text:
        return {"ok": False, "error": "empty command"}
    selected_robot = req.get("robot")
    trace = await dispatch_command_text(text, selected_robot=selected_robot)
    return {
        "ok": trace.ok,
        "text": trace.text,
        "source": trace.source,
        "llm_available": trace.llm_available,
        "parsed_json": trace.normalized_json or {"actions": trace.actions},
        "actions": trace.actions,
        "dispatch_log": trace.dispatch_log,
        "error": trace.error,
        "trace_id": trace.id,
        "selected_robot": trace.selected_robot,
        "llm_request_summary": trace.llm_request_summary,
        "llm_raw_json": trace.llm_raw_json,
        "normalized_json": trace.normalized_json,
        "validation_errors": trace.validation_errors,
        "generated_tasks": trace.generated_tasks,
    }


@app.post("/api/sim-speed")
async def set_sim_speed(req: dict):
    global sim_speed
    speed = float(req.get("speed", 1.0))
    if speed not in (1.0, 2.0, 4.0):
        return {"ok": False, "error": "speed must be one of 1, 2, 4", "sim_speed": sim_speed}
    sim_speed = speed
    asyncio.create_task(broadcast(build_envelope()))
    return {"ok": True, "sim_speed": sim_speed}


@app.post("/api/sim-pause")
async def toggle_sim_pause(req: dict):
    global sim_paused
    paused = bool(req.get("paused", not sim_paused))
    sim_paused = paused
    asyncio.create_task(broadcast(build_envelope()))
    return {"ok": True, "sim_paused": sim_paused}


@app.post("/api/settings")
async def update_settings(req: dict):
    if "path_planning" in req or "mode" in req:
        pp = req.get("path_planning") or req
        mode = str(pp.get("mode", path_settings["mode"]))
        if mode not in {"dynamic", "static"}:
            return {"ok": False, "error": "path planning mode must be dynamic or static", "settings": {"path_planning": path_settings, "robot_safety": robot_settings, "panel_status": panel_settings, "automation": automation_settings, "ai_parser": public_ai_settings()}}
        interval = float(pp.get("replan_interval_s", path_settings["replan_interval_s"]))
        retreat_distance = float(pp.get("retreat_distance_m", path_settings["retreat_distance_m"]))
        show_target_marker = bool(pp.get("show_target_marker", path_settings["show_target_marker"]))
        path_settings["mode"] = mode
        path_settings["replan_interval_s"] = max(1.0, min(30.0, interval))
        path_settings["retreat_distance_m"] = max(0.2, min(3.0, retreat_distance))
        path_settings["show_target_marker"] = show_target_marker
    if "ai_parser" in req:
        ai = req.get("ai_parser") or {}
        configure_ai_parser(
            provider=ai.get("provider"),
            base_url=ai.get("base_url"),
            model=ai.get("model"),
            api_key=ai.get("api_key"),
            clear_key=bool(ai.get("clear_key")),
        )
    if "robot_safety" in req:
        rs = req.get("robot_safety") or {}
        if "disabled_go2_names" in rs:
            valid_names = {g.name for g in sim.go2s}
            disabled = [str(name) for name in (rs.get("disabled_go2_names") or []) if str(name) in valid_names]
            robot_settings["disabled_go2_names"] = sorted(set(disabled))
        if "go2_battery_redline_pct" in rs:
            robot_settings["go2_battery_redline_pct"] = max(5.0, min(80.0, float(rs.get("go2_battery_redline_pct") or 20.0)))
        if "drone_battery_redline_pct" in rs:
            robot_settings["drone_battery_redline_pct"] = max(5.0, min(80.0, float(rs.get("drone_battery_redline_pct") or 20.0)))
    if "panel_status" in req:
        ps = req.get("panel_status") or {}
        if "healthy_ttl_s" in ps:
            panel_settings["healthy_ttl_s"] = max(0.0, min(86400.0, float(ps.get("healthy_ttl_s") or 0.0)))
        if "degradation_interval_s" in ps:
            panel_settings["degradation_interval_s"] = max(0.0, min(86400.0, float(ps.get("degradation_interval_s") or 0.0)))
    if "automation" in req:
        auto = req.get("automation") or {}
        if "auto_inspect_enabled" in auto:
            new_enabled = bool(auto.get("auto_inspect_enabled"))
            if new_enabled != automation_settings.get("auto_inspect_enabled", False):
                automation_settings["auto_inspect_enabled"] = new_enabled
                if new_enabled:
                    # Trigger first inspection on the next sim frame instead of
                    # waiting a full interval.  Set last_auto_inspect_ts far enough
                    # in the past that now - last >= interval passes immediately.
                    automation_settings["last_auto_inspect_ts"] = 0.0
                    automation_settings["auto_inspect_pending"] = False
                    mission.log("Automatic timed inspection enabled. First inspection will start shortly.")
                else:
                    automation_settings["last_auto_inspect_ts"] = sim_now()
                    automation_settings["auto_inspect_pending"] = False
                    mission.log("Automatic timed inspection disabled.")
        if "auto_inspect_interval_s" in auto:
            new_interval = max(0.0, min(86400.0, float(auto.get("auto_inspect_interval_s") or 0.0)))
            if new_interval != automation_settings.get("auto_inspect_interval_s", 360.0):
                automation_settings["auto_inspect_interval_s"] = new_interval
                # Only reset the timer when the interval actually changed and the
                # feature is enabled, so the new interval takes effect.
                if automation_settings.get("auto_inspect_enabled", False):
                    automation_settings["last_auto_inspect_ts"] = 0.0
                    automation_settings["auto_inspect_pending"] = False
                    mission.log(f"Automatic inspection interval changed to {new_interval:.0f}s.")
    settings = {"path_planning": path_settings, "robot_safety": robot_settings, "panel_status": panel_settings, "automation": automation_settings, "ai_parser": public_ai_settings()}
    asyncio.create_task(broadcast(build_envelope()))
    return {"ok": True, "settings": settings}


@app.post("/api/robots/{robot_name}/return")
async def api_robot_return(robot_name: str):
    if robot_name.startswith("Go2-"):
        ok = start_go2_return_base(robot_name, "Manual return from Robots tab")
    elif robot_name.startswith("Drone-"):
        ok = start_drone_return_base(robot_name, "Manual return from Robots tab")
    else:
        ok = False
    asyncio.create_task(broadcast(build_envelope()))
    return {"ok": ok, "robot": robot_name, "message": "Return issued" if ok else "Unknown robot or no route"}


@app.post("/api/robots/{robot_name}/disable")
async def api_robot_disable(robot_name: str):
    ok = robot_name.startswith("Go2-") and set_go2_available(robot_name, False)
    asyncio.create_task(broadcast(build_envelope()))
    return {"ok": ok, "robot": robot_name, "available": False}


@app.post("/api/robots/{robot_name}/enable")
async def api_robot_enable(robot_name: str):
    ok = robot_name.startswith("Go2-") and set_go2_available(robot_name, True)
    asyncio.create_task(broadcast(build_envelope()))
    return {"ok": ok, "robot": robot_name, "available": True}


@app.post("/api/ai-parser-test")
async def ai_parser_test():
    result = await asyncio.to_thread(test_ai_connection)
    asyncio.create_task(broadcast(build_envelope()))
    return result


@app.post("/api/panels/{row}/{col}/status")
async def api_panel_status(row: int, col: int, req: dict):
    status = str(req.get("status", "unknown"))
    note = str(req.get("note", "manual panel update"))
    if status not in PANEL_STATUSES:
        return {"ok": False, "error": f"invalid status: {status}", "allowed": sorted(PANEL_STATUSES)}
    ok = update_panel_status(row, col, status, "manual", note)
    if not ok:
        return {"ok": False, "error": f"panel not found: ({row},{col})"}
    asyncio.create_task(broadcast(build_envelope()))
    return {"ok": True, "panel": {"row": row, "col": col, "status": status}}


@app.post("/api/tasks/{task_id}/stop-blocked")
async def api_task_stop_blocked(task_id: str):
    ok, message = stop_blocked_task(task_id)
    asyncio.create_task(broadcast(build_envelope()))
    return {"ok": ok, "message": message, "task_id": task_id}


@app.post("/api/tasks/{task_id}/retry-blocked")
async def api_task_retry_blocked(task_id: str):
    ok, message = retry_blocked_task(task_id)
    asyncio.create_task(broadcast(build_envelope()))
    return {"ok": ok, "message": message, "task_id": task_id}


@app.post("/api/system-update")
async def system_update():
    save_update_snapshot()
    asyncio.create_task(broadcast(build_envelope()))
    asyncio.create_task(restart_process_later())
    return {"ok": True, "restarting": True, "preserve_records": True, "boot_id": BOOT_ID, "ts": time.time()}


@app.post("/api/system-restart")
async def system_restart():
    restart_system_state()
    asyncio.create_task(broadcast(build_envelope()))
    asyncio.create_task(restart_process_later())
    return {"ok": True, "restarting": True, "boot_id": BOOT_ID, "ts": time.time()}


@app.get("/api/status")
async def api_status():
    return build_envelope()


@app.websocket("/ws/solar")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    await ws.send_text(json.dumps(build_envelope(), default=str))
    print("[Solar] Client connected. Full state sent.")
    try:
        while True:
            data = await ws.receive_text()
            await handle_command(json.loads(data))
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


async def handle_command(packet: dict):
    raw = packet.get("command", "")
    typ = packet.get("type", "")

    if typ == "reset":
        await dispatch_command_text("reset")
        return

    if typ == "select_camera":
        return

    if not raw:
        return

    trace = await dispatch_command_text(raw)
    print(f"[Solar] {raw} -> source={trace.source} actions={trace.actions}")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8011"))
    uvicorn.run(app, host=host, port=port)
