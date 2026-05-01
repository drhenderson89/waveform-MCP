"""Audio clip tools — stub. Imports a file as an AudioClip on a track."""

from __future__ import annotations

from typing import Any

from ..model import AudioClip, Edit, _id
from .common import op

TOOLS: list[dict[str, Any]] = [
    {
        "name": "audio_clip_import",
        "description": (
            "Place an audio file as a clip on a track. warp_mode controls "
            "time-stretch behavior. Optional clip-level gain (dB), fade in/out "
            "(beats), and offset_in_source (beats — skip the first N beats of "
            "the source file, useful for trimming silent intros)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "file_path", "start_beats", "length_beats"],
            "properties": {
                "track_id": {"type": "string"},
                "file_path": {"type": "string"},
                "start_beats": {"type": "number"},
                "length_beats": {"type": "number"},
                "name": {"type": "string"},
                "warp_mode": {
                    "type": "string",
                    "enum": ["none", "stretch", "warp"],
                    "default": "none",
                },
                "gain_db": {"type": "number", "default": 0.0},
                "fade_in_beats": {"type": "number", "default": 0.0},
                "fade_out_beats": {"type": "number", "default": 0.0},
                "offset_in_source_beats": {"type": "number", "default": 0.0},
            },
        },
    },
]


@op("audio_clip_import", intent_template="Import audio onto {track_id}")
def audio_clip_import(
    edit: Edit,
    track_id: str,
    file_path: str,
    start_beats: float,
    length_beats: float,
    name: str | None = None,
    warp_mode: str = "none",
    gain_db: float = 0.0,
    fade_in_beats: float = 0.0,
    fade_out_beats: float = 0.0,
    offset_in_source_beats: float = 0.0,
) -> dict:
    t = edit.track(track_id)
    c = AudioClip(
        id=_id("clip"),
        name=name or file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
        start_beats=start_beats,
        length_beats=length_beats,
        file_path=file_path,
        warp_mode=warp_mode,  # type: ignore[arg-type]
        gain_db=gain_db,
        fade_in_beats=fade_in_beats,
        fade_out_beats=fade_out_beats,
        offset_in_source_beats=offset_in_source_beats,
    )
    t.clips.append(c)
    return {"clip_id": c.id}


HANDLERS = {"audio_clip_import": audio_clip_import}
