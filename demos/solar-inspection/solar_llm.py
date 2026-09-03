"""LLM semantic parser for solar inspection commands.

The provider is OpenAI-compatible. DeepSeek is the default runtime target.
API keys are injected by the backend at runtime and are never written here.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from solar_skills import ALLOWED_SKILLS, SolarSkillCommand

LLM_COOLDOWN_UNTIL = 0.0
DEFAULT_AI_SETTINGS = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "api_key": "",
    "last_error": "",
}
AI_SETTINGS = DEFAULT_AI_SETTINGS.copy()


@dataclass
class LLMParseResult:
    commands: list[SolarSkillCommand] = field(default_factory=list)
    raw_json: dict[str, Any] = field(default_factory=dict)
    normalized_json: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    request_summary: dict[str, Any] = field(default_factory=dict)


SYSTEM_PROMPT = """You are the Solar Inspection mission parser.
Convert natural language (English or Chinese) into strict JSON only. No explanation text.

Robots:
- Drones: Drone-01..Drone-05
- Ground robots: Go2-01..Go2-05
- Use "auto" when the user asks for inspection but does not name a robot.

Skills:
- inspect_solar_field: full farm drone scan, then Go2 confirmation.
- inspect_row: composite row inspection; fields row.
- drone_scan_row: scan one row by drone; fields row; robot can be "auto".
- drone_scan_panels: scan panel list; field panels as [[row,col],...]; robot can be "auto".
- go2_confirm_anomalies: confirm currently detected anomalies; optional row or panels; robot can be "auto".
- go2_navigate / drone_navigate: navigate to one panel; fields row, col.
- inspect_panel: inspect one panel; fields row, col.
- move_forward / move_backward: Go2 movement; params.distance_m defaults to 1.0.
- turn_left / turn_right: Go2 turn; params.angle_deg defaults to 90.
- drone_return, go2_return_base, stop, idle, reset.

Output schema:
{
  "version": "solar-command-v1",
  "intent": "short_intent_name",
  "confidence": 0.0,
  "tasks": [
    {
      "label": "Task label",
      "mode": "sequential",
      "steps": [
        {
          "label": "Step label",
          "mode": "parallel",
          "depends_on": "",
          "actions": [
            {"skill": "move_forward", "robot": "Go2-01", "desc": "Move forward 2m", "params": {"distance_m": 2}}
          ]
        }
      ]
    }
  ]
}

Rules:
- Preserve action order.
- If a user mentions multiple robots doing the same action, put them in one parallel step.
- If a later step omits robots after explicit Go2/Drone robots, keep the same compatible robots.
- For vague row inspection like "去检查一下第八行", output intent "inspect_row" and a two-step task:
  1) drone_scan_row with robot "auto"
  2) go2_confirm_anomalies with robot "auto", same row, depends_on "anomalies_from_previous_step"
- Rows and columns are zero-based if the user says row 8 or panel 2,4. Do not convert to one-based.
- Use strict known skills only.

Examples:
User: 去检查一下第八行
JSON: {"version":"solar-command-v1","intent":"inspect_row","confidence":0.86,"tasks":[{"label":"Inspect row 8","mode":"sequential","steps":[{"label":"Drone scan row 8","mode":"parallel","actions":[{"skill":"drone_scan_row","robot":"auto","row":8,"desc":"Drone scan row 8"}]},{"label":"Go2 confirm anomalies from row 8","mode":"conditional","depends_on":"anomalies_from_previous_step","actions":[{"skill":"go2_confirm_anomalies","robot":"auto","row":8,"desc":"Go2 confirm anomalies from row 8"}]}]}]}
User: Go2-03 and Go2-04 turn right, move forward 5m
JSON: {"version":"solar-command-v1","intent":"go2_motion_chain","confidence":0.93,"tasks":[{"label":"Go2-03 and Go2-04 motion chain","mode":"sequential","steps":[{"label":"Turn right 90deg","mode":"parallel","actions":[{"skill":"turn_right","robot":"Go2-03","desc":"Turn right 90deg","params":{"angle_deg":90}},{"skill":"turn_right","robot":"Go2-04","desc":"Turn right 90deg","params":{"angle_deg":90}}]},{"label":"Move forward 5m","mode":"parallel","actions":[{"skill":"move_forward","robot":"Go2-03","desc":"Move forward 5m","params":{"distance_m":5}},{"skill":"move_forward","robot":"Go2-04","desc":"Move forward 5m","params":{"distance_m":5}}]}]}]}
"""


def configure_ai_parser(
    *,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    clear_key: bool = False,
) -> None:
    if provider:
        AI_SETTINGS["provider"] = str(provider).strip() or DEFAULT_AI_SETTINGS["provider"]
    if base_url:
        AI_SETTINGS["base_url"] = str(base_url).strip().rstrip("/") or DEFAULT_AI_SETTINGS["base_url"]
    if model:
        AI_SETTINGS["model"] = str(model).strip() or DEFAULT_AI_SETTINGS["model"]
    if clear_key:
        AI_SETTINGS["api_key"] = ""
    elif api_key is not None and str(api_key).strip():
        candidate = str(api_key).strip()
        if not _looks_masked_key(candidate):
            AI_SETTINGS["api_key"] = candidate
    AI_SETTINGS["last_error"] = ""


def reset_ai_parser_runtime() -> None:
    AI_SETTINGS.clear()
    AI_SETTINGS.update(DEFAULT_AI_SETTINGS)


def public_ai_settings() -> dict[str, Any]:
    api_key = _api_key()
    runtime_api_key = AI_SETTINGS.get("api_key") or ""
    runtime_key = bool(AI_SETTINGS.get("api_key"))
    return {
        "provider": AI_SETTINGS.get("provider") or DEFAULT_AI_SETTINGS["provider"],
        "base_url": _base_url(),
        "model": _model(),
        "api_key_set": bool(api_key),
        "api_key_masked": _mask_key(runtime_api_key),
        "runtime_key_set": runtime_key,
        "env_key_set": False,
        "llm_available": bool(api_key),
        "last_error": AI_SETTINGS.get("last_error", ""),
    }


def ai_settings_snapshot() -> dict[str, Any]:
    return AI_SETTINGS.copy()


def restore_ai_settings_snapshot(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        return
    AI_SETTINGS.update({
        "provider": data.get("provider") or DEFAULT_AI_SETTINGS["provider"],
        "base_url": (data.get("base_url") or DEFAULT_AI_SETTINGS["base_url"]).rstrip("/"),
        "model": data.get("model") or DEFAULT_AI_SETTINGS["model"],
        "api_key": data.get("api_key") or "",
        "last_error": data.get("last_error") or "",
    })


def llm_available() -> bool:
    return bool(_api_key())


def parse_with_llm(raw_command: str, timeout_s: float = 10.0) -> Optional[list[SolarSkillCommand]]:
    result = parse_plan_with_llm(raw_command, timeout_s=timeout_s)
    return result.commands if result and result.commands else None


def parse_plan_with_llm(raw_command: str, timeout_s: float = 10.0) -> Optional[LLMParseResult]:
    global LLM_COOLDOWN_UNTIL
    if time.time() < LLM_COOLDOWN_UNTIL:
        return None

    api_key = _api_key()
    if not api_key:
        return None

    base_url = _base_url()
    model = _model()
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw_command},
        ],
    }

    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            LLM_COOLDOWN_UNTIL = time.time() + 60.0
        AI_SETTINGS["last_error"] = f"HTTP {exc.code}: {exc.reason}"
        print(f"[Solar LLM] {AI_SETTINGS['last_error']}")
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        AI_SETTINGS["last_error"] = str(exc)
        print(f"[Solar LLM] {exc}")
        return None

    try:
        content = body["choices"][0]["message"]["content"]
        data = json.loads(content)
        normalized, errors = normalize_command_json(data)
        commands = commands_from_llm_json(normalized)
        AI_SETTINGS["last_error"] = "" if commands else "; ".join(errors)
        return LLMParseResult(
            commands=commands,
            raw_json=data,
            normalized_json=normalized,
            errors=errors,
            request_summary={
                "provider": AI_SETTINGS.get("provider"),
                "base_url": base_url,
                "model": model,
                "response_format": "json_object",
            },
        )
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        AI_SETTINGS["last_error"] = f"invalid response: {exc}"
        print(f"[Solar LLM] invalid response: {exc}")
        return None


def test_ai_connection(timeout_s: float = 10.0) -> dict[str, Any]:
    api_key = _api_key()
    if not api_key:
        return {"ok": False, "error": "missing API key", "settings": public_ai_settings()}

    base_url = _base_url()
    model = _model()
    nonce = f"solar-{int(time.time() * 1000)}"
    user_message = (
        f"Hello from the solar inspection demo. "
        f"Connection test nonce: {nonce}. Reply in one short sentence and include this nonce."
    )
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": 'Return strict JSON only with fields: {"ok": true, "nonce": "...", "reply": "..."}.'},
            {"role": "user", "content": user_message},
        ],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        data = json.loads(content)
        reply = str(data.get("reply") or data.get("message") or content)
        ok = bool(data.get("ok")) and str(data.get("nonce") or "") == nonce
        AI_SETTINGS["last_error"] = ""
        return {
            "ok": ok,
            "message": reply,
            "user_message": user_message,
            "nonce": nonce,
            "llm_reply": content,
            "provider": AI_SETTINGS.get("provider"),
            "base_url": base_url,
            "model": model,
            "settings": public_ai_settings(),
        }
    except urllib.error.HTTPError as exc:
        AI_SETTINGS["last_error"] = f"HTTP {exc.code}: {exc.reason}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError, IndexError, TypeError) as exc:
        AI_SETTINGS["last_error"] = str(exc)
    return {"ok": False, "error": AI_SETTINGS["last_error"], "settings": public_ai_settings()}


def commands_from_llm_json(data: dict[str, Any]) -> list[SolarSkillCommand]:
    actions = _flatten_actions(data)
    if not isinstance(actions, list):
        raise ValueError("missing actions array")

    commands: list[SolarSkillCommand] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        skill = str(action.get("skill", "")).strip()
        if skill not in ALLOWED_SKILLS:
            continue
        commands.append(
            SolarSkillCommand(
                skill=skill,
                desc=str(action.get("desc") or skill.replace("_", " ")),
                robot=_optional_robot(action.get("robot")),
                row=_optional_int(action.get("row")),
                col=_optional_int(action.get("col")),
                panels=_optional_panels(action.get("panels")),
                params=action.get("params") if isinstance(action.get("params"), dict) else {},
            )
        )
    return commands


def normalize_command_json(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return {"version": "solar-command-v1", "intent": "invalid", "tasks": []}, ["response is not an object"]
    normalized = {
        "version": str(data.get("version") or "solar-command-v1"),
        "intent": str(data.get("intent") or "custom"),
        "confidence": _optional_float(data.get("confidence"), 0.0),
        "tasks": [],
    }
    if isinstance(data.get("tasks"), list):
        for task_index, task in enumerate(data["tasks"]):
            if not isinstance(task, dict):
                errors.append(f"task {task_index + 1} is not an object")
                continue
            steps = []
            raw_steps = task.get("steps")
            if not isinstance(raw_steps, list):
                raw_steps = [{"label": task.get("label") or "Task step", "actions": task.get("actions") or []}]
            for step_index, step in enumerate(raw_steps):
                if not isinstance(step, dict):
                    errors.append(f"task {task_index + 1} step {step_index + 1} is not an object")
                    continue
                actions = [_normalize_action(a, errors) for a in (step.get("actions") or []) if isinstance(a, dict)]
                actions = [a for a in actions if a]
                steps.append({
                    "label": str(step.get("label") or f"Step {step_index + 1}"),
                    "mode": str(step.get("mode") or "parallel"),
                    "depends_on": str(step.get("depends_on") or ""),
                    "actions": actions,
                })
            normalized["tasks"].append({
                "label": str(task.get("label") or f"Task {task_index + 1}"),
                "mode": str(task.get("mode") or "sequential"),
                "steps": steps,
            })
    elif isinstance(data.get("actions"), list):
        actions = [_normalize_action(a, errors) for a in data["actions"] if isinstance(a, dict)]
        actions = [a for a in actions if a]
        normalized["tasks"].append({
            "label": str(data.get("label") or "Command task"),
            "mode": "sequential",
            "steps": [{"label": "Command actions", "mode": "parallel", "depends_on": "", "actions": actions}],
        })
    else:
        errors.append("missing tasks or actions")
    return normalized, errors


def _normalize_action(action: dict[str, Any], errors: list[str]) -> Optional[dict[str, Any]]:
    skill = str(action.get("skill", "")).strip()
    if skill not in ALLOWED_SKILLS:
        errors.append(f"unsupported skill: {skill or '<missing>'}")
        return None
    out: dict[str, Any] = {
        "skill": skill,
        "desc": str(action.get("desc") or skill.replace("_", " ").title()),
    }
    robot = _optional_robot(action.get("robot"))
    if robot:
        out["robot"] = robot
    row = _optional_int(action.get("row"))
    col = _optional_int(action.get("col"))
    if row is not None:
        out["row"] = row
    if col is not None:
        out["col"] = col
    panels = _optional_panels(action.get("panels"))
    if panels:
        out["panels"] = panels
    params = action.get("params") if isinstance(action.get("params"), dict) else {}
    if skill in {"move_forward", "move_backward"}:
        params = {**params, "distance_m": _clamp_float(params.get("distance_m"), 1.0, 0.1, 20.0)}
    if skill in {"turn_left", "turn_right"}:
        params = {**params, "angle_deg": _clamp_float(params.get("angle_deg"), 90.0, 1.0, 360.0)}
    if params:
        out["params"] = params
    return out


def _flatten_actions(data: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(data.get("actions"), list):
        return data["actions"]
    actions: list[dict[str, Any]] = []
    for task in data.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        for step in task.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("actions"), list):
                actions.extend(step["actions"])
    return actions


def _api_key() -> str:
    return AI_SETTINGS.get("api_key") or ""


def _mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 2:
        return value[0] + "*" * max(1, len(value) - 1)
    return value[0] + "*" * max(3, len(value) - 2) + value[-1]


def _looks_masked_key(value: str) -> bool:
    return "*" in value and len(value) >= 3


def _base_url() -> str:
    return (AI_SETTINGS.get("base_url") or os.getenv("LLM_BASE_URL") or DEFAULT_AI_SETTINGS["base_url"]).rstrip("/")


def _model() -> str:
    return AI_SETTINGS.get("model") or os.getenv("LLM_MODEL") or DEFAULT_AI_SETTINGS["model"]


def _optional_robot(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() == "auto":
        return "auto"
    parts = text.replace("_", "-").split("-")
    if len(parts) == 2 and parts[0].lower() in {"go2", "drone"}:
        try:
            return f"{parts[0].title()}-{int(parts[1]):02d}"
        except ValueError:
            return None
    return text


def _optional_panels(value: Any) -> list[tuple[int, int]]:
    panels: list[tuple[int, int]] = []
    if not isinstance(value, list):
        return panels
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            row = _optional_int(item[0])
            col = _optional_int(item[1])
            if row is not None and col is not None:
                panels.append((row, col))
        elif isinstance(item, dict):
            row = _optional_int(item.get("row"))
            col = _optional_int(item.get("col"))
            if row is not None and col is not None:
                panels.append((row, col))
    return panels


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    return max(low, min(high, _optional_float(value, default)))
