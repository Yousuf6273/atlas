"""Live checks against real public services. Skipped unless ATLAS_LIVE_TESTS=1.

They assert only on structure (non-empty, well-formed), never on specific
companies, because real-world data changes.
"""

from __future__ import annotations

import logging

import pytest

from atlas.geo.areas import AREAS
from atlas.models.enums import Industry
from atlas.models.search import SearchCriteria
from atlas.net.cache import HttpCache
from atlas.net.fetcher import Fetcher
from atlas.providers.base import ProviderContext
from atlas.providers.gleif import GleifProvider
from atlas.providers.osm_overpass import OverpassProvider
from atlas.providers.wikidata import WikidataProvider

pytestmark = pytest.mark.live


@pytest.fixture
async def live_ctx(cfg):
    cache = HttpCache(cfg.paths.cache_db_path)
    async with Fetcher(cfg.http, cache) as f:
        yield ProviderContext(cfg=cfg, fetcher=f, log=logging.getLogger("live"))


async def test_wikidata_live(live_ctx):
    ok, detail = await WikidataProvider().health_check(live_ctx)
    assert ok, detail
    res = await WikidataProvider().discover(
        SearchCriteria(industries=[Industry.AEROSPACE, Industry.SOFTWARE], areas=[AREAS["dubai"]]), live_ctx)
    assert res.scanned > 100
    assert all(c.source_url.startswith("https://www.wikidata.org/wiki/Q") for c in res.candidates)


async def test_overpass_live_small_zone(live_ctx):
    res = await OverpassProvider().discover(
        SearchCriteria(industries=list(Industry), areas=[AREAS["dubai_silicon_oasis"]]), live_ctx)
    assert res.scanned > 0 or res.partial  # servers may be overloaded; partial must then be reported
    assert all(c.external_id.split("/")[0] in {"node", "way", "relation"} for c in res.candidates)


async def test_gleif_live(live_ctx):
    ok, detail = await GleifProvider().health_check(live_ctx)
    assert ok, detail
