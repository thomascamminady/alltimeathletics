"""Polite scraper for alltime-athletics.com.

Fetches one event page at a time with a 1-second sleep between requests and
an identifying User-Agent. Optionally caches HTML on disk; in CI we run with
no cache so we always see the freshest data.

Transient network errors and 5xx responses are retried with exponential
backoff so a single hiccup on any of ~190 pages doesn't fail the whole
weekly pipeline.

Address family: Larsson's host publishes both A and AAAA records, but
GitHub-hosted runners are usually IPv4-only. If ``getaddrinfo`` returns the
AAAA first, every attempt fails with ENETUNREACH before the retry loop can
help, so we bind the socket to ``0.0.0.0`` to keep it on IPv4.

That pin is itself a failure mode, though: on a host where the name resolves
to an IPv6 address, connecting an IPv4-bound socket raises EAFNOSUPPORT
("Address family for hostname not supported") and retrying never helps — it
took the weekly refresh down once. So we keep an unpinned client as a
fallback and rotate onto it when the pinned one hits a transport error.
"""

from __future__ import annotations

import random
import time
from collections.abc import Sequence
from pathlib import Path

import httpx

from alltimeathletics.events import Event

USER_AGENT = (
    "alltimeathletics-mirror "
    "(+https://github.com/thomascamminady/alltimeathletics; thomas@camminady.dev)"
)
REQUEST_GAP_SECONDS = 1.0
TIMEOUT_SECONDS = 30.0
# 6 attempts × exponential backoff (capped at MAX_RETRY_BACKOFF_SECONDS)
# tolerates ~2 min of intermittent 503s before giving up. Larsson's site
# tends to throw transient 5xx during peak hours; widening from 4 → 6
# attempts moved the per-page failure rate from "occasional" to "rare".
MAX_RETRIES = 6
RETRY_BACKOFF_SECONDS = 2.0
MAX_RETRY_BACKOFF_SECONDS = 60.0


def _client(*, local_address: str | None = None) -> httpx.Client:
    """Build an httpx client, optionally pinned to a local address."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
        transport=httpx.HTTPTransport(local_address=local_address),
    )


def _client_chain() -> list[httpx.Client]:
    """IPv4-pinned client first, unpinned fallback second (see module docstring)."""
    return [_client(local_address="0.0.0.0"), _client()]


def _fetch_with_retries(clients: Sequence[httpx.Client], url: str) -> bytes:
    """GET ``url`` with bounded retries on transient failures.

    Retried: connection/read/timeout errors and HTTP 5xx. 4xx fails fast.
    Backoff doubles each attempt (capped) with up to 25% jitter so multiple
    retries don't all rehit a struggling origin at the same instant.

    A transport error may mean this host can't use the address family the
    current client is pinned to, which no amount of retrying fixes — so we
    advance to the next client in the chain before backing off.
    """
    last_exc: Exception | None = None
    idx = 0
    for attempt in range(MAX_RETRIES):
        try:
            r = clients[idx].get(url)
            if r.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"server error {r.status_code}", request=r.request, response=r
                )
            r.raise_for_status()
            return r.content
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if isinstance(exc, httpx.TransportError):
                idx = min(idx + 1, len(clients) - 1)
            if attempt == MAX_RETRIES - 1:
                break
            backoff = min(RETRY_BACKOFF_SECONDS * (2**attempt), MAX_RETRY_BACKOFF_SECONDS)
            time.sleep(backoff * (1.0 + random.uniform(0.0, 0.25)))
    assert last_exc is not None
    raise last_exc


def fetch(
    event: Event,
    *,
    cache_dir: Path | None = None,
    clients: Sequence[httpx.Client] | None = None,
) -> str:
    """Return the HTML for `event`, hitting the network unless a cached copy exists.

    Cache is plain on-disk by slug — no ETag negotiation, simple is enough for
    a weekly cron. CI runs with `cache_dir=None`.
    """
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{event.slug}.htm"
        if cached.exists():
            return cached.read_text(encoding="latin-1")

    own_clients = clients is None
    chain = list(clients) if clients is not None else _client_chain()
    try:
        # Larsson's pages are served as latin-1; httpx may guess wrong.
        html = _fetch_with_retries(chain, event.url).decode("latin-1")
    finally:
        if own_clients:
            for c in chain:
                c.close()

    if cache_dir is not None:
        (cache_dir / f"{event.slug}.htm").write_text(html, encoding="latin-1")

    return html


def fetch_all(events: list[Event], *, cache_dir: Path | None = None) -> dict[str, str]:
    """Fetch every event sequentially, sleeping between network hits.

    Returns slug -> raw HTML.
    """
    out: dict[str, str] = {}
    chain = _client_chain()
    try:
        for i, event in enumerate(events):
            from_cache = cache_dir is not None and (cache_dir / f"{event.slug}.htm").exists()
            out[event.slug] = fetch(event, cache_dir=cache_dir, clients=chain)
            if not from_cache and i < len(events) - 1:
                time.sleep(REQUEST_GAP_SECONDS)
    finally:
        for c in chain:
            c.close()
    return out
