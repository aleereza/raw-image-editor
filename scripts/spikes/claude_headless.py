"""Spike f: run headless Claude Code (`claude -p`) against the spike MCP server and time each burst.

Usage: uv run --group spikes python scripts/spikes/claude_headless.py SAMPLES_DIR OUT_DIR BURSTS_JSON [--sheet-only]

BURSTS_JSON maps burst names to NEF file names, e.g. '{"A": ["DSC_0001.NEF", "DSC_0002.NEF"]}'. The run uses the
account Claude Code is signed in to, so keep the bursts few. Claude gets no built-in tools, only the MCP tools, and
runs in OUT_DIR so no project instructions are picked up. Every stream-json event is saved with its arrival time in
OUT_DIR/events.jsonl, and a summary is printed. `--sheet-only` builds the sheets without calling Claude.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = ["mcp__rie__list_bursts", "mcp__rie__get_burst_sheet", "mcp__rie__suggest_frame"]
PROMPT = (
    "Use the rie tools. Call list_bursts, then for each burst call get_burst_sheet and then suggest_frame with the "
    "best frame (everyone's eyes open, good expressions, sharp, nobody blocked) and a one-line reason; list frames "
    "you would reject. When every burst has a suggestion, reply with one word: done."
)


def main() -> None:
    samples, out, bursts = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3]
    out.mkdir(parents=True, exist_ok=True)
    env = {"RIE_SAMPLES": str(samples), "RIE_BURSTS": bursts, "RIE_OUT": str(out), "RIE_MODELS": str(REPO / "models")}
    server = [str(REPO / ".venv" / "bin" / "python"), str(REPO / "scripts" / "spikes" / "mcp_burst_server.py")]
    if "--sheet-only" in sys.argv:
        os.environ.update(env)
        sys.path.insert(0, str(REPO / "scripts" / "spikes"))
        import mcp_burst_server as srv

        for name in json.loads(bursts):
            t = time.perf_counter()
            data = srv.sheet(name)
            print(json.dumps({"sheet": name, "bytes": len(data), "seconds": round(time.perf_counter() - t, 2)}))
        return

    config = out / "mcp-config.json"
    config.write_text(json.dumps({"mcpServers": {"rie": {"command": server[0], "args": server[1:], "env": env}}}))
    cmd = [
        "claude",
        "-p",
        "--mcp-config",
        str(config),
        "--strict-mcp-config",
        "--tools",
        "",
        "--allowedTools",
        ",".join(TOOLS),
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
    ]
    start = time.monotonic()
    events = []
    with subprocess.Popen(cmd, cwd=out, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as proc:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(PROMPT)
        proc.stdin.close()
        for line in proc.stdout:
            if line.strip():
                events.append({"t": round(time.monotonic() - start, 2), "event": json.loads(line)})
    (out / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")

    calls = []
    for e in events:
        ev = e["event"]
        for block in (ev.get("message", {}) or {}).get("content", []) if isinstance(ev.get("message"), dict) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append({"t": e["t"], "tool": block["name"], "input": block.get("input")})
    init = next((e["event"] for e in events if e["event"].get("subtype") == "init"), {})
    result = next((e["event"] for e in events if e["event"].get("type") == "result"), {})
    limits = [e["event"] for e in events if "rate_limit" in json.dumps(e["event"])[:200]]
    print(
        json.dumps(
            {
                "model": init.get("model"),
                "mcp_servers": init.get("mcp_servers"),
                "first_event_s": events[0]["t"] if events else None,
                "wall_s": round(time.monotonic() - start, 1),
                "tool_calls": [(c["t"], c["tool"]) for c in calls],
            },
            indent=1,
        )
    )
    keep = ("subtype", "is_error", "duration_ms", "duration_api_ms", "num_turns", "total_cost_usd", "usage", "result")
    print(json.dumps({k: result.get(k) for k in keep}, indent=1))
    if limits:
        print(json.dumps({"rate_limit_events": limits[-2:]}, indent=1))
    suggestions = out / "suggestions.jsonl"
    print(suggestions.read_text() if suggestions.exists() else "no suggestions recorded")


if __name__ == "__main__":
    main()
