"""
Merge a fresh Airbridge install pull into docs/data/airbridge_installs.json.

WHY THIS EXISTS
---------------
Installs on the dashboard come from the Airbridge MMP (BigQuery table
chotot-dwh.chotot_airbridge.airbridge_raw_data_app_install), NOT from Meta's
self-reported numbers. BigQuery is only reachable through a Claude session with
Duyen's BQ MCP (CI has NO BigQuery credentials), so the refresh is:

  1. In a Claude session, run the QUERY below (fill in the recent date window +
     the ad_id IN-list — see airbridge_query_template.sql). It returns ONE row:
     a JSON string `by_day` = { "YYYY-MM-DD": { "<ad_id>": installs, ... }, ... }.
     Big results get auto-saved to a tool-results/*.txt file.
  2. Run:  python airbridge_merge.py <path-to-bq-result-or-raw-json>
     -> merges the new days into docs/data/airbridge_installs.json,
        OVERWRITING any day present in the new pull (so late-attributed
        installs get corrected) and KEEPING every older day untouched.
  3. git add docs/data/airbridge_installs.json && commit && push.
     GitHub Pages redeploys and the dashboard shows the fresh installs — no Meta
     rebuild needed (spend stays on its own weekly cadence).

INPUT FORMATS ACCEPTED
----------------------
  * A BQ MCP tool-result file: {"rows":[{"f":[{"v":"<by_day json string>"}]}], ...}
  * A raw by_day object:        {"2026-08-05": {"<ad_id>": 12}, ...}
  * A full snapshot file:       {"by_day": {...}, ...}
"""

import json
import sys
import datetime
import pathlib

OUT_PATH = pathlib.Path(__file__).parent / "docs" / "data" / "airbridge_installs.json"
KEEP_DAYS = 75  # prune anything older than this many days to bound file size


def extract_by_day(raw):
    """Pull the {day: {ad_id: n}} map out of any of the accepted input shapes."""
    if isinstance(raw, dict) and "rows" in raw and raw["rows"]:
        s = raw["rows"][0]["f"][0]["v"]
        return json.loads(s)
    if isinstance(raw, dict) and "by_day" in raw:
        return raw["by_day"]
    if isinstance(raw, dict):
        return raw
    raise ValueError("Unrecognized input JSON shape")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python airbridge_merge.py <bq-result-or-raw-json>")
    new = extract_by_day(json.load(open(sys.argv[1])))
    if not new:
        sys.exit("no rows in input — nothing to merge")

    snap = json.loads(OUT_PATH.read_text()) if OUT_PATH.exists() else {}
    by_day = snap.get("by_day", {})

    # Overwrite each day present in the new pull; keep all other days.
    for day, m in new.items():
        by_day[day] = {str(k): int(v) for k, v in m.items()}

    # Prune old days to keep the file small (dashboard only needs ~65 days back).
    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    by_day = {d: m for d, m in by_day.items() if d >= cutoff}

    days = sorted(by_day)
    out = {
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "chotot-dwh.chotot_airbridge.airbridge_raw_data_app_install",
        "channel": "facebook.business",
        "count_rule": "all_install_events",
        "join_key": "Ad_Creative_ID = Meta ad_id",
        "date_range": [days[0], days[-1]] if days else [],
        "by_day": by_day,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    total = sum(sum(m.values()) for m in by_day.values())
    print(f"merged {len(new)} day(s); snapshot now {len(days)} days "
          f"({days[0]}..{days[-1]}), {total:,} installs -> {OUT_PATH}")


if __name__ == "__main__":
    main()
