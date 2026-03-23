"""Diff two WebSocket sessions: frame counts, payload diffs, timing deltas."""

import json
from dataclasses import dataclass, field
from typing import Optional

from .capture import load_session


@dataclass
class FrameDiff:
    """Difference between two corresponding frames."""
    index: int
    diff_type: str  # "payload", "timing", "direction", "type", "missing_left", "missing_right"
    left: Optional[dict] = None
    right: Optional[dict] = None
    detail: str = ""


@dataclass
class SessionDiff:
    """Complete diff between two sessions."""
    left_path: str
    right_path: str
    left_frame_count: int = 0
    right_frame_count: int = 0
    frame_diffs: list = field(default_factory=list)
    timing_deltas: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def compute_summary(self):
        """Compute summary statistics."""
        payload_diffs = [d for d in self.frame_diffs if d.diff_type == "payload"]
        timing_diffs = [d for d in self.frame_diffs if d.diff_type == "timing"]
        direction_diffs = [d for d in self.frame_diffs if d.diff_type == "direction"]
        missing_left = [d for d in self.frame_diffs if d.diff_type == "missing_left"]
        missing_right = [d for d in self.frame_diffs if d.diff_type == "missing_right"]

        common = min(self.left_frame_count, self.right_frame_count)
        identical = common - len(payload_diffs) - len(direction_diffs)

        self.summary = {
            "left_frames": self.left_frame_count,
            "right_frames": self.right_frame_count,
            "common_frames": common,
            "identical_frames": max(0, identical),
            "payload_differences": len(payload_diffs),
            "timing_differences": len(timing_diffs),
            "direction_differences": len(direction_diffs),
            "extra_in_left": len(missing_right),
            "extra_in_right": len(missing_left),
        }

        if self.timing_deltas:
            self.summary["avg_timing_delta_ms"] = round(
                sum(abs(d) for d in self.timing_deltas) / len(self.timing_deltas) * 1000, 2
            )
            self.summary["max_timing_delta_ms"] = round(
                max(abs(d) for d in self.timing_deltas) * 1000, 2
            )

        return self.summary


def diff_sessions(
    left_path: str,
    right_path: str,
    timing_threshold: float = 0.1,
) -> SessionDiff:
    """
    Diff two WebSocket sessions.

    Args:
        left_path: Path to first .wslog file
        right_path: Path to second .wslog file
        timing_threshold: Minimum timing difference (seconds) to report
    """
    left_header, left_frames = load_session(left_path)
    right_header, right_frames = load_session(right_path)

    result = SessionDiff(
        left_path=left_path,
        right_path=right_path,
        left_frame_count=len(left_frames),
        right_frame_count=len(right_frames),
    )

    # Compare frame by frame
    max_frames = max(len(left_frames), len(right_frames))

    for i in range(max_frames):
        left_frame = left_frames[i] if i < len(left_frames) else None
        right_frame = right_frames[i] if i < len(right_frames) else None

        if left_frame is None:
            result.frame_diffs.append(FrameDiff(
                index=i,
                diff_type="missing_left",
                right=right_frame,
                detail=f"Frame #{i} only in right session",
            ))
            continue

        if right_frame is None:
            result.frame_diffs.append(FrameDiff(
                index=i,
                diff_type="missing_right",
                left=left_frame,
                detail=f"Frame #{i} only in left session",
            ))
            continue

        # Compare direction
        if left_frame.get("direction") != right_frame.get("direction"):
            result.frame_diffs.append(FrameDiff(
                index=i,
                diff_type="direction",
                left=left_frame,
                right=right_frame,
                detail=f"Direction: {left_frame.get('direction')} vs {right_frame.get('direction')}",
            ))

        # Compare payload
        if not _payloads_equal(left_frame, right_frame):
            left_preview = _payload_preview(left_frame)
            right_preview = _payload_preview(right_frame)
            result.frame_diffs.append(FrameDiff(
                index=i,
                diff_type="payload",
                left=left_frame,
                right=right_frame,
                detail=f"Payload differs:\n  L: {left_preview}\n  R: {right_preview}",
            ))

        # Compare timing
        left_ts = left_frame.get("timestamp", 0)
        right_ts = right_frame.get("timestamp", 0)
        delta = right_ts - left_ts
        result.timing_deltas.append(delta)

        if abs(delta) > timing_threshold:
            result.frame_diffs.append(FrameDiff(
                index=i,
                diff_type="timing",
                left=left_frame,
                right=right_frame,
                detail=f"Timing delta: {delta*1000:.1f}ms (L={left_ts:.3f}s, R={right_ts:.3f}s)",
            ))

    result.compute_summary()
    return result


def _payloads_equal(left: dict, right: dict) -> bool:
    """Compare payloads with JSON-awareness."""
    if left.get("payload_type") != right.get("payload_type"):
        return False

    lp = left.get("payload", "")
    rp = right.get("payload", "")

    if lp == rp:
        return True

    # Try JSON comparison
    if left.get("payload_type") == "text":
        try:
            return json.loads(lp) == json.loads(rp)
        except (json.JSONDecodeError, TypeError):
            pass

    return False


def _payload_preview(frame: dict, max_len: int = 60) -> str:
    """Create a short preview of a frame's payload."""
    payload = frame.get("payload", "")
    ptype = frame.get("payload_type", "text")

    if ptype == "binary":
        size = frame.get("size", 0)
        return f"<binary {size} bytes>"

    if len(payload) > max_len:
        return payload[:max_len] + "..."
    return payload


def format_diff_report(diff: SessionDiff) -> str:
    """Format a human-readable diff report."""
    lines = []
    lines.append("=" * 60)
    lines.append("WebSocket Session Diff Report")
    lines.append("=" * 60)
    lines.append(f"Left:  {diff.left_path} ({diff.left_frame_count} frames)")
    lines.append(f"Right: {diff.right_path} ({diff.right_frame_count} frames)")
    lines.append("")

    s = diff.summary
    lines.append("Summary:")
    lines.append(f"  Common frames:        {s.get('common_frames', 0)}")
    lines.append(f"  Identical frames:     {s.get('identical_frames', 0)}")
    lines.append(f"  Payload differences:  {s.get('payload_differences', 0)}")
    lines.append(f"  Timing differences:   {s.get('timing_differences', 0)}")
    lines.append(f"  Direction differences: {s.get('direction_differences', 0)}")
    lines.append(f"  Extra in left:        {s.get('extra_in_left', 0)}")
    lines.append(f"  Extra in right:       {s.get('extra_in_right', 0)}")

    if "avg_timing_delta_ms" in s:
        lines.append(f"  Avg timing delta:     {s['avg_timing_delta_ms']}ms")
        lines.append(f"  Max timing delta:     {s['max_timing_delta_ms']}ms")

    if diff.frame_diffs:
        lines.append("")
        lines.append("Details:")
        lines.append("-" * 40)
        for d in diff.frame_diffs[:50]:
            lines.append(f"  Frame #{d.index} [{d.diff_type}]: {d.detail}")

        if len(diff.frame_diffs) > 50:
            lines.append(f"  ... and {len(diff.frame_diffs) - 50} more differences")

    return "\n".join(lines)
