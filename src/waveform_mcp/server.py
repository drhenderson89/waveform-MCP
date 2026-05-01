"""MCP server entry point.

Speaks MCP over stdio (the standard transport for desktop / CLI clients).
Aggregates tools from the modules in `tools/` and dispatches them.

Run:
    waveform-mcp                       # stdio server
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import events
import sys

from .tools import (
    audio,
    automation,
    clips,
    composer,
    desktop,
    edit as edit_tools,
    loops,
    midi,
    music_theory,
    plugins,
    preset_library,
    render,
    tracks,
    waveform_app,
)


def _collect_tools() -> tuple[list[Tool], dict[str, Any]]:
    all_tool_defs: list[dict[str, Any]] = []
    handlers: dict[str, Any] = {}

    modules: list[Any] = [edit_tools, tracks, midi, audio, clips, automation, plugins, render, waveform_app, desktop, preset_library, composer, music_theory, loops]
    if sys.platform == "win32":
        from .tools import schema_capture, waveform_workflows, win_input
        modules.extend([win_input, waveform_workflows, schema_capture])

    for mod in modules:
        all_tool_defs.extend(mod.TOOLS)
        handlers.update(mod.HANDLERS)

    tools = [
        Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t.get("inputSchema", {"type": "object", "properties": {}}),
        )
        for t in all_tool_defs
    ]
    return tools, handlers


def build_server() -> Server:
    server = Server("waveform-mcp")
    tool_list, handlers = _collect_tools()

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tool_list

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name not in handlers:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"ok": False, "error": f"unknown tool: {name}"}),
                )
            ]
        try:
            result = handlers[name](arguments or {})
        except Exception as e:
            result = {"ok": False, "error": str(e), "type": type(e).__name__}
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    return server


def main() -> None:
    # Direct event log to a stable file in the current working dir, so the
    # preview UI can find it.
    log_path = Path.cwd() / "events.log"
    events.configure(log_path)

    asyncio.run(_serve())


async def _serve() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    main()
