"""Polite scraper for alltime-athletics.com.

Fetches one event page at a time with a 1-second sleep between requests and
an identifying User-Agent. Optionally caches HTML on disk; in CI we run with
no cache so we always see the freshest data.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from alltimeathletics.events import Event

USER_AGENT = (
    "alltimeathletics-mirror "
    "(+https://github.com/thomascamminady/alltimeathletics; thomas@camminady.dev)"
)
REQUEST_GAP_SECONDS = 1.0
TIMEOUT_SECONDS = 30.0


def fetch(event: Event, *, cache_dir: Path | None = None, client: httpx.Client | None = None) -> str:
    """Return the HTML for `event`, hitting the network unless a cached copy exists.

    Cache is plain on-disk by slug — no ETag negotiation, simple is enough for
    a weekly cron. CI runs with `cache_dir=None`.
    """
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{event.slug}.htm"
        if cached.exists():
            return cached.read_text(encoding="latin-1")

    own_client = client is None
    c = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    try:
        r = c.get(event.url)
        r.raise_for_status()
        # Larsson's pages are served as latin-1; httpx may guess wrong.
        html = r.content.decode("latin-1")
    finally:
        if own_client:
            c.close()

    if cache_dir is not None:
        (cache_dir / f"{event.slug}.htm").write_text(html, encoding="latin-1")

    return html


def fetch_all(events: list[Event], *, cache_dir: Path | None = None) -> dict[str, str]:
    """Fetch every event sequentially, sleeping between network hits.

    Returns slug -> raw HTML.
    """
    out: dict[str, str] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS) as client:
        for i, event in enumerate(events):
            from_cache = cache_dir is not None and (cache_dir / f"{event.slug}.htm").exists()
            out[event.slug] = fetch(event, cache_dir=cache_dir, client=client)
            if not from_cache and i < len(events) - 1:
                time.sleep(REQUEST_GAP_SECONDS)
    return out
