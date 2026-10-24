# YAML description validation — 2026-10-24

Baseline: `a3fc70d708d135861e22f19fc54f1585c7d3af83`. Scope: independent public SKillSentra-for-Chatgpt source maintenance. Test plan: [Y01–Y09](../product/04-quality/test-plan-yaml-description.md).

Environment: Windows, Python 3.12.14, PyYAML 6.0.3 installed into an isolated dependency directory, Node 24.19.0, Playwright 1.62.1 and local Chromium. Tests use temporary databases/artifact directories.

Implementation under test: `b31374fc9a6fab71ac8e75714b12ec1c763500e4`. The following documentation-only update records its executed results; it does not change the application or test code.

## Executed results

| Check | Result | Evidence |
| --- | --- | --- |
| New parser cases against the original implementation | Expected failure | 15 methods produced 23 assertion failures and 1 error; screenshot format was reproduced. |
| Parser and SkillEngine targeted regression | PASS | 24 tests, 29.060 seconds; exact Chinese text, block semantics, compatibility, registration, snapshot bytes and scan findings. |
| Full Python suite, first attempt | FAIL retained | 258/259 passed; the existing HTTP concurrency case encountered Windows connection abort `WinError 10053`. |
| Same concurrency case, unchanged isolated retest | PASS | 1 test, 0.543 seconds. No production or test assertion changes were made for this interruption. |
| Full Python suite, complete retest without concurrent browser run | PASS | 259 tests, 243.250 seconds. |
| Python compilation, existing documentation checker, five frontend syntax checks and AI truth contracts | PASS | `scripts/test.ps1` completed its remaining checks; 6/6 truth contracts passed. |
| Chromium journeys plus frontend truth contracts | PASS | 22/22 tests; 93.466 seconds. The six truth cases overlap with the previous row and are not additional unique tests. |
| Touched-document links and workflow YAML | PASS | Local links in the 7 changed/new Markdown documents checked separately, including the Chinese README; both workflow files parsed as YAML. |
| Scoped diff | PASS | 16 files within the frozen task scope; no changes to the other SkillSentra checkout. |
| Clean-source reconstruction | PASS | Built and source-verified 107 files from clean implementation commit `b31374f`; extracted to a fresh directory, freshly installed its pinned requirements, and executed Python in isolated mode. Parsing, registration, scanning and marketplace summary all preserved the complete Chinese description. |
| Local Docker container execution | NOT RUN | Docker CLI exists, but Docker Desktop Linux engine pipe is absent. No container-runtime pass is claimed. |

## Reproduction and maintenance

The old parser returned `>` for the screenshot's block description. The fixed parser returns both Chinese sentences, separated by the YAML folded space, with the specified final newline. The source and frozen snapshot bytes are preserved. Runtime dependency installation, CI and the backend Dockerfile now use the same pinned requirements; source ZIP inventory and SBOM tests include PyYAML.

Run `python -m pip install -r requirements.txt` in the same interpreter used for the service/tests, then `scripts/test.ps1` and `npm run test:e2e` with the configured browser runtime. See the [maintenance guide](../product/05-release/yaml-description-maintenance.md) for the interpreter selection and behavior boundary.

## Retained log digests

| Local receipt | SHA-256 |
| --- | --- |
| Baseline parser regression | `ee45b0e92a28fa313e3d441cca5d2935f2d02bed21a20af91d92fdc8367f0f73` |
| Targeted regression | `03ccac949f8e6ede3cb754d00bec9a6629b683236b32f0561ae981b94812c991` |
| Chromium and truth contracts | `4be0c830e0a9acb8568fb66fb125813d770482401b0ad8eb330bd783829a2c2f` |
| First full-suite attempt | `3e285c4dcc41187c60b82944323f4e29ae8e75a6dfdabac2d11f53f552029ef0` |
| Passing full-suite retest | `87b77bf88ad47684572d6bd1bd5430d67a74f285bde9a6c8ba8553cef1901b57` |

Implementation validation archive: `skillsentra-for-chatgpt-0.8.0.zip`, 977,751 bytes, 107 files, SHA-256 `90cabadc2307a79a865e2e9e2601982c50e460db6c4ae427a8e97c5f1ea208f1`. This identifies the locally rebuilt implementation archive, not a replacement for the historical GitHub v0.8.0 release asset. Build manifest `source_dirty=false`; archive inventory, source bytes and hashes were verified. The archive contains `requirements.txt`; its SBOM lists required `PyYAML 6.0.3`.

The original failed attempt is retained separately from the passing retest. These are local execution records; they are not independent trusted execution, production or real-user evidence.

AI Devloop 26.8.9 native `open_devloop` and task-scoped `get_work_context` were executed. `evaluate_change_gate` returned `BLOCKED / CHANGE_CHAIN_CONTEXT_MISMATCH`. The user explicitly authorized independent candidate continuation, with the original block preserved and no AI Devloop configuration/history/deployment changes.
