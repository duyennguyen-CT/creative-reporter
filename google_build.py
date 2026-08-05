"""
Build docs/data/google_data.json for the dashboard's Google Ads tab.

WHY THIS EXISTS
---------------
The Google tab mirrors the Meta tab but its data comes from two manual sources
(there is NO Google Ads API wired up, and BigQuery has no CI access):

  1. COST / IMPR / CLICKS  <- a Google Sheet the team maintains
       "[CT] App Growth - performance tracking 2026", tab "gg_raw_creative".
       Export that ONE tab to CSV (File > Download, or the per-tab CSV export
       URL) — columns:
         Day, Account (Customer), Account (Customer) ID, Campaign, Campaign ID,
         Ad group, Ad group ID, Cost, Impr., Clicks, Conversions
       Cost is already in VND.

  2. INSTALLS  <- Airbridge MMP (BigQuery), Channel='google.adwords', counted by
       Campaign_ID + Ad_Group_ID + Event_Date. These IDs equal the sheet's
       "Campaign ID" / "Ad group ID" (verified 20/20 match). Run this in a Claude
       BQ session and save the auto-saved tool-result file:

         SELECT TO_JSON_STRING(ARRAY_AGG(STRUCT(day, key, installs))) AS data FROM (
           SELECT CAST(Event_Date AS STRING) AS day,
             CONCAT(CAST(Campaign_ID AS STRING),'_',CAST(Ad_Group_ID AS STRING)) AS key,
             COUNT(*) AS installs
           FROM `chotot-dwh.chotot_airbridge.airbridge_raw_data_app_install`
           WHERE Channel='google.adwords'
             AND Event_Date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 75 DAY) AND CURRENT_DATE()
           GROUP BY 1,2)

USAGE
-----
  python google_build.py <sheet.csv> <airbridge_bq_result.(txt|json)>
    -> writes docs/data/google_data.json (per-day cost/impr/clicks/installs,
       keyed by "<campaign_id>_<ad_group_id>"; the browser sums each preset's
       today-relative window client-side, exactly like airbridge_installs.json).

OUTPUT SHAPE
------------
  {
    "generated_at": "...Z",
    "data_through":  "YYYY-MM-DD",   # latest sheet day
    "install_through": "YYYY-MM-DD", # latest Airbridge day
    "accounts": ["Chotot_app_pty", ...],           # ordered by total spend desc
    "dims": { "<cid>_<agid>": {"acct","camp","ag","cid","agid"} },
    "by_day": { "YYYY-MM-DD": { "<cid>_<agid>": [cost, impr, clicks, installs] } }
  }
"""

import csv
import json
import sys
import datetime
import pathlib

OUT_PATH = pathlib.Path(__file__).parent / "docs" / "data" / "google_data.json"
KEEP_DAYS = 75


def _num(s):
    """Parse '463,544' / '1,234.5' / '' -> number (0 when blank)."""
    s = (s or "").strip().replace(",", "")
    if not s:
        return 0
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except ValueError:
        return 0


def load_sheet(path):
    """Return (dims, cost_by_day, max_day) from the gg_raw_creative CSV."""
    rows = list(csv.reader(open(path, encoding="utf-8")))
    hi = next(i for i, r in enumerate(rows) if r and r[0].strip() == "Day")
    hdr = [h.strip() for h in rows[hi]]
    idx = {name: hdr.index(name) for name in hdr}

    def col(r, name):
        return r[idx[name]] if idx.get(name, -1) < len(r) else ""

    dims = {}
    cost_by_day = {}   # day -> key -> [cost, impr, clicks]
    max_day = ""
    for r in rows[hi + 1:]:
        if not r or not r[0].strip():
            continue
        day = col(r, "Day").strip()
        cid = col(r, "Campaign ID").strip()
        agid = col(r, "Ad group ID").strip()
        if not day or not cid or not agid:
            continue
        key = f"{cid}_{agid}"
        max_day = max(max_day, day)
        dims.setdefault(key, {
            "acct": col(r, "Account (Customer)").strip(),
            "camp": col(r, "Campaign").strip(),
            "ag": col(r, "Ad group").strip(),
            "cid": cid, "agid": agid,
        })
        b = cost_by_day.setdefault(day, {}).setdefault(key, [0, 0, 0])
        b[0] += _num(col(r, "Cost"))
        b[1] += _num(col(r, "Impr."))
        b[2] += _num(col(r, "Clicks"))
    return dims, cost_by_day, max_day


def load_installs(path):
    """Return (installs_by_day, max_day) from the Airbridge BQ result file.

    Accepts either the raw ARRAY_AGG json array, or the MCP tool-result wrapper
    {"rows":[{"f":[{"v":"<json array string>"}]}], ...}.
    """
    raw = json.load(open(path))
    if isinstance(raw, dict) and "rows" in raw:
        arr = json.loads(raw["rows"][0]["f"][0]["v"])
    elif isinstance(raw, dict) and "data" in raw:
        arr = json.loads(raw["data"]) if isinstance(raw["data"], str) else raw["data"]
    else:
        arr = raw
    out = {}
    max_day = ""
    for e in arr:
        day, key, n = e["day"], e["key"], int(e["installs"])
        out.setdefault(day, {})[key] = out.setdefault(day, {}).get(key, 0) + n
        max_day = max(max_day, day)
    return out, max_day


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: python google_build.py <sheet.csv> <airbridge_bq_result>")
    dims, cost_by_day, sheet_max = load_sheet(sys.argv[1])
    inst_by_day, inst_max = load_installs(sys.argv[2])

    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()

    # Merge cost + installs into one by_day map, keyed by cid_agid. Only keep
    # keys we have dims for (known ad groups from the sheet); drop orphan installs.
    by_day = {}
    dropped = 0
    days = set(d for d in cost_by_day if d >= cutoff) | set(d for d in inst_by_day if d >= cutoff)
    for day in days:
        cb = cost_by_day.get(day, {})
        ib = inst_by_day.get(day, {})
        merged = {}
        for key, (cost, impr, clk) in cb.items():
            merged[key] = [cost, impr, clk, ib.get(key, 0)]
        for key, n in ib.items():
            if key in merged:
                continue
            if key in dims:            # install-only day for a known ad group
                merged[key] = [0, 0, 0, n]
            else:
                dropped += n
        if merged:
            by_day[day] = merged

    # Order accounts by total spend desc (nicer default sub-tab order).
    acct_spend = {}
    for day, m in by_day.items():
        for key, v in m.items():
            acct_spend[dims[key]["acct"]] = acct_spend.get(dims[key]["acct"], 0) + v[0]
    accounts = [a for a, _ in sorted(acct_spend.items(), key=lambda kv: -kv[1])]

    out = {
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": sheet_max,
        "install_through": inst_max,
        "accounts": accounts,
        "dims": dims,
        "by_day": by_day,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    tot_cost = sum(v[0] for m in by_day.values() for v in m.values())
    tot_inst = sum(v[3] for m in by_day.values() for v in m.values())
    dd = sorted(by_day)
    print(f"wrote {OUT_PATH}")
    print(f"  {len(dd)} days ({dd[0]}..{dd[-1]}), {len(accounts)} accounts, "
          f"{len(dims)} ad groups")
    print(f"  spend ₫{tot_cost:,.0f} · installs {tot_inst:,} "
          f"(sheet through {sheet_max}, installs through {inst_max})")
    if dropped:
        print(f"  note: dropped {dropped:,} installs with no matching sheet ad group")


if __name__ == "__main__":
    main()
