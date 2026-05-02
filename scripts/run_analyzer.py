"""Drive the music-analyzer pipeline non-interactively on a rendered WAV.
Outputs a JSON summary of mix balance: per-stem RMS, peak, spectral bands,
collision pairs, masking issues."""
import os, sys, json, subprocess
from pathlib import Path

ANALYZER_DIR = r"C:\Users\jarms\repos\music-analyzer"
PYEXE = r"C:\Users\jarms\repos\music-analyzer\venv\Scripts\python.exe"

WAV = sys.argv[1] if len(sys.argv) > 1 else \
    r"C:\Users\jarms\OneDrive\Documents\MCP-Validate\Exported\MCP-Validate Edit 1 Export 1.wav"

# Run a small driver in the analyzer's venv that imports the modules directly
driver = r'''
import os, sys, json
sys.path.insert(0, r"C:\Users\jarms\repos\music-analyzer")
import numpy as np, librosa
from analyzers.stems import separate_stems

WAV = r"{wav}"
print("[1/4] Separating stems via demucs...", flush=True)
stems = separate_stems(WAV)
if not stems:
    print(json.dumps({{"ok": False, "error": "stem separation failed"}}))
    sys.exit(1)
print(f"  stems: {{list(stems.keys())}}", flush=True)

print("[2/4] Loading + measuring stems...", flush=True)
SR = 22050
def db(x): return 20*np.log10(max(x, 1e-10))

def measure(path):
    y, _ = librosa.load(path, sr=SR, mono=True)
    rms_full = float(np.sqrt(np.mean(y**2)))
    peak    = float(np.max(np.abs(y)))
    # 7-band spectrum
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)
    bands = {{
        "sub":(20,60),"bass":(60,250),"low_mid":(250,500),
        "mid":(500,2000),"upper_mid":(2000,4000),
        "presence":(4000,8000),"air":(8000,11000),
    }}
    out = {{}}
    for name,(lo,hi) in bands.items():
        m = (freqs>=lo)&(freqs<hi)
        out[name] = round(db(float(np.mean(S[m,:]))), 1) if m.any() else -99
    return {{"rms_db": round(db(rms_full),1), "peak_db": round(db(peak),1), "bands_db": out}}

result = {{"file": WAV, "stems": {{}}}}
for name, p in stems.items():
    result["stems"][name] = measure(p)
# Also full mix
print("[3/4] Measuring full mix...", flush=True)
result["full"] = measure(WAV)

print("[4/4] Done.", flush=True)
print("---JSON---")
print(json.dumps(result, indent=2))
'''.format(wav=WAV.replace("\\","\\\\"))

env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
proc = subprocess.run([PYEXE, "-c", driver], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", env=env)
# Use sys.stdout.buffer.write for binary-safe output (Windows stdout is cp1252)
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print(proc.stdout)
if proc.returncode != 0:
    print("STDERR:", proc.stderr)
