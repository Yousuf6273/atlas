"""Industry and business-type vocabulary.

Each industry has weighted *terms* (regular expressions matched on word
boundaries). ``strong`` terms are specific to the industry; ``medium`` terms
are suggestive; ``negative`` terms indicate a look-alike business that shares
keywords but is not a technical employer (e.g. car rental vs automotive
engineering). OSM tag hints and Wikidata industry labels map structured data to
industries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from atlas.models.enums import BusinessType, Industry


@dataclass(frozen=True)
class IndustryDef:
    key: Industry
    label: str
    strong: tuple[str, ...]
    medium: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()
    # Case-sensitive patterns (acronyms such as "AI").
    strong_cs: tuple[str, ...] = ()
    query_synonyms: tuple[str, ...] = ()
    wikidata_labels: tuple[str, ...] = ()
    osm_tags: tuple[str, ...] = ()  # "key=value" hints


INDUSTRIES: dict[Industry, IndustryDef] = {d.key: d for d in [
    IndustryDef(
        Industry.AEROSPACE, "Aerospace & aviation engineering",
        strong=(r"aerospace", r"aircraft", r"avionics?", r"airframes?", r"aero[- ]?structures?", r"aeronautic(al|s)",
                r"MRO", r"maintenance,? repair,? (and|&) overhaul", r"jet engines?", r"gas turbines?", r"satellites?",
                r"spacecraft", r"space (technology|systems|sector|industry|mission|missions|exploration)",
                r"launch vehicles?", r"UAVs?", r"unmanned (aerial|aircraft)", r"drones?", r"rotorcraft",
                r"helicopters?", r"flight simulat(or|ors|ion)", r"part[- ]145", r"EASA", r"GCAA", r"cubesats?",
                r"propulsion", r"flight control", r"air ?worthiness"),
        medium=(r"aviation", r"airlines?", r"airports?", r"flight training", r"ground support equipment",
                r"composites?", r"defen[cs]e"),
        negative=(r"travel agen(cy|ts?)", r"air tickets?", r"airline tickets?", r"holiday packages?",
                  r"freight forward(ing|er)", r"cargo services", r"drone (photography|videography|show|light show)",
                  r"aerial (photography|filming|videography)"),
        query_synonyms=("aerospace", "aviation", "aircraft", "aeronautical", "space", "space tech", "satellite",
                        "drone", "drones", "uav", "avionics", "mro"),
        wikidata_labels=("aerospace", "aviation", "aircraft", "airline", "space", "satellite", "defense industry",
                         "arms industry", "unmanned aerial vehicle"),
    ),
    IndustryDef(
        Industry.MECHANICAL, "Mechanical engineering",
        strong=(r"mechanical engineering", r"mechanical design", r"machining", r"CNC", r"precision engineering",
                r"metal fabrication", r"steel fabrication", r"fabrication (works?|workshop|services)",
                r"pumps?", r"compressors?", r"gearbox(es)?", r"hydraulics?", r"pneumatics?",
                r"HVAC (design|engineering|systems)", r"MEP (engineering|design|contractor|consultant)s?",
                r"thermal (engineering|management|systems)", r"finite element", r"FEA", r"CFD",
                r"heat exchangers?", r"pressure vessels?", r"valves?", r"mechanical (works|contracting|systems)",
                r"turbomachinery", r"engineering design"),
        medium=(r"engineering services", r"engineering solutions", r"engineering (company|firm|consultancy)",
                r"fabrication", r"piping", r"welding", r"HVAC", r"industrial equipment", r"maintenance services",
                r"engineers?"),
        negative=(r"(ac|a/c|aircon) (repair|cleaning|maintenance) (services? )?(in dubai|at home|near)",
                  r"home maintenance", r"handyman", r"cleaning services", r"plumbing services", r"pest control"),
        query_synonyms=("mechanical", "mechanical engineering", "hvac", "mep", "fabrication", "machining", "cnc",
                        "precision engineering"),
        wikidata_labels=("mechanical engineering", "engineering", "machine", "industrial machinery", "hvac"),
        osm_tags=("craft=metal_construction", "office=engineer", "office=engineering", "craft=hvac",
                  "craft=turner", "craft=welder"),
    ),
    IndustryDef(
        Industry.ROBOTICS, "Robotics & automation",
        strong=(r"robot(s|ic|ics)?", r"industrial automation", r"automation (systems|solutions|engineering)",
                r"PLCs?", r"SCADA", r"mechatronics?", r"cobots?", r"autonomous (systems|robots?|vehicles?|navigation)",
                r"motion control", r"machine vision", r"AGVs?", r"AMRs?", r"control systems?", r"actuators?",
                r"servo", r"DCS", r"process automation", r"building automation", r"IIoT", r"industrial IoT"),
        medium=(r"automation", r"sensors?", r"embedded systems?", r"IoT", r"internet of things", r"smart factory",
                r"industry 4\.0"),
        negative=(r"robotic process automation", r"\bRPA\b", r"home automation", r"smart home installation",
                  r"car wash", r"robotics (classes|courses|camp|workshops?) for kids", r"STEM (kits|classes)"),
        query_synonyms=("robotics", "robot", "robots", "automation", "industrial automation", "mechatronics", "plc",
                        "scada", "autonomous", "control systems"),
        wikidata_labels=("robotics", "automation", "industrial automation", "mechatronics"),
    ),
    IndustryDef(
        Industry.AUTOMOTIVE, "Automotive engineering",
        strong=(r"automotive engineering", r"vehicle (engineering|design|development|testing|dynamics)",
                r"electric vehicles?", r"\bEVs?\b", r"powertrains?", r"motorsport", r"autonomous driving",
                r"(auto|automotive|vehicle) parts? manufactur(ing|er)", r"\bOEMs?\b", r"chassis", r"EV charging",
                r"battery (systems|packs?|management)", r"BMS", r"vehicle telematics", r"homologation"),
        medium=(r"automotive", r"vehicles?", r"fleet (management|telematics)", r"automobiles?", r"trucks?",
                r"buses"),
        negative=(r"(car|vehicle) rental", r"rent a car", r"used cars?", r"car wash", r"car showrooms?",
                  r"car (repair|garage|detailing|polishing|recovery|tinting)", r"auto (repair|garage|spa)",
                  r"tyre (shop|services)", r"driving (school|institute|centre)", r"limousine", r"chauffeur"),
        query_synonyms=("automotive", "vehicle", "vehicles", "ev", "electric vehicle", "motorsport", "car"),
        wikidata_labels=("automotive industry", "automobile", "electric vehicle", "motorsport", "vehicle"),
    ),
    IndustryDef(
        Industry.MANUFACTURING, "Manufacturing",
        strong=(r"manufactur(ing|er|ers|es|e)", r"factor(y|ies)", r"production (lines?|facilit(y|ies)|plant)",
                r"(manufacturing|production) plant", r"injection mou?lding", r"extrusion", r"additive manufacturing",
                r"3D printing", r"assembly (lines?|plant)", r"casting", r"forging", r"sheet metal",
                r"industrial production", r"contract manufacturing"),
        medium=(r"industries", r"industrial", r"production", r"fabricat(e|ed|ion|ing)", r"plant", r"ISO 9001"),
        negative=(r"general trading", r"import (and|&) export", r"wholesale", r"supermarket", r"bakery",
                  r"confectionery", r"catering"),
        query_synonyms=("manufacturing", "manufacturer", "manufacturers", "factory", "factories", "industrial",
                        "production", "3d printing", "additive manufacturing"),
        wikidata_labels=("manufacturing", "industrial manufacturing", "manufacturer", "industry",
                         "steel industry", "aluminium industry", "chemical industry", "plastics industry"),
        osm_tags=("industrial=manufacturing", "man_made=works", "industrial=factory", "craft=metal_construction"),
    ),
    IndustryDef(
        Industry.AI_ML, "AI & machine learning",
        strong=(r"artificial intelligence", r"machine learning", r"deep learning", r"computer vision",
                r"natural language processing", r"\bNLP\b", r"generative AI", r"\bLLMs?\b", r"large language models?",
                r"neural networks?", r"AI[- ](powered|driven|based|native|first|platform|models?|solutions?|startup)",
                r"data science", r"predictive (analytics|models?|modell?ing)", r"reinforcement learning",
                r"speech recognition", r"conversational AI", r"MLOps"),
        medium=(r"intelligent automation", r"smart algorithms", r"analytics platform"),
        strong_cs=(r"\bAI\b",),
        query_synonyms=("ai", "artificial intelligence", "machine learning", "ml", "deep learning",
                        "computer vision", "nlp", "generative ai", "data science"),
        wikidata_labels=("artificial intelligence", "machine learning", "computer vision", "data science",
                         "AI-assisted software development"),
    ),
    IndustryDef(
        Industry.SOFTWARE, "Software engineering",
        strong=(r"software (development|engineering|company|products?|solutions?|house)", r"\bSaaS\b",
                r"(mobile|web) (app|application)s? development", r"app development", r"web development",
                r"cloud (platform|native|solutions|computing)", r"\bAPIs?\b", r"DevOps", r"full[- ]stack",
                r"software platform", r"product engineering", r"cyber ?security", r"ERP (solutions|implementation)",
                r"custom software", r"open[- ]source"),
        medium=(r"platform", r"digital transformation", r"IT (solutions|services)", r"apps?", r"software",
                r"technology company", r"tech startup", r"startups?"),
        negative=(r"(laptop|mobile|phone|computer) (repair|shop|store)", r"IT hardware trading",
                  r"printer (supplies|repair)", r"CCTV installation"),
        query_synonyms=("software", "software engineering", "saas", "app development", "software development",
                        "cybersecurity", "cyber security", "it services"),
        wikidata_labels=("software industry", "software development", "information technology", "computer security",
                         "cybersecurity", "software company", "technology company", "cloud computing",
                         "internet", "e-commerce", "video game industry"),
        osm_tags=("office=it", "office=software"),
    ),
    IndustryDef(
        Industry.DATA_ANALYTICS, "Data analytics",
        strong=(r"data analytics", r"business intelligence", r"\bBI\b (solutions|platform|dashboards?)",
                r"dashboards?", r"data engineering", r"big data", r"data (visuali[sz]ation|warehous(e|ing)|platform)",
                r"Power BI", r"Tableau", r"analytics (platform|solutions|services)", r"data[- ]driven insights",
                r"geospatial (analytics|data|intelligence)", r"market research"),
        medium=(r"analytics", r"insights", r"data"),
        query_synonyms=("data", "data analytics", "analytics", "business intelligence", "bi", "dashboards",
                        "data engineering"),
        wikidata_labels=("data analysis", "business intelligence", "analytics", "market research"),
    ),
    IndustryDef(
        Industry.FINTECH, "Financial technology",
        strong=(r"fintech", r"payments? (platform|gateway|solutions|processing|infrastructure)", r"digital wallets?",
                r"e-?wallets?", r"neobank", r"open banking", r"regtech", r"insurtech", r"lending platform",
                r"buy now,? pay later", r"\bBNPL\b", r"remittance (platform|app)", r"blockchain", r"tokeni[sz]ation",
                r"digital (banking|assets)", r"crypto(currency)? exchange", r"virtual assets?", r"card issuing"),
        medium=(r"payments?", r"financial (technology|services|platform)", r"banking", r"crypto"),
        negative=(r"forex signals", r"trading course", r"get rich", r"investment visa"),
        query_synonyms=("fintech", "financial technology", "payments", "blockchain", "crypto", "digital banking",
                        "insurtech", "regtech"),
        wikidata_labels=("fintech", "financial technology", "payment", "cryptocurrency", "blockchain",
                         "financial services", "bank", "cryptocurrency exchange"),
    ),
    IndustryDef(
        Industry.QUANT_FINANCE, "Quantitative finance & investment research",
        strong=(r"quantitative (trading|research|strateg(y|ies)|investment|finance|analysis)", r"quant(s)?\b",
                r"algorithmic trading", r"systematic (trading|strateg(y|ies)|investing)", r"hedge funds?",
                r"asset management", r"investment (research|management|analysis)", r"portfolio management",
                r"market making", r"proprietary trading", r"risk (analytics|modell?ing|management)",
                r"family office", r"private equity", r"venture capital", r"multi[- ]strategy", r"high[- ]frequency"),
        medium=(r"investments?", r"brokerage", r"wealth management", r"capital markets", r"trading",
                r"funds?"),
        negative=(r"real estate investment", r"property investment", r"forex signals", r"trading courses?",
                  r"investment visa", r"golden visa", r"general trading"),
        query_synonyms=("quant", "quantitative", "quantitative finance", "investment research", "hedge fund",
                        "hedge funds", "asset management", "trading", "algorithmic trading", "investment",
                        "private equity", "venture capital"),
        wikidata_labels=("asset management", "hedge fund", "investment management", "investment", "private equity",
                         "venture capital", "financial sector", "brokerage", "investment banking", "finance"),
        osm_tags=("office=financial", "office=financial_advisor"),
    ),
]}


@dataclass(frozen=True)
class BusinessTypeDef:
    """Self-description patterns for a business type.

    Patterns prefixed with ``~`` are *weak* (count half): phrases that also appear when a site merely
    mentions clients, partners or deliverables ("we produce the ads", "a leading manufacturer of …").
    """

    key: BusinessType
    patterns: tuple[str, ...]
    name_patterns: tuple[str, ...] = ()
    weight: float = 1.0


BUSINESS_TYPES: list[BusinessTypeDef] = [
    BusinessTypeDef(BusinessType.RECRUITMENT_AGENCY,
                    (r"recruitment (agency|services|solutions|consultants?)", r"staffing (agency|solutions|services)",
                     r"manpower (supply|services|agency)", r"head ?hunt(ing|ers?)", r"executive search",
                     r"talent (acquisition|sourcing) (services|solutions) for (our )?clients", r"placement agency"),
                    (r"recruit", r"manpower", r"staffing", r"headhunt"), 1.3),
    BusinessTypeDef(BusinessType.TRAINING_PROVIDER,
                    (r"training (courses?|centre|center|institute|provider)", r"certification courses?",
                     r"(enrol|enroll) (now|today)", r"course (fees?|duration|syllabus)", r"classes for (kids|children)",
                     r"STEM (education|classes|camps?)", r"tutoring", r"training academy"),
                    (r"academy", r"institute", r"training", r"education", r"learning cent"), 1.2),
    BusinessTypeDef(BusinessType.DISTRIBUTOR_TRADING,
                    (r"authori[sz]ed (distributor|dealer|reseller|agent)", r"exclusive (distributor|agent)s?",
                     r"general trading", r"(leading|largest) (distributor|stockist)s?", r"~(leading|largest) suppliers?",
                     r"we (distribute|stock)", r"~we supply", r"distributor of", r"~suppliers? of", r"stockists?",
                     r"import(er|ers|ing)? (and|&) (export|distribut)", r"wholesale", r"trading (company|llc|est)"),
                    (r"trading", r"traders", r"supplies", r"distribution", r"impex"), 1.0),
    BusinessTypeDef(BusinessType.MANUFACTURER,
                    (r"we (manufacture|fabricate)", r"our (factory|factories|plant|manufacturing facility)",
                     r"(manufacturing|production) (facility|facilities|plant)", r"in-house manufacturing", r"~in-house production",
                     r"~manufacturer of", r"~leading manufacturer", r"square (feet|meters|metres) (factory|facility)"),
                    (r"industries", r"manufactur", r"factory", r"mills?"), 1.0),
    BusinessTypeDef(BusinessType.ENGINEERING_DESIGN,
                    (r"engineering (consultancy|consultants|design services|services)", r"design (and|&) engineering",
                     r"consulting engineers?", r"(detailed|concept|conceptual) design", r"feasibility studies",
                     r"design office", r"engineering firm", r"multidisciplinary engineering"),
                    (r"engineering", r"engineers", r"consultants"), 1.0),
    BusinessTypeDef(BusinessType.RND_TECHNOLOGY,
                    (r"research (and|&) development", r"\bR&D\b", r"we (develop|build|design) (our own|proprietary|cutting)",
                     r"proprietary technology", r"~patent(ed|s)", r"prototyp(e|es|ing)", r"deep[- ]tech",
                     r"spin[- ]?off", r"~start-?up"), (r"labs?", r"technologies", r"robotics", r"research"), 0.9),
    BusinessTypeDef(BusinessType.SOFTWARE_PRODUCT,
                    (r"~\bSaaS\b", r"our (platform|app|software)", r"~our products?", r"download (the|our) app",
                     r"~sign up", r"book a demo", r"request a demo", r"free trial", r"pricing plans?"),
                    (r"\.ai\b", r"\bapp\b", r"software", r"digital"), 0.9),
    BusinessTypeDef(BusinessType.SYSTEMS_INTEGRATOR,
                    (r"systems? integrat(or|ion)", r"turn-?key (solutions?|projects?)", r"installation,? (testing )?(and|&) commissioning",
                     r"design,? supply,? install", r"supply,? installation"), (r"systems", r"solutions"), 0.9),
    BusinessTypeDef(BusinessType.SERVICE_MAINTENANCE,
                    (r"maintenance (services|contracts)", r"annual maintenance contracts?", r"\bAMC\b",
                     r"repair services", r"facility management", r"after-?sales service", r"calibration services"),
                    (r"services", r"maintenance", r"technical services"), 0.8),
    BusinessTypeDef(BusinessType.FINANCIAL_FIRM,
                    (r"regulated by the (DFSA|SCA|FSRA|central bank)", r"(is|are) an? (asset|investment|wealth) "
                     r"manage(r|ment (company|firm))", r"(is|are) an? (hedge fund|brokerage|family office)",
                     r"assets under management", r"\bAUM\b", r"licen[sc]ed by the (DFSA|SCA|FSRA)"),
                    (r"capital", r"investments?", r"partners", r"asset management", r"securities"), 1.0),
    BusinessTypeDef(BusinessType.CONSULTANCY,
                    (r"management consult(ing|ancy)", r"business consult(ing|ancy)", r"business setup",
                     r"company formation", r"strategy consulting", r"advisory services"),
                    (r"consult", r"advisory", r"business setup"), 0.8),
    BusinessTypeDef(BusinessType.OPERATOR,
                    (r"(airline|carrier) (flying|serving|operat)", r"book (a|your) flight", r"passengers? per year",
                     r"airport operator"), (r"airways", r"airlines?", r"airport"), 0.9),
    BusinessTypeDef(BusinessType.RETAIL_CONSUMER,
                    (r"add to cart", r"shop now", r"free delivery", r"(car|vehicle) rental", r"rent a car",
                     r"showroom", r"book (an|your) appointment", r"our (store|shop|branches) (in|across)",
                     r"cash on delivery", r"(translation|interpreting|relocation|moving|removal) services"),
                    (r"rent a car", r"store", r"shop", r"mart", r"salon", r"translation", r"relocations?", r"movers"), 1.0),
    BusinessTypeDef(BusinessType.GOVERNMENT_PUBLIC,
                    (r"(is|are) a (federal |local |semi-)?government (entity|agency|authority|organi[sz]ation|"
                     r"department|body)", r"(is|are) (a|the) (federal|public) authority", r"government-owned entity"),
                    (r"authority", r"ministry", r"municipality", r"council"), 1.0),
]


GENERIC_NAME_TOKENS = {
    "llc", "l.l.c", "fze", "fzco", "fz", "dmcc", "ltd", "limited", "inc", "co", "company", "est", "establishment",
    "group", "holding", "holdings", "international", "middle", "east", "me", "uae", "dubai", "the", "and", "&",
    "of", "services", "solutions", "trading", "general", "gulf", "emirates", "abu", "dhabi", "sharjah", "branch",
    "plc", "pjsc", "psc", "fz-llc", "llp", "wll", "spc", "corporation", "corp", "technologies", "technology",
}


def _compile(patterns: tuple[str, ...], *, case_sensitive: bool = False) -> list[re.Pattern[str]]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return [re.compile(r"(?<![A-Za-z0-9])(?:" + p + r")(?![A-Za-z0-9])", flags) for p in patterns]


@dataclass
class CompiledIndustry:
    key: Industry
    strong: list[re.Pattern[str]]
    medium: list[re.Pattern[str]]
    negative: list[re.Pattern[str]]
    strong_cs: list[re.Pattern[str]] = field(default_factory=list)


@lru_cache(maxsize=1)
def compiled_industries() -> dict[Industry, CompiledIndustry]:
    return {
        k: CompiledIndustry(k, _compile(d.strong), _compile(d.medium), _compile(d.negative),
                            _compile(d.strong_cs, case_sensitive=True))
        for k, d in INDUSTRIES.items()
    }


@lru_cache(maxsize=1)
def compiled_business_types() -> list[tuple[BusinessTypeDef, list[tuple[re.Pattern[str], bool]], list[re.Pattern[str]]]]:
    """[(definition, [(pattern, is_weak)], name_patterns)]."""
    return [(d, [(_compile((p.lstrip("~"),))[0], p.startswith("~")) for p in d.patterns], _compile(d.name_patterns))
            for d in BUSINESS_TYPES]


# schema.org types a site declares about itself (JSON-LD) → business type, with weight.
SCHEMA_TYPE_HINTS: dict[str, tuple[BusinessType, float]] = {
    "EmploymentAgency": (BusinessType.RECRUITMENT_AGENCY, 2.0),
    "EducationalOrganization": (BusinessType.TRAINING_PROVIDER, 1.5),
    "School": (BusinessType.TRAINING_PROVIDER, 2.0),
    "FinancialService": (BusinessType.FINANCIAL_FIRM, 1.0),
    "BankOrCreditUnion": (BusinessType.FINANCIAL_FIRM, 1.5),
    "AccountingService": (BusinessType.CONSULTANCY, 1.0),
    "ProfessionalService": (BusinessType.CONSULTANCY, 0.5),
    "GovernmentOrganization": (BusinessType.GOVERNMENT_PUBLIC, 2.0),
    "Airline": (BusinessType.OPERATOR, 2.0),
    "Store": (BusinessType.RETAIL_CONSUMER, 1.5),
    "AutoRental": (BusinessType.RETAIL_CONSUMER, 2.0),
    "AutoDealer": (BusinessType.RETAIL_CONSUMER, 1.5),
    "AutoRepair": (BusinessType.RETAIL_CONSUMER, 1.5),
    "ITService": (BusinessType.SYSTEMS_INTEGRATOR, 0.5),
    "SoftwareApplication": (BusinessType.SOFTWARE_PRODUCT, 1.0),
    "WebApplication": (BusinessType.SOFTWARE_PRODUCT, 1.0),
}


def industry_for_wikidata_label(label: str) -> list[Industry]:
    lab = label.lower()
    out = []
    for k, d in INDUSTRIES.items():
        if any(w.lower() in lab or lab in w.lower() for w in d.wikidata_labels):
            out.append(k)
    return out


def industries_for_osm_tags(tags: dict[str, str]) -> list[Industry]:
    out = []
    pairs = {f"{k}={v}" for k, v in tags.items() if isinstance(v, str)}
    for k, d in INDUSTRIES.items():
        if pairs & set(d.osm_tags):
            out.append(k)
    return out
