"""
Standard application message template for ImmoScout24 / Kleinanzeigen.
Generates formal German "Sie" form messages using your applicant profile.

Your personal profile is loaded from config/profile.json (git-ignored).
Copy config/profile.example.json to config/profile.json and fill in your
details. If profile.json is missing, placeholder values are used.
"""

import json
from pathlib import Path

_PLACEHOLDER = {
    "name": "Your Full Name",
    "phone": "+49 000 0000000",
    "email": "you@example.com",
    "employer": "Your Employer GmbH",
    "position": "Your Position",
    "contract_type": "unbefristet",
    "gross_annual": "00.000",
    "household": "X Personen",
    "move_in": "sofort / nach Vereinbarung",
    "pets": "Keine",
    "smoker": "Nein",
    "instruments": "Keine",
}


def _load_profile() -> dict:
    path = Path(__file__).parent.parent / "config" / "profile.json"
    if path.exists():
        try:
            return {**_PLACEHOLDER, **json.loads(path.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(_PLACEHOLDER)


APPLICANT = _load_profile()


def generate_application_message(listing_address: str = "", listing_title: str = "") -> str:
    """Generate the standard application message in German."""
    address_line = f" in der {listing_address}" if listing_address else ""
    title_line = f'"{listing_title}"' if listing_title else "Ihre inserierte Wohnung"

    return f"""Sehr geehrte Damen und Herren,

mit großem Interesse habe ich Ihr Angebot {title_line}{address_line} gesehen und möchte mich hiermit als Mieter bewerben.

Zu meiner Person:
Mein Name ist {APPLICANT['name']}, ich bin seit Juli 2026 als {APPLICANT['position']} bei der {APPLICANT['employer']} in Kassel tätig ({APPLICANT['contract_type']}). Mein Bruttojahresgehalt beträgt EUR {APPLICANT['gross_annual']}.

Haushalt: {APPLICANT['household']}. Meine Frau und Tochter ziehen Mitte Oktober 2026 aus Indien nach.

Ich bin Nichtraucher, habe keine Haustiere und keine Musikinstrumente.

Gerne stelle ich Ihnen folgende Unterlagen zur Verfügung:
– Arbeitsvertrag und aktuelle Gehaltsnachweise
– Kopie Reisepass und Blaue Karte EU
– Meldebescheinigung Kassel
– Schufa-Auskunft (beantragt, liegt in Kürze vor)

Über eine Einladung zur Besichtigung würde ich mich sehr freuen. Ich bin unter {APPLICANT['phone']} oder per E-Mail an {APPLICANT['email']} erreichbar.

Mit freundlichen Grüßen
{APPLICANT['name']}"""


def generate_viewing_confirmation(contact_name: str, date_str: str,
                                  time_str: str, address: str) -> str:
    """Generate a viewing confirmation reply in German."""
    salutation = f"Sehr geehrte/r {contact_name}" if contact_name else "Sehr geehrte Damen und Herren"

    return f"""{salutation},

vielen Dank für die Einladung. Ich bestätige gerne den Besichtigungstermin am {date_str} um {time_str} Uhr für die Wohnung {address}.

Für Rückfragen bin ich unter {APPLICANT['phone']} erreichbar.

Mit freundlichen Grüßen
{APPLICANT['name']}"""


def generate_document_followup(contact_name: str) -> str:
    """Generate a follow-up message offering to send documents."""
    salutation = f"Sehr geehrte/r {contact_name}" if contact_name else "Sehr geehrte Damen und Herren"

    return f"""{salutation},

vielen Dank für Ihre Rückmeldung. Die gewünschten Unterlagen sende ich Ihnen gerne umgehend zu.

Folgende Dokumente kann ich bereitstellen:
– Arbeitsvertrag ({APPLICANT['employer']}, {APPLICANT['contract_type']})
– Gehaltsnachweise Juli + August 2026
– Kopie Reisepass und Blaue Karte EU
– Meldebescheinigung Kassel
– Schufa-Datenkopie (sobald per Post eingetroffen)

Bitte teilen Sie mir mit, auf welchem Weg ich Ihnen die Unterlagen zukommen lassen soll.

Mit freundlichen Grüßen
{APPLICANT['name']}
{APPLICANT['phone']}"""
