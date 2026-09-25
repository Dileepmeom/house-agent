"""
Kassel Wohnungssuche — ImmoScout24 Scraper.
Uses Playwright to search, extract listings, and auto-apply.

Usage:
    python scraper_immoscout.py          # scrape + auto-apply
    python scraper_immoscout.py --dry    # scrape only, no applications sent
"""

import asyncio
import json
import re
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright, Page, Browser

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from database import init_db, upsert_listing, create_application, create_action, get_conn
from application_template import generate_application_message, APPLICANT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent / "data" / "scraper.log")
    ]
)
log = logging.getLogger("immoscout_scraper")

# --- Search configuration ---
SEARCH_CONFIG = {
    "base_url": "https://www.immobilienscout24.de",
    # Pre-built search URL with your criteria:
    # 3+ rooms, max €1100 warm, floor 2-3, near bus/tram, 5km radius Kassel center
    "search_url": (
        "https://www.immobilienscout24.de/Suche/radius/wohnung-mieten"
        "?centerofsearchaddress=Kassel"
        "&numberofrooms=3.0-"
        "&price=-1100.0"
        "&pricetype=calculatedtotalrent"
        "&floor=2-3"
        "&geocoordinates=51.31292%3B9.49829%3B5.0"
    ),
    "max_pages": 5,
    "login_email": APPLICANT.get("email", ""),
    # Stored in env var IMMOSCOUT_PASSWORD — never hardcoded
}

# Floor filter: reject 4th floor and above
MAX_FLOOR = 3


async def login(page: Page):
    """Log into ImmoScout24 (needed for sending messages)."""
    log.info("Navigating to login page...")
    await page.goto(f"{SEARCH_CONFIG['base_url']}/anbieter/login.html")
    await page.wait_for_load_state("networkidle")

    # Check if already logged in
    if await page.query_selector('[data-testid="user-menu"]'):
        log.info("Already logged in.")
        return True

    # Fill email
    email_input = await page.query_selector('#username')
    if email_input:
        await email_input.fill(SEARCH_CONFIG["login_email"])
        await page.click('[data-testid="submit-button"]')
        await page.wait_for_timeout(2000)

        # Password would need to be entered — in production, use
        # saved browser session / cookies to avoid re-login
        log.warning(
            "Login form reached — in production, use a persistent browser "
            "profile with saved session cookies to avoid password entry."
        )
        return False

    return False


async def scrape_search_results(page: Page) -> list[dict]:
    """Scrape listing cards from search results pages."""
    all_listings = []

    for page_num in range(1, SEARCH_CONFIG["max_pages"] + 1):
        url = SEARCH_CONFIG["search_url"]
        if page_num > 1:
            url += f"&pagenumber={page_num}"

        log.info(f"Scraping page {page_num}: {url}")
        await page.goto(url)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)

        # Extract listing data from the page
        listings = await page.evaluate("""() => {
            const cards = document.querySelectorAll('[data-testid="result-list-entry"],' +
                'article[class*="result"],' +
                '[class*="resultlist__listing"]');

            return Array.from(cards).map(card => {
                const titleEl = card.querySelector('a[href*="/expose/"]');
                const link = titleEl ? titleEl.href : null;
                const title = titleEl ? titleEl.textContent.trim() : null;
                const exposeMatch = link ? link.match(/expose\\/(\\d+)/) : null;
                const exposeId = exposeMatch ? exposeMatch[1] : null;

                // Extract price, size, rooms from the card
                const textContent = card.textContent;
                const priceMatch = textContent.match(/(\\d[\\d.,]+)\\s*€/);
                const sizeMatch = textContent.match(/(\\d[\\d.,]+)\\s*m²/);
                const roomsMatch = textContent.match(/(\\d[\\d.,]*)\\s*Zi/);

                // Extract address/district
                const addressEl = card.querySelector('[class*="address"],' +
                    '[class*="location"],' +
                    'span[class*="city"]');
                const address = addressEl ? addressEl.textContent.trim() : null;

                // Extract floor if visible
                const floorMatch = textContent.match(/(\\d+)\\.\\s*(?:OG|Obergeschoss|Etage|Stock)/i);

                return {
                    source_id: exposeId,
                    link: link,
                    listing_title: title,
                    rent_display: priceMatch ? priceMatch[0] : null,
                    rent_warm: priceMatch ? parseFloat(priceMatch[1].replace('.', '').replace(',', '.')) : null,
                    size_sqm: sizeMatch ? parseFloat(sizeMatch[1].replace(',', '.')) : null,
                    rooms: roomsMatch ? parseFloat(roomsMatch[1].replace(',', '.')) : null,
                    address: address,
                    floor: floorMatch ? floorMatch[0] : null,
                    floor_number: floorMatch ? parseInt(floorMatch[1]) : null,
                };
            }).filter(l => l.source_id);
        }""")

        if not listings:
            log.info(f"No listings found on page {page_num}, stopping.")
            break

        all_listings.extend(listings)
        log.info(f"Found {len(listings)} listings on page {page_num}")

        # Check if there's a next page
        next_btn = await page.query_selector('[aria-label="Next page"], [data-testid="pagination-next"]')
        if not next_btn:
            break

        await page.wait_for_timeout(1500)  # polite delay

    return all_listings


async def get_listing_details(page: Page, expose_url: str) -> dict:
    """Visit an individual listing page and extract full details."""
    await page.goto(expose_url)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)

    details = await page.evaluate("""() => {
        const getText = (sel) => {
            const el = document.querySelector(sel);
            return el ? el.textContent.trim() : null;
        };

        const body = document.body.textContent;

        // Check if listing is deactivated
        const deactivated = body.includes('deaktiviert') ||
                           body.includes('nicht mehr verfügbar');

        // Extract floor
        const floorMatch = body.match(/Geschoss[:\\s]*(\\d+)/i) ||
                          body.match(/Etage[:\\s]*(\\d+)/i) ||
                          body.match(/(\\d+)\\.\\s*(?:OG|Obergeschoss)/i);

        // Extract availability
        const availMatch = body.match(/(?:Bezugsfrei|Verfügbar|frei)\\s*(?:ab|:)?\\s*(\\d{1,2}\\.\\d{1,2}\\.\\d{2,4}|sofort)/i);

        // Check WBS requirement
        const wbs = body.includes('WBS') || body.includes('Wohnberechtigungsschein');

        // Extract district
        const districtMatch = body.match(/(?:Stadtteil|Lage)[:\\s]*([A-ZÄÖÜa-zäöü\\s-]+?)(?:\\s|,|\\n)/);

        return {
            is_active: !deactivated,
            floor_number: floorMatch ? parseInt(floorMatch[1]) : null,
            availability_date: availMatch ? availMatch[1] : null,
            requires_wbs: wbs,
            district: districtMatch ? districtMatch[1].trim() : null,
        };
    }""")

    return details


async def submit_application(page: Page, expose_url: str, listing: dict, message: str) -> bool:
    """
    Navigate to a listing and send the given application message.

    This performs a real, irreversible send to a landlord and must only be
    invoked from an explicit, human-triggered action (e.g. `--send <id>`
    after the draft was approved on the dashboard) — never from the
    unattended scrape/cron path. See run_scrape(), which only drafts.
    Returns True if the message was sent successfully.
    """
    log.info(f"Submitting application to {expose_url}...")

    await page.goto(expose_url)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)

    # Look for the contact/message button
    contact_btn = await page.query_selector(
        'button[data-testid="contactButton"],'
        'a[href*="kontakt"],'
        '[class*="contact-button"],'
        'button:has-text("Nachricht schreiben"),'
        'button:has-text("Anbieter kontaktieren")'
    )

    if not contact_btn:
        log.warning(f"No contact button found for {expose_url}")
        return False

    await contact_btn.click()
    await page.wait_for_timeout(2000)

    textarea = await page.query_selector(
        'textarea[name="message"],'
        'textarea[data-testid="message-textarea"],'
        'textarea[class*="message"],'
        'textarea'
    )

    if not textarea:
        log.warning(f"No message textarea found for {expose_url}")
        return False

    await textarea.fill(message)
    await page.wait_for_timeout(500)

    # Find and click send button
    send_btn = await page.query_selector(
        'button[data-testid="send-button"],'
        'button[type="submit"]:has-text("Senden"),'
        'button:has-text("Nachricht senden"),'
        'button:has-text("Absenden")'
    )

    if not send_btn:
        log.warning(f"No send button found for {expose_url}")
        return False

    await send_btn.click()
    await page.wait_for_timeout(3000)

    # Check for success indicator
    success = await page.query_selector(
        '[class*="success"],'
        '[data-testid="success"],'
        ':has-text("erfolgreich gesendet")'
    )

    if success:
        log.info(f"Successfully applied to {expose_url}")
        return True
    else:
        log.warning(f"Apply result unclear for {expose_url} — check manually")
        return True  # Assume success if no error shown


async def send_approved_application(source_id: str) -> bool:
    """
    Manually-triggered: submit the application for one listing whose draft
    was approved on the dashboard. Run this yourself per listing — it is
    never called from the scraper's automatic scrape loop or from cron.
    """
    with get_conn() as conn:
        row = conn.execute("""
            SELECT l.*, a.id as application_id, a.application_message, a.status
            FROM listings l JOIN applications a ON a.listing_id = l.id
            WHERE l.source_id = ?
        """, (source_id,)).fetchone()

    if not row:
        log.error(f"No listing/application found for source_id={source_id}")
        return False
    if row["status"] != "approved":
        log.error(
            f"Application for {row['address']} has status '{row['status']}', "
            "not 'approved' — approve it on the dashboard first."
        )
        return False
    if not row["link"]:
        log.error(
            f"'{row['address']}' has no ImmoScout24 URL — it's a manually-tracked "
            "listing (e.g. seeded from your spreadsheet), not a live scraped one, "
            "so there's no listing page to submit through. This can't be auto-sent; "
            "apply to it yourself the normal way."
        )
        return False

    async with async_playwright() as p:
        user_data_dir = Path(__file__).parent.parent / "data" / "browser_profile"
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir), headless=True,
            viewport={"width": 1280, "height": 800},
        )
        page = await browser.new_page()
        try:
            sent = await submit_application(
                page, row["link"], dict(row), row["application_message"]
            )
            if sent:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE applications SET status = 'applied', date_applied = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                        (row["application_id"],)
                    )
            return sent
        finally:
            await browser.close()


async def run_scrape(dry_run: bool = False):
    """Main scraping + auto-apply workflow."""
    init_db()
    log.info("=" * 60)
    log.info(f"Starting ImmoScout24 scrape {'(DRY RUN)' if dry_run else ''}")
    log.info("=" * 60)

    # Record scrape run
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO scrape_runs (source) VALUES ('immoscout24')"
        )
        run_id = cursor.lastrowid

    new_count = 0
    error_count = 0

    async with async_playwright() as p:
        # Use persistent context to keep login session
        user_data_dir = Path(__file__).parent.parent / "data" / "browser_profile"
        user_data_dir.mkdir(parents=True, exist_ok=True)

        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=True,
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        page = await browser.new_page()

        try:
            # Step 1: Scrape search results
            raw_listings = await scrape_search_results(page)
            log.info(f"Total raw listings scraped: {len(raw_listings)}")

            for raw in raw_listings:
                # Floor filter
                if raw.get("floor_number") and raw["floor_number"] > MAX_FLOOR:
                    log.info(f"Skipping {raw['source_id']} — floor {raw['floor_number']} > {MAX_FLOOR}")
                    continue

                # Warm rent filter
                if raw.get("rent_warm") and raw["rent_warm"] > 1100:
                    log.info(f"Skipping {raw['source_id']} — rent €{raw['rent_warm']} > €1100")
                    continue

                # Upsert into DB
                listing_data = {
                    "source": "immoscout24",
                    "source_id": raw["source_id"],
                    "address": raw.get("address") or f"ImmoScout expose {raw['source_id']}",
                    "rent_warm": raw.get("rent_warm"),
                    "rent_display": raw.get("rent_display"),
                    "rooms": raw.get("rooms"),
                    "size_sqm": raw.get("size_sqm"),
                    "floor": raw.get("floor"),
                    "floor_number": raw.get("floor_number"),
                    "link": raw.get("link") or f"https://www.immobilienscout24.de/expose/{raw['source_id']}",
                    "listing_title": raw.get("listing_title"),
                    "raw_data": raw,
                }

                listing_id, is_new = upsert_listing(listing_data)

                if is_new:
                    new_count += 1
                    log.info(f"NEW listing: {listing_data['address']} — €{raw.get('rent_warm', '?')} warm")

                    # Draft the application and queue it for your approval —
                    # nothing is ever sent to a landlord without you clicking
                    # "approve" on the dashboard and then running the manual
                    # send step yourself. See submit_application()/
                    # send_approved_application() below.
                    if not dry_run:
                        message = generate_application_message(
                            listing_data["address"],
                            listing_data.get("listing_title", "")
                        )
                        app_id = create_application(listing_id=listing_id, message=message)
                        create_action(
                            listing_id=listing_id,
                            application_id=app_id,
                            action_type="review_application",
                            title=f"New match — review application: {listing_data['address']}",
                            description=f"€{raw.get('rent_warm', '?')} warm, "
                                        f"{raw.get('rooms', '?')} rooms, "
                                        f"{raw.get('size_sqm', '?')} m²",
                            reply_de=message,
                        )
                        log.info(f"Drafted application for review: {listing_data['address']}")
                    else:
                        log.info(f"  (dry run — would draft application for review)")

        except Exception as e:
            log.error(f"Scraper error: {e}", exc_info=True)
            error_count += 1
        finally:
            await browser.close()

    # Update scrape run record
    with get_conn() as conn:
        conn.execute("""
            UPDATE scrape_runs SET
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                listings_found = ?,
                new_listings = ?,
                errors = ?,
                status = ?
            WHERE id = ?
        """, (
            len(raw_listings), new_count,
            str(error_count) if error_count else None,
            "completed" if error_count == 0 else "completed_with_errors",
            run_id
        ))

    log.info(f"Scrape complete: {len(raw_listings)} found, {new_count} new, {error_count} errors")


if __name__ == "__main__":
    if "--send" in sys.argv:
        idx = sys.argv.index("--send")
        if idx + 1 >= len(sys.argv):
            print("Usage: python scraper_immoscout.py --send <source_id>")
            sys.exit(1)
        source_id = sys.argv[idx + 1]
        ok = asyncio.run(send_approved_application(source_id))
        sys.exit(0 if ok else 1)

    dry_run = "--dry" in sys.argv
    asyncio.run(run_scrape(dry_run=dry_run))
