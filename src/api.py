"""
Kassel Wohnungssuche — FastAPI Backend.
Serves dashboard data and handles approval actions.

Run: uvicorn api:app --host 0.0.0.0 --port 8000
"""

import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from database import (
    init_db, get_conn, get_dashboard_data, get_sync_state,
    upsert_listing, create_application, create_action,
)
from application_template import generate_application_message
from geo import geocode_district

log = logging.getLogger("api")

app = FastAPI(title="Kassel Wohnungssuche API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Lock this down in production
    allow_methods=["*"],
    allow_headers=["*"],
)

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"


@app.get("/")
def root():
    return RedirectResponse("/dashboard/")


@app.on_event("startup")
def startup():
    init_db()


# --- Models ---

class ActionResponse(BaseModel):
    action: str  # "approve", "edit", "dismiss"
    edited_reply: str | None = None


class ManualNote(BaseModel):
    listing_id: str
    note: str


class ScrapedListing(BaseModel):
    source_id: str
    link: str | None = None
    listing_title: str | None = None
    address: str | None = None
    rent_warm: float | None = None
    rent_display: str | None = None
    rooms: float | None = None
    size_sqm: float | None = None
    floor_number: int | None = None


class IngestBody(BaseModel):
    listings: list[ScrapedListing]


# Applicant's hard criteria (see config/profile.json + your search filters):
#   floor 2-3 only, warm rent <= 1100, rooms >= 3.
MAX_FLOOR = 3
MAX_WARM_RENT = 1100
MIN_ROOMS = 3


def should_draft_application(item: "ScrapedListing") -> bool:
    """Decide whether a freshly-scraped ImmoScout24 listing is a strong enough
    match to AUTO-DRAFT a German application for (which then goes into your
    review queue), versus just saving it to the dashboard to browse.

    Returning False doesn't discard the listing — it's still saved and shown;
    it just won't get a drafted application waiting for you.
    """
    # Lenient: draft unless a field is present AND clearly fails. A None means
    # "the scraper couldn't read this off the card" — treated as "unknown, don't
    # reject on it" so a good listing with a missing floor still reaches you.
    if item.floor_number is not None and item.floor_number > MAX_FLOOR:
        return False
    if item.rent_warm is not None and item.rent_warm > MAX_WARM_RENT:
        return False
    if item.rooms is not None and item.rooms < MIN_ROOMS:
        return False
    return True


# --- Endpoints ---

@app.get("/api/dashboard")
def dashboard():
    """Return all dashboard data."""
    return get_dashboard_data()


@app.get("/api/listings")
def list_listings(status: str = None, active_only: bool = True):
    """List all listings, optionally filtered."""
    with get_conn() as conn:
        query = "SELECT * FROM listings WHERE 1=1"
        params = []
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY first_seen_at DESC"
        return [dict(r) for r in conn.execute(query, params).fetchall()]


@app.get("/api/applications")
def list_applications(status: str = None):
    """List all applications."""
    with get_conn() as conn:
        query = """
            SELECT a.*, l.address, l.district, l.rent_display, l.link
            FROM applications a
            JOIN listings l ON l.id = a.listing_id
        """
        params = []
        if status:
            query += " WHERE a.status = ?"
            params.append(status)
        query += " ORDER BY a.last_update DESC"
        return [dict(r) for r in conn.execute(query, params).fetchall()]


@app.get("/api/actions")
def list_actions(status: str = "pending"):
    """List pending action items."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT aq.*, l.address, l.district, l.link
            FROM action_queue aq
            LEFT JOIN listings l ON l.id = aq.listing_id
            WHERE aq.status = ?
            ORDER BY aq.created_at DESC
        """, (status,)).fetchall()]


@app.post("/api/actions/{action_id}/respond")
def respond_to_action(action_id: int, response: ActionResponse):
    """Approve, edit, or dismiss an action."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_conn() as conn:
        action = conn.execute(
            "SELECT * FROM action_queue WHERE id = ?", (action_id,)
        ).fetchone()

        if not action:
            raise HTTPException(404, "Action not found")

        if response.action == "approve":
            conn.execute("""
                UPDATE action_queue SET status = 'approved', resolved_at = ?
                WHERE id = ?
            """, (now, action_id))
            if action["action_type"] == "review_application":
                conn.execute(
                    "UPDATE applications SET status = 'approved' WHERE id = ?",
                    (action["application_id"],)
                )
                return {
                    "status": "approved",
                    "message": (
                        "Marked approved. To actually send it, run: "
                        "python src/scraper_immoscout.py --send <listing_source_id>"
                    ),
                }
            return {
                "status": "approved",
                "message": "Marked approved — sending replies is still a manual step (not yet wired up).",
            }

        elif response.action == "edit":
            conn.execute("""
                UPDATE action_queue SET
                    user_response = ?,
                    proposed_reply_de = COALESCE(?, proposed_reply_de),
                    status = 'approved',
                    resolved_at = ?
                WHERE id = ?
            """, (response.edited_reply, response.edited_reply, now, action_id))
            if action["action_type"] == "review_application" and response.edited_reply:
                conn.execute(
                    "UPDATE applications SET application_message = ?, status = 'approved' WHERE id = ?",
                    (response.edited_reply, action["application_id"])
                )
            return {
                "status": "approved_with_edits",
                "message": (
                    "Marked approved with your edits. To actually send it, run: "
                    "python src/scraper_immoscout.py --send <listing_source_id>"
                ),
            }

        elif response.action == "dismiss":
            conn.execute("""
                UPDATE action_queue SET status = 'dismissed', resolved_at = ?
                WHERE id = ?
            """, (now, action_id))
            return {"status": "dismissed"}

        raise HTTPException(400, "Invalid action")


@app.post("/api/sync")
def sync_now():
    """
    Manually triggered: check Gmail for new landlord replies right now.
    Read-only against Gmail (gmail.readonly scope) — never sends anything.
    """
    from gmail_checker import check_new_emails

    try:
        events = check_new_emails(hours_back=72)
    except SystemExit:
        raise HTTPException(
            400,
            "Gmail isn't set up yet — see README.md 'Gmail API Credentials'. "
            "Need config/credentials.json and to run `python src/gmail_checker.py` once."
        )
    except Exception as e:
        log.error(f"Sync failed: {e}")
        raise HTTPException(500, f"Sync failed: {e}")

    return {
        "synced_at": get_sync_state("last_gmail_sync"),
        "new_events": len(events),
        "events": events,
    }


@app.post("/api/actions/{action_id}/send")
def send_action(action_id: int):
    """
    Explicitly send ONE approved application to the landlord via ImmoScout24.
    You must click this yourself per listing (or use send-all-approved) —
    nothing here runs unattended.
    """
    from scraper_immoscout import send_approved_application

    with get_conn() as conn:
        action = conn.execute(
            "SELECT aq.*, l.source_id, l.address FROM action_queue aq "
            "LEFT JOIN listings l ON l.id = aq.listing_id WHERE aq.id = ?",
            (action_id,)
        ).fetchone()

    if not action:
        raise HTTPException(404, "Action not found")
    if action["action_type"] != "review_application":
        raise HTTPException(400, "This action isn't an application to send")
    if not action["source_id"]:
        raise HTTPException(400, "No source_id on this listing — can't submit via ImmoScout24")

    try:
        ok = asyncio.run(send_approved_application(action["source_id"]))
    except Exception as e:
        log.error(f"Send crashed for action {action_id}: {e}")
        raise HTTPException(502, f"Send crashed: {e}. Check data/scraper.log for details.")

    if not ok:
        raise HTTPException(
            502,
            "Send failed — check data/scraper.log. Common causes: not logged into "
            "ImmoScout24 yet (run src/login_immoscout.py), or this listing has no "
            "real ImmoScout24 link (manually-tracked listings can't be auto-sent)."
        )

    with get_conn() as conn:
        conn.execute(
            "UPDATE action_queue SET status = 'done', resolved_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (action_id,)
        )

    return {"status": "sent", "listing": action["address"]}


@app.post("/api/actions/send-all-approved")
def send_all_approved():
    """
    Send every 'review_application' action you've already approved on the
    dashboard, one after another. Still requires you to click this button —
    nothing sends unless you trigger it.
    """
    from scraper_immoscout import send_approved_application

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT aq.id, l.address, l.source_id
            FROM action_queue aq
            LEFT JOIN listings l ON l.id = aq.listing_id
            JOIN applications a ON a.id = aq.application_id
            WHERE aq.action_type = 'review_application'
              AND aq.status = 'approved' AND a.status = 'approved'
        """).fetchall()

    results = []
    for row in rows:
        if not row["source_id"]:
            results.append({"listing": row["address"], "sent": False, "error": "no source_id"})
            continue
        try:
            ok = asyncio.run(send_approved_application(row["source_id"]))
        except Exception as e:
            log.error(f"Send crashed for {row['address']}: {e}")
            results.append({"listing": row["address"], "sent": False, "error": str(e)})
            continue
        results.append({"listing": row["address"], "sent": ok})
        if ok:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE action_queue SET status = 'done', resolved_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                    (row["id"],)
                )

    return {"attempted": len(results), "results": results}


@app.get("/api/extension/ping")
def extension_ping():
    """Health check the Chrome extension's 'Connect' button calls."""
    data = get_dashboard_data()
    return {
        "ok": True,
        "listings": data["stats"].get("total", 0),
        "pending_actions": data["stats"].get("pending_actions", 0),
    }


@app.get("/api/application-draft")
def application_draft(address: str = "", title: str = ""):
    """Return the German application text for a listing — used by the
    extension's 'Insert my application' autofill button."""
    return {"message": generate_application_message(address, title)}


@app.post("/api/listings/ingest")
def _district_from_address(addr: str | None) -> str | None:
    """Pull the district out of an address like
    'Silberbornstr. 29 A, Niederzwehren, Kassel' -> 'Niederzwehren'."""
    if not addr:
        return None
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if len(parts) >= 3 and parts[-1].lower().startswith("kassel"):
        return parts[-2]
    if len(parts) == 2:
        return parts[0]
    return None


def ingest_listings(body: IngestBody):
    """Receive listings the Chrome extension scraped from your logged-in
    ImmoScout24 session. Upsert them, and for strong matches (per your filter)
    draft a German application into your review queue. Never sends anything."""
    new_count = 0
    drafted = 0

    for item in body.listings:
        listing_data = {
            "source": "immoscout24",
            "source_id": item.source_id,
            "address": item.address or (item.listing_title or f"ImmoScout expose {item.source_id}"),
            "rent_warm": item.rent_warm,
            "rent_display": item.rent_display,
            "rooms": item.rooms,
            "size_sqm": item.size_sqm,
            "floor_number": item.floor_number,
            "link": item.link or f"https://www.immobilienscout24.de/expose/{item.source_id}",
            "listing_title": item.listing_title,
            "raw_data": item.model_dump(),
        }
        listing_id, is_new = upsert_listing(listing_data)
        if not is_new:
            continue
        new_count += 1

        # Derive district from the address ("… , <District>, Kassel") and place
        # the listing on the map via district-center coordinates.
        district = _district_from_address(item.address)
        lat, lng = geocode_district(district)
        with get_conn() as conn:
            conn.execute(
                "UPDATE listings SET district = COALESCE(district, ?), "
                "latitude = ?, longitude = ? WHERE id = ?",
                (district, lat, lng, listing_id),
            )

        if should_draft_application(item):
            message = generate_application_message(
                listing_data["address"], item.listing_title or ""
            )
            app_id = create_application(listing_id=listing_id, message=message)
            create_action(
                listing_id=listing_id,
                application_id=app_id,
                action_type="review_application",
                title=f"New match — review application: {listing_data['address']}",
                description=f"{item.rent_display or ''} · "
                            f"{item.rooms or '?'} rooms · floor {item.floor_number or '?'}",
                reply_de=message,
            )
            drafted += 1

    return {"received": len(body.listings), "new": new_count, "drafted": drafted}


@app.get("/api/emails/recent")
def recent_emails(limit: int = 20):
    """Get recent email events."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT ee.*, l.address
            FROM email_events ee
            LEFT JOIN listings l ON l.id = ee.listing_id
            ORDER BY ee.received_at DESC
            LIMIT ?
        """, (limit,)).fetchall()]


@app.get("/api/stats")
def get_stats():
    """Get summary statistics."""
    with get_conn() as conn:
        stats = dict(conn.execute("""
            SELECT
                COUNT(DISTINCT l.id) as total_listings,
                COUNT(DISTINCT CASE WHEN a.status IN ('auto_applied','applied') THEN a.id END) as active_apps,
                COUNT(DISTINCT CASE WHEN a.status = 'viewing_scheduled' THEN a.id END) as viewings,
                COUNT(DISTINCT CASE WHEN a.status = 'rejected' THEN a.id END) as rejected,
                COUNT(DISTINCT CASE WHEN a.status = 'offered' THEN a.id END) as offers
            FROM listings l
            LEFT JOIN applications a ON a.listing_id = l.id
        """).fetchone())

        pending = conn.execute(
            "SELECT COUNT(*) as c FROM action_queue WHERE status = 'pending'"
        ).fetchone()["c"]

        last_scrape = conn.execute(
            "SELECT completed_at, new_listings FROM scrape_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

        stats["pending_actions"] = pending
        stats["last_scrape"] = dict(last_scrape) if last_scrape else None

        return stats


@app.get("/api/scrape-runs")
def scrape_history(limit: int = 10):
    """Get recent scrape run history."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT * FROM scrape_runs ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()]


app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
