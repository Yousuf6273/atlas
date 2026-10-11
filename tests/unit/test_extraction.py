"""HTML parsing, URL handling, contact and signal extraction on SYNTHETIC pages."""

from __future__ import annotations

from atlas.crawl.html import parse_html
from atlas.crawl.urls import canonicalize, classify, should_skip
from atlas.extract.contacts import (
    classify_local_part,
    extract_contacts,
    extract_people,
    find_emails,
)
from atlas.extract.signals import extract_claims, extract_structured
from atlas.models.enums import ContactChannel, RoleCategory
from tests.conftest import synthetic_html

T = "2026-10-11T00:00:00+00:00"
R = "synthetic-robotics.example"


def page(name: str, url: str):
    return parse_html(synthetic_html(name), url)


def test_url_canonicalization_and_classification():
    assert canonicalize("HTTPS://Example.com:443/About/?utm_source=x&b=2&a=1#top") == "https://example.com/About?a=1&b=2"
    assert canonicalize("https://example.com/index.html") == "https://example.com/"
    assert classify("https://x.example/careers/graduate-internships") == "internship"
    assert classify("https://x.example/about-us") == "about"
    assert classify("https://x.example/p?id=1", "Contact us") == "contact"
    assert should_skip("https://x.example/files/brochure.pdf")
    assert should_skip("https://x.example/ar/about")
    assert should_skip("https://x.example/wp-login.php")
    assert not should_skip("https://x.example/services")


def test_parse_home_page_structure():
    p = page(f"{R}/index.html", f"https://{R}/")
    assert p.og_site_name == "Synthetic Robotics Testco"
    assert "robotic welding cells" in p.meta_description
    assert any(i.get("name") == "Synthetic Robotics Testco FZ-LLC" for i in p.jsonld)
    assert "+97143210001" in p.tels
    assert "© 2026" in p.footer_text
    assert any("linkedin.com/company" in link.url for link in p.links)
    assert not p.is_bot_challenge and not p.is_placeholder and not p.needs_js
    # nav text is excluded from main blocks
    assert not any(b == "Home" for b in p.blocks)


def test_bot_challenge_and_placeholder_detection():
    challenge = parse_html("<html><head><title>Vercel Security Checkpoint</title></head><body>Verifying</body></html>", "u")
    assert challenge.is_bot_challenge
    parked = page("synthetic-parked.example/index.html", "https://synthetic-parked.example/")
    assert parked.is_placeholder


def test_email_finding_filters_and_deobfuscation():
    text = "Write to info [at] acme [dot] ae or sales@acme.ae. Not emails: logo@2x.png, name@example.com"
    emails = [e for e, _ in find_emails(text)]
    assert "sales@acme.ae" in emails and "info@acme.ae" in emails
    assert not any("png" in e or "example.com" in e for e in emails)


def test_local_part_classification():
    assert classify_local_part("careers") == (RoleCategory.HR_TALENT, True)
    assert classify_local_part("info") == (RoleCategory.GENERAL, True)
    assert classify_local_part("hr-dubai") == (RoleCategory.HR_TALENT, True)
    assert classify_local_part("jane.roe")[1] is False


def test_people_extraction_from_team_page():
    people = extract_people(page(f"{R}/team.html", f"https://{R}/team"))
    names = {(p.name, p.role_category) for p in people}
    assert ("Jane Roe", RoleCategory.FOUNDER_EXEC) in names
    assert ("Omar Testname", RoleCategory.ENGINEERING_LEAD) in names
    assert not any(p.name == "Read more" for p in people)


def test_contacts_from_contact_and_careers_pages():
    cp = extract_contacts(page(f"{R}/contact.html", f"https://{R}/contact"), "contact", R, T)
    kinds = {(c.channel, c.value) for c in cp}
    assert (ContactChannel.EMAIL, "info@synthetic-robotics.example") in kinds
    assert (ContactChannel.CONTACT_FORM, f"https://{R}/contact") in kinds
    assert any(c.channel == ContactChannel.PHONE and c.value == "+97143210001" for c in cp)
    assert all(c.source_url == f"https://{R}/contact" for c in cp)
    careers = extract_contacts(page(f"{R}/careers.html", f"https://{R}/careers"), "careers", R, T)
    hr = [c for c in careers if c.channel == ContactChannel.EMAIL]
    assert hr and hr[0].value == "careers@synthetic-robotics.example" and hr[0].role_category == RoleCategory.HR_TALENT
    assert any(c.channel == ContactChannel.CAREERS_PAGE for c in careers)


def test_no_guessed_emails_for_named_people():
    team = extract_contacts(page(f"{R}/team.html", f"https://{R}/team"), "team", R, T)
    assert not any(c.channel == ContactChannel.EMAIL for c in team)  # names only; nothing constructed
    assert any(c.channel == ContactChannel.NAMED_PERSON and c.name == "Jane Roe" for c in team)


def test_signal_claims_have_provenance():
    p = page(f"{R}/careers.html", f"https://{R}/careers")
    claims = extract_claims(p, "careers", T, company_name="Synthetic Robotics Testco")
    types = {c.claim_type for c in claims}
    assert {"internship_signal", "eligibility_school_ok", "hiring", "hiring_role"} <= types
    for c in claims:
        assert c.evidence.source_url == f"https://{R}/careers" and c.evidence.snippet
    roles = {c.value for c in claims if c.claim_type == "hiring_role"}
    assert "Junior Data Analyst" in roles and "Document Controller" in roles


def test_eligibility_university_and_age():
    p = parse_html(synthetic_html("synthetic-aero.example/careers/graduate-internships.html"),
                   "https://synthetic-aero.example/careers/graduate-internships")
    claims = extract_claims(p, "internship", T)
    assert any(c.claim_type == "eligibility_university_only" for c in claims)
    assert [c.value for c in claims if c.claim_type == "eligibility_min_age"] == ["18"]


def test_size_activity_and_structured():
    about = page(f"{R}/about.html", f"https://{R}/about")
    claims = extract_claims(about, "about", T, company_name="Synthetic Robotics Testco")
    sizes = [c.value for c in claims if c.claim_type == "size_statement"]
    assert any(v.endswith("|40") for v in sizes) and any(v.endswith("|25") for v in sizes)
    assert not any(v.endswith("|2015") for v in sizes)  # years are not headcounts
    assert any(c.claim_type == "engineering_team" for c in claims)
    assert any(c.claim_type == "activity" for c in claims)
    home = page(f"{R}/index.html", f"https://{R}/")
    st = extract_structured(home, T)
    assert any(c.claim_type == "structured_name" and c.value == "Synthetic Robotics Testco FZ-LLC" for c in st)
    hc = extract_claims(home, "home", T)
    assert any(c.claim_type == "copyright_year" and c.value == "2026" for c in hc)
    assert any(c.claim_type == "dated_content" and c.value == "2026-03-14" for c in hc)
    assert any(c.claim_type == "location_mention" and c.value == "dubai_silicon_oasis" for c in hc)


def test_negative_lookalike_claims():
    p = page("synthetic-rentals.example/index.html", "https://synthetic-rentals.example/")
    claims = extract_claims(p, "home", T)
    assert any(c.claim_type == "industry_negative" and c.value.startswith("automotive") for c in claims)
    assert any(c.claim_type == "business_type_signal" and c.value.startswith("retail_consumer") for c in claims)


# ------------------------------------------------------------------ regressions from the first live run

def _claims(html_body: str, page_type: str = "careers", url: str = "https://synthetic-co.example/careers"):
    return extract_claims(parse_html(f"<html><body>{html_body}</body></html>", url), page_type, T)


def test_audience_phrase_is_not_a_university_requirement():
    cl = _claims("<p>Whether you are a seasoned professional or a recent graduate, we support your career.</p>"
                 "<p>Business Operations Intern</p>")
    assert not any(c.claim_type == "eligibility_university_only" for c in cl)
    cl = _claims("<p>Our internship is open to undergraduate students only.</p>")
    assert any(c.claim_type == "eligibility_university_only" for c in cl)


def test_site_terms_age_is_not_an_internship_rule():
    cl = _claims("<p>You must be 18 years or older to use this website.</p>", page_type="home",
                 url="https://synthetic-co.example/")
    assert not any(c.claim_type == "eligibility_min_age" for c in cl)
    cl = _claims("<p>Applicants must be 18 years or older.</p>")
    assert [c.value for c in cl if c.claim_type == "eligibility_min_age"] == ["18"]


def test_foreign_internship_not_counted_as_uae_programme():
    cl = _claims("<h1>Python Developer Internship In Thane, Navi Mumbai</h1>")
    assert any(c.claim_type == "internship_abroad" for c in cl)
    assert not any(c.claim_type == "internship_signal" for c in cl)


def test_slogan_headings_are_not_offerings():
    page = parse_html("<html><body><h2>Your AI-Powered Growth System</h2><h2>We make AI easy</h2>"
                      "<h2>Computer Vision Inspection</h2></body></html>", "https://synthetic-co.example/services")
    offerings = [c.value for c in extract_claims(page, "services", T) if c.claim_type == "offering"]
    assert offerings == ["Computer Vision Inspection"]


def test_injected_instructions_produce_no_claims():
    p = page("synthetic-trading.example/about.html", "https://synthetic-trading.example/about")
    cl = extract_claims(p, "about", T)
    assert not any(c.claim_type == "size_statement" for c in cl)
    assert any(c.claim_type == "prompt_injection_text" for c in cl)


def test_school_students_in_news_is_not_an_internship_route():
    cl = _claims("<p>UAE school students put their engineering skills to the test at a university competition.</p>",
                 page_type="news", url="https://synthetic-co.example/news/1")
    assert not any(c.claim_type == "eligibility_school_ok" for c in cl)
    cl = _claims("<p>We offer work experience placements for high school students every summer.</p>",
                 page_type="about", url="https://synthetic-co.example/about")
    assert any(c.claim_type == "eligibility_school_ok" for c in cl)


def test_weak_business_type_phrases_are_marked():
    cl = _claims("<p>We produce the ads and a leading manufacturer of intercoms is our partner.</p>", page_type="home",
                 url="https://synthetic-co.example/")
    vals = [c.value for c in cl if c.claim_type == "business_type_signal"]
    assert "manufacturer|~manufacturer of" in vals or "manufacturer|~leading manufacturer" in vals
    assert not any(v.startswith("manufacturer|we produce") for v in vals)
