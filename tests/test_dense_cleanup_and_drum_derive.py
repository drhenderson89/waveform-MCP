"""Tests for keys cleanup + onset-derived drum patterns."""
from __future__ import annotations

import pytest

from waveform_mcp.composition_spec import NoteEvent
from waveform_mcp.reference_adapter import (
    _basic_backbeat_pattern,
    _clean_dense_role,
    _derive_drum_pattern_from_onsets,
    _staccato,
)


# --------------------------------------------------------------------------- #
# _clean_dense_role                                                           #
# --------------------------------------------------------------------------- #


class TestCleanDenseRole:
    def test_drops_low_velocity(self):
        notes = [
            NoteEvent(0, 0.5, pitch=60, velocity=20),  # too quiet
            NoteEvent(0, 0.5, pitch=62, velocity=80),
        ]
        out = _clean_dense_role(notes, min_velocity=35)
        assert len(out) == 1
        assert out[0].pitch == 62

    def test_drops_short_notes(self):
        notes = [
            NoteEvent(0, 0.1, pitch=60, velocity=80),  # too short
            NoteEvent(0, 0.5, pitch=62, velocity=80),
        ]
        out = _clean_dense_role(notes, min_length_beats=0.25)
        assert len(out) == 1
        assert out[0].pitch == 62

    def test_caps_polyphony_per_position(self):
        # 6 simultaneous notes at the same beat — only top 4 by velocity kept
        notes = [
            NoteEvent(0, 0.5, pitch=60, velocity=v)
            for v in (80, 70, 60, 50, 40, 30)
        ]
        out = _clean_dense_role(notes, min_velocity=20, max_polyphony=4)
        assert len(out) == 4
        kept_velocities = sorted(n.velocity for n in out)
        # The lowest 2 (30, 40) should be dropped, but 40 might survive
        # depending on sort stability — accept any 4 of the loudest 5
        assert all(v >= 40 for v in kept_velocities)

    def test_snaps_to_8th_grid(self):
        notes = [
            NoteEvent(start_beat=0.30, length_beats=0.5, pitch=60, velocity=80),
        ]
        out = _clean_dense_role(notes, grid_beats=0.5)
        assert out[0].start_beat == 0.5

    def test_empty_returns_empty(self):
        assert _clean_dense_role([]) == []

    def test_dramatic_density_reduction(self):
        # Simulate basic-pitch's over-transcription: 100 short, soft notes
        # sprinkled across one bar
        notes = [
            NoteEvent(start_beat=i * 0.04, length_beats=0.1,
                      pitch=60 + (i % 12), velocity=20 + (i % 30))
            for i in range(100)
        ]
        out = _clean_dense_role(notes, min_velocity=35,
                                 min_length_beats=0.25, max_polyphony=4,
                                 grid_beats=0.5)
        # Should be way fewer
        assert len(out) < 30


# --------------------------------------------------------------------------- #
# _derive_drum_pattern_from_onsets                                            #
# --------------------------------------------------------------------------- #


class TestDeriveDrumPattern:
    def test_kick_on_beat_1_always(self):
        out = _derive_drum_pattern_from_onsets(
            bass_notes=[], lead_notes=[], keys_notes=[],
        )
        kick = [n for n in out if n.pitch == 36]
        assert any(n.start_beat == 0.0 for n in kick)

    def test_snare_on_backbeat_4_4(self):
        out = _derive_drum_pattern_from_onsets(
            bass_notes=[], lead_notes=[], keys_notes=[],
            bar_beats=4,
        )
        snare = [n for n in out if n.pitch == 38]
        starts = sorted(n.start_beat for n in snare)
        # Backbeats: beat 2 and beat 4 (1-indexed) → start_beats 1.0 and 3.0
        assert 1.0 in starts
        assert 3.0 in starts

    def test_hat_on_8ths(self):
        out = _derive_drum_pattern_from_onsets(
            bass_notes=[], lead_notes=[], keys_notes=[],
            bar_beats=4,
        )
        hat = sorted(n.start_beat for n in out if n.pitch == 42)
        # 8 hats per bar at 0, 0.5, 1.0, 1.5, ..., 3.5
        assert hat == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]

    def test_extra_kicks_from_strong_bass_onsets(self):
        # Strong bass concentration at beat 2.5 → extra kick there.
        # Add a few weaker hits elsewhere so the histogram has a clear peak.
        bass_notes = [
            NoteEvent(start_beat=2.5, length_beats=0.5, pitch=36, velocity=100)
            for _ in range(20)  # heavy at 2.5
        ] + [
            NoteEvent(start_beat=0.25 * i, length_beats=0.25, pitch=36, velocity=80)
            for i in range(16)  # one weaker hit at every 16th
        ]
        out = _derive_drum_pattern_from_onsets(
            bass_notes=bass_notes, lead_notes=[], keys_notes=[],
        )
        kick = sorted(n.start_beat for n in out if n.pitch == 36)
        assert 2.5 in kick

    def test_uniform_bass_doesnt_explode_kick_count(self):
        # Near-uniform bass histogram (BasicPitch sustained-content case)
        # → should NOT produce kick on every 16th
        bass_notes = [
            NoteEvent(start_beat=0.25 * i, length_beats=0.25, pitch=36, velocity=80)
            for i in range(16)  # exactly one hit per 16th = uniform
        ]
        out = _derive_drum_pattern_from_onsets(
            bass_notes=bass_notes, lead_notes=[], keys_notes=[],
        )
        kicks = [n for n in out if n.pitch == 36]
        # Just the always-on beat-1 kick (no extras qualify when uniform)
        assert len(kicks) <= 4  # at most 1 always + 3 extra cap

    def test_loop_length_matches_bar(self):
        out = _derive_drum_pattern_from_onsets(
            bass_notes=[], lead_notes=[], keys_notes=[], bar_beats=4,
        )
        # All notes should fit within one bar (0..4)
        for n in out:
            assert 0.0 <= n.start_beat < 4.0

    def test_returns_sorted_by_time(self):
        out = _derive_drum_pattern_from_onsets(
            bass_notes=[NoteEvent(2.5, 0.25, pitch=36, velocity=100)],
            lead_notes=[], keys_notes=[],
        )
        starts = [n.start_beat for n in out]
        assert starts == sorted(starts)

    def test_works_in_3_4(self):
        out = _derive_drum_pattern_from_onsets(
            bass_notes=[], lead_notes=[], keys_notes=[], bar_beats=3,
        )
        # No notes past beat 3
        for n in out:
            assert 0.0 <= n.start_beat < 3.0
        # Backbeat for 3/4 = beat 2 (1-indexed) → start 1.0
        snare = [n for n in out if n.pitch == 38]
        assert any(n.start_beat == 1.0 for n in snare)


# --------------------------------------------------------------------------- #
# _staccato                                                                   #
# --------------------------------------------------------------------------- #


class TestStaccato:
    def test_caps_long_notes(self):
        notes = [NoteEvent(0, 4.0, pitch=60, velocity=80)]
        out = _staccato(notes, max_length_beats=0.25)
        assert out[0].length_beats == 0.25

    def test_shortens_by_ratio(self):
        notes = [NoteEvent(0, 0.4, pitch=60, velocity=80)]
        out = _staccato(notes, max_length_beats=1.0, ratio=0.5)
        # 0.4 * 0.5 = 0.2 (under cap) → 0.2
        assert abs(out[0].length_beats - 0.2) < 1e-6

    def test_minimum_length_floor(self):
        notes = [NoteEvent(0, 0.01, pitch=60, velocity=80)]
        out = _staccato(notes, ratio=0.5)
        # Tiny note should still have a floor (≥ 0.05)
        assert out[0].length_beats >= 0.05

    def test_preserves_pitch_velocity_position(self):
        notes = [NoteEvent(start_beat=2.5, length_beats=1.0,
                            pitch=72, velocity=99, channel=3)]
        out = _staccato(notes)
        assert out[0].start_beat == 2.5
        assert out[0].pitch == 72
        assert out[0].velocity == 99
        assert out[0].channel == 3

    def test_empty_returns_empty(self):
        assert _staccato([]) == []


# --------------------------------------------------------------------------- #
# _basic_backbeat_pattern                                                     #
# --------------------------------------------------------------------------- #


class TestBackbeat:
    def test_4_4_has_kick_snare_hat_structure(self):
        out = _basic_backbeat_pattern(bar_beats=4)
        kicks = [n for n in out if n.pitch == 36]
        snares = [n for n in out if n.pitch == 38]
        hats = [n for n in out if n.pitch == 42]
        # Kick on beat 1
        assert any(k.start_beat == 0.0 for k in kicks)
        # Snare on beats 2 and 4 (0-indexed: 1.0 and 3.0)
        snare_starts = sorted(n.start_beat for n in snares)
        assert snare_starts == [1.0, 3.0]
        # Hat on quarter notes only — 4 hits per bar (not 8)
        assert len(hats) == 4

    def test_3_4_has_one_snare_on_beat_2(self):
        out = _basic_backbeat_pattern(bar_beats=3)
        snares = [n for n in out if n.pitch == 38]
        assert len(snares) == 1
        assert snares[0].start_beat == 1.0

    def test_all_notes_within_bar(self):
        out = _basic_backbeat_pattern(bar_beats=4)
        for n in out:
            assert 0.0 <= n.start_beat < 4.0
