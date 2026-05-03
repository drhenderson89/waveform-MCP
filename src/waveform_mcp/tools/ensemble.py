"""Ensemble cohesion primitives — make the song breathe as a unit.

Every generative composer was rolling its own ad-hoc per-section
velocity multipliers, phrase-position math, and "is this a solo bar"
detection. This module factors that into one place so:

  * Section dynamics curves are consistent (intro/build/peak/outro)
  * `_bar_in_phrase`, `_section_at_bar`, `_is_solo_bar` are written once
  * Drum-comp-for-soloist + phrase-end fills + lay-out logic compose
    cleanly across genres

Anti-pattern these primitives prevent: "all the pieces were made
separately and sound nice on their own but together it just sounds
like a puzzle mashed together with glue."
"""
from __future__ import annotations

from typing import Any


# ── Section dynamic scale defaults ──
# Standard intro→build→peak→cooldown shape. Multiply velocities by
# these factors to make the ensemble swell and recede together.
SECTION_DYNAMICS_DEFAULT: dict[str, float] = {
    # Generic section names
    "intro":     0.55,
    "verse":     0.85,
    "pre_chorus": 0.92,
    "chorus":    1.05,
    "bridge":    1.05,
    "breakdown": 0.45,
    "buildup":   0.95,
    "drop":      1.10,
    "outro":     0.55,
    # AABA section names
    "A1":  0.85, "A2":  0.92, "A3":  0.95,
    "A4":  0.98, "A5":  0.88,
    "B":   1.05, "B2":  1.05,
    # Subdivisions seen in lofi/synthwave composers
    "chorus_a": 1.05, "chorus_b": 1.10,
    "verse_a":  0.85, "verse_b":  0.90,
}


def section_at_bar(sections_plan: list[dict], bar: int) -> dict | None:
    """Return the section dict whose [start, start+bars) range covers `bar`."""
    cursor = 0
    for sec in sections_plan:
        if cursor <= bar < cursor + sec["bars"]:
            return sec
        cursor += sec["bars"]
    return None


def section_start_bar(sections_plan: list[dict], role: str) -> int:
    """First bar index of the named section (0 if not found)."""
    cursor = 0
    for sec in sections_plan:
        if sec["role"] == role:
            return cursor
        cursor += sec["bars"]
    return 0


def bar_in_phrase(sections_plan: list[dict], bar: int,
                   phrase_length: int = 4) -> int:
    """Return position of `bar` within its 4-bar phrase (0..phrase_length-1).
    Counts from the start of the section the bar belongs to. Returns 0
    for silent sections (intro/outro) so end-of-phrase logic doesn't fire.
    """
    sec = section_at_bar(sections_plan, bar)
    if not sec or sec["role"] in ("intro", "outro"):
        return 0
    sec_start = section_start_bar(sections_plan, sec["role"])
    return (bar - sec_start) % phrase_length


def is_solo_phrase(sections_plan: list[dict], bar: int,
                    silent_roles: tuple[str, ...] = ("intro", "outro"),
                    phrase_length: int = 4) -> bool:
    """A "solo phrase" is a 4-bar phrase where the lead instrument is
    expected to peak (octave-lifted, contour-inverted, ornamented).
    The drummer should comp louder on those phrases.

    Heuristic: every 3rd and 4th of every 4-phrase cycle is a solo phrase.
    Phrases are counted across the whole song's non-silent bars.
    """
    sec = section_at_bar(sections_plan, bar)
    if not sec or sec["role"] in silent_roles:
        return False
    phrase_idx = 0
    cursor = 0
    while cursor < bar:
        sec_at = section_at_bar(sections_plan, cursor)
        if sec_at and sec_at["role"] not in silent_roles:
            if cursor + phrase_length <= bar + 1:
                phrase_idx += 1
            cursor += phrase_length
        else:
            cursor += 1
    return phrase_idx % 4 in (2, 3)


def section_dynamic_scale(sections_plan: list[dict], bar: int,
                           overrides: dict[str, float] | None = None
                           ) -> float:
    """Return the velocity multiplier for the section containing `bar`.
    `overrides` merges with SECTION_DYNAMICS_DEFAULT.
    """
    table = dict(SECTION_DYNAMICS_DEFAULT)
    if overrides:
        table.update(overrides)
    sec = section_at_bar(sections_plan, bar)
    if not sec:
        return 0.9
    return table.get(sec["role"], 0.9)


def scale_velocities(notes: list[dict], sections_plan: list[dict],
                      *, overrides: dict[str, float] | None = None,
                      compress: float = 1.0,
                      base: float = 0.0,
                      bar_length_beats: float = 4.0) -> list[dict]:
    """Apply section-aware velocity scaling to a list of notes IN PLACE.

    `compress` (0..1) compresses the dynamic curve toward unity — useful
    for the lead which shouldn't get TOO quiet relative to the rhythm
    section. `compress=0.3` means the curve is "30% of full swing" plus
    a floor of `base + (1 - compress) * 1.0` so a quiet section is
    e.g. 0.7 + 0.3 * 0.55 = 0.865 instead of 0.55.

    Returns the same list (for chaining).
    """
    floor = base + (1.0 - compress) * 1.0
    for n in notes:
        bar = int(float(n["start_beats"]) // bar_length_beats)
        scale = section_dynamic_scale(sections_plan, bar, overrides)
        scale = floor + compress * scale
        n["velocity"] = max(20, min(127, int(round(int(n["velocity"]) * scale))))
    return notes


def comp_for_lead_drums(lead_notes: list[dict],
                         sections_plan: list[dict],
                         total_bars: int,
                         *,
                         standard_kick_beats: tuple[int, ...] = (0, 2),
                         solo_kick_beats: tuple[int, ...] = (0,),
                         standard_snare_beats: tuple[int, ...] = (1, 3),
                         crash_on_solo_entry: bool = True,
                         phrase_length: int = 4,
                         midi_kick: int = 36, midi_snare: int = 38,
                         midi_rim: int = 37, midi_ride: int = 42,
                         midi_crash: int = 49,
                         silent_roles: tuple[str, ...] = ("intro", "outro"),
                         bar_length_beats: float = 4.0,
                         ) -> list[dict]:
    """Generate a drum part that COMPS for a given lead.

    Behaviour:
      * Standard bars: kick on `standard_kick_beats`, snare on `standard_snare_beats`,
        ride on every beat + 8th-note skip on beats 2 & 4.
      * Solo phrase bars: kick drops to `solo_kick_beats` (gives lead space),
        snare softer with rim-shot ghost on every lead off-beat, ride
        thinned to beats 1 & 3.
      * First bar of every solo phrase entry: crash cymbal punctuation.
      * Last bar of every 4-bar phrase: tom-roll fill (hi-mid-low).

    Returns notes ready to drop into a drum clip.
    """
    # Index lead onsets by bar
    lead_by_bar: dict[int, list[float]] = {}
    for n in lead_notes:
        bar = int(float(n["start_beats"]) // bar_length_beats)
        off = float(n["start_beats"]) - bar * bar_length_beats
        lead_by_bar.setdefault(bar, []).append(off)

    drum_notes: list[dict] = []
    for bar in range(total_bars):
        sec = section_at_bar(sections_plan, bar)
        if not sec or sec["role"] == "intro":
            for beat in range(4):
                drum_notes.append({"pitch": midi_ride,
                                    "start_beats": float(bar * 4 + beat),
                                    "length_beats": 0.4, "velocity": 55})
            continue
        outro = sec["role"] == "outro"
        is_solo = is_solo_phrase(sections_plan, bar, silent_roles,
                                  phrase_length) and not outro
        bp = bar_in_phrase(sections_plan, bar, phrase_length)
        is_fill_bar = (bp == phrase_length - 1) and not outro

        kick_beats = solo_kick_beats if is_solo else standard_kick_beats
        for beat in kick_beats:
            drum_notes.append({"pitch": midi_kick,
                                "start_beats": float(bar * 4 + beat),
                                "length_beats": 0.4,
                                "velocity": 100 if not outro else 75})
        for beat in standard_snare_beats:
            drum_notes.append({"pitch": midi_snare,
                                "start_beats": float(bar * 4 + beat),
                                "length_beats": 0.4,
                                "velocity": (80 if is_solo else 95)
                                            if not outro else 70})
        # Comp for the soloist on solo bars
        if is_solo:
            lead_off_beats = [off for off in lead_by_bar.get(bar, [])
                              if abs(off - round(off)) > 0.1]
            for off in lead_off_beats:
                if any(abs(off - b) < 0.15 for b in standard_snare_beats):
                    continue
                drum_notes.append({"pitch": midi_rim,
                                    "start_beats": float(bar * 4 + off),
                                    "length_beats": 0.18, "velocity": 50})
        # Ride
        if is_solo:
            for beat in (0, 2):
                drum_notes.append({"pitch": midi_ride,
                                    "start_beats": float(bar * 4 + beat),
                                    "length_beats": 0.45, "velocity": 60})
        else:
            for beat in range(4):
                drum_notes.append({"pitch": midi_ride,
                                    "start_beats": float(bar * 4 + beat),
                                    "length_beats": 0.45,
                                    "velocity": 78 if beat in (0, 2) else 70})
                if beat in (1, 3):
                    drum_notes.append({"pitch": midi_ride,
                                        "start_beats": float(bar * 4 + beat + 0.66),
                                        "length_beats": 0.3, "velocity": 62})
        # Crash on solo phrase entry
        if crash_on_solo_entry and is_solo:
            if bar == 0 or not is_solo_phrase(sections_plan, bar - 1,
                                                silent_roles, phrase_length):
                drum_notes.append({"pitch": midi_crash,
                                    "start_beats": float(bar * 4),
                                    "length_beats": 1.0, "velocity": 85})
        # Phrase-end tom fill
        if is_fill_bar:
            for pitch, off in [(50, 0.0), (48, 0.25), (45, 0.5)]:
                drum_notes.append({"pitch": pitch,
                                    "start_beats": float(bar * 4 + 3 + off),
                                    "length_beats": 0.22, "velocity": 75})
    return drum_notes


# ============================================================================
# MCP tool surface
# ============================================================================


def ensemble_dynamics(args: dict[str, Any]) -> dict[str, Any]:
    """Return section velocity multipliers for a sections_plan.

    Args:
      sections_plan: list of {role, bars, ...} dicts
      overrides: optional dict mapping role → multiplier

    Returns:
      {ok, scales: [{bar, role, scale}, ...]}
    """
    plan = args.get("sections_plan") or []
    overrides = args.get("overrides") or {}
    out = []
    cursor = 0
    for sec in plan:
        role = sec.get("role", "")
        scale = SECTION_DYNAMICS_DEFAULT.copy()
        scale.update(overrides)
        out.append({"bar_start": cursor, "bars": int(sec["bars"]),
                     "role": role, "scale": scale.get(role, 0.9)})
        cursor += int(sec["bars"])
    return {"ok": True, "scales": out, "total_bars": cursor}


HANDLERS = {"ensemble_dynamics": ensemble_dynamics}

TOOLS = [{
    "name": "ensemble_dynamics",
    "description": (
        "Return per-section velocity multipliers for a sections_plan. "
        "Apply uniformly to drums, bass, keys, lead notes so the whole "
        "ensemble breathes together (quiet intro, building verses, "
        "peak chorus/bridge, soft outro)."
    ),
    "input_schema": {
        "type": "object",
        "required": ["sections_plan"],
        "properties": {
            "sections_plan": {"type": "array",
                              "items": {"type": "object"}},
            "overrides": {"type": "object",
                          "description": "role → multiplier overrides"},
        },
    },
}]
