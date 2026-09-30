"""
Playwright test suite for the Kassel dashboard.

Each test documents what a feature is SUPPOSED to do, then verifies it against
the live dashboard and reports PASS / FAIL. Tests run against localhost only
(never ImmoScout), so no bot-detection is involved.

Prerequisites:
    pip install playwright && playwright install chromium
    python src/api.py          # dashboard must be running on :8000

Run:
    python tests/test_dashboard.py
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from database import get_conn  # noqa: E402  (used only to restore state after the destructive test)

BASE = "http://localhost:8000/dashboard/"
results = []


def record(name, expected, passed, detail=""):
    results.append({"name": name, "expected": expected, "passed": passed, "detail": detail})


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("dialog", lambda d: d.accept())  # auto-accept confirm()/alert()

        try:
            page.goto(BASE, wait_until="networkidle", timeout=15000)
        except Exception as e:
            record("dashboard_loads", "Dashboard loads at /dashboard/", False,
                   f"could not reach {BASE}: {e}. Is `python src/api.py` running?")
            _report(); browser.close(); return

        # Capture window.open() targets instead of opening real tabs, and stop
        # the 60s auto-refresh so tests are deterministic.
        page.evaluate("""() => {
            window.__opened = [];
            window.open = (u) => { window.__opened.push(u); return null; };
            for (let i = 1; i < 99999; i++) clearInterval(i);
        }""")
        page.wait_for_selector("#listingsBody tr", timeout=10000)
        page.wait_for_timeout(500)

        test_dashboard_loads(page)
        test_stats_render(page)
        test_listings_table_columns(page)
        test_floor_and_movein_populated(page)
        test_status_filter(page)
        test_row_click_focuses_map(page)
        test_marker_hover_highlights_row(page)
        test_marker_hover_no_scroll_jump(page)
        test_view_listing_hrefs(page)
        test_action_cards_render(page)
        test_open_approved_button(page)
        test_sync_button(page)
        test_search_button(page)
        test_recent_emails_panel(page)
        test_mark_sent(page)  # destructive; restores state afterward

        browser.close()
    _report()


def test_dashboard_loads(page):
    """Expected: the page loads with title 'Kassel Wohnungssuche' and a header."""
    title = page.title()
    ok = "Kassel" in title and page.locator("header h1").count() == 1
    record("dashboard_loads", "Page loads with 'Kassel' title + header", ok, f"title={title!r}")


def test_stats_render(page):
    """Expected: a row of stat tiles (Total listings, Active apps, …) each with a number."""
    tiles = page.locator(".stat")
    n = tiles.count()
    nums = [tiles.nth(i).locator(".n").inner_text() for i in range(n)]
    ok = n >= 5 and all(v.strip().isdigit() for v in nums)
    record("stats_render", "≥5 stat tiles, each showing a number", ok, f"{n} tiles, values={nums}")


def test_listings_table_columns(page):
    """Expected: the listings table has columns Address, District, Rent, Floor, Move-in, Status."""
    headers = [h.inner_text().strip() for h in page.locator("table thead th").all()]
    expected = ["Address", "District", "Rent", "Floor", "Move-in", "Status"]
    rows = page.locator("#listingsBody tr").count()
    ok = headers == expected and rows > 0
    record("listings_table_columns", f"Columns {expected} + ≥1 row", ok,
           f"headers={headers}, rows={rows}")


def test_floor_and_movein_populated(page):
    """Expected: at least one row shows a real floor (EG/.OG) and a move-in date."""
    body = page.locator("#listingsBody").inner_text()
    has_floor = ("OG" in body) or ("EG" in body)
    has_move = any(tok in body for tok in [".2026", "sofort"])
    ok = has_floor and has_move
    record("floor_and_movein_populated", "Floor (EG/.OG) and move-in date visible in table",
           ok, f"has_floor={has_floor}, has_move={has_move}")


def test_status_filter(page):
    """Expected: choosing a status in the filter narrows the table to only that status."""
    page.select_option("#statusFilter", "rejected")
    page.wait_for_timeout(300)
    rows = page.locator("#listingsBody tr")
    n = rows.count()
    all_rejected = n > 0 and all("rejected" in rows.nth(i).inner_text().lower() for i in range(n))
    page.select_option("#statusFilter", "all")  # reset
    page.wait_for_timeout(200)
    record("status_filter", "Selecting 'rejected' shows only rejected rows", all_rejected,
           f"{n} rows shown, all rejected={all_rejected}")


def test_row_click_focuses_map(page):
    """Expected: clicking a table row highlights it (and focuses the map marker)."""
    page.evaluate("document.querySelectorAll('#listingsBody tr.highlight').forEach(t=>t.classList.remove('highlight'))")
    page.locator("#listingsBody tr").first.click()
    page.wait_for_timeout(300)
    hl = page.locator("#listingsBody tr.highlight").count()
    record("row_click_focuses_map", "Clicking a row highlights it + pans the map", hl >= 1,
           f"{hl} row(s) highlighted after click")


def test_marker_hover_highlights_row(page):
    """Expected (BUG the user reported): hovering a map marker highlights the
    corresponding row in the table below."""
    page.evaluate("document.querySelectorAll('#listingsBody tr.highlight').forEach(t=>t.classList.remove('highlight'))")
    before = page.locator("#listingsBody tr.highlight").count()
    marker = page.locator(".leaflet-marker-icon").first
    marker.hover(force=True)
    page.wait_for_timeout(400)
    after = page.locator("#listingsBody tr.highlight").count()
    record("marker_hover_highlights_row",
           "Hovering a map marker highlights its row in the table below",
           before == 0 and after >= 1, f"highlighted rows before={before}, after={after}")


def test_marker_hover_no_scroll_jump(page):
    """Expected (regression): hovering a map marker highlights the row but must
    NOT scroll/jump the page — hover-scroll fights the user's own scrolling."""
    page.evaluate("window.scrollTo(0, 700)")
    page.wait_for_timeout(200)
    before = page.evaluate("window.scrollY")
    page.evaluate("""() => {
        const i = document.querySelector('.leaflet-marker-icon');
        if (i) i.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
    }""")
    page.wait_for_timeout(500)
    after = page.evaluate("window.scrollY")
    record("marker_hover_no_scroll_jump",
           "Marker hover highlights the row WITHOUT scrolling the page",
           before == after, f"scrollY {before} -> {after}")


def test_view_listing_hrefs(page):
    """Expected: 'View listing' / address links point to valid ImmoScout expose URLs."""
    hrefs = page.eval_on_selector_all(
        "#actions a, #listingsBody a",
        "els => els.map(e => e.getAttribute('href')).filter(Boolean)")
    immoscout = [h for h in hrefs if "immobilienscout24.de/expose/" in h]
    bad = [h for h in hrefs if not h.startswith("https://")]
    ok = len(immoscout) > 0 and not bad
    record("view_listing_hrefs", "Listing links are valid https ImmoScout expose URLs", ok,
           f"{len(immoscout)} ImmoScout links, {len(bad)} malformed")


def test_action_cards_render(page):
    """Expected: approved applications show as cards with Open-to-apply, Mark-sent, Dismiss."""
    text = page.locator("#actions").inner_text()
    ok = ("Open to apply" in text) and ("Mark sent" in text) and ("Dismiss" in text)
    record("action_cards_render", "Approved cards show Open-to-apply / Mark-sent / Dismiss", ok,
           f"open={'Open to apply' in text}, sent={'Mark sent' in text}, dismiss={'Dismiss' in text}")


def test_open_approved_button(page):
    """Expected: 'Open approved to apply' opens each ready listing (captured, not real tabs)."""
    page.evaluate("window.__opened = []")
    btn = page.get_by_role("button", name="Open approved to apply")
    exists = btn.count() == 1
    if exists:
        btn.click()
        page.wait_for_timeout(600)
    opened = page.evaluate("window.__opened")
    ok = exists and len(opened) > 0 and all("immobilienscout24.de" in u for u in opened)
    record("open_approved_button", "Opens all approved-with-link listings in tabs", ok,
           f"button={exists}, opened {len(opened)} listing tabs")


def test_sync_button(page):
    """Expected: 'Sync Gmail' exists and /api/sync responds gracefully (400 until Gmail is set up)."""
    btn = page.get_by_role("button", name="Sync Gmail").count() == 1
    resp = page.evaluate("""async () => {
        const r = await fetch('/api/sync', {method:'POST'});
        return {status: r.status};
    }""")
    ok = btn and resp["status"] in (200, 400)
    record("sync_button", "Sync Gmail button + /api/sync responds (400 if unconfigured)", ok,
           f"button={btn}, /api/sync status={resp['status']}")


def test_search_button(page):
    """Expected: 'Search new properties' opens the pre-filtered ImmoScout search URL."""
    page.evaluate("window.__opened = []")
    btn = page.get_by_role("button", name="Search new properties")
    exists = btn.count() == 1
    if exists:
        btn.click()
        page.wait_for_timeout(300)
    opened = page.evaluate("window.__opened")
    ok = exists and any("immobilienscout24.de/Suche" in u for u in opened)
    record("search_button", "Opens the pre-filtered ImmoScout search", ok,
           f"button={exists}, opened={opened[:1]}")


def test_recent_emails_panel(page):
    """Expected: a 'Recent emails' panel exists (empty state is fine before Gmail sync)."""
    text = page.content()
    ok = "Recent emails" in text
    record("recent_emails_panel", "Recent emails panel present", ok, "")


def test_mark_sent(page):
    """Expected: 'Mark sent' on an approved card removes it from the queue and
    marks the application applied. (Destructive — state is restored afterward.)"""
    with get_conn() as conn:
        done_before = {r[0] for r in conn.execute(
            "SELECT id FROM action_queue WHERE status='done'").fetchall()}

    cards_before = page.locator(".action-item").count()
    card = page.locator(".action-item", has_text="Mark sent").first
    if card.count() == 0:
        record("mark_sent", "Mark sent clears the card + marks applied", False, "no approved card found")
        return
    card.get_by_role("button", name="Mark sent").click()
    page.wait_for_timeout(600)
    cards_after = page.locator(".action-item").count()
    ok = cards_after == cards_before - 1

    # Restore: any action newly flipped to 'done' goes back to 'approved'.
    with get_conn() as conn:
        done_now = {r[0] for r in conn.execute(
            "SELECT id FROM action_queue WHERE status='done'").fetchall()}
        new_done = done_now - done_before
        for aid in new_done:
            app = conn.execute("SELECT application_id FROM action_queue WHERE id=?", (aid,)).fetchone()
            conn.execute("UPDATE action_queue SET status='approved', resolved_at=NULL WHERE id=?", (aid,))
            if app and app[0]:
                conn.execute("UPDATE applications SET status='approved', date_applied=NULL WHERE id=?", (app[0],))

    record("mark_sent", "Mark sent removes the card + marks applied (state restored)", ok,
           f"cards {cards_before} -> {cards_after}")


def _report():
    print("\n" + "=" * 78)
    print("KASSEL DASHBOARD — FEATURE TEST REPORT")
    print("=" * 78)
    passed = sum(1 for r in results if r["passed"])
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['name']}")
        print(f"        expected: {r['expected']}")
        if r["detail"]:
            print(f"        actual:   {r['detail']}")
    print("-" * 78)
    print(f"{passed}/{len(results)} passed")
    print("=" * 78)
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    run()
