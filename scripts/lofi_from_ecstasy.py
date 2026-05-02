"""Generate a lofi track using the proper compose_lofi_track tool.

Calls the real MCP composer (350-line implementation with section-aware
drum density, real factory bass samples, jazz piano voicings, melody
with motif development, vinyl crackle on master). Result is dramatically
more musical than hand-rolled chord-tone quarter-note drones.
"""
import sys
sys.path.insert(0, "src")

from waveform_mcp.tools.composer import compose_lofi_track
from waveform_mcp.tools.waveform_app import waveform_launch

OUT = r"C:\Users\jarms\OneDrive\Documents\MCP-Validate\Ecstasy-inspired-lofi.tracktionedit"

# 76 BPM is compose_lofi_track's default — Ecstasy's vibe. Seeds 1..N
# vary the melody motif & bass groove so each compose is unique.
result = compose_lofi_track({
    "out_path": OUT,
    "tempo": 76.0,
    "seed": 7,            # try different seeds (1, 7, 42, ...) for variation
})
print("ok=", result.get("ok"))
print("bars=", result.get("bars"))
print("tracks=", result.get("tracks"))

print()
print("opening in Waveform...")
print(waveform_launch({"file_path": OUT}))
