"""Solar inspection simulation: multi-drone fleet, Go2 ground robot."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Pose2D:
    x: float = 0.0; y: float = 0.0; yaw: float = 0.0

@dataclass
class Pose3D:
    x: float = 0.0; y: float = 0.0; z: float = 0.0
    roll: float = 0.0; pitch: float = 0.0; yaw: float = 0.0

@dataclass
class VelocityCmd:
    linear_x: float = 0.0; linear_y: float = 0.0; angular: float = 0.0


# ---- Go2 Quadruped Robot ----
@dataclass
class Go2Robot:
    name: str = "Go2-01"
    pose: Pose2D = field(default_factory=Pose2D)
    battery: float = 100.0
    status: str = "idle"
    task_desc: str = "Standby at base"
    camera_active: bool = False
    inspection_target: Optional[tuple[int, int]] = None
    inspection_dwell: float = 0.0
    inspection_done: bool = False

    # Leg animation phases (cyclic for walking effect)
    leg_phase: float = 0.0
    max_speed: float = 0.85; max_turn: float = 0.65
    pos_tol: float = 0.45; yaw_tol: float = math.radians(14)
    manual_vx: float = 0.0; manual_vy: float = 0.0; manual_va: float = 0.0
    manual_timer: float = 0.0
    nav_vx: float = 0.0; nav_vy: float = 0.0; nav_va: float = 0.0

    def telemetry(self) -> dict:
        return {
            "type": "go2", "name": self.name,
            "pose": {"x": self.pose.x, "y": self.pose.y, "yaw": self.pose.yaw},
            "battery": self.battery, "status": self.status, "task_desc": self.task_desc,
            "camera_active": self.camera_active,
            "inspection_target": self.inspection_target,
            "leg_phase": self.leg_phase,
            "joint_states": self._visual_joint_states(),
        }

    def _visual_joint_states(self) -> dict[str, float]:
        moving = self.status not in ("idle", "landed")
        phase = self.leg_phase if moving else 0.0
        states = {}
        for leg, offset in {"FL": 0.0, "FR": math.pi, "RL": math.pi, "RR": 0.0}.items():
            lp = (phase + offset) % (2 * math.pi)
            swing = math.sin(lp)
            lift = max(0.0, math.sin(lp))
            states[f"{leg}_hip_joint"] = 0.06 * swing if moving else 0.0
            states[f"{leg}_thigh_joint"] = 0.6 + (0.14 * swing + 0.12 * lift if moving else 0.0)
            states[f"{leg}_calf_joint"] = -1.3 + (-0.12 * swing + 0.22 * lift if moving else 0.0)
        return states

    def move_to(self, tx: float, ty: float, dt: float) -> VelocityCmd:
        dx, dy = tx - self.pose.x, ty - self.pose.y
        dist = math.hypot(dx, dy)
        if dist < self.pos_tol:
            self.nav_vx *= 0.5
            self.nav_vy *= 0.5
            self.nav_va *= 0.5
            return VelocityCmd()
        desired = math.atan2(dy, dx)
        err = self._wrap(desired - self.pose.yaw)
        turn = max(-self.max_turn, min(self.max_turn, err * 1.4))
        alignment = max(0.18, math.cos(min(abs(err), math.pi / 2)))
        if abs(err) > math.radians(115):
            alignment = 0.0
        target_speed = min(self.max_speed, dist * 0.75) * alignment
        vx = target_speed * math.cos(self.pose.yaw)
        vy = target_speed * math.sin(self.pose.yaw)
        alpha = min(1.0, max(0.08, dt * 4.0))
        self.nav_vx += (vx - self.nav_vx) * alpha
        self.nav_vy += (vy - self.nav_vy) * alpha
        self.nav_va += (turn - self.nav_va) * alpha
        return VelocityCmd(self.nav_vx, self.nav_vy, self.nav_va)

    def step(self, cmd: VelocityCmd, dt: float):
        # Apply manual override if active
        if abs(self.manual_vx) + abs(self.manual_vy) + abs(self.manual_va) > 0.001:
            self.manual_timer -= dt
            if self.manual_timer <= 0:
                self.manual_vx = self.manual_vy = self.manual_va = 0.0
                self.status = "idle"
                self.task_desc = "Standby"
            else:
                cmd = VelocityCmd(self.manual_vx, self.manual_vy, self.manual_va)
                self.status = "moving"
        moving = abs(cmd.linear_x) + abs(cmd.linear_y) + abs(cmd.angular)
        self.pose.x += cmd.linear_x * dt
        self.pose.y += cmd.linear_y * dt
        self.pose.yaw = self._wrap(self.pose.yaw + cmd.angular * dt)
        self.leg_phase += moving * dt * 11.0  # visual gait speed; does not affect movement
        self.battery = max(0, self.battery - 0.005 * moving * dt)
        if self.status == "inspecting":
            self.battery = max(0, self.battery - 0.02 * dt)
            self.inspection_dwell += dt

    @staticmethod
    def _wrap(a): return (a + math.pi) % (2 * math.pi) - math.pi


# ---- Quadrotor Drone ----
@dataclass
class Drone:
    name: str = "Drone-01"
    pose: Pose3D = field(default_factory=Pose3D)
    battery: float = 100.0
    status: str = "idle"
    task_desc: str = "Standby on pad"
    camera_active: bool = False
    photo_count: int = 0
    propeller_rpm: float = 0.0
    propeller_angle: float = 0.0

    max_h_speed: float = 5.0; max_v_speed: float = 2.0
    scan_speed: float = 2.5; max_yaw_rate: float = 1.0
    pos_tol: float = 0.8; alt_tol: float = 0.5
    kp_z: float = 0.8; kd_z: float = 0.2; prev_z_err: float = 0.0
    # Smoothing
    _vx_smooth: float = 0.0; _vy_smooth: float = 0.0; _vz_smooth: float = 0.0

    def telemetry(self) -> dict:
        return {
            "type": "drone", "name": self.name,
            "pose": {"x": self.pose.x, "y": self.pose.y, "z": self.pose.z,
                     "roll": self.pose.roll, "pitch": self.pose.pitch, "yaw": self.pose.yaw},
            "battery": self.battery, "status": self.status, "task_desc": self.task_desc,
            "camera_active": self.camera_active, "photo_count": self.photo_count,
            "propeller_rpm": self.propeller_rpm, "propeller_angle": self.propeller_angle,
        }

    def compute_flight(self, tx: float, ty: float, tz: float, dt: float) -> dict:
        dx, dy, dz = tx - self.pose.x, ty - self.pose.y, tz - self.pose.z
        hd = math.hypot(dx, dy)
        # Smooth PID for altitude
        ze = max(-5.0, min(5.0, dz))
        zd = (ze - self.prev_z_err) / max(dt, 0.001)
        self.prev_z_err = ze
        vz_raw = self.kp_z * ze + self.kd_z * zd
        vz_raw = max(-self.max_v_speed, min(self.max_v_speed, vz_raw))
        if abs(ze) < self.alt_tol:
            vz_raw *= 0.3  # reduce oscillation near target
        # Low-pass filter vertical speed
        self._vz_smooth = self._vz_smooth * 0.7 + vz_raw * 0.3
        vz = self._vz_smooth

        # Horizontal with smoothing
        if hd < self.pos_tol:
            vx = vy = 0.0
        elif hd < 1.0:
            # Slow approach near target
            spd = hd * 1.5
            vx, vy = spd * dx / hd, spd * dy / hd
        else:
            spd = min(self.max_h_speed, hd * 1.2)
            vx, vy = spd * dx / hd, spd * dy / hd
        self._vx_smooth = self._vx_smooth * 0.75 + vx * 0.25
        self._vy_smooth = self._vy_smooth * 0.75 + vy * 0.25

        dyaw = math.atan2(dy, dx) if hd > 0.3 else self.pose.yaw
        return {"vx": self._vx_smooth, "vy": self._vy_smooth, "vz": vz,
                "yaw": dyaw, "dist": hd, "dz": abs(dz)}

    def step(self, cmd: dict, dt: float):
        self.pose.x += cmd.get("vx", 0) * dt
        self.pose.y += cmd.get("vy", 0) * dt
        self.pose.z += cmd.get("vz", 0) * dt
        self.pose.z = max(0.05, self.pose.z)
        self.pose.yaw = self._wrap(self.pose.yaw + math.copysign(
            min(self.max_yaw_rate * dt, abs(self._wrap(cmd.get("yaw", self.pose.yaw) - self.pose.yaw))),
            self._wrap(cmd.get("yaw", self.pose.yaw) - self.pose.yaw)))
        rpm = 4000 + abs(cmd.get("vz", 0)) * 2000
        self.propeller_rpm = rpm if cmd.get("dist", 1) > 0.05 else 0
        self.propeller_angle += dt * (rpm / 60.0) * math.tau  # accumulate rotation
        if self.status in ("flying", "scanning"):
            self.battery = max(0, self.battery - 0.03 * dt)

    @staticmethod
    def _wrap(a): return (a + math.pi) % (2 * math.pi) - math.pi


# ---- Combined Simulation ----
@dataclass
class SolarSimulation:
    drones: list[Drone] = field(default_factory=list)
    go2s: list[Go2Robot] = field(default_factory=list)
    control_dt: float = 0.02
    sim_time: float = 0.0

    def __post_init__(self):
        if not self.drones:
            self.drones = [Drone(f"Drone-0{i+1}") for i in range(5)]
        if not self.go2s:
            self.go2s = [Go2Robot(f"Go2-0{i+1}") for i in range(5)]

    def step(self, go2_cmds: list[VelocityCmd], drone_cmds: list[dict], dt: float):
        for g, gc in zip(self.go2s, go2_cmds):
            g.step(gc, dt)
        for d, c in zip(self.drones, drone_cmds):
            d.step(c, dt)
        self.sim_time += dt

    def telemetry(self) -> dict:
        return {
            "go2s": [g.telemetry() for g in self.go2s],
            "drones": [d.telemetry() for d in self.drones],
            "sim_time": self.sim_time,
        }
