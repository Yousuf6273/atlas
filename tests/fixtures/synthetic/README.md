# SYNTHETIC TEST FIXTURES — NOT REAL COMPANIES

Every company, person, address, phone number and website in this directory is
fictional. Domains use the reserved `.example` TLD (RFC 2606) so they can never
resolve to a real site. Phone numbers use the unallocated-looking pattern
+971 4 321 0xxx and must not be called.

They exist only to exercise ATLAS's crawler, extractors and assessment engines
in automated tests (served through mocked HTTP). They must never be imported
into a real ATLAS database or shown as prospecting results.

| Site | Scenario it tests |
|------|-------------------|
| synthetic-robotics.example | small robotics firm in Dubai Silicon Oasis; school students welcome; named team; vacancies → *qualified*, *confirmed opportunity* |
| synthetic-aero.example | large aerospace MRO; internship needs university enrolment and age 18+ → *explicitly ineligible* |
| synthetic-rentals.example | "rent a car" look-alike for automotive searches → *rejected* |
| synthetic-parked.example | parked "domain for sale" page → *rejected (inactive)* |
| synthetic-blocked.example | robots.txt disallows everything → *not crawled, needs review* |
| synthetic-trading.example | industrial pump distributor; prompt-injection text on About page |
