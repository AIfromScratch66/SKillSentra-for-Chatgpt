from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import AppError


EVAL_SCHEMA = "skillsentra-eval-v2"
EVIDENCE_KIND = "synthetic_static_contract"
EVIDENCE_LEVEL = "E2"
GRADER_VERSION = "skillsentra-static-contract-grader-v1"
ALLOWED_SPLITS = ("golden", "challenge", "incident")
STAGE_SPLITS = {
    "evaluation": ("golden",),
    "regression": ("challenge", "incident"),
    "baseline": ALLOWED_SPLITS,
}
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
CASE_ID_PATTERN = re.compile(r"^case-(?:golden|challenge|incident)-[0-9a-f]{16}$")
GRADER_SPEC = {
    "version": GRADER_VERSION,
    "normalization": "unicode-nfkc-casefold-whitespace-v1",
    "rule": "all declared contract_fragments must occur in the frozen Skill text",
    "execution": "no model, tool, script, network, user, or task output is executed",
}


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def normalize_case_input(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def case_input_digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(normalize_case_input(value).encode('utf-8')).hexdigest()}"


def stable_case_id(case: dict[str, Any]) -> str:
    identity = {
        "split": case.get("split"),
        "input_digest": case.get("input_digest"),
        "expected_behavior": case.get("expected_behavior"),
        "slice": case.get("slice"),
        "source": case.get("source"),
        "provenance": case.get("provenance"),
        "contract_fragments": case.get("contract_fragments"),
    }
    suffix = canonical_digest(identity).split(":", 1)[1][:16]
    return f"case-{case.get('split', 'unknown')}-{suffix}"


def compute_dataset_digest(pack: dict[str, Any]) -> str:
    unsigned = deepcopy(pack)
    unsigned.pop("dataset_digest", None)
    return canonical_digest(unsigned)


def _language_slice(*values: str) -> str:
    combined = " ".join(values)
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", combined))
    has_latin = bool(re.search(r"[A-Za-z]", combined))
    if has_cjk and has_latin:
        return "mixed"
    return "zh" if has_cjk else "en"


def _make_case(
    split: str,
    input_text: str,
    expected_behavior: str,
    task_kind: str,
    risk: str,
    source_field: str,
    contract_fragments: list[str],
) -> dict[str, Any]:
    clean_input = input_text.strip()
    clean_fragments = [item.strip() for item in contract_fragments if item.strip()]
    case: dict[str, Any] = {
        "split": split,
        "input": clean_input,
        "input_digest": case_input_digest(clean_input),
        "expected_behavior": expected_behavior,
        "slice": {
            "language": _language_slice(clean_input, *clean_fragments),
            "risk": risk,
            "task_kind": task_kind,
        },
        "source": {"kind": "template_payload", "field": source_field},
        "provenance": {
            "origin": "user_supplied_or_explicit_template_fallback",
            "generator": EVAL_SCHEMA,
        },
        "contract_fragments": clean_fragments,
    }
    case["case_id"] = stable_case_id(case)
    return case


def build_template_eval_pack(
    *,
    skill_name: str,
    trigger: str,
    success: str,
    non_goal: str,
    failure: str,
    must_not: str,
) -> dict[str, Any]:
    cases = [
        _make_case(
            "golden",
            trigger,
            "trigger_and_follow_output_contract",
            "positive_trigger",
            "low",
            "trigger+success",
            [trigger, success],
        ),
        _make_case(
            "challenge",
            non_goal,
            "do_not_trigger",
            "negative_trigger",
            "boundary",
            "nonGoal",
            [non_goal],
        ),
        _make_case(
            "incident",
            failure,
            "stop_or_report_unknown",
            "failure_handling",
            "high",
            "failure+mustNot",
            [failure, must_not],
        ),
    ]
    pack: dict[str, Any] = {
        "schema": EVAL_SCHEMA,
        "dataset_id": f"{skill_name}-synthetic-static-contract",
        "dataset_version": "2",
        "evidence_kind": EVIDENCE_KIND,
        "evidence_level": EVIDENCE_LEVEL,
        "limitations": [
            "Only frozen text-contract presence is checked.",
            "No model, host, tool, script, real task, or real user is executed.",
            "These local synthetic checks do not establish task-quality improvement or E4/E5 evidence.",
        ],
        "cases": cases,
    }
    pack["dataset_digest"] = compute_dataset_digest(pack)
    return pack


def _require_text(value: Any, field: str, maximum: int = 2_000) -> str:
    if not isinstance(value, str):
        raise AppError("eval_pack_invalid", f"{field} 必须是字符串。")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise AppError("eval_pack_invalid", f"{field} 必须为 1–{maximum} 个字符。")
    return normalized


def validate_eval_pack(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AppError("eval_pack_invalid", "Eval Pack 必须是 JSON 对象。")
    if payload.get("schema") != EVAL_SCHEMA:
        raise AppError("eval_schema_mismatch", f"Eval Pack schema 必须是 {EVAL_SCHEMA}。", 409)
    _require_text(payload.get("dataset_id"), "dataset_id", 160)
    _require_text(payload.get("dataset_version"), "dataset_version", 80)
    if payload.get("evidence_kind") != EVIDENCE_KIND or payload.get("evidence_level") != EVIDENCE_LEVEL:
        raise AppError("eval_pack_invalid", "Eval Pack 必须明确标记为本地合成静态契约 E2。")
    limitations = payload.get("limitations")
    if not isinstance(limitations, list) or not limitations or any(not isinstance(item, str) or not item.strip() for item in limitations):
        raise AppError("eval_pack_invalid", "limitations 必须是非空字符串数组。")
    supplied_digest = payload.get("dataset_digest")
    if not isinstance(supplied_digest, str) or not DIGEST_PATTERN.fullmatch(supplied_digest):
        raise AppError("eval_pack_invalid", "dataset_digest 必须是 SHA-256 摘要。")
    expected_digest = compute_dataset_digest(payload)
    if supplied_digest != expected_digest:
        raise AppError(
            "eval_pack_tampered",
            "Eval Pack 内容与冻结 dataset_digest 不一致。",
            409,
            {"expected": expected_digest, "actual": supplied_digest},
        )

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise AppError("eval_pack_invalid", "Eval Pack cases 必须是非空数组。")
    seen_ids: set[str] = set()
    seen_inputs: dict[str, str] = {}
    present_splits: set[str] = set()
    for index, item in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(item, dict):
            raise AppError("eval_pack_invalid", f"{prefix} 必须是对象。")
        split = _require_text(item.get("split"), f"{prefix}.split", 40)
        if split not in ALLOWED_SPLITS:
            raise AppError("eval_pack_invalid", f"{prefix}.split 不受支持。")
        present_splits.add(split)
        input_text = _require_text(item.get("input"), f"{prefix}.input")
        input_digest = item.get("input_digest")
        if input_digest != case_input_digest(input_text):
            raise AppError("eval_pack_tampered", f"{prefix}.input_digest 与规范化输入不一致。", 409)
        expected_behavior = _require_text(item.get("expected_behavior"), f"{prefix}.expected_behavior", 120)
        if expected_behavior not in {
            "trigger_and_follow_output_contract",
            "do_not_trigger",
            "stop_or_report_unknown",
        }:
            raise AppError("eval_pack_invalid", f"{prefix}.expected_behavior 不受支持。")
        slice_value = item.get("slice")
        if not isinstance(slice_value, dict):
            raise AppError("eval_pack_invalid", f"{prefix}.slice 必须是对象。")
        for field in ("language", "risk", "task_kind"):
            _require_text(slice_value.get(field), f"{prefix}.slice.{field}", 80)
        source = item.get("source")
        provenance = item.get("provenance")
        if not isinstance(source, dict) or not isinstance(provenance, dict):
            raise AppError("eval_pack_invalid", f"{prefix} 必须包含 source 和 provenance 对象。")
        _require_text(source.get("kind"), f"{prefix}.source.kind", 80)
        _require_text(source.get("field"), f"{prefix}.source.field", 120)
        _require_text(provenance.get("origin"), f"{prefix}.provenance.origin", 160)
        _require_text(provenance.get("generator"), f"{prefix}.provenance.generator", 120)
        fragments = item.get("contract_fragments")
        if not isinstance(fragments, list) or not fragments or any(not isinstance(value, str) or not value.strip() for value in fragments):
            raise AppError("eval_pack_invalid", f"{prefix}.contract_fragments 必须是非空字符串数组。")
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or not CASE_ID_PATTERN.fullmatch(case_id) or case_id != stable_case_id(item):
            raise AppError("eval_pack_tampered", f"{prefix}.case_id 与冻结案例内容不一致。", 409)
        if case_id in seen_ids:
            raise AppError("eval_duplicate_case", "Eval Pack 包含重复 case_id。", 409, {"case_id": case_id})
        seen_ids.add(case_id)
        previous_split = seen_inputs.get(input_digest)
        if previous_split is not None and previous_split != split:
            raise AppError(
                "eval_contamination_detected",
                "规范化任务输入同时出现在不同 split，已阻止污染评价。",
                409,
                {"input_digest": input_digest, "splits": sorted({previous_split, split})},
            )
        if previous_split is not None:
            raise AppError("eval_duplicate_case", "同一 split 包含重复规范化任务输入。", 409, {"input_digest": input_digest})
        seen_inputs[input_digest] = split

    missing = [split for split in ALLOWED_SPLITS if split not in present_splits]
    if missing:
        raise AppError("eval_split_missing", "Eval Pack 缺少必需 split。", 409, {"missing": missing})
    return deepcopy(payload)


def load_eval_pack(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AppError("eval_pack_missing", "当前候选缺少 evals/cases.json。", 409)
    if path.stat().st_size > 1_000_000:
        raise AppError("eval_pack_invalid", "Eval Pack 超过 1 MB 上限。", 413)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError("eval_pack_invalid", "Eval Pack 不是有效 UTF-8 JSON。") from exc
    return validate_eval_pack(payload)


def _slice_counts(results: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, int]]]:
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for dimension in ("language", "risk", "task_kind"):
        values = sorted({str(item["slice"][dimension]) for item in results})
        counts[dimension] = {}
        for value in values:
            selected = [item for item in results if item["slice"][dimension] == value]
            passed = sum(item["status"] == "passed" for item in selected)
            counts[dimension][value] = {
                "total": len(selected),
                "passed": passed,
                "failed": len(selected) - passed,
            }
    return counts


def evaluate_static_contract(
    pack: dict[str, Any],
    *,
    skill_text: str,
    artifact_digest: str,
    stage: str,
) -> dict[str, Any]:
    frozen = validate_eval_pack(pack)
    if stage not in STAGE_SPLITS:
        raise AppError("invalid_validation_stage", "Eval Pack 不支持该验证阶段。")
    if not DIGEST_PATTERN.fullmatch(artifact_digest):
        raise AppError("invalid_artifact_digest", "评价制品必须绑定 SHA-256 摘要。")
    selected_splits = STAGE_SPLITS[stage]
    selected = sorted(
        (item for item in frozen["cases"] if item["split"] in selected_splits),
        key=lambda item: item["case_id"],
    )
    if not selected:
        raise AppError("eval_split_missing", "当前阶段没有可执行的冻结案例。", 409, {"stage": stage})

    normalized_skill = normalize_case_input(skill_text)
    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for case in selected:
        missing_fragments = [
            fragment
            for fragment in case["contract_fragments"]
            if normalize_case_input(fragment) not in normalized_skill
        ]
        passed = not missing_fragments
        result = {
            "case_id": case["case_id"],
            "split": case["split"],
            "slice": deepcopy(case["slice"]),
            "input_digest": case["input_digest"],
            "expected_behavior": case["expected_behavior"],
            "status": "passed" if passed else "failed",
            "missing_contract_fragment_digests": [case_input_digest(item) for item in missing_fragments],
        }
        results.append(result)
        if not passed:
            findings.append(
                {
                    "severity": "high",
                    "code": "EVAL_CONTRACT_CASE_FAILED",
                    "summary": f"冻结 {case['split']} 案例缺少声明的静态契约。",
                    "remediation": "恢复对应适用范围、禁止范围或失败处理契约后重跑。",
                    "path": f"evals/cases.json#{case['case_id']}",
                }
            )

    passed_count = sum(item["status"] == "passed" for item in results)
    score = round(100 * passed_count / len(results), 1)
    slices = _slice_counts(results)
    grader_digest = canonical_digest(GRADER_SPEC)
    world = {
        "artifact_digest": artifact_digest,
        "dataset_digest": frozen["dataset_digest"],
        "grader_digest": grader_digest,
        "grader_version": GRADER_VERSION,
        "stage": stage,
        "selected_splits": list(selected_splits),
        "scripts_executed": False,
    }
    world_digest = canonical_digest(world)
    result_core = {
        "dataset_digest": frozen["dataset_digest"],
        "grader_digest": grader_digest,
        "world_digest": world_digest,
        "stage": stage,
        "score": score,
        "case_results": results,
        "slice_counts": slices,
    }
    result_digest = canonical_digest(result_core)
    evidence = {
        "evidence_kind": EVIDENCE_KIND,
        "evidence_level": EVIDENCE_LEVEL,
        "claim_scope": "frozen synthetic static-contract conformance only",
        "real_task_performance": "unknown",
        "real_user_evidence": "unknown",
        "production_evidence": "unknown",
        "limitations": deepcopy(frozen["limitations"]),
        "dataset": {
            "id": frozen["dataset_id"],
            "version": frozen["dataset_version"],
            "digest": frozen["dataset_digest"],
            "selected_splits": list(selected_splits),
            "case_count": len(results),
        },
        "grader": {
            "version": GRADER_VERSION,
            "digest": grader_digest,
            "spec": deepcopy(GRADER_SPEC),
        },
        "world": {**world, "digest": world_digest},
        "case_results": results,
        "slice_counts": slices,
        "result_digest": result_digest,
    }
    return {
        "status": "passed" if passed_count == len(results) else "failed",
        "score": score,
        "findings": findings,
        "evidence": evidence,
    }
