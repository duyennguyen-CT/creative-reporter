"""
Print the BigQuery SQL that pulls Airbridge app-install counts per day per ad,
scoped to the ad_ids currently on the dashboard (read from docs/data/latest.json).

Usage:
    python airbridge_query.py [DAYS]     # DAYS defaults to 14 (daily refresh)
    python airbridge_query.py 65         # full backfill window

WHY 14 DAYS (not 3): each refresh OVERWRITES the days it pulls and keeps the
rest, so a wide window self-heals gaps. The old 3-day window meant any day the
refresh didn't run was lost forever, and newly-launched ads (which only enter
latest.json on the weekly spend rebuild) had their early install days scroll
out of the window before they were ever captured -> permanently blank installs.
A 14-day window comfortably covers the weekly latest.json cadence.

Then, in a Claude session with the BQ MCP, run the printed SQL against project
chotot-dwh. It returns ONE row: a JSON string `by_day`. Save that result and
feed it to airbridge_merge.py. See airbridge_merge.py for the full loop.

Counting rule: every row in the table = 1 install event (Event_Category
"Install (App)"), Channel = 'facebook.business', joined on
Ad_Creative_ID = Meta ad_id.
"""

import json
import sys
import pathlib

LATEST = pathlib.Path(__file__).parent / "docs" / "data" / "latest.json"


def dashboard_ad_ids():
    d = json.loads(LATEST.read_text())
    ranges = d.get("ranges") or {"_": d}
    ids = set()
    for rng in ranges.values():
        for acc in rng.get("accounts", []):
            for c in acc.get("creatives", []):
                if c.get("id"):
                    ids.add(str(c["id"]))
    return sorted(ids)


def build_sql(days):
    # NO ad_id gate: pull EVERY facebook.business ad's installs. The old
    # `Ad_Creative_ID IN (dashboard ids)` gate silently dropped installs for any
    # ad not yet in latest.json — i.e. every ad launched since the last weekly
    # spend rebuild — so freshly-launched ads showed blank installs until (and
    # unless) their install days were still inside the window on a later run.
    # Pulling ungated means whatever ad_ids the dashboard has (now or after the
    # next spend refresh) will always find their installs. Extra ad_ids in the
    # snapshot are harmless: the dashboard only reads its own ids.
    return f"""WITH daily AS (
  SELECT Event_Date AS d, CAST(Ad_Creative_ID AS STRING) AS a, COUNT(*) AS c
  FROM `chotot-dwh.chotot_airbridge.airbridge_raw_data_app_install`
  WHERE Channel='facebook.business'
    AND Event_Date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {days} DAY) AND CURRENT_DATE()
    AND Ad_Creative_ID IS NOT NULL
  GROUP BY d, a
),
per_day AS (
  SELECT CAST(d AS STRING) AS day,
    CONCAT('{{', STRING_AGG(TO_JSON_STRING(a) || ':' || CAST(c AS STRING), ','), '}}') AS m
  FROM daily GROUP BY d
)
SELECT CONCAT('{{', STRING_AGG(TO_JSON_STRING(day) || ':' || m, ',' ORDER BY day), '}}') AS by_day
FROM per_day"""


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    print(build_sql(days))
