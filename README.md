# SKillSentra for Chatgpt

> Govern exact Agent Skill versions with evidence from ChatGPT.

[中文说明](README.zh-CN.md)

## Status

The current public source release is **v0.8.0**, based on SkillSentra Personal v0.6.5. It adds a stateless JSON-response MCP-over-HTTP adapter at `POST /mcp`, intended for Streamable HTTP clients, while preserving SkillSentra's exact-version evidence, explicit-write confirmation and human release gates.

The dedicated product name is **SKillSentra for Chatgpt** and the short description is **Govern Skills with evidence**. It is an independent SkillSentra integration and is not made or endorsed by OpenAI.

This repository and its v0.8.0 GitHub Release are a public source distribution. They are not a public MCP deployment, a verified ChatGPT connection, production approval, or ChatGPT Directory approval. The portable ZIP remains a candidate skeleton for operators to configure and validate. See [ChatGPT setup and security boundaries](CHATGPT.md), the [v0.8.0 release notes](product/05-release/release-notes-v0.8.0.md), and the [current validation report](evidence/validation-report-v0.8.0.md).

## What v0.8.0 adds

- A dependency-free, stateless JSON-response MCP-over-HTTP preview at `POST /mcp`, reusing the same 19 versioned MCP tool definitions and execution layer as the stdio bridge. GET/SSE and session lifecycle are not implemented.
- Loopback-first startup, bounded JSON requests, configurable Origin checks, and gateway-secret authentication by default on every listener; anonymous mode is an explicit loopback-only test exception.
- A portable Agent Plugins manifest with the distinct identity `skillsentra-for-chatgpt`; the legacy Codex manifest remains as a compatibility fallback.
- A deterministic portable-plugin builder that accepts a public-looking HTTPS `/mcp` URL and emits a correctly rooted candidate skeleton without fabricating registration metadata. Its static URL checks do not verify endpoint existence, OAuth, ChatGPT compatibility or submission readiness.
- ChatGPT-specific deployment, testing and evidence boundaries without inventing a public URL, registration ID, OAuth approval, or directory status.

## Start the ChatGPT adapter locally

Start SkillSentra first, then start the MCP gateway in a second terminal:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-dev.ps1
$env:SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN = "<generated gateway secret>"
$env:SKILLSENTRA_API_TOKEN = "<different SkillSentra service token>"
python plugins/skillsentra/scripts/chatgpt_mcp_server.py
```

The local MCP endpoint is `http://127.0.0.1:8787/mcp`. A non-ChatGPT client sends the gateway secret as its Bearer token; the gateway uses only the separate service token upstream. For isolated loopback protocol testing, anonymous mode must be opted into explicitly with `SKILLSENTRA_CHATGPT_ALLOW_ANONYMOUS_LOOPBACK=true`. ChatGPT cannot present this custom static API key or shared Bearer secret. Do not expose this preview server directly to the internet or treat a TLS reverse proxy as sufficient authentication.

Real ChatGPT Developer Mode testing requires either [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) or a public HTTPS security gateway that implements [MCP OAuth 2.1 authentication](https://developers.openai.com/plugins/build/auth), including discovery metadata, authorization code with PKCE `S256`, resource/audience validation and scope enforcement. Neither path has been implemented and verified for this candidate.

## Compatibility and evidence boundary

Passing unit, protocol and local browser tests demonstrates behavior only for the tested commit. It does not by itself prove official MCP Inspector interoperability, a working ChatGPT connection, public directory approval, production deployment or real-user value. Neither official MCP Inspector nor real ChatGPT has been recorded as passed for v0.8.0. Real ChatGPT evidence requires tool discovery and representative read/write journeys in ChatGPT Developer Mode against the exact deployed commit. Such evidence still does not authorize installation, update application, production deployment or directory publication.

ChatGPT writes remain bounded by the existing tool schemas. State-changing tools require explicit confirmation, and proposal write-back remains bound to an exact request ID and context hash. A successful write is not acceptance, phase completion or release approval.

## What v0.6.5 adds

Local QA fixes acknowledgement boundaries, generated-case provenance and template domain isolation. The default browser command now includes AI truth contracts. Detailed v0.6.5 process records remain in the private Personal repository and are not included in this public snapshot. Prior version summaries below are historical.

## What v0.6.3 adds

- Policy qualification now reuses the receipt transaction instead of opening a second SQLite session. The final local synthetic run measured the in-transaction predicate at p95 0.0115 ms and full receipt ingestion at p95 53.4933 ms; both are local engineering evidence, not production-capacity claims.
- Separate `/livez`, `/readyz`, and bounded `/metrics` probes. Container health checks now fail closed when SQLite or the migration ledger is unavailable, while request metrics retain no path, query, tenant, or body data.
- GitHub Top 100 refreshes use configurable bounded timeouts, limited exponential retries, auditable refresh metadata, and last-successful-snapshot fallback. Failed cold starts remain explicitly unavailable rather than fabricating candidates.
- Google and ChatGPT sign-in diagnostics list missing server-side prerequisites without exposing values. ChatGPT sign-in remains unavailable until real external-application approval and callback integration exist.
- GitHub release packaging is read-only by default; only the tag-only prerelease job receives content-write permission, after verifying the tag matches the package version.

## What v0.6.2 added

- Tenant-scoped AI connections and local Skill sources. Project policy binding now rejects a connection owned by another tenant.
- A bounded GitHub `topic:skill` Top 100 candidate snapshot ordered by repository stars. Every entry remains an `unverified_candidate`, requires static scanning, and is neither an installable catalog nor the platform's verified weekly ranking.
- More reliable Windows MCP startup through explicit UTF-8 I/O, manifest-derived server version reporting, and dedicated composer/logo assets.
- Local regression fixes for bounded discovery transport, non-ASCII MCP responses, marketplace fallback states, and browser journeys. The full suite must still pass for the exact candidate before installation.

## What v0.6.1 adds

- A responsive account flow: immediate in-progress feedback, duplicate-submit protection, a clear network deadline, and browser-visible password requirements.
- Config-gated Google OAuth authorization-code sign-in with a short-lived, single-use state, verified email identity binding, and no third-party token storage. Set the three `SKILLSENTRA_GOOGLE_*` variables in `.env.example` and register the callback URI in Google Cloud.
- A truthful Sign in with ChatGPT entry: the product explains that it requires OpenAI external-application partner access and credentials. It remains unavailable until that access is granted; no simulated ChatGPT identity is created.

## What v0.6.0 added

- A ten-role, internally simulated expert panel with eight weighted dimensions, E0-E5 evidence levels, exact artifact binding, Evaluation World fields, and fail-closed score caps.
- Bounded GitHub repository and explicit HTTPS catalog discovery. Every external result remains an `unverified_candidate` and requires a fixed commit plus static scanning.
- A real rolling seven-day leaderboard based on recent post-acquisition reviews and paid sandbox acquisitions. Lifetime totals are reported separately; zero-signal entries do not rank.
- A bundled Codex plugin and stdio MCP bridge with eleven tools. Seven are read-only; four state-changing tools require `confirmed: true` after explicit user confirmation.
- Visual Studio and control-plane surfaces for creating, evaluating, comparing, delivering, and checking Skill updates without silently applying or installing them.

The plugin bridge does **not** install a Skill, invoke a downloaded Skill at runtime, apply an update, publish a version, deploy to production, or authorize a release. A successful local MCP call or internal score is not real ChatGPT cloud evidence.

## Run locally

Requirements: Windows PowerShell and Python 3.11+.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-dev.ps1
```

Open:

- Marketplace: <http://127.0.0.1:8766/>
- Skill Studio: <http://127.0.0.1:8766/studio.html>
- Evidence Control Plane: <http://127.0.0.1:8766/platform.html>

Run the deterministic suite:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
```

The v0.6.3 verification record is generated with the deterministic suite and browser journeys before installation. A local pass remains distinct from production release authorization.

## Main capabilities

| Area | Current local capability | Boundary |
|---|---|---|
| Lifecycle | Create, evaluate, compare, deliver, and check upstream changes | No automatic merge, install, or runtime invocation |
| Discovery | Search GitHub repositories or an explicit public HTTPS catalog | Results are unverified and never executed |
| Expert review | Structured internal panel, evidence levels, hard gates, API and visual review | Internal simulation is not external endorsement |
| ChatGPT integration | Portable candidate metadata plus stdio and stateless JSON-response MCP-over-HTTP protocol preview | Official MCP Inspector and real ChatGPT Developer Mode remain unverified |
| Marketplace | Trust Passport, acquisition, post-acquisition review, seven-day ranking | Sandbox economics only; no production payout |
| Runtime governance | Exact digests, tenant-scoped repository snapshots, Agent state and revocation | Single-node pre-production baseline |

## Release boundary

v0.8.0 is authorized for public GitHub source distribution. It does not close the production hard gates. Public production/GA, real-money movement, automatic Skill installation or update application, a public cloud MCP deployment, and ChatGPT Directory submission remain unauthorized.

Remaining production blockers include real ChatGPT cloud connectivity, real-user outcome evidence, cryptographically bound approval receipts, production observability, and an environment with TLS, managed secrets, encrypted backups, restoration evidence, and independent human release authorization. Internal scores and local test results do not override those gates.

## Documentation

- [Development guide](DEVELOPMENT.md)
- [ChatGPT adapter guide](CHATGPT.md)
- [v0.8.0 release notes](product/05-release/release-notes-v0.8.0.md)
- [v0.8.0 validation report](evidence/validation-report-v0.8.0.md)
- [Security policy](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

No open-source license is granted. This public repository follows the Personal edition's existing licensing posture: source-visible, all rights reserved. You may inspect and run the software for your own authorized use, but copying, modification, redistribution, sublicensing, or derivative distribution requires permission from the copyright holder. Third-party components remain subject to their own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).
