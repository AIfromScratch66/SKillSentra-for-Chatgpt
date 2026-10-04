---
name: skillsentra-for-chatgpt
description: Search and govern exact SkillSentra records from ChatGPT, read collaboration requests, return context-bound proposals, and inspect AI usage through the bundled MCP bridge. Use when the user explicitly invokes $skillsentra-for-chatgpt for Skill lifecycle work.
---

# SKillSentra for Chatgpt

Use SkillSentra as an evidence-aware control surface from ChatGPT. The HTTP and stdio transports reuse the same tool catalog and connect to `SKILLSENTRA_BASE_URL`. This local ChatGPT package pins the plugin to the isolated service at `http://127.0.0.1:8866`.

## Workflow

1. When finding or selecting a Skill, search before acting. Use `search_skills` for published SkillSentra records. When the user asks for external discovery, use `discover_skills` for GitHub or a user-specified HTTPS catalog and label every result as an unverified candidate that still requires a fixed commit and static scan. Present the exact `id`, repository, version or digest when available, and let the user select one result. Use `get_skill` to inspect a selected published ID. For collaboration on an already selected project, read `get_project_context` directly; no marketplace search is needed. Never substitute a similar name for an exact ID.
2. For a new lifecycle project, collect the exact project name and route (`template` or `existing`). Explain that `create_project` writes a project record and call it only after the user explicitly confirms that write.
3. Before evaluation, call `get_project_context` for the selected `project_id` and choose the exact `version_id` and validation stage. Explain that `evaluate_project` runs server validation checks, not an AI or ChatGPT call, and writes the returned evidence record. Obtain explicit confirmation before calling it.
4. Before an expert review, call `get_expert_framework`, bind the review to its exact framework version and the evaluated artifact's SHA-256 digest, and label every seat as an internal simulation. Obtain explicit confirmation immediately before `record_expert_review` writes the review.
5. Before checking updates, confirm the exact `project_id`. Explain that `check_updates` refreshes the upstream snapshot when configured and records a Base/Local/Upstream comparison. Obtain explicit confirmation immediately before calling it.
6. Report evidence and unknowns separately. An internal score, simulated expert opinion, passing local check, or successful MCP call is not publication, installation, deployment, or release authorization.

## AI collaboration and usage

- For a browser-created request, call `list_host_requests` and `get_host_request` for the exact project and request. For a user-authorized new request, call `create_host_request` with its route, zero-based step index and instruction. Use the returned server context and `context_hash`; do not reconstruct a digest or silently switch candidates.
- Generate the requested proposal in this host, then call `submit_host_result` after authorization to persist it. Use the exact `request_id` and `context_hash`. Return only the proposal actually produced, identify the host accurately, and omit an unknown model name. The result is stored as a proposal; no field patch, step completion or release is automatically authorized. If context is stale, read it again and regenerate; never resubmit an old proposal under a new digest.
- When the user wants SkillSentra to invoke its configured model API, use `run_ai_step` after confirmation that includes possible API charges. It always requires a real configured provider and cannot fall back to Mock. OpenAI API and compatible-provider calls are distinct from ChatGPT/Codex host proposals.
- Call `get_ai_runs` and `get_ai_contribution` to verify persisted results and usage. Preserve the service's usage status and provenance. Only send optional host Token counts if this host actually exposes them; omit unknown counts instead of estimating them, inserting zero, or deriving counts from text length. Host-reported usage is never independently verified provider usage.
- Read back the exact host request after a write to confirm its stored status. A transport fixture proves protocol behavior only; do not present it as a real ChatGPT model invocation. If these tools are not available in the current host, say that installation/activation remains unverified rather than pretending `$skillsentra-for-chatgpt` ran.

## Boundaries

- Search, context, host-request reads and AI ledger reads are read-only. `discover_skills` performs bounded external network reads but never downloads or runs repository code.
- Project creation, validation, internal-review recording, update checks, real AI calls, host-request creation and host-result submission write auditable records and require explicit confirmation. Pass `confirmed: true` only for the write the user authorized.
- Internal simulated expert reviews belong in development evidence and backend records, not product UI. They do not represent outside expert endorsement or human approval.
- The bridge does not install a Skill, invoke a downloaded Skill at runtime, merge an update, publish a version, or authorize a release. Say so when a request reaches those boundaries.
- Preserve upstream error codes and status instead of guessing success. After a timeout, empty response or server error, completion may be unknown; read the exact request or stored run records before retrying a write, especially a paid AI call. Treat an explicit validation or conflict rejection according to its returned reason.
