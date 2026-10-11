"""Enumerations shared across the domain model.

String enums are used so values are stored verbatim in SQLite and exports.
"""

from __future__ import annotations

from enum import StrEnum


class Industry(StrEnum):
    AEROSPACE = "aerospace"
    MECHANICAL = "mechanical"
    ROBOTICS = "robotics"
    AUTOMOTIVE = "automotive"
    MANUFACTURING = "manufacturing"
    AI_ML = "ai_ml"
    SOFTWARE = "software"
    DATA_ANALYTICS = "data_analytics"
    FINTECH = "fintech"
    QUANT_FINANCE = "quant_finance"


INDUSTRY_LABELS: dict[str, str] = {
    "aerospace": "Aerospace & aviation engineering",
    "mechanical": "Mechanical engineering",
    "robotics": "Robotics & automation",
    "automotive": "Automotive engineering",
    "manufacturing": "Manufacturing",
    "ai_ml": "AI & machine learning",
    "software": "Software engineering",
    "data_analytics": "Data analytics",
    "fintech": "Financial technology",
    "quant_finance": "Quantitative finance & investment research",
}


class BusinessType(StrEnum):
    ENGINEERING_DESIGN = "engineering_design"
    MANUFACTURER = "manufacturer"
    RND_TECHNOLOGY = "rnd_technology"
    SOFTWARE_PRODUCT = "software_product"
    SYSTEMS_INTEGRATOR = "systems_integrator"
    SERVICE_MAINTENANCE = "service_maintenance"
    DISTRIBUTOR_TRADING = "distributor_trading"
    RECRUITMENT_AGENCY = "recruitment_agency"
    TRAINING_PROVIDER = "training_provider"
    CONSULTANCY = "consultancy"
    FINANCIAL_FIRM = "financial_firm"
    OPERATOR = "operator"
    RETAIL_CONSUMER = "retail_consumer"
    GOVERNMENT_PUBLIC = "government_public"
    UNKNOWN = "unknown"


BUSINESS_TYPE_LABELS: dict[str, str] = {
    "engineering_design": "Engineering design / consultancy",
    "manufacturer": "Manufacturer",
    "rnd_technology": "R&D / technology developer",
    "software_product": "Software / product company",
    "systems_integrator": "Systems integrator",
    "service_maintenance": "Technical services / maintenance",
    "distributor_trading": "Distributor / trading company",
    "recruitment_agency": "Recruitment agency",
    "training_provider": "Training provider",
    "consultancy": "Business consultancy",
    "financial_firm": "Financial / investment firm",
    "operator": "Operator (e.g. airline, airport)",
    "retail_consumer": "Retail / consumer service",
    "government_public": "Government / public body",
    "unknown": "Unknown",
}


class QualificationStatus(StrEnum):
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class GeoStatus(StrEnum):
    IN_AREA = "in_area"
    NEARBY = "nearby"  # same country, neighbouring/commutable area
    IN_COUNTRY = "in_country"
    OUTSIDE = "outside"
    UNKNOWN = "unknown"


class OperationalStatus(StrEnum):
    LIKELY_ACTIVE = "likely_active"
    UNCERTAIN = "uncertain"
    LIKELY_INACTIVE = "likely_inactive"


class InternshipCategory(StrEnum):
    CONFIRMED = "confirmed_opportunity"
    PLAUSIBLE = "plausible_speculative"
    UNCLEAR = "eligibility_unclear"
    INELIGIBLE = "explicitly_ineligible"


INTERNSHIP_LABELS: dict[str, str] = {
    "confirmed_opportunity": "Confirmed relevant opportunity",
    "plausible_speculative": "Plausible speculative outreach",
    "eligibility_unclear": "Eligibility unclear",
    "explicitly_ineligible": "Explicitly ineligible",
}


class SizeBand(StrEnum):
    MICRO = "micro"  # 1-9
    SMALL = "small"  # 10-49
    MEDIUM = "medium"  # 50-249
    LARGE = "large"  # 250-999
    ENTERPRISE = "enterprise"  # 1000+
    UNKNOWN = "unknown"


SIZE_LABELS: dict[str, str] = {
    "micro": "Micro (1–9)",
    "small": "Small (10–49)",
    "medium": "Medium (50–249)",
    "large": "Large (250–999)",
    "enterprise": "Enterprise (1000+)",
    "unknown": "Unknown",
}


class TaskBasis(StrEnum):
    OBSERVED = "observed_need"
    INFERRED = "inferred_opportunity"


class Level(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Supervision(StrEnum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    MINIMAL = "minimal"


class ContactChannel(StrEnum):
    EMAIL = "email"
    CONTACT_FORM = "contact_form"
    PHONE = "phone"
    CAREERS_PAGE = "careers_page"
    LINKEDIN_COMPANY = "linkedin_company_page"
    WEBSITE = "website"
    # A named decision-maker published on the official site, with no direct
    # channel of their own (address them via the company's general channel).
    NAMED_PERSON = "named_person"


class RoleCategory(StrEnum):
    FOUNDER_EXEC = "founder_exec"
    ENGINEERING_LEAD = "engineering_lead"
    OPERATIONS = "operations"
    RND = "rnd"
    PROJECT = "project"
    HR_TALENT = "hr_talent"
    GENERAL = "general"
    SALES = "sales"
    OTHER = "other"


ROLE_LABELS: dict[str, str] = {
    "founder_exec": "Founder / Managing Director",
    "engineering_lead": "Engineering / Technical lead",
    "operations": "Operations",
    "rnd": "R&D",
    "project": "Project management",
    "hr_talent": "HR / Talent acquisition",
    "general": "General enquiries",
    "sales": "Sales",
    "other": "Other",
}


class EmailStatus(StrEnum):
    INVALID = "invalid"
    UNKNOWN = "unknown"
    SYNTAX_VALID = "syntax_valid"
    MAIL_ROUTING_OK = "domain_mail_routing_ok"
    PUBLISHED = "publicly_published"
    PUBLISHED_INDIVIDUAL = "published_individual_unverified_inbox"


EMAIL_STATUS_LABELS: dict[str, str] = {
    "invalid": "Invalid address",
    "unknown": "Unknown status",
    "syntax_valid": "Syntactically valid only",
    "domain_mail_routing_ok": "Domain has working mail routing (source not confirmed)",
    "publicly_published": "Publicly published address · domain routes mail",
    "published_individual_unverified_inbox": "Published individual address · inbox not verified",
}


class OutreachStatus(StrEnum):
    NOT_CONTACTED = "not_contacted"
    DRAFT_PREPARED = "draft_prepared"
    CONTACTED = "contacted"
    AWAITING_RESPONSE = "awaiting_response"
    REPLIED = "replied"
    INTERESTED = "interested"
    MEETING_ARRANGED = "meeting_arranged"
    REJECTED = "rejected"
    PLACEMENT_OFFERED = "placement_offered"
    DO_NOT_CONTACT = "do_not_contact"


OUTREACH_LABELS: dict[str, str] = {
    "not_contacted": "Not contacted",
    "draft_prepared": "Draft prepared",
    "contacted": "Contacted",
    "awaiting_response": "Awaiting response",
    "replied": "Replied",
    "interested": "Interested",
    "meeting_arranged": "Meeting arranged",
    "rejected": "Rejected",
    "placement_offered": "Placement offered",
    "do_not_contact": "Do not contact",
}

# Statuses that mean a message has actually been sent by the user.
SENT_STATUSES = {
    OutreachStatus.CONTACTED,
    OutreachStatus.AWAITING_RESPONSE,
    OutreachStatus.REPLIED,
    OutreachStatus.INTERESTED,
    OutreachStatus.MEETING_ARRANGED,
    OutreachStatus.REJECTED,
    OutreachStatus.PLACEMENT_OFFERED,
}


class DraftKind(StrEnum):
    SHORT_EMAIL = "short_email"
    DETAILED_EMAIL = "detailed_email"
    NETWORKING = "networking_message"
    FOLLOW_UP = "follow_up"
    INTRO_REQUEST = "introduction_request"


DRAFT_LABELS: dict[str, str] = {
    "short_email": "Short introductory email",
    "detailed_email": "Detailed email",
    "networking_message": "Professional networking message",
    "follow_up": "Polite follow-up",
    "introduction_request": "Introduction request (via mutual contact)",
}


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class LocationType(StrEnum):
    HEADQUARTERS = "headquarters"
    BRANCH = "branch"
    REGISTERED = "registered_address"
    FACILITY = "facility"
    UNKNOWN = "unknown"


class ProviderAvailability(StrEnum):
    AVAILABLE = "available"
    MANUAL_ONLY = "manual_only"
    REQUIRES_SETUP = "requires_setup"
    DISABLED_POLICY = "disabled_policy"
    DISABLED_PAID = "disabled_paid"
