from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_NAME = "SKillSentra for Chatgpt"
ARTIFACT_STEM = "skillsentra-for-chatgpt"
INCLUDE_ROOTS = ("app", "bridge", "demo-apple", "plugins", "sample-skills", "scripts")
INCLUDE_FILES = (
    "README.md", "README.zh-CN.md", "CHATGPT.md", "DEVELOPMENT.md", "SECURITY.md", "Dockerfile",
    "Dockerfile.chatgpt-mcp", "compose.yaml", "compose.chatgpt.yaml",
    ".env.example", ".env.production.example", ".env.chatgpt-mcp.example",
    "package.json", "package-lock.json", "THIRD_PARTY_NOTICES.md",
    "product/03-architecture/openapi-v0.4.yaml",
    "product/05-release/rollout-rollback-and-limitations.md",
)
SKIP_PARTS = {"__pycache__", "node_modules", ".git", "data", "release"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def release_files(version: str) -> list[Path]:
    files: list[Path] = []
    for root_name in INCLUDE_ROOTS:
        root = PROJECT_ROOT / root_name
        for path in root.rglob("*"):
            if path.is_file() and not any(part in SKIP_PARTS for part in path.relative_to(PROJECT_ROOT).parts):
                files.append(path)
    for name in INCLUDE_FILES:
        path = PROJECT_ROOT / name
        if path.is_file():
            files.append(path)
    minor = ".".join(version.split(".")[:2])
    for template in (
        "product/01-product/PRD-v{minor}.md",
        "product/03-architecture/implementation-architecture-v{minor}.md",
        "product/05-release/production-readiness-report-v{minor}.md",
        "product/05-release/release-notes-v{version}.md",
        "product/04-quality/test-plan-v{version}.md",
        "evidence/validation-report-v{minor}.md",
        "evidence/validation-report-v{version}.md",
        "evidence/performance/control-plane-benchmark-v{version}.json",
        "evidence/recovery/sqlite-restore-rehearsal-v{version}.json",
    ):
        path = PROJECT_ROOT / template.format(minor=minor, version=version)
        if path.is_file():
            files.append(path)
    return sorted(set(files), key=lambda item: item.relative_to(PROJECT_ROOT).as_posix().encode("utf-8"))


def package_metadata() -> dict[str, Any]:
    return json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))


def build(output: Path, version: str) -> dict[str, Any]:
    # Generated release files must not make an otherwise clean source tree look dirty.
    source_status = git_value(
        "status",
        "--porcelain",
        "--",
        ".",
        ":(exclude)release",
        f":(exclude)evidence/performance/control-plane-benchmark-v{version}.json",
        f":(exclude)evidence/recovery/sqlite-restore-rehearsal-v{version}.json",
    )
    source_dirty = bool(source_status)
    output.mkdir(parents=True, exist_ok=True)
    files = release_files(version)
    archive = output / f"{ARTIFACT_STEM}-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path in files:
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            info = zipfile.ZipInfo(relative)
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, path.read_bytes())
    archive_digest = sha256(archive)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "schema_version": "1.0",
        "product": PRODUCT_NAME,
        "version": version,
        "commit": git_value("rev-parse", "HEAD"),
        "branch": git_value("branch", "--show-current"),
        "source_dirty": source_dirty,
        "source_changes": source_status.splitlines(),
        "generated_at": generated_at,
        "artifact": {"path": archive.name, "sha256": archive_digest, "bytes": archive.stat().st_size},
        "files": [
            {"path": path.relative_to(PROJECT_ROOT).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size}
            for path in files
        ],
    }
    (output / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    package = package_metadata()
    components = [
        {
            "type": "library", "name": name, "version": str(version_value), "scope": "optional",
            "purl": f"pkg:npm/{name}@{version_value}",
        }
        for name, version_value in sorted((package.get("devDependencies") or {}).items())
    ]
    sbom = {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'skillsentra-for-chatgpt:{archive_digest}')}",
        "version": 1,
        "metadata": {
            "timestamp": generated_at,
            "component": {"type": "application", "name": PRODUCT_NAME, "version": version, "hashes": [{"alg": "SHA-256", "content": archive_digest}]},
            "properties": [{"name": "skillsentra.runtime", "value": "Python 3.12 standard library"}],
        },
        "components": components,
    }
    (output / "sbom.cdx.json").write_text(json.dumps(sbom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    provenance = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": archive.name, "digest": {"sha256": archive_digest}}],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://github.com/AIfromScratch66/SKillSentra-for-Chatgpt/tree/main/scripts/build_release.py",
                "externalParameters": {"version": version, "sourceDirty": source_dirty},
                "resolvedDependencies": [{"uri": "git+local", "digest": {"gitCommit": manifest["commit"]}}],
            },
            "runDetails": {"builder": {"id": "skillsentra-for-chatgpt-local-builder-v1"}, "metadata": {"invocationId": archive_digest[:24]}},
        },
    }
    (output / "provenance.intoto.jsonl").write_text(json.dumps(provenance, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a deterministic SkillSentra release archive, manifest, SBOM and provenance statement")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "release")
    parser.add_argument("--version", default=str(package_metadata().get("version") or "0.0.0"))
    args = parser.parse_args()
    manifest = build(args.output.resolve(), args.version)
    print(json.dumps({"artifact": manifest["artifact"], "commit": manifest["commit"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
