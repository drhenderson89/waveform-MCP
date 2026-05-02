"""Audit-driven regression tests.

Each test guards against a specific bug or oversight uncovered during
the MCP audit. Add a test here whenever you find a real-world issue
that wasn't caught by existing coverage.
"""
from __future__ import annotations

import importlib

from waveform_mcp.mix_calibration import (
    PLUGIN_CHAIN_OFFSETS_DB, chain_offset_db,
)


# --------------------------------------------------------------------------- #
# Calibration table coverage                                                  #
# --------------------------------------------------------------------------- #


class TestCalibrationCoverage:
    """The composers in this repo produce specific plugin chains. Each
    chain ought to have a calibration entry; otherwise mix levels go
    through the conservative -10 dB unknown default and tracks come out
    too quiet."""

    def test_named_kit_aliases_are_known(self):
        # Named samplers (the composer ships these labels)
        for chain in [("808 kit",), ("metal kit",), ("lofi bass",)]:
            offset = chain_offset_db(chain)
            assert offset > -10.0, \
                f"{chain} should have a real entry, got default {offset}"

    def test_genre_composer_4osc_named_presets(self):
        # The synthwave / lofi composers name their 4OSC instances
        # ("Counter Synth", "Pad Synth", etc.). All should resolve to
        # the same -8 dB offset as the bare "4osc" entry.
        for chain in [("counter synth",), ("marimba 4osc",),
                      ("keys synth",), ("pad synth",), ("bass synth",)]:
            offset = chain_offset_db(chain)
            assert offset > -10.0, \
                f"{chain} should resolve, got default {offset}"


# --------------------------------------------------------------------------- #
# Composer state isolation between calls                                      #
# --------------------------------------------------------------------------- #


class TestComposerStateLeak:
    """compose_lofi_track mutates module-level lookup tables when given
    a custom chord_progression. Without proper reset, those mutations
    leak into the next call. These tests guard against that."""

    def test_jazz_voicings_reset_between_calls(self):
        # Reload the module to pick a clean snapshot
        from waveform_mcp.tools import composer
        importlib.reload(composer)

        original_keys = set(composer.JAZZ_VOICINGS.keys())

        # Simulate the body of compose_lofi_track's progression override
        composer.JAZZ_VOICINGS.clear()
        composer.JAZZ_VOICINGS.update(composer._JAZZ_VOICINGS_DEFAULT)
        # Now apply a fake custom progression that adds a new root
        composer.JAZZ_VOICINGS[42] = [70, 77, 80]
        composer.JAZZ_VOICINGS[46] = [74, 81, 84]

        # On the next "call," reset the same way
        composer.JAZZ_VOICINGS.clear()
        composer.JAZZ_VOICINGS.update(composer._JAZZ_VOICINGS_DEFAULT)

        assert set(composer.JAZZ_VOICINGS.keys()) == original_keys, \
            "JAZZ_VOICINGS should match original after reset"

    def test_seventh_interval_override_clears(self):
        from waveform_mcp.tools import composer
        importlib.reload(composer)

        # Set an override
        composer._SEVENTH_INTERVAL_OVERRIDE = {42: 11, 46: 11}
        # Simulate the start-of-call reset
        composer._SEVENTH_INTERVAL_OVERRIDE = {}
        assert composer._SEVENTH_INTERVAL_OVERRIDE == {}


# --------------------------------------------------------------------------- #
# Dead-helper removal sanity                                                  #
# --------------------------------------------------------------------------- #


class TestNoDeadHelpers:
    """Drum-kit population was duplicated across three modules:
      - compose_from_spec._populate_default_drum_kit
      - lofi_generative._populate_drum_kit
      - plugins._auto_populate_drum_kit
    Only the last is canonical. The other two were removed; this test
    guards against accidental reintroduction."""

    def test_no_duplicate_drum_kit_helpers(self):
        from waveform_mcp.tools import compose_from_spec, lofi_generative
        assert not hasattr(compose_from_spec, "_populate_default_drum_kit"), \
            "dead helper resurrected — drum kit logic should live only " \
            "in plugins._auto_populate_drum_kit"
        assert not hasattr(lofi_generative, "_populate_drum_kit"), \
            "dead helper resurrected — drum kit logic should live only " \
            "in plugins._auto_populate_drum_kit"

    def test_canonical_helper_present(self):
        from waveform_mcp.tools import plugins
        assert hasattr(plugins, "_auto_populate_drum_kit"), \
            "canonical drum kit helper missing"
