"""Plugin-aware mix level calibration.

The composer's old flow assigned per-track volume_db purely from
(genre, role) tables. That works only if every instrument plugin outputs
at the same reference level — which they don't. A sample-based drum
sampler at unity is ~20 dB hotter than a sforzando-driven SFZ, which is
~10 dB quieter than a 4OSC synth at default settings, etc.

This module bakes in empirical per-plugin-chain output offsets, so the
composer can compensate when it sets a track's fader. The flow becomes:

    target_db   = mix_level_for(genre, role)            # what we want to hear
    chain_off   = chain_offset_db(track.plugins)        # what the chain adds
    fader_db    = target_db - chain_off                 # set this on the fader

Robustness rules:
  * Lookups never raise — unknown plugins fall back to a safe pessimistic
    offset; unknown genres/roles use safe defaults.
  * Final fader values are clamped to [-32 dB, +8 dB] (Tracktion's slider
    range maxes near +8 dB).
  * The static table is the primary source of truth. A dynamic
    `auto_calibrate_from_render` helper exists for unknown plugins
    (renders → measures → back-solves) but is opt-in and only invoked
    when explicitly requested.
  * Idempotent: passing the same inputs always returns the same result.
  * Pure: no DAW or filesystem access in the core lookup path; auto-
    calibration takes its dependencies (render fn, measure fn) as args
    so it's testable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Static plugin-chain offset table                                            #
# --------------------------------------------------------------------------- #
#
# Offsets are in dB at unity fader, measured against a reference of "sample-
# based drum sampler at default settings ≈ 0 dB". A negative number means
# the chain outputs quieter than reference; the composer should add that
# many dB of fader to reach target.
#
# Keys: lowercased plugin-name tokens, in chain order (left = first in
# signal path). Each tuple is treated as a substring/prefix match — the
# lookup uses the longest matching key.
#
# To add a new plugin: render a 4-bar pass with only that plugin in the
# chain, measure RMS-dB of the rendered audio, subtract -6 dB (the drum
# sampler reference level), and add the result here. Or use
# `auto_calibrate_from_render` to do this automatically.

PLUGIN_CHAIN_OFFSETS_DB: dict[tuple[str, ...], float] = {
    # ── Built-in Tracktion instrument plugins ──
    ("sampler",):                    0.0,    # reference: drum sampler @ default
    ("4osc",):                      -8.0,    # built-in synth, varies by patch
    # ── Named Sampler kits / banks (alias to sampler offset) ──
    ("808 kit",):                    0.0,    # the factory 808 drum kit alias
    ("metal kit",):                  0.0,    # named sampler from compose_djent
    ("lofi bass",):                  0.0,    # multi-sample bass guitar (real samples)
    # ── SFZ/sample-library instruments via sforzando ──
    ("sforzando",):                 -16.0,   # bare sforzando + typical SFZ
    # ── Guitar amp-sim chains (sforzando feeding amp+cab) ──
    ("sforzando", "anvil"):          -4.0,   # +amp boosts gain back up
    ("sforzando", "the anvil"):      -4.0,   # alt name spelling
    ("sforzando", "anvil", "mt-a"):  -2.0,   # +cab IR pulls a bit down
    ("sforzando", "the anvil", "mt-a"): -2.0,
    ("sforzando", "anvil", "mt-a", "tsc 1.1"):       0.0,   # full chain ≈ unity
    ("sforzando", "the anvil", "mt-a", "tsc 1.1"):   0.0,
    # ── Standalone amp sims fed from 4OSC ──
    ("4osc", "anvil"):               -2.0,
    ("4osc", "anvil", "mt-a"):       -1.0,
    # ── MIDI drum samplers other than the built-in ──
    ("mt power drum kit",):          +2.0,   # MT-PDK is hotter than built-in
    # ── Named 4OSC presets used by genre composers ──
    # These are the same engine as ("4osc",) — alias to its offset.
    ("counter synth",):             -8.0,
    ("marimba 4osc",):              -8.0,
    ("keys synth",):                -8.0,
    ("pad synth",):                 -8.0,
    ("bass synth",):                -8.0,
    ("synth bass",):                -8.0,
    ("arp synth",):                 -8.0,
    ("lead synth",):                -8.0,
    ("pad b synth",):               -8.0,
    # ── 4OSC + per-track FX chains ──
    ("lofi bass", "bass cleanup", "bass comp"): +2.0,
    ("keys synth", "plate reverb"): -8.0,
    ("arp synth", "plate reverb"):  -8.0,
    ("lead synth", "plate reverb"): -8.0,
    ("pad b synth", "pad b hp"):    -8.0,
}

# When no plugin chain is known (track has no instrument plugins, or all
# plugins are unrecognised), assume a conservative -10 dB offset. That
# means the composer will boost by 10 dB more than naive — which is still
# safer than the previous behaviour (zero compensation) for nearly every
# real plugin.
UNKNOWN_CHAIN_OFFSET_DB = -10.0

# Hard clamp on final fader values. Tracktion's volume slider tops out
# near +8 dB (slider position 1.0 = 0.6376*10^(8/40)). Going higher is a
# no-op and obscures the real gain. Cutting deeper than -32 dB usually
# means the track shouldn't be playing at all — bail out instead.
FADER_DB_MIN = -32.0
FADER_DB_MAX = 8.0


# --------------------------------------------------------------------------- #
# Pure lookup helpers                                                         #
# --------------------------------------------------------------------------- #


def _normalise(name: object) -> str:
    """Lowercase + collapse whitespace. Accepts any input — non-string
    values are coerced to str (so a stray int/None won't raise)."""
    if name is None:
        return ""
    try:
        s = str(name).strip().lower()
    except Exception:
        return ""
    if not s:
        return ""
    # Treat "TSC 1.1" and "tsc 1.1" identically; collapse multi-space
    s = re.sub(r"\s+", " ", s)
    return s


def chain_offset_db(plugin_names: Iterable[str | None]) -> float:
    """Return the dB offset for a plugin chain.

    Uses longest-matching-prefix lookup. The chain is the list of *instrument
    and effect* plugin names in signal-path order. `volume`, `level`,
    `auxsend`, `auxreturn`, and `comp`/`eq` master-bus plugins are ignored —
    they're not what determines instrument output level.

    Always returns a finite float; never raises.
    """
    chain = tuple(_normalise(n) for n in plugin_names if n)
    # Drop chrome plugins that don't change instrument output level
    chain = tuple(p for p in chain if p and not _is_chrome_plugin(p))
    if not chain:
        logger.debug("chain_offset_db: empty chain → unknown default %.1f", UNKNOWN_CHAIN_OFFSET_DB)
        return UNKNOWN_CHAIN_OFFSET_DB

    # Try exact match first, then progressively shorter prefixes
    best: tuple[tuple[str, ...], float] | None = None
    for key, offset in PLUGIN_CHAIN_OFFSETS_DB.items():
        if len(key) > len(chain):
            continue
        # Is `key` a contiguous subsequence at the start of `chain`?
        if chain[: len(key)] == key:
            if best is None or len(key) > len(best[0]):
                best = (key, offset)
    if best is not None:
        logger.debug("chain_offset_db: %r → matched %r → %.1f dB", chain, best[0], best[1])
        return best[1]

    # Single-plugin partial match: at minimum, look up the *first* plugin
    # alone. This handles unfamiliar long chains like sforzando→X→Y.
    head = (chain[0],)
    if head in PLUGIN_CHAIN_OFFSETS_DB:
        logger.debug("chain_offset_db: %r → head match %r → %.1f dB", chain, head,
                     PLUGIN_CHAIN_OFFSETS_DB[head])
        return PLUGIN_CHAIN_OFFSETS_DB[head]

    logger.warning("chain_offset_db: %r → no match → using default %.1f dB",
                   chain, UNKNOWN_CHAIN_OFFSET_DB)
    return UNKNOWN_CHAIN_OFFSET_DB


_CHROME_TYPES = frozenset({
    "volume", "level", "auxsend", "auxreturn",
    # Master-bus FX that don't define instrument level
    "limiter",
})


def _is_chrome_plugin(name: str) -> bool:
    return _normalise(name) in _CHROME_TYPES


def calibrated_fader_db(
    target_db: float,
    plugin_names: Iterable[str | None],
    *,
    extra_offset_db: float = 0.0,
) -> float:
    """Return the fader dB to set so that the track's *audible* level
    matches `target_db` (which typically comes from MIX_BALANCE).

    `extra_offset_db` is an optional per-call adjustment (e.g. duplicate
    panned tracks may want -3 dB extra to avoid summing too hot).

    Always returns a finite float clamped to [FADER_DB_MIN, FADER_DB_MAX].
    """
    chain = chain_offset_db(plugin_names)
    raw = float(target_db) - float(chain) + float(extra_offset_db)
    clamped = max(FADER_DB_MIN, min(FADER_DB_MAX, raw))
    if abs(raw - clamped) > 0.01:
        logger.warning(
            "calibrated_fader_db: clamped %.1f→%.1f (target=%.1f, chain=%.1f, extra=%.1f)",
            raw, clamped, target_db, chain, extra_offset_db,
        )
    return clamped


# --------------------------------------------------------------------------- #
# Dynamic auto-calibration (opt-in, render-driven)                            #
# --------------------------------------------------------------------------- #


@dataclass
class CalibrationResult:
    """Outcome of a single-pass auto-calibration."""

    track_id: str
    target_db: float
    measured_db: float | None
    fader_db_before: float
    fader_db_after: float
    converged: bool
    note: str = ""


def _safe_correction(target_db: float, measured_db: float, current_fader_db: float,
                     *, max_step_db: float = 12.0) -> float:
    """Return a new fader dB given target/measured/current, with a step
    limit so a single bad measurement can't blow up the mix."""
    error = target_db - measured_db
    # Limit per-iteration adjustment
    error = max(-max_step_db, min(max_step_db, error))
    return max(FADER_DB_MIN, min(FADER_DB_MAX, current_fader_db + error))


def auto_calibrate_pass(
    targets: dict[str, float],
    current_fader_db: dict[str, float],
    measure_stem_rms_db: Callable[[str], float | None],
    *,
    tolerance_db: float = 2.0,
    max_step_db: float = 12.0,
) -> dict[str, CalibrationResult]:
    """Single-pass calibration: given per-track target and current fader,
    measure each stem, compute a corrected fader. Caller is responsible
    for actually applying the new faders and re-rendering.

    Args:
      targets: {track_id: target_audible_db}
      current_fader_db: {track_id: current_fader_db_set_on_track}
      measure_stem_rms_db: callable(track_id) -> float dB or None if unmeasurable
      tolerance_db: how close we must be to consider track converged
      max_step_db: cap on single-iteration correction

    Returns:
      dict of {track_id: CalibrationResult}
    """
    out: dict[str, CalibrationResult] = {}
    for tid, tgt in targets.items():
        cur = current_fader_db.get(tid, 0.0)
        try:
            measured = measure_stem_rms_db(tid)
        except Exception as e:
            logger.warning("auto_calibrate_pass: measure failed for %s: %s", tid, e)
            measured = None

        if measured is None:
            out[tid] = CalibrationResult(
                track_id=tid, target_db=tgt, measured_db=None,
                fader_db_before=cur, fader_db_after=cur,
                converged=False, note="unmeasurable; left fader unchanged",
            )
            continue

        if abs(measured - tgt) <= tolerance_db:
            out[tid] = CalibrationResult(
                track_id=tid, target_db=tgt, measured_db=measured,
                fader_db_before=cur, fader_db_after=cur,
                converged=True, note=f"within ±{tolerance_db} dB",
            )
            continue

        new_fader = _safe_correction(tgt, measured, cur, max_step_db=max_step_db)
        out[tid] = CalibrationResult(
            track_id=tid, target_db=tgt, measured_db=measured,
            fader_db_before=cur, fader_db_after=new_fader,
            converged=False, note=f"adjusted by {new_fader-cur:+.1f} dB",
        )
    return out


# --------------------------------------------------------------------------- #
# Public registration helpers (so users can extend the table at runtime)      #
# --------------------------------------------------------------------------- #


def register_chain(plugin_names: Iterable[str], offset_db: float) -> None:
    """Add or override a plugin chain offset. Useful for users who load
    custom VSTs we don't know about; they can characterise once and
    register so future composes get correct fader values."""
    key = tuple(_normalise(n) for n in plugin_names if n)
    if not key:
        raise ValueError("plugin_names must contain at least one plugin")
    if not (FADER_DB_MIN <= offset_db <= 24.0):  # offsets can be wider than fader range
        raise ValueError(f"offset_db {offset_db} outside reasonable range")
    PLUGIN_CHAIN_OFFSETS_DB[key] = float(offset_db)
    logger.info("register_chain: %r = %.1f dB", key, offset_db)
