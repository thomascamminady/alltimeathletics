"""Smoke-test: ``uv run python -m alltimeathletics.site`` must succeed.

Skipped when the parquet is absent (same convention as the other test files).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PARQUET = REPO_ROOT / "data" / "alltime_athletics.parquet"


@pytest.fixture(scope="module")
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


def test_index_page_exists(built_site: Path) -> None:
    assert (built_site / "index.html").exists()


def test_athlete_index_exists(built_site: Path) -> None:
    assert (built_site / "athlete" / "index.html").exists()
    assert (built_site / "athlete" / "index.json").exists()


def test_event_pages_present(built_site: Path) -> None:
    event_pages = list((built_site / "event").glob("*.html"))
    assert len(event_pages) >= 150, f"only {len(event_pages)} event pages"


def test_analytics_pages_present(built_site: Path) -> None:
    analytics_pages = list((built_site / "analytics").glob("*.html"))
    assert len(analytics_pages) >= 150, f"only {len(analytics_pages)} analytics pages"


def test_athlete_pages_present(built_site: Path) -> None:
    athlete_pages = list((built_site / "athlete").glob("*.html"))
    # subtract index.html itself
    n = sum(1 for p in athlete_pages if p.name != "index.html")
    assert n >= 20_000, f"only {n} athlete pages — build regression?"


def test_parquet_copied(built_site: Path) -> None:
    assert (built_site / "data" / "alltime_athletics.parquet").exists()


def test_no_jinja_errors_in_event_page(built_site: Path) -> None:
    """Spot-check one event page for obvious Jinja rendering artefacts."""
    page = built_site / "event" / "m_100ok.html"
    assert page.exists()
    content = page.read_text()
    assert "Traceback" not in content
    assert "UndefinedError" not in content
    assert "TemplateSyntaxError" not in content
    assert "View source" in content
    assert "All events" in content


def test_no_jinja_errors_in_athlete_page(built_site: Path) -> None:
    """Spot-check Usain Bolt's page for obvious rendering artefacts."""
    page = built_site / "athlete" / "usain-bolt-jam-19860821.html"
    assert page.exists()
    content = page.read_text()
    assert "Traceback" not in content
    assert "Usain Bolt" in content
    assert "All athletes" in content
