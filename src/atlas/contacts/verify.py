"""Free, non-intrusive email verification.

Checks performed: syntax (RFC 5322 via email-validator), domain existence and
mail routing (DNS MX, falling back to A/AAAA "implicit MX" per RFC 5321 §5.1),
source verification (the address appears on the page it was attributed to),
and domain consistency with the company website.

What is *not* done: SMTP probing (RCPT TO). DNS can show that a domain accepts
mail; it can never show that a particular mailbox exists. Statuses are worded
accordingly.
"""

from __future__ import annotations

import asyncio

import dns.asyncresolver
import dns.exception
import dns.resolver
from email_validator import EmailNotValidError, validate_email

from atlas.extract.contacts import classify_local_part, email_domain_relation
from atlas.models.contact import CheckResult, EmailVerification
from atlas.models.enums import EmailStatus


class DomainMailChecker:
    """Cached MX / implicit-MX lookups."""

    def __init__(self, timeout_s: float = 5.0):
        self.timeout_s = timeout_s
        self._cache: dict[str, tuple[bool | None, str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._resolver = dns.asyncresolver.Resolver()
        self._resolver.lifetime = timeout_s
        self._resolver.timeout = timeout_s

    async def check(self, domain: str) -> tuple[bool | None, str]:
        """(routes_mail, detail). None = could not determine (DNS failure)."""
        domain = domain.lower().rstrip(".")
        if domain in self._cache:
            return self._cache[domain]
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            if domain in self._cache:
                return self._cache[domain]
            result = await self._lookup(domain)
            self._cache[domain] = result
            return result

    async def _lookup(self, domain: str) -> tuple[bool | None, str]:
        try:
            ans = await self._resolver.resolve(domain, "MX")
            hosts = sorted((r.preference, str(r.exchange).rstrip(".")) for r in ans)
            if len(hosts) == 1 and hosts[0][1] in ("", "."):
                return False, "null MX record (domain declares it accepts no mail)"
            return True, "MX: " + ", ".join(h for _p, h in hosts[:3])
        except dns.resolver.NXDOMAIN:
            return False, "domain does not exist (NXDOMAIN)"
        except dns.resolver.NoAnswer:
            pass
        except dns.resolver.NoNameservers:
            return None, "DNS servers failed to answer"
        except (dns.exception.Timeout, dns.exception.DNSException) as exc:
            return None, f"DNS lookup failed: {type(exc).__name__}"
        for rtype in ("A", "AAAA"):
            try:
                await self._resolver.resolve(domain, rtype)
                return True, f"no MX record; mail may be delivered to the domain's {rtype} record (implicit MX)"
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                continue
            except dns.exception.DNSException as exc:
                return None, f"DNS lookup failed: {type(exc).__name__}"
        return False, "no MX or address records — domain cannot receive mail"


async def verify_email(email: str, *, checker: DomainMailChecker | None, site_domain: str | None,
                       published_on_source: bool | None, check_mx: bool = True) -> EmailVerification:
    checks: list[CheckResult] = []
    notes: list[str] = []
    try:
        v = validate_email(email, check_deliverability=False)
        normalized = v.normalized
        checks.append(CheckResult(check="syntax", passed=True, detail="valid address syntax"))
    except EmailNotValidError as exc:
        checks.append(CheckResult(check="syntax", passed=False, detail=str(exc)[:200]))
        return EmailVerification(email=email, status=EmailStatus.INVALID, checks=checks)
    domain = normalized.split("@", 1)[1]
    routes: bool | None = None
    if check_mx and checker is not None:
        routes, detail = await checker.check(domain)
        checks.append(CheckResult(check="mx", passed=routes, detail=detail))
    else:
        checks.append(CheckResult(check="mx", passed=None, detail="DNS check disabled"))
    relation = email_domain_relation(normalized, site_domain)
    checks.append(CheckResult(
        check="consistency", passed=relation == "company_domain",
        detail={"company_domain": "address is on the company's website domain",
                "free_mail_provider": "free webmail address (common for very small firms; lower confidence)",
                "other_domain": "address is on a different domain from the website (agency/parent/old domain?)"}[relation]))
    checks.append(CheckResult(check="source", passed=published_on_source,
                              detail="address appears on the cited page" if published_on_source else
                              ("address not found on the cited page" if published_on_source is False else
                               "source not re-checked (from structured data)")))
    _cat, generic = classify_local_part(normalized.split("@", 1)[0])
    checks.append(CheckResult(check="individual", passed=not generic,
                              detail="role mailbox (e.g. info@, careers@)" if generic else
                              "appears to be an individual's mailbox — existence cannot be verified without SMTP "
                              "probing, which ATLAS does not do"))
    notes.append("MX/DNS checks show whether the domain accepts mail, not whether this mailbox exists.")

    if routes is False:
        status = EmailStatus.INVALID
    elif published_on_source and routes:
        status = EmailStatus.PUBLISHED if generic else EmailStatus.PUBLISHED_INDIVIDUAL
    elif routes:
        status = EmailStatus.MAIL_ROUTING_OK
    elif routes is None:
        # Never claim mail routing that was not checked.
        status = EmailStatus.SYNTAX_VALID
        notes.append("DNS could not be checked; only syntax" + (" and source" if published_on_source else "")
                     + " confirmed.")
    else:
        status = EmailStatus.UNKNOWN
    return EmailVerification(email=normalized, status=status, checks=checks, notes=notes)
