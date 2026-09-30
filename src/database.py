"""
Kassel Wohnungssuche — SQLite database layer.
Stores listings, application status, email responses, and action queue.
"""

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "kassel.db"


def get_db_path():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(get_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            id TEXT PRIMARY KEY,
            address TEXT NOT NULL,
            district TEXT,
            rent_kalt REAL,
            rent_warm REAL,
            rent_display TEXT,
            rooms REAL,
            size_sqm REAL,
            floor TEXT,
            floor_number INTEGER,
            latitude REAL,
            longitude REAL,
            source TEXT NOT NULL,  -- 'immoscout24', 'kleinanzeigen', 'wunderflats', 'referral', 'manual'
            source_id TEXT,       -- expose number or listing ID on the platform
            link TEXT,
            availability_date TEXT,
            requires_wbs INTEGER DEFAULT 0,
            requires_schufa INTEGER DEFAULT 0,
            listing_title TEXT,
            listing_description TEXT,
            first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            last_checked_at TEXT,
            is_active INTEGER DEFAULT 1,
            is_duplicate INTEGER DEFAULT 0,
            duplicate_of TEXT,
            raw_data TEXT,  -- JSON blob of full scraped data
            FOREIGN KEY (duplicate_of) REFERENCES listings(id)
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            -- statuses: pending, auto_applied, applied, viewing_scheduled,
            --           viewing_done, docs_requested, offered, rejected,
            --           withdrawn, too_high_floor, no_response
            date_applied TEXT,
            contact_name TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            agent_company TEXT,
            application_message TEXT,  -- the German message sent
            last_update TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            next_action TEXT,
            notes TEXT,
            FOREIGN KEY (listing_id) REFERENCES listings(id)
        );

        CREATE TABLE IF NOT EXISTS email_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT,
            application_id INTEGER,
            gmail_thread_id TEXT,
            gmail_message_id TEXT,
            direction TEXT NOT NULL,  -- 'inbound', 'outbound'
            sender TEXT,
            recipient TEXT,
            subject TEXT,
            body_preview TEXT,
            event_type TEXT,
            -- event_types: auto_reply, viewing_invite, rejection, docs_request,
            --              offer, question, confirmation, unknown
            received_at TEXT NOT NULL,
            processed_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            raw_data TEXT,
            FOREIGN KEY (listing_id) REFERENCES listings(id),
            FOREIGN KEY (application_id) REFERENCES applications(id)
        );

        CREATE TABLE IF NOT EXISTS action_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT,
            application_id INTEGER,
            action_type TEXT NOT NULL,
            -- action_types: approve_reply, answer_question, confirm_viewing,
            --               upload_docs, review_contract, auto_applied_notice,
            --               review_application
            status TEXT NOT NULL DEFAULT 'pending',  -- pending, approved, dismissed, done
            title TEXT NOT NULL,
            description TEXT,
            proposed_reply_en TEXT,  -- English version for you to review
            proposed_reply_de TEXT,  -- German version to actually send
            user_response TEXT,     -- your edit/approval
            gmail_thread_id TEXT,   -- set when this action came from an email, for a direct link
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            resolved_at TEXT,
            FOREIGN KEY (listing_id) REFERENCES listings(id),
            FOREIGN KEY (application_id) REFERENCES applications(id)
        );

        CREATE TABLE IF NOT EXISTS scrape_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            completed_at TEXT,
            listings_found INTEGER DEFAULT 0,
            new_listings INTEGER DEFAULT 0,
            errors TEXT,
            status TEXT DEFAULT 'running'  -- running, completed, failed
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source, source_id);
        CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(is_active);
        CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
        CREATE INDEX IF NOT EXISTS idx_applications_listing ON applications(listing_id);
        CREATE INDEX IF NOT EXISTS idx_action_queue_status ON action_queue(status);
        CREATE INDEX IF NOT EXISTS idx_email_events_listing ON email_events(listing_id);
        """)


def set_sync_state(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )


def get_sync_state(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


# --- Helper functions ---

def upsert_listing(listing: dict) -> tuple[str, bool]:
    """Insert or update a listing. Returns (id, is_new)."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM listings WHERE source = ? AND source_id = ?",
            (listing["source"], listing.get("source_id"))
        ).fetchone()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if existing:
            conn.execute("""
                UPDATE listings SET
                    rent_warm = COALESCE(?, rent_warm),
                    is_active = COALESCE(?, is_active),
                    last_checked_at = ?
                WHERE id = ?
            """, (listing.get("rent_warm"), listing.get("is_active"), now, existing["id"]))
            return existing["id"], False
        else:
            lid = listing.get("id") or f"{listing['source']}_{listing.get('source_id', now)}"
            conn.execute("""
                INSERT INTO listings
                (id, address, district, rent_kalt, rent_warm, rent_display, rooms,
                 size_sqm, floor, floor_number, source, source_id, link,
                 availability_date, requires_wbs, listing_title, first_seen_at, last_checked_at, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lid, listing["address"], listing.get("district"),
                listing.get("rent_kalt"), listing.get("rent_warm"),
                listing.get("rent_display"), listing.get("rooms"),
                listing.get("size_sqm"), listing.get("floor"),
                listing.get("floor_number"), listing["source"],
                listing.get("source_id"), listing.get("link"),
                listing.get("availability_date"), listing.get("requires_wbs", 0),
                listing.get("listing_title"), now, now,
                json.dumps(listing.get("raw_data")) if listing.get("raw_data") else None
            ))
            return lid, True


def create_application(listing_id: str, message: str, contact: dict = None) -> int:
    """Create a draft application record awaiting your review — date_applied
    is set only once it's actually sent (see send_approved_application)."""
    with get_conn() as conn:
        cursor = conn.execute("""
            INSERT INTO applications
            (listing_id, status, contact_name, contact_email,
             contact_phone, agent_company, application_message)
            VALUES (?, 'pending_review', ?, ?, ?, ?, ?)
        """, (
            listing_id,
            contact.get("name") if contact else None,
            contact.get("email") if contact else None,
            contact.get("phone") if contact else None,
            contact.get("company") if contact else None,
            message
        ))
        return cursor.lastrowid


def create_action(listing_id: str, action_type: str, title: str,
                  description: str = None, reply_en: str = None,
                  reply_de: str = None, application_id: int = None,
                  gmail_thread_id: str = None) -> int:
    """Queue an action for your review."""
    with get_conn() as conn:
        cursor = conn.execute("""
            INSERT INTO action_queue
            (listing_id, application_id, action_type, title, description,
             proposed_reply_en, proposed_reply_de, gmail_thread_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (listing_id, application_id, action_type, title,
              description, reply_en, reply_de, gmail_thread_id))
        return cursor.lastrowid


def get_dashboard_data() -> dict:
    """Return all data needed for the dashboard API."""
    with get_conn() as conn:
        listings = [dict(r) for r in conn.execute("""
            SELECT l.*, a.status as app_status, a.date_applied, a.contact_name,
                   a.contact_email, a.agent_company, a.next_action, a.last_update as app_last_update,
                   a.id as application_id
            FROM listings l
            LEFT JOIN applications a ON a.listing_id = l.id
            ORDER BY l.first_seen_at DESC
        """).fetchall()]

        actions = [dict(r) for r in conn.execute("""
            SELECT aq.*, l.address, l.district, l.link, l.latitude, l.longitude,
                   l.floor, l.floor_number, l.availability_date, l.rent_display, l.rooms
            FROM action_queue aq
            LEFT JOIN listings l ON l.id = aq.listing_id
            WHERE aq.status IN ('pending', 'approved')
            ORDER BY
              CASE aq.status WHEN 'pending' THEN 0 ELSE 1 END,
              aq.created_at DESC
        """).fetchall()]

        recent_emails = [dict(r) for r in conn.execute("""
            SELECT ee.*, l.link as listing_link, l.address as listing_address
            FROM email_events ee
            LEFT JOIN listings l ON l.id = ee.listing_id
            ORDER BY ee.received_at DESC LIMIT 20
        """).fetchall()]

        stats = dict(conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN a.status IN ('auto_applied','applied') THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN a.status = 'viewing_scheduled' THEN 1 ELSE 0 END) as viewings,
                SUM(CASE WHEN a.status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                SUM(CASE WHEN l.is_active = 1 AND a.id IS NULL THEN 1 ELSE 0 END) as new_matches
            FROM listings l
            LEFT JOIN applications a ON a.listing_id = l.id
        """).fetchone())

        pending_actions = conn.execute(
            "SELECT COUNT(*) as c FROM action_queue WHERE status = 'pending'"
        ).fetchone()["c"]

        last_sync = conn.execute(
            "SELECT value FROM sync_state WHERE key = 'last_gmail_sync'"
        ).fetchone()

    return {
        "listings": listings,
        "actions": actions,
        "recent_emails": recent_emails,
        "stats": {**stats, "pending_actions": pending_actions},
        "last_gmail_sync": last_sync["value"] if last_sync else None,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {get_db_path()}")
