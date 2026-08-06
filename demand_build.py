"""
Build docs/data/demand_data.json for the Campaign dashboard's DEMAND phase.

WHY THIS EXISTS
---------------
The themed campaigns (Thổ Địa, Back to School) flow Brand -> App install ->
Demand (Drive DAU / DAU-with-lead). App install is an *install* buy (installs /
CPI, sourced from gg_raw_creative + Airbridge). DEMAND is a *lead / DAU* buy and
its campaigns (incl. the big PMax ones) are NOT in gg_raw_creative — they live in
the team Google Sheet tab **raw_retention**, campaign-level, with lead/DWL/DAU +
cost/impr/clicks (both FB and GG channels in one tab). So the Demand sections are
sourced from here and measured on LEAD (CPL), not installs.

SOURCE
------
  Sheet "[CT] App Growth - performance tracking 2026", tab **raw_retention**
  (gid=118997184). Export that ONE tab to CSV (authenticated Chrome:
  /export?format=csv&gid=118997184 -> lands in ~/Downloads). Columns used:
    date, account_name, campaign, dau, new dau, dwl, lead, cost, clicks,
    impression, Channel  (cost already in VND).

USAGE
-----
  python demand_build.py "<raw_retention.csv>"
    -> writes docs/data/demand_data.json (per-day cost/impr/clicks/lead/dwl/dau,
       keyed by "<channel>|<campaign>"; browser sums each preset's today-relative
       window client-side, same abWindow() logic as the other snapshots).

OUTPUT SHAPE
------------
  {
    "generated_at": "...Z",
    "data_through": "YYYY-MM-DD",
    "dims": { "<channel>|<campaign>": {"campaign","channel","acct"} },
    "by_day": { "YYYY-MM-DD": { "<key>": [cost, impr, clicks, lead, dwl, dau] } }
  }
"""

import csv
import json
import sys
import datetime
import pathlib

OUT_PATH = pathlib.Path(__file__).parent / "docs" / "data" / "demand_data.json"
KEEP_DAYS = 75
# PTY demand accounts we surface in the Campaign tab (GG demand + FB demand).
ACCOUNTS = {"Chotot_PTY_DAU_New", "Chotot_pty_sgd"}


def _num(s):
    s = (s or "").strip().replace(",", "").replace("₫", "").replace("₫", "").strip()
    if not s:
        return 0
    try:
        f = float(s)
        return int(f) if f == int(f) else round(f, 2)
    except ValueError:
        return 0


def _iso(d):
    """M/D/YYYY -> YYYY-MM-DD (raw_retention uses US-style dates)."""
    d = (d or "").strip()
    if not d:
        return ""
    try:
        m, day, y = d.split("/")
        return f"{int(y):04d}-{int(m):02d}-{int(day):02d}"
    except ValueError:
        return ""


def main():
    if len(sys.argv) < 2:
        sys.exit('usage: python demand_build.py "<raw_retention.csv>"')
    rows = list(csv.reader(open(sys.argv[1], encoding="utf-8")))
    hdr = [h.strip() for h in rows[0]]
    idx = {}  # first occurrence wins (header has duplicate account_name/Channel cols)
    for i, name in enumerate(hdr):
        if name not in idx:
            idx[name] = i

    def col(r, name):
        i = idx.get(name, -1)
        return r[i] if 0 <= i < len(r) else ""

    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    dims, by_day, max_day = {}, {}, ""
    for r in rows[1:]:
        acct = col(r, "account_name").strip()
        if acct not in ACCOUNTS:
            continue
        camp = col(r, "campaign").strip()
        chan = col(r, "Channel").strip() or "GG"
        day = _iso(col(r, "date"))
        if not camp or not day or day < cutoff:
            continue
        key = f"{chan}|{camp}"
        dims.setdefault(key, {"campaign": camp, "channel": chan, "acct": acct})
        b = by_day.setdefault(day, {}).setdefault(key, [0, 0, 0, 0, 0, 0])
        b[0] += _num(col(r, "cost"))
        b[1] += _num(col(r, "impression"))
        b[2] += _num(col(r, "clicks"))
        b[3] += _num(col(r, "lead"))
        b[4] += _num(col(r, "dwl"))
        b[5] += _num(col(r, "dau"))
        max_day = max(max_day, day)

    out = {
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": max_day,
        "dims": dims,
        "by_day": by_day,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT_PATH}  ({len(dims)} campaigns, "
          f"{len(by_day)} days, through {max_day})")


if __name__ == "__main__":
    main()
