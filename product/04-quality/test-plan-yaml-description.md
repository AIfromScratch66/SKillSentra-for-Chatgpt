# YAML description maintenance test plan

Date: 2026-10-24. Baseline: `a3fc70d708d135861e22f19fc54f1585c7d3af83` on the independent `SKillSentra-for-Chatgpt` repository. Existing release: v0.8.0.

## Requirements and cases

| ID | Requirement | Method and expected result |
| --- | --- | --- |
| Y01 | Read the screenshot's Chinese folded description completely | Exact expected text includes both sentences with YAML folding and final newline. |
| Y02 | Honor block scalar semantics | Fixed expected values for `>`, `>-`, `>+`, `|`, `|-`, `|+`, indentation indicators, header comments, paragraphs and more-indented content. |
| Y03 | Preserve compatible textual metadata | Single-line, escaped/quoted and multiline plain/quoted scalars, Unicode, BOM, CRLF, numeric-looking versions and boolean-looking names remain strings. |
| Y04 | Recognize only standalone frontmatter delimiters | Inline or indented `---` stays in the description; body fields and missing/malformed fences cannot become metadata. |
| Y05 | Reject unsafe or malformed input without executing it | Invalid YAML and unsafe tags return no partial metadata; list/mapping descriptions are omitted and nested fields cannot override top-level fields. |
| Y06 | Exercise actual import paths | Register, freeze and scan a Chinese multiline source; retain exact description, source bytes and snapshot bytes; no false `DESCRIPTION_THIN` finding. |
| Y07 | Distribute the parser dependency | Pinned requirements are installed in CI/backend Dockerfile, included in the source ZIP, and listed as required in the SBOM. |
| Y08 | Preserve existing behavior | Full Python suite, compilation, documentation links, frontend syntax, AI truth contracts and existing Chromium journeys. |
| Y09 | Verify delivery | Build from clean committed source, verify source/inventory hashes, reconstruct the ZIP and run with isolated dependencies; push without force and verify remote SHA/CI. |

## Execution conditions

Use isolated temporary databases/artifact directories and a pinned PyYAML installation. Execute the new parser cases against the baseline before implementation; record original failures, fixes and retests separately. Run the browser journeys with the existing test harness and a real local Chromium runtime. A failed or unavailable step remains failed or not run.

Completion requires the targeted checks and affected regression checks to pass, with actual test results in the validation report. The native AI Devloop change gate remains `BLOCKED / CHANGE_CHAIN_CONTEXT_MISMATCH`; the user explicitly authorized independent candidate continuation. Local tests do not override that record or grant deployment authority.
