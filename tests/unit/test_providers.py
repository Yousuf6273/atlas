"""Discovery providers against mocked API responses (SYNTHETIC records only)."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from atlas.assess.relevance import quick_relevance, scan
from atlas.geo.areas import AREAS
from atlas.models.enums import Industry
from atlas.models.search import SearchCriteria
from atlas.net.fetcher import Fetcher
from atlas.providers.base import ProviderContext
from atlas.providers.gleif import GleifProvider, lookup_legal_entity
from atlas.providers.manual import (
    map_columns,
    parse_tabular,
    rows_to_candidates,
    urls_to_candidates,
)
from atlas.providers.osm_overpass import (
    OverpassProvider,
    _area_bboxes,
    build_query,
    element_to_candidate,
)
from atlas.providers.searxng import SearxngProvider
from atlas.providers.wikidata import WikidataProvider

OSM_ELEMENTS = {"elements": [
    {"type": "node", "id": 1, "lat": 25.12, "lon": 55.38,
     "tags": {"name": "Synthetic Robotics Testco", "office": "company", "website": "synthetic-robotics.example",
              "email": "info@synthetic-robotics.example", "phone": "+971 4 000 0000"}},
    {"type": "way", "id": 2, "center": {"lat": 25.13, "lon": 55.23},
     "tags": {"name": "Synthetic Precision Machining Workshop", "craft": "metal_construction"}},
    {"type": "node", "id": 3, "lat": 25.2, "lon": 55.27, "tags": {"name": "Synthetic Embassy", "office": "diplomatic"}},
    {"type": "node", "id": 4, "lat": 25.2, "lon": 55.27, "tags": {"name": "Synthetic Rent A Car", "office": "company"}},
    {"type": "node", "id": 5, "lat": 25.2, "lon": 55.27, "tags": {"office": "company"}},
]}


@pytest.fixture
async def ctx(cfg):
    async with Fetcher(cfg.http, None) as f:
        yield ProviderContext(cfg=cfg, fetcher=f, log=logging.getLogger("test"))


def test_relevance_negative_terms_suppress_lookalikes():
    crit = SearchCriteria(industries=[Industry.AUTOMOTIVE])
    assert quick_relevance("Synthetic Rent A Car LLC", criteria=crit).score == 0
    assert quick_relevance("Synthetic Electric Vehicle Engineering", criteria=crit).score > 0.4
    hits = scan("We design robotic arms. We also offer robotic process automation consulting.")
    assert any(h.industry == Industry.ROBOTICS and h.term.lower() == "robotic" for h in hits)


def test_relevance_excluded_keyword():
    crit = SearchCriteria(industries=[Industry.ROBOTICS], excluded_keywords=["training"])
    r = quick_relevance("Synthetic Robotics Training Centre", criteria=crit)
    assert r.excluded and r.score == 0


def test_overpass_query_and_tiles():
    q = build_query((25.0, 55.0, 25.1, 55.1), 25)
    assert "[bbox:25.0,55.0,25.1,55.1]" in q and 'nwr["office"]["name"]' in q and "out tags center" in q
    crit = SearchCriteria(areas=[AREAS["dubai"]])
    tiles = _area_bboxes(crit, 0.12, 60)
    assert 20 <= len(tiles) <= 60
    zone_tiles = _area_bboxes(SearchCriteria(areas=[AREAS["difc"]]), 0.12, 60)
    assert len(zone_tiles) == 1


def test_osm_element_conversion_and_exclusions():
    c = element_to_candidate(OSM_ELEMENTS["elements"][0], "2026-01-01T00:00:00+00:00")
    assert c.website == "https://synthetic-robotics.example" and c.external_id == "node/1"
    assert c.location.latitude == 25.12 and c.source_url.endswith("/node/1")
    assert element_to_candidate(OSM_ELEMENTS["elements"][2], "t") is None  # diplomatic excluded
    assert element_to_candidate(OSM_ELEMENTS["elements"][4], "t") is None  # unnamed


@respx.mock
async def test_overpass_discover_filters_and_reports_partial(ctx):
    ctx.cfg.discovery.overpass_endpoints = ["https://ovp1.example/api/interpreter", "https://ovp2.example/api/interpreter"]
    ctx.cfg.discovery.overpass_tile_deg = 0.5  # 2 tiles for the DIFC-sized test area below
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=OSM_ELEMENTS)

    respx.post("https://ovp1.example/api/interpreter").mock(side_effect=handler)
    respx.post("https://ovp2.example/api/interpreter").mock(side_effect=handler)
    crit = SearchCriteria(industries=[Industry.ROBOTICS, Industry.MECHANICAL], areas=[AREAS["difc"]])
    res = await OverpassProvider().discover(crit, ctx)
    names = {c.name for c in res.candidates}
    assert names == {"Synthetic Robotics Testco", "Synthetic Precision Machining Workshop"}
    assert not res.partial


@respx.mock
async def test_overpass_failover_and_partial_coverage(ctx, monkeypatch):
    import atlas.providers.osm_overpass as mod

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
    ctx.cfg.discovery.overpass_endpoints = ["https://ovp1.example/api/interpreter", "https://ovp2.example/api/interpreter"]
    respx.post("https://ovp1.example/api/interpreter").mock(return_value=httpx.Response(504))
    respx.post("https://ovp2.example/api/interpreter").mock(return_value=httpx.Response(429))
    res = await OverpassProvider().discover(SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["difc"]]), ctx)
    assert res.partial and res.candidates == [] and res.errors


WIKIDATA_JSON = {"results": {"bindings": [
    {"org": {"value": "http://www.wikidata.org/entity/Q900000001"}, "orgLabel": {"value": "Synthetic Aerospace Testco"},
     "orgDescription": {"value": "aircraft maintenance company"}, "website": {"value": "https://synthetic-aero.example"},
     "typeLabel": {"value": "company"}, "industryLabel": {"value": "aerospace"}, "hqLabel": {"value": "Dubai"},
     "employees": {"value": "1200"}},
    {"org": {"value": "http://www.wikidata.org/entity/Q900000002"}, "orgLabel": {"value": "Synthetic Hotel Testco"},
     "orgDescription": {"value": "hotel"}, "typeLabel": {"value": "business"}, "hqLabel": {"value": "Dubai"}},
    {"org": {"value": "http://www.wikidata.org/entity/Q900000003"}, "orgLabel": {"value": "Synthetic Abu Dhabi Aero"},
     "orgDescription": {"value": "aerospace manufacturer"}, "typeLabel": {"value": "company"},
     "hqLabel": {"value": "Abu Dhabi"}, "coord": {"value": "Point(54.38 24.45)"}},
]}}


@respx.mock
async def test_wikidata_discover_area_and_industry(ctx):
    respx.get(url__startswith="https://query.wikidata.org/sparql").mock(return_value=httpx.Response(
        200, json=WIKIDATA_JSON, headers={"content-type": "application/sparql-results+json"}))
    crit = SearchCriteria(industries=[Industry.AEROSPACE], areas=[AREAS["dubai"]])
    res = await WikidataProvider().discover(crit, ctx)
    assert [c.name for c in res.candidates] == ["Synthetic Aerospace Testco"]
    c = res.candidates[0]
    assert c.employees == 1200 and "P1128" in c.employees_source and c.website == "https://synthetic-aero.example"


GLEIF_JSON = {"data": [{"id": "SYNTHETICLEI000000001", "attributes": {
    "lei": "SYNTHETICLEI000000001",
    "entity": {"legalName": {"name": "Synthetic Robotics Testco LLC"}, "otherNames": [], "status": "ACTIVE",
               "legalAddress": {"city": "Dubai", "region": "AE-DU", "addressLines": ["Synthetic St"]}},
    "registration": {"status": "ISSUED"}}}]}


@respx.mock
async def test_gleif_lookup_and_discover(ctx):
    respx.get(url__startswith="https://api.gleif.org/api/v1/lei-records").mock(return_value=httpx.Response(
        200, json=GLEIF_JSON, headers={"content-type": "application/vnd.api+json"}))
    m = await lookup_legal_entity(ctx.fetcher, ctx.cfg.discovery.gleif_endpoint, "Synthetic Robotics Testco")
    assert m and m.lei == "SYNTHETICLEI000000001" and m.similarity >= 90
    res = await GleifProvider().discover(SearchCriteria(industries=[Industry.ROBOTICS], areas=[AREAS["dubai"]]), ctx)
    assert [c.name for c in res.candidates] == ["Synthetic Robotics Testco LLC"]


@respx.mock
async def test_searxng_requires_config_and_filters(ctx):
    res = await SearxngProvider().discover(SearchCriteria(industries=[Industry.ROBOTICS]), ctx)
    assert res.errors and not res.candidates
    ctx.cfg.discovery.searxng_url = "http://searx.test:8888"
    respx.get(url__startswith="http://searx.test:8888/search").mock(return_value=httpx.Response(200, json={"results": [
        {"url": "https://www.synthetic-robotics.example/about", "title": "Synthetic Robotics | Home", "content": "x"},
        {"url": "https://www.linkedin.com/company/x", "title": "LinkedIn"},
        {"url": "https://www.bayt.com/jobs", "title": "Jobs"},
    ]}))
    res = await SearxngProvider().discover(SearchCriteria(industries=[Industry.ROBOTICS]), ctx)
    assert [c.website for c in res.candidates] == ["https://www.synthetic-robotics.example/"]


def test_csv_import_mapping_and_problems():
    data = "Company Name,Website,Emirate,Sector\nSynthetic Robotics Testco,synthetic-robotics.example,Dubai,Robotics\n,,Dubai,\n"
    rows = parse_tabular(data.encode(), "list.csv")
    assert map_columns(rows[0].keys())["name"] == "Company Name"
    cands, problems = rows_to_candidates(rows, source_label="list.csv")
    assert len(cands) == 1 and cands[0].website == "https://synthetic-robotics.example"
    assert problems and "row 3" in problems[0]


def test_url_list_import():
    cands, problems = urls_to_candidates("https://synthetic-a.example\nwww.facebook.com/x\nnot a url\nsynthetic-a.example/about")
    assert [c.external_id for c in cands] == ["synthetic-a.example"]
    assert len(problems) == 2


def test_wikidata_zone_filter_needs_zone_level_evidence():
    from atlas.providers.wikidata import _in_requested_area

    crit = SearchCriteria(areas=[AREAS["dubai_silicon_oasis"]])
    assert _in_requested_area(crit, {"dubai"}, None) is False
    assert _in_requested_area(crit, {"dubai silicon oasis"}, None) is True
    assert _in_requested_area(crit, set(), (25.1217, 55.3855)) is True
    assert _in_requested_area(SearchCriteria(areas=[AREAS["dubai"]]), {"dubai"}, None) is True


@respx.mock
async def test_directory_page_importer_respects_robots_and_filters(ctx):
    from atlas.providers.base import ProviderError
    from atlas.providers.manual import directory_page_candidates

    respx.get("https://directory.example/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    respx.get("https://directory.example/members").mock(return_value=httpx.Response(200, headers={"content-type": "text/html"}, text=(
        "<html><body><a href='https://www.synthetic-robotics.example/'>Synthetic Robotics Testco</a>"
        "<a href='https://www.linkedin.com/company/x'>LinkedIn</a><a href='/about'>About the directory</a>"
        "<a href='https://synthetic-aero.example/contact'>Visit website</a></body></html>")))
    cands, notes = await directory_page_candidates("https://directory.example/members", ctx)
    assert [(c.name, c.website) for c in cands] == [
        ("Synthetic Robotics Testco", "https://www.synthetic-robotics.example/"),
        ("synthetic-aero.example", "https://synthetic-aero.example/")]
    respx.get("https://blocked-directory.example/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    with pytest.raises(ProviderError):
        await directory_page_candidates("https://blocked-directory.example/list", ctx)
