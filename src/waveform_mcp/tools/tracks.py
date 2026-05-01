"""Track-level tools: add, mix, send, marker."""

from __future__ import annotations

from typing import Any

from ..model import Edit, Marker, Plugin, Send, Track, _id
from .common import op

TOOLS: list[dict[str, Any]] = [
    {
        "name": "track_add",
        "description": "Add a new track. kind ∈ {audio, midi, folder, submix, automation}.",
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["audio", "midi", "folder", "submix", "automation"],
                    "default": "midi",
                },
                "volume_db": {"type": "number", "default": 0.0},
                "pan": {"type": "number", "minimum": -1, "maximum": 1, "default": 0.0},
            },
        },
    },
    {
        "name": "track_remove",
        "description": "Delete a track by id.",
        "inputSchema": {
            "type": "object",
            "required": ["track_id"],
            "properties": {"track_id": {"type": "string"}},
        },
    },
    {
        "name": "mix_set",
        "description": "Set track volume (dB), pan (-1..1), mute, solo. Pass only the fields to change.",
        "inputSchema": {
            "type": "object",
            "required": ["track_id"],
            "properties": {
                "track_id": {"type": "string"},
                "volume_db": {"type": "number"},
                "pan": {"type": "number", "minimum": -1, "maximum": 1},
                "mute": {"type": "boolean"},
                "solo": {"type": "boolean"},
            },
        },
    },
    {
        "name": "send_add",
        "description": (
            "Add an aux send (parallel routing) on a track. Tracktion routes "
            "via numbered buses 0..15 — pass bus_num explicitly, or pass "
            "dest_track_id to auto-pick a bus. Pair with aux_return_add on "
            "the receiving track. Emits a real <PLUGIN type='auxsend'/> "
            "element with proper slider position from amount_db."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["src_track_id"],
            "properties": {
                "src_track_id":  {"type": "string"},
                "dest_track_id": {"type": "string", "description": "Optional convenience: auto-pick bus from track index"},
                "bus_num":       {"type": "integer", "minimum": 0, "maximum": 15},
                "amount_db":     {"type": "number", "default": -6.0},
                "pre_post":      {"type": "string", "enum": ["pre", "post"], "default": "post"},
            },
        },
    },
    {
        "name": "marker_add",
        "description": "Drop a marker (verse, chorus, drop, etc.) at a beat position.",
        "inputSchema": {
            "type": "object",
            "required": ["name", "start_beats"],
            "properties": {
                "name": {"type": "string"},
                "start_beats": {"type": "number"},
                "length_beats": {"type": "number", "default": 0.0},
            },
        },
    },
    {
        "name": "tempo_set",
        "description": "Set the project tempo in BPM (currently single global tempo; per-bar curves later).",
        "inputSchema": {
            "type": "object",
            "required": ["bpm"],
            "properties": {"bpm": {"type": "number", "minimum": 20, "maximum": 999}},
        },
    },
    {
        "name": "key_set",
        "description": "Set the project key.",
        "inputSchema": {
            "type": "object",
            "required": ["root", "mode"],
            "properties": {
                "root": {"type": "integer", "minimum": 0, "maximum": 11},
                "mode": {"type": "string", "enum": ["major", "minor"]},
            },
        },
    },
    {
        "name": "bus_create",
        "description": "Create a submix / aux track other tracks can route to. kind ∈ {submix, folder}.",
        "inputSchema": {"type": "object", "required": ["name"],
                        "properties": {"name": {"type": "string"},
                                       "kind": {"type": "string", "enum": ["submix", "folder"], "default": "submix"}}},
    },
    {
        "name": "aux_return_add",
        "description": (
            "Add an auxreturn plugin to a track so it receives audio from "
            "the given bus number (0..15). Pair with send_add(bus_num=N) on "
            "source tracks for parallel routing — e.g. drum tracks all "
            "send to bus 0, aux-return track applies a single reverb."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id"],
            "properties": {
                "track_id": {"type": "string"},
                "bus_num":  {"type": "integer", "default": 0, "minimum": 0, "maximum": 15},
            },
        },
    },
    {
        "name": "bus_route",
        "description": "Route a track's output to a bus instead of master. Stack drums onto a 'Drum Bus' for one comp/EQ.",
        "inputSchema": {"type": "object", "required": ["src_track_id", "dest_bus_id"],
                        "properties": {"src_track_id": {"type": "string"},
                                       "dest_bus_id": {"type": "string"}}},
    },
    {
        "name": "mix_apply_reference",
        "description": (
            "Set a track's volume_db (and optional pan) to the genre+role "
            "reference from the MIX_BALANCE table. Use this once per track "
            "after creating it instead of hand-tuning levels. Genres: "
            "synthwave, lofi, edm, pop, hip_hop, cinematic, ambient. Roles: "
            "drums, kick, snare, hat, bass, sub_bass, lead, pad, keys, arp, "
            "counter, melody, ambience, fx. Returns the dB value applied."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["track_id", "genre", "role"],
            "properties": {
                "track_id": {"type": "string"},
                "genre":    {"type": "string"},
                "role":     {"type": "string"},
                "pan":      {"type": "number", "minimum": -1, "maximum": 1},
                "offset_db": {"type": "number", "default": 0.0,
                              "description": "Signed adjustment on top of the reference (e.g. +1 to lift slightly)"},
            },
        },
    },
]


# ---------- impls ----------


@op("track_add", intent_template="Add {kind} track '{name}'")
def track_add(edit: Edit, name: str, kind: str = "midi", volume_db: float = 0.0, pan: float = 0.0) -> dict:
    t = Track(id=_id("trk"), name=name, kind=kind, volume_db=volume_db, pan=pan)  # type: ignore[arg-type]
    edit.tracks.append(t)
    return {"track_id": t.id}


@op("track_remove", intent_template="Remove track {track_id}")
def track_remove(edit: Edit, track_id: str) -> dict:
    edit.tracks = [t for t in edit.tracks if t.id != track_id]
    return {"track_id": track_id}


@op("mix_set", intent_template="Adjust mix on {track_id}")
def mix_set(
    edit: Edit,
    track_id: str,
    volume_db: float | None = None,
    pan: float | None = None,
    mute: bool | None = None,
    solo: bool | None = None,
) -> dict:
    t = edit.track(track_id)
    if volume_db is not None:
        t.volume_db = volume_db
    if pan is not None:
        t.pan = pan
    if mute is not None:
        t.mute = mute
    if solo is not None:
        t.solo = solo
    return {"track_id": track_id}


@op("send_add", intent_template="Aux send from {src_track_id} bus {bus_num}")
def send_add(
    edit: Edit,
    src_track_id: str,
    dest_track_id: str | None = None,
    amount_db: float = -6.0,
    pre_post: str = "post",
    bus_num: int | None = None,
) -> dict:
    """Add an aux send on the source track.

    Tracktion routes sends via numbered aux buses (0..15). The send is
    realized as an `auxsend` plugin on the source track that writes to
    `bus_num`. The destination track is conventional only — it's a track
    with an `auxreturn` plugin reading `bus_num`.

    For convenience, if you pass `dest_track_id` instead of `bus_num`, we
    use the destination's index in `edit.tracks` modulo 16 as the bus
    number — sufficient for simple "send Drums to Reverb track" cases.

    Schema (captured from tracktion_engine internal/AuxSend):
      <PLUGIN type="auxsend" busNum="0" auxSendSliderPos="0.5" phase="0"/>
    """
    src = edit.track(src_track_id)
    if bus_num is None:
        if dest_track_id is None:
            raise ValueError("pass bus_num or dest_track_id")
        dest = edit.track(dest_track_id)
        bus_num = edit.tracks.index(dest) % 16

    # Convert dB to slider position (Tracktion uses
    # decibelsToVolumeFaderPosition: roughly 0..1 with 0.638 = unity).
    slider = 0.6376281380653381
    if amount_db != 0.0:
        slider = 0.6376281380653381 * (10.0 ** (amount_db / 40.0))

    src.plugins.append(Plugin(
        id=_id("plg"),
        type="auxsend",
        name=f"Send #{bus_num + 1}",
        params={
            "busNum": int(bus_num),
            "auxSendSliderPos": float(slider),
            "phase": 0,
        },
    ))
    src.sends.append(Send(dest_track_id=dest_track_id or "", amount_db=amount_db, pre_post=pre_post))  # type: ignore[arg-type]
    return {"track_id": src_track_id, "bus_num": bus_num, "amount_db": amount_db}


@op("aux_return_add", intent_template="Add aux return bus {bus_num} on {track_id}")
def aux_return_add(edit: Edit, track_id: str, bus_num: int = 0) -> dict:
    """Add an auxreturn plugin to a track so it receives audio from
    `bus_num`. Pair with send_add(bus_num=N) on source tracks.

    Schema: <PLUGIN type="auxreturn" busNum="0"/>
    """
    t = edit.track(track_id)
    t.plugins.append(Plugin(
        id=_id("plg"),
        type="auxreturn",
        name=f"Return #{int(bus_num) + 1}",
        params={"busNum": int(bus_num)},
    ))
    return {"track_id": track_id, "bus_num": int(bus_num)}


@op("marker_add", intent_template="Marker '{name}' @ beat {start_beats}")
def marker_add(edit: Edit, name: str, start_beats: float, length_beats: float = 0.0) -> dict:
    m = Marker(name=name, start_beats=start_beats, length_beats=length_beats)
    edit.markers.append(m)
    return {"marker": name}


@op("tempo_set", intent_template="Tempo → {bpm} BPM")
def tempo_set(edit: Edit, bpm: float) -> dict:
    edit.tempo = bpm
    return {}


@op("key_set", intent_template="Key → {root} {mode}")
def key_set(edit: Edit, root: int, mode: str) -> dict:
    edit.key_root = root
    edit.key_mode = mode  # type: ignore[assignment]
    return {}


@op("bus_create", intent_template="Create {kind} bus '{name}'")
def bus_create(edit: Edit, name: str, kind: str = "submix") -> dict:
    """Create a submix / aux track that other tracks can route into.
    Use bus_route to set a track's output, or send_add for parallel sends."""
    t = Track(id=_id("trk"), name=name, kind=kind)  # type: ignore[arg-type]
    edit.tracks.append(t)
    return {"track_id": t.id, "kind": kind}


@op("bus_route", intent_template="Route {src_track_id} → {dest_bus_id}")
def bus_route(edit: Edit, src_track_id: str, dest_bus_id: str) -> dict:
    """Set src_track's output to flow into dest_bus instead of master.

    Tracktion implements bus routing by NESTING tracks inside a FOLDERTRACK
    rather than a logical "output to bus" reference. We set `Track.output`
    in the model for now; the XML writer doesn't yet emit nested children
    (needs another fixture: capture a project where a track has been
    physically dragged into a submix folder). The bus FOLDERTRACK itself
    IS emitted correctly — it just doesn't have routed children yet.
    """
    src = edit.track(src_track_id)
    edit.track(dest_bus_id)  # validate exists
    src.output = dest_bus_id
    return {"track_id": src_track_id, "output": dest_bus_id,
            "note": "FOLDERTRACK emitted; child-track nesting awaits next fixture pass"}


@op("mix_apply_reference", intent_template="Apply {genre}/{role} mix to {track_id}")
def mix_apply_reference(
    edit: Edit,
    track_id: str,
    genre: str,
    role: str,
    pan: float | None = None,
    offset_db: float = 0.0,
) -> dict:
    """Look up the (genre, role) reference dB and apply it as the track's
    static volume_db. Optional pan & offset_db for fine adjustments.
    """
    from .. import music_theory_data as data
    t = edit.track(track_id)
    ref = data.mix_level_for(genre, role)
    t.volume_db = ref + float(offset_db)
    if pan is not None:
        t.pan = float(pan)
    return {
        "track_id": track_id, "genre": genre, "role": role,
        "reference_db": ref, "applied_db": t.volume_db,
    }


HANDLERS = {
    "track_add": track_add,
    "track_remove": track_remove,
    "mix_set": mix_set,
    "send_add": send_add,
    "marker_add": marker_add,
    "tempo_set": tempo_set,
    "key_set": key_set,
    "mix_apply_reference": mix_apply_reference,
    "bus_create": bus_create,
    "bus_route": bus_route,
    "aux_return_add": aux_return_add,
}
