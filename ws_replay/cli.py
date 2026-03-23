"""CLI for ws-replay: capture, replay, diff, and inspect WebSocket sessions."""

import asyncio
import signal
import sys

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
@click.version_option(package_name="ws-replay")
def cli():
    """Record, replay, and diff WebSocket sessions."""
    pass


@cli.command()
@click.argument("target_url")
@click.option("-o", "--output", default="session.wslog", help="Output file path")
@click.option("-p", "--port", default=9090, help="Local proxy port")
@click.option("-h", "--host", "listen_host", default="localhost", help="Local proxy host")
def capture(target_url: str, output: str, port: int, listen_host: str):
    """Capture WebSocket traffic by proxying to TARGET_URL.

    Example: ws-replay capture ws://localhost:8080 -o my_session.wslog
    """
    from .capture import capture_proxy

    frame_count = 0

    def on_frame(frame):
        nonlocal frame_count
        frame_count += 1
        direction = "[green]->[/green]" if "client" in frame.direction.split("->")[0] else "[blue]<-[/blue]"
        console.print(
            f"  {direction} #{frame.frame_index:4d}  "
            f"{frame.payload_type:6s}  {frame.size:>8d}B  "
            f"[dim]{frame.timestamp:.3f}s[/dim]"
        )

    def on_start(host, p, target):
        console.print(f"\n[bold green]ws-replay proxy started[/bold green]")
        console.print(f"  Listening: ws://{host}:{p}")
        console.print(f"  Proxying:  {target}")
        console.print(f"  Output:    {output}")
        console.print(f"  Press Ctrl+C to stop\n")

    def on_stop(path, count):
        console.print(f"\n[bold yellow]Capture stopped[/bold yellow]")
        console.print(f"  Saved {count} frames to {path}")

    loop = asyncio.new_event_loop()

    task = loop.create_task(capture_proxy(
        target_url=target_url,
        listen_host=listen_host,
        listen_port=port,
        output_path=output,
        on_frame=on_frame,
        on_start=on_start,
        on_stop=on_stop,
    ))

    def shutdown(sig, frame):
        task.cancel()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()


@cli.command()
@click.argument("session_path")
@click.option("-u", "--url", default=None, help="Override target URL")
@click.option("-s", "--speed", default=1.0, help="Playback speed multiplier")
@click.option("--step", is_flag=True, help="Step through frames one at a time")
@click.option("--no-verify", is_flag=True, help="Skip response verification")
@click.option("-t", "--timeout", default=10.0, help="Response timeout in seconds")
def replay(session_path: str, url: str, speed: float, step: bool, no_verify: bool, timeout: float):
    """Replay a captured WebSocket session.

    Example: ws-replay replay session.wslog --speed 2.0
    """
    from .replay import replay_session, print_replay_summary

    def on_send(frame):
        payload_preview = frame["payload"][:60] + "..." if len(frame["payload"]) > 60 else frame["payload"]
        console.print(f"  [green]->[/green] Send #{frame['frame_index']}: {payload_preview}")

    def on_receive(frame, actual, match):
        status = "[green]MATCH[/green]" if match else "[red]MISMATCH[/red]"
        console.print(f"  [blue]<-[/blue] Recv #{frame['frame_index']}: {status}")

    def on_mismatch(idx, expected, actual):
        console.print(f"     [dim]Expected: {str(expected)[:60]}[/dim]")
        console.print(f"     [dim]Got:      {str(actual)[:60]}[/dim]")

    def on_step_wait():
        input("  [Press Enter to continue...]")

    console.print(f"\n[bold]Replaying: {session_path}[/bold]")
    console.print(f"  Speed: {speed}x | Verify: {not no_verify} | Step: {step}\n")

    result = asyncio.run(replay_session(
        session_path=session_path,
        target_url=url,
        speed=speed,
        step_mode=step,
        verify=not no_verify,
        on_send=on_send,
        on_receive=on_receive,
        on_mismatch=on_mismatch,
        on_step_wait=on_step_wait if step else None,
        timeout=timeout,
    ))

    print_replay_summary(result)
    sys.exit(0 if not result.mismatches else 1)


@cli.command()
@click.argument("session1")
@click.argument("session2")
@click.option("--timing-threshold", default=0.1, help="Min timing diff to report (seconds)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def diff(session1: str, session2: str, timing_threshold: float, as_json: bool):
    """Diff two WebSocket sessions.

    Example: ws-replay diff session_v1.wslog session_v2.wslog
    """
    import json as json_mod
    from .differ import diff_sessions, format_diff_report

    result = diff_sessions(session1, session2, timing_threshold=timing_threshold)

    if as_json:
        output = {
            "summary": result.summary,
            "diffs": [
                {"index": d.index, "type": d.diff_type, "detail": d.detail}
                for d in result.frame_diffs
            ],
        }
        click.echo(json_mod.dumps(output, indent=2))
    else:
        report = format_diff_report(result)
        console.print(report)

    # Exit code: 0 if identical, 1 if different
    sys.exit(0 if not result.frame_diffs else 1)


@cli.command()
@click.argument("session_path")
@click.option("--format", "fmt", type=click.Choice(["summary", "timeline", "json"]), default="summary")
def inspect(session_path: str, fmt: str):
    """Inspect a captured WebSocket session.

    Example: ws-replay inspect session.wslog
    """
    import json as json_mod
    from .capture import load_session

    header, frames = load_session(session_path)

    if fmt == "json":
        click.echo(json_mod.dumps({"header": header, "frames": frames}, indent=2))
        return

    from .exporter import export_session_summary
    summary = export_session_summary(session_path)
    console.print(summary)


@cli.command()
@click.argument("session_path")
@click.option("-o", "--output", default=None, help="Output file (default: <input>_redacted.wslog)")
@click.option("--seed", default="ws-replay-redact", help="Seed for consistent replacements")
@click.option("--pattern", multiple=True, help="Extra pattern as name=regex")
def redact(session_path: str, output: str, seed: str, pattern: tuple):
    """Redact sensitive data from a session file.

    Example: ws-replay redact session.wslog -o session_clean.wslog
    """
    from .redactor import redact_session

    if output is None:
        from pathlib import Path
        p = Path(session_path)
        output = str(p.parent / f"{p.stem}_redacted{p.suffix}")

    extra_patterns = {}
    for p in pattern:
        if "=" in p:
            name, regex = p.split("=", 1)
            extra_patterns[name] = regex

    stats = redact_session(
        input_path=session_path,
        output_path=output,
        extra_patterns=extra_patterns if extra_patterns else None,
        seed=seed,
    )

    console.print(f"\n[bold green]Redaction complete[/bold green]")
    console.print(f"  Output:           {output}")
    console.print(f"  Frames processed: {stats['frames_processed']}")
    console.print(f"  Frames modified:  {stats['frames_modified']}")
    console.print(f"  Total redactions: {stats['total_redactions']}")


@cli.command()
@click.argument("session_path")
@click.option("-o", "--output", default=None, help="Output .py file (default: repro_<session>.py)")
@click.option("-u", "--url", default=None, help="Override target URL")
@click.option("--no-verify", is_flag=True, help="Exclude response verification")
@click.option("-s", "--speed", default=1.0, help="Playback speed")
def export(session_path: str, output: str, url: str, no_verify: bool, speed: float):
    """Export a standalone reproduction script from a session.

    Example: ws-replay export session.wslog -o repro.py
    """
    from pathlib import Path
    from .exporter import export_python_script

    if output is None:
        p = Path(session_path)
        output = f"repro_{p.stem}.py"

    script = export_python_script(
        session_path=session_path,
        output_path=output,
        target_url=url,
        include_verification=not no_verify,
        speed=speed,
    )

    console.print(f"\n[bold green]Script exported[/bold green]: {output}")
    console.print(f"  Run with: python {output}")


if __name__ == "__main__":
    cli()
