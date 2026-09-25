"""
Kassel Wohnungssuche — Gmail Response Checker.
Uses Google Gmail API to read new emails, classify them, and update the DB.

Setup:
1. Create a Google Cloud project
2. Enable Gmail API
3. Create OAuth2 credentials (Desktop app type)
4. Download credentials.json to config/
5. Run once manually to complete OAuth flow
"""

import json
import re
import sys
import logging
import base64
from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

sys.path.insert(0, str(Path(__file__).parent))
from database import init_db, get_conn, create_action, set_sync_state
from application_template import APPLICANT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent / "data" / "gmail_checker.log")
    ]
)
log = logging.getLogger("gmail_checker")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CONFIG_DIR = Path(__file__).parent.parent / "config"
TOKEN_PATH = CONFIG_DIR / "gmail_token.json"
CREDS_PATH = CONFIG_DIR / "credentials.json"

# Keywords for classifying emails
REJECTION_KEYWORDS = [
    "leider", "absage", "anderweitig vergeben", "nicht berücksichtigen",
    "vermietet", "rented out", "keine Besichtigung", "abgelehnt",
    "already rented", "an einen anderen"
]
VIEWING_KEYWORDS = [
    "besichtigung", "besichtigungstermin", "einladung",
    "viewing", "termin", "kennenlern"
]
DOCS_KEYWORDS = [
    "unterlagen", "dokumente", "schufa", "gehaltsnachwei",
    "selbstauskunft", "einkommensnachwei", "nachweis",
    "documents", "gehaltsabrechnung"
]
QUESTION_KEYWORDS = [
    "fragen", "frage", "bitte teilen sie", "könnten sie",
    "wann", "wie viele", "questions"
]
OFFER_KEYWORDS = [
    "zusage", "mietvertrag", "angebot", "zugesagt",
    "freuen uns ihnen", "offer"
]


def get_gmail_service():
    """Authenticate and return Gmail API service."""
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_PATH.exists():
                log.error(
                    f"credentials.json not found at {CREDS_PATH}. "
                    "Download it from Google Cloud Console."
                )
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_PATH.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def classify_email(subject: str, body: str) -> str:
    """Classify an email into event types."""
    text = (subject + " " + body).lower()

    # Check in priority order
    if any(kw in text for kw in OFFER_KEYWORDS):
        return "offer"
    if any(kw in text for kw in REJECTION_KEYWORDS):
        return "rejection"
    if any(kw in text for kw in VIEWING_KEYWORDS):
        return "viewing_invite"
    if any(kw in text for kw in DOCS_KEYWORDS):
        return "docs_request"
    if any(kw in text for kw in QUESTION_KEYWORDS):
        return "question"

    # Check for auto-replies
    if "automatisch" in text or "auto-reply" in text or "abwesenheit" in text:
        return "auto_reply"

    return "unknown"


def match_email_to_listing(sender: str, subject: str, body: str) -> tuple:
    """Try to match an email to a known listing/application."""
    with get_conn() as conn:
        # First check by known contact email
        apps = conn.execute("""
            SELECT a.id as app_id, a.listing_id, l.address
            FROM applications a
            JOIN listings l ON l.id = a.listing_id
            WHERE a.contact_email IS NOT NULL
        """).fetchall()

        for app in apps:
            # Check if sender matches
            if app["contact_email"] and app["contact_email"].lower() in sender.lower():
                return app["listing_id"], app["app_id"]

        # Try matching by address in subject/body
        listings = conn.execute("""
            SELECT l.id, l.address, a.id as app_id
            FROM listings l
            LEFT JOIN applications a ON a.listing_id = l.id
        """).fetchall()

        text = (subject + " " + body).lower()
        for listing in listings:
            if listing["address"] and listing["address"].lower()[:20] in text:
                return listing["id"], listing["app_id"]

        # Try matching by expose ID
        expose_match = re.search(r'expose/(\d+)', body + subject)
        if expose_match:
            expose_id = expose_match.group(1)
            match = conn.execute(
                "SELECT id FROM listings WHERE source_id = ?", (expose_id,)
            ).fetchone()
            if match:
                app = conn.execute(
                    "SELECT id FROM applications WHERE listing_id = ?", (match["id"],)
                ).fetchone()
                return match["id"], app["id"] if app else None

    return None, None


def get_message_body(msg) -> str:
    """Extract plain text body from a Gmail message."""
    payload = msg.get("payload", {})

    # Check for simple body
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Check parts
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # Recurse into multipart
        for subpart in part.get("parts", []):
            if subpart.get("mimeType") == "text/plain" and subpart.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(subpart["body"]["data"]).decode("utf-8", errors="replace")

    return ""


def check_new_emails(hours_back: int = 24):
    """Check Gmail for new housing-related emails."""
    init_db()
    service = get_gmail_service()

    # Build search query
    after_date = (datetime.now(timezone.utc) - timedelta(hours=hours_back))
    after_epoch = int(after_date.timestamp())
    query = (
        f"after:{after_epoch} "
        "(from:immobilienscout24 OR from:kleinanzeigen OR from:wunderflats "
        "OR from:immomio OR subject:wohnung OR subject:besichtigung "
        "OR subject:mietwohnung OR subject:bewerbung)"
    )

    log.info(f"Searching Gmail: {query}")

    results = service.users().messages().list(
        userId="me", q=query, maxResults=50
    ).execute()

    messages = results.get("messages", [])
    log.info(f"Found {len(messages)} messages")

    new_events = []

    for msg_ref in messages:
        msg_id = msg_ref["id"]

        # Check if we've already processed this message
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM email_events WHERE gmail_message_id = ?", (msg_id,)
            ).fetchone()
            if existing:
                continue

        # Fetch full message
        msg = service.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("From", "")
        subject = headers.get("Subject", "")
        date_str = headers.get("Date", "")
        thread_id = msg.get("threadId", "")

        body = get_message_body(msg)
        body_preview = body[:500] if body else ""

        # Skip marketing/notification emails
        if any(skip in subject.lower() for skip in [
            "neue angebote", "suchauftrag", "we think you will",
            "still looking", "newsletter", "preisatlas"
        ]):
            log.info(f"Skipping marketing email: {subject}")
            continue

        # Classify
        event_type = classify_email(subject, body)
        listing_id, app_id = match_email_to_listing(sender, subject, body)

        log.info(f"Processing: [{event_type}] {subject} (from: {sender})")

        # Store in DB
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO email_events
                (listing_id, application_id, gmail_thread_id, gmail_message_id,
                 direction, sender, recipient, subject, body_preview,
                 event_type, received_at)
                VALUES (?, ?, ?, ?, 'inbound', ?, ?, ?, ?, ?, ?)
            """, (
                listing_id, app_id, thread_id, msg_id,
                sender, APPLICANT.get("email", "me"), subject,
                body_preview, event_type, date_str
            ))

            # Update application status based on event type
            if app_id:
                status_map = {
                    "rejection": "rejected",
                    "viewing_invite": "viewing_scheduled",
                    "docs_request": "docs_requested",
                    "offer": "offered",
                }
                new_status = status_map.get(event_type)
                if new_status:
                    conn.execute(
                        "UPDATE applications SET status = ?, last_update = ? WHERE id = ?",
                        (new_status, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), app_id)
                    )

        # Create dashboard actions for items needing attention
        if event_type == "viewing_invite":
            create_action(
                listing_id=listing_id,
                application_id=app_id,
                action_type="confirm_viewing",
                title=f"Viewing invitation: {subject}",
                description=f"From: {sender}\n\n{body_preview}",
                reply_en="I'd like to confirm this viewing. Shall I reply with confirmation + your phone number?",
                gmail_thread_id=thread_id,
            )
        elif event_type == "docs_request":
            create_action(
                listing_id=listing_id,
                application_id=app_id,
                action_type="upload_docs",
                title=f"Documents requested: {subject}",
                description=f"From: {sender}\n\n{body_preview}",
                reply_en="Landlord is asking for documents. Ready to send the standard pack?",
                gmail_thread_id=thread_id,
            )
        elif event_type == "question":
            create_action(
                listing_id=listing_id,
                application_id=app_id,
                action_type="answer_question",
                title=f"Question from landlord: {subject}",
                description=f"From: {sender}\n\n{body_preview}",
                reply_en="Landlord has a question I need your input on. See the email details above.",
                gmail_thread_id=thread_id,
            )
        elif event_type == "offer":
            create_action(
                listing_id=listing_id,
                application_id=app_id,
                action_type="review_contract",
                title=f"🎉 OFFER received: {subject}",
                description=f"From: {sender}\n\n{body_preview}",
                reply_en="Congratulations — you got an offer! Review the details and decide.",
                gmail_thread_id=thread_id,
            )

        new_events.append({
            "type": event_type,
            "subject": subject,
            "sender": sender,
            "listing_id": listing_id,
        })

    set_sync_state("last_gmail_sync", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    log.info(f"Processed {len(new_events)} new email events")
    return new_events


if __name__ == "__main__":
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    events = check_new_emails(hours_back=hours)

    if events:
        print(f"\n{'='*60}")
        print(f"New email events ({len(events)}):")
        for ev in events:
            print(f"  [{ev['type']}] {ev['subject']}")
    else:
        print("No new housing-related emails.")
