"""Rule-based + LLM command parsing for solar inspection."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from solar_llm import llm_available, parse_plan_with_llm
from solar_skills import SolarSkillCommand


@dataclass
class SemanticParseResult:
    commands: list[SolarSkillCommand] = field(default_factory=list)
    source: str = "none"
    raw_json: dict[str, Any] = field(default_factory=dict)
    normalized_json: dict[str, Any] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    llm_request_summary: dict[str, Any] = field(default_factory=dict)


async def parse_command_async(raw_command: str, *, allow_llm: bool = True) -> SemanticParseResult:
    """Returns parser commands plus observable metadata."""
    text = raw_command.strip()
    if not text:
        return SemanticParseResult()

    rule_commands = parse_command_rules(text)
    if rule_commands:
        return SemanticParseResult(commands=rule_commands, source="rules")

    if allow_llm and llm_available():
        llm_result = await asyncio.to_thread(parse_plan_with_llm, text)
        if llm_result and llm_result.commands:
            return SemanticParseResult(
                commands=llm_result.commands,
                source="llm",
                raw_json=llm_result.raw_json,
                normalized_json=llm_result.normalized_json,
                validation_errors=llm_result.errors,
                llm_request_summary=llm_result.request_summary,
            )

    # Second pass rules for partial matches
    fallback = parse_command_rules(text, relaxed=True)
    if fallback:
        return SemanticParseResult(commands=fallback, source="rules")
    return SemanticParseResult()


def parse_command_rules(raw_command: str, relaxed: bool = False) -> list[SolarSkillCommand]:
    t = raw_command.strip().lower()
    if not t:
        return []

    robots = _extract_robots(raw_command)
    robot = robots[0] if robots else None
    clauses = [c.strip() for c in re.split(r"\b(?:then|and then|next|再|然后)\b", t) if c.strip()]
    if len(clauses) > 1:
        commands: list[SolarSkillCommand] = []
        for clause in clauses:
            commands.extend(parse_command_rules(clause, relaxed=relaxed))
        if commands:
            return commands

    if any(w in t for w in ("stop", "halt", "abort", "cancel", "land all", "emergency")):
        return [SolarSkillCommand("stop", "Stop / abort mission", robot=robot)]

    if t in ("reset", "restart"):
        return [SolarSkillCommand("reset", "Reset simulation", robot=robot)]

    if any(w in t for w in ("standby", "idle", "hold", "wait", "stay")):
        return [SolarSkillCommand("idle", "Standby", robot=robot)]

    if any(w in t for w in (
        "inspect solar", "patrol solar", "scan solar", "solar inspection",
        "inspect field", "scan field", "start inspection", "start patrol", "start scan",
    )):
        return [SolarSkillCommand("inspect_solar_field", "Start full solar field inspection")]

    row = _extract_row_index(t)
    if row is not None and any(w in t for w in ("inspect", "check", "scan", "patrol", "检查", "巡检", "看一下", "扫")):
        return [SolarSkillCommand("inspect_row", f"Inspect row {row}", row=row)]

    panel_match = re.search(r"\bpanel\s*(\d+)\s*(?:[,，]|\s+)\s*(\d+)", t)
    if panel_match and any(w in t for w in ("send", "navigate", "dispatch", "go to", "fly to", "move to")):
        row, col = int(panel_match.group(1)), int(panel_match.group(2))
        if robots:
            commands: list[SolarSkillCommand] = []
            for target in robots:
                if target.startswith("Go2-"):
                    commands.append(SolarSkillCommand(
                        "go2_navigate",
                        f"Navigate {target} to panel ({row},{col})",
                        robot=target,
                        row=row,
                        col=col,
                    ))
                elif target.startswith("Drone-"):
                    commands.append(SolarSkillCommand(
                        "drone_navigate",
                        f"Navigate {target} to panel ({row},{col})",
                        robot=target,
                        row=row,
                        col=col,
                    ))
            if commands:
                return commands
        if "drone" in t or "fly" in t:
            return [SolarSkillCommand("drone_navigate", f"Navigate drone to panel ({row},{col})", robot=robot, row=row, col=col)]
        if "go2" in t or "dog" in t or "ground" in t:
            return [SolarSkillCommand("go2_navigate", f"Navigate Go2 to panel ({row},{col})", robot=robot, row=row, col=col)]

    m = re.search(r"(?:inspect|check)\s*(?:panel|solar)?\s*(\d+)\s*(?:[,，]|\s+)\s*(\d+)", t)
    if m:
        return [SolarSkillCommand(
            "inspect_panel",
            f"Inspect panel ({m.group(1)},{m.group(2)})",
            robot=robot,
            row=int(m.group(1)), col=int(m.group(2)),
        )]

    if any(w in t for w in ("go2 inspect", "dog inspect", "ground check", "ground inspect", "confirm anomaly")):
        return [SolarSkillCommand("go2_inspect", "Dispatch Go2 for ground inspection", robot=target)
                for target in (robots or [None])]

    if any(w in t for w in (
        "go2 return", "return go2", "recall go2", "recall dog", "recall dogs",
        "dog return", "return dog", "dogs return", "go2 base", "return base",
    )):
        explicit_go2s = [target for target in robots if target.startswith("Go2-")]
        fleet_requested = (
            not explicit_go2s
            and (
                "all" in t
                or "every" in t
                or "fleet" in t
                or "go2s" in t
                or "dogs" in t
                or "all go2" in t
                or "return go2" in t
                or "recall go2" in t
                or "recall dog" in t
                or "recall dogs" in t
            )
        )
        targets = explicit_go2s or (["Go2 Fleet"] if fleet_requested else [robot if robot and robot.startswith("Go2-") else None])
        return [SolarSkillCommand("go2_return_base", "Recall Go2 to base", robot=target) for target in targets]

    if any(w in t for w in ("rtl", "return home", "drone land", "recall", "drone return", "return drones")):
        return [SolarSkillCommand("drone_return", "Recall drones to base")]

    go2_move = _parse_go2_motion(t)
    if go2_move:
        distance_m = _extract_distance_m(t) if go2_move in {"move_forward", "move_backward"} else None
        if go2_move in {"move_forward", "move_backward"} and distance_m is None:
            distance_m = 1.0
        angle_deg = _extract_angle_deg(t) if go2_move in {"turn_left", "turn_right"} else None
        if go2_move in {"turn_left", "turn_right"} and angle_deg is None:
            angle_deg = 90.0
        params = {}
        if distance_m is not None:
            params["distance_m"] = distance_m
        if angle_deg is not None:
            params["angle_deg"] = angle_deg
        desc = go2_move.replace("_", " ").title()
        if distance_m is not None:
            desc += f" {distance_m:g}m"
        if angle_deg is not None:
            desc += f" {angle_deg:g}deg"
        targets = robots or [None]
        return [SolarSkillCommand(go2_move, desc, robot=target, params=params) for target in targets]

    if relaxed:
        if "forward" in t or "ahead" in t or "advance" in t:
            distance_m = _extract_distance_m(t) or 1.0
            return [SolarSkillCommand(
                "move_forward",
                f"Move forward {distance_m:g}m",
                robot=target,
                params={"distance_m": distance_m},
            ) for target in (robots or [None])]
        if "back" in t or "reverse" in t:
            distance_m = _extract_distance_m(t) or 1.0
            return [SolarSkillCommand(
                "move_backward",
                f"Move backward {distance_m:g}m",
                robot=target,
                params={"distance_m": distance_m},
            ) for target in (robots or [None])]
        if "left" in t:
            angle_deg = _extract_angle_deg(t) or 90.0
            return [SolarSkillCommand(
                "turn_left",
                f"Turn left {angle_deg:g}deg",
                robot=target,
                params={"angle_deg": angle_deg},
            ) for target in (robots or [None])]
        if "right" in t:
            angle_deg = _extract_angle_deg(t) or 90.0
            return [SolarSkillCommand(
                "turn_right",
                f"Turn right {angle_deg:g}deg",
                robot=target,
                params={"angle_deg": angle_deg},
            ) for target in (robots or [None])]

    return []


def _parse_go2_motion(t: str) -> Optional[str]:
    if any(w in t for w in ("move forward", "go forward", "walk forward", "forward", "ahead", "advance")):
        if "back" not in t:
            return "move_forward"
    if any(w in t for w in ("move backward", "go back", "walk back", "backward", "reverse")):
        return "move_backward"
    if "turn left" in t or t == "left":
        return "turn_left"
    if "turn right" in t or t == "right":
        return "turn_right"
    return None


def _extract_distance_m(t: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m\b|meters?\b|metres?\b|米)", t)
    if not m:
        return None
    value = float(m.group(1))
    return max(0.1, min(20.0, value))


def _extract_angle_deg(t: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:deg|degree|degrees|度)", t)
    if not m:
        return None
    value = float(m.group(1))
    return max(1.0, min(360.0, value))


def _extract_row_index(t: str) -> Optional[int]:
    m = re.search(r"\brow\s*(\d+)\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*行", t)
    if not m:
        return None
    token = m.group(1)
    if token.isdigit():
        return int(token)
    values = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if token == "十":
        return 10
    if token.startswith("十"):
        return 10 + values.get(token[1:], 0)
    if token.endswith("十"):
        return values.get(token[:-1], 1) * 10
    if "十" in token:
        left, right = token.split("十", 1)
        return values.get(left, 1) * 10 + values.get(right, 0)
    return values.get(token)


def _extract_robot(text: str) -> Optional[str]:
    robots = _extract_robots(text)
    return robots[0] if robots else None


def _extract_robots(text: str) -> list[str]:
    found: list[str] = []
    for m in re.finditer(r"(drone-\d{2}|go2-\d{2})", text, re.IGNORECASE):
        parts = m.group(1).split("-")
        robot = f"{parts[0].title()}-{parts[1]}"
        if robot not in found:
            found.append(robot)
    return found
