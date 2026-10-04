from __future__ import annotations

import argparse
import hashlib
import json
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and "\\" not in name and not path.is_absolute() and ".." not in path.parts


def verify(root: Path, source_root: Path | None = None) -> dict[str, Any]:
    release_root = root.resolve()
    manifest = _load_json(release_root / "build-manifest.json")
    if manifest.get("source_dirty") is not False or manifest.get("source_changes") != []:
        raise ValueError("Release manifest was not built from a clean source tree")

    artifact_meta = manifest.get("artifact")
    if not isinstance(artifact_meta, dict):
        raise ValueError("Release manifest artifact metadata is missing")
    artifact_name = artifact_meta.get("path")
    if not isinstance(artifact_name, str) or Path(artifact_name).name != artifact_name:
        raise ValueError("Release artifact path must be one file inside the release directory")
    artifact = release_root / artifact_name
    actual_archive_digest = sha256(artifact)
    if actual_archive_digest != artifact_meta.get("sha256"):
        raise ValueError("Release archive SHA-256 does not match the build manifest")
    if artifact.stat().st_size != artifact_meta.get("bytes"):
        raise ValueError("Release archive size does not match the build manifest")

    file_rows = manifest.get("files")
    if not isinstance(file_rows, list) or not file_rows:
        raise ValueError("Release manifest file inventory is empty")
    expected: dict[str, dict[str, Any]] = {}
    for row in file_rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("Release manifest contains an invalid file row")
        name = row["path"]
        if not _safe_member(name) or name in expected:
            raise ValueError(f"Release manifest contains an unsafe or duplicate path: {name!r}")
        expected[name] = row

    with zipfile.ZipFile(artifact) as package:
        infos = package.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("Release archive contains duplicate member names")
        if any(not _safe_member(name) for name in names):
            raise ValueError("Release archive contains an unsafe member path")
        if set(names) != set(expected):
            missing = sorted(set(expected) - set(names))
            extra = sorted(set(names) - set(expected))
            raise ValueError(f"Release archive inventory mismatch; missing={missing}, extra={extra}")
        for info in infos:
            if info.is_dir() or info.flag_bits & 0x1:
                raise ValueError(f"Release archive member must be an unencrypted file: {info.filename}")
            content = package.read(info)
            row = expected[info.filename]
            if len(content) != row.get("bytes") or _sha256_bytes(content) != row.get("sha256"):
                raise ValueError(f"Release archive member digest mismatch: {info.filename}")

    if source_root is not None:
        source = source_root.resolve()
        for name, row in expected.items():
            path = source / Path(*PurePosixPath(name).parts)
            if not path.is_file():
                raise ValueError(f"Release source file is missing: {name}")
            if path.stat().st_size != row.get("bytes") or sha256(path) != row.get("sha256"):
                raise ValueError(f"Release source file does not match the archive: {name}")

    sbom = _load_json(release_root / "sbom.cdx.json")
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.5":
        raise ValueError("SBOM must declare CycloneDX 1.5")
    serial = sbom.get("serialNumber")
    if not isinstance(serial, str) or not serial.startswith("urn:uuid:"):
        raise ValueError("SBOM serialNumber must be a UUID URN")
    parsed_serial = uuid.UUID(serial.removeprefix("urn:uuid:"))
    if parsed_serial.variant != uuid.RFC_4122 or parsed_serial.version != 5:
        raise ValueError("SBOM serialNumber must be a deterministic RFC-4122 UUIDv5")
    component = ((sbom.get("metadata") or {}).get("component") or {})
    if component.get("name") != manifest.get("product") or component.get("version") != manifest.get("version"):
        raise ValueError("SBOM component identity does not match the build manifest")
    hashes = component.get("hashes") or []
    if {item.get("content") for item in hashes if item.get("alg") == "SHA-256"} != {actual_archive_digest}:
        raise ValueError("SBOM component digest does not match the release archive")

    provenance_path = release_root / "provenance.intoto.jsonl"
    provenance_lines = [line for line in provenance_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(provenance_lines) != 1:
        raise ValueError("Provenance must contain exactly one JSON statement")
    provenance = json.loads(provenance_lines[0])
    subjects = provenance.get("subject") or []
    expected_subject = {"name": artifact.name, "digest": {"sha256": actual_archive_digest}}
    if subjects != [expected_subject]:
        raise ValueError("Provenance subject does not match the release archive")

    return {
        "verified": True,
        "artifact": artifact.name,
        "sha256": actual_archive_digest,
        "files": len(expected),
        "source_verified": source_root is not None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a SKillSentra for Chatgpt release and its source inventory")
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.release_dir, args.source_root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"Release verification failed: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
