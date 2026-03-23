"""Export minimal reproduction scripts from captured WebSocket sessions."""

import json
import textwrap
from pathlib import Path
from typing import Optional

from .capture import load_session


def export_python_script(
    session_path: str,
    output_path: Optional[str] = None,
    target_url: Optional[str] = None,
    include_verification: bool = True,
    speed: float = 1.0,
) -> str:
    """
    Generate a standalone Python script that reproduces the WebSocket session.

    Args:
        session_path: Path to .wslog file
        output_path: Path for output .py file (None = return as string)
        target_url: Override target URL
        include_verification: Include response verification code
        speed: Timing multiplier

    Returns:
        Generated script as string
    """
    header, frames = load_session(session_path)
    url = target_url or header.get("target_url", "ws://localhost:8080")

    client_sends = []
    expected_responses = []

    for frame in frames:
        if frame["direction"] == "client->server":
            client_sends.append(frame)
        else:
            expected_responses.append(frame)

    # Build the script
    lines = []
    lines.append('#!/usr/bin/env python3')
    lines.append('"""')
    lines.append(f'WebSocket reproduction script.')
    lines.append(f'Generated from: {session_path}')
    lines.append(f'Target: {url}')
    lines.append(f'Frames: {len(client_sends)} sends, {len(expected_responses)} expected responses')
    lines.append('"""')
    lines.append('')
    lines.append('import asyncio')
    lines.append('import json')
    lines.append('import sys')

    if any(f["payload_type"] == "binary" for f in client_sends):
        lines.append('import base64')

    lines.append('')
    lines.append('import websockets')
    lines.append('')
    lines.append('')
    lines.append(f'TARGET_URL = "{url}"')
    lines.append(f'SPEED = {speed}')
    lines.append('')

    # Build frames data
    lines.append('# Captured frames (direction, delay_seconds, payload_type, payload)')
    lines.append('FRAMES = [')

    prev_ts = 0.0
    for frame in frames:
        ts = frame.get("timestamp", 0)
        delay = ts - prev_ts
        prev_ts = ts

        direction = frame["direction"]
        ptype = frame["payload_type"]
        payload = frame["payload"]

        # Escape the payload for Python
        if ptype == "text":
            escaped = json.dumps(payload)  # JSON-safe string
            lines.append(f'    ("{direction}", {delay:.4f}, "text", {escaped}),')
        else:
            lines.append(f'    ("{direction}", {delay:.4f}, "binary", "{payload}"),')

    lines.append(']')
    lines.append('')
    lines.append('')

    # Main function
    lines.append('async def reproduce():')
    lines.append('    """Reproduce the WebSocket session."""')
    lines.append('    mismatches = 0')
    lines.append('    sent = 0')
    lines.append('    received = 0')
    lines.append('')
    lines.append('    async with websockets.connect(TARGET_URL) as ws:')
    lines.append('        for direction, delay, ptype, payload in FRAMES:')
    lines.append('            # Respect original timing')
    lines.append('            if delay > 0:')
    lines.append('                await asyncio.sleep(delay / SPEED)')
    lines.append('')
    lines.append('            if direction == "client->server":')
    lines.append('                if ptype == "binary":')
    lines.append('                    import base64')
    lines.append('                    await ws.send(base64.b64decode(payload))')
    lines.append('                else:')
    lines.append('                    await ws.send(payload)')
    lines.append('                sent += 1')
    lines.append('                print(f"  -> Sent frame #{sent}: {payload[:80]}...")')

    if include_verification:
        lines.append('')
        lines.append('            elif direction == "server->client":')
        lines.append('                try:')
        lines.append('                    resp = await asyncio.wait_for(ws.recv(), timeout=10.0)')
        lines.append('                    received += 1')
        lines.append('                    resp_str = resp if isinstance(resp, str) else repr(resp)')
        lines.append('                    # Compare with expected')
        lines.append('                    expected = payload')
        lines.append('                    if resp_str == expected:')
        lines.append('                        print(f"  <- Received #{received}: MATCH")')
        lines.append('                    else:')
        lines.append('                        mismatches += 1')
        lines.append('                        print(f"  <- Received #{received}: MISMATCH")')
        lines.append('                        print(f"     Expected: {expected[:60]}...")')
        lines.append('                        print(f"     Got:      {resp_str[:60]}...")')
        lines.append('                except asyncio.TimeoutError:')
        lines.append('                    mismatches += 1')
        lines.append('                    print(f"  <- TIMEOUT waiting for response")')

    lines.append('')
    lines.append('    print(f"\\nDone: {sent} sent, {received} received, {mismatches} mismatches")')
    lines.append('    return mismatches == 0')
    lines.append('')
    lines.append('')
    lines.append('if __name__ == "__main__":')
    lines.append('    success = asyncio.run(reproduce())')
    lines.append('    sys.exit(0 if success else 1)')
    lines.append('')

    script = "\n".join(lines)

    if output_path:
        Path(output_path).write_text(script)

    return script


def export_curl_commands(session_path: str) -> str:
    """
    Export session info as comments (WebSocket doesn't have direct curl equiv).
    Useful for documentation purposes.
    """
    header, frames = load_session(session_path)
    url = header.get("target_url", "ws://localhost:8080")

    lines = []
    lines.append(f"# WebSocket session: {url}")
    lines.append(f"# Total frames: {len(frames)}")
    lines.append(f"# Install: pip install websockets")
    lines.append(f"# Use: python repro.py")
    lines.append("")
    lines.append(f"# Quick test with websocat (if installed):")
    lines.append(f"# websocat {url}")
    lines.append("")

    client_frames = [f for f in frames if f["direction"] == "client->server" and f["payload_type"] == "text"]
    for i, frame in enumerate(client_frames[:10]):
        payload = frame["payload"]
        if len(payload) > 200:
            payload = payload[:200] + "..."
        lines.append(f"# Frame {i}: {payload}")

    return "\n".join(lines)


def export_session_summary(session_path: str) -> str:
    """Export a human-readable summary of the session."""
    header, frames = load_session(session_path)

    client_frames = [f for f in frames if f["direction"] == "client->server"]
    server_frames = [f for f in frames if f["direction"] == "server->client"]

    text_frames = [f for f in frames if f["payload_type"] == "text"]
    binary_frames = [f for f in frames if f["payload_type"] == "binary"]

    total_size = sum(f.get("size", 0) for f in frames)
    duration = frames[-1]["timestamp"] if frames else 0

    lines = []
    lines.append(f"Session Summary")
    lines.append(f"===============")
    lines.append(f"Target URL:     {header.get('target_url', 'N/A')}")
    lines.append(f"Duration:       {duration:.2f}s")
    lines.append(f"Total frames:   {len(frames)}")
    lines.append(f"  Client->Server: {len(client_frames)}")
    lines.append(f"  Server->Client: {len(server_frames)}")
    lines.append(f"  Text frames:    {len(text_frames)}")
    lines.append(f"  Binary frames:  {len(binary_frames)}")
    lines.append(f"Total data:     {_format_size(total_size)}")
    lines.append("")

    if frames:
        lines.append("Timeline:")
        for i, frame in enumerate(frames[:20]):
            direction = "->" if "client" in frame["direction"].split("->")[0] else "<-"
            ptype = frame["payload_type"]
            size = frame.get("size", 0)
            ts = frame.get("timestamp", 0)
            preview = _frame_preview(frame)
            lines.append(f"  [{ts:7.3f}s] {direction} {ptype:6s} {_format_size(size):>8s}  {preview}")

        if len(frames) > 20:
            lines.append(f"  ... and {len(frames) - 20} more frames")

    return "\n".join(lines)


def _frame_preview(frame: dict, max_len: int = 50) -> str:
    """Create a short preview of frame content."""
    if frame["payload_type"] == "binary":
        return "<binary>"
    payload = frame.get("payload", "")
    if len(payload) > max_len:
        return payload[:max_len] + "..."
    return payload


def _format_size(size_bytes: int) -> str:
    """Format byte size to human readable."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
