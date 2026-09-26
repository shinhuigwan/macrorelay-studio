"""Portable selected-node payload for copying between Studio macros."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


MIME_TYPE = "application/x-macrorelay-nodes+json"


def copy_nodes(macro: dict[str, Any], indexes: list[int]) -> dict[str, Any]:
    steps = macro.get("steps") or []
    selected = sorted({int(index) for index in indexes if 0 < int(index) <= len(steps)})
    positions = macro.get("graph_positions") or {}
    return {
        "version": 1,
        "nodes": [
            {
                "index": index,
                "step": deepcopy(steps[index - 1]),
                "position": list(positions.get(str(index), [(index - 1) * 240, 0])),
            }
            for index in selected
        ],
    }


def paste_nodes(macro: dict[str, Any], payload: dict[str, Any], x: float, y: float) -> list[int]:
    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(nodes, list) or not nodes:
        raise ValueError("복사한 노드 데이터가 올바르지 않습니다.")
    steps = macro.setdefault("steps", [])
    positions = macro.setdefault("graph_positions", {})
    old_indexes = [int(node["index"]) for node in nodes]
    if len(old_indexes) != len(set(old_indexes)) or any(index <= 0 for index in old_indexes):
        raise ValueError("복사한 노드 번호가 올바르지 않습니다.")
    mapping = {old: len(steps) + offset + 1 for offset, old in enumerate(old_indexes)}
    left = min(float(node["position"][0]) for node in nodes)
    top = min(float(node["position"][1]) for node in nodes)

    def mapped(value: Any) -> int:
        try:
            return mapping.get(int(value), 0)
        except (TypeError, ValueError):
            return 0

    for node in nodes:
        old = int(node["index"])
        step = deepcopy(node["step"])
        success = mapped(step.get("on_success"))
        if not success and not step.get("on_success") and old + 1 in mapping and not step.get("stop_on_success"):
            success = mapping[old + 1]
        step["on_success"] = success
        step["on_fail"] = mapped(step.get("on_fail"))
        if isinstance(step.get("success_candidates"), list):
            step["success_candidates"] = [target for value in step["success_candidates"] if (target := mapped(value))]
        if isinstance(step.get("fail_candidates"), list):
            step["fail_candidates"] = [target for value in step["fail_candidates"] if (target := mapped(value))]
        if not success and not step.get("success_candidates"):
            step["stop_on_success"] = True
        if not step["on_fail"] and not step.get("fail_candidates"):
            step["abort_on_fail"] = True
        if isinstance(step.get("edge_conditions"), list):
            step["edge_conditions"] = [
                {**rule, "target": mapped(rule.get("target"))}
                for rule in step["edge_conditions"]
                if isinstance(rule, dict) and mapped(rule.get("target"))
            ]
        routes = step.get("asset_routes")
        if isinstance(routes, dict):
            for route in routes.values():
                if isinstance(route, dict):
                    for outcome in ("true", "fail"):
                        route[outcome] = mapped(route.get(outcome))
        for field in ("target_step", "jump_to", "on_match", "on_no_match"):
            if field in step:
                step[field] = mapped(step[field])
        # Do not merge a partial branch/workflow into an unrelated target macro.
        for field in ("workflow_id", "workflow_label", "branch_id", "branch_label"):
            step.pop(field, None)
        automation = step.get("_automation")
        if isinstance(automation, dict):
            automation.pop("group_flow_generated", None)
            if not automation:
                step.pop("_automation", None)
        new_index = mapping[old]
        steps.append(step)
        source_x, source_y = node["position"][:2]
        positions[str(new_index)] = [round(x + float(source_x) - left, 2), round(y + float(source_y) - top, 2)]
    return [mapping[index] for index in old_indexes]
