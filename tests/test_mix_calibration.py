"""Tests for waveform_mcp.mix_calibration.

These are pure unit tests — no DAW, no rendering. Validates the lookup
table semantics, clamping, and calibration arithmetic so that the
composer can rely on it producing sane fader values for any plugin chain.
"""
from __future__ import annotations

import math
import pytest

from waveform_mcp import mix_calibration as mc


# --------------------------------------------------------------------------- #
# chain_offset_db                                                             #
# --------------------------------------------------------------------------- #


class TestChainOffset:
    def test_empty_chain_returns_unknown_default(self):
        assert mc.chain_offset_db([]) == mc.UNKNOWN_CHAIN_OFFSET_DB

    def test_none_entries_skipped(self):
        # Only chrome plugins → effectively empty
        assert mc.chain_offset_db([None, "volume", "level"]) == mc.UNKNOWN_CHAIN_OFFSET_DB

    def test_known_single_plugin_exact(self):
        # Sampler is the reference, offset 0
        assert mc.chain_offset_db(["sampler"]) == 0.0

    def test_case_insensitive(self):
        assert mc.chain_offset_db(["Sampler"]) == 0.0
        assert mc.chain_offset_db(["SAMPLER"]) == 0.0

    def test_whitespace_collapsed(self):
        assert mc.chain_offset_db(["  Sampler  "]) == 0.0
        # "TSC 1.1" should match regardless of multiple spaces
        full = ["sforzando", "anvil", "mt-a", "tsc 1.1"]
        with_spaces = ["  sforzando ", "anvil  ", "mt-a", "TSC  1.1"]
        assert mc.chain_offset_db(full) == mc.chain_offset_db(with_spaces)

    def test_chrome_plugins_ignored(self):
        # Adding volume/level/auxsend doesn't change the lookup
        a = mc.chain_offset_db(["sforzando", "anvil"])
        b = mc.chain_offset_db(["sforzando", "anvil", "volume", "level"])
        assert a == b

    def test_longest_prefix_match_wins(self):
        # Full guitar chain should match the 4-plugin entry, not just
        # ("sforzando",) which would give -16 dB
        full = mc.chain_offset_db(["sforzando", "anvil", "mt-a", "tsc 1.1"])
        sforz_only = mc.chain_offset_db(["sforzando"])
        assert full > sforz_only  # full chain is louder than bare sforzando
        assert full == 0.0

    def test_unknown_chain_uses_head_or_default(self):
        # ("sforzando", "weirdplugin") — no exact match, but head is sforzando
        # so we should get the head offset, not the unknown default
        offset = mc.chain_offset_db(["sforzando", "weirdplugin"])
        assert offset == mc.PLUGIN_CHAIN_OFFSETS_DB[("sforzando",)]

    def test_completely_unknown_chain_uses_default(self):
        offset = mc.chain_offset_db(["nonexistent_plugin_xyz"])
        assert offset == mc.UNKNOWN_CHAIN_OFFSET_DB

    def test_never_raises_on_garbage_input(self):
        # Robustness: any input shape returns a finite number, never raises
        for garbage in [[], [None], [""], ["", "", ""], [None, None], [123]]:  # type: ignore[list-item]
            try:
                v = mc.chain_offset_db(garbage)  # type: ignore[arg-type]
            except Exception as e:
                pytest.fail(f"chain_offset_db raised on {garbage!r}: {e}")
            assert math.isfinite(v)


# --------------------------------------------------------------------------- #
# calibrated_fader_db                                                         #
# --------------------------------------------------------------------------- #


class TestCalibratedFader:
    def test_target_minus_chain_offset(self):
        # target=-8, chain offset for sampler=0 → fader=-8
        assert mc.calibrated_fader_db(-8.0, ["sampler"]) == -8.0

    def test_quiet_chain_compensated_with_higher_fader(self):
        # target=-8, sforzando offset=-16 → fader=-8 - (-16) = +8
        # That's at the clamp ceiling.
        assert mc.calibrated_fader_db(-8.0, ["sforzando"]) == 8.0

    def test_relative_balance_matches_targets(self):
        """Two different chains targeting same dB should have audible
        equivalence. The fader values will differ, but their (target - chain)
        deltas should be equal."""
        target = -8.0
        f_drum  = mc.calibrated_fader_db(target, ["sampler"])
        f_guit  = mc.calibrated_fader_db(target, ["sforzando", "anvil", "mt-a", "tsc 1.1"])
        # Both chains have known offsets; both faders are within clamp range
        # so the difference should equal the chain-offset difference
        chain_drum  = mc.chain_offset_db(["sampler"])
        chain_guit  = mc.chain_offset_db(["sforzando", "anvil", "mt-a", "tsc 1.1"])
        assert math.isclose(f_drum  - f_guit, chain_guit - chain_drum, abs_tol=0.01)

    def test_extra_offset_added(self):
        base = mc.calibrated_fader_db(-8.0, ["sampler"])
        with_extra = mc.calibrated_fader_db(-8.0, ["sampler"], extra_offset_db=-3.0)
        assert math.isclose(with_extra, base - 3.0, abs_tol=0.01)

    def test_clamps_to_min(self):
        # Extreme negative target + very loud chain → would go below -32
        very_quiet = mc.calibrated_fader_db(-50.0, ["mt power drum kit"])
        assert very_quiet == mc.FADER_DB_MIN

    def test_clamps_to_max(self):
        # Quiet chain + high target → would go above +8
        very_loud = mc.calibrated_fader_db(0.0, ["sforzando"])  # target 0, offset -16 → +16
        assert very_loud == mc.FADER_DB_MAX

    def test_clamping_logged_at_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger=mc.logger.name):
            mc.calibrated_fader_db(0.0, ["sforzando"])  # would be +16, clamps to +8
        assert any("clamped" in rec.message for rec in caplog.records)

    def test_idempotent(self):
        # Same inputs → same output, repeatedly
        for _ in range(5):
            assert mc.calibrated_fader_db(-8.0, ["sampler"]) == -8.0


# --------------------------------------------------------------------------- #
# auto_calibrate_pass                                                         #
# --------------------------------------------------------------------------- #


class TestAutoCalibratePass:
    def test_converged_when_within_tolerance(self):
        targets = {"t1": -10.0}
        current = {"t1": -8.0}
        results = mc.auto_calibrate_pass(
            targets, current,
            measure_stem_rms_db=lambda tid: -10.5,  # 0.5 dB off, within ±2
        )
        assert results["t1"].converged is True
        assert results["t1"].fader_db_after == current["t1"]

    def test_corrects_when_too_quiet(self):
        targets = {"t1": -10.0}
        current = {"t1": -10.0}
        results = mc.auto_calibrate_pass(
            targets, current,
            measure_stem_rms_db=lambda tid: -16.0,  # 6 dB too quiet
        )
        assert results["t1"].converged is False
        # Should bump fader up by ~6 dB
        assert math.isclose(results["t1"].fader_db_after, -4.0, abs_tol=0.5)

    def test_step_limited(self):
        targets = {"t1": -10.0}
        current = {"t1": 0.0}
        # Measured at -50, error is -40 dB but step cap is 12
        results = mc.auto_calibrate_pass(
            targets, current,
            measure_stem_rms_db=lambda tid: -50.0,
            max_step_db=12.0,
        )
        delta = results["t1"].fader_db_after - results["t1"].fader_db_before
        assert delta <= 12.0 + 1e-6
        # And clamped to FADER_DB_MAX
        assert results["t1"].fader_db_after <= mc.FADER_DB_MAX + 1e-6

    def test_unmeasurable_track_left_alone(self):
        targets = {"t1": -10.0}
        current = {"t1": -8.0}
        results = mc.auto_calibrate_pass(
            targets, current,
            measure_stem_rms_db=lambda tid: None,
        )
        assert results["t1"].measured_db is None
        assert results["t1"].fader_db_after == current["t1"]
        assert results["t1"].converged is False

    def test_measure_callable_exception_does_not_crash(self):
        targets = {"t1": -10.0}
        current = {"t1": -8.0}

        def boom(tid):
            raise RuntimeError("synthetic failure")

        results = mc.auto_calibrate_pass(targets, current, measure_stem_rms_db=boom)
        # Treated like unmeasurable
        assert results["t1"].measured_db is None
        assert results["t1"].fader_db_after == current["t1"]


# --------------------------------------------------------------------------- #
# register_chain                                                              #
# --------------------------------------------------------------------------- #


class TestRegisterChain:
    def teardown_method(self):
        # Clean up any registered chains from this test
        PLUGIN_CHAIN_KEYS = {("test_xyz_plugin",)}
        for k in PLUGIN_CHAIN_KEYS:
            mc.PLUGIN_CHAIN_OFFSETS_DB.pop(k, None)

    def test_register_then_lookup(self):
        mc.register_chain(["test_xyz_plugin"], -7.0)
        assert mc.chain_offset_db(["test_xyz_plugin"]) == -7.0

    def test_register_overrides(self):
        mc.register_chain(["test_xyz_plugin"], -7.0)
        mc.register_chain(["test_xyz_plugin"], -3.0)
        assert mc.chain_offset_db(["test_xyz_plugin"]) == -3.0

    def test_empty_chain_rejected(self):
        with pytest.raises(ValueError):
            mc.register_chain([], 0.0)

    def test_extreme_offset_rejected(self):
        with pytest.raises(ValueError):
            mc.register_chain(["x"], -200.0)
        with pytest.raises(ValueError):
            mc.register_chain(["x"], +200.0)
