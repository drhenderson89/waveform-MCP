"""MIDI file export — write the current Edit's MIDI clips to a Standard
MIDI File (.mid) so the user can drop the generated parts into another DAW
or share them.

Single-file export: one track per Edit MIDI track, format-1 SMF.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from ..model import STORE, MidiClip


TOOLS: list[dict[str, Any]] = [
    {
        "name": "edit_export_midi",
        "description": (
            "Export all MIDI clips in the current Edit to a Standard MIDI "
            "File (.mid). One MIDI track per Edit MIDI track. Format-1 SMF "
            "with the Edit's tempo + time signature in track 0. Drop into "
            "any DAW for editing/printing/learning."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["out_path"],
            "properties": {"out_path": {"type": "string"}},
        },
    },
]


def _vlq(n: int) -> bytes:
    """Variable-length quantity encoding for MIDI delta-times."""
    if n == 0:
        return b"\x00"
    out = []
    out.append(n & 0x7F)
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _meta_tempo(bpm: float) -> bytes:
    """FF 51 03 tttttt — tempo in microseconds per quarter note."""
    us_per_qn = int(60_000_000 / bpm)
    return b"\xff\x51\x03" + us_per_qn.to_bytes(3, "big")


def _meta_timesig(num: int, denom: int) -> bytes:
    """FF 58 04 nn dd cc bb."""
    # denominator is power-of-2 exponent: 4 → 2, 8 → 3
    import math as _m
    dd = int(_m.log2(denom))
    return b"\xff\x58\x04" + bytes([num, dd, 24, 8])


def _meta_track_name(name: str) -> bytes:
    name_bytes = name.encode("utf-8", errors="replace")[:128]
    return b"\xff\x03" + _vlq(len(name_bytes)) + name_bytes


def _meta_end_of_track() -> bytes:
    return b"\xff\x2f\x00"


def _build_track(clip_events: list[tuple[int, bytes]], track_name: str) -> bytes:
    """Build a Track Chunk body from sorted (abs_tick, event_bytes) pairs."""
    body = bytearray()
    body += _vlq(0) + _meta_track_name(track_name)
    last_tick = 0
    for abs_tick, ev in clip_events:
        delta = abs_tick - last_tick
        body += _vlq(delta) + ev
        last_tick = abs_tick
    body += _vlq(0) + _meta_end_of_track()
    return b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)


def edit_export_midi(args: dict) -> dict:
    out_path = args["out_path"]
    edit = STORE.edit
    ticks_per_qn = 480

    # Track 0: tempo + time signature
    tempo_track_events: list[tuple[int, bytes]] = []
    tempo_track_events.append((0, _meta_tempo(edit.tempo)))
    tempo_track_events.append((0, _meta_timesig(edit.time_sig_num, edit.time_sig_denom)))
    # Tempo changes
    for sb, bpm in (edit.tempo_changes or []):
        tick = int(sb * ticks_per_qn)
        tempo_track_events.append((tick, _meta_tempo(float(bpm))))
    tempo_track_events.sort(key=lambda x: x[0])
    tempo_track_chunk = _build_track(tempo_track_events, "Tempo")

    # One track per MIDI track in the Edit
    instrument_track_chunks: list[bytes] = []
    for ti, t in enumerate(edit.tracks):
        if not any(isinstance(c, MidiClip) for c in t.clips):
            continue
        events: list[tuple[int, bytes]] = []
        channel = ti % 16
        for c in t.clips:
            if not isinstance(c, MidiClip):
                continue
            base_tick = int(c.start_beats * ticks_per_qn)
            for n in c.notes:
                start = base_tick + int(n.start_beats * ticks_per_qn)
                end   = start + max(1, int(n.length_beats * ticks_per_qn))
                events.append((start, bytes([0x90 | channel, n.pitch & 0x7F, n.velocity & 0x7F])))
                events.append((end,   bytes([0x80 | channel, n.pitch & 0x7F, 0])))
        events.sort(key=lambda x: x[0])
        instrument_track_chunks.append(_build_track(events, t.name))

    # File header — format 1, ntrks = 1 + instrument tracks
    n_tracks = 1 + len(instrument_track_chunks)
    header_body = (1).to_bytes(2, "big") + n_tracks.to_bytes(2, "big") + ticks_per_qn.to_bytes(2, "big")
    header = b"MThd" + (6).to_bytes(4, "big") + header_body

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(header)
        f.write(tempo_track_chunk)
        for tc in instrument_track_chunks:
            f.write(tc)

    return {"ok": True, "path": out_path, "n_tracks": n_tracks}


HANDLERS = {"edit_export_midi": edit_export_midi}
