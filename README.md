# Kitchen Check

A FastAPI service that records kitchen hygiene / operational-readiness checks and computes a weighted score and pass/warn/fail status for each one.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python seed.py
uvicorn app.main:app --reload
```

Then open:

- Web UI: <http://127.0.0.1:8000/>
- Interactive API docs: <http://127.0.0.1:8000/docs>

`python seed.py` populates the in-memory store with sample checks so the UI has data on first load. The store is in-process, so re-run `seed.py` after each restart if you want the seed data back.

## Project Layout

```
kitchen-check/
├── app/
│   ├── main.py        # FastAPI app, route handlers, static mount
│   ├── data.py        # In-memory data store
│   └── scoring.py     # Item weights and scoring rules
├── web/
│   └── index.html     # Frontend UI (vanilla JS, fetches the API)
├── scorer.py          # CLI: score a check from a JSON file
├── seed.py            # Populate the store with sample checks
├── requirements.txt
└── README.md
```

## API Endpoints

### `GET /`
Serves `web/index.html`.

### `GET /checks`
List all checks with their items, score, and status.

### `GET /checks/{check_id}`
Fetch a single check by id.

### `POST /checks`
Create a new check. Request body:

```json
{
  "kitchen": "Kitchen A",
  "items": [
    {"name": "temperature_log", "passed": true},
    {"name": "handwash_station", "passed": false},
    {"name": "uniform", "passed": true}
  ]
}
```

Response includes the assigned `id`, computed `score` (0–100), and `status` (`pass` / `warn` / `fail`).

### `PUT /checks/{check_id}`
Replace the items on an existing check. Score and status are recomputed.

### `DELETE /checks/{check_id}`
Remove a check.

### `GET /score/summary`
Aggregate stats across all checks: total count, average score, pass rate, and a breakdown by kitchen.

## How Scoring Works

Each check is a list of items, each tagged as **passed** or **failed** by the inspector. Items are assigned a tier in `app/scoring.py`, and each tier has a weight:

| Tier      | Weight | Examples                                       |
|-----------|--------|------------------------------------------------|
| critical  | 5      | temperature controls, handwashing, allergen separation |
| major     | 3      | labelling, dated storage, cleaning logs        |
| minor     | 1      | uniform, signage, tidiness                     |

The numeric score is the weighted percentage of items that passed:

```
score = round(100 * sum(weight for passing items) / sum(weight for all items))
```

The status is derived from the score, with one override:

- **Any failed critical item → `fail`**, regardless of score. A single critical violation is disqualifying, mirroring real food-safety inspection grading.
- Otherwise:
  - `score >= 90` → `pass`
  - `70 <= score < 90` → `warn`
  - `score < 70` → `fail`

This means a kitchen can rack up many minor failings and still warn rather than fail, but cannot pass a check while a critical item is failing.

## CLI Scorer

To score a check from the command line without running the server:

```bash
python scorer.py path/to/check.json
```

The JSON file should use the same shape as the `POST /checks` body. Output is the score and status.

## Notes

- Data lives in memory only — restart clears everything. Re-run `python seed.py` to reload samples.
- Use a different port with `uvicorn app.main:app --reload --port 8080`.
- The `--reload` flag is for development; drop it for a production-style run.
