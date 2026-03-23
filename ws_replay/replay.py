"""Replay captured WebSocket sessions with timing control."""

import asyncio
import base64
import json
import time
from typing import Optional, Callable

import websockets

from .capture import load_session


class ReplayResult:
    """Result of a replay session."""

    def __init__(self):
        self.frames_sent = 0
        self.frames_received = 0
        self.mismatches: list[dict] = []
        self.matched = 0
        self.total_expected = 0

    @property
    def match_rate(self) -> float:
        if self.total_expected == 0:
            return 1.0
        return self.matched / self.total_expected

    def summary(self) -> dict:
        return {
            "frames_sent": self.frames_sent,
            "frames_received": self.frames_received,
            "expected_responses": self.total_expected,
            "matched_responses": self.matched,
            "mismatches": len(self.mismatches),
            "match_rate": f"{self.match_rate:.1%}",
        }


async def replay_session(
    session_path: str,
    target_url: Optional[str] = None,
    speed: float = 1.0,
    step_mode: bool = False,
    verify: bool = True,
    on_send: Optional[Callable] = None,
    on_receive: Optional[Callable] = None,
    on_mismatch: Optional[Callable] = None,
    on_step_wait: Optional[Callable] = None,
    timeout: float = 10.0,
) -> ReplayResult:
    """
    Replay a captured WebSocket session.

    Args:
        session_path: Path to .wslog JSONL file
        target_url: WebSocket URL to replay against (overrides captured URL)
        speed: Playback speed multiplier (2.0 = 2x faster)
        step_mode: Wait for confirmation between frames
        verify: Check server responses against captured ones
        on_send: Callback(frame_data) when sending
        on_receive: Callback(frame_data, expected_data, match) when receiving
        on_mismatch: Callback(frame_index, expected, actual) on mismatch
        on_step_wait: Callback() that blocks until user continues (for step mode)
        timeout: Timeout for waiting for server response
    """
    header, frames = load_session(session_path)
    url = target_url or header.get("target_url", "ws://localhost:8080")

    result = ReplayResult()

    # Separate client and server frames
    client_frames = [f for f in frames if f["direction"] == "client->server"]
    server_frames = [f for f in frames if f["direction"] == "server->client"]
    result.total_expected = len(server_frames)

    # Build timeline of frame pairs
    timeline = sorted(frames, key=lambda f: f["timestamp"])

    async with websockets.connect(url) as ws:
        prev_timestamp = 0.0
        server_frame_idx = 0
        receive_buffer = []

        for frame in timeline:
            # Wait for timing
            delay = (frame["timestamp"] - prev_timestamp) / speed
            if delay > 0 and not step_mode:
                await asyncio.sleep(delay)
            prev_timestamp = frame["timestamp"]

            if step_mode and on_step_wait:
                on_step_wait()

            if frame["direction"] == "client->server":
                # Send client frame
                data = _decode_payload(frame)
                await ws.send(data)
                result.frames_sent += 1
                if on_send:
                    on_send(frame)

            elif frame["direction"] == "server->client" and verify:
                # Try to receive and compare
                try:
                    received = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    result.frames_received += 1

                    expected = _decode_payload(frame)
                    match = _compare_payloads(expected, received)

                    if match:
                        result.matched += 1
                    else:
                        mismatch = {
                            "frame_index": frame["frame_index"],
                            "expected_preview": _preview(expected),
                            "actual_preview": _preview(received),
                        }
                        result.mismatches.append(mismatch)
                        if on_mismatch:
                            on_mismatch(frame["frame_index"], expected, received)

                    if on_receive:
                        on_receive(frame, received, match)

                except asyncio.TimeoutError:
                    mismatch = {
                        "frame_index": frame["frame_index"],
                        "expected_preview": _preview(_decode_payload(frame)),
                        "actual_preview": "<TIMEOUT>",
                    }
                    result.mismatches.append(mismatch)

    return result


def _decode_payload(frame: dict):
    """Decode a frame's payload back to its original type."""
    if frame["payload_type"] == "binary":
        return base64.b64decode(frame["payload"])
    return frame["payload"]


def _compare_payloads(expected, actual) -> bool:
    """Compare two payloads, attempting JSON-aware comparison for text."""
    if isinstance(expected, bytes) and isinstance(actual, bytes):
        return expected == actual
    if isinstance(expected, str) and isinstance(actual, str):
        # Try JSON comparison (ignores key order)
        try:
            return json.loads(expected) == json.loads(actual)
        except (json.JSONDecodeError, TypeError):
            pass
        return expected == actual
    return False


def _preview(data, max_len=80) -> str:
    """Create a short preview of payload data."""
    if isinstance(data, bytes):
        return f"<binary {len(data)} bytes>"
    text = str(data)
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def print_replay_summary(result: ReplayResult):
    """Print a human-readable replay summary."""
    summary = result.summary()
    print(f"\n--- Replay Summary ---")
    print(f"Frames sent:       {summary['frames_sent']}")
    print(f"Frames received:   {summary['frames_received']}")
    print(f"Expected responses: {summary['expected_responses']}")
    print(f"Matched:           {summary['matched_responses']}")
    print(f"Mismatches:        {summary['mismatches']}")
    print(f"Match rate:        {summary['match_rate']}")

    if result.mismatches:
        print(f"\nMismatched frames:")
        for m in result.mismatches[:10]:
            print(f"  Frame #{m['frame_index']}:")
            print(f"    Expected: {m['expected_preview']}")
            print(f"    Actual:   {m['actual_preview']}")
        if len(result.mismatches) > 10:
            print(f"  ... and {len(result.mismatches) - 10} more")
