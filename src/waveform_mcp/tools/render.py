"""Render tool — stub.

Real implementation needs the planned `wf-engine-helper` C++ binary built on
top of tracktion_engine. For now this tool reports "not implemented" so the
LLM doesn't promise the user audio that won't be produced.

When wf-engine-helper exists, this becomes:
    subprocess.run([helper, "render", "--in", edit_path, "--out", wav_path,
                    "--sr", "48000", "--bits", "24"], check=True)
"""

from __future__ import annotations

from typing import Any

from ..events import publish

TOOLS: list[dict[str, Any]] = [
    {
        "name": "render",
        "description": (
            "Render the current Edit to a WAV file. NOT YET IMPLEMENTED — "
            "requires the wf-engine-helper binary which is not built. "
            "Use this tool to check status; it will return ok=False with a clear message "
            "so the agent can fall back to telling the user to render from inside Waveform."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["out_path"],
            "properties": {
                "out_path": {"type": "string"},
                "sample_rate": {"type": "integer", "default": 48000},
                "bit_depth": {"type": "integer", "enum": [16, 24, 32], "default": 24},
            },
        },
    },
    {
        "name": "render_stems",
        "description": "Render per-track stems. NOT YET IMPLEMENTED.",
        "inputSchema": {
            "type": "object",
            "required": ["out_dir"],
            "properties": {"out_dir": {"type": "string"}},
        },
    },
]


def render(args: dict) -> dict:
    publish({"kind": "render_attempt", "args": args, "result": "not_implemented"})
    return {
        "ok": False,
        "error": "wf-engine-helper not built yet",
        "fallback": "Open the .tracktionedit in Waveform and use File → Export.",
    }


def render_stems(args: dict) -> dict:
    publish({"kind": "render_attempt", "args": args, "result": "not_implemented"})
    return {"ok": False, "error": "wf-engine-helper not built yet"}


HANDLERS = {"render": render, "render_stems": render_stems}
