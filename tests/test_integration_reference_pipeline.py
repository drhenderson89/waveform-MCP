"""Integration tests for the full reference → spec → edit pipeline.

Uses pre-computed analyzer JSONs from the music-analyzer repo's downloads
folder. Skips automatically if those JSONs aren't present (e.g. clean
clone). Doesn't run the analyzer subprocess — that's exercised via mocks
in test_import_song.py.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from waveform_mcp.composition_spec import CompositionSpec
from waveform_mcp.reference_adapter import (
    AdapterOptions,
    spec_from_analyzer_json,
)
from waveform_mcp.tools.compose_from_spec import compose_from_spec
from waveform_mcp.model import STORE


ANALYSES_DIR = Path(r"C:\Users\jarms\repos\music-analyzer\downloads")


def _all_real_analyses() -> list[Path]:
    if not ANALYSES_DIR.exists():
        return []
    return sorted(ANALYSES_DIR.glob("*_full_analysis.json"))


pytestmark = pytest.mark.skipif(
    not _all_real_analyses(),
    reason="no analyzer JSONs found in music-analyzer/downloads",
)


def _tmp_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".tracktionedit")
    os.close(fd)
    os.remove(path)
    return path


# --------------------------------------------------------------------------- #
# End-to-end: every real analyzer JSON should produce a buildable edit       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("json_path", _all_real_analyses(),
                         ids=lambda p: p.name)
def test_real_analysis_round_trips_to_edit(json_path: Path):
    """For every analyzer JSON we have on disk:
       1. Load it
       2. Adapt to CompositionSpec
       3. Validate the spec
       4. Build a .tracktionedit
       5. Round-trip the spec through to_dict/from_dict
    """
    with open(json_path, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    # 1+2: adapt
    spec = spec_from_analyzer_json(analysis)
    assert isinstance(spec, CompositionSpec)
    assert spec.tempo_bpm > 0
    assert spec.tracks, f"no tracks produced for {json_path.name}"

    # 3: validate
    problems = spec.validate()
    # We tolerate some non-fatal problems but log them
    if problems:
        print(f"\n  {json_path.name} validation notes:")
        for p in problems[:5]:
            print(f"    {p}")

    # 4: build
    out = _tmp_path()
    try:
        result = compose_from_spec({"spec": spec, "out_path": out})
        assert result["ok"] is True, result
        assert os.path.exists(out)
        assert os.path.getsize(out) > 100
        assert result["bars"] > 0
        assert result["tracks"]  # at least one track
    finally:
        if os.path.exists(out):
            os.remove(out)

    # 5: round-trip
    rebuilt = CompositionSpec.from_dict(spec.to_dict())
    assert rebuilt.tempo_bpm == spec.tempo_bpm
    assert rebuilt.genre == spec.genre
    assert len(rebuilt.tracks) == len(spec.tracks)


# --------------------------------------------------------------------------- #
# Specific cross-genre check: forced genre takes effect                       #
# --------------------------------------------------------------------------- #


def test_force_genre_metal_produces_metal_tracks():
    json_files = _all_real_analyses()
    if not json_files:
        pytest.skip("no analysis JSONs available")
    with open(json_files[0], "r", encoding="utf-8") as f:
        analysis = json.load(f)

    spec = spec_from_analyzer_json(
        analysis,
        options=AdapterOptions(force_genre="metal"),
    )
    assert spec.genre == "metal"
    roles = {t.role for t in spec.tracks}
    assert "rhythm_guitar_l" in roles
    assert "rhythm_guitar_r" in roles


def test_bars_override_caps_total_length():
    json_files = _all_real_analyses()
    if not json_files:
        pytest.skip("no analysis JSONs available")
    with open(json_files[0], "r", encoding="utf-8") as f:
        analysis = json.load(f)

    spec = spec_from_analyzer_json(
        analysis,
        options=AdapterOptions(bars_override=8),
    )
    # 8 bars * time_sig_num beats. Allow a half-beat margin.
    max_beats = 8 * spec.time_sig_num + 0.5
    for s in spec.sections:
        assert s.start_beat + s.length_beats <= max_beats


# --------------------------------------------------------------------------- #
# Resource cleanup                                                            #
# --------------------------------------------------------------------------- #


def test_global_store_isolated_after_build():
    """After compose_from_spec finishes, STORE should hold the just-built
    edit (we use it for inspection in tests). Verifies the build didn't
    leave the store in a corrupt state."""
    json_files = _all_real_analyses()
    if not json_files:
        pytest.skip("no analysis JSONs available")
    with open(json_files[0], "r", encoding="utf-8") as f:
        analysis = json.load(f)

    spec = spec_from_analyzer_json(analysis)
    out = _tmp_path()
    try:
        compose_from_spec({"spec": spec, "out_path": out})
        # STORE.edit should be the spec we just built
        assert STORE.edit is not None
        assert STORE.edit.tempo == spec.tempo_bpm
        assert len(STORE.edit.tracks) == len(spec.tracks)
    finally:
        if os.path.exists(out):
            os.remove(out)
