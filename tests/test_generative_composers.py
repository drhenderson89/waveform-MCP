"""Smoke tests for the generative composers.

Both compose_lofi_generative and compose_synthwave_generative drive
progression_generate / arrangement_plan / drum_pattern / melody_generate.
A live bug had progression_generate's output shape mis-handled — the
generators only worked when caller passed an explicit chord_progression.
These tests guard against that regression: they exercise the path where
no chord_progression is given, so progression_generate's output gets
parsed.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from waveform_mcp.tools.lofi_generative import compose_lofi_generative
from waveform_mcp.tools.synthwave_generative import compose_synthwave_generative


def _tmp_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".tracktionedit")
    os.close(fd)
    os.remove(path)
    return path


class TestLofiGenerative:
    def test_default_progression_path_works(self):
        # No chord_progression arg → composer must call progression_generate
        # and parse its output. This was broken; this test guards the fix.
        out = _tmp_path()
        try:
            r = compose_lofi_generative({"out_path": out, "seed": 1})
            assert r["ok"] is True, r
            assert os.path.exists(out)
            assert r["bars"] > 0
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_different_seeds_produce_different_outputs(self):
        out_a = _tmp_path()
        out_b = _tmp_path()
        try:
            ra = compose_lofi_generative({"out_path": out_a, "seed": 1})
            rb = compose_lofi_generative({"out_path": out_b, "seed": 99})
            assert ra["ok"] and rb["ok"]
            # Different seeds → different tempo OR key OR section count
            different = (ra["tempo"] != rb["tempo"]
                         or ra["key"] != rb["key"]
                         or ra["bars"] != rb["bars"]
                         or ra["sections"] != rb["sections"])
            assert different, "seeds produced identical metadata"
        finally:
            for p in (out_a, out_b):
                if os.path.exists(p):
                    os.remove(p)

    def test_user_chord_progression_override(self):
        # Caller passes a 2-chord progression — composer must honor it
        out = _tmp_path()
        try:
            r = compose_lofi_generative({
                "out_path": out, "seed": 1,
                "chord_progression": [
                    {"bass_root": 41, "voicing": [69, 76, 79], "seventh_interval": 11},
                    {"bass_root": 36, "voicing": [64, 71, 74], "seventh_interval": 11},
                ],
            })
            assert r["ok"]
        finally:
            if os.path.exists(out):
                os.remove(out)


class TestSynthwaveGenerative:
    def test_default_progression_path_works(self):
        out = _tmp_path()
        try:
            r = compose_synthwave_generative({"out_path": out, "seed": 1})
            assert r["ok"] is True, r
            assert os.path.exists(out)
            assert r["bars"] > 0
            assert "Drums" not in r["sections"]   # sections are roles
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_tempo_in_synthwave_range(self):
        out = _tmp_path()
        try:
            r = compose_synthwave_generative({"out_path": out, "seed": 7})
            # Default tempo range is [100, 128]
            assert 99 < r["tempo"] < 129
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_different_seeds_different_outputs(self):
        out_a, out_b = _tmp_path(), _tmp_path()
        try:
            ra = compose_synthwave_generative({"out_path": out_a, "seed": 1})
            rb = compose_synthwave_generative({"out_path": out_b, "seed": 99})
            assert ra["ok"] and rb["ok"]
            different = (ra["tempo"] != rb["tempo"]
                         or ra["key"] != rb["key"]
                         or ra["sections"] != rb["sections"]
                         or ra["chord_roots"] != rb["chord_roots"])
            assert different
        finally:
            for p in (out_a, out_b):
                if os.path.exists(p):
                    os.remove(p)

    def test_explicit_tempo_override(self):
        out = _tmp_path()
        try:
            r = compose_synthwave_generative({
                "out_path": out, "seed": 1, "tempo": 110.0,
            })
            assert abs(r["tempo"] - 110.0) < 0.01
        finally:
            if os.path.exists(out):
                os.remove(out)
