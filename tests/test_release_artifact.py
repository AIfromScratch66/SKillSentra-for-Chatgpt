from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.build_release import build
from scripts.verify_release import verify


ROOT = Path(__file__).resolve().parents[1]


def clean_git_value(*args: str) -> str:
    if args[:2] == ("status", "--porcelain"):
        return ""
    if args == ("rev-parse", "HEAD"):
        return "fixture-commit"
    if args == ("branch", "--show-current"):
        return "fixture-branch"
    return "fixture"


class ReleaseArtifactTests(unittest.TestCase):
    def test_release_is_deterministic_rooted_and_source_verified(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            with patch("scripts.build_release.git_value", side_effect=clean_git_value):
                first_manifest = build(Path(first), "0.8.0")
                second_manifest = build(Path(second), "0.8.0")

            self.assertEqual(first_manifest["product"], "SKillSentra for Chatgpt")
            self.assertEqual(
                first_manifest["artifact"]["path"],
                "skillsentra-for-chatgpt-0.8.0.zip",
            )
            self.assertEqual(first_manifest["artifact"]["sha256"], second_manifest["artifact"]["sha256"])

            result = verify(Path(first), ROOT)
            self.assertTrue(result["verified"])
            self.assertTrue(result["source_verified"])

            archive = Path(first) / str(first_manifest["artifact"]["path"])
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
                packaged_package = json.loads(package.read("package.json"))
                packaged_lock = json.loads(package.read("package-lock.json"))

            for required in (
                ".env.example",
                ".env.chatgpt-mcp.example",
                "CHATGPT.md",
                "Dockerfile.chatgpt-mcp",
                "compose.chatgpt.yaml",
                "package-lock.json",
                "product/04-quality/test-plan-v0.8.0.md",
                "product/05-release/release-notes-v0.8.0.md",
                "evidence/validation-report-v0.8.0.md",
                "evidence/validation-report-v0.8.0.md",
                "THIRD_PARTY_NOTICES.md",
            ):
                self.assertIn(required, names)
            self.assertEqual(packaged_package["name"], "skillsentra-for-chatgpt")
            self.assertEqual(packaged_package["version"], "0.8.0")
            self.assertEqual(packaged_lock["name"], packaged_package["name"])
            self.assertEqual(packaged_lock["version"], packaged_package["version"])

    def test_verifier_rejects_archive_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("scripts.build_release.git_value", side_effect=clean_git_value):
                manifest = build(Path(directory), "0.8.0")
            archive = Path(directory) / str(manifest["artifact"]["path"])
            with archive.open("ab") as handle:
                handle.write(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                verify(Path(directory), ROOT)


if __name__ == "__main__":
    unittest.main()
