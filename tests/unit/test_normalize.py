from atlas.resolve.normalize import (
    clean_text,
    has_branch_marker,
    is_company_domain,
    normalize_address,
    normalize_name,
    normalize_phone,
    normalize_url,
    registrable_domain,
    website_root,
)


def test_normalize_name_strips_legal_forms():
    assert normalize_name("Acme Robotics L.L.C.") == "acme robotics"
    assert normalize_name("ACME ROBOTICS FZ-LLC") == "acme robotics"
    assert normalize_name("Acme Robotics FZCO (Branch)") == "acme robotics"
    assert normalize_name("Acme Robotics DMCC") == "acme robotics"
    assert normalize_name("Al Futtaim Engineering & Technologies LLC") == "al futtaim engineering and technologies"
    assert normalize_name("Société Générale") == "societe generale"


def test_branch_marker():
    assert has_branch_marker("Acme Engineering (Dubai Branch)")
    assert not has_branch_marker("Acme Engineering")


def test_registrable_domain():
    assert registrable_domain("https://www.shop.acme.co.uk/path?q=1") == "acme.co.uk"
    assert registrable_domain("acme.ae") == "acme.ae"
    assert registrable_domain("https://sub.acme.co.ae/") == "acme.co.ae"  # co.ae is a public suffix; com.ae is not
    assert registrable_domain("http://192.168.0.1/") is None
    assert registrable_domain("not a url") is None


def test_normalize_url_and_root():
    assert normalize_url("www.acme.ae") == "https://www.acme.ae"
    assert normalize_url("mailto:x@acme.ae") is None
    assert normalize_url("javascript:void(0)") is None
    assert website_root("https://WWW.Acme.ae/about?x=1") == "https://www.acme.ae/"


def test_company_domain_filter():
    assert not is_company_domain("facebook.com")
    assert not is_company_domain("linktr.ee")
    assert is_company_domain("acme.ae")


def test_phone_normalisation():
    assert normalize_phone("04 123 4567") == "+97141234567"
    assert normalize_phone("+971 50 123 4567") == "+971501234567"
    assert normalize_phone("123") is None


def test_address_and_text():
    assert normalize_address("Office 5, Bldg 3, Sheikh Zayed Rd., P.O. Box 123, U.A.E.") == \
        "office 5 building 3 sheikh zayed road po box 123 uae"
    assert clean_text("a​ b\x00\n c") == "a b c"
