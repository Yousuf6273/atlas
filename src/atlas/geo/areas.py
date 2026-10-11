"""UAE gazetteer used by the query parser, discovery providers and geo checks.

Zone centres were checked against OpenStreetMap/Nominatim on 2026-10-11.
Free-zone and district boundaries are *approximated* by a centre and radius;
they are flagged ``approximate=True`` and geo checks never reject a company
solely because it falls outside an approximate radius (they downgrade to
``nearby``/``in_country`` instead).
"""

from __future__ import annotations

import re

from atlas.models.search import GeoArea

UAE_BBOX = (22.63, 51.42, 26.16, 56.61)

EMIRATE_NAMES = {
    "AE-DU": "Dubai",
    "AE-AZ": "Abu Dhabi",
    "AE-SH": "Sharjah",
    "AE-AJ": "Ajman",
    "AE-RK": "Ras Al Khaimah",
    "AE-FU": "Fujairah",
    "AE-UQ": "Umm Al Quwain",
}

# Emirates within normal commuting distance of each other (for "nearby").
NEIGHBOURS = {
    "AE-DU": {"AE-SH", "AE-AJ", "AE-AZ"},
    "AE-SH": {"AE-DU", "AE-AJ", "AE-UQ"},
    "AE-AJ": {"AE-SH", "AE-DU", "AE-UQ"},
    "AE-AZ": {"AE-DU"},
    "AE-UQ": {"AE-AJ", "AE-SH", "AE-RK"},
    "AE-RK": {"AE-UQ"},
    "AE-FU": set(),
}


def _a(key, label, kind, *, iso=None, bbox=None, center=None, radius_m=None, parent=None, aliases=(), approximate=False):
    return GeoArea(key=key, label=label, kind=kind, iso_code=iso, bbox=bbox, center=center, radius_m=radius_m,
                   parent_key=parent, aliases=list(aliases), approximate=approximate)


AREAS: dict[str, GeoArea] = {a.key: a for a in [
    _a("uae", "United Arab Emirates", "country", iso="AE", bbox=UAE_BBOX,
       aliases=["uae", "u.a.e", "united arab emirates", "emirates", "the emirates"]),
    # Discovery bboxes cover the main urban/industrial areas, not deserts.
    _a("dubai", "Dubai", "emirate", iso="AE-DU", bbox=(24.78, 54.88, 25.36, 55.62), parent="uae",
       aliases=["dubai", "dxb"]),
    _a("abu_dhabi", "Abu Dhabi", "emirate", iso="AE-AZ", bbox=(24.25, 54.30, 24.62, 54.80), parent="uae",
       aliases=["abu dhabi", "abudhabi", "auh"]),
    _a("sharjah", "Sharjah", "emirate", iso="AE-SH", bbox=(25.22, 55.35, 25.47, 55.65), parent="uae",
       aliases=["sharjah", "shj"]),
    _a("ajman", "Ajman", "emirate", iso="AE-AJ", bbox=(25.36, 55.42, 25.45, 55.57), parent="uae", aliases=["ajman"]),
    _a("ras_al_khaimah", "Ras Al Khaimah", "emirate", iso="AE-RK", bbox=(25.61, 55.77, 25.93, 56.10), parent="uae",
       aliases=["ras al khaimah", "ras al-khaimah", "rak"]),
    _a("fujairah", "Fujairah", "emirate", iso="AE-FU", bbox=(25.05, 56.25, 25.20, 56.37), parent="uae",
       aliases=["fujairah"]),
    _a("umm_al_quwain", "Umm Al Quwain", "emirate", iso="AE-UQ", bbox=(25.48, 55.52, 25.60, 55.75), parent="uae",
       aliases=["umm al quwain", "umm al-quwain", "uaq"]),
    # Dubai zones / districts
    _a("difc", "DIFC", "zone", center=(25.2135, 55.2814), radius_m=800, parent="dubai", approximate=True,
       aliases=["difc", "dubai international financial centre", "dubai international financial center", "gate district"]),
    _a("dubai_internet_city", "Dubai Internet City", "zone", center=(25.0975, 55.1640), radius_m=1100, parent="dubai",
       approximate=True, aliases=["dubai internet city", "internet city", "dic"]),
    _a("dubai_media_city", "Dubai Media City", "zone", center=(25.0935, 55.1516), radius_m=900, parent="dubai",
       approximate=True, aliases=["dubai media city", "media city"]),
    _a("dubai_knowledge_park", "Dubai Knowledge Park", "zone", center=(25.1031, 55.1636), radius_m=600,
       parent="dubai", approximate=True, aliases=["dubai knowledge park", "knowledge park"]),
    _a("dubai_silicon_oasis", "Dubai Silicon Oasis", "zone", center=(25.1217, 55.3855), radius_m=2200,
       parent="dubai", approximate=True, aliases=["dubai silicon oasis", "silicon oasis", "dso", "dtec"]),
    _a("jafza", "Jebel Ali Free Zone", "zone", center=(25.0000, 55.0800), radius_m=6000, parent="dubai",
       approximate=True, aliases=["jafza", "jebel ali free zone", "jebel ali"]),
    _a("al_quoz", "Al Quoz", "zone", center=(25.1313, 55.2337), radius_m=3000, parent="dubai", approximate=True,
       aliases=["al quoz", "al quoz industrial", "al quoz industrial area"]),
    _a("dubai_investments_park", "Dubai Investments Park", "zone", center=(24.9780, 55.1912), radius_m=2500,
       parent="dubai", approximate=True, aliases=["dubai investments park", "dip"]),
    _a("dubai_science_park", "Dubai Science Park", "zone", center=(25.0735, 55.2412), radius_m=900, parent="dubai",
       approximate=True, aliases=["dubai science park", "science park"]),
    _a("dubai_production_city", "Dubai Production City", "zone", center=(25.0321, 55.1913), radius_m=1500,
       parent="dubai", approximate=True, aliases=["dubai production city", "impz"]),
    _a("jlt", "JLT / DMCC", "zone", center=(25.0709, 55.1387), radius_m=1000, parent="dubai", approximate=True,
       aliases=["jlt", "jumeirah lakes towers", "dmcc", "jumeirah lake towers"]),
    _a("business_bay", "Business Bay", "zone", center=(25.1795, 55.2684), radius_m=1800, parent="dubai",
       approximate=True, aliases=["business bay"]),
    _a("dubai_industrial_city", "Dubai Industrial City", "zone", center=(24.8399, 55.0663), radius_m=3500,
       parent="dubai", approximate=True, aliases=["dubai industrial city", "dubai industrial park"]),
    _a("al_qusais_industrial", "Al Qusais Industrial Area", "zone", center=(25.2898, 55.3954), radius_m=2500,
       parent="dubai", approximate=True, aliases=["al qusais", "al qusais industrial", "al qusais industrial area"]),
    _a("ras_al_khor_industrial", "Ras Al Khor Industrial Area", "zone", center=(25.1763, 55.3630), radius_m=2500,
       parent="dubai", approximate=True, aliases=["ras al khor", "ras al khor industrial"]),
    _a("dafza", "Dubai Airport Free Zone", "zone", center=(25.2698, 55.3752), radius_m=900, parent="dubai",
       approximate=True, aliases=["dafza", "dubai airport free zone", "dafz"]),
    _a("d3", "Dubai Design District", "zone", center=(25.1883, 55.2975), radius_m=500, parent="dubai",
       approximate=True, aliases=["d3", "dubai design district"]),
    _a("dubai_south", "Dubai South", "zone", center=(24.9144, 55.1615), radius_m=6000, parent="dubai",
       approximate=True, aliases=["dubai south", "dubai world central", "dwc", "al maktoum airport"]),
    _a("dubai_studio_city", "Dubai Studio City", "zone", center=(25.0416, 55.2523), radius_m=1200, parent="dubai",
       approximate=True, aliases=["dubai studio city", "studio city"]),
    _a("dubai_outsource_city", "Dubai Outsource City", "zone", center=(25.1236, 55.4252), radius_m=1000,
       parent="dubai", approximate=True, aliases=["dubai outsource city", "outsource city"]),
    _a("al_barsha", "Al Barsha", "zone", center=(25.0995, 55.2017), radius_m=2500, parent="dubai", approximate=True,
       aliases=["al barsha", "barsha"]),
    _a("dwtc", "Dubai World Trade Centre", "zone", center=(25.2238, 55.2877), radius_m=700, parent="dubai",
       approximate=True, aliases=["dwtc", "dubai world trade centre", "dubai world trade center", "world trade centre"]),
    _a("dubai_commercity", "Dubai CommerCity", "zone", center=(25.2355, 55.3665), radius_m=800, parent="dubai",
       approximate=True, aliases=["dubai commercity", "commercity"]),
    # Other emirates
    _a("masdar_city", "Masdar City", "zone", center=(24.4275, 54.6235), radius_m=2000, parent="abu_dhabi",
       approximate=True, aliases=["masdar", "masdar city"]),
    _a("adgm", "ADGM (Al Maryah Island)", "zone", center=(24.5013, 54.3885), radius_m=1000, parent="abu_dhabi",
       approximate=True, aliases=["adgm", "abu dhabi global market", "al maryah island"]),
    _a("mussafah", "Mussafah", "zone", center=(24.3550, 54.5000), radius_m=5000, parent="abu_dhabi", approximate=True,
       aliases=["mussafah", "musaffah", "icad"]),
    _a("saif_zone", "SAIF Zone", "zone", center=(25.3327, 55.4826), radius_m=1500, parent="sharjah", approximate=True,
       aliases=["saif zone", "saif", "sharjah airport free zone"]),
    _a("hamriyah", "Hamriyah Free Zone", "zone", center=(25.4570, 55.4996), radius_m=2500, parent="sharjah",
       approximate=True, aliases=["hamriyah", "hamriyah free zone"]),
]}

# Alias → key, longest aliases first so "dubai silicon oasis" beats "dubai".
_ALIAS_INDEX: list[tuple[str, str]] = sorted(
    ((alias, key) for key, area in AREAS.items() for alias in area.aliases),
    key=lambda x: -len(x[0]),
)


def get_area(key: str) -> GeoArea:
    return AREAS[key]


def emirate_area_for_iso(iso: str) -> GeoArea | None:
    for a in AREAS.values():
        if a.iso_code == iso:
            return a
    return None


def root_emirate(area: GeoArea) -> GeoArea | None:
    """The emirate an area belongs to (itself for emirates, None for the country)."""
    cur: GeoArea | None = area
    while cur is not None and cur.kind not in ("emirate", "city"):
        cur = AREAS.get(cur.parent_key) if cur.parent_key else None
    return cur


def find_areas(text: str) -> list[tuple[GeoArea, str]]:
    """Find gazetteer areas mentioned in free text. Returns (area, matched alias)."""
    lowered = " " + re.sub(r"[^a-z0-9.\- ]", " ", text.lower()) + " "
    found: list[tuple[GeoArea, str]] = []
    consumed: list[tuple[int, int]] = []
    for alias, key in _ALIAS_INDEX:
        # Short abbreviations (dic, dip, dso, rak…) must be standalone tokens.
        pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
        for m in re.finditer(pattern, lowered):
            span = (m.start(), m.end())
            if any(s <= span[0] < e or s < span[1] <= e for s, e in consumed):
                continue
            if len(alias) <= 3 and alias not in {"uae", "dxb", "auh", "shj", "rak", "uaq", "d3"}:
                # Ambiguous short tokens need context words.
                window = lowered[max(0, span[0] - 25): span[1] + 25]
                if not re.search(r"\bin\b|\bat\b|\bbased\b|\bnear\b|\bzone\b|free zone", window):
                    continue
            consumed.append(span)
            area = AREAS[key]
            if all(a.key != area.key for a, _ in found):
                found.append((area, alias))
    return found
