from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def mapping_block(workflow: str, header: str) -> str:
    lines = workflow.splitlines()
    start = lines.index(header)
    indentation = len(header) - len(header.lstrip())
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        current = len(line) - len(line.lstrip())
        if current <= indentation:
            end = index
            break
    return "\n".join(lines[start:end])


def run_scripts(workflow: str) -> str:
    lines = workflow.splitlines()
    scripts: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() != "run: |":
            index += 1
            continue
        indentation = len(line) - len(line.lstrip())
        index += 1
        block: list[str] = []
        while index < len(lines):
            candidate = lines[index]
            current = len(candidate) - len(candidate.lstrip())
            if candidate.strip() and current <= indentation:
                break
            block.append(candidate)
            index += 1
        scripts.extend(block)
    return "\n".join(scripts)


class ReleasePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        cls.package = mapping_block(cls.workflow, "  package:")
        cls.publish = mapping_block(cls.workflow, "  publish-release:")

    def test_packaging_is_read_only_and_publish_has_minimal_write_scope(self) -> None:
        root = self.workflow.split("jobs:", 1)[0]
        self.assertIn("permissions:\n  contents: read", root)
        self.assertNotIn("contents: write", self.package)
        self.assertEqual(self.publish.count("contents: write"), 1)
        self.assertEqual(self.workflow.count("contents: write"), 1)
        self.assertEqual(self.workflow.count("persist-credentials: false"), 2)

    def test_publish_requires_tag_push_and_is_serialized_by_ref(self) -> None:
        self.assertIn(
            "if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')",
            self.publish,
        )
        concurrency = mapping_block(self.workflow, "concurrency:")
        self.assertIn("group: skillsentra-release-${{ github.ref }}", concurrency)
        self.assertIn("cancel-in-progress: false", concurrency)

    def test_untrusted_ref_values_reach_shell_only_through_environment(self) -> None:
        scripts = run_scripts(self.workflow)
        self.assertNotIn("${{ github.ref_name }}", scripts)
        self.assertNotIn("${{ github.event.repository.default_branch }}", scripts)
        self.assertNotIn("${{ github.sha }}", scripts)
        for binding in (
            "RELEASE_TAG: ${{ github.ref_name }}",
            "DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}",
            "EVENT_SHA: ${{ github.sha }}",
        ):
            with self.subTest(binding=binding):
                self.assertIn(binding, self.publish)

    def test_source_identity_and_downloaded_artifact_are_reverified(self) -> None:
        for control in (
            "fetch-depth: 0",
            "git show-ref --verify --quiet",
            "git merge-base --is-ancestor",
            "python scripts/verify_release.py release",
            'manifest.get("version") == expected_version',
            'manifest.get("commit") == expected_commit',
            'manifest.get("source_dirty") is False',
            'manifest.get("source_changes") == []',
        ):
            with self.subTest(control=control):
                self.assertIn(control, self.publish)

    def test_existing_release_is_immutable_and_metadata_is_not_called_attestation(self) -> None:
        for forbidden in ("gh release edit", "gh release upload", "--clobber"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.publish)
        self.assertIn('gh release view "$RELEASE_TAG"', self.publish)
        self.assertIn("Release already exists", self.publish)
        self.assertIn('gh release create "$RELEASE_TAG"', self.publish)
        self.assertIn("unsigned-build-metadata.intoto.jsonl", self.publish)
        self.assertIn("unsigned build metadata, not a GitHub or Sigstore attestation", self.publish)

    def test_future_release_uses_the_dedicated_chatgpt_identity(self) -> None:
        self.assertTrue(self.workflow.startswith("name: SKillSentra for Chatgpt release"))
        self.assertIn("skillsentra-for-chatgpt-${EXPECTED_VERSION}.zip", self.publish)
        self.assertIn("SKillSentra for Chatgpt ${RELEASE_TAG}", self.publish)
        self.assertNotIn("--prerelease", self.publish)
        self.assertNotIn("Private Beta", self.workflow)
        self.assertIn("npm ci --ignore-scripts", self.package)


if __name__ == "__main__":
    unittest.main()
