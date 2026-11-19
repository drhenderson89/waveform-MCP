"""MCP server entry point.

Speaks MCP over stdio (the standard transport for desktop / CLI clients).
Aggregates tools from the modules in `tools/` and dispatches them.

Run:
    waveform-mcp                       # stdio server
"""

from __future__ import annotations

import asyncio
import functools
import json
import sys
from typing import Any

import anyio
from mcp.server import Server
from mcp.server.caching import CacheHint
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool

from . import events
from . import safety
from . import __version__
import sys

from .tools import (
    audio,
    audio_analysis,
    audio_detect,
    automation,
    clips,
    composer,
    composition,
    desktop,
    drum_patterns,
    edit as edit_tools,
    ensemble,
    jazz_generative,
    lofi_generative,
    loops,
    melody,
    midi,
    midi_export,
    music_theory,
    plugins,
    preset_library,
    reference_songs,
    render,
    step_clips,
    synthwave_generative,
    tracks,
    waveform_app,
    workflow,
)


def _collect_tools() -> tuple[list[Tool], dict[str, Any]]:
    all_tool_defs: list[dict[str, Any]] = []
    handlers: dict[str, Any] = {}

    modules: list[Any] = [
        edit_tools, tracks, midi, audio, clips, automation, plugins, render,
        waveform_app, desktop, preset_library, composer, music_theory, loops,
        melody, drum_patterns, composition, step_clips,
        audio_analysis, audio_detect, midi_export, reference_songs, workflow,
        # Generative composers — formerly only callable via Python; now
        # registered so MCP clients can drive them directly.
        lofi_generative, synthwave_generative, jazz_generative,
        # Cross-genre ensemble cohesion primitives.
        ensemble,
    ]
    if sys.platform == "win32":
        from .tools import schema_capture, waveform_workflows, win_input
        modules.extend([win_input, waveform_workflows, schema_capture])
    # The desktop_* tools act on whatever window has focus, not just Waveform,
    # so they are only offered when the person running the server opts in.
    if not safety.desktop_enabled():
        modules.remove(desktop)

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
    """Build the MCP server (protocol revision 2026-07-28).

    The v1 `@server.list_tools()` / `@server.call_tool()` decorators are gone in
    SDK v2; handlers are passed to the constructor instead.
    """
    tool_list, handlers = _collect_tools()

    async def on_list_tools(_ctx: Any, _params: Any) -> ListToolsResult:
        return ListToolsResult(tools=tool_list)

    async def on_call_tool(_ctx: Any, params: CallToolRequestParams) -> CallToolResult:
        name = params.name
        if name not in handlers:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps({"ok": False, "error": f"unknown tool: {name}"}),
                    )
                ],
                is_error=False,
            )
        try:
            # The handlers are synchronous and many of them do blocking disk,
            # audio and subprocess work, so they run on a worker thread rather
            # than stalling the event loop and every concurrent request.
            result = await anyio.to_thread.run_sync(
                functools.partial(handlers[name], params.arguments or {})
            )
        except Exception as e:
            result = {"ok": False, "error": str(e), "type": type(e).__name__}
        # This server has always reported failure through an `ok: False` field
        # in the payload rather than the protocol's isError, and that is
        # preserved here rather than changed as a migration side effect.
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, default=str))],
            is_error=False,
        )

    # This is the largest tool catalogue in the suite: 157 tools serializing to
    # roughly 65 KiB. The list is built once at process start and cannot change
    # while the process lives, so it is safely cacheable, and a client that
    # honours the hint stops re-fetching 65 KiB on every reconnect. `public`
    # because the catalogue is static code, identical for every caller, with no
    # auth-scoped variation. Tool *results* are caller-specific, but tools/call
    # is not a cacheable method.
    return Server(
        "waveform-mcp",
        version=__version__,
        cache_hints={
            "tools/list": CacheHint(ttl_ms=300_000, scope="public"),
            "server/discover": CacheHint(ttl_ms=300_000, scope="public"),
        },
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def main() -> None:
    # Direct event log to a stable file (the current working dir unless
    # WAVEFORM_MCP_STATE_DIR is set), so the preview UI can find it.
    log_path = safety.events_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    events.configure(log_path)

    asyncio.run(_serve())


async def _serve() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    main()
