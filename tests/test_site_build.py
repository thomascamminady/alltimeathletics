"""Smoke-test: ``uv run python -m alltimeathletics.site`` must succeed.

The ``built_site`` fixture lives in ``conftest.py`` so the browser-smoke
tests can reuse the same build.
"""

from __future__ import annotations

from pathlib import Path


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
    assert 'class="primary-nav"' in content
    assert 'class="nav-tab nav-tab-active"' in content


def test_no_jinja_errors_in_athlete_page(built_site: Path) -> None:
    """Spot-check Usain Bolt's page for obvious rendering artefacts."""
    page = built_site / "athlete" / "usain-bolt-jam-19860821.html"
    assert page.exists()
    content = page.read_text()
    assert "Traceback" not in content
    assert "Usain Bolt" in content
    assert 'class="primary-nav"' in content
    assert 'class="nav-tab nav-tab-active"' in content
