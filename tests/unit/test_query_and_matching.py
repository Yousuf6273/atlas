"""Query parser and entity-resolution rules."""

from __future__ import annotations

from atlas.models.company import CandidateCompany, CandidateLocation
from atlas.models.enums import BusinessType, Industry, SizeBand
from atlas.query import parse_query
from atlas.resolve.matcher import CandidateFeatures, compare, decide

# ------------------------------------------------------------------ parser (brief's example queries)

def test_parse_small_mech_robotics_dubai():
    p = parse_query("Find 100 small mechanical engineering and robotics companies in Dubai that might accept a summer intern.")
    c = p.criteria
    assert c.desired_count == 100
    assert [a.key for a in c.areas] == ["dubai"]
    assert set(c.industries) == {Industry.MECHANICAL, Industry.ROBOTICS}
    assert set(c.size_preference) == {SizeBand.MICRO, SizeBand.SMALL}
    assert c.internship_type == "summer"


def test_parse_ai_startups_difc_python():
    c = parse_query("Find AI startups in DIFC where I could help with Python automation.").criteria
    assert c.industries == [Industry.AI_ML]
    assert [a.key for a in c.areas] == ["difc"]
    assert "python" in c.skills and "python_automation" in c.task_preferences
    assert Industry.ROBOTICS not in c.industries  # "automation" inside the skills clause is not an industry


def test_parse_aerospace_uae_engineering_teams():
    c = parse_query("Find aerospace-related companies in the UAE with engineering teams.").criteria
    assert c.industries == [Industry.AEROSPACE] and c.require_engineering_team
    assert [a.key for a in c.areas] == ["uae"]


def test_parse_manufacturing_excel_supplier():
    p = parse_query("Find 50 manufacturing companies where I could offer Excel automation or supplier research.")
    c = p.criteria
    assert c.industries == [Industry.MANUFACTURING] and c.desired_count == 50
    assert c.task_preferences == ["supplier_research", "spreadsheet_automation"]
    assert any("defaulted to Dubai" in n for n in p.notes)


def test_parse_exclusions():
    c = parse_query("drone companies in Dubai Silicon Oasis excluding recruitment agencies").criteria
    assert BusinessType.RECRUITMENT_AGENCY in c.excluded_business_types
    c2 = parse_query("fintech and quant firms in DIFC, not crypto").criteria
    assert Industry.FINTECH in c2.industries and "crypto" in c2.excluded_keywords


def test_parse_unknown_words_reported_and_modifiers_kept():
    p = parse_query("marine engineering firms in Sharjah")
    assert "marine" in p.criteria.keywords
    assert parse_query("blorbic widgets").ambiguity > 0


# ------------------------------------------------------------------ entity resolution

def cand(name, website=None, phone=None, lat=None, lon=None, provider="osm_overpass", ext="x"):
    loc = CandidateLocation(latitude=lat, longitude=lon) if lat is not None else None
    return CandidateCompany(name=name, provider=provider, external_id=ext, website=website, phone=phone, location=loc)


def row(id_, name, domain=None, phones=(), coords=()):
    from atlas.resolve.normalize import normalize_name

    return {"id": id_, "name": name, "normalized_name": normalize_name(name), "domain": domain, "phones": list(phones),
            "coords": list(coords)}


def test_same_domain_merges_despite_legal_suffix():
    f = CandidateFeatures.of(cand("Acme Robotics FZ-LLC", website="https://www.acme-robotics.example"))
    assert compare(f, row(1, "ACME Robotics", "acme-robotics.example"))[0] == "merge"


def test_same_domain_different_name_goes_to_review():
    f = CandidateFeatures.of(cand("Acme Aviation Training", website="https://acme.example"))
    action, _s, _r, rel = compare(f, row(1, "Acme Holdings", "acme.example"))
    assert action == "review" and rel == "possible_parent_subsidiary"


def test_same_name_far_apart_is_possible_branch():
    f = CandidateFeatures.of(cand("Gulf Precision Engineering", lat=25.20, lon=55.27))
    action, _s, _r, rel = compare(f, row(1, "Gulf Precision Engineering LLC", coords=[(24.45, 54.38)]))
    assert action == "review" and rel == "possible_branch"


def test_same_name_close_merges():
    f = CandidateFeatures.of(cand("Gulf Precision Engineering", lat=25.2000, lon=55.2700))
    assert compare(f, row(1, "Gulf Precision Engineering L.L.C.", coords=[(25.2003, 55.2702)]))[0] == "merge"


def test_different_domains_never_auto_merge():
    f = CandidateFeatures.of(cand("Siemens Energy", website="https://siemens-energy.example"))
    action, *_ = compare(f, row(1, "Siemens Energy LLC", "siemens.example"))
    assert action == "review"


def test_parent_subsidiary_names_reviewed_not_merged():
    f = CandidateFeatures.of(cand("Falcon Robotics Systems"))
    action, *_rest, rel = compare(f, row(1, "Falcon Robotics"))
    assert action in ("review",)


def test_unrelated_names_new():
    f = CandidateFeatures.of(cand("Desert Data Labs"))
    assert decide(f, [row(1, "Gulf Precision Engineering")]).action == "new"


def test_phone_match_merges():
    f = CandidateFeatures.of(cand("Orbit Avionics Services", phone="+971 4 321 0009"))
    assert compare(f, row(1, "Orbit Avionics", phones=["+97143210009"]))[0] == "merge"


def test_ambiguous_multiple_merges_become_review():
    f = CandidateFeatures.of(cand("Gulf Precision Engineering"))
    d = decide(f, [row(1, "Gulf Precision Engineering LLC"), row(2, "Gulf Precision Engineering FZE")])
    assert d.action == "review" and len(d.reviews) == 2
