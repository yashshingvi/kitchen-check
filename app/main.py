"""KitchenCheck POC — FastAPI gateway.

Endpoints
---------
GET  /                              → consumer dashboard (static SPA)
GET  /healthz                       → liveness probe
GET  /api/checklist                 → FSSAI Schedule 4 checklist
GET  /api/establishments            → list + search + filter
GET  /api/establishments/{id_or_slug}        → full profile
GET  /api/establishments/{id_or_slug}/score  → current KitchenCheck score
GET  /api/badge/{slug}.svg          → embeddable trust badge
GET  /api/admin/queue               → prioritized inspection queue
GET  /api/stats                     → city-level summary for the dashboard

API docs (auto-generated): /docs and /redoc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .data import BY_ID, BY_SLUG, CHECKLIST, CITY, ESTABLISHMENTS
from .scoring import composite_score

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(
    title="KitchenCheck API",
    description=(
        "Food safety transparency API. Computes FSSAI-aligned compliance bands "
        "and a forward-looking risk score per kitchen, with explainable drivers."
    ),
    version="0.1.0",
    contact={"name": "KitchenCheck POC"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# --- Helpers --------------------------------------------------------------- #


def _resolve(id_or_slug: str) -> dict[str, Any]:
    est = BY_ID.get(id_or_slug) or BY_SLUG.get(id_or_slug)
    if not est:
        raise HTTPException(status_code=404, detail="Establishment not found")
    return est


def _summary(establishment: dict[str, Any]) -> dict[str, Any]:
    """Card-shaped projection used by list endpoints and search results."""
    score = composite_score(establishment, CHECKLIST)
    return {
        "id": establishment["id"],
        "slug": establishment["slug"],
        "name": establishment["name"],
        "type": establishment["type"],
        "cuisine": establishment["cuisine"],
        "area": establishment["area"],
        "address": establishment["address"],
        "lat": establishment["lat"],
        "lng": establishment["lng"],
        "license_id": establishment["license_id"],
        "kc_score": score["kc_score"],
        "band": score["band"],
        "last_inspection_date": score["last_inspection_date"],
        "stale": score["stale"],
        "is_new": score["is_new"],
        "risk_prob": score["risk"]["risk_prob"],
    }


# --- Health & meta --------------------------------------------------------- #


@app.get("/healthz", tags=["meta"], summary="Liveness probe")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/stats", tags=["meta"], summary="City-level summary")
def stats() -> dict[str, Any]:
    summaries = [_summary(e) for e in ESTABLISHMENTS]
    band_counts: dict[str, int] = {}
    for s in summaries:
        band_counts[s["band"]] = band_counts.get(s["band"], 0) + 1
    rated = [s["kc_score"] for s in summaries if s["kc_score"] is not None]
    return {
        "city": CITY,
        "total_establishments": len(summaries),
        "rated": len(rated),
        "unrated": len(summaries) - len(rated),
        "mean_kc_score": round(sum(rated) / len(rated), 1) if rated else None,
        "band_distribution": band_counts,
    }


@app.get("/api/checklist", tags=["meta"], summary="FSSAI Schedule 4 checklist")
def get_checklist() -> dict[str, Any]:
    return {"sections": CHECKLIST.sections}


# --- Establishments -------------------------------------------------------- #


@app.get(
    "/api/establishments",
    tags=["establishments"],
    summary="List, search, and filter establishments",
)
def list_establishments(
    q: str | None = Query(None, description="Free-text search on name/cuisine/area"),
    area: str | None = Query(None, description="Filter by area (exact match)"),
    type: str | None = Query(
        None,
        description="restaurant / cloud_kitchen / qsr / sweet_shop",
    ),
    band: str | None = Query(None, description="A+ / A / B / C / NC / UNRATED"),
    sort: str = Query(
        "score_desc",
        description="score_desc | score_asc | risk_desc | recent",
    ),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    results = [_summary(e) for e in ESTABLISHMENTS]

    if q:
        needle = q.lower()
        results = [
            s
            for s in results
            if needle in s["name"].lower()
            or needle in s["cuisine"].lower()
            or needle in s["area"].lower()
        ]
    if area:
        results = [s for s in results if s["area"].lower() == area.lower()]
    if type:
        results = [s for s in results if s["type"] == type]
    if band:
        results = [s for s in results if s["band"] == band]

    # `None` sorts safely to the bottom regardless of direction.
    def _score_key(s: dict[str, Any]) -> float:
        return s["kc_score"] if s["kc_score"] is not None else -1.0

    if sort == "score_desc":
        results.sort(key=_score_key, reverse=True)
    elif sort == "score_asc":
        results.sort(key=_score_key)
    elif sort == "risk_desc":
        results.sort(key=lambda s: s["risk_prob"], reverse=True)
    elif sort == "recent":
        results.sort(key=lambda s: s["last_inspection_date"] or "", reverse=True)

    return {"count": len(results), "results": results[:limit]}


@app.get(
    "/api/establishments/{id_or_slug}",
    tags=["establishments"],
    summary="Establishment profile (full inspection history)",
)
def get_establishment(id_or_slug: str) -> dict[str, Any]:
    est = _resolve(id_or_slug)
    score = composite_score(est, CHECKLIST)
    return {
        "id": est["id"],
        "slug": est["slug"],
        "name": est["name"],
        "type": est["type"],
        "cuisine": est["cuisine"],
        "area": est["area"],
        "address": est["address"],
        "lat": est["lat"],
        "lng": est["lng"],
        "license_id": est["license_id"],
        "years_operation": est.get("years_operation"),
        "seating_capacity": est.get("seating_capacity"),
        "score": score,
        "inspections": est["inspections"],
    }


@app.get(
    "/api/establishments/{id_or_slug}/score",
    tags=["establishments"],
    summary="KitchenCheck composite score with explainability",
)
def get_score(id_or_slug: str) -> dict[str, Any]:
    est = _resolve(id_or_slug)
    return composite_score(est, CHECKLIST)


# --- Trust badge ----------------------------------------------------------- #


_BAND_COLOR = {
    "A+": "#0a8f3b",
    "A": "#3fa84a",
    "B": "#e0a92a",
    "C": "#d24a3a",
    "NC": "#8a1f1f",
    "UNRATED": "#888888",
}


@app.get(
    "/api/badge/{slug}.svg",
    tags=["badge"],
    summary="Embeddable trust badge (SVG)",
    response_class=Response,
)
def badge(slug: str) -> Response:
    est = _resolve(slug)
    score = composite_score(est, CHECKLIST)
    band = score["band"]
    color = _BAND_COLOR.get(band, "#888888")
    kc = score["kc_score"] if score["kc_score"] is not None else "—"

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="180" height="60" viewBox="0 0 180 60">
  <rect width="180" height="60" rx="6" fill="#1b1b1f"/>
  <rect x="0" y="0" width="60" height="60" rx="6" fill="{color}"/>
  <text x="30" y="38" text-anchor="middle" font-family="Helvetica, Arial, sans-serif"
        font-size="24" font-weight="700" fill="white">{band}</text>
  <text x="70" y="24" font-family="Helvetica, Arial, sans-serif" font-size="11"
        fill="#9aa0a6">KitchenCheck</text>
  <text x="70" y="44" font-family="Helvetica, Arial, sans-serif" font-size="16"
        font-weight="600" fill="white">Score {kc}</text>
</svg>"""
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


# --- Admin / regulator surface -------------------------------------------- #


@app.get(
    "/api/admin/queue",
    tags=["admin"],
    summary="Prioritized inspection queue (risk × staleness)",
)
def admin_queue(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """Chicago-style prioritization: rank by risk_prob × (1 + months_since_last)."""
    rows: list[dict[str, Any]] = []
    for est in ESTABLISHMENTS:
        score = composite_score(est, CHECKLIST)
        risk = score["risk"]["risk_prob"]
        last = score["last_inspection_date"]
        if last:
            from datetime import date

            days = (date.today() - date.fromisoformat(last)).days
        else:
            days = 365  # cold-start kitchens go into the queue at ~1y staleness.
        priority = risk * (1.0 + days / 30.0)
        rows.append(
            {
                "id": est["id"],
                "slug": est["slug"],
                "name": est["name"],
                "area": est["area"],
                "type": est["type"],
                "risk_prob": risk,
                "days_since_last_inspection": days,
                "band": score["band"],
                "priority_score": round(priority, 3),
            }
        )
    rows.sort(key=lambda r: r["priority_score"], reverse=True)
    return {"count": len(rows), "queue": rows[:limit]}


# --- Static dashboard ------------------------------------------------------ #

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
