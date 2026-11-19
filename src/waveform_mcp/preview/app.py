"""Preview UI — FastAPI + websocket.

Runs as a SEPARATE PROCESS from the MCP server. They share state via the
events.log JSONL file: the MCP server appends to it, this app tails it and
fans out new lines to connected websocket clients.

Why separate processes:
  - The MCP server speaks stdio with the host (Claude Code / Desktop). We
    can't also bind ports there without contortions.
  - User can restart / reload the preview without disrupting their
    in-progress conversation with Claude.

Run:
    waveform-mcp-preview              # listens on http://localhost:7878
    waveform-mcp-preview --port 9000

The app expects events.log to exist in the current working directory by
default, matching what server.py writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

STATIC_DIR = Path(__file__).parent / "static"

LOOPBACK_HOSTS = ["localhost", "127.0.0.1"]


def _origin_allowed(origin: str | None, host: str | None) -> bool:
    """Browsers don't apply CORS to WebSockets, so any website open in the
    user's browser could otherwise connect to ws://localhost:7878 and read the
    event stream (file paths, project contents). Browsers always send Origin
    on a WebSocket handshake; accept only this page's own origin. Non-browser
    clients send no Origin and are allowed."""
    if origin is None:
        return True
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


def create_app(events_path: Path, allowed_hosts: list[str] | None = None) -> FastAPI:
    app = FastAPI(title="Waveform MCP Preview")
    # Rejects requests whose Host header isn't this machine (DNS rebinding:
    # a website pointing its own hostname at 127.0.0.1 to read the API).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or LOOPBACK_HOSTS)

    state = {
        "events_path": events_path,
        "clients": set(),  # asyncio.Queue per websocket
    }

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/events")
    async def get_events(limit: int = 200):
        """Snapshot of recent events from the log file."""
        if not events_path.exists():
            return []
        lines = events_path.read_text(encoding="utf-8").splitlines()
        limit = max(1, min(int(limit), 1000))
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        if not _origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
            await websocket.close(code=1008)  # policy violation
            return
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        state["clients"].add(queue)

        # send recent history first
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines()[-200:]:
                try:
                    await websocket.send_text(line)
                except Exception:
                    state["clients"].discard(queue)
                    return

        try:
            while True:
                event = await queue.get()
                await websocket.send_text(json.dumps(event))
        except WebSocketDisconnect:
            pass
        finally:
            state["clients"].discard(queue)

    @app.on_event("startup")
    async def _start_tailer():
        asyncio.create_task(_tail_events(state))

    return app


async def _tail_events(state: dict) -> None:
    """Tail events.log and push each new line to all connected websocket clients."""
    path: Path = state["events_path"]
    last_size = path.stat().st_size if path.exists() else 0

    while True:
        await asyncio.sleep(0.25)
        try:
            if not path.exists():
                continue
            size = path.stat().st_size
            if size <= last_size:
                if size < last_size:
                    last_size = 0  # log was rotated/truncated
                continue
            with path.open("r", encoding="utf-8") as f:
                f.seek(last_size)
                chunk = f.read()
                last_size = f.tell()
            for line in chunk.splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for q in list(state["clients"]):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass
        except Exception:
            # Don't kill the tailer; log files come and go.
            await asyncio.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7878)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--events", default="events.log")
    args = parser.parse_args()

    allowed = list(LOOPBACK_HOSTS)
    if args.host not in LOOPBACK_HOSTS:
        print(f"WARNING: listening on {args.host}; the event stream (file paths, "
              "project contents) is readable by anything that can reach it, with no "
              "authentication.")
        if args.host not in ("0.0.0.0", "::"):
            allowed.append(args.host)
        else:
            allowed = ["*"]
    app = create_app(Path(args.events).resolve(), allowed_hosts=allowed)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
