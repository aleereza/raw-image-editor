# ADR 0006: Claude Code headless with the app's MCP server

- **Status:** accepted
- **Date:** 2026-11-17 (Phase 0, spike f)
- **Scripts:** `scripts/spikes/mcp_burst_server.py`, `scripts/spikes/claude_headless.py`

## Context

Decision D6 runs Claude through Claude Code on the owner's account. The job scheduler starts headless `claude -p` runs that use the app's MCP tools, with no API key. The spike checked that this works with the official MCP Python SDK and measured time and usage per burst. To spare the plan's usage limits, it used one run on two bursts (seven frames).

## Setup

- **MCP server:** official MCP Python SDK 2.3.0 over stdio. In 2.x, FastMCP is `mcp.server.mcpserver.MCPServer`, and tools return images with `mcp.server.mcpserver.Image`. It has three tools:
  - `list_bursts`;
  - `get_burst_sheet(burst)`: a numbered sheet of the burst's embedded JPEGs at 760 px per frame, with YuNet face crops (180 px) under each frame, scaled to at most 1568 px and saved as JPEG quality 85. A sheet of 4 landscape frames is 1532 × 1398 (494 KB); 3 portrait frames give 845 × 1568 (106 KB). Building one takes 0.7–0.8 s.
  - `suggest_frame(burst, best, reason, rejects)`: records the suggestion as a JSON line.
- **Command:** the prompt goes in on stdin, and the working directory is a scratch folder so no project instructions load:

  ```bash
  claude -p --mcp-config mcp-config.json --strict-mcp-config --tools "" \
    --allowedTools mcp__rie__list_bursts,mcp__rie__get_burst_sheet,mcp__rie__suggest_frame \
    --permission-mode dontAsk --output-format stream-json --verbose --no-session-persistence
  ```

- `--bare` cannot be used: it reads only an API key and ignores the subscription sign-in.

## Findings

- **Run:** Claude Code 2.1.327, with `claude-opus-5-5`, the model the account is set to. Only the three MCP tools were available.
- **Timeline:**

  | Time | Event |
  |---|---|
  | 1.5 s | first event |
  | 4.9 s | `list_bursts` |
  | 8.6 s | both sheets requested in parallel |
  | 14.7 s and 15.6 s | suggestions recorded |
  | 17.4 s | done (19.3 s wall time) |

- **Usage:** 6 turns and 28.3k input tokens: 9.9k written to the prompt cache, 18.3k read from it. Output was 694 tokens, 225 of them thinking. The API-equivalent cost was $0.097, about $0.05 per burst.
- **Plan usage:** the stream had one `rate_limit_event`, at the start. It showed the five-hour window 48% used and the seven-day window 17% used. These readings count everything the account does at the time, this development session included, and no reading comes at the end of a run. So the share of one small run cannot be measured; a real folder will show it.
- **Suggestions:** both were sensible, and each came with a one-line reason and frames to reject.
  - In the outdoor group burst, Claude picked the frame where everyone looks at the camera with eyes open.
  - In the indoor burst, it picked the frame with open eyes and a face not hidden by the spoon. That is also the only frame of the three whose recorded focus distance matches the subject; the other two report 5 m.

## Decision

- Keep D6. Phase 3's runner uses the command above:
  - it parses stream-json;
  - it records each `rate_limit_event` reading with the run;
  - it chunks bursts;
  - it caches answers by sheet hash and prompt version.
- The MCP server is built on `MCPServer` from the official SDK 2.x.
- The spike's server is throwaway; Phase 3 writes the real one in `src/rie/mcp/`.

## Consequences for the plan

- §5's usage estimate can now be grounded, though only roughly. After about 5 s of start-up per run, each burst took 3–5 s of model time and about 3k tokens for its sheet. A 60-burst folder in chunks of 10 would take 6 runs, roughly 4–6 minutes and $2–3 API-equivalent. This should be measured on a real folder early in Phase 3, before the plan's usage share can be stated.
- Claude Code loads user-level settings (model, hooks). The passes keep them, so that the model is whatever the owner's Claude Code is set to. `--setting-sources` can exclude them if hooks get in the way.
