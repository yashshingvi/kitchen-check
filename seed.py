"""Seed the KitchenCheck SQLite database with 32 Bengaluru establishments.

Distribution exercises every score path:
  A+  : 6   exemplary kitchens
  A   : 7   satisfactory
  B   : 8   needs improvement
  C   : 5   significant risk
  NC  : 4   forced non-compliance (critical NC)
  UNRATED : 2 cold-start, no inspections on file

Run:  python seed.py            # writes kitchencheck.db (idempotent)
      python seed.py --force    # drop and recreate
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
from datetime import date, timedelta

import scorer

DB_PATH = os.path.join(os.path.dirname(__file__), "kitchencheck.db")

SCHEMA = """
CREATE TABLE establishments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    cuisine TEXT,
    area TEXT,
    address TEXT,
    lat REAL,
    lng REAL,
    license_id TEXT,
    years_operating INTEGER DEFAULT 1
);

CREATE TABLE inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    establishment_id INTEGER NOT NULL REFERENCES establishments(id) ON DELETE CASCADE,
    inspected_on TEXT NOT NULL,
    inspector TEXT
);
CREATE INDEX idx_inspections_estab ON inspections(establishment_id);

CREATE TABLE inspection_items (
    inspection_id INTEGER NOT NULL REFERENCES inspections(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('C','NC','PC','NA')),
    PRIMARY KEY (inspection_id, item_id)
);
"""

INSPECTORS = [
    "B. Ramesh (FSO Bengaluru-S)",
    "K. Anitha (FSO Bengaluru-E)",
    "M. Prakash (FSO Bengaluru-N)",
    "S. Lakshmi (FSO Bengaluru-W)",
    "J. Iqbal (FoSCoS audit)",
]


# ---------------------------------------------------------------------------
# Inspection generators — one per band tier.
# ---------------------------------------------------------------------------

ALL_IDS = scorer.ALL_ITEM_IDS
CRIT_IDS = scorer.CRITICAL_ITEM_IDS
NONCRIT_IDS = [i for i in ALL_IDS if i not in CRIT_IDS]


def _gen_items(profile: str, rng: random.Random) -> dict[str, str]:
    """Return an item_id -> status map producing a target band.

    Profiles: aplus, a, b, c, nc, mixed_improving, mixed_declining
    """
    items = {i: "C" for i in ALL_IDS}

    if profile == "aplus":
        # 0-1 minor PC
        flips = rng.sample(NONCRIT_IDS, k=rng.randint(0, 1))
        for i in flips:
            items[i] = "PC"

    elif profile == "a":
        # 2-3 minor issues
        pc_flips = rng.sample(NONCRIT_IDS, k=rng.randint(2, 3))
        for i in pc_flips:
            items[i] = "PC"

    elif profile == "b":
        # several issues, but no critical NC
        nc_flips = rng.sample(NONCRIT_IDS, k=rng.randint(3, 5))
        for i in nc_flips:
            items[i] = "NC"
        pc_flips = rng.sample([i for i in NONCRIT_IDS if i not in nc_flips], k=2)
        for i in pc_flips:
            items[i] = "PC"

    elif profile == "c":
        # widespread issues, but critical items still C (just under 50%)
        nc_flips = rng.sample(NONCRIT_IDS, k=rng.randint(8, 12))
        for i in nc_flips:
            items[i] = "NC"
        # Drop a couple of critical-ish-but-not-critical items to PC
        for i in rng.sample(CRIT_IDS, k=2):
            items[i] = "PC"

    elif profile == "nc":
        # forced NC by critical violation
        crit_fail = rng.sample(CRIT_IDS, k=rng.randint(1, 2))
        for i in crit_fail:
            items[i] = "NC"
        # plus some non-critical issues for realism
        for i in rng.sample(NONCRIT_IDS, k=rng.randint(2, 4)):
            items[i] = "NC"

    return items


def _inspections_for(profile: str, rng: random.Random, count: int = 3) -> list[dict]:
    """Generate `count` inspections spaced ~5-8 months apart, ending recently.

    profile may be:
      "aplus","a","b","c","nc"                — steady
      "declining"                              — A → B → C
      "improving"                              — C → B → A
      "fresh_nc_clean_history"                 — last is NC, prior were A
    """
    today = date.today()
    end_offset_days = rng.randint(20, 90)
    last_date = today - timedelta(days=end_offset_days)
    gap_days = rng.randint(150, 240)

    if profile == "declining":
        seq = ["a", "b", "c"][-count:]
    elif profile == "improving":
        seq = ["c", "b", "a"][-count:]
    elif profile == "fresh_nc_clean_history":
        seq = ["a", "a", "nc"][-count:]
    else:
        seq = [profile] * count

    inspections = []
    for i, prof in enumerate(seq):
        d = last_date - timedelta(days=gap_days * (count - 1 - i))
        inspections.append({
            "date": d.isoformat(),
            "inspector": rng.choice(INSPECTORS),
            "items": _gen_items(prof, rng),
        })
    return inspections


# ---------------------------------------------------------------------------
# Establishments — 32 Bengaluru kitchens with realistic geographic spread.
# ---------------------------------------------------------------------------

ESTABLISHMENTS = [
    # ---- A+ : exemplary ----
    {"slug": "mtr-lalbagh", "name": "MTR Lalbagh", "type": "restaurant", "cuisine": "South Indian",
     "area": "Lalbagh", "address": "14, Lalbagh Rd", "lat": 12.9507, "lng": 77.5848,
     "license_id": "10012022000001", "years": 65, "profile": "aplus"},
    {"slug": "freshmenu-hsr", "name": "FreshMenu HSR", "type": "cloud_kitchen", "cuisine": "Multi-cuisine",
     "area": "HSR Layout", "address": "Sector 2, HSR", "lat": 12.9116, "lng": 77.6473,
     "license_id": "10012022000002", "years": 8, "profile": "aplus"},
    {"slug": "blue-tokai-indiranagar", "name": "Blue Tokai Indiranagar", "type": "qsr", "cuisine": "Café",
     "area": "Indiranagar", "address": "100 Ft Rd, Indiranagar", "lat": 12.9719, "lng": 77.6412,
     "license_id": "10012022000003", "years": 6, "profile": "aplus"},
    {"slug": "wow-momo-koramangala", "name": "Wow! Momo Koramangala", "type": "qsr", "cuisine": "Tibetan",
     "area": "Koramangala", "address": "80 Ft Rd, 5th Block", "lat": 12.9352, "lng": 77.6245,
     "license_id": "10012022000004", "years": 4, "profile": "aplus"},
    {"slug": "anand-sweets-jp-nagar", "name": "Anand Sweets JP Nagar", "type": "sweet_shop", "cuisine": "Sweets",
     "area": "JP Nagar", "address": "24th Main, JP Nagar", "lat": 12.9082, "lng": 77.5855,
     "license_id": "10012022000005", "years": 30, "profile": "aplus"},
    {"slug": "thirdwave-koramangala", "name": "Third Wave Coffee Koramangala", "type": "qsr", "cuisine": "Café",
     "area": "Koramangala", "address": "7th Block, Koramangala", "lat": 12.9279, "lng": 77.6271,
     "license_id": "10012022000006", "years": 5, "profile": "aplus"},

    # ---- A : satisfactory ----
    {"slug": "vidyarthi-bhavan", "name": "Vidyarthi Bhavan", "type": "restaurant", "cuisine": "South Indian",
     "area": "Gandhi Bazaar", "address": "32 Gandhi Bazaar Main", "lat": 12.9421, "lng": 77.5707,
     "license_id": "10012022000007", "years": 78, "profile": "a"},
    {"slug": "rameshwaram-cafe-orr", "name": "Rameshwaram Café ORR", "type": "qsr", "cuisine": "South Indian",
     "area": "Bellandur", "address": "ORR, Bellandur", "lat": 12.9259, "lng": 77.6762,
     "license_id": "10012022000008", "years": 3, "profile": "a"},
    {"slug": "empire-koramangala", "name": "Empire Restaurant Koramangala", "type": "restaurant", "cuisine": "North Indian",
     "area": "Koramangala", "address": "80 Ft Rd, 4th Block", "lat": 12.9343, "lng": 77.6177,
     "license_id": "10012022000009", "years": 20, "profile": "a"},
    {"slug": "kream-krunch-malleshwaram", "name": "Kream & Krunch Malleshwaram", "type": "qsr", "cuisine": "Café",
     "area": "Malleshwaram", "address": "Sampige Rd", "lat": 13.0035, "lng": 77.5712,
     "license_id": "10012022000010", "years": 10, "profile": "a"},
    {"slug": "burma-burma-ub-city", "name": "Burma Burma UB City", "type": "restaurant", "cuisine": "Burmese",
     "area": "UB City", "address": "Vittal Mallya Rd", "lat": 12.9719, "lng": 77.5946,
     "license_id": "10012022000011", "years": 9, "profile": "a"},
    {"slug": "swensens-residency", "name": "Swensen's Residency Rd", "type": "qsr", "cuisine": "Desserts",
     "area": "Residency Rd", "address": "Residency Rd", "lat": 12.9698, "lng": 77.6033,
     "license_id": "10012022000012", "years": 15, "profile": "a"},
    {"slug": "biryani-pavilion-cloud", "name": "Biryani Pavilion (Cloud)", "type": "cloud_kitchen", "cuisine": "Biryani",
     "area": "Marathahalli", "address": "Outer Ring Rd", "lat": 12.9591, "lng": 77.6974,
     "license_id": "10012022000013", "years": 4, "profile": "improving"},

    # ---- B : needs improvement ----
    {"slug": "behrouz-biryani-btm", "name": "Behrouz Biryani BTM", "type": "cloud_kitchen", "cuisine": "Biryani",
     "area": "BTM Layout", "address": "16th Main, BTM 2", "lat": 12.9166, "lng": 77.6101,
     "license_id": "10012022000014", "years": 5, "profile": "b"},
    {"slug": "box-n-pox-whitefield", "name": "Box-n-Pox Whitefield", "type": "cloud_kitchen", "cuisine": "Asian",
     "area": "Whitefield", "address": "ITPL Main Rd", "lat": 12.9698, "lng": 77.7500,
     "license_id": "10012022000015", "years": 3, "profile": "b"},
    {"slug": "punjabi-rasoi-jayanagar", "name": "Punjabi Rasoi Jayanagar", "type": "restaurant", "cuisine": "North Indian",
     "area": "Jayanagar", "address": "11th Main, 4th Block", "lat": 12.9293, "lng": 77.5825,
     "license_id": "10012022000016", "years": 12, "profile": "b"},
    {"slug": "rolls-mania-electronic-city", "name": "Rolls Mania Electronic City", "type": "qsr", "cuisine": "Rolls",
     "area": "Electronic City", "address": "Hosur Rd, Phase 1", "lat": 12.8456, "lng": 77.6603,
     "license_id": "10012022000017", "years": 6, "profile": "b"},
    {"slug": "saravana-bhavan-rajajinagar", "name": "Saravana Bhavan Rajajinagar", "type": "restaurant", "cuisine": "South Indian",
     "area": "Rajajinagar", "address": "Dr Rajkumar Rd", "lat": 12.9911, "lng": 77.5556,
     "license_id": "10012022000018", "years": 18, "profile": "b"},
    {"slug": "kebabs-and-curries-cloud", "name": "Kebabs & Curries Co. (Cloud)", "type": "cloud_kitchen", "cuisine": "Mughlai",
     "area": "Hennur", "address": "Hennur Main Rd", "lat": 13.0298, "lng": 77.6398,
     "license_id": "10012022000019", "years": 2, "profile": "b"},
    {"slug": "sri-krishna-sweets-rt-nagar", "name": "Sri Krishna Sweets RT Nagar", "type": "sweet_shop", "cuisine": "Sweets",
     "area": "RT Nagar", "address": "RT Nagar Main Rd", "lat": 13.0234, "lng": 77.5946,
     "license_id": "10012022000020", "years": 22, "profile": "b"},
    {"slug": "chai-point-mg-road", "name": "Chai Point MG Road", "type": "qsr", "cuisine": "Café",
     "area": "MG Road", "address": "MG Rd", "lat": 12.9750, "lng": 77.6055,
     "license_id": "10012022000021", "years": 11, "profile": "b"},

    # ---- C : significant risk ----
    {"slug": "nandhini-jayanagar", "name": "Nandhini Jayanagar", "type": "restaurant", "cuisine": "Andhra",
     "area": "Jayanagar", "address": "5th Block, Jayanagar", "lat": 12.9265, "lng": 77.5836,
     "license_id": "10012022000022", "years": 14, "profile": "declining"},
    {"slug": "faasos-marathahalli", "name": "Faasos Marathahalli", "type": "cloud_kitchen", "cuisine": "Wraps",
     "area": "Marathahalli", "address": "Marathahalli Junction", "lat": 12.9573, "lng": 77.6979,
     "license_id": "10012022000023", "years": 4, "profile": "declining"},
    {"slug": "hotel-shanti-sagar-electronic-city", "name": "Hotel Shanti Sagar EC", "type": "restaurant", "cuisine": "South Indian",
     "area": "Electronic City", "address": "Phase 2, Electronic City", "lat": 12.8389, "lng": 77.6770,
     "license_id": "10012022000024", "years": 8, "profile": "c"},
    {"slug": "tandoori-knights-cloud", "name": "Tandoori Knights (Cloud)", "type": "cloud_kitchen", "cuisine": "North Indian",
     "area": "Yeshwanthpur", "address": "Yeshwanthpur Industrial Area", "lat": 13.0287, "lng": 77.5407,
     "license_id": "10012022000025", "years": 2, "profile": "c"},
    {"slug": "quick-bites-banashankari", "name": "Quick Bites Banashankari", "type": "qsr", "cuisine": "Multi-cuisine",
     "area": "Banashankari", "address": "Banashankari 2nd Stage", "lat": 12.9258, "lng": 77.5519,
     "license_id": "10012022000026", "years": 5, "profile": "c"},

    # ---- NC : forced non-compliance ----
    {"slug": "shivaji-military-hotel-jayanagar", "name": "Shivaji Military Hotel", "type": "restaurant", "cuisine": "Military Hotel",
     "area": "Jayanagar", "address": "10th Main, 4th Block", "lat": 12.9279, "lng": 77.5840,
     "license_id": "10012022000027", "years": 25, "profile": "nc"},
    {"slug": "biryani-blues-cloud", "name": "Biryani Blues (Cloud)", "type": "cloud_kitchen", "cuisine": "Biryani",
     "area": "Mahadevapura", "address": "Whitefield Main Rd", "lat": 12.9968, "lng": 77.6920,
     "license_id": "10012022000028", "years": 3, "profile": "nc"},
    {"slug": "midnight-meals-cloud", "name": "Midnight Meals (Cloud)", "type": "cloud_kitchen", "cuisine": "Late-night",
     "area": "Koramangala", "address": "5th Block, Koramangala", "lat": 12.9341, "lng": 77.6191,
     "license_id": "10012022000029", "years": 2, "profile": "fresh_nc_clean_history"},
    {"slug": "metro-meat-shop-shivajinagar", "name": "Metro Meat Shop Shivajinagar", "type": "sweet_shop", "cuisine": "Meat Retail",
     "area": "Shivajinagar", "address": "Russell Market", "lat": 12.9852, "lng": 77.6065,
     "license_id": "10012022000030", "years": 35, "profile": "nc"},

    # ---- UNRATED : cold start ----
    {"slug": "spice-route-yelahanka", "name": "Spice Route Yelahanka", "type": "restaurant", "cuisine": "Pan-Asian",
     "area": "Yelahanka", "address": "Yelahanka New Town", "lat": 13.1007, "lng": 77.5963,
     "license_id": "10012022000031", "years": 1, "profile": "unrated"},
    {"slug": "the-new-bakehouse-frazer-town", "name": "The New Bakehouse Frazer Town", "type": "qsr", "cuisine": "Bakery",
     "area": "Frazer Town", "address": "Mosque Rd", "lat": 12.9988, "lng": 77.6149,
     "license_id": "10012022000032", "years": 1, "profile": "unrated"},
]


# ---------------------------------------------------------------------------
# DB build
# ---------------------------------------------------------------------------

def build(force: bool = False) -> str:
    if force and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    if os.path.exists(DB_PATH):
        print(f"DB already exists at {DB_PATH}. Use --force to recreate.")
        return DB_PATH

    rng = random.Random(42)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    for e in ESTABLISHMENTS:
        cur = conn.execute(
            """INSERT INTO establishments
               (slug, name, type, cuisine, area, address, lat, lng, license_id, years_operating)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (e["slug"], e["name"], e["type"], e["cuisine"], e["area"], e["address"],
             e["lat"], e["lng"], e["license_id"], e["years"]),
        )
        estab_id = cur.lastrowid

        profile = e["profile"]
        if profile == "unrated":
            continue

        count = rng.randint(2, 4) if profile in ("aplus", "a", "b", "c", "nc") else 3
        inspections = _inspections_for(profile, rng, count=count)

        for insp in inspections:
            cur = conn.execute(
                "INSERT INTO inspections (establishment_id, inspected_on, inspector) VALUES (?,?,?)",
                (estab_id, insp["date"], insp["inspector"]),
            )
            insp_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO inspection_items (inspection_id, item_id, status) VALUES (?,?,?)",
                [(insp_id, k, v) for k, v in insp["items"].items()],
            )

    conn.commit()

    # Sanity check + band distribution print-out.
    distribution: dict[str, int] = {}
    rows = conn.execute("SELECT id, slug, name, type FROM establishments").fetchall()
    for eid, slug, name, etype in rows:
        inspections = _load_inspections(conn, eid)
        result = scorer.composite_score(
            {"id": eid, "slug": slug, "name": name, "type": etype},
            inspections,
        )
        distribution[result["band"]] = distribution.get(result["band"], 0) + 1

    conn.close()
    print(f"Seeded {len(ESTABLISHMENTS)} establishments → {DB_PATH}")
    print("Band distribution:", distribution)
    return DB_PATH


def _load_inspections(conn: sqlite3.Connection, establishment_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, inspected_on, inspector FROM inspections WHERE establishment_id=? ORDER BY inspected_on",
        (establishment_id,),
    ).fetchall()
    inspections = []
    for iid, idate, inspector in rows:
        items_rows = conn.execute(
            "SELECT item_id, status FROM inspection_items WHERE inspection_id=?",
            (iid,),
        ).fetchall()
        inspections.append({
            "date": idate,
            "inspector": inspector,
            "items": dict(items_rows),
        })
    return inspections


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="drop and recreate the DB")
    args = parser.parse_args()
    build(force=args.force)
