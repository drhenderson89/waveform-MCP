"""Conformance tests for MCP protocol revision 2026-07-28.

Driven through a real in-process client, so the dispatch path that applies
cache hints and result metadata is actually exercised. Calling `_collect_tools`
directly would bypass it and the cache-hint assertions would check nothing.
"""

from __future__ import annotations

import json

import anyio

from mcp.client.client import Client
from waveform_mcp import __version__
from waveform_mcp.server import _collect_tools, build_server

PROTOCOL_VERSION = "2026-07-28"


def _run(body):
    async def main():
        async with Client(build_server()) as client:
            value = body(client)
            return await value if hasattr(value, "__await__") else value
    return anyio.run(main)


def _wire(body):
    return _run(body).model_dump(by_alias=True, exclude_none=True)


def test_negotiates_2026_07_28():
    assert _run(lambda c: c.protocol_version) == PROTOCOL_VERSION


def test_tools_list_carries_cache_hints():
    """SEP-2549. This is the largest catalogue in the suite, so the hint is
    what stops a client re-fetching ~65 KiB on every reconnect.

    Asserted on the serialized wire form: the snake_case attributes would still
    pass if the camelCase aliases regressed.
    """
    wire = _wire(lambda c: c.list_tools())
    assert wire["ttlMs"] == 300_000
    assert wire["cacheScope"] == "public"


def test_results_carry_result_type():
    assert _wire(lambda c: c.list_tools())["resultType"] == "complete"


def test_server_identifies_itself_in_result_meta():
    info = _wire(lambda c: c.list_tools())["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert info["name"] == "waveform-mcp"
    assert info["version"] == __version__


def test_server_discover_advertises_supported_versions():
    wire = _wire(lambda c: c.session.discover())
    assert PROTOCOL_VERSION in wire["supportedVersions"]
    assert wire["ttlMs"] == 300_000


def test_tool_order_is_deterministic():
    """Ordering comes from the module list in `_collect_tools` and each module's
    own TOOLS order. It was already deterministic; this pins it, so a reordering
    is a deliberate choice rather than a silent cache-miss for every client
    holding a cached catalogue."""
    first = [t["name"] for t in _wire(lambda c: c.list_tools())["tools"]]
    second = [t["name"] for t in _wire(lambda c: c.list_tools())["tools"]]

    assert first == [t.name for t in _collect_tools()[0]]
    assert first == second


def test_every_advertised_tool_has_a_handler():
    """A tool in TOOLS with no HANDLERS entry is advertised but uncallable."""
    tools, handlers = _collect_tools()
    assert {t.name for t in tools} - set(handlers) == set()


def test_tool_names_are_unique():
    """`handlers.update(mod.HANDLERS)` means a duplicate name across modules
    silently shadows the earlier handler while both stay advertised."""
    names = [t.name for t in _collect_tools()[0]]
    assert len(names) == len(set(names))


def test_cache_hints_do_not_meaningfully_inflate_the_payload():
    """The migration does not shrink tools/list - the catalogue is the same
    bytes either way. What the hints buy is clients not re-fetching it.

    This guards the other direction: that the hints themselves stay negligible
    against a ~65 KiB catalogue rather than growing into real overhead.
    """
    wire = _wire(lambda c: c.list_tools())
    catalogue = len(json.dumps({"tools": wire["tools"]}, separators=(",", ":")).encode())
    full = len(json.dumps(wire, separators=(",", ":")).encode())

    overhead = full - catalogue
    assert overhead < 1024, f"protocol overhead grew to {overhead} bytes"


def test_unknown_tool_reports_through_ok_field_not_iserror():
    """This server has always signalled failure with an `ok: False` payload
    field rather than the protocol's isError. Preserved deliberately rather
    than changed as a side effect of the transport migration."""
    wire = _wire(lambda c: c.call_tool("does_not_exist", {}))

    assert not wire.get("isError")
    assert json.loads(wire["content"][0]["text"])["ok"] is False
