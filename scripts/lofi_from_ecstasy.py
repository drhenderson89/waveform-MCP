"""Generative lofi track inspired by Ecstasy's harmonic palette.

Uses compose_lofi_generative (NOT the old hardcoded compose_lofi_track):
chord progression, section arrangement, drum pattern selection, melody
contour, bass feel, and tempo are all randomized per seed. Different
seeds produce meaningfully different songs.

We pass an explicit Ecstasy-inspired chord progression so the harmonic
flavor lifts from the source — but the rest (drums, melody, bass,
arrangement, tempo) varies freely.
"""
import sys
sys.path.insert(0, "src")

from waveform_mcp.tools.lofi_generative import compose_lofi_generative
from waveform_mcp.tools.waveform_app import waveform_launch

OUT = r"C:\Users\jarms\OneDrive\Documents\MCP-Validate\Ecstasy-inspired-lofi.tracktionedit"

# Ecstasy's most-used progression: Fmaj7 → F#maj7 → A#maj7 → Cmaj7
ECSTASY_PROGRESSION = [
    {"bass_root": 41, "voicing": [69, 76, 79], "seventh_interval": 11},  # Fmaj7
    {"bass_root": 42, "voicing": [70, 77, 80], "seventh_interval": 11},  # F#maj7
    {"bass_root": 46, "voicing": [74, 81, 84], "seventh_interval": 11},  # A#maj7
    {"bass_root": 36, "voicing": [64, 71, 74], "seventh_interval": 11},  # Cmaj7
]

result = compose_lofi_generative({
    "out_path": OUT,
    "seed": 23,                          # change for different songs
    "key_root": "F", "key_mode": "major",
    "chord_progression": ECSTASY_PROGRESSION,
})
print("ok=", result.get("ok"))
print("seed=", result.get("seed"), "tempo=", result.get("tempo"))
print("bars=", result.get("bars"), "sections=", result.get("sections"))
print("melody_contour=", result.get("melody_contour"),
      "bass_feel=", result.get("bass_feel"),
      "energy_curve=", result.get("energy_curve"))
print()
print("opening...")
print(waveform_launch({"file_path": OUT}))
