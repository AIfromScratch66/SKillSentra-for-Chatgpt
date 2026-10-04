"""Opt-in OpenAI verification with synthetic inputs and an isolated database.

Default: inspect credential availability and write a status report; no network.
Paid calls require both --execute-real and --model. No user project is loaded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai_gateway import AIGateway, make_orchestration_id  # noqa: E402
from app.database import Database  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.repository import Repository  # noqa: E402


CREDENTIAL_ENV = "SKILLSENTRA_OPENAI_API_KEY"
STEPS = {
    "template": ["define goal", "compare references", "define evaluation", "design instructions", "generate candidate", "test candidate", "improve candidate", "review delivery", "plan updates"],
    "existing": ["inspect baseline", "evaluate baseline", "propose improvements", "update candidate", "regression review", "review upgrade", "review updates"],
}


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Verify real OpenAI collaboration using synthetic data. Default makes no API calls.",
        epilog="Examples: python scripts/verify_real_ai.py | python scripts/verify_real_ai.py --execute-real --model YOUR_MODEL --step-limit 1",
    )
    command.add_argument("--execute-real", action="store_true", help="Explicitly enable paid OpenAI Responses requests (up to 48 calls).")
    command.add_argument("--model", help="Exact accessible OpenAI model to use; required with --execute-real.")
    command.add_argument("--step-limit", type=int, default=16, metavar="1..16", help="Total steps across template (9) then existing (7); 1 makes three role calls.")
    command.add_argument("--output", type=Path, default=ROOT / "output" / "real-ai-verification.json", help="JSON report destination (default: output/real-ai-verification.json).")
    return command


def save_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Checkpoints preserve receipts if a later call fails or is interrupted.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.stem + "-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def public_receipt(run: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id", "route", "step_index", "role", "status", "model", "provider_model", "provider_request_id",
        "transport_request_id", "provider_response_status", "usage_status", "usage_source",
        "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_output_tokens",
        "estimated_cost", "currency", "pricebook_version", "cost_status", "output_sha256", "error_code",
        "duration_ms", "created_at", "finished_at",
    )
    receipt = {name: run.get(name) for name in fields}
    receipt["run_id"] = receipt.pop("id")
    receipt["response_id"] = receipt.pop("provider_request_id")
    receipt["actual_model"] = receipt.pop("provider_model")
    receipt["requested_model"] = receipt.pop("model")
    receipt["source"] = "openai_responses_api"
    return receipt


def collect_receipts(report: dict[str, Any], repository: Repository, project_ids: list[str]) -> None:
    runs = [run for project_id in project_ids for run in repository.list_ai_runs(project_id, 200)]
    runs.sort(key=lambda run: run["created_at"])
    report["runs"] = [public_receipt(run) for run in runs]
    report["real_calls"] = sum(run.get("cost_status") != "not_sent" for run in runs)
    report["confirmed_provider_responses"] = sum(bool(run.get("provider_request_id")) for run in runs)
    report["completed_calls"] = sum(run.get("status") == "completed" for run in runs)
    report["unknown_usage_run_count"] = sum(run.get("usage_status") != "reported" for run in runs)
    report["usage_complete"] = bool(runs) and report["unknown_usage_run_count"] == 0
    report["known_token_subtotals"] = {field: sum(int(run.get(field) or 0) for run in runs) for field in ("input_tokens", "output_tokens", "total_tokens")}
    report["tokens"] = dict(report["known_token_subtotals"]) if report["usage_complete"] else None
    costs_known = bool(runs) and all(run.get("estimated_cost") is not None for run in runs)
    subtotal = round(sum(float(run.get("estimated_cost") or 0) for run in runs), 8)
    report["known_estimated_cost_subtotal"] = subtotal
    report["estimated_cost"] = subtotal if costs_known else None


def verify(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    credential_present = bool(os.environ.get(CREDENTIAL_ENV, "").strip())
    report: dict[str, Any] = {
        "schema_version": "skillsentra-real-ai-verification-v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "blocked" if not credential_present else "ready_not_executed",
        "reason": "credential_unavailable" if not credential_present else "explicit_execution_required",
        "credential_present": credential_present, "execute_requested": bool(args.execute_real),
        "endpoint": "https://api.openai.com/v1", "requested_model": args.model,
        "planned_steps": args.step_limit, "planned_role_calls": args.step_limit * 3,
        "completed_steps": 0, "real_calls": 0, "confirmed_provider_responses": 0,
        "tokens": None, "estimated_cost": None, "runs": [],
        "data_source": "synthetic_verification_inputs", "isolated_database": True,
        "chatgpt_host_verified": False,
        "evidence_boundary": "OpenAI API verification does not prove a ChatGPT host callback or production user outcome.",
        "cost_note": "Costs are estimates from the configured local pricebook, not an OpenAI invoice. Unknown usage is not zero.",
    }
    if not credential_present or not args.execute_real:
        save_report(report, args.output)
        return report, 2 if not credential_present else 0

    report.update(status="running", reason="explicit_real_execution")
    exit_code = 0
    save_report(report, args.output)
    with tempfile.TemporaryDirectory(prefix="skillsentra-real-ai-") as directory:
        repository: Repository | None = None
        project_ids: list[str] = []
        try:
            database = Database(Path(directory) / "verification.db")
            database.migrate()
            repository = Repository(database)
            gateway = AIGateway(repository)
            report.update(currency=gateway.pricebook.currency, pricebook_version=gateway.pricebook.version)
            save_report(report, args.output)
            connection = gateway.create_connection({"provider": "openai", "display_name": "Isolated real API verification", "credential_env": CREDENTIAL_ENV})
            tested = gateway.test_connection(connection["id"])
            if args.model not in {item["id"] for item in tested["model_catalog"]}:
                raise AppError("verification_model_unavailable", "Requested model is not present in the accessible OpenAI model catalog.", 409)
            remaining = args.step_limit
            for route, labels in STEPS.items():
                if remaining <= 0:
                    break
                project = repository.create_project(f"Synthetic API verification: {route}", route)
                project_ids.append(project["id"])
                repository.update_policy(project["id"], {
                    "connection_id": connection["id"], "creator_model": args.model,
                    "evaluator_model": args.model, "safety_model": args.model,
                    "fallback_enabled": False, "daily_budget": 20,
                })
                previous_step: list[dict[str, str]] = []
                for index, label in enumerate(labels[:remaining]):
                    orchestration_id = make_orchestration_id()
                    prior_roles: list[dict[str, str]] = []
                    for role in ("creator", "evaluator", "safety"):
                        run = gateway.run(project["id"], {
                            "route": route, "step_index": index, "require_real": True, "role": role,
                            "orchestration_id": orchestration_id,
                            "task": f"Synthetic verification only. {label}. Help design a Skill that converts fictional task labels into a plain checklist. Keep your answer below 100 words. Do not claim safety or release approval.",
                            "context": {"example_input": ["alpha task", "beta task"], "expected_example": "- alpha task\n- beta task", "previous_step_outputs": previous_step, "prior_role_outputs": prior_roles},
                        })
                        collect_receipts(report, repository, project_ids)
                        save_report(report, args.output)
                        if run.get("usage_status") != "reported":
                            raise AppError("verification_usage_incomplete", "A provider response lacked complete valid token usage; verification stopped.", 502)
                        prior_roles.append({"role": role, "summary": run["output"]["summary"]})
                    previous_step = prior_roles
                    report["completed_steps"] += 1
                    remaining -= 1
                    collect_receipts(report, repository, project_ids)
                    save_report(report, args.output)
            report.update(status="verified_real_api", reason="all_requested_steps_completed_with_provider_receipts")
        except AppError as error:
            report.update(status="failed", reason=error.code, error_message=error.message)
            exit_code = 1
        except KeyboardInterrupt:
            report.update(status="interrupted", reason="operator_interrupted")
            exit_code = 130
        except Exception as error:
            # Exception text can contain transport configuration; record type only.
            report.update(status="failed", reason="verification_internal_error", error_type=type(error).__name__)
            exit_code = 1
        finally:
            if repository is not None:
                collect_receipts(report, repository, project_ids)
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            save_report(report, args.output)
    return report, exit_code


def main(argv: list[str] | None = None) -> int:
    command = parser()
    args = command.parse_args(argv)
    if not 1 <= args.step_limit <= 16:
        command.error("--step-limit must be between 1 and 16")
    if args.execute_real and (not args.model or not args.model.strip()):
        command.error("--execute-real requires an explicit --model")
    if args.output.suffix.lower() != ".json":
        command.error("--output must name a .json report")
    report, exit_code = verify(args)
    print(json.dumps({"status": report["status"], "reason": report["reason"], "real_calls": report["real_calls"], "tokens": report["tokens"], "report": str(args.output)}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
