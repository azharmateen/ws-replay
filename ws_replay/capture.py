"""WebSocket proxy that intercepts client<->server frames and logs to JSONL."""

import asyncio
import json
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import websockets
from websockets.frames import Frame


@dataclass
class CapturedFrame:
    """A single captured WebSocket frame."""
    timestamp: float
    direction: str  # "client->server" or "server->client"
    payload_type: str  # "text" or "binary"
    payload: str  # text content or base64 for binary
    size: int
    frame_index: int


@dataclass
class CaptureSession:
    """A WebSocket capture session."""
    target_url: str
    start_time: float = field(default_factory=time.time)
    frames: list = field(default_factory=list)
    frame_counter: int = 0

    def add_frame(self, direction: str, data, payload_type: str) -> CapturedFrame:
        """Record a frame."""
        import base64

        if payload_type == "binary":
            payload_str = base64.b64encode(data).decode("ascii")
            size = len(data)
        else:
            payload_str = data if isinstance(data, str) else data.decode("utf-8", errors="replace")
            size = len(payload_str.encode("utf-8"))

        frame = CapturedFrame(
            timestamp=time.time() - self.start_time,
            direction=direction,
            payload_type=payload_type,
            payload=payload_str,
            size=size,
            frame_index=self.frame_counter,
        )
        self.frames.append(frame)
        self.frame_counter += 1
        return frame

    def save(self, output_path: str) -> str:
        """Save session to JSONL file."""
        path = Path(output_path)
        with open(path, "w") as f:
            # Write header line
            header = {
                "_type": "session_header",
                "target_url": self.target_url,
                "start_time": self.start_time,
                "total_frames": len(self.frames),
                "version": "1.0",
            }
            f.write(json.dumps(header) + "\n")

            # Write each frame
            for frame in self.frames:
                f.write(json.dumps(asdict(frame)) + "\n")

        return str(path)


def load_session(path: str) -> tuple[dict, list[dict]]:
    """Load a session from a JSONL file. Returns (header, frames)."""
    frames = []
    header = {}
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if i == 0 and data.get("_type") == "session_header":
                header = data
            else:
                frames.append(data)
    return header, frames


async def _proxy_client_to_server(client_ws, server_ws, session: CaptureSession, on_frame=None):
    """Forward frames from client to server."""
    try:
        async for message in client_ws:
            payload_type = "binary" if isinstance(message, bytes) else "text"
            frame = session.add_frame("client->server", message, payload_type)
            if on_frame:
                on_frame(frame)
            await server_ws.send(message)
    except websockets.exceptions.ConnectionClosed:
        pass


async def _proxy_server_to_client(client_ws, server_ws, session: CaptureSession, on_frame=None):
    """Forward frames from server to client."""
    try:
        async for message in server_ws:
            payload_type = "binary" if isinstance(message, bytes) else "text"
            frame = session.add_frame("server->client", message, payload_type)
            if on_frame:
                on_frame(frame)
            await client_ws.send(message)
    except websockets.exceptions.ConnectionClosed:
        pass


async def capture_proxy(
    target_url: str,
    listen_host: str = "localhost",
    listen_port: int = 9090,
    output_path: str = "session.wslog",
    on_frame=None,
    on_start=None,
    on_stop=None,
):
    """
    Start a WebSocket proxy that captures all frames.

    Listens on listen_host:listen_port and forwards to target_url.
    """
    session = CaptureSession(target_url=target_url)

    async def handler(client_ws):
        """Handle a single client connection."""
        async with websockets.connect(target_url) as server_ws:
            # Run both directions concurrently
            client_to_server = asyncio.create_task(
                _proxy_client_to_server(client_ws, server_ws, session, on_frame)
            )
            server_to_client = asyncio.create_task(
                _proxy_server_to_client(client_ws, server_ws, session, on_frame)
            )

            # Wait for either direction to close
            done, pending = await asyncio.wait(
                [client_to_server, server_to_client],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel the other direction
            for task in pending:
                task.cancel()

    if on_start:
        on_start(listen_host, listen_port, target_url)

    try:
        async with websockets.serve(handler, listen_host, listen_port):
            # Run until interrupted
            await asyncio.Future()  # Run forever
    except asyncio.CancelledError:
        pass
    finally:
        saved_path = session.save(output_path)
        if on_stop:
            on_stop(saved_path, len(session.frames))

    return session
