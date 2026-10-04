from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import zipfile
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "skillsentra"
PORTABLE_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


def validate_mcp_url(value: str) -> str:
    url = value.strip()
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path.rstrip("/") != "/mcp"
    ):
        raise ValueError("MCP URL must be an HTTPS origin plus the /mcp path, without credentials, query, or fragment")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("MCP URL must use a valid HTTPS port") from exc
    if parts.netloc.endswith(":") or port == 0:
        raise ValueError("MCP URL must use a valid HTTPS port")
    hostname = (parts.hostname or "").rstrip(".").lower()
    if not hostname or hostname == "localhost" or hostname.endswith((".localhost", ".invalid", ".example", ".test")):
        raise ValueError("MCP URL must use a public-looking hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("MCP URL must not use a private, loopback, link-local, or reserved IP address")
    return url.rstrip("/")


def _files() -> list[Path]:
    plugin_root = PLUGIN.resolve()
    manifest = PLUGIN / "plugin.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Portable plugin manifest must be a regular file inside the plugin root")
    try:
        manifest.resolve(strict=True).relative_to(plugin_root)
    except (OSError, ValueError) as exc:
        raise ValueError("Portable plugin manifest must stay inside the plugin root") from exc
    files = [manifest]
    for directory in (PLUGIN / "assets", PLUGIN / "skills"):
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise ValueError("Portable plugin sources must not contain symbolic links")
            if path.is_file():
                try:
                    path.resolve(strict=True).relative_to(plugin_root)
                except (OSError, ValueError) as exc:
                    raise ValueError("Portable plugin sources must stay inside the plugin root") from exc
                files.append(path)
    return sorted(set(files), key=lambda path: path.relative_to(PLUGIN).as_posix().encode("utf-8"))


def _portable_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    relative = path.relative_to(PLUGIN).as_posix()
    if relative.endswith("/agents/openai.yaml"):
        text = raw.decode("utf-8")
        replacements = (
            ('display_name: "SKillSentra for Chatgpt"', 'display_name: "SKillSentra for Chatgpt"'),
            (
                'short_description: "Search and govern exact Agent Skill versions"',
                'short_description: "Govern Skills with evidence"',
            ),
            (
                'description: "Bundled stdio bridge to the configured SkillSentra HTTP service"',
                'description: "Registered remote SkillSentra HTTPS /mcp connection"',
            ),
            ('transport: "stdio"', 'transport: "streamable-http"'),
        )
        for source, replacement in replacements:
            if text.count(source) != 1:
                raise ValueError(f"Expected exactly one portable metadata marker in {relative}: {source}")
            text = text.replace(source, replacement, 1)
        raw = text.encode("utf-8")
    return raw


def build(mcp_url: str, output: Path) -> dict[str, object]:
    endpoint = validate_mcp_url(mcp_url)
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    output = output.resolve()
    try:
        output.relative_to(PLUGIN.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("Portable plugin output must be outside the plugin source tree")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"skillsentra-for-chatgpt-plugin-{version}.zip"
    mcp_config = {
        "$schema": PORTABLE_MCP_SCHEMA,
        "mcpServers": {
            "skillsentra-for-chatgpt": {
                "type": "streamable-http",
                "url": endpoint,
            }
        },
    }
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path in _files():
            relative = path.relative_to(PLUGIN).as_posix()
            info = zipfile.ZipInfo(relative)
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, _portable_bytes(path))
        info = zipfile.ZipInfo("mcp.json")
        info.date_time = (2026, 1, 1, 0, 0, 0)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        package.writestr(info, json.dumps(mcp_config, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return {
        "artifact": archive.name,
        "sha256": digest,
        "version": version,
        "mcp_url": endpoint,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a portable SKillSentra for Chatgpt plugin ZIP for a public-looking HTTPS /mcp URL"
    )
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release" / "chatgpt-plugin")
    args = parser.parse_args()
    try:
        result = build(args.mcp_url, args.output.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ChatGPT plugin build failed: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
