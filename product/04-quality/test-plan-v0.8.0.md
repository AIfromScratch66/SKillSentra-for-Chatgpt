# SKillSentra for Chatgpt 0.8.0 test plan

Date: 2026-10-04. Scope: the public source snapshot proposed for `AIfromScratch66/SKillSentra-for-Chatgpt`.

## Required checks

1. Run the complete Python unit and contract suite.
2. Validate JavaScript syntax and the Chromium browser journeys.
3. Exercise the local MCP-over-HTTP adapter for initialization, tool discovery, representative reads, rejected unconfirmed writes, confirmed test-data writes, request bounds, origin checks, and authentication failures.
4. Build the deterministic release archive and verify its inventory, hashes, version, and source identity.
5. Validate Markdown links and scan the public snapshot for credentials, personal absolute paths, private repository links, generated data, and private Git history.
6. Require the pull-request checks to pass before merge, create `v0.8.0` only from the merged `main` commit, and require the tag workflow to pass before treating the GitHub Release as complete.

## Acceptance boundary

These checks authorize a public GitHub source release only. They do not demonstrate a public MCP deployment, MCP OAuth 2.1, official MCP Inspector interoperability, a real ChatGPT connection, ChatGPT Directory approval, production readiness, or real-user value.
