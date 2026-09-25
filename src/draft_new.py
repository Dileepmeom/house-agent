"""
Draft (but do not send) applications for every listing that's still 'new'
(no application record yet). Queues each as a review_application action for
approval on the dashboard. Run: python src/draft_new.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from database import init_db, get_conn, create_application, create_action
from application_template import generate_application_message


def draft_new():
    init_db()
    with get_conn() as conn:
        new_listings = conn.execute("""
            SELECT l.* FROM listings l
            LEFT JOIN applications a ON a.listing_id = l.id
            WHERE a.id IS NULL AND l.is_active = 1
        """).fetchall()

    count = 0
    for listing in new_listings:
        message = generate_application_message(
            listing_address=listing["address"],
            listing_title=listing["listing_title"] or ""
        )
        app_id = create_application(listing_id=listing["id"], message=message)
        create_action(
            listing_id=listing["id"],
            application_id=app_id,
            action_type="review_application",
            title=f"New match — review application: {listing['address']}",
            description=f"{listing['rent_display'] or ''}, {listing['district'] or ''}",
            reply_de=message,
        )
        count += 1
        print(f"Drafted: {listing['address']}")

    print(f"\n{count} application(s) drafted and queued for your review.")


if __name__ == "__main__":
    draft_new()
