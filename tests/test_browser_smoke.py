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


def _new_page(browser):  # noqa: ANN001, ANN202 — playwright types
    """A page with the third-party analytics request stubbed out.

    Every page loads GoatCounter from an external host. These tests run offline
    and assert that the console is clean, so we fulfil that request with an
    empty script — otherwise a failed third-party fetch would masquerade as a
    bug in our own JS.
    """
    page = browser.new_page()
    page.route(
        "**gc.zgo.at/**",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )
    return page


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
    page = _new_page(browser)
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
    page = _new_page(browser)
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


def test_event_filter_hides_empty_categories(browser, site_server: str) -> None:  # noqa: ANN001
    """Filtering must not leave a category subheading with nothing under it.

    The event list is flat — a heading ``<li>`` followed by its event ``<li>``s
    — so hiding only the events used to strand headings like "Sprints" above an
    empty gap when searching for "mile".
    """
    page = _new_page(browser)
    page.goto(site_server + "/", wait_until="domcontentloaded", timeout=30_000)

    # Every category is visible before any filtering.
    assert (
        page.eval_on_selector_all(
            ".event-list:not([hidden]) > li.event-category", "els => els.length"
        )
        > 0
    ), "no category headings rendered"

    page.fill("#filter", "mile")

    # Read back, per visible list, each visible heading and how many visible
    # event rows follow it before the next heading.
    stranded = page.evaluate("""() => {
        const bad = [];
        document.querySelectorAll(".event-list:not([hidden])").forEach((ul) => {
            let heading = null;
            let shown = 0;
            const settle = () => {
                if (heading && !heading.hidden && shown === 0) {
                    bad.push(heading.textContent.trim());
                }
            };
            ul.querySelectorAll(":scope > li").forEach((li) => {
                if (li.classList.contains("event-category")) {
                    settle();
                    heading = li;
                    shown = 0;
                    return;
                }
                if (!li.hidden && li.querySelector("a")) shown += 1;
            });
            settle();
        });
        return bad;
    }""")
    assert not stranded, f"category headings left with no matching events: {stranded}"

    # The filter still works: matching events remain, non-matching are gone.
    labels = page.eval_on_selector_all(
        ".event-list:not([hidden]) > li:not([hidden]) a",
        "els => els.map(e => e.dataset.label.toLowerCase())",
    )
    assert labels, "filter hid everything — expected the mile events to survive"
    assert all("mile" in x for x in labels), f"non-matching events still shown: {labels}"

    # Clearing the box restores every category.
    page.fill("#filter", "")
    restored = page.eval_on_selector_all(
        ".event-list:not([hidden]) > li.event-category:not([hidden])", "els => els.length"
    )
    assert restored > 0, "categories not restored after clearing the filter"
    page.close()
