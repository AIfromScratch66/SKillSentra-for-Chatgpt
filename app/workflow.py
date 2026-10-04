from __future__ import annotations

import math
from typing import Any

from .errors import AppError
from .repository import Repository


def validate_step_contract(
    repository: Repository,
    project_id: str,
    route: str,
    step_index: int,
    payload: dict[str, Any],
    status: str,
) -> None:
    if route not in {"template", "existing"}:
        raise AppError("invalid_step_route", "步骤路线不正确。")
    maximum = 8 if route == "template" else 6
    if step_index < 0 or step_index > maximum:
        raise AppError("invalid_step_index", "步骤编号超出当前路线范围。")
    if status not in {"draft", "complete", "blocked"}:
        raise AppError("invalid_step_status", "步骤状态不正确。")

    project = repository.get_project(project_id)
    if project is None:
        raise AppError("project_not_found", "项目不存在。", 404)
    if project["route"] != route:
        raise AppError("project_route_conflict", "项目路线与步骤路线不一致。", 409)
    if status != "complete":
        return

    if route == "template":
        _validate_template(repository, project, step_index, payload)
    else:
        _validate_existing(repository, project, step_index, payload)


def _validate_template(repository: Repository, project: dict[str, Any], index: int, payload: dict[str, Any]) -> None:
    if index == 0:
        _require_text(payload, "skillName", "Skill 名称")
        _require_text(payload, "audience", "主要使用者")
        _require_text(payload, "goal", "目标")
        _require_text(payload, "success", "成功输出")
    elif index == 1:
        if payload.get("reuseDecision") not in {"复用", "扩展", "新建"}:
            raise AppError("step_gate_failed", "请选择复用、扩展或新建。", 409)
        _require_text(payload, "differenceGoal", "差异化目标")
    elif index == 2:
        dimensions = payload.get("dimensions")
        if not isinstance(dimensions, list) or not dimensions:
            raise AppError("step_gate_failed", "请设置评价维度。", 409)
        enabled = [item for item in dimensions if isinstance(item, dict) and item.get("on")]
        total = sum(_finite_number(item.get("weight", 0)) for item in enabled)
        if not enabled or abs(total - 100) > 0.001:
            raise AppError("step_gate_failed", "已启用评价维度的权重必须合计为 100%。", 409, {"weight_total": total})
    elif index == 3:
        _require_text(payload, "method", "创建方法")
        flows = payload.get("flows")
        if not isinstance(flows, list) or len(flows) < 3 or any(not str(item).strip() for item in flows):
            raise AppError("step_gate_failed", "请提供至少三个有效流程步骤。", 409)
        _require_text(payload, "mustDo", "必须做到")
        _require_text(payload, "mustNot", "禁止事项")
    elif index == 4:
        _require_validation(repository, project["id"], "static")
    elif index == 5:
        _require_validation(repository, project["id"], "evaluation")
    elif index == 6:
        decisions = [item.get("decision") for item in payload.get("proposals") or [] if isinstance(item, dict)]
        if not any(decision in {"accept", "reject"} for decision in decisions):
            raise AppError("step_gate_failed", "请至少处理一项改进建议。", 409)
        _require_validation(repository, project["id"], "regression")
    elif index == 7:
        _require_delivered_version(repository, project["id"])


def _validate_existing(repository: Repository, project: dict[str, Any], index: int, payload: dict[str, Any]) -> None:
    if index == 0:
        if not project.get("source_id"):
            raise AppError("step_gate_failed", "请先导入并冻结一个授权 Skill。", 409)
    elif index == 1:
        _require_validation(repository, project["id"], "baseline")
    elif index == 2:
        accepted = [item for item in payload.get("proposals") or [] if isinstance(item, dict) and item.get("decision") == "accept"]
        if not accepted:
            raise AppError("step_gate_failed", "请至少接受一个修改方向。", 409)
    elif index == 3:
        if not repository.list_skill_versions(project["id"]):
            raise AppError("step_gate_failed", "请先生成平台可管理的候选版本。", 409)
    elif index == 4:
        _require_validation(repository, project["id"], "regression")
    elif index == 5:
        _require_delivered_version(repository, project["id"])
    elif index == 6:
        if not repository.list_update_checks(project["id"], 1):
            raise AppError("step_gate_failed", "请先完成一次三方更新检查。", 409)


def _require_text(payload: dict[str, Any], key: str, label: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise AppError("step_gate_failed", f"请填写{label}。", 409, {"field": key})
    if len(value) > 20_000:
        raise AppError("step_field_too_large", f"{label}内容过长。", 413, {"field": key})
    return value


def _finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError("step_gate_failed", "评价权重必须是数字。", 409) from exc
    if not math.isfinite(number) or number < 0 or number > 100:
        raise AppError("step_gate_failed", "评价权重必须在 0–100 之间。", 409)
    return number


def _require_validation(repository: Repository, project_id: str, stage: str) -> None:
    validation = repository.latest_validation(project_id, stage)
    if not validation or validation["status"] != "passed":
        raise AppError("step_gate_failed", f"请先通过 {stage} 验证。", 409, {"stage": stage})


def _require_delivered_version(repository: Repository, project_id: str) -> None:
    if not any(version["status"] == "delivered" for version in repository.list_skill_versions(project_id)):
        raise AppError("step_gate_failed", "请先完成候选版本交付确认。", 409)
