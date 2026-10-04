# SKillSentra for Chatgpt

`SKillSentra for Chatgpt` is a public source edition derived from SkillSentra Personal. It exposes the existing evidence-bound Skill lifecycle tools through a stateless JSON-response MCP-over-HTTP adapter while leaving the core service, database and human gates intact.

## Identity

- Display name: `SKillSentra for Chatgpt`
- Chinese display name: `SKillSentra for Chatgpt（ChatGPT 专用协作版）`
- Package ID: `skillsentra-for-chatgpt`
- Version: `0.8.0`
- Short description: `Govern Skills with evidence`
- Status: `Public source release`

This is an independent SkillSentra integration and is not made or endorsed by OpenAI.

## Local architecture

```text
Potential MCP client
(ChatGPT and official MCP Inspector not yet verified)
        |
        | Stateless JSON-response MCP-over-HTTP protocol preview at POST /mcp
        v
SkillSentra ChatGPT gateway :8787
        |
        | bounded HTTP API calls, separate service token
        v
SkillSentra service :8766 -> SQLite and isolated artifacts
```

The gateway and the existing stdio adapter import the same tool catalog and request executor. No ChatGPT-only copy of business logic is maintained. GET/SSE and session lifecycle are not implemented in this preview.

## Run locally

1. Copy `.env.example` to your local environment file and keep secrets out of Git.
2. Start SkillSentra:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/run-dev.ps1
   ```

3. Start the gateway:

   ```powershell
   $env:SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN = "<generated gateway secret>"
   $env:SKILLSENTRA_API_TOKEN = "<different SkillSentra service token>"
   python plugins/skillsentra/scripts/chatgpt_mcp_server.py
   ```

4. A planned interoperability check is to connect the official MCP Inspector to `http://127.0.0.1:8787/mcp` and exercise `initialize`, `tools/list`, a read, a rejected unconfirmed write and an explicitly confirmed write against non-production data. This check has not yet been recorded as executed evidence.

The command defaults to `127.0.0.1:8787` and still requires the gateway secret. Anonymous access is available only when loopback is explicit and `SKILLSENTRA_CHATGPT_ALLOW_ANONYMOUS_LOOPBACK=true`; non-loopback anonymous startup is rejected. This direct static Bearer mechanism is for non-ChatGPT clients and protocol-preview testing only. It must not be exposed as ChatGPT-supported public authentication.

For the container pair, prepare both the core service file `.env.production` and the gateway-only `.env.chatgpt-mcp`. Make the gateway secret different from the service token, and make the service token match one appropriately scoped key in `SKILLSENTRA_AUTH_TOKENS_JSON`. Then run:

```powershell
docker compose --env-file .env.chatgpt-mcp -f compose.yaml -f compose.chatgpt.yaml up --build
```

Compose receives only the two required secrets and the optional Origin value for the gateway; it does not inject the core service's password pepper, OAuth secrets, SMTP password or model API keys into the gateway container.

## ChatGPT Developer Mode

ChatGPT cannot call a private loopback address directly, and it cannot present a custom static API key or shared Bearer secret to this gateway as its public authentication contract. Therefore the current endpoint must not be registered as though a TLS reverse proxy alone made it ChatGPT-ready.

A real ChatGPT Developer Mode connection requires one of two separately verified paths:

1. **Private development:** use [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) so the local MCP server stays private. Tunnel setup, workspace association and an end-to-end ChatGPT call have not been performed for this candidate.
2. **Direct public HTTPS:** place a reviewed security gateway/resource server in front of `/mcp` that implements the [OpenAI MCP OAuth requirements](https://developers.openai.com/plugins/build/auth): protected-resource and authorization-server discovery, authorization code with PKCE `S256`, propagation of the `resource` parameter, issuer and audience validation, expiry checks, and per-tool scope enforcement. CIMD, DCR or a predefined client must also be configured as applicable. None of this OAuth path is implemented or verified in this repository.

The older product feature named “Sign in with ChatGPT” is a separate login integration and does not satisfy MCP OAuth 2.1.

The repository intentionally does not include:

- a fabricated public server URL;
- an `.app.json` with a fabricated `plugin_asdk_app...` identifier;
- a claim that OAuth, domain verification, directory review or publication has completed.

After a real MCP connection is registered, its exact technical ID may be mapped in `.app.json` in a separately reviewed change.

Once an operator has a public-looking HTTPS `/mcp` URL, the static builder can inject that value into a portable candidate ZIP:

```powershell
python scripts/build_chatgpt_plugin.py --mcp-url https://your-real-host.example/mcp --output release/chatgpt-plugin
```

The builder rejects HTTP, credentials in URLs, query secrets, loopback/private IPs, reserved placeholder domains and paths other than `/mcp`. These are static syntax checks only: the builder does not prove that the endpoint exists, is reachable, implements OAuth, works with ChatGPT or passes review. The generated ZIP has `plugin.json` and `mcp.json` at its root and excludes the legacy local stdio wiring, but it remains a **portable candidate skeleton**. Building the ZIP is not installation, submission or publication.

## Authentication and data handling

- By default every listener requires a Bearer token that exactly matches `SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN`; comparison is constant-time. Anonymous mode is an explicit loopback-only test exception. This shared-secret preview is not ChatGPT MCP OAuth 2.1, and ChatGPT cannot directly present this custom static credential.
- The incoming gateway secret is never forwarded upstream. SkillSentra receives only the separate `SKILLSENTRA_API_TOKEN`, which controls tenant and role authorization. The two secrets must be different and neither is logged or persisted.
- Configure SkillSentra tenant tokens and roles in the service secret store or deployment environment, never in committed source.
- Origin checks are additive browser-defense controls, not a substitute for authentication.
- Tool inputs and Skill/project content are untrusted data. They never become runtime instructions for the gateway itself.

## Writes and human control

State-changing tools require `confirmed: true` after explicit user confirmation. The current tool surface can create project records, invoke authorized AI steps, run validations, record reviews, check updates, create collaboration requests and submit context-bound proposals. None of these calls installs or executes a downloaded Skill, applies an update, accepts a proposal, advances a lifecycle gate, publishes, deploys, pays or authorizes release.

## Evidence levels

| Level | What it demonstrates | Current branch claim |
|---|---|---|
| Unit and contract tests | Python behavior and protocol contracts for one local state | Full local regression passed 243/243 for the tested implementation; exact receipts are in the validation report |
| Local HTTP integration | `/mcp` initialization, tool discovery, calls and negative security cases | Automated local coverage only; no official MCP Inspector claim |
| Browser regression | Existing SkillSentra web journeys remain intact | Local Chromium and accessibility regression passed 22/22; the PR Chromium job also passed |
| GitHub CI | Remote runners reproduce the committed checks | Final branch and PR test/package workflows passed; the tag workflow reruns them before prerelease publication |
| Real ChatGPT Developer Mode | ChatGPT discovers and uses the deployed endpoint | Not claimed without a real URL and connection record |
| Public directory | OpenAI review and an explicit publish action completed | **NO_GO**; not submitted or authorized |
| Real-user value | Representative users achieve measured outcomes | Not claimed |

## Release and rollback

The authorized GitHub release path is a reviewed pull request into `main`, an annotated `v0.8.0` tag on the exact merged commit, and the controlled workflow-created stable GitHub Release. Do not tag an unmerged feature-branch commit: the workflow requires the tag to be an ancestor of the default branch. The GitHub Release publishes source and deterministic artifacts only; public MCP deployment, a real ChatGPT connection and ChatGPT Directory submission remain separate human-authorized operations and are currently **NO_GO**.

To roll back local development, stop the gateway and remove or disable the ChatGPT connection. Preserve append-only collaboration records for audit. Do not overwrite the database with an older backup after new writes; use the existing controlled restore procedure.

## Package metadata

The portable identity and metadata manifest is `plugins/skillsentra/plugin.json`. The legacy `plugins/skillsentra/.codex-plugin/plugin.json` remains for compatibility. Without an endpoint-injected `mcp.json` or registered `.app.json`, the source tree is not a fully wired, install-tested ChatGPT MCP package. `scripts/build_chatgpt_plugin.py` creates a correctly rooted portable candidate ZIP after an operator supplies a public-looking HTTPS `/mcp` URL; it performs static validation, not endpoint or authentication verification. ChatGPT Developer Mode registration, official MCP Inspector interoperability, OAuth, complete installation testing and public review remain separate **NO_GO** gates.
