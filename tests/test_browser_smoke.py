"""Browser smoke-test: render each page in headless Chromium.

Catches regressions a static-string check can't: JS errors, broken
LiteTable wiring, missing data files, console exceptions. Skipped
gracefully when Playwright (or its Chromium download) isn't available
so ``pytest`` keeps working on a fresh checkout.

To enable locally::

    uv add --dev playwright
    uv run playwright install chromium

CI may opt into this by installing the same.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site_server(built_site: Path) -> Iterator[str]:
    port = _free_port()
    handler = partial(SimpleHTTPRequestHandler, directory=str(built_site))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def browser():  # noqa: ANN201 — playwright type
    try:
        with sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except PlaywrightError as e:
                pytest.skip(f"Chromium not installed for Playwright: {e}")
            yield b
            b.close()
    except PlaywrightError as e:
        pytest.skip(f"Playwright failed to start: {e}")


PAGES = [
    pytest.param("/", False, id="home"),
    pytest.param("/event/mmaraok.html", True, id="event-marathon-men"),
    pytest.param("/event/m_100ok.html", True, id="event-100m-men"),
    pytest.param("/athlete/index.html", True, id="athlete-index"),
    pytest.param("/sql.html", False, id="sql"),
    pytest.param("/about.html", False, id="about"),
]


@pytest.mark.parametrize("path,expects_table", PAGES)
def test_page_renders(browser, site_server: str, path: str, expects_table: bool) -> None:  # noqa: ANN001
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on(
        "console",
        lambda m: m.type == "error" and errors.append(f"console.error: {m.text}"),
    )
    page.goto(site_server + path, wait_until="domcontentloaded", timeout=30_000)
    if expects_table:
        page.wait_for_selector(".lt-table tbody tr", timeout=15_000)
        rows = page.eval_on_selector_all(".lt-table tbody tr", "els => els.length")
        assert rows > 0, f"{path}: no rows rendered"
    page.close()
    assert not errors, f"{path}: console / page errors: {errors}"


def test_athlete_page_renders(browser, site_server: str, built_site: Path) -> None:  # noqa: ANN001
    """Pick the first athlete page and confirm it renders entries + chart."""
    candidates = [p for p in (built_site / "athlete").glob("*.html") if p.name != "index.html"]
    assert candidates, "no athlete pages built"
    target = candidates[0]
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on(
        "console",
        lambda m: m.type == "error" and errors.append(f"console.error: {m.text}"),
    )
    page.goto(f"{site_server}/athlete/{target.name}", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector(".lt-table tbody tr", timeout=15_000)
    rows = page.eval_on_selector_all(".lt-table tbody tr", "els => els.length")
    assert rows > 0
    page.close()
    assert not errors, f"{target.name}: console / page errors: {errors}"
