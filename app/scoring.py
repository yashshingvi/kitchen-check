"""KitchenCheck scoring engine.

Two scores are produced per establishment and combined into one headline grade:

1. Compliance score — deterministic, FSSAI-aligned. Mirrors Schedule 4 marking:
   standard items = 2 marks, critical items = 4 marks. Any NC on a critical
   item forces band "NC" regardless of percentage.

2. Risk score — probability of a critical violation in the next 90 days. The
   plan calls for a LightGBM model trained on Chicago + NYC inspection data;
   the POC ships a transparent heuristic with the same interface so the ML
   service can be swapped in without touching the rest of the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

STANDARD_MARKS = 2
CRITICAL_MARKS = 4

# C = Compliant, NC = Non-compliant, PC = Partial, NA = Not applicable.
MARK_FRACTION = {"C": 1.0, "PC": 0.5, "NC": 0.0}

# Composite weighting from PLAN §3.3.
COMPLIANCE_WEIGHT = 0.7
RISK_WEIGHT = 0.3


@dataclass
class ChecklistItem:
    id: str
    label: str
    critical: bool
    section_id: str
    section_title: str

    @property
    def marks(self) -> int:
        return CRITICAL_MARKS if self.critical else STANDARD_MARKS


class Checklist:
    """Flat, indexed view of the YAML checklist for fast lookup."""

    def __init__(self, raw: dict[str, Any]):
        self.sections = raw["sections"]
        self.items: dict[str, ChecklistItem] = {}
        for section in self.sections:
            for item in section["items"]:
                self.items[item["id"]] = ChecklistItem(
                    id=item["id"],
                    label=item["label"],
                    critical=item.get("critical", False),
                    section_id=section["id"],
                    section_title=section["title"],
                )

    def get(self, item_id: str) -> ChecklistItem:
        return self.items[item_id]


def _band_from_pct(pct: float, has_critical_nc: bool) -> str:
    if has_critical_nc:
        return "NC"
    if pct >= 90:
        return "A+"
    if pct >= 80:
        return "A"
    if pct >= 50:
        return "B"
    return "C"


def score_inspection(items: dict[str, str], checklist: Checklist) -> dict[str, Any]:
    """Score a single inspection payload.

    `items` maps checklist item id -> status (C / NC / PC / NA).
    Items with status NA or missing are excluded from the denominator.
    """
    raw = 0.0
    max_possible = 0.0
    has_critical_nc = False
    section_totals: dict[str, dict[str, float]] = {}
    violations: list[dict[str, Any]] = []

    for item_id, status in items.items():
        if status == "NA":
            continue
        item = checklist.get(item_id)
        marks = item.marks
        fraction = MARK_FRACTION.get(status, 0.0)
        awarded = marks * fraction

        raw += awarded
        max_possible += marks

        bucket = section_totals.setdefault(
            item.section_id,
            {"title": item.section_title, "awarded": 0.0, "possible": 0.0},
        )
        bucket["awarded"] += awarded
        bucket["possible"] += marks

        if status == "NC" and item.critical:
            has_critical_nc = True

        if status in ("NC", "PC"):
            violations.append(
                {
                    "item_id": item.id,
                    "label": item.label,
                    "section": item.section_title,
                    "status": status,
                    "critical": item.critical,
                    "marks_lost": marks - awarded,
                }
            )

    pct = (100.0 * raw / max_possible) if max_possible else 0.0
    band = _band_from_pct(pct, has_critical_nc)

    section_breakdown = [
        {
            "id": sid,
            "title": data["title"],
            "pct": round(100.0 * data["awarded"] / data["possible"], 1)
            if data["possible"]
            else 0.0,
        }
        for sid, data in section_totals.items()
    ]

    # Largest mark losses first; critical items break ties.
    violations.sort(key=lambda v: (v["marks_lost"], v["critical"]), reverse=True)

    return {
        "raw_score": round(raw, 1),
        "max_score": round(max_possible, 1),
        "compliance_pct": round(pct, 1),
        "band": band,
        "has_critical_nc": has_critical_nc,
        "section_breakdown": section_breakdown,
        "top_violations": violations[:3],
        "violation_count": len(violations),
    }


def _days_since(d: date) -> int:
    return (date.today() - d).days


def estimate_risk(
    inspections: list[dict[str, Any]],
    checklist: Checklist,
    establishment: dict[str, Any],
) -> dict[str, Any]:
    """Estimate probability of a critical violation in the next 90 days.

    POC heuristic that stands in for the LightGBM model described in PLAN §3.2.
    Features and weights are deliberately interpretable so the SHAP-style
    explanation surface is honest about why the score moves.
    """
    if not inspections:
        # Cold-start: neutral prior, flagged as "new establishment" upstream.
        return {
            "risk_prob": 0.35,
            "drivers": [
                {
                    "feature": "no_inspection_history",
                    "explanation": "No inspections on file — neutral risk prior applied.",
                    "impact": 0.0,
                }
            ],
        }

    sorted_inspections = sorted(inspections, key=lambda i: i["date"])
    last = sorted_inspections[-1]

    # Critical NC count in last 24 months (window aligned with PLAN feature spec).
    cutoff = date.today() - timedelta(days=730)
    crit_nc_24m = 0
    for insp in sorted_inspections:
        if _to_date(insp["date"]) < cutoff:
            continue
        for item_id, status in insp["items"].items():
            if status == "NC" and checklist.get(item_id).critical:
                crit_nc_24m += 1

    # Recency penalty — old inspections mean stale signal.
    days_since_last = _days_since(_to_date(last["date"]))
    staleness = min(days_since_last / 365.0, 2.0)  # cap at 2 years of penalty.

    # Trend slope across the last 3 compliance percentages (negative = worsening).
    recent_pct = [
        score_inspection(i["items"], checklist)["compliance_pct"]
        for i in sorted_inspections[-3:]
    ]
    if len(recent_pct) >= 2:
        slope = (recent_pct[-1] - recent_pct[0]) / max(len(recent_pct) - 1, 1)
    else:
        slope = 0.0

    last_score = score_inspection(last["items"], checklist)
    last_pct = last_score["compliance_pct"]
    last_critical_nc = last_score["has_critical_nc"]

    # Establishment risk multipliers — cloud kitchens have a higher prior because
    # they're unbranded multi-tenant premises (matches PLAN feature spec).
    type_priors = {
        "cloud_kitchen": 0.05,
        "qsr": 0.03,
        "restaurant": 0.0,
        "sweet_shop": 0.02,
    }
    type_prior = type_priors.get(establishment.get("type", ""), 0.0)

    # Combine — bounded logistic-ish blend, weights chosen to keep outputs in [0,1].
    base = 0.15
    risk = (
        base
        + 0.04 * crit_nc_24m
        + 0.10 * staleness
        + (-0.005 * (last_pct - 70))  # higher % pushes risk down.
        + (-0.01 * slope)             # improving trend pushes risk down.
        + (0.25 if last_critical_nc else 0.0)
        + type_prior
    )
    risk = max(0.02, min(0.95, risk))

    drivers: list[dict[str, Any]] = []
    if last_critical_nc:
        drivers.append(
            {
                "feature": "last_inspection_critical_nc",
                "explanation": "Critical violation recorded at last inspection.",
                "impact": 0.25,
            }
        )
    if crit_nc_24m > 0:
        drivers.append(
            {
                "feature": "critical_nc_history_24m",
                "explanation": f"{crit_nc_24m} critical violation(s) in the last 24 months.",
                "impact": round(0.04 * crit_nc_24m, 3),
            }
        )
    if staleness >= 0.5:
        drivers.append(
            {
                "feature": "stale_inspection",
                "explanation": f"Last inspection was {days_since_last} days ago.",
                "impact": round(0.10 * staleness, 3),
            }
        )
    if slope < -2.0:
        drivers.append(
            {
                "feature": "worsening_trend",
                "explanation": f"Compliance trending down ({slope:+.1f} pts per inspection).",
                "impact": round(-0.01 * slope, 3),
            }
        )
    if type_prior > 0:
        drivers.append(
            {
                "feature": "establishment_type",
                "explanation": f"{establishment.get('type', '').replace('_', ' ').title()} carries an elevated prior.",
                "impact": type_prior,
            }
        )
    if not drivers:
        drivers.append(
            {
                "feature": "clean_recent_history",
                "explanation": "No recent critical violations and inspection is fresh.",
                "impact": 0.0,
            }
        )

    drivers.sort(key=lambda d: d["impact"], reverse=True)
    return {"risk_prob": round(risk, 3), "drivers": drivers[:3]}


def _to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def composite_score(
    establishment: dict[str, Any],
    checklist: Checklist,
    weights: tuple[float, float] = (COMPLIANCE_WEIGHT, RISK_WEIGHT),
) -> dict[str, Any]:
    """Return the full KitchenCheck score for one establishment.

    Combines the latest inspection's compliance score with the risk model.
    Shape mirrors what the consumer dashboard and badge endpoints consume.
    """
    inspections = establishment.get("inspections", [])
    w_compliance, w_risk = weights

    if not inspections:
        risk = estimate_risk([], checklist, establishment)
        return {
            "establishment_id": establishment["id"],
            "kc_score": None,
            "band": "UNRATED",
            "compliance": None,
            "risk": risk,
            "last_inspection_date": None,
            "stale": True,
            "is_new": True,
        }

    sorted_inspections = sorted(inspections, key=lambda i: i["date"])
    latest = sorted_inspections[-1]
    compliance = score_inspection(latest["items"], checklist)
    risk = estimate_risk(inspections, checklist, establishment)

    kc_score = (
        w_compliance * compliance["compliance_pct"]
        + w_risk * 100.0 * (1.0 - risk["risk_prob"])
    )

    last_date = _to_date(latest["date"])
    stale = _days_since(last_date) > 180

    return {
        "establishment_id": establishment["id"],
        "kc_score": round(kc_score, 1),
        "band": compliance["band"],
        "compliance": compliance,
        "risk": risk,
        "last_inspection_date": last_date.isoformat(),
        "last_inspector": latest.get("inspector"),
        "stale": stale,
        "is_new": False,
        "inspection_count": len(inspections),
        "trend": [
            {
                "date": _to_date(i["date"]).isoformat(),
                "pct": score_inspection(i["items"], checklist)["compliance_pct"],
            }
            for i in sorted_inspections[-6:]
        ],
    }
