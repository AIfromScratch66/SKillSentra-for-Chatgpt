from __future__ import annotations

import json
import unittest
from pathlib import Path

import app


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "skillsentra"


class ChatGPTPackageTests(unittest.TestCase):
    def test_dedicated_identity_and_versions_are_consistent(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        portable = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
        compatibility = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )

        self.assertEqual(package["name"], "skillsentra-for-chatgpt")
        self.assertEqual(package["version"], "0.8.0")
        self.assertEqual(app.__version__, package["version"])
        self.assertEqual(portable["name"], package["name"])
        self.assertEqual(portable["version"], package["version"])
        self.assertEqual(compatibility["name"], package["name"])
        self.assertTrue(compatibility["version"].startswith(package["version"] + "+chatgpt."))

    def test_portable_manifest_has_truthful_chatgpt_metadata(self) -> None:
        manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["$schema"],
            "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        )
        extension = manifest["extensions"]["com.openai"]
        self.assertNotIn("apps", extension)
        interface = extension["interface"]
        self.assertEqual(interface["displayName"], "SKillSentra for Chatgpt")
        self.assertEqual(interface["shortDescription"], "Govern Skills with evidence")
        self.assertIn("not made or endorsed by OpenAI", interface["longDescription"])
        self.assertEqual(
            set(interface["capabilities"]),
            {
                "Discover Skills",
                "Read versioned project evidence",
                "Create Skill projects",
                "Run confirmed AI and validation actions",
                "Submit context-bound proposals",
            },
        )
        prompts = interface["defaultPrompt"]
        self.assertEqual(len(prompts), 3)
        self.assertTrue(all(len(prompt) <= 128 and "@" not in prompt for prompt in prompts))
        self.assertNotIn("evidence-backed", prompts[0].lower())
        for key in ("composerIcon", "logo"):
            self.assertTrue((PLUGIN / interface[key]).is_file())

    def test_unregistered_package_does_not_invent_remote_wiring(self) -> None:
        self.assertFalse((PLUGIN / "mcp.json").exists())
        self.assertFalse((PLUGIN / ".app.json").exists())
        guide = (ROOT / "CHATGPT.md").read_text(encoding="utf-8")
        self.assertIn("public server URL", guide)
        self.assertIn("plugin_asdk_app", guide)
        self.assertIn("public source edition", guide)

    def test_compose_uses_explicit_minimal_gateway_secret_injection(self) -> None:
        compose = (ROOT / "compose.chatgpt.yaml").read_text(encoding="utf-8")
        gateway_env = (ROOT / ".env.chatgpt-mcp.example").read_text(encoding="utf-8")
        self.assertNotIn("env_file:", compose)
        self.assertIn("SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN: ${SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN:?", compose)
        self.assertIn("SKILLSENTRA_API_TOKEN: ${SKILLSENTRA_API_TOKEN:?", compose)
        self.assertIn("SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN=", gateway_env)
        self.assertIn("SKILLSENTRA_API_TOKEN=", gateway_env)
        guide = (ROOT / "CHATGPT.md").read_text(encoding="utf-8")
        self.assertIn("--env-file .env.chatgpt-mcp", guide)

    def test_chatgpt_runtime_files_are_in_release_inventory(self) -> None:
        from scripts.build_release import release_files

        paths = {path.relative_to(ROOT).as_posix() for path in release_files("0.8.0")}
        for required in (
            "CHATGPT.md",
            "Dockerfile.chatgpt-mcp",
            "compose.chatgpt.yaml",
            ".env.example",
            ".env.chatgpt-mcp.example",
            "package-lock.json",
            "plugins/skillsentra/plugin.json",
            "plugins/skillsentra/scripts/chatgpt_mcp_server.py",
            "product/04-quality/test-plan-v0.8.0.md",
            "evidence/validation-report-v0.8.0.md",
            "THIRD_PARTY_NOTICES.md",
            "scripts/build_chatgpt_plugin.py",
        ):
            self.assertIn(required, paths)

    def test_ci_builds_and_health_checks_the_chatgpt_container(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertIn("chatgpt-container:", workflow)
        self.assertIn("docker compose --env-file .env.chatgpt-mcp.example", workflow)
        self.assertIn("docker build -f Dockerfile.chatgpt-mcp", workflow)
        self.assertIn("http://127.0.0.1:8787/healthz", workflow)
        self.assertIn("npm ci --ignore-scripts", workflow)


if __name__ == "__main__":
    unittest.main()
