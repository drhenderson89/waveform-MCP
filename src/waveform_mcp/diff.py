"""Compute structural diffs between two Edit snapshots.

Used by the EditStore to attach a `diff` field to each event so the preview
UI knows exactly what changed without re-walking the whole tree, and so the
JSONL log is human-readable when triaging "what did the LLM do at step N."

We don't try to be a generic JSON-diff library. We compare at the granularity
the preview UI cares about: which tracks/clips/notes were added/removed/changed.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .model import Edit, MidiClip, Track


def _track_index(edit: Edit) -> dict[str, Track]:
    return {t.id: t for t in edit.tracks}


def _clip_owner(edit: Edit, clip_id: str) -> str | None:
    for t in edit.tracks:
        for c in t.clips:
            if c.id == clip_id:
                return t.id
    return None


def diff_edits(before: Edit, after: Edit) -> dict[str, Any]:
    """Return a coarse diff suitable for streaming to a UI."""
    out: dict[str, Any] = {}

    # top-level scalars
    for field in ("tempo", "time_sig_num", "time_sig_denom", "key_root", "key_mode", "length_beats", "name"):
        if getattr(before, field) != getattr(after, field):
            out.setdefault("edit", {})[field] = {
                "from": getattr(before, field),
                "to": getattr(after, field),
            }

    bt = _track_index(before)
    at = _track_index(after)

    added_tracks = [asdict(at[i]) for i in at if i not in bt]
    removed_tracks = [bt[i].id for i in bt if i not in at]
    if added_tracks:
        out["tracks_added"] = added_tracks
    if removed_tracks:
        out["tracks_removed"] = removed_tracks

    # per-track changes (mix, plugins, clips, notes)
    track_changes: list[dict[str, Any]] = []
    for tid in at:
        if tid not in bt:
            continue
        before_t = bt[tid]
        after_t = at[tid]
        change: dict[str, Any] = {}

        for field in ("name", "volume_db", "pan", "mute", "solo", "output"):
            if getattr(before_t, field) != getattr(after_t, field):
                change[field] = {"from": getattr(before_t, field), "to": getattr(after_t, field)}

        # plugins (compare by id sequence + names, coarse)
        if [(p.id, p.name) for p in before_t.plugins] != [
            (p.id, p.name) for p in after_t.plugins
        ]:
            change["plugins"] = [
                {"id": p.id, "type": p.type, "name": p.name} for p in after_t.plugins
            ]

        # clips
        before_clip_ids = {c.id for c in before_t.clips}
        after_clip_ids = {c.id for c in after_t.clips}
        if before_clip_ids != after_clip_ids:
            change["clips_added"] = [
                _clip_brief(c) for c in after_t.clips if c.id not in before_clip_ids
            ]
            change["clips_removed"] = list(before_clip_ids - after_clip_ids)

        # notes — for clips that exist in both, compare counts
        for after_clip in after_t.clips:
            if not isinstance(after_clip, MidiClip):
                continue
            for before_clip in before_t.clips:
                if before_clip.id == after_clip.id and isinstance(before_clip, MidiClip):
                    if len(before_clip.notes) != len(after_clip.notes):
                        change.setdefault("notes_changed", []).append(
                            {
                                "clip_id": after_clip.id,
                                "from": len(before_clip.notes),
                                "to": len(after_clip.notes),
                            }
                        )
                    break

        if change:
            change["track_id"] = tid
            track_changes.append(change)

    if track_changes:
        out["tracks_changed"] = track_changes

    return out


def _clip_brief(c) -> dict[str, Any]:
    base = {
        "id": c.id,
        "kind": c.kind,
        "name": c.name,
        "start": c.start_beats,
        "length": c.length_beats,
    }
    if isinstance(c, MidiClip):
        base["note_count"] = len(c.notes)
    return base
