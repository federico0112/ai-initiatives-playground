"""The simulated data: every answer-key figure re-derives from the files, and generation is reproducible."""

import json
import subprocess
import sys

from pathlib import Path

PROTO = Path(__file__).resolve().parents[1]


def run(script, *args):
    return subprocess.run([sys.executable, str(PROTO / "dataset" / script), *args], capture_output=True, text=True)


def test_verify_passes(sim_data):
    ver = run("verify_dataset.py", "--root", str(sim_data))
    assert ver.returncode == 0, ver.stdout + ver.stderr
    for scn in ("base", "v1", "v2", "v3", "v4"):
        key = json.loads((sim_data / "answer-keys" / f"{scn}.json").read_text())
        assert key["alarm"]["observed_pct"] and key["truth"]["owner"] and key["recovery"]["verified_at"]


def test_generation_is_reproducible(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for out in (a, b):
        assert run("generate_dataset.py", "--scale", "0.05", "--scenarios", "base,v1", "--out", str(out)).returncode == 0
    files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    # Rendered PDFs embed a creation date; their source Markdown is compared instead.
    assert all((a / f).read_bytes() == (b / f).read_bytes() for f in files if f.suffix != ".pdf")
