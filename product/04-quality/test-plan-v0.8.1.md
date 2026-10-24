# v0.8.1 release verification plan

Baseline: `9ea8012b160ee5cbfea61879023286c3bd6d9bff`. The YAML parser and its regression cases stay unchanged from the tested fix. This iteration packages that fix as a new GitHub source download, with explicit user authorization.

1. Check version consistency across package/lock, backend, portable and compatibility manifests, MCP fallback and Compose image labels.
2. Run affected release/artifact/plugin/API/health tests and local documentation checks; preserve the parser SHA-256 and substantive test expectations.
3. Require the existing pull-request test and release-package workflows to pass. They execute the full Python and Chromium suites, dependency installation, source packaging, inventory/SBOM verification and synthetic backup/performance checks.
4. Merge the reviewed release PR into `main`, create an annotated `v0.8.1` tag on the exact merged commit, and let the existing tag-only publish workflow create immutable release assets after rerunning checks.
5. Download published assets, verify SHA-256 and file inventory against the exact tag, check package version 0.8.1 and unchanged YAML parser, then install dependencies in an isolated directory and verify complete Chinese description parsing.
6. Confirm GitHub's latest release points to v0.8.1 and preserve the old v0.8.0 release assets.

Failures block the affected publication step. The AI Devloop native engineering gate remains BLOCKED; no automatic test result advances a formal lifecycle phase, changes global configuration or deploys a service. Local Docker execution remains outside the observed local evidence when its engine is unavailable; GitHub gateway-container evidence is separate.
