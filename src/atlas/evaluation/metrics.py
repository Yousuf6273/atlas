"""Evaluation metrics computed only from the student's manual reviews.

No metric is reported unless there are reviewed companies to support it, and
every rate is shown with its sample size and a 95 % Wilson interval.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from atlas.db.review import evaluations


@dataclass
class Rate:
    label: str
    successes: int
    n: int

    @property
    def value(self) -> float | None:
        return self.successes / self.n if self.n else None

    @property
    def interval(self) -> tuple[float, float] | None:
        if not self.n:
            return None
        z = 1.96
        p = self.successes / self.n
        denom = 1 + z * z / self.n
        centre = (p + z * z / (2 * self.n)) / denom
        half = z * math.sqrt(p * (1 - p) / self.n + z * z / (4 * self.n * self.n)) / denom
        return max(0.0, centre - half), min(1.0, centre + half)

    def fmt(self) -> str:
        if not self.n:
            return "no reviews yet"
        lo, hi = self.interval
        return f"{self.value:.0%} ({self.successes}/{self.n}; 95% CI {lo:.0%}–{hi:.0%})"


def compute_metrics(conn: sqlite3.Connection, job_id: int | None = None) -> list[Rate]:
    rows = evaluations(conn, job_id)

    def rate(label: str, field: str, invert: bool = False) -> Rate:
        vals = [r[field] for r in rows if r[field] is not None]
        succ = sum(1 for v in vals if (not v if invert else v))
        return Rate(label, succ, len(vals))

    claims_checked = sum(r["claims_checked"] or 0 for r in rows if r["claims_checked"])
    unsupported = sum(r["unsupported_claims"] or 0 for r in rows if r["claims_checked"])
    return [
        rate("Discovery relevance (company is relevant to the search)", "discovery_relevant"),
        rate("Duplicate rate (record duplicates another)", "is_duplicate"),
        rate("Industry classification accuracy", "industry_correct"),
        rate("Contact-source validity (contact really published at the cited source)", "contact_source_valid"),
        Rate("Unsupported factual claims (of claims checked)", unsupported, claims_checked),
        rate("Internship suitability assessment judged correct", "internship_assessment_correct"),
        rate("Outreach recommendation judged useful", "outreach_useful"),
    ]


def review_sample(conn: sqlite3.Connection, job_id: int, n: int = 50) -> list[int]:
    """First ``n`` companies of a job by score (the brief's 25–50 company review set)."""
    rows = conn.execute(
        "SELECT jc.company_id FROM job_companies jc JOIN companies c ON c.id = jc.company_id "
        "LEFT JOIN prospect_scores s ON s.company_id = c.id AND s.is_current = 1 "
        "WHERE jc.job_id = ? AND c.merged_into_id IS NULL AND c.qualification_status IS NOT NULL "
        "ORDER BY COALESCE(s.total, 0) DESC LIMIT ?", (job_id, n)).fetchall()
    return [int(r[0]) for r in rows]
