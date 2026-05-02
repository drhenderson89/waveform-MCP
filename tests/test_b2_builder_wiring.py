"""Tests for B2 wiring: reverb_rt60_s and sidechain_to_drums on TrackSpec
get translated into actual plate reverb / ducking comp plugins by the
builder."""
from __future__ import annotations

import os
import tempfile

from waveform_mcp.composition_spec import (
    CompositionSpec, NoteEvent, PluginChainSpec, SectionSpec, TrackSpec,
)
from waveform_mcp.model import STORE
from waveform_mcp.tools.compose_from_spec import compose_from_spec


def _tmp_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".tracktionedit")
    os.close(fd)
    os.remove(path)
    return path


def _spec_with(track: TrackSpec) -> CompositionSpec:
    return CompositionSpec(
        name="Test", tempo_bpm=120,
        sections=[SectionSpec(type="verse", start_beat=0, length_beats=16)],
        tracks=[track],
    )


class TestReverbWiring:
    def test_reverb_rt60_added_as_plate_reverb_plugin(self):
        track = TrackSpec(
            name="Lead", role="lead",
            chain=PluginChainSpec(plugins=["4osc"]),
            notes=[NoteEvent(0, 1, pitch=60)],
            reverb_rt60_s=1.5, reverb_wet=0.3,
        )
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": _spec_with(track), "out_path": out})
            assert r["ok"]
            t = next(x for x in STORE.edit.tracks if x.name == "Lead")
            plate = [p for p in t.plugins if p.type == "plateReverb"]
            assert len(plate) == 1
            params = plate[0].params
            # Decay should land in mid range for 1.5s RT60
            assert 0.3 < params["decay"] < 0.8
            assert params["mix"] <= 0.5
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_no_reverb_no_plate_added(self):
        track = TrackSpec(
            name="Drums", role="drums",
            chain=PluginChainSpec(plugins=["sampler"]),
            reverb_rt60_s=None,
        )
        out = _tmp_path()
        try:
            compose_from_spec({"spec": _spec_with(track), "out_path": out})
            t = next(x for x in STORE.edit.tracks if x.name == "Drums")
            assert not any(p.type == "plateReverb" for p in t.plugins)
        finally:
            if os.path.exists(out):
                os.remove(out)


class TestSidechainWiring:
    def test_sidechain_flag_adds_ducking_comp(self):
        track = TrackSpec(
            name="Pad", role="pad",
            chain=PluginChainSpec(plugins=["4osc"]),
            sidechain_to_drums=True,
        )
        out = _tmp_path()
        try:
            r = compose_from_spec({"spec": _spec_with(track), "out_path": out})
            assert r["ok"]
            t = next(x for x in STORE.edit.tracks if x.name == "Pad")
            pump_comps = [p for p in t.plugins
                          if p.type == "comp" and p.name == "Pump"]
            assert len(pump_comps) == 1
            params = pump_comps[0].params
            assert params["attack"] <= 5.0  # fast attack
            assert params["ratio"] >= 4.0   # squeezing ratio
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_no_sidechain_no_pump_comp(self):
        track = TrackSpec(
            name="Pad", role="pad",
            chain=PluginChainSpec(plugins=["4osc"]),
            sidechain_to_drums=False,
        )
        out = _tmp_path()
        try:
            compose_from_spec({"spec": _spec_with(track), "out_path": out})
            t = next(x for x in STORE.edit.tracks if x.name == "Pad")
            pumps = [p for p in t.plugins if p.name == "Pump"]
            assert pumps == []
        finally:
            if os.path.exists(out):
                os.remove(out)


class TestBoth:
    def test_reverb_and_sidechain_can_coexist(self):
        track = TrackSpec(
            name="Lead", role="lead",
            chain=PluginChainSpec(plugins=["4osc"]),
            reverb_rt60_s=1.0, reverb_wet=0.25,
            sidechain_to_drums=True,
        )
        out = _tmp_path()
        try:
            compose_from_spec({"spec": _spec_with(track), "out_path": out})
            t = next(x for x in STORE.edit.tracks if x.name == "Lead")
            assert any(p.type == "plateReverb" for p in t.plugins)
            assert any(p.name == "Pump" for p in t.plugins)
        finally:
            if os.path.exists(out):
                os.remove(out)
