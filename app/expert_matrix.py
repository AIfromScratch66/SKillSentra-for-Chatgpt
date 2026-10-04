from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any

from .errors import AppError


FRAMEWORK_VERSION = "skillsentra-expert-matrix-v1"
EVIDENCE_LEVELS = ("E0", "E1", "E2", "E3", "E4", "E5")
GATE_STATUSES = {"pass", "fail", "unknown"}
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
EXTERNAL_EVIDENCE_GATES = frozenset(
    {
        "real_host_connection",
        "real_task_evidence",
        "manual_accessibility",
        "human_release_authorization",
    }
)

DIMENSIONS: tuple[dict[str, Any], ...] = (
    {"id": "product_strategy", "name": "产品战略与一站式价值", "weight": 15},
    {"id": "instruction_architecture", "name": "Skill 指令架构与发现性", "weight": 15},
    {"id": "evaluation_science", "name": "评价科学与真实任务增量", "weight": 14},
    {"id": "safety_privacy", "name": "安全、隐私与最小权限", "weight": 10},
    {"id": "plugin_interoperability", "name": "ChatGPT 插件与 MCP 互操作", "weight": 14},
    {"id": "experience_visualization", "name": "结构化协作与可视化体验", "weight": 10},
    {"id": "reliability_lifecycle", "name": "可靠性、更新与全生命周期", "weight": 17},
    {"id": "accessibility_localization", "name": "可访问性与本地化", "weight": 5},
)

ROLES: tuple[dict[str, str], ...] = (
    {"id": "product_strategist", "name": "Skill 产品战略师", "focus": "价值链、用户任务与商业边界"},
    {"id": "instruction_architect", "name": "Skill 指令架构师", "focus": "触发、渐进加载与交付契约"},
    {"id": "evaluation_scientist", "name": "评价科学家", "focus": "冻结数据集、grader 与反事实增量"},
    {"id": "safety_privacy", "name": "安全与隐私审计师", "focus": "最小权限、危险动作与数据边界"},
    {"id": "mcp_architect", "name": "MCP 与宿主集成架构师", "focus": "协议、工具注解与真实联通"},
    {"id": "ux_visualization", "name": "AI 协同体验设计师", "focus": "GraphDiff、证据检查器与人工决定"},
    {"id": "reliability", "name": "平台可靠性工程师", "focus": "一致性、回滚、可观测性与容量"},
    {"id": "retrieval_marketplace", "name": "检索与市场专家", "focus": "搜索、排行真实性与来源追踪"},
    {"id": "ecosystem_governance", "name": "生态与治理专家", "focus": "许可、发布角色与供应链"},
    {"id": "accessibility", "name": "无障碍与本地化专家", "focus": "键盘、读屏、缩放与跨语言"},
)

GATES: tuple[dict[str, Any], ...] = (
    {"id": "critical_safety", "name": "关键安全", "cap": 5.9, "required_evidence": "E2"},
    {"id": "provenance_license", "name": "来源与许可", "cap": 6.4, "required_evidence": "E2"},
    {"id": "real_host_connection", "name": "真实 ChatGPT 宿主联通", "cap": 7.9, "required_evidence": "E4"},
    {"id": "real_task_evidence", "name": "真实任务与对照证据", "cap": 8.4, "required_evidence": "E4"},
    {"id": "manual_accessibility", "name": "人工无障碍验证", "cap": 8.9, "required_evidence": "E4"},
    {"id": "human_release_authorization", "name": "人工发布授权", "cap": 9.2, "required_evidence": "E4"},
)

EVALUATION_WORLD_FIELDS = (
    "skill_version",
    "host_version",
    "adapter_version",
    "model",
    "installed_skill_set",
    "tool_permissions",
    "dataset_id",
    "dataset_version",
    "grader_version",
    "executed_at",
    "external_dependencies",
)


def expert_framework() -> dict[str, Any]:
    return {
        "version": FRAMEWORK_VERSION,
        "review_kind": "internal_simulation",
        "disclaimer": "这些席位由 ChatGPT/Codex 内部模拟，不代表外部专家背书或发布授权。",
        "score_scale": {"minimum": 0, "maximum": 10, "precision": 4},
        "evidence_levels": [
            {"id": "E0", "name": "主张"},
            {"id": "E1", "name": "代码或静态契约"},
            {"id": "E2", "name": "本地自动化验证"},
            {"id": "E3", "name": "真实浏览器渲染与人工截图"},
            {"id": "E4", "name": "真实宿主、真实用户任务或独立验证"},
            {"id": "E5", "name": "持续生产结果"},
        ],
        "dimensions": deepcopy(DIMENSIONS),
        "roles": deepcopy(ROLES),
        "gates": deepcopy(GATES),
        "external_evidence_registry": {
            "status": "unavailable",
            "accepts_client_attestations": False,
            "restricted_gates": sorted(EXTERNAL_EVIDENCE_GATES),
        },
        "evaluation_world_fields": list(EVALUATION_WORLD_FIELDS),
        "rules": [
            "总分是维度加权概览，硬门失败或 Unknown 会限制最高分。",
            "只有绑定精确制品摘要和 Evaluation World 的证据才可入账。",
            "内部模拟评价最多形成 NO_GO、ITERATE 或 READY_FOR_HUMAN_DECISION，不能批准发布。",
            "9.999/10 不是目标门；必须先关闭全部硬门并取得 E4/E5 证据。",
            "可信独立 E4/E5 证据注册表上线前，客户端提交不能让外部硬门通过。",
        ],
    }


def _require_string(payload: dict[str, Any], field: str, maximum: int = 240) -> str:
    value = str(payload.get(field) or "").strip()
    if not value or len(value) > maximum:
        raise AppError("invalid_expert_review", f"{field} 必须为 1–{maximum} 个字符。")
    return value


def _evidence_refs(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise AppError("invalid_expert_review", f"{field} 必须是非空字符串数组。")
    return [item.strip()[:500] for item in value[:50]]


def validate_expert_review(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("framework_version") != FRAMEWORK_VERSION:
        raise AppError("expert_framework_mismatch", "专家矩阵版本不匹配，请刷新框架后重试。", 409)
    if payload.get("review_kind") != "internal_simulation":
        raise AppError("invalid_expert_review_kind", "只接受明确标记为内部模拟的评价。")

    artifact_digest = _require_string(payload, "artifact_digest", 80).lower()
    if not DIGEST_PATTERN.fullmatch(artifact_digest):
        raise AppError("invalid_artifact_digest", "artifact_digest 必须是 sha256: 加 64 位小写十六进制。")

    world = payload.get("evaluation_world")
    if not isinstance(world, dict):
        raise AppError("invalid_evaluation_world", "evaluation_world 必须是对象。")
    normalized_world: dict[str, Any] = {}
    for field in EVALUATION_WORLD_FIELDS:
        value = world.get(field)
        if field in {"installed_skill_set", "tool_permissions", "external_dependencies"}:
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise AppError("invalid_evaluation_world", f"evaluation_world.{field} 必须是字符串数组。")
            normalized_world[field] = [item.strip()[:240] for item in value if item.strip()][:100]
        else:
            normalized_world[field] = _require_string(world, field, 240)

    submitted_dimensions = payload.get("dimensions")
    if not isinstance(submitted_dimensions, dict):
        raise AppError("invalid_expert_dimensions", "dimensions 必须是对象。")
    normalized_dimensions: dict[str, Any] = {}
    raw_score = 0.0
    evidence_indexes: list[int] = []
    for dimension in DIMENSIONS:
        dimension_id = dimension["id"]
        item = submitted_dimensions.get(dimension_id)
        if not isinstance(item, dict):
            raise AppError("invalid_expert_dimensions", f"缺少维度 {dimension_id}。")
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError) as exc:
            raise AppError("invalid_expert_score", f"{dimension_id}.score 必须是数字。") from exc
        if not math.isfinite(score) or score < 0 or score > 10:
            raise AppError("invalid_expert_score", f"{dimension_id}.score 必须在 0–10 之间。")
        evidence_level = str(item.get("evidence_level") or "")
        if evidence_level not in EVIDENCE_LEVELS:
            raise AppError("invalid_evidence_level", f"{dimension_id}.evidence_level 必须是 E0–E5。")
        refs = _evidence_refs(item.get("evidence_refs"), f"{dimension_id}.evidence_refs")
        if evidence_level != "E0" and not refs:
            raise AppError("evidence_required", f"{dimension_id} 的 {evidence_level} 分数必须提供证据引用。")
        normalized_dimensions[dimension_id] = {
            "score": round(score, 4),
            "evidence_level": evidence_level,
            "evidence_refs": refs,
            "note": str(item.get("note") or "").strip()[:1000],
        }
        raw_score += score * int(dimension["weight"]) / 100
        evidence_indexes.append(EVIDENCE_LEVELS.index(evidence_level))

    submitted_gates = payload.get("gates")
    if not isinstance(submitted_gates, dict):
        raise AppError("invalid_expert_gates", "gates 必须是对象。")
    normalized_gates: dict[str, Any] = {}
    capped_score = raw_score
    all_gates_pass = True
    for gate in GATES:
        gate_id = gate["id"]
        item = submitted_gates.get(gate_id)
        if not isinstance(item, dict):
            raise AppError("invalid_expert_gates", f"缺少硬门 {gate_id}。")
        status = str(item.get("status") or "").lower()
        if status not in GATE_STATUSES:
            raise AppError("invalid_gate_status", f"{gate_id}.status 必须是 pass、fail 或 unknown。")
        if gate_id in EXTERNAL_EVIDENCE_GATES and status == "pass":
            raise AppError(
                "external_evidence_unverified",
                f"硬门 {gate_id} 需要可信独立 E4/E5 证据注册表；当前不接受客户端自报通过。",
                409,
            )
        evidence_level = str(item.get("evidence_level") or "E0")
        if evidence_level not in EVIDENCE_LEVELS:
            raise AppError("invalid_evidence_level", f"{gate_id}.evidence_level 必须是 E0–E5。")
        refs = _evidence_refs(item.get("evidence_refs"), f"{gate_id}.evidence_refs")
        if status == "pass" and not refs:
            raise AppError("evidence_required", f"硬门 {gate_id} 通过时必须提供证据引用。")
        required_index = EVIDENCE_LEVELS.index(str(gate["required_evidence"]))
        if status == "pass" and EVIDENCE_LEVELS.index(evidence_level) < required_index:
            raise AppError(
                "gate_evidence_insufficient",
                f"硬门 {gate_id} 至少需要 {gate['required_evidence']} 证据，当前只有 {evidence_level}。",
            )
        if status != "pass":
            all_gates_pass = False
            capped_score = min(capped_score, float(gate["cap"]))
        normalized_gates[gate_id] = {
            "status": status,
            "evidence_level": evidence_level,
            "evidence_refs": refs,
            "note": str(item.get("note") or "").strip()[:1000],
            "score_cap": gate["cap"] if status != "pass" else None,
        }

    findings = payload.get("findings", [])
    proposed_changes = payload.get("proposed_changes", [])
    if not isinstance(findings, list) or not isinstance(proposed_changes, list):
        raise AppError("invalid_expert_review", "findings 与 proposed_changes 必须是数组。")
    findings = [str(item).strip()[:1000] for item in findings if str(item).strip()][:100]
    proposed_changes = [str(item).strip()[:1000] for item in proposed_changes if str(item).strip()][:100]

    evidence_ceiling = EVIDENCE_LEVELS[min(evidence_indexes)]
    capped_score = round(min(raw_score, capped_score), 4)
    raw_score = round(raw_score, 4)
    if not all_gates_pass:
        decision = "NO_GO"
    elif capped_score < 9.999 or evidence_ceiling not in {"E4", "E5"}:
        decision = "ITERATE"
    else:
        decision = "READY_FOR_HUMAN_DECISION"

    return {
        "framework_version": FRAMEWORK_VERSION,
        "review_kind": "internal_simulation",
        "reviewer_label": _require_string(payload, "reviewer_label", 160),
        "artifact_digest": artifact_digest,
        "raw_score": raw_score,
        "capped_score": capped_score,
        "decision": decision,
        "evidence_ceiling": evidence_ceiling,
        "evaluation_world": normalized_world,
        "dimensions": normalized_dimensions,
        "gates": normalized_gates,
        "findings": findings,
        "proposed_changes": proposed_changes,
    }
