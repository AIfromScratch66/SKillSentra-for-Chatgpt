from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .database import Database, json_dumps, utc_now
from .errors import AppError
from .repository import Repository, make_id
from .skill_engine import SkillEngine, canonical_manifest


ARTIFACT_CANON = "artifact-canon-v1"
DIGEST_ALGORITHM = "sha256"
GITHUB_REPOSITORY_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]{1,100})/(?P<repo>[A-Za-z0-9_.-]{1,100})(?:\.git)?/?$"
)
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ARTIFACT_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    auth_mode: str = "local"

    def require(self, *roles: str) -> None:
        if "admin" in self.roles or self.roles.intersection(roles):
            return
        raise AppError("forbidden", "当前身份没有执行此操作的权限。", 403, {"required_roles": list(roles)})


class TokenAuthenticator:
    """Server-side token registry.

    The registry is loaded from SKILLSENTRA_AUTH_TOKENS_JSON. Raw tokens are never
    returned or persisted. A non-loopback deployment must opt into token mode.
    """

    def __init__(self, mode: str = "local", registry_json: str = ""):
        self.mode = mode
        self._registry: dict[str, Principal] = {}
        if mode in {"token", "hybrid"}:
            try:
                registry = json.loads(registry_json or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("SKILLSENTRA_AUTH_TOKENS_JSON must be valid JSON") from exc
            if not isinstance(registry, dict) or (mode == "token" and not registry):
                raise ValueError("Token authentication requires at least one configured token")
            for token, value in registry.items():
                if not isinstance(token, str) or len(token) < 24 or not isinstance(value, dict):
                    raise ValueError("Each auth token must be at least 24 characters and map to an object")
                tenant_id = str(value.get("tenant_id") or "").strip()
                actor_id = str(value.get("actor_id") or "").strip()
                roles = value.get("roles") or []
                if not tenant_id or not actor_id or not isinstance(roles, list) or not roles:
                    raise ValueError("Each auth record requires tenant_id, actor_id and roles")
                digest = self._digest(token)
                self._registry[digest] = Principal(tenant_id, actor_id, frozenset(str(item) for item in roles), "token")

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authenticate(self, authorization: str) -> Principal:
        if self.mode == "local":
            return Principal("ten_demo", "local-admin", frozenset({"admin", "security", "finance", "operator"}))
        if self.mode == "accounts":
            raise AppError("authentication_required", "请先注册或登录。", 401)
        if not authorization.startswith("Bearer "):
            raise AppError("authentication_required", "请提供有效访问令牌。", 401)
        token = authorization.removeprefix("Bearer ").strip()
        principal = self._registry.get(self._digest(token))
        if principal is None:
            raise AppError("authentication_failed", "访问令牌无效或已撤销。", 401)
        return principal


class GitHubArchiveIngestor:
    """Resolve a GitHub ref and statically unpack one immutable archive."""

    def __init__(self, timeout: float = 20, compressed_limit: int = 25_000_000, expanded_limit: int = 50_000_000):
        self.timeout = timeout
        self.compressed_limit = compressed_limit
        self.expanded_limit = expanded_limit

    def fetch(self, owner: str, repo: str, ref: str, work_root: Path) -> tuple[str, Path]:
        ref = self._validate_ref(ref)
        token = os.environ.get("SKILLSENTRA_GITHUB_TOKEN", "").strip()
        commit_payload = self._request_json(f"https://api.github.com/repos/{owner}/{repo}/commits/{urllib.parse.quote(ref, safe='')}", token)
        commit = str(commit_payload.get("sha") or "").lower()
        if not HEX_SHA_RE.fullmatch(commit):
            raise AppError("github_invalid_commit", "GitHub 未返回可固定的 commit。", 502)
        archive_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{commit}"
        archive_path = work_root / "archive.zip"
        self._download(archive_url, archive_path, token)
        extract_root = work_root / "expanded"
        extract_root.mkdir(parents=True, exist_ok=True)
        self._extract_zip(archive_path, extract_root)
        skill_files = sorted(extract_root.rglob("SKILL.md"))
        if not skill_files:
            raise AppError("skill_not_found", "仓库中没有发现 SKILL.md。", 409)
        if len(skill_files) > 50:
            raise AppError("skill_discovery_ambiguous", "仓库包含过多 Skill，需要缩小扫描路径。", 409)
        return commit, skill_files[0].parent

    @staticmethod
    def _validate_ref(ref: str) -> str:
        ref = (ref or "HEAD").strip()
        if not SAFE_REF_RE.fullmatch(ref) or ".." in ref or ref.startswith("/") or ref.endswith("/"):
            raise AppError("github_invalid_ref", "GitHub ref 格式不正确。")
        return ref

    def _headers(self, token: str) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "SkillSentra/0.3",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request_json(self, url: str, token: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=self._headers(token))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except urllib.error.HTTPError as exc:
            code = "github_not_authorized" if exc.code in {401, 403, 404} else "github_unavailable"
            status = 409 if exc.code in {401, 403, 404} else 502
            raise AppError(code, "无法读取该 GitHub 仓库或 ref。", status, {"provider_status": exc.code}) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AppError("github_unavailable", "GitHub 当前不可达，请稍后重试。", 502) from exc
        if len(raw) > 2_000_000:
            raise AppError("github_response_too_large", "GitHub 元数据响应超过限制。", 502)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("github_invalid_response", "GitHub 返回了无效响应。", 502) from exc
        if not isinstance(payload, dict):
            raise AppError("github_invalid_response", "GitHub 返回了无效响应。", 502)
        return payload

    def _download(self, url: str, target: Path, token: str) -> None:
        request = urllib.request.Request(url, headers=self._headers(token))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response, target.open("wb") as output:
                total = 0
                while True:
                    chunk = response.read(65_536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.compressed_limit:
                        raise AppError("github_archive_too_large", "仓库压缩包超过 25 MB 限制。", 413)
                    output.write(chunk)
        except AppError:
            raise
        except urllib.error.HTTPError as exc:
            raise AppError("github_archive_failed", "无法下载固定 commit 的仓库归档。", 502, {"provider_status": exc.code}) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AppError("github_archive_failed", "下载仓库归档超时。", 502) from exc

    def _extract_zip(self, archive: Path, target: Path) -> None:
        total = 0
        count = 0
        seen: set[str] = set()
        try:
            package = zipfile.ZipFile(archive)
        except zipfile.BadZipFile as exc:
            raise AppError("github_archive_invalid", "GitHub 仓库归档不是有效 ZIP。", 502) from exc
        with package:
            for info in package.infolist():
                count += 1
                if count > 1_500:
                    raise AppError("github_archive_file_limit", "仓库文件数超过 1,500 个限制。", 413)
                pure = PurePosixPath(info.filename)
                if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                    raise AppError("github_archive_path_escape", "仓库归档包含不安全路径。", 409)
                normalized = pure.as_posix().casefold()
                if normalized in seen:
                    raise AppError("github_archive_path_collision", "仓库归档包含重复规范路径。", 409)
                seen.add(normalized)
                mode = info.external_attr >> 16
                if mode & 0o170000 == 0o120000:
                    raise AppError("github_archive_symlink", "仓库归档包含符号链接。", 409)
                total += info.file_size
                if total > self.expanded_limit:
                    raise AppError("github_archive_expanded_limit", "仓库展开后超过 50 MB 限制。", 413)
                if info.compress_size > 0 and info.file_size / info.compress_size > 100:
                    raise AppError("github_archive_ratio_limit", "仓库归档压缩比异常。", 409)
                destination = target.joinpath(*pure.parts).resolve()
                try:
                    destination.relative_to(target.resolve())
                except ValueError as exc:
                    raise AppError("github_archive_path_escape", "仓库归档尝试写出隔离目录。", 409) from exc
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(info) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, 65_536)


class ControlPlane:
    def __init__(self, database: Database, repository: Repository, skill_engine: SkillEngine):
        self.database = database
        self.repository = repository
        self.skill_engine = skill_engine
        self.github = GitHubArchiveIngestor()

    def ensure_tenant(self, principal: Principal) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tenants WHERE id = ?", (principal.tenant_id,)).fetchone()
            if row is None:
                slug = re.sub(r"[^a-z0-9-]+", "-", principal.tenant_id.lower()).strip("-") or "workspace"
                suffix = 1
                candidate = slug
                while connection.execute("SELECT 1 FROM tenants WHERE slug = ?", (candidate,)).fetchone():
                    suffix += 1
                    candidate = f"{slug}-{suffix}"
                connection.execute(
                    "INSERT INTO tenants(id, name, slug, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (principal.tenant_id, "SkillSentra Workspace", candidate, now, now),
                )
                self._audit(connection, principal, "tenant.created", "tenant", principal.tenant_id, {"slug": candidate})
            self._ensure_finance_rules(connection, principal.tenant_id, now)
            row = connection.execute("SELECT * FROM tenants WHERE id = ?", (principal.tenant_id,)).fetchone()
        return dict(row)

    def overview(self, principal: Principal) -> dict[str, Any]:
        self.ensure_tenant(principal)
        with self.database.session() as connection:
            tenant = dict(connection.execute("SELECT * FROM tenants WHERE id = ?", (principal.tenant_id,)).fetchone())
            counts = {
                "repositories": self._count(connection, "repositories", principal.tenant_id),
                "snapshots": self._count(connection, "repository_snapshots", principal.tenant_id),
                "evidence": self._count(connection, "evidence_attestations", principal.tenant_id),
                "agents": self._count(connection, "agents", principal.tenant_id),
                "deployments": self._count(connection, "deployments", principal.tenant_id),
                "qualified_receipts": int(connection.execute("SELECT COUNT(*) AS n FROM usage_receipts WHERE tenant_id = ? AND status = 'qualified'", (principal.tenant_id,)).fetchone()["n"]),
                "active_revocations": int(connection.execute("SELECT COUNT(*) AS n FROM revocations WHERE tenant_id = ? AND status = 'active'", (principal.tenant_id,)).fetchone()["n"]),
            }
            risk = connection.execute(
                "SELECT severity, COUNT(*) AS n FROM evidence_attestations WHERE tenant_id = ? AND status IN ('warning','fail') GROUP BY severity",
                (principal.tenant_id,),
            ).fetchall()
            evidence_status_rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM evidence_attestations WHERE tenant_id = ? GROUP BY status",
                (principal.tenant_id,),
            ).fetchall()
            evidence_by_status = {row["status"]: int(row["n"]) for row in evidence_status_rows}
            snapshot_status_rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM repository_snapshots WHERE tenant_id = ? GROUP BY status",
                (principal.tenant_id,),
            ).fetchall()
            snapshots_by_status = {row["status"]: int(row["n"]) for row in snapshot_status_rows}
            deployment_status_rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM deployments WHERE tenant_id = ? GROUP BY status",
                (principal.tenant_id,),
            ).fetchall()
            deployments_by_status = {row["status"]: int(row["n"]) for row in deployment_status_rows}
            drift = int(connection.execute("SELECT COUNT(*) AS n FROM deployments WHERE tenant_id = ? AND status = 'drifted'", (principal.tenant_id,)).fetchone()["n"])
            ledger = connection.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN l.side='debit' THEN l.amount_minor ELSE -l.amount_minor END), 0) AS balance
                FROM journal_lines l JOIN journal_transactions t ON t.id=l.transaction_id WHERE t.tenant_id=?
                """,
                (principal.tenant_id,),
            ).fetchone()
        ledger_balance = int(ledger["balance"])
        hard_blockers = (
            counts["active_revocations"]
            + snapshots_by_status.get("blocked", 0)
            + evidence_by_status.get("fail", 0)
            + drift
            + int(ledger_balance != 0)
        )
        review_items = (
            snapshots_by_status.get("review_required", 0)
            + evidence_by_status.get("warning", 0)
            + evidence_by_status.get("unknown", 0)
        )
        checks = {
            "artifact_frozen": snapshots_by_status.get("scanned", 0) > 0,
            "evidence_clear": (
                counts["evidence"] > 0
                and counts["active_revocations"] == 0
                and snapshots_by_status.get("blocked", 0) == 0
                and evidence_by_status.get("fail", 0) == 0
                and review_items == 0
            ),
            "runtime_observed": deployments_by_status.get("observed", 0) > 0 and drift == 0,
            "usage_reconciled": counts["qualified_receipts"] > 0 and ledger_balance == 0,
        }
        passed_checks = sum(1 for passed in checks.values() if passed)
        if hard_blockers:
            readiness_status = "blocked"
        elif passed_checks == len(checks) and review_items == 0:
            readiness_status = "ready"
        else:
            readiness_status = "review"

        if counts["active_revocations"] or snapshots_by_status.get("blocked", 0) or evidence_by_status.get("fail", 0):
            next_step = {"code": "resolve_security_blockers", "section": "security"}
        elif drift:
            next_step = {"code": "resolve_agent_drift", "section": "agents"}
        elif ledger_balance != 0:
            next_step = {"code": "reconcile_ledger", "section": "settlement"}
        elif not checks["artifact_frozen"]:
            next_step = {"code": "freeze_artifact", "section": "repositories"}
        elif review_items or not checks["evidence_clear"]:
            next_step = {"code": "review_evidence", "section": "security"}
        elif not checks["runtime_observed"]:
            next_step = {"code": "observe_agent", "section": "agents"}
        elif not checks["usage_reconciled"]:
            next_step = {"code": "qualify_usage", "section": "settlement"}
        else:
            next_step = {"code": "ready", "section": "overview"}
        return {
            "tenant": tenant,
            "principal": {"actor_id": principal.actor_id, "roles": sorted(principal.roles), "auth_mode": principal.auth_mode},
            "counts": counts,
            "risk_by_severity": {row["severity"]: row["n"] for row in risk},
            "evidence_by_status": evidence_by_status,
            "snapshots_by_status": snapshots_by_status,
            "deployments_by_status": deployments_by_status,
            "drifted_deployments": drift,
            "ledger_balance_minor": ledger_balance,
            "readiness": {
                "status": readiness_status,
                "score": passed_checks * 25,
                "passed_checks": passed_checks,
                "total_checks": len(checks),
                "hard_blockers": hard_blockers,
                "review_items": review_items,
                "checks": checks,
                "next_step": next_step,
            },
            "production_payout": False,
            "service_level": "private_beta",
        }

    def list_repositories(self, principal: Principal) -> list[dict[str, Any]]:
        self.ensure_tenant(principal)
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT r.*, (SELECT COUNT(*) FROM repository_snapshots s WHERE s.repository_id=r.id) AS snapshot_count,
                       (SELECT s.artifact_digest FROM repository_snapshots s WHERE s.repository_id=r.id ORDER BY s.created_at DESC LIMIT 1) AS latest_digest
                FROM repositories r WHERE r.tenant_id=? ORDER BY r.updated_at DESC
                """,
                (principal.tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_repository(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator")
        self.ensure_tenant(principal)
        url = str(payload.get("url") or "").strip()
        source_id = str(payload.get("source_id") or "").strip()
        if source_id:
            source = self.repository.get_skill_source(source_id, principal.tenant_id)
            if source is None:
                raise AppError("skill_source_not_found", "授权 Skill 来源不存在。", 404)
            provider = "local-fixture"
            owner = "local"
            name = str(source["name"])
            url = f"local://{source_id}"
            visibility = "internal"
        else:
            match = GITHUB_REPOSITORY_RE.fullmatch(url)
            if not match:
                raise AppError("invalid_github_url", "请输入 https://github.com/owner/repository 格式的地址。")
            provider = "github"
            owner = match.group("owner")
            name = match.group("repo").removesuffix(".git")
            visibility = str(payload.get("visibility") or "public")
            if visibility not in {"public", "private", "internal"}:
                raise AppError("invalid_repository_visibility", "仓库可见性不正确。")
        repository_id = make_id("repo")
        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM repositories WHERE tenant_id=? AND provider=? AND owner=? AND name=?",
                (principal.tenant_id, provider, owner, name),
            ).fetchone()
            if existing:
                row = connection.execute("SELECT * FROM repositories WHERE id=?", (existing["id"],)).fetchone()
                return dict(row)
            connection.execute(
                """
                INSERT INTO repositories(id, tenant_id, provider, owner, name, source_url, visibility, default_branch, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (repository_id, principal.tenant_id, provider, owner, name, url, visibility, str(payload.get("default_branch") or "main"), now, now),
            )
            self._audit(connection, principal, "repository.created", "repository", repository_id, {"provider": provider, "owner": owner, "name": name})
            row = connection.execute("SELECT * FROM repositories WHERE id=?", (repository_id,)).fetchone()
        return dict(row)

    def scan_repository(self, principal: Principal, repository_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator")
        repository = self._tenant_record("repositories", repository_id, principal.tenant_id)
        ref = str(payload.get("ref") or repository["default_branch"] or "HEAD")
        source_id = str(payload.get("source_id") or "")
        with tempfile.TemporaryDirectory(prefix="skillsentra-quarantine-") as temp_name:
            if repository["provider"] == "local-fixture":
                source_id = repository["source_url"].removeprefix("local://")
                source = self.repository.get_skill_source(source_id, principal.tenant_id)
                if source is None:
                    raise AppError("skill_source_not_found", "授权 Skill 来源不存在。", 404)
                root = Path(source["root_path"])
                commit = f"local-{source['artifact_digest'][:40]}"
                manifest = canonical_manifest(root)
            else:
                # A GitHub snapshot has no server-local source binding. Ignore a
                # caller-supplied source_id so it cannot create a cross-tenant
                # reference to a private local Skill record.
                source_id = ""
                commit, root = self.github.fetch(repository["owner"], repository["name"], ref, Path(temp_name))
                source = None
                manifest = canonical_manifest(root)
            scan = self.skill_engine.scan_path(root)
            snapshot = self._persist_snapshot(principal, repository, ref, commit, manifest, scan, source_id or None)
        return snapshot

    def _persist_snapshot(
        self,
        principal: Principal,
        repository: dict[str, Any],
        ref: str,
        commit: str,
        manifest: dict[str, Any],
        scan: dict[str, Any],
        source_id: str | None,
    ) -> dict[str, Any]:
        snapshot_id = make_id("snap")
        now = utc_now()
        findings = scan.get("findings") or []
        critical = any(item.get("severity") == "critical" for item in findings)
        status = "blocked" if critical else ("review_required" if findings else "scanned")
        files = manifest.get("files") or []
        summary = {
            "status": scan.get("status", "failed"),
            "score": scan.get("score", 0),
            "findings": findings,
            "evidence": scan.get("evidence", {}),
            "executed_repository_code": False,
            "scan_mode": "static",
        }
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM repository_snapshots WHERE tenant_id=? AND repository_id=? AND resolved_commit=? AND artifact_digest=?",
                (principal.tenant_id, repository["id"], commit, manifest["artifact_digest"]),
            ).fetchone()
            if existing:
                return self.get_snapshot(principal, str(existing["id"]))
            connection.execute(
                """
                INSERT INTO repository_snapshots(
                    id, tenant_id, repository_id, requested_ref, resolved_commit, artifact_digest,
                    source_id, status, file_count, total_bytes, scan_summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id, principal.tenant_id, repository["id"], ref, commit,
                    manifest["artifact_digest"], source_id, status, int(manifest.get("file_count", len(files))),
                    int(manifest.get("total_bytes", 0)), json_dumps(summary), now,
                ),
            )
            attestations = self._attestations(scan, manifest["artifact_digest"], snapshot_id, principal.tenant_id, now)
            for item in attestations:
                connection.execute(
                    """
                    INSERT INTO evidence_attestations(
                        id, tenant_id, snapshot_id, subject_digest, evidence_type, status, severity,
                        engine, ruleset_version, evidence_json, valid_until, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    item,
                )
            connection.execute("UPDATE repositories SET status=?, last_error='', updated_at=? WHERE id=?", ("blocked" if critical else "ready", now, repository["id"]))
            self._audit(
                connection, principal, "repository.scanned", "snapshot", snapshot_id,
                {"commit": commit, "digest": manifest["artifact_digest"], "status": status, "executed": False},
            )
        return self.get_snapshot(principal, snapshot_id)

    def get_snapshot(self, principal: Principal, snapshot_id: str) -> dict[str, Any]:
        snapshot = self._tenant_record("repository_snapshots", snapshot_id, principal.tenant_id)
        with self.database.session() as connection:
            evidence = connection.execute(
                "SELECT * FROM evidence_attestations WHERE tenant_id=? AND snapshot_id=? ORDER BY created_at, id",
                (principal.tenant_id, snapshot_id),
            ).fetchall()
        snapshot["scan_summary"] = json.loads(snapshot.pop("scan_summary_json") or "{}")
        snapshot["evidence"] = [self._decode_evidence(dict(row)) for row in evidence]
        return snapshot

    def list_snapshots(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT s.*, r.owner, r.name AS repository_name FROM repository_snapshots s
                JOIN repositories r ON r.id=s.repository_id
                WHERE s.tenant_id=? ORDER BY s.created_at DESC LIMIT 100
                """,
                (principal.tenant_id,),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["scan_summary"] = json.loads(item.pop("scan_summary_json") or "{}")
            output.append(item)
        return output

    def list_evidence(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT e.*, r.owner, r.name AS repository_name FROM evidence_attestations e
                JOIN repository_snapshots s ON s.id=e.snapshot_id JOIN repositories r ON r.id=s.repository_id
                WHERE e.tenant_id=? ORDER BY e.created_at DESC LIMIT 300
                """,
                (principal.tenant_id,),
            ).fetchall()
        return [self._decode_evidence(dict(row)) for row in rows]

    def create_agent(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator")
        self.ensure_tenant(principal)
        name = self._required_text(payload, "name", 100)
        environment = str(payload.get("environment") or "development")
        if environment not in {"development", "staging", "production"}:
            raise AppError("invalid_agent_environment", "Agent 环境不正确。")
        capabilities = payload.get("capabilities") or {
            "discover": "enforced", "install_remove": "observed", "resolve_digest": "enforced",
            "observe_run": "observed", "intercept_capability": "unsupported", "confirm": "unsupported",
            "revoke": "enforced", "offline_policy": "declared",
        }
        if not isinstance(capabilities, dict):
            raise AppError("invalid_agent_capabilities", "Agent 能力必须是对象。")
        agent_id = make_id("agt")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO agents(id, tenant_id, name, environment, host_name, adapter, adapter_version, status, capabilities_json, last_heartbeat_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'online', ?, ?, ?, ?)
                """,
                (
                    agent_id, principal.tenant_id, name, environment,
                    str(payload.get("host_name") or "reference-host")[:120],
                    str(payload.get("adapter") or "skillsentra-reference")[:120],
                    str(payload.get("adapter_version") or "0.3.0")[:60],
                    json_dumps(capabilities), now, now, now,
                ),
            )
            self._audit(connection, principal, "agent.registered", "agent", agent_id, {"environment": environment})
        return self.get_agent(principal, agent_id)

    def get_agent(self, principal: Principal, agent_id: str) -> dict[str, Any]:
        record = self._tenant_record("agents", agent_id, principal.tenant_id)
        record["capabilities"] = json.loads(record.pop("capabilities_json") or "{}")
        return record

    def list_agents(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute("SELECT * FROM agents WHERE tenant_id=? ORDER BY updated_at DESC", (principal.tenant_id,)).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["capabilities"] = json.loads(item.pop("capabilities_json") or "{}")
            output.append(item)
        return output

    def create_deployment(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator")
        agent = self._tenant_record("agents", self._required_text(payload, "agent_id", 100), principal.tenant_id)
        snapshot = self._tenant_record("repository_snapshots", self._required_text(payload, "snapshot_id", 100), principal.tenant_id)
        if snapshot["status"] == "blocked":
            raise AppError("security_gate_denied", "该制品存在未处置 Critical，不能绑定 Agent。", 409)
        if self._active_revocation(principal.tenant_id, snapshot["artifact_digest"]):
            raise AppError("artifact_revoked", "该制品已撤销，不能部署。", 409)
        environment = agent["environment"]
        if environment == "production" and snapshot["status"] != "scanned":
            raise AppError("production_gate_review_required", "生产环境只允许证据完整且无需人工复核的制品。", 409)
        control_level = str(payload.get("control_level") or "observed")
        if control_level not in {"enforced", "observed", "declared", "unsupported"}:
            raise AppError("invalid_control_level", "Adapter 控制等级不正确。")
        deployment_id = make_id("dep")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO deployments(
                    id, tenant_id, agent_id, snapshot_id, desired_digest, policy_version,
                    control_level, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?)
                """,
                (
                    deployment_id, principal.tenant_id, agent["id"], snapshot["id"], snapshot["artifact_digest"],
                    str(payload.get("policy_version") or "policy-v1")[:100], control_level, now, now,
                ),
            )
            self._audit(connection, principal, "deployment.applied", "deployment", deployment_id, {"agent_id": agent["id"], "digest": snapshot["artifact_digest"]})
        return self.get_deployment(principal, deployment_id)

    def observe_deployment(self, principal: Principal, deployment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator")
        deployment = self._tenant_record("deployments", deployment_id, principal.tenant_id)
        observed = self._required_text(payload, "observed_digest", 128)
        if not ARTIFACT_DIGEST_RE.fullmatch(observed):
            raise AppError("invalid_artifact_digest", "观测 Digest 必须是 sha256: 加 64 位小写摘要。")
        status = "observed" if hmac.compare_digest(observed, deployment["desired_digest"]) else "drifted"
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE deployments SET observed_digest=?, status=?, revision=revision+1, updated_at=? WHERE id=? AND tenant_id=?",
                (observed, status, now, deployment_id, principal.tenant_id),
            )
            self._audit(connection, principal, "deployment.observed", "deployment", deployment_id, {"status": status, "observed_digest": observed})
        return self.get_deployment(principal, deployment_id)

    def get_deployment(self, principal: Principal, deployment_id: str) -> dict[str, Any]:
        return self._tenant_record("deployments", deployment_id, principal.tenant_id)

    def list_deployments(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT d.*, a.name AS agent_name, a.environment FROM deployments d
                JOIN agents a ON a.id=d.agent_id WHERE d.tenant_id=? ORDER BY d.updated_at DESC
                """,
                (principal.tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_artifact(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("security")
        digest = self._required_text(payload, "artifact_digest", 128)
        if not ARTIFACT_DIGEST_RE.fullmatch(digest):
            raise AppError("invalid_artifact_digest", "撤销对象必须是完整 sha256: Digest。")
        reason = self._required_text(payload, "reason", 500)
        severity = str(payload.get("severity") or "high")
        if severity not in {"medium", "high", "critical"}:
            raise AppError("invalid_revocation_severity", "撤销严重度不正确。")
        revocation_id = make_id("rev")
        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM revocations WHERE tenant_id=? AND artifact_digest=? AND status='active'",
                (principal.tenant_id, digest),
            ).fetchone()
            if existing:
                return dict(existing)
            connection.execute(
                "INSERT INTO revocations(id, tenant_id, artifact_digest, reason, severity, actor_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (revocation_id, principal.tenant_id, digest, reason, severity, principal.actor_id, now),
            )
            connection.execute(
                "UPDATE deployments SET status='revoked', revision=revision+1, updated_at=? WHERE tenant_id=? AND desired_digest=? AND status NOT IN ('revoked','rolled_back')",
                (now, principal.tenant_id, digest),
            )
            connection.execute(
                "UPDATE entitlements SET status='revoked' WHERE tenant_id=? AND artifact_digest=? AND status='active'",
                (principal.tenant_id, digest),
            )
            self._audit(connection, principal, "artifact.revoked", "artifact", digest, {"reason": reason, "severity": severity})
            row = connection.execute("SELECT * FROM revocations WHERE id=?", (revocation_id,)).fetchone()
        return dict(row)

    def list_revocations(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute("SELECT * FROM revocations WHERE tenant_id=? ORDER BY created_at DESC", (principal.tenant_id,)).fetchall()
        return [dict(row) for row in rows]

    def create_entitlement(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator", "finance")
        snapshot = self._tenant_record("repository_snapshots", self._required_text(payload, "snapshot_id", 100), principal.tenant_id)
        if snapshot["status"] == "blocked" or self._active_revocation(principal.tenant_id, snapshot["artifact_digest"]):
            raise AppError("entitlement_gate_denied", "被阻断或已撤销制品不能签发授权。", 409)
        entitlement_id = make_id("ent")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO entitlements(
                    id, tenant_id, publisher_id, customer_id, snapshot_id, artifact_digest, starts_at, ends_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entitlement_id, principal.tenant_id, str(payload.get("publisher_id") or "publisher-demo")[:120],
                    str(payload.get("customer_id") or principal.tenant_id)[:120], snapshot["id"], snapshot["artifact_digest"],
                    str(payload.get("starts_at") or now), payload.get("ends_at"), now,
                ),
            )
            self._audit(connection, principal, "entitlement.issued", "entitlement", entitlement_id, {"digest": snapshot["artifact_digest"]})
            row = connection.execute("SELECT * FROM entitlements WHERE id=?", (entitlement_id,)).fetchone()
        return dict(row)

    def ingest_receipt(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator", "finance")
        external_id = self._required_text(payload, "receipt_id", 160)
        agent = self._tenant_record("agents", self._required_text(payload, "agent_id", 100), principal.tenant_id)
        deployment = self._tenant_record("deployments", self._required_text(payload, "deployment_id", 100), principal.tenant_id)
        if deployment["agent_id"] != agent["id"]:
            raise AppError("receipt_agent_mismatch", "用量收据的 Agent 与部署不一致。", 409)
        digest = self._required_text(payload, "artifact_digest", 128)
        if not ARTIFACT_DIGEST_RE.fullmatch(digest):
            raise AppError("invalid_artifact_digest", "用量收据必须携带完整 sha256: Digest。")
        metric = self._required_text(payload, "metric", 80)
        unit = self._required_text(payload, "unit", 40)
        try:
            quantity = int(payload.get("quantity", 0))
        except (TypeError, ValueError) as exc:
            raise AppError("invalid_receipt_quantity", "用量必须是正整数。") from exc
        if quantity < 1 or quantity > 1_000_000_000:
            raise AppError("invalid_receipt_quantity", "用量必须在 1–1,000,000,000 之间。")
        evidence_level = str(payload.get("evidence_level") or "E0")
        if evidence_level not in {"E0", "E1", "E2", "E3"}:
            raise AppError("invalid_evidence_level", "证据等级必须是 E0–E3。")
        occurred_at = self._parse_time(str(payload.get("occurred_at") or utc_now()))
        signature = self._required_text(payload, "signature", 500)
        publisher_id = str(payload.get("publisher_id") or "publisher-demo")[:120]
        now = utc_now()
        with self.database.transaction() as connection:
            duplicate = connection.execute(
                "SELECT * FROM usage_receipts WHERE tenant_id=? AND external_receipt_id=?",
                (principal.tenant_id, external_id),
            ).fetchone()
            if duplicate:
                return dict(duplicate)
            current_deployment = connection.execute(
                "SELECT * FROM deployments WHERE id=? AND tenant_id=?",
                (deployment["id"], principal.tenant_id),
            ).fetchone()
            if current_deployment is None:
                raise AppError("resource_not_found", "资源不存在或当前租户无权访问。", 404)
            deployment = dict(current_deployment)
            if deployment["agent_id"] != agent["id"]:
                raise AppError("receipt_agent_mismatch", "用量收据的 Agent 与部署不一致。", 409)
            # Keep the policy decision in the receipt transaction. Opening a
            # separate SQLite connection for the revocation lookup dominated
            # this small decision on Windows and also left a write-race between
            # qualification and receipt persistence.
            exclusion = self._qualification_reason(
                principal.tenant_id,
                deployment,
                digest,
                evidence_level,
                occurred_at,
                connection=connection,
            )
            status = "excluded" if exclusion else "qualified"
            entitlement = connection.execute(
                """
                SELECT * FROM entitlements WHERE tenant_id=? AND artifact_digest=? AND status='active'
                  AND starts_at<=? AND (ends_at IS NULL OR ends_at>?) ORDER BY created_at DESC LIMIT 1
                """,
                (principal.tenant_id, digest, occurred_at, occurred_at),
            ).fetchone()
            if status == "qualified" and entitlement is None:
                status = "excluded"
                exclusion = "entitlement_missing"
            price = self._effective_price(connection, principal.tenant_id, metric, unit, occurred_at)
            share = self._effective_share(connection, principal.tenant_id, occurred_at)
            if status == "qualified" and price is None:
                status = "excluded"
                exclusion = "price_rule_missing"
            gross = quantity * int(price["amount_minor"]) if status == "qualified" and price else 0
            publisher_amount = math.floor(gross * int(share["publisher_bps"]) / 10_000) if status == "qualified" and share else 0
            receipt_id = make_id("rcp")
            connection.execute(
                """
                INSERT INTO usage_receipts(
                    id, tenant_id, external_receipt_id, publisher_id, entitlement_id, agent_id, deployment_id,
                    run_id, canonicalization_version, digest_algorithm, artifact_digest, metric, unit, quantity,
                    policy_version, evidence_level, occurred_at, signature, status, exclusion_reason,
                    price_rule_id, share_rule_id, gross_amount_minor, publisher_amount_minor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id, principal.tenant_id, external_id, publisher_id, entitlement["id"] if entitlement else None,
                    agent["id"], deployment["id"], str(payload.get("run_id") or make_id("external-run"))[:160],
                    ARTIFACT_CANON, DIGEST_ALGORITHM, digest, metric, unit, quantity,
                    self._required_text(payload, "policy_version", 100), evidence_level, occurred_at, signature,
                    status, exclusion, price["id"] if price else None, share["id"] if share else None,
                    gross, publisher_amount, now,
                ),
            )
            if status == "qualified":
                self._post_usage_transaction(connection, principal.tenant_id, receipt_id, publisher_id, str(price["currency"]), gross, publisher_amount, now)
            self._audit(connection, principal, "usage.receipt.processed", "usage_receipt", receipt_id, {"status": status, "reason": exclusion, "gross_minor": gross})
            row = connection.execute("SELECT * FROM usage_receipts WHERE id=?", (receipt_id,)).fetchone()
        return dict(row)

    def list_receipts(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute("SELECT * FROM usage_receipts WHERE tenant_id=? ORDER BY created_at DESC LIMIT 200", (principal.tenant_id,)).fetchall()
        return [dict(row) for row in rows]

    def create_statement(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("finance")
        publisher_id = str(payload.get("publisher_id") or "publisher-demo")[:120]
        cycle_start = self._parse_time(str(payload.get("cycle_start") or "2000-01-01T00:00:00+00:00"))
        cycle_end = self._parse_time(str(payload.get("cycle_end") or utc_now()))
        if cycle_start >= cycle_end:
            raise AppError("invalid_statement_cycle", "结算周期开始时间必须早于结束时间。")
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM settlement_statements WHERE tenant_id=? AND publisher_id=? AND cycle_start=? AND cycle_end=? ORDER BY version DESC LIMIT 1",
                (principal.tenant_id, publisher_id, cycle_start, cycle_end),
            ).fetchone()
            if existing:
                return self._decode_statement(dict(existing))
            rows = connection.execute(
                """
                SELECT * FROM usage_receipts WHERE tenant_id=? AND publisher_id=? AND status='qualified'
                  AND statement_id IS NULL AND occurred_at>=? AND occurred_at<? ORDER BY occurred_at, id
                """,
                (principal.tenant_id, publisher_id, cycle_start, cycle_end),
            ).fetchall()
            order_rows = connection.execute(
                """
                SELECT o.*, p.artifact_digest, p.name AS publication_name
                FROM marketplace_orders o JOIN marketplace_publications p ON p.id=o.publication_id
                WHERE o.publisher_user_id=? AND o.status='paid' AND o.amount_minor>0
                  AND o.statement_id IS NULL AND o.created_at>=? AND o.created_at<?
                ORDER BY o.created_at, o.id
                """,
                (publisher_id, cycle_start, cycle_end),
            ).fetchall()
            if not rows and not order_rows:
                raise AppError("statement_empty", "当前周期没有可结算用量。", 409)
            currency_rows = connection.execute(
                """
                SELECT DISTINCT p.currency FROM usage_receipts r JOIN price_rules p ON p.id=r.price_rule_id
                WHERE r.tenant_id=? AND r.publisher_id=? AND r.status='qualified'
                  AND r.statement_id IS NULL AND r.occurred_at>=? AND r.occurred_at<?
                """,
                (principal.tenant_id, publisher_id, cycle_start, cycle_end),
            ).fetchall()
            currencies = {str(item["currency"]) for item in currency_rows}
            currencies.update(str(item["currency"]) for item in order_rows)
            if len(currencies) != 1:
                raise AppError("statement_currency_conflict", "一个 Statement 只能包含一种币种。", 409)
            statement_id = make_id("stmt")
            gross = sum(int(row["gross_amount_minor"]) for row in rows) + sum(int(row["amount_minor"]) for row in order_rows)
            payable = sum(int(row["publisher_amount_minor"]) for row in rows) + sum(int(row["publisher_amount_minor"]) for row in order_rows)
            line_items = [
                {
                    "source_type": "usage_receipt",
                    "receipt_id": row["id"], "external_receipt_id": row["external_receipt_id"],
                    "artifact_digest": row["artifact_digest"], "metric": row["metric"], "unit": row["unit"],
                    "quantity": row["quantity"], "gross_minor": row["gross_amount_minor"],
                    "publisher_minor": row["publisher_amount_minor"], "occurred_at": row["occurred_at"],
                }
                for row in rows
            ]
            line_items.extend(
                {
                    "source_type": "marketplace_order", "order_id": row["id"], "publication_id": row["publication_id"],
                    "publication_name": row["publication_name"], "artifact_digest": row["artifact_digest"],
                    "gross_minor": row["amount_minor"], "publisher_minor": row["publisher_amount_minor"],
                    "platform_fee_minor": row["platform_fee_minor"], "occurred_at": row["created_at"],
                }
                for row in order_rows
            )
            currency = next(iter(currencies))
            connection.execute(
                """
                INSERT INTO settlement_statements(
                    id, tenant_id, publisher_id, cycle_start, cycle_end, currency, status,
                    total_gross_minor, total_payable_minor, line_items_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'issued', ?, ?, ?, ?)
                """,
                (statement_id, principal.tenant_id, publisher_id, cycle_start, cycle_end, currency, gross, payable, json_dumps(line_items), utc_now()),
            )
            if rows:
                connection.executemany(
                    "UPDATE usage_receipts SET statement_id=? WHERE id=? AND statement_id IS NULL",
                    [(statement_id, row["id"]) for row in rows],
                )
            if order_rows:
                connection.executemany(
                    "UPDATE marketplace_orders SET statement_id=? WHERE id=? AND statement_id IS NULL",
                    [(statement_id, row["id"]) for row in order_rows],
                )
            self._audit(connection, principal, "statement.issued", "statement", statement_id, {"payable_minor": payable, "currency": currency})
            row = connection.execute("SELECT * FROM settlement_statements WHERE id=?", (statement_id,)).fetchone()
        return self._decode_statement(dict(row))

    def list_statements(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute("SELECT * FROM settlement_statements WHERE tenant_id=? ORDER BY created_at DESC", (principal.tenant_id,)).fetchall()
        return [self._decode_statement(dict(row)) for row in rows]

    def submit_payout(self, principal: Principal, statement_id: str, idempotency_key: str) -> dict[str, Any]:
        principal.require("finance")
        if len(idempotency_key) < 12 or len(idempotency_key) > 200:
            raise AppError("idempotency_key_required", "支付沙箱操作需要 12–200 字符的 Idempotency-Key。")
        statement = self._tenant_record("settlement_statements", statement_id, principal.tenant_id)
        if statement["status"] not in {"issued", "closed"}:
            raise AppError("statement_not_payable", "当前 Statement 不可支付。", 409)
        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM payout_intents WHERE tenant_id=? AND idempotency_key=?",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
            if existing:
                return dict(existing)
            payout_id = make_id("pay")
            provider_reference = f"sandbox_{uuid.uuid4().hex[:18]}"
            connection.execute(
                """
                INSERT INTO payout_intents(
                    id, tenant_id, statement_id, idempotency_key, provider_reference,
                    amount_minor, currency, mode, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'sandbox', 'paid', ?, ?)
                """,
                (
                    payout_id, principal.tenant_id, statement_id, idempotency_key, provider_reference,
                    statement["total_payable_minor"], statement["currency"], now, now,
                ),
            )
            self._post_payout_transaction(
                connection, principal.tenant_id, payout_id, statement["publisher_id"], statement["currency"], int(statement["total_payable_minor"]), now,
            )
            connection.execute("UPDATE settlement_statements SET status='closed' WHERE id=?", (statement_id,))
            self._audit(connection, principal, "payout.sandbox.paid", "payout", payout_id, {"statement_id": statement_id, "amount_minor": statement["total_payable_minor"]})
            row = connection.execute("SELECT * FROM payout_intents WHERE id=?", (payout_id,)).fetchone()
        return dict(row)

    def list_payouts(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute("SELECT * FROM payout_intents WHERE tenant_id=? ORDER BY created_at DESC", (principal.tenant_id,)).fetchall()
        return [dict(row) for row in rows]

    def ledger(self, principal: Principal) -> dict[str, Any]:
        principal.require("finance")
        with self.database.session() as connection:
            transactions = connection.execute(
                "SELECT * FROM journal_transactions WHERE tenant_id=? ORDER BY created_at DESC LIMIT 200", (principal.tenant_id,)
            ).fetchall()
            output = []
            imbalances = []
            for row in transactions:
                item = dict(row)
                lines = [dict(line) for line in connection.execute("SELECT * FROM journal_lines WHERE transaction_id=? ORDER BY id", (row["id"],)).fetchall()]
                debit = sum(int(line["amount_minor"]) for line in lines if line["side"] == "debit")
                credit = sum(int(line["amount_minor"]) for line in lines if line["side"] == "credit")
                item["lines"] = lines
                item["balanced"] = debit == credit
                item["debit_minor"] = debit
                item["credit_minor"] = credit
                output.append(item)
                if debit != credit:
                    imbalances.append(item["id"])
        return {"transactions": output, "imbalances": imbalances, "balanced": not imbalances}

    def list_audit(self, principal: Principal, limit: int = 200) -> dict[str, Any]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM platform_audit_events WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
                (principal.tenant_id, max(1, min(limit, 500))),
            ).fetchall()
            ascending = connection.execute(
                "SELECT * FROM platform_audit_events WHERE tenant_id=? ORDER BY id",
                (principal.tenant_id,),
            ).fetchall()
        valid, broken_at = self._verify_audit_chain([dict(row) for row in ascending])
        return {
            "events": [self._decode_audit(dict(row)) for row in rows],
            "integrity": {"valid": valid, "broken_at": broken_at, "event_count": len(ascending)},
        }

    def run_demo_path(self, principal: Principal) -> dict[str, Any]:
        principal.require("admin")
        self.ensure_tenant(principal)
        sources = self.skill_engine.discover_sources(principal.tenant_id)
        if not sources:
            raise AppError("demo_source_missing", "没有可用的演示 Skill 来源。", 409)
        repository = self.create_repository(principal, {"source_id": sources[0]["id"]})
        snapshot = self.scan_repository(principal, repository["id"], {"source_id": sources[0]["id"]})
        agents = self.list_agents(principal)
        agent = agents[0] if agents else self.create_agent(
            principal,
            {"name": "Reference Agent", "environment": "staging", "host_name": "skillsentra-demo-host"},
        )
        deployments = [item for item in self.list_deployments(principal) if item["desired_digest"] == snapshot["artifact_digest"]]
        deployment = deployments[0] if deployments else self.create_deployment(
            principal,
            {"agent_id": agent["id"], "snapshot_id": snapshot["id"], "policy_version": "policy-demo-v1", "control_level": "enforced"},
        )
        if not deployment["observed_digest"]:
            deployment = self.observe_deployment(principal, deployment["id"], {"observed_digest": snapshot["artifact_digest"]})
        with self.database.session() as connection:
            entitlement_row = connection.execute(
                "SELECT * FROM entitlements WHERE tenant_id=? AND artifact_digest=? AND status='active' LIMIT 1",
                (principal.tenant_id, snapshot["artifact_digest"]),
            ).fetchone()
        entitlement = dict(entitlement_row) if entitlement_row else self.create_entitlement(principal, {"snapshot_id": snapshot["id"], "publisher_id": "publisher-demo"})
        receipt = self.ingest_receipt(
            principal,
            {
                "receipt_id": f"demo-{snapshot['artifact_digest'][:12]}", "publisher_id": entitlement["publisher_id"],
                "agent_id": agent["id"], "deployment_id": deployment["id"], "run_id": "demo-run-001",
                "artifact_digest": snapshot["artifact_digest"], "metric": "verified_run", "unit": "run",
                "quantity": 1, "policy_version": deployment["policy_version"], "evidence_level": "E3",
                "signature": "demo-signature-not-production", "occurred_at": utc_now(),
            },
        )
        return {
            "repository": repository, "snapshot": snapshot, "agent": agent, "deployment": deployment,
            "entitlement": entitlement, "receipt": receipt, "overview": self.overview(principal),
        }

    @staticmethod
    def _attestations(scan: dict[str, Any], digest: str, snapshot_id: str, tenant_id: str, now: str) -> list[tuple[Any, ...]]:
        findings = scan.get("findings") or []
        severity_order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        highest = max((str(item.get("severity") or "info") for item in findings), key=lambda item: severity_order.get(item, 0), default="info")
        static_status = "fail" if highest == "critical" else ("warning" if findings else "pass")
        base = [
            ("structure", "pass" if scan.get("status") == "passed" else "fail", highest, {"score": scan.get("score", 0)}),
            ("source_provenance", "pass", "info", {"fixed_subject": digest}),
            ("secret_and_script", static_status, highest, {"findings": findings}),
            ("dependency_license", "unknown", "info", {"reason": "No package-manager execution; declarations only"}),
            ("capability_inference", "warning" if findings else "pass", "medium" if findings else "info", {"mode": "static"}),
            ("dynamic_sandbox", "unknown", "info", {"executed": False}),
        ]
        return [
            (
                make_id("att"), tenant_id, snapshot_id, digest, evidence_type, status, severity,
                "skillsentra-static", "ruleset-2026.08", json_dumps(evidence), None, now,
            )
            for evidence_type, status, severity, evidence in base
        ]

    @staticmethod
    def _decode_evidence(record: dict[str, Any]) -> dict[str, Any]:
        record["evidence"] = json.loads(record.pop("evidence_json") or "{}")
        return record

    @staticmethod
    def _decode_statement(record: dict[str, Any]) -> dict[str, Any]:
        record["line_items"] = json.loads(record.pop("line_items_json") or "[]")
        return record

    @staticmethod
    def _decode_audit(record: dict[str, Any]) -> dict[str, Any]:
        record["detail"] = json.loads(record.pop("detail_json") or "{}")
        return record

    def _tenant_record(self, table: str, record_id: str, tenant_id: str) -> dict[str, Any]:
        allowed = {
            "repositories", "repository_snapshots", "agents", "deployments", "settlement_statements",
        }
        if table not in allowed:
            raise ValueError("Unsupported tenant table")
        with self.database.session() as connection:
            row = connection.execute(f"SELECT * FROM {table} WHERE id=? AND tenant_id=?", (record_id, tenant_id)).fetchone()
        if row is None:
            raise AppError("resource_not_found", "资源不存在或当前租户无权访问。", 404)
        return dict(row)

    @staticmethod
    def _required_text(payload: dict[str, Any], field: str, limit: int) -> str:
        value = str(payload.get(field) or "").strip()
        if not value:
            raise AppError("field_required", f"{field} 为必填项。", 400, {"field": field})
        if len(value) > limit:
            raise AppError("field_too_large", f"{field} 超过长度限制。", 413, {"field": field, "limit": limit})
        return value

    @staticmethod
    def _count(connection: Any, table: str, tenant_id: str) -> int:
        return int(connection.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id=?", (tenant_id,)).fetchone()["n"])

    def _active_revocation(self, tenant_id: str, digest: str, connection: Any | None = None) -> bool:
        if connection is None:
            with self.database.session() as owned_connection:
                return self._active_revocation(tenant_id, digest, owned_connection)
        row = connection.execute(
            "SELECT 1 FROM revocations WHERE tenant_id=? AND artifact_digest=? AND status='active'",
            (tenant_id, digest),
        ).fetchone()
        return row is not None

    def _qualification_reason(
        self,
        tenant_id: str,
        deployment: dict[str, Any],
        digest: str,
        level: str,
        occurred_at: str,
        *,
        connection: Any | None = None,
    ) -> str:
        del occurred_at
        if level not in {"E2", "E3"}:
            return "evidence_level_insufficient"
        if deployment["status"] != "observed":
            return "deployment_not_observed"
        if not deployment["observed_digest"] or not hmac.compare_digest(deployment["observed_digest"], digest):
            return "artifact_digest_mismatch"
        if self._active_revocation(tenant_id, digest, connection):
            return "artifact_revoked"
        return ""

    @staticmethod
    def _parse_time(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AppError("invalid_timestamp", "时间必须是 RFC3339 格式。") from exc
        if parsed.tzinfo is None:
            raise AppError("invalid_timestamp", "时间必须包含时区。")
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def _ensure_finance_rules(connection: Any, tenant_id: str, now: str) -> None:
        if connection.execute("SELECT 1 FROM price_rules WHERE tenant_id=?", (tenant_id,)).fetchone() is None:
            connection.execute(
                "INSERT INTO price_rules(id, tenant_id, version, metric, unit, currency, amount_minor, effective_from, created_at) VALUES (?, ?, 'demo-price-v1', 'verified_run', 'run', 'USD', 100, ?, ?)",
                (make_id("price"), tenant_id, "2000-01-01T00:00:00.000+00:00", now),
            )
        if connection.execute("SELECT 1 FROM share_rules WHERE tenant_id=?", (tenant_id,)).fetchone() is None:
            connection.execute(
                "INSERT INTO share_rules(id, tenant_id, version, publisher_bps, effective_from, created_at) VALUES (?, ?, 'demo-share-v1', 8500, ?, ?)",
                (make_id("share"), tenant_id, "2000-01-01T00:00:00.000+00:00", now),
            )

    @staticmethod
    def _effective_price(connection: Any, tenant_id: str, metric: str, unit: str, occurred_at: str) -> Any:
        return connection.execute(
            """
            SELECT * FROM price_rules WHERE tenant_id=? AND metric=? AND unit=? AND effective_from<=?
              AND (effective_to IS NULL OR effective_to>?) ORDER BY effective_from DESC LIMIT 1
            """,
            (tenant_id, metric, unit, occurred_at, occurred_at),
        ).fetchone()

    @staticmethod
    def _effective_share(connection: Any, tenant_id: str, occurred_at: str) -> Any:
        return connection.execute(
            """
            SELECT * FROM share_rules WHERE tenant_id=? AND effective_from<=?
              AND (effective_to IS NULL OR effective_to>?) ORDER BY effective_from DESC LIMIT 1
            """,
            (tenant_id, occurred_at, occurred_at),
        ).fetchone()

    @staticmethod
    def _post_usage_transaction(
        connection: Any, tenant_id: str, receipt_id: str, publisher_id: str, currency: str,
        gross: int, publisher_amount: int, now: str,
    ) -> None:
        if gross <= 0:
            raise AppError("invalid_journal_amount", "合格用量的结算金额必须大于 0。", 409)
        transaction_id = make_id("jtx")
        platform_amount = gross - publisher_amount
        connection.execute(
            "INSERT INTO journal_transactions(id, tenant_id, source_type, source_id, currency, description, created_at) VALUES (?, ?, 'usage_receipt', ?, ?, 'Qualified Skill usage', ?)",
            (transaction_id, tenant_id, receipt_id, currency, now),
        )
        lines = [("accounts_receivable", "debit", gross, tenant_id), ("publisher_payable", "credit", publisher_amount, publisher_id)]
        if platform_amount:
            lines.append(("platform_revenue", "credit", platform_amount, tenant_id))
        for account, side, amount, party in lines:
            connection.execute(
                "INSERT INTO journal_lines(id, transaction_id, account_code, side, amount_minor, party_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (make_id("jln"), transaction_id, account, side, amount, party, now),
            )

    @staticmethod
    def _post_payout_transaction(
        connection: Any, tenant_id: str, payout_id: str, publisher_id: str, currency: str, amount: int, now: str,
    ) -> None:
        if amount <= 0:
            raise AppError("invalid_payout_amount", "支付金额必须大于 0。", 409)
        transaction_id = make_id("jtx")
        connection.execute(
            "INSERT INTO journal_transactions(id, tenant_id, source_type, source_id, currency, description, created_at) VALUES (?, ?, 'payout', ?, ?, 'Sandbox publisher payout', ?)",
            (transaction_id, tenant_id, payout_id, currency, now),
        )
        for account, side in (("publisher_payable", "debit"), ("sandbox_cash", "credit")):
            connection.execute(
                "INSERT INTO journal_lines(id, transaction_id, account_code, side, amount_minor, party_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (make_id("jln"), transaction_id, account, side, amount, publisher_id, now),
            )

    @staticmethod
    def _audit(connection: Any, principal: Principal, action: str, entity_type: str, entity_id: str, detail: dict[str, Any]) -> None:
        previous = connection.execute(
            "SELECT event_hash FROM platform_audit_events WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
            (principal.tenant_id,),
        ).fetchone()
        previous_hash = str(previous["event_hash"]) if previous else ""
        created_at = utc_now()
        canonical = json_dumps(
            {
                "tenant_id": principal.tenant_id, "actor_id": principal.actor_id, "action": action,
                "entity_type": entity_type, "entity_id": entity_id, "detail": detail,
                "previous_hash": previous_hash, "created_at": created_at,
            }
        )
        event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO platform_audit_events(
                tenant_id, actor_id, action, entity_type, entity_id, detail_json,
                previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (principal.tenant_id, principal.actor_id, action, entity_type, entity_id, json_dumps(detail), previous_hash, event_hash, created_at),
        )

    @staticmethod
    def _verify_audit_chain(rows: list[dict[str, Any]]) -> tuple[bool, int | None]:
        previous_hash = ""
        for row in rows:
            detail = json.loads(row.get("detail_json") or "{}")
            canonical = json_dumps(
                {
                    "tenant_id": row["tenant_id"], "actor_id": row["actor_id"], "action": row["action"],
                    "entity_type": row["entity_type"], "entity_id": row["entity_id"], "detail": detail,
                    "previous_hash": previous_hash, "created_at": row["created_at"],
                }
            )
            expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if row["previous_hash"] != previous_hash or not hmac.compare_digest(row["event_hash"], expected):
                return False, int(row["id"])
            previous_hash = row["event_hash"]
        return True, None
