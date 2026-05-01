"""Tests against the real Waveform 13.5 fixture in tests/fixtures/.

The fixture was captured by the win_input + waveform_workflows tools, hands-off
from a running Waveform instance. Re-capture by running waveform_new_project
+ waveform_save and copying the resulting .tracktionedit into fixtures/.
"""

from __future__ import annotations

from pathlib import Path

from waveform_mcp.xml_reader import read

FIXTURE = Path(__file__).parent / "fixtures" / "ref.tracktionedit"


def test_real_fixture_loads():
    """The reader tolerates the real Waveform schema and finds the 8 default tracks."""
    if not FIXTURE.exists():
        # fixture not captured yet — skip rather than fail in fresh checkouts
        return
    edit = read(str(FIXTURE))
    assert edit.tempo == 120.0
    assert edit.time_sig_num == 4
    assert edit.time_sig_denom == 4
    assert len(edit.tracks) == 8


def test_real_fixture_track_attributes():
    """Track ids and colours roundtripped from real format."""
    if not FIXTURE.exists():
        return
    edit = read(str(FIXTURE))
    # IDs are integer-strings allocated sequentially by Waveform
    assert all(t.id.isdigit() for t in edit.tracks)
    # 8 default tracks have no user-added plugins
    for t in edit.tracks:
        assert t.plugins == []
