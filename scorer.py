"""KitchenCheck scoring engine.

Implements the FSSAI Schedule 4-aligned compliance score and a heuristic risk
score. The checklist is embedded here (no YAML dependency) so the module is
self-contained.

Per PLAN.md §3:
- Standard items: 2 marks. Critical items: 4 marks.
- Any NC on a critical item forces band "NC" regardless of percentage.
- Composite kc_score = 0.7 * compliance_pct + 0.3 * 100 * (1 - risk_prob).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

STANDARD_MARKS = 2
CRITICAL_MARKS = 4
MARK_FRACTION = {"C": 1.0, "PC": 0.5, "NC": 0.0}
COMPLIANCE_WEIGHT = 0.7
RISK_WEIGHT = 0.3


# FSSAI Schedule 4 — POC subset. 5 sections, 25 items.
CHECKLIST: list[dict[str, Any]] = [
    {
        "id": "design_facilities",
        "title": "Design & Facilities",
        "items": [
            {"id": "df_layout", "label": "Premises layout prevents cross-contamination", "critical": False},
            {"id": "df_lighting", "label": "Adequate lighting in food handling areas", "critical": False},
            {"id": "df_ventilation", "label": "Ventilation prevents condensation and odour buildup", "critical": False},
            {"id": "df_water", "label": "Potable water source available and tested", "critical": True},
            {"id": "df_drainage", "label": "Drainage flows away from food prep, no stagnation", "critical": False},
            {"id": "df_pestproofing", "label": "Premises pest-proofed (screens, sealed entries)", "critical": False},
        ],
    },
    {
        "id": "control_operation",
        "title": "Control of Operation",
        "items": [
            {"id": "co_temp_cold", "label": "Cold storage maintained at or below 5°C", "critical": True},
            {"id": "co_temp_hot", "label": "Hot holding maintained at or above 60°C", "critical": True},
            {"id": "co_cross_contam", "label": "Raw and cooked foods physically separated", "critical": True},
            {"id": "co_packaging", "label": "Food-grade packaging materials in use", "critical": True},
            {"id": "co_haccp", "label": "HACCP plan documented and followed", "critical": False},
            {"id": "co_traceability", "label": "Supplier and batch traceability records maintained", "critical": False},
        ],
    },
    {
        "id": "maintenance_sanitation",
        "title": "Maintenance & Sanitation",
        "items": [
            {"id": "ms_cleaning_schedule", "label": "Documented cleaning schedule visible and signed", "critical": False},
            {"id": "ms_waste", "label": "Waste segregated and removed daily", "critical": False},
            {"id": "ms_pest_control", "label": "Pest control contract active, logs current", "critical": False},
            {"id": "ms_equipment", "label": "Equipment maintained, food-contact surfaces intact", "critical": False},
            {"id": "ms_chemicals", "label": "Cleaning chemicals stored away from food", "critical": True},
        ],
    },
    {
        "id": "personal_hygiene",
        "title": "Personal Hygiene",
        "items": [
            {"id": "ph_handwash", "label": "Handwashing stations stocked and accessible", "critical": False},
            {"id": "ph_clothing", "label": "Staff in clean protective clothing, hair covered", "critical": False},
            {"id": "ph_medical", "label": "Annual medical fitness certificates on file", "critical": False},
            {"id": "ph_illness", "label": "Illness reporting procedure documented and known", "critical": True},
            {"id": "ph_jewellery", "label": "No exposed jewellery / nail polish on food handlers", "critical": False},
        ],
    },
    {
        "id": "training_complaints",
        "title": "Training & Complaint Handling",
        "items": [
            {"id": "tc_training_log", "label": "Staff food-safety training records up to date", "critical": False},
            {"id": "tc_fostac", "label": "At least one FoSTaC-certified food safety supervisor", "critical": False},
            {"id": "tc_complaint_log", "label": "Customer complaint log maintained", "critical": False},
            {"id": "tc_recall", "label": "Product recall procedure documented", "critical": False},
        ],
    },
]


def _build_item_index() -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for section in CHECKLIST:
        for item in section["items"]:
            idx[item["id"]] = {
                "id": item["id"],
                "label": item["label"],
                "critical": item["critical"],
                "section_id": section["id"],
                "section_title": section["title"],
                "marks": CRITICAL_MARKS if item["critical"] else STANDARD_MARKS,
            }
    return idx


ITEM_INDEX = _build_item_index()
ALL_ITEM_IDS = list(ITEM_INDEX.keys())
CRITICAL_ITEM_IDS = [i for i, v in ITEM_INDEX.items() if v["critical"]]


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


def score_inspection(items: dict[str, str]) -> dict[str, Any]:
    """Score a single inspection payload.

    `items` maps checklist item id -> status (C / NC / PC / NA).
    Items with status NA or missing are excluded from the denominator.
    """
    raw = 0.0
    max_possible = 0.0
    has_critical_nc = False
    section_totals: dict[str, dict[str, Any]] = {}
    violations: list[dict[str, Any]] = []

    for item_id, status in items.items():
        if status == "NA":
            continue
        item = ITEM_INDEX.get(item_id)
        if item is None:
            continue
        marks = item["marks"]
        fraction = MARK_FRACTION.get(status, 0.0)
        awarded = marks * fraction

        raw += awarded
        max_possible += marks

        bucket = section_totals.setdefault(
            item["section_id"],
            {"title": item["section_title"], "awarded": 0.0, "possible": 0.0},
        )
        bucket["awarded"] += awarded
        bucket["possible"] += marks

        if status == "NC" and item["critical"]:
            has_critical_nc = True

        if status in ("NC", "PC"):
            violations.append({
                "item_id": item["id"],
                "label": item["label"],
                "section": item["section_title"],
                "status": status,
                "critical": item["critical"],
                "marks_lost": round(marks - awarded, 1),
            })

    pct = (100.0 * raw / max_possible) if max_possible else 0.0
    band = _band_from_pct(pct, has_critical_nc)

    section_breakdown = [
        {
            "id": sid,
            "title": data["title"],
            "pct": round(100.0 * data["awarded"] / data["possible"], 1)
            if data["possible"] else 0.0,
        }
        for sid, data in section_totals.items()
    ]

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


def _to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def _days_since(d: date) -> int:
    return (date.today() - d).days


def estimate_risk(inspections: list[dict[str, Any]], establishment: dict[str, Any]) -> dict[str, Any]:
    """Heuristic stand-in for the LightGBM risk model (PLAN §3.2).

    Returns risk_prob in [0,1] and top SHAP-style drivers in plain language.
    """
    if not inspections:
        return {
            "risk_prob": 0.35,
            "drivers": [{
                "feature": "no_inspection_history",
                "explanation": "No inspections on file — neutral risk prior applied.",
                "impact": 0.0,
            }],
        }

    sorted_inspections = sorted(inspections, key=lambda i: i["date"])
    last = sorted_inspections[-1]

    cutoff = date.today() - timedelta(days=730)
    crit_nc_24m = 0
    for insp in sorted_inspections:
        if _to_date(insp["date"]) < cutoff:
            continue
        for item_id, status in insp["items"].items():
            item = ITEM_INDEX.get(item_id)
            if status == "NC" and item and item["critical"]:
                crit_nc_24m += 1

    days_since_last = _days_since(_to_date(last["date"]))
    staleness = min(days_since_last / 365.0, 2.0)

    recent_pct = [score_inspection(i["items"])["compliance_pct"] for i in sorted_inspections[-3:]]
    if len(recent_pct) >= 2:
        slope = (recent_pct[-1] - recent_pct[0]) / max(len(recent_pct) - 1, 1)
    else:
        slope = 0.0

    last_score = score_inspection(last["items"])
    last_pct = last_score["compliance_pct"]
    last_critical_nc = last_score["has_critical_nc"]

    type_priors = {
        "cloud_kitchen": 0.05,
        "qsr": 0.03,
        "restaurant": 0.0,
        "sweet_shop": 0.02,
    }
    type_prior = type_priors.get(establishment.get("type", ""), 0.0)

    base = 0.15
    risk = (
        base
        + 0.04 * crit_nc_24m
        + 0.10 * staleness
        + (-0.005 * (last_pct - 70))
        + (-0.01 * slope)
        + (0.25 if last_critical_nc else 0.0)
        + type_prior
    )
    risk = max(0.02, min(0.95, risk))

    drivers: list[dict[str, Any]] = []
    if last_critical_nc:
        drivers.append({
            "feature": "last_inspection_critical_nc",
            "explanation": "Critical violation recorded at last inspection.",
            "impact": 0.25,
        })
    if crit_nc_24m > 0:
        drivers.append({
            "feature": "critical_nc_history_24m",
            "explanation": f"{crit_nc_24m} critical violation(s) in the last 24 months.",
            "impact": round(0.04 * crit_nc_24m, 3),
        })
    if staleness >= 0.5:
        drivers.append({
            "feature": "stale_inspection",
            "explanation": f"Last inspection was {days_since_last} days ago.",
            "impact": round(0.10 * staleness, 3),
        })
    if slope < -2.0:
        drivers.append({
            "feature": "worsening_trend",
            "explanation": f"Compliance trending down ({slope:+.1f} pts per inspection).",
            "impact": round(-0.01 * slope, 3),
        })
    if type_prior > 0:
        drivers.append({
            "feature": "establishment_type",
            "explanation": f"{establishment.get('type', '').replace('_', ' ').title()} carries an elevated risk prior.",
            "impact": type_prior,
        })
    if not drivers:
        drivers.append({
            "feature": "clean_recent_history",
            "explanation": "No recent critical violations and inspection is fresh.",
            "impact": 0.0,
        })

    drivers.sort(key=lambda d: d["impact"], reverse=True)
    return {"risk_prob": round(risk, 3), "drivers": drivers[:3]}


def composite_score(establishment: dict[str, Any], inspections: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine compliance + risk into the headline KitchenCheck score."""
    if not inspections:
        risk = estimate_risk([], establishment)
        return {
            "establishment_id": establishment["id"],
            "kc_score": None,
            "band": "UNRATED",
            "compliance": None,
            "risk": risk,
            "last_inspection_date": None,
            "stale": True,
            "is_new": True,
            "inspection_count": 0,
            "trend": [],
        }

    sorted_inspections = sorted(inspections, key=lambda i: i["date"])
    latest = sorted_inspections[-1]
    compliance = score_inspection(latest["items"])
    risk = estimate_risk(inspections, establishment)

    kc_score = (
        COMPLIANCE_WEIGHT * compliance["compliance_pct"]
        + RISK_WEIGHT * 100.0 * (1.0 - risk["risk_prob"])
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
                "pct": score_inspection(i["items"])["compliance_pct"],
            }
            for i in sorted_inspections[-6:]
        ],
    }
