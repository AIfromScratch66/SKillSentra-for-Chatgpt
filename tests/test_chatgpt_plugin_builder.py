from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import scripts.build_chatgpt_plugin as builder
from scripts.build_chatgpt_plugin import PLUGIN, PORTABLE_MCP_SCHEMA, build, validate_mcp_url


class ChatGPTPluginBuilderTests(unittest.TestCase):
    def test_url_must_be_public_looking_https_mcp_endpoint(self) -> None:
        invalid = (
            "http://mcp.acme.com/mcp",
            "https://localhost/mcp",
            "https://127.0.0.1/mcp",
            "https://mcp.example/mcp",
            "https://user:secret@mcp.acme.com/mcp",
            "https://mcp.acme.com/not-mcp",
            "https://mcp.acme.com/mcp?token=secret",
            "https://mcp.acme.com:bad/mcp",
            "https://mcp.acme.com:/mcp",
            "https://mcp.acme.com:0/mcp",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_mcp_url(value)

        self.assertEqual(validate_mcp_url("https://mcp.acme.com:443/mcp"), "https://mcp.acme.com:443/mcp")

    def test_builder_creates_rooted_portable_zip_without_legacy_wiring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = build("https://mcp.acme.com/mcp", Path(directory))
            archive = Path(directory) / str(result["artifact"])
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                self.assertIn("plugin.json", names)
                self.assertIn("mcp.json", names)
                self.assertIn("skills/skillsentra-for-chatgpt/SKILL.md", names)
                self.assertIn("assets/skillsentra-icon.svg", names)
                self.assertFalse(any(name.startswith("plugins/") for name in names))
                self.assertFalse(any(name.startswith("scripts/") for name in names))
                self.assertFalse(any(name.startswith(".codex-plugin/") for name in names))
                self.assertNotIn(".mcp.json", names)

                plugin = json.loads(package.read("plugin.json"))
                mcp = json.loads(package.read("mcp.json"))
                agent = package.read("skills/skillsentra-for-chatgpt/agents/openai.yaml").decode("utf-8")

            self.assertEqual(plugin["name"], "skillsentra-for-chatgpt")
            self.assertEqual(plugin["version"], "0.8.0")
            self.assertEqual(mcp["$schema"], PORTABLE_MCP_SCHEMA)
            self.assertEqual(
                mcp["mcpServers"]["skillsentra-for-chatgpt"],
                {"type": "streamable-http", "url": "https://mcp.acme.com/mcp"},
            )
            self.assertIn('display_name: "SKillSentra for Chatgpt"', agent)
            self.assertIn('short_description: "Govern Skills with evidence"', agent)
            self.assertIn('description: "Registered remote SkillSentra HTTPS /mcp connection"', agent)
            self.assertIn('transport: "streamable-http"', agent)
            self.assertEqual(len(str(result["sha256"])), 64)

    def test_builder_rejects_output_inside_plugin_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_plugin = Path(directory) / "plugin"
            shutil.copytree(PLUGIN, temporary_plugin)
            with mock.patch.object(builder, "PLUGIN", temporary_plugin):
                with self.assertRaisesRegex(ValueError, "outside the plugin source tree"):
                    builder.build("https://mcp.acme.com/mcp", temporary_plugin / "assets" / "output")

    def test_builder_rejects_symbolic_links_in_portable_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_plugin = Path(directory) / "plugin"
            shutil.copytree(PLUGIN, temporary_plugin)
            link = temporary_plugin / "assets" / "linked-icon.svg"
            try:
                link.symlink_to(temporary_plugin / "assets" / "skillsentra-icon.svg")
                symlink_patch = contextlib.nullcontext()
            except OSError:
                link.write_text("not a real icon", encoding="utf-8")
                original_is_symlink = Path.is_symlink
                symlink_patch = mock.patch.object(
                    Path,
                    "is_symlink",
                    lambda path: path.name == link.name or original_is_symlink(path),
                )
            with mock.patch.object(builder, "PLUGIN", temporary_plugin), symlink_patch:
                with self.assertRaisesRegex(ValueError, "symbolic links"):
                    builder.build("https://mcp.acme.com/mcp", Path(directory) / "output")

    def test_portable_metadata_transform_fails_closed_when_source_drifts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_plugin = Path(directory) / "plugin"
            agent = temporary_plugin / "skills" / "skillsentra-for-chatgpt" / "agents" / "openai.yaml"
            agent.parent.mkdir(parents=True)
            agent.write_text('display_name: "Unexpected"\n', encoding="utf-8")
            with mock.patch.object(builder, "PLUGIN", temporary_plugin):
                with self.assertRaisesRegex(ValueError, "Expected exactly one portable metadata marker"):
                    builder._portable_bytes(agent)


if __name__ == "__main__":
    unittest.main()
