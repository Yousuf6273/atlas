"""Contact-quality scoring and best-channel recommendation.

Contact quality rubric (0–100, per channel)
-------------------------------------------
email, role mailbox (careers@/hr@/info@…) on the company domain, published on the
official site, domain routes mail ......................................... 80 (careers/HR: 85)
email, named individual with a target role, published, routes mail ........ 90
email, individual mailbox without a known role, published, routes mail .... 70
email on the company domain from structured data only (OSM/Wikidata/import),
domain routes mail ......................................................... 55
email on a free webmail domain, published on the official site ............ 45
official contact form on the company website .............................. 60
careers page / jobs portal linked from the official site .................. 55 (internship page: 65)
named decision-maker published on the site (no direct channel) ............ 35
phone number .............................................................. 30
LinkedIn company page linked from the official site ....................... 20
Penalties: address on an unrelated domain −15; source not confirmed −10;
domain does not route mail → 0; on the do-not-contact list → 0.
"""

from __future__ import annotations

from typing import Any

from atlas.models.enums import ContactChannel, EmailStatus, RoleCategory, SizeBand

PREFERRED_ROLES_BY_SIZE: dict[str, list[RoleCategory]] = {
    SizeBand.MICRO: [RoleCategory.FOUNDER_EXEC, RoleCategory.OPERATIONS, RoleCategory.ENGINEERING_LEAD,
                     RoleCategory.GENERAL],
    SizeBand.SMALL: [RoleCategory.FOUNDER_EXEC, RoleCategory.ENGINEERING_LEAD, RoleCategory.OPERATIONS,
                     RoleCategory.RND, RoleCategory.GENERAL, RoleCategory.HR_TALENT],
    SizeBand.MEDIUM: [RoleCategory.ENGINEERING_LEAD, RoleCategory.RND, RoleCategory.OPERATIONS, RoleCategory.PROJECT,
                      RoleCategory.HR_TALENT, RoleCategory.GENERAL],
    SizeBand.LARGE: [RoleCategory.HR_TALENT, RoleCategory.ENGINEERING_LEAD, RoleCategory.RND, RoleCategory.GENERAL],
    SizeBand.ENTERPRISE: [RoleCategory.HR_TALENT, RoleCategory.GENERAL],
    SizeBand.UNKNOWN: [RoleCategory.FOUNDER_EXEC, RoleCategory.ENGINEERING_LEAD, RoleCategory.HR_TALENT,
                       RoleCategory.OPERATIONS, RoleCategory.GENERAL],
}

TARGET_ROLES = {RoleCategory.FOUNDER_EXEC, RoleCategory.ENGINEERING_LEAD, RoleCategory.OPERATIONS, RoleCategory.RND,
                RoleCategory.PROJECT, RoleCategory.HR_TALENT}


def channel_quality(c: dict[str, Any], *, relation: str | None = None) -> tuple[float, str]:
    """Score one stored contact row. ``relation`` is the email/domain relation when known."""
    ch = c["channel"]
    status = c.get("verification_status") or "unknown"
    official = c.get("source_type") == "official_site"
    role = RoleCategory(c.get("role_category") or "general")
    if ch == ContactChannel.EMAIL:
        if status == EmailStatus.INVALID:
            return 0.0, "email domain cannot receive mail"
        routes = status in (EmailStatus.PUBLISHED, EmailStatus.PUBLISHED_INDIVIDUAL, EmailStatus.MAIL_ROUTING_OK)
        if relation == "free_mail_provider" and official:
            score, why = 45.0, "free webmail address published on the official site"
        elif not official:
            score, why = (55.0 if routes else 40.0), "from structured data (not seen on the official site)"
        elif c.get("is_generic"):
            score = 85.0 if role == RoleCategory.HR_TALENT else 80.0
            why = "role mailbox published on the official site"
        elif c.get("name") and role in TARGET_ROLES:
            score, why = 90.0, f"named {c.get('role') or role.value} published on the official site"
        else:
            score, why = 70.0, "individual address published on the official site (inbox not verifiable)"
        if relation == "other_domain":
            score -= 15
            why += "; different domain from the website"
        if official and status not in (EmailStatus.PUBLISHED, EmailStatus.PUBLISHED_INDIVIDUAL):
            score -= 10
            why += "; source or mail routing not confirmed"
        return max(0.0, score), why
    if ch == ContactChannel.CONTACT_FORM:
        return 60.0, "official contact form"
    if ch == ContactChannel.CAREERS_PAGE:
        intern = "intern" in (c.get("value") or "").lower()
        return (65.0 if intern else 55.0), "internship page" if intern else "careers page / jobs portal"
    if ch == ContactChannel.NAMED_PERSON:
        return 35.0, f"named {c.get('role') or 'person'} published on the site (no direct channel)"
    if ch == ContactChannel.PHONE:
        return 30.0, "phone number"
    if ch == ContactChannel.LINKEDIN_COMPANY:
        return 20.0, "LinkedIn company page linked from the site"
    return 10.0, "website only"


def best_contact(contacts: list[dict[str, Any]], size: SizeBand, *, has_programme: bool = False
                 ) -> tuple[dict[str, Any] | None, str]:
    """Pick the channel to use first, with a short strategy explanation."""
    usable = [c for c in contacts if c["channel"] in ("email", "contact_form", "careers_page") and c["quality_score"] > 0]
    prefs = PREFERRED_ROLES_BY_SIZE.get(size, PREFERRED_ROLES_BY_SIZE[SizeBand.UNKNOWN])
    if size in (SizeBand.LARGE, SizeBand.ENTERPRISE) or has_programme:
        careers = [c for c in usable if c["channel"] == "careers_page" or c.get("role_category") == "hr_talent"]
        if careers:
            pick = max(careers, key=lambda c: c["quality_score"])
            return pick, ("Larger organisation or formal programme: apply through the official careers/internship "
                          "route rather than emailing individuals.")

    def rank(c):
        role = c.get("role_category") or "general"
        try:
            pr = prefs.index(RoleCategory(role))
        except ValueError:
            pr = len(prefs)
        return (-c["quality_score"] + pr * 6)

    if not usable:
        named = [c for c in contacts if c["channel"] == "named_person"]
        if named:
            return named[0], "No direct channel found; address the named person via the company's phone or a mutual contact."
        return None, "No usable contact channel found; consider visiting, calling, or asking for an introduction."
    pick = min(usable, key=rank)
    if size in (SizeBand.MICRO, SizeBand.SMALL):
        why = "Small company: a short personal email to the founder/MD or operations lead is appropriate."
    elif size == SizeBand.MEDIUM:
        why = "Mid-sized company: target an engineering/operations lead, or HR if no technical contact is published."
    else:
        why = "Size unknown: use the most direct published channel and keep the message short."
    return pick, why
