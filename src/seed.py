"""
Seed the database with the applications already in flight before the agent
existed (from the original tracking sheet). Run once: python src/seed.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from database import init_db, get_conn

# Rough district-center coordinates for the dashboard map (Kassel, Hesse).
DISTRICT_COORDS = {
    "Vorderer Westen": (51.3138, 9.4700),
    "Mitte": (51.3155, 9.4910),
    "Wilhelmshöhe": (51.3080, 9.4300),
    "Nord": (51.3300, 9.4850),
    "Südstadt": (51.2980, 9.4850),
    "Wehlheiden": (51.3050, 9.4650),
    "Niederzwehren": (51.2850, 9.4650),
    "Kirchditmold": (51.3250, 9.4500),
    "West": (51.3200, 9.4600),
    "Nord-Holland": (51.3400, 9.4750),
    "Rothenditmold": (51.3300, 9.4550),
    "Kassel": (51.3127, 9.4797),
}

# id, address, district, rent_display, rent_warm, app_status, contact_name
SEED = [
    ("tischbein12a", "Tischbeinstraße 12a, 34121 Kassel, 2.OG", "Vorderer Westen", "€700/€840", 840, "docs_requested", "Björn Ernst"),
    ("grassweg4", "Grassweg 4, 34121 Kassel", "Mitte", "€781.65 warm", 781.65, "applied", None),
    ("loewenburg", "Kassel-Wilhelmshöhe, 2 ZKB Altbau", "Wilhelmshöhe", "—", None, "draft_not_sent", "Mathias Pötter"),
    ("leipziger11", "Leipziger Straße 11, 34125 Kassel", "Nord", "€550 kalt", None, "viewing_done", "Ingmar Schoerck"),
    ("frankfurter75a", "Frankfurter Str. 75A, 34121 Kassel", "Südstadt", "€695 kalt", None, "rejected", "Mateo Santa"),
    ("wilhelmshoher190", "Wilhelmshöher Allee 190, Kassel", "Wehlheiden", "€790", 790, "rejected", "Fitore Bajrami"),
    ("hupfeld11", "Hupfeldstr. 11, 34121 Kassel", "Wehlheiden", "€680 kalt", None, "rejected", "Johannes Djukic"),
    ("wunderflats", "Himmelsstürmer Studio, Kassel", "Kassel", "€1,650 furnished", 1650, "pending_confirmation", "Anna Rath"),
    ("bismarck20", "Bismarckstr. 20, Mitte", "Mitte", "€822 warm", 822, "new", None),
    ("quellen10", "Quellenstraße 10, Niederzwehren", "Niederzwehren", "€850 warm", 850, "new", None),
    ("frasenweg11", "Frasenweg 11, Kirchditmold", "Kirchditmold", "€949 warm", 949, "new", None),
    ("koelnische147", "Kölnische Str. 147, West", "West", "€871 warm", 871, "new", None),
    ("fichtner29", "Fichtnerstraße 29, Nord-Holland", "Nord-Holland", "€800 warm", 800, "new", None),
    ("rotenburger7", "Rotenburger Str. 7, Rothenditmold", "Rothenditmold", "€929 warm", 929, "new", None),
    ("silberborn37", "An der Kurhessenhalle 37, Niederzwehren", "Niederzwehren", "€711 warm", 711, "new", None),
]

REJECTED_REASONS = {
    "frankfurter75a": "4th floor",
    "wilhelmshoher190": "4th floor",
    "hupfeld11": "deactivated",
}


def seed():
    init_db()
    with get_conn() as conn:
        for source_id, address, district, rent_display, rent_warm, status, contact in SEED:
            lat, lng = DISTRICT_COORDS.get(district, DISTRICT_COORDS["Kassel"])
            listing_id = f"seed_{source_id}"

            conn.execute("""
                INSERT OR IGNORE INTO listings
                (id, address, district, rent_warm, rent_display, source, source_id,
                 latitude, longitude, is_active)
                VALUES (?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?)
            """, (
                listing_id, address, district, rent_warm, rent_display,
                source_id, lat, lng,
                0 if status == "rejected" else 1,
            ))

            if status == "new":
                # No application yet — just a tracked match.
                continue

            existing = conn.execute(
                "SELECT id FROM applications WHERE listing_id = ?", (listing_id,)
            ).fetchone()
            if existing:
                continue

            notes = REJECTED_REASONS.get(source_id)
            conn.execute("""
                INSERT INTO applications
                (listing_id, status, contact_name, notes)
                VALUES (?, ?, ?, ?)
            """, (listing_id, status, contact, notes))

    print(f"Seeded {len(SEED)} listings.")


if __name__ == "__main__":
    seed()
