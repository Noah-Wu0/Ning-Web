"""Solar inspection orchestrator: multi-drone scan + Go2 ground verification."""
from __future__ import annotations

import math, re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from sim_solar import Go2Robot, Drone, VelocityCmd
from solar_map import (SolarFarmMap, SolarPanel,
    check_panel_at_position, mark_panel_inspected, is_within_boundary)
from terrain_height import terrain_height

DRONE_PARK_WORLD_Y = 0.655
DRONE_RETURN_MIN_AGL = 4.0


class State(Enum):
    IDLE = "idle"
    TAKEOFF = "takeoff"
    SCANNING = "scanning"
    RTB = "returning_to_base"
    COMPLETE = "complete"


@dataclass
class Mission:
    farm: SolarFarmMap
    state: State = State.IDLE
    drone_states: dict[str, State] = field(default_factory=dict)
    drone_scan_indices: dict[str, int] = field(default_factory=dict)
    drone_photo_cooldowns: dict[str, float] = field(default_factory=dict)
    go2_target: Optional[SolarPanel] = None
    go2_inspect_dwell: float = 0.0
    go2_inspect_done: bool = False
    anomaly_queue: list[tuple[int, int]] = field(default_factory=list)
    inspected_list: list[tuple[int, int]] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)

    def log(self, m: str):
        self.log_lines.append(m)
        self.log_lines = self.log_lines[-50:]

    def reset(self):
        self.state = State.IDLE
        self.drone_states = {}
        self.drone_scan_indices = {}
        self.drone_photo_cooldowns = {}
        self.go2_target = None
        self.go2_inspect_dwell = 0.0
        self.go2_inspect_done = False
        self.anomaly_queue = []
        self.inspected_list = []
        self.log_lines = []


def parse_solar_command(raw: str) -> Optional[dict]:
    t = raw.strip().lower()
    if any(w in t for w in ("stop", "halt", "abort", "cancel", "land all")):
        return {"action": "stop"}
    if any(w in t for w in ("inspect solar", "patrol solar", "scan solar",
                             "solar inspection", "inspect field", "scan field",
                             "start inspection", "start patrol", "start scan")):
        return {"action": "start"}
    m = re.search(r"(?:inspect|check)\s*(?:panel|solar)?\s*(\d+)[,\s]+(\d+)", t)
    if m:
        return {"action": "panel", "row": int(m.group(1)), "col": int(m.group(2))}
    if any(w in t for w in ("go2 inspect", "dog inspect", "ground check", "confirm")):
        return {"action": "go2_go"}
    if any(w in t for w in ("rtl", "return home", "drone land", "recall", "drone return")):
        return {"action": "rtl"}
    return None


def update_mission(mission: Mission, drones: list[Drone], go2s: list[Go2Robot], dt: float) -> tuple[list[VelocityCmd], list[dict]]:
    """Main update: returns (go2_cmds, [drone_cmds...]).
    Go2 movement is handled entirely by the assignment system in server.py;
    this function only controls drone phases and returns zero go2_cmds."""
    farm = mission.farm
    drone_cmds = [{"vx": 0, "vy": 0, "vz": 0, "yaw": d.pose.yaw, "dist": 0, "dz": 0} for d in drones]
    go2_cmds = [VelocityCmd() for _ in go2s]

    if mission.state == State.IDLE:
        _update_idle(mission, drones, go2s)

    elif mission.state == State.TAKEOFF:
        drone_cmds = _update_takeoff(mission, drones, dt)

    elif mission.state == State.SCANNING:
        drone_cmds = _update_scanning(mission, drones, dt)

    elif mission.state == State.RTB:
        drone_cmds = _update_rtb(mission, drones, dt, farm)

    elif mission.state == State.COMPLETE:
        for d in drones:
            d.status = "landed"; d.task_desc = "Mission complete."

    return go2_cmds, drone_cmds


def _ground_z(x: float, y: float, offset: float = 0.05) -> float:
    return terrain_height(x, y) + offset


def _safe_rtb_cmd(d: Drone, pad, dt: float, scan_altitude: float) -> dict:
    hd = math.hypot(d.pose.x - pad.x, d.pose.y - pad.y)
    ground = terrain_height(pad.x, pad.y)
    if hd > 0.7:
        cruise_z = max(
            d.pose.z,
            ground + max(scan_altitude, DRONE_RETURN_MIN_AGL),
            ground + DRONE_PARK_WORLD_Y + DRONE_RETURN_MIN_AGL,
        )
        d.status = "returning"; d.task_desc = "Returning to pad"
        cmd = d.compute_flight(pad.x, pad.y, cruise_z, dt)
        cmd["dist"] = max(cmd.get("dist", hd), 1.0)
        return cmd

    ground = terrain_height(pad.x, pad.y)
    target_z = ground + DRONE_PARK_WORLD_Y
    d.pose.x = pad.x; d.pose.y = pad.y
    d.status = "landing"; d.task_desc = "Descending to pad"
    d.pose.z = max(target_z, d.pose.z - d.max_v_speed * dt)
    d._vx_smooth = d._vy_smooth = d._vz_smooth = 0.0
    if d.pose.z <= target_z + 0.02:
        d.pose.z = target_z
        d.prev_z_err = 0.0
        d.propeller_rpm = 0; d.status = "landed"
        d.task_desc = "Landed on pad"
        return {"vx": 0, "vy": 0, "vz": 0, "yaw": d.pose.yaw, "dist": 0, "dz": 0}
    return {"vx": 0, "vy": 0, "vz": 0, "yaw": d.pose.yaw, "dist": 0, "dz": 0}
    cmd["vx"] = 0.0; cmd["vy"] = 0.0; cmd["yaw"] = d.pose.yaw; cmd["dist"] = 1.0
    return cmd


def _update_idle(mission: Mission, drones: list[Drone], go2s: list[Go2Robot]):
    for d in drones:
        d.status = "idle"; d.camera_active = False; d.task_desc = "Standby on pad"
    for g in go2s:
        g.status = "idle"; g.task_desc = "Standby at base"


def _update_takeoff(mission: Mission, drones: list[Drone], dt: float) -> list[dict]:
    farm = mission.farm
    alt = farm.drone_scan_altitude
    all_ready = True
    cmds = []
    for i, d in enumerate(drones):
        ds = mission.drone_states.get(d.name, State.TAKEOFF)
        pad = farm.bases[i] if i < len(farm.bases) else farm.bases[0]
        target_z = _ground_z(pad.x, pad.y, alt)
        cmd = d.compute_flight(pad.x, pad.y, target_z, dt)
        cmds.append(cmd)
        if ds == State.TAKEOFF:
            d.status = "taking_off"
            d.task_desc = f"Taking off to {alt:.0f}m"
            if cmd["dist"] < 1.0 and cmd["dz"] < 0.5:
                mission.drone_states[d.name] = State.SCANNING
                mission.drone_scan_indices[d.name] = 0
        if mission.drone_states.get(d.name) != State.SCANNING:
            all_ready = False
    if all_ready:
        mission.state = State.SCANNING
        mission.log("All drones at scan altitude. Starting patrol.")
    return cmds


def _update_scanning(mission: Mission, drones: list[Drone], dt: float) -> list[dict]:
    farm = mission.farm
    cmds = []
    for i, d in enumerate(drones):
        lane = i % len(farm.waypoints)
        wps = farm.waypoints[lane]
        idx = mission.drone_scan_indices.get(d.name, 0)

        if idx >= len(wps):
            d.status = "returning"
            d.task_desc = "Lane complete. Waiting."
            cmds.append({"vx": 0, "vy": 0, "vz": 0, "yaw": d.pose.yaw, "dist": 0, "dz": 0})
            _check_all_drones_done(mission, drones)
            continue

        wp = wps[idx]
        target_z = _ground_z(wp.x, wp.y, wp.z)
        d.status = "scanning"
        d.task_desc = f"Scanning ({wp.row},{wp.col}) [{idx+1}/{len(wps)}]"
        cmd = d.compute_flight(wp.x, wp.y, target_z, dt)
        cmds.append(cmd)

        cd = mission.drone_photo_cooldowns.get(d.name, 0)
        if cmd["dist"] < 1.0 and cmd["dz"] < 0.5:
            cd += dt
            if cd > 0.4:
                panel = check_panel_at_position(farm, wp.x, wp.y, 2.5)
                if panel:
                    anomaly = mark_panel_inspected(farm, panel)
                    d.photo_count += 1; d.camera_active = True
                    if anomaly:
                        mission.log(f"[{d.name}] ANOMALY at ({panel.row},{panel.col})!")
                        if (panel.row, panel.col) not in mission.anomaly_queue:
                            mission.anomaly_queue.append((panel.row, panel.col))
                wp.reached = True
                mission.drone_scan_indices[d.name] = idx + 1
                cd = 0
        else:
            d.camera_active = False; cd = 0
        mission.drone_photo_cooldowns[d.name] = cd
    return cmds


def _check_all_drones_done(mission: Mission, drones: list[Drone]):
    farm = mission.farm
    all_done = True
    for i, d in enumerate(drones):
        lane = i % len(farm.waypoints)
        wps = farm.waypoints[lane]
        idx = mission.drone_scan_indices.get(d.name, 0)
        if idx < len(wps):
            all_done = False
    if all_done:
        inspected = sum(1 for p in farm.panels if p.inspected)
        mission.log(f"All drones done. {inspected}/{len(farm.panels)} panels scanned.")
        mission.state = State.RTB


def _update_rtb(mission: Mission, drones: list[Drone], dt: float, farm: SolarFarmMap) -> list[dict]:
    cmds = []
    all_landed = True
    for i, d in enumerate(drones):
        pad = farm.bases[i] if i < len(farm.bases) else farm.bases[0]
        cmd = _safe_rtb_cmd(d, pad, dt, farm.drone_scan_altitude)
        if d.status != "landed":
            all_landed = False
        cmds.append(cmd)

    if all_landed:
        if mission.anomaly_queue:
            mission.log(f"Drones landed. {len(mission.anomaly_queue)} anomalies await ground confirmation.")
            mission.state = State.COMPLETE
        else:
            mission.state = State.COMPLETE
    return cmds
