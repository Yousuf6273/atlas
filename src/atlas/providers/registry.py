"""Provider registry: implemented adapters plus documented *non-automated* sources.

Sources that were investigated but are not automated are registered as
metadata-only entries so the UI can show why, and what the manual
alternative is. They have no code path that touches the network.
"""

from __future__ import annotations

import sqlite3

from atlas.db import providers as provider_db
from atlas.models.enums import ProviderAvailability
from atlas.models.search import SearchCriteria
from atlas.providers.base import DiscoveryProvider, ProviderInfo
from atlas.providers.gleif import GleifProvider
from atlas.providers.manual import (
    DirectoryPageProvider,
    ManualCsvProvider,
    UrlListProvider,
)
from atlas.providers.osm_overpass import OverpassProvider
from atlas.providers.searxng import SearxngProvider
from atlas.providers.wikidata import WikidataProvider

IMPLEMENTED: list[DiscoveryProvider] = [
    OverpassProvider(),
    WikidataProvider(),
    GleifProvider(),
    SearxngProvider(),
    ManualCsvProvider(),
    UrlListProvider(),
    DirectoryPageProvider(),
]

NOT_AUTOMATED: list[ProviderInfo] = [
    ProviderInfo(
        name="difc_public_register", display_name="DIFC Public Register",
        availability=ProviderAvailability.DISABLED_POLICY,
        description="Register of DIFC-licensed entities.",
        coverage="DIFC entities only.", source_url="https://www.difc.com/business/public-register",
        limitations="Reviewed 2026-10-11: difc.com answers automated clients with a bot-protection checkpoint "
                    "(HTTP 429, 'Vercel Security Checkpoint') and robots.txt disallows its /api/ and search paths. "
                    "ATLAS does not bypass anti-bot systems. Look companies up manually in a browser and add them "
                    "via CSV or URL import.",
        enabled_by_default=False, runs_in_search=False),
    ProviderInfo(
        name="dubai_pulse_ded", display_name="Dubai Pulse – DED licence datasets",
        availability=ProviderAvailability.MANUAL_ONLY,
        description="Dubai Economy commercial/industrial licence master data (mainland Dubai).",
        coverage="Mainland Dubai licences (not free zones).",
        source_url="https://www.dubaipulse.gov.ae/data/ded-licenses/ded_license_master-open",
        limitations="Reviewed 2026-10-11: download requires a permission request, acceptance of the dataset's "
                    "terms and approval (up to 14 days). After approval, download the CSV yourself and use "
                    "Manual import; check the licence terms permit this use.",
        enabled_by_default=False, runs_in_search=False),
    ProviderInfo(
        name="chamber_freezone_directories", display_name="Chamber & free-zone member directories",
        availability=ProviderAvailability.MANUAL_ONLY,
        description="Dubai Chamber, DMCC, JAFZA, DSO/Dtec and similar member or tenant directories.",
        coverage="Varies by directory.", source_url="",
        limitations="Not automated: these are interactive or login-gated web applications whose terms were not "
                    "confirmed to permit automated collection. Use the Directory page importer only for a "
                    "public page whose terms you have checked, or export/copy entries into a CSV.",
        enabled_by_default=False, runs_in_search=False),
    ProviderInfo(
        name="linkedin", display_name="LinkedIn",
        availability=ProviderAvailability.DISABLED_POLICY,
        description="Professional network.", coverage="—", source_url="https://www.linkedin.com/legal/user-agreement",
        limitations="LinkedIn's User Agreement prohibits scraping. ATLAS never fetches LinkedIn; it only records a "
                    "company's LinkedIn page link when the company's own website publishes it.",
        enabled_by_default=False, runs_in_search=False),
    ProviderInfo(
        name="opencorporates", display_name="OpenCorporates API",
        availability=ProviderAvailability.REQUIRES_SETUP,
        description="Global company registry aggregator.", coverage="Limited UAE coverage.",
        source_url="https://api.opencorporates.com/",
        limitations="Requires an API key (free only for approved public-benefit projects). Not implemented.",
        enabled_by_default=False, runs_in_search=False),
    ProviderInfo(
        name="google_places", display_name="Google Places API",
        availability=ProviderAvailability.DISABLED_PAID,
        description="Business listings.", coverage="Broad.", source_url="https://developers.google.com/maps",
        limitations="Paid API (billing account required). Not implemented, to keep the core workflow free.",
        enabled_by_default=False, runs_in_search=False),
    ProviderInfo(
        name="apollo_hunter", display_name="Apollo / Hunter (contact databases)",
        availability=ProviderAvailability.DISABLED_PAID,
        description="Commercial contact databases.", coverage="—", source_url="",
        limitations="Paid subscriptions and guessed-email style data; deliberately not implemented.",
        enabled_by_default=False, runs_in_search=False),
]


def all_infos() -> list[ProviderInfo]:
    return [p.info for p in IMPLEMENTED] + NOT_AUTOMATED


def get_provider(name: str) -> DiscoveryProvider | None:
    return next((p for p in IMPLEMENTED if p.name == name), None)


def sync_to_db(conn: sqlite3.Connection) -> None:
    for info in all_infos():
        provider_db.register_provider(
            conn, name=info.name, display_name=info.display_name, availability=info.availability.value,
            description=info.description, coverage=info.coverage, source_url=info.source_url,
            limitations=info.limitations, enabled_default=info.enabled_by_default,
        )


def search_providers(conn: sqlite3.Connection, criteria: SearchCriteria) -> list[tuple[DiscoveryProvider, str | None]]:
    """Providers to run for a search, each with a skip reason (None = run)."""
    out = []
    for p in IMPLEMENTED:
        if not p.info.runs_in_search:
            continue
        if criteria.providers and p.name not in criteria.providers:
            continue
        if not criteria.providers and not provider_db.is_enabled(conn, p.name):
            out.append((p, "disabled in Settings"))
            continue
        ok, why = p.applicable(criteria)
        out.append((p, None if ok else why))
    return out
