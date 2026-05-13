# KitchenCheck — POC Plan

A food safety transparency platform that ingests kitchen inspection data, computes a
compliance + risk score per establishment, and surfaces it through a public dashboard
and an operator portal. Aligned to the FSSAI Hygiene Rating Scheme (HRS) so scores are
defensible against existing Indian regulation, with an ML-driven risk layer on top.

---

## 1. Problem framing

Consumers in India have weak signal on which kitchens (especially cloud / dark kitchens
serving Swiggy and Zomato) are actually safe. FSSAI runs a voluntary Hygiene Rating
Scheme on a 1–5 star scale and is rolling out geo-tagged digital monitoring through
FoSCoS in 2026, but coverage is uneven, ratings are valid for two years, and there is
no live "trust" surface where a buyer can check an establishment's score before
ordering.

KitchenCheck targets three users:

- **Consumers** — want a one-glance trust signal before they order or dine in.
- **Operators** (restaurants, cloud kitchens) — want to understand their score, what
  drives it, and how to improve.
- **Regulators / aggregators** — want a prioritized list of where to inspect next,
  Chicago-style. Aggregators (Swiggy / Zomato / Razorpay-onboarded merchants) can
  bake the score into onboarding KYC and merchant trust pages.

The POC scope is one city (recommend Bengaluru or Delhi NCR — Delhi food safety dept
started inspecting cloud kitchens in April 2026, so there is fresh ground-truth data
to validate against).

---

## 2. Inspection model & data foundation

### 2.1 FSSAI Schedule 4 checklist structure

KitchenCheck mirrors the FSSAI Schedule 4 inspection sections so that scores map 1:1
to existing regulatory categories:

| Section | Examples of checks |
|---|---|
| **Design & Facilities** | Layout, lighting, ventilation, water source, drainage, pest-proofing |
| **Control of Operation** | Temperature control, cross-contamination prevention, food-grade packaging, HACCP application |
| **Maintenance & Sanitation** | Cleaning schedules, waste management, pest control, equipment upkeep |
| **Personal Hygiene** | Handwashing, protective clothing, annual medical checks & inoculations, illness reporting |
| **Training & Complaint Handling** | Staff training records, customer complaint logs, recall procedures |

Each line item is one of:

- `C` — Compliant
- `NC` — Non-compliant
- `PC` — Partial compliance
- `NA` — Not applicable / not observed

Standard items are worth **2 marks**; asterisked critical items (food-grade packaging,
temperature, cross-contamination, etc.) are worth **4 marks**. Any `NC` on an
asterisked item forces an overall Non-Compliance regardless of total score.

### 2.2 Establishment type matrix

FSSAI has separate checklists per sector. The POC will support these four to start:

- Catering / restaurants (dine-in)
- Cloud / dark kitchens
- QSR / cafés
- Sweet shops & meat retail (smaller checklists, useful for breadth)

### 2.3 Data sources

| Source | What we get | How |
|---|---|---|
| FSSAI HRS portal (`hygiene.fssai.gov.in`) | Officially rated establishments, star ratings | Scrape + manual seed (terms permitting); fall back to public registry |
| FoSCoS public license registry | License ID, premises, category, status | Public lookup |
| Aggregator partner feed (future) | Live order-time location, hours | Partner API (Swiggy / Zomato / Razorpay merchants) |
| Operator self-upload | Self-audit checklist, photos, certs | Authenticated upload from operator portal |
| Consumer reports | "I saw a roach", photo, comment | Public form, throttled, used as a soft signal only |
| Chicago / NYC open inspection data | Labeled training data for the ML model | Public CSV / Socrata API |

The Chicago and NYC datasets are used for **transfer learning during the POC** because
labeled FSSAI inspection outcomes at scale are not yet public. Architecture leaves a
clean swap-in point when partner / FSSAI data becomes available.

---

## 3. Scoring algorithm

Two scores per establishment, combined into one headline grade:

### 3.1 Compliance score (deterministic, FSSAI-aligned)

```
raw_score   = Σ (marks awarded per item)
max_score   = Σ (marks possible per applicable item)
pct         = 100 · raw_score / max_score
has_critical_NC = any asterisked item is NC

if has_critical_NC:
    band = "NC"             # forced non-compliance
elif pct >= 90:
    band = "A+"             # exemplary (HRS ~5★)
elif pct >= 80:
    band = "A"              # satisfactory (HRS ~4★)
elif pct >= 50:
    band = "B"              # needs improvement (HRS ~2–3★)
else:
    band = "C"              # significant risk
```

Bands are intentionally aligned with FSSAI's A+/A/B grading so the score is
recognizable to operators and regulators.

### 3.2 Risk score (ML, probability of critical violation in next 90 days)

A tabular gradient-boosting model predicts the probability that an establishment will
have at least one critical violation in the next 90 days.

- **Model**: LightGBM as primary, XGBoost as fallback. Both handle categorical
  features natively, are CPU-friendly, train in minutes on POC data volumes, and
  expose SHAP for per-establishment explanation.
- **Target**: binary — was there ≥1 critical violation in the 90-day window after
  inspection date `t`. Trained on Chicago + NYC public inspection histories during
  the POC; swapped to FSSAI labels when available.
- **Features (~40)**:
  - **Establishment**: cuisine, category (cloud kitchen / dine-in / QSR), seating
    capacity, years of operation, multi-brand-on-one-premises flag.
  - **History**: prior compliance band, count of critical NCs in last 12 / 24 months,
    days since last inspection, slope of last 3 scores.
  - **Location**: ward / zone, density of food businesses within 500 m, prior
    inspection failure rate of the cluster, proximity to drain / market.
  - **Operational**: hours per day, peak-hour utilization, staff turnover (if
    available from operator portal), training completion rate.
  - **Soft signals**: consumer report count last 30 days (rate-limited, decayed),
    review sentiment around hygiene keywords (optional v2).
- **Encoding**: native LightGBM categorical handling for low-cardinality fields,
  target encoding with leave-one-out for high-cardinality (cuisine subtype, locality).
- **Validation**: time-based split (no random K-fold — leaks future into past).
  Track ROC-AUC, PR-AUC, and calibration (Brier score). Target ROC-AUC ≥ 0.75 on
  Chicago data as the POC bar; published Chicago model finds critical violations
  ~7 days earlier than baseline scheduling.

### 3.3 Composite KitchenCheck grade

```
kc_score = 0.7 · compliance_pct + 0.3 · (100 · (1 − risk_prob))
```

Compliance dominates because it's directly auditable and operator-actionable. The
risk component pulls the score down for kitchens with a clean recent inspection but
historical instability, which is the gap a static rating system misses.

The weights are configurable per deployment (the regulator surface might want
50/50; a consumer-facing badge wants 70/30 to avoid penalizing kitchens with no
recorded incidents).

### 3.4 Explainability

For every score we surface:

- The top 3 negative items from the deterministic checklist (largest mark loss).
- The top 3 risk-driving features from SHAP, in plain language ("frequent staff
  turnover", "prior critical violation 4 months ago").

Non-negotiable for trust and for letting operators improve — opaque scores get
ignored or litigated.

---

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Consumer dashboard                          │
│                       (Next.js 15 + Mapbox)                          │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  REST + JSON
┌──────────────────────────▼──────────────────────────────────────────┐
│                       FastAPI gateway                                │
│   /establishments  /scores  /reports  /operator  /admin              │
└────┬────────────┬─────────────────┬─────────────────┬────────────────┘
     │            │                 │                 │
┌────▼────┐  ┌────▼─────┐     ┌─────▼──────┐    ┌─────▼──────┐
│Postgres │  │  Redis   │     │  Scoring   │    │   ML       │
│+PostGIS │  │ (cache)  │     │  service   │    │  service   │
└─────────┘  └──────────┘     │ (rules)    │    │ (LightGBM) │
     ▲                        └────────────┘    └─────┬──────┘
     │                                                │
┌────┴───────────────────────┐                  ┌─────▼──────┐
│  Ingestion workers         │                  │  Model     │
│  (Prefect flows / cron)    │                  │  registry  │
│  - FSSAI scrape            │                  │  (MLflow)  │
│  - Chicago/NYC CSV import  │                  └────────────┘
│  - Operator upload handler │
│  - Consumer report stream  │
└────────────────────────────┘
```

### Service boundaries

- **API gateway (FastAPI)** — auth, request validation (Pydantic v2), rate limiting,
  feature flags. Pure I/O; no business logic.
- **Scoring service** — pure-function module given a checklist payload → compliance
  band. Easy to unit-test, easy to version.
- **ML service** — loads the current LightGBM model from MLflow registry; exposes
  `/predict` returning probability + SHAP top features. Versioned, A/B-able.
- **Ingestion workers** — Prefect flows for scheduled scrapes and bulk imports;
  background workers for operator uploads (image OCR for cert verification later).
- **Postgres + PostGIS** — single source of truth; geo queries for the map view.
- **Redis** — read-through cache for the consumer dashboard (score reads dominate).

The scoring and ML services are deliberately separate so that the deterministic
score is never blocked or distorted by a flaky model. If ML is down, the consumer
still sees the compliance band; only the risk overlay disappears.

---

## 5. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Same stack across API, scoring, ML — one codebase to staff |
| API framework | **FastAPI** + Pydantic v2 | Async, schema-first, auto OpenAPI, fast enough for POC |
| ORM | **SQLAlchemy 2.0** (with `SQLModel` for ergonomics) | Mature, async-capable |
| Database | **PostgreSQL 16** + **PostGIS** | Relational data + geo queries, no second store needed |
| Cache | **Redis 7** | Score reads, rate-limit counters, feature-flag cache |
| ML | **LightGBM** (primary), scikit-learn, pandas, **SHAP** | CPU-friendly, native categorical handling, explainable |
| Model registry | **MLflow** | Versioning + simple promotion workflow |
| Orchestration | **Prefect 3** | Python-native, fits POC scale without Airflow overhead |
| Frontend | **Next.js 15** (App Router) + TypeScript | Server components for SEO of establishment pages |
| UI | **Tailwind + shadcn/ui** | Speed; matches Razorpay's design idiom if reused |
| Maps | **Mapbox GL JS** (or Leaflet + OSM as free fallback) | Heat overlays, custom markers |
| Auth | **Clerk** for POC (swap to in-house JWT for prod) | Fastest path to operator + admin roles |
| Container | **Docker** + docker-compose for local | One-command spin-up for the POC |
| Deploy | **Fly.io** or **Render** for POC; **AWS ECS** for prod | Region-pin to BLR / BOM for latency |
| Observability | **Sentry** + **OpenTelemetry** → Grafana Cloud | Errors + traces from day one |
| CI | **GitHub Actions** | Lint (ruff), type-check (mypy / pyright), pytest, docker build |

Razorpay-internal alternatives (if this POC graduates to a real product behind their
merchant onboarding flow): swap Clerk for the internal IAM, deploy on the internal
k8s, and use the existing merchant DB as the establishment source of truth.

---

## 6. Dashboard features

### 6.1 Consumer surface (`kitchencheck.in/<slug>`)

- **Search**: by name, address, cuisine, area; geo-search "near me".
- **Map view**: city-wide map with markers color-coded by grade; heat overlay of
  risk score; cluster by area at zoom-out.
- **Establishment profile page**:
  - Big grade badge (A+ / A / B / C) and 0–100 KitchenCheck score.
  - Last inspected date and freshness indicator (stale > 180 days).
  - Trend sparkline of last 6 inspections.
  - Section-level breakdown (Design, Personal Hygiene, etc.) with bars.
  - "Why this score" explainer: top 3 violations + top 3 risk drivers in plain
    language.
  - Recent consumer reports (moderated, low-weight).
  - Trust badge embed snippet (`<iframe>` / image link for the operator to put on
    their own site or Zomato listing).
- **Filters**: grade band, cuisine, area, inspection freshness, "open now".
- **Compare**: side-by-side up to 3 kitchens.

### 6.2 Operator portal (`operator.kitchencheck.in`)

- Login (Clerk) → dashboard for their own premises.
- Own score and a peer benchmark (median in same cuisine + area).
- Drill-down into every checklist item that cost them marks, with FSSAI clause
  references and remediation tips.
- Self-audit mode: walk through the checklist themselves, save as a private draft.
- Document upload: FSSAI license, training certificates, pest control contracts.
- Re-inspection request flow (out of scope for week-1 POC; stubbed UI only).

### 6.3 Regulator / admin surface

- Prioritized inspection queue ranked by risk score × time-since-last-inspection
  (Chicago model showed ~25% lift in finding critical violations this way).
- Bulk import of inspector tablet data (CSV / JSON).
- Model performance dashboard: ROC-AUC trend, drift alarms, slice metrics by
  cuisine and area.
- Override audit log: any manual score adjustment is logged with reason + user.

### 6.4 Embeddable badge (Razorpay-aligned angle)

A signed image URL that any merchant can drop into their menu or order page:

```
<a href="https://kitchencheck.in/m/<slug>" target="_blank">
  <img src="https://badge.kitchencheck.in/<slug>.svg" alt="KitchenCheck grade A">
</a>
```

The badge re-fetches on every page load so it always shows the current grade — no
stale embeds. Natural integration point with Razorpay's merchant pages.

---

## 7. Implementation roadmap

Six weeks, two engineers (one full-stack, one ML), one designer part-time.

### Week 1 — Foundations
- Repo scaffolding (monorepo: `api/`, `web/`, `ml/`, `infra/`).
- Postgres schema: `establishments`, `inspections`, `inspection_items`, `scores`,
  `users`, `reports`.
- FSSAI Schedule 4 checklist digitized as YAML (sections, items, weights, critical
  flag, FSSAI clause reference).
- 50 seed establishments hand-curated from Bengaluru / Delhi NCR.
- `docker-compose up` brings up the whole stack locally.

### Week 2 — Deterministic scoring + read API
- Scoring service implementation + unit tests for every band boundary, including
  the forced-NC path on critical items.
- `/establishments`, `/establishments/{id}`, `/establishments/{id}/score` endpoints.
- Ingestion script that loads Chicago + NYC public datasets into a parallel schema
  (used for training only, never surfaced).

### Week 3 — ML risk model v1
- Feature pipeline (pandas → parquet → LightGBM dataset).
- Train + evaluate v1 on Chicago + NYC data; time-based split; target ROC-AUC ≥ 0.75.
- SHAP explainer wired into the predict endpoint.
- MLflow registry set up; model versioned and promotable.

### Week 4 — Consumer dashboard MVP
- Next.js scaffold, establishment search + profile page + map view.
- Trust-badge SVG endpoint.
- Caching layer (Redis) on score reads.
- Lighthouse / Core Web Vitals pass.

### Week 5 — Operator + admin portals
- Clerk auth, operator role mapping by FSSAI license ID.
- Operator dashboard with section-level breakdown and remediation tips.
- Admin prioritized-queue view.
- Bulk inspection CSV import endpoint.

### Week 6 — Pilot + hardening
- Pilot with 5–10 friendly operators in Bengaluru.
- Load test (target: 100 RPS on score reads at p95 < 150 ms).
- Sentry + OTel dashboards.
- Demo deck + retro + decision on prod-graduation.

### Milestones / exit criteria

- M1 (end of W2): A known-bad kitchen run through the API returns grade C with
  correct top-3 violations.
- M2 (end of W3): Model ROC-AUC ≥ 0.75 on time-based test split.
- M3 (end of W4): Consumer can search, click, and see a score in < 1 s p95.
- M4 (end of W6): At least 3 operators have logged in, viewed their score, and
  given written feedback.

---

## 8. Risks & open questions

| Risk | Mitigation |
|---|---|
| FSSAI labeled-outcome data is sparse — model trained on US data may not transfer | Use Chicago/NYC as bootstrap; aggressively re-train as soon as we have ≥500 Indian inspections; degrade gracefully (compliance-only score) if model confidence is low |
| Legal exposure from publishing scores about businesses | Start with HRS-rated establishments (already public), operator-consented uploads, and explicit "this is not a regulatory grade" disclaimer; require complaint right-of-reply |
| Operators gaming self-uploads | Self-audit scores are watermarked "self-reported" and weighted at 0.3 of an inspector-audit score; trigger a real audit if self-audit and risk model disagree |
| Consumer-report abuse (rivals reporting each other) | Throttle per device + per geo; reports are a soft signal only; never cross compliance band thresholds based on reports alone |
| Scrape ToS risk on FSSAI portals | Prefer official APIs / FoSCoS bulk lookups; build manual seed flow as fallback |
| Model bias against small / new kitchens (no history → no signal → defaulted to risky) | Cold-start kitchens get a neutral risk prior + an explicit "new establishment" badge; never display a risk-heavy score without ≥1 real inspection |

### Open questions to resolve before week 1

1. City to pilot in — Bengaluru (better tech adoption among operators) vs Delhi NCR
   (live regulator activity giving fresher ground truth).
2. Distribution channel — standalone domain, embed inside Razorpay merchant pages,
   or partner with Swiggy / Zomato.
3. Whether to pursue an MoU with the state FSSAI office before going public, or
   ship consumer-side first and engage regulators after traction.

---

## 9. Success metrics (POC → graduation)

- **Coverage**: ≥ 200 establishments scored in pilot city by end of W6.
- **Engagement**: ≥ 1,000 unique consumer pageviews on establishment profiles in
  pilot month.
- **Operator pull**: ≥ 10 operators self-onboarding without outbound effort.
- **Model quality**: ROC-AUC ≥ 0.75 on time-based split; calibration error < 0.1.
- **Regulator interest**: at least one written expression of interest from a state
  food safety office for the prioritized-queue tool.

---

## 10. References

- FSSAI Hygiene Rating Scheme: <https://hygiene.fssai.gov.in/about.php>
- FSSAI Schedule 4 / Inspection Matrices: <https://www.fssai.gov.in/cms/inspection-matrices.php>
- FSSAI revised inspection checklists PDF: <https://foscos.fssai.gov.in/assets/docs/CheckList.pdf>
- NYC restaurant letter-grading methodology: <https://www.nyc.gov/site/doh/business/food-operators/letter-grading-for-restaurants.page>
- Chicago food inspection forecasting (open source): <https://github.com/Chicago/food-inspections-evaluation>
- Hindsight analysis of the Chicago model: <https://arxiv.org/pdf/1910.04906>
- Predicting food-outlet compliance (PMC review): <https://pmc.ncbi.nlm.nih.gov/articles/PMC8656817/>
- ML for food safety monitoring (Wang 2022 review): <https://ift.onlinelibrary.wiley.com/doi/10.1111/1541-4337.12868>
- Cloud-kitchen inspection drive (Delhi, April 2026): <https://theprint.in/feature/swiggy-zomato-cloud-kitchens-delhi-food-safety-dept/2892140/>
