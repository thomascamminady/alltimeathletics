"""Session-wide fixtures for the test suite.

The ``built_site`` fixture builds the static site into a tmp directory once
per pytest invocation. Both the structural tests (``test_site_build``) and
the browser smoke tests (``test_browser_smoke``) share it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PARQUET = REPO_ROOT / "data" / "alltime_athletics.parquet"


@pytest.fixture(scope="session")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not PARQUET.exists():
        pytest.skip("parquet absent — run `make scrape` first")
    out = tmp_path_factory.mktemp("site")
    result = subprocess.run(
        [sys.executable, "-m", "alltimeathletics.site", "--out", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"site build failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout[-2000:]}\n"
        f"stderr: {result.stderr[-2000:]}"
    )
    return out
