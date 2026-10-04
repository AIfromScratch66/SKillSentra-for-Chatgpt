# SkillSentra Security Policy

## Supported profile

Version `0.6.x` is intended for a controlled private beta. The production profile uses hybrid account sessions and operator tokens behind a TLS-terminating reverse proxy, encrypted persistent storage, restricted outbound networking, monitored backups, and an explicit onboarding policy. Production payout is not implemented and remains hard-disabled; only the built-in payout sandbox is available.

## Reporting a vulnerability

Do not open a public issue containing credentials, private repository content, tenant data, exploit details, or payment evidence. Use the private security-reporting channel configured by the deploying organization. Include the affected version, exact request or artifact identity, impact, reproduction boundary, and whether any secret or tenant boundary may have been exposed.

Operators should acknowledge a credible report within two business days, classify it using the runbook, preserve evidence, and immediately use deny, quarantine, revocation, read-only, or settlement freeze controls when a hard invariant may be affected.

## Security invariants

- Authorization, tenant filtering, policy gates, revocation, qualification, and ledger balance are enforced outside the AI model.
- Repository ingestion resolves a ref to a commit, limits archive size and expansion, rejects unsafe paths and links, and does not execute repository code.
- API and model credentials are server-side environment references and are never returned to the browser or stored in project records.
- Artifact authorization uses the full canonical identity (`artifact-canon-v1`, `sha256`, `sha256:<64 hex>`), never a display name.
- Active revocation outranks deployment state, entitlements, local approval caches, and settlement qualification.
- Financial history is append-only; payouts are idempotent and sandbox-only.
- Marketplace reviews require a recorded acquisition; price is excluded from ranking, and statement sources are frozen exactly once.
- Account passwords use salted `scrypt`; opaque session and CSRF tokens are hashed at rest, and session mutations require matching CSRF proof.
- Production account lifecycle can require email verification and one-time password recovery; action tokens are hashed at rest, password recovery revokes existing sessions, and SMTP delivery requires STARTTLS.
- The platform audit stream is tenant-scoped and hash chained. A failed integrity verification is an incident, not a cosmetic warning.

## Deployment requirements

1. Bind the container to a private interface and put a maintained HTTPS reverse proxy or load balancer in front of it.
2. Set `SKILLSENTRA_AUTH_MODE=hybrid`, inject high-entropy operator tokens and a high-entropy `SKILLSENTRA_PASSWORD_PEPPER` through the deployment secret manager. Never commit `.env.production`.
3. Configure `SKILLSENTRA_TRUSTED_ORIGINS` to the exact public HTTPS origin.
4. Use a dedicated persistent volume with encryption, backups, access logging, and restore testing.
5. Inject short-lived GitHub App installation credentials where possible; grant repository contents read-only.
6. Restrict outbound traffic to approved GitHub and AI endpoints. Application DNS checks are a secondary control, not a network boundary.
7. Keep `payout_production` unavailable. No environment variable or API in this release can enable real fund movement.
8. Run the complete automated suite, browser tests, release verification, backup restore rehearsal, and an operator go/no-go review before serving external users.
9. Set `SKILLSENTRA_REQUIRE_EMAIL_VERIFICATION=true` and configure the STARTTLS mailer before enabling self-service onboarding; retain anti-automation controls, moderation, privacy notice and takedown operations.
10. Keep ChatGPT OAuth disabled until OpenAI external-application approval, official credentials, the exact HTTPS callback, and a real authorization-code/PKCE test are independently recorded.

## Known security boundaries

- SQLite is supported for a single-node private beta. A multi-node or high-write deployment requires the PostgreSQL implementation specified by the RFD before general availability.
- Local accounts provide configurable email verification and password recovery in the production profile; MFA, device posture and privileged step-up are not implemented. Google SSO requires a real callback test, and ChatGPT SSO remains an external integration gate.
- Static and heuristic evidence does not prove that a Skill is safe. Dynamic evidence stays `Unknown` unless a separately authorized sandbox produces it.
- The GitHub environment token integration is compatible with short-lived installation tokens but does not itself mint GitHub App tokens.
