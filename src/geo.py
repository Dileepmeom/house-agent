"""
Approximate coordinates for Kassel districts, so captured listings (which only
carry a district name, not exact lat/long) can be placed on the dashboard map.
These are district-center approximations — pins are indicative, not exact.
"""

DISTRICT_COORDS = {
    "Mitte": (51.3155, 9.4910),
    "Vorderer Westen": (51.3138, 9.4700),
    "Wehlheiden": (51.3050, 9.4650),
    "Niederzwehren": (51.2850, 9.4650),
    "Kirchditmold": (51.3250, 9.4500),
    "Rothenditmold": (51.3300, 9.4550),
    "Nord": (51.3300, 9.4850),
    "Nord (Holland)": (51.3320, 9.4830),
    "Nord-Holland": (51.3320, 9.4830),
    "West": (51.3200, 9.4600),
    "Wilhelmshöhe": (51.3080, 9.4300),
    "Bad Wilhelmshöhe": (51.3080, 9.4300),
    "Südstadt": (51.2980, 9.4850),
    "Bettenhausen": (51.3080, 9.5250),
    "Waldau": (51.2800, 9.5300),
    "Fasanenhof": (51.2950, 9.4560),
    "Harleshausen": (51.3450, 9.4300),
    "Wesertor": (51.3200, 9.5050),
    "Unterneustadt": (51.3130, 9.5050),
    "Oberzwehren": (51.2820, 9.4520),
    "Kassel": (51.3127, 9.4797),  # city-center fallback
}


def geocode_district(district: str | None) -> tuple[float | None, float | None]:
    """Best-effort district-center coordinates; falls back to Kassel center,
    then (None, None) if nothing matches."""
    if not district:
        return (None, None)
    # Exact match first, then a loose contains-match (addresses like
    # "Niederzwehren, Kassel" or "Nord (Holland)").
    if district in DISTRICT_COORDS:
        return DISTRICT_COORDS[district]
    for name, coords in DISTRICT_COORDS.items():
        if name != "Kassel" and name.lower() in district.lower():
            return coords
    return DISTRICT_COORDS["Kassel"]
