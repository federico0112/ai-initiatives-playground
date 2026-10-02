import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROTO = HERE.parent
sys.path.insert(0, str(PROTO))  # makes `contracts` importable


@pytest.fixture(scope="session")
def sim_data(tmp_path_factory):
    """All scenarios generated once at 10% volume."""
    out = tmp_path_factory.mktemp("ops") / "sim-data"
    gen = subprocess.run([sys.executable, str(PROTO / "dataset/generate_dataset.py"), "--scale", "0.1", "--out", str(out)],
                         capture_output=True, text=True)
    assert gen.returncode == 0, gen.stderr
    return out
