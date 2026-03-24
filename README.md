# ws-replay

[![Built with Claude Code](https://img.shields.io/badge/Built%20with-Claude%20Code-blue?logo=anthropic&logoColor=white)](https://claude.ai/code)


**Record, replay, and diff WebSocket sessions to reproduce real-time bugs locally.**

> Stop saying "it works on my machine." Capture the exact WebSocket conversation, replay it deterministically, and diff sessions to find what changed.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

## The Problem

WebSocket bugs are notoriously hard to reproduce. The connection is stateful, timing matters, and by the time you open DevTools, the bug is gone. **ws-replay** captures the entire conversation so you can replay it on demand.

## Features

- **Capture** -- Transparent proxy records every frame with timestamps, direction, and payload type
- **Replay** -- Reproduce sessions with original timing (or speed up/slow down), with response verification
- **Diff** -- Compare two sessions frame-by-frame: payloads, timing deltas, missing frames
- **Redact** -- Strip tokens, emails, API keys with consistent fake replacements (same input = same output)
- **Export** -- Generate standalone Python scripts for minimal reproductions
- **Inspect** -- View session summaries, timelines, and raw frame data

## Install

```bash
pip install ws-replay
```

## Quick Start

```bash
# 1. Capture: proxy localhost:9090 -> your server at ws://localhost:8080
ws-replay capture ws://localhost:8080 -o bug_session.wslog

# 2. Point your client at ws://localhost:9090 instead of :8080
#    Reproduce the bug. Press Ctrl+C when done.

# 3. Inspect what was captured
ws-replay inspect bug_session.wslog

# 4. Replay against the server (verifies responses match)
ws-replay replay bug_session.wslog --speed 2.0

# 5. Diff two sessions (before/after a fix)
ws-replay diff before.wslog after.wslog

# 6. Redact sensitive data before sharing
ws-replay redact bug_session.wslog -o bug_session_clean.wslog

# 7. Export a standalone repro script
ws-replay export bug_session.wslog -o repro.py
```

## Session Format

Sessions are stored as JSONL (one JSON object per line):

```jsonl
{"_type": "session_header", "target_url": "ws://localhost:8080", "start_time": 1711234567.89, "total_frames": 42, "version": "1.0"}
{"timestamp": 0.0, "direction": "client->server", "payload_type": "text", "payload": "{\"type\":\"auth\",\"token\":\"...\"}", "size": 45, "frame_index": 0}
{"timestamp": 0.023, "direction": "server->client", "payload_type": "text", "payload": "{\"type\":\"auth_ok\"}", "size": 18, "frame_index": 1}
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `ws-replay capture <url>` | Start capture proxy |
| `ws-replay replay <file>` | Replay session against server |
| `ws-replay diff <a> <b>` | Compare two sessions |
| `ws-replay inspect <file>` | View session details |
| `ws-replay redact <file>` | Remove sensitive data |
| `ws-replay export <file>` | Generate repro script |

## Use Cases

- **Bug reproduction**: Capture once, replay forever
- **Regression testing**: Diff sessions before/after code changes
- **API documentation**: Inspect and export real WebSocket conversations
- **Security review**: Redact credentials before sharing captures with teammates

## License

MIT
