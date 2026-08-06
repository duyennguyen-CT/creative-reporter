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
sourced from here and measured primarily on LEAD (CPL), not installs.

Installs are an ADD-ON secondary metric: DAU/lead buys still drive some installs,
and Duyen wants to see how many. Those come from Airbridge (BigQuery), matched by
Campaign name + a normalized channel (facebook* -> FB, else GG), so the key equals
raw_retention's "<Channel>|<campaign>". Installs are OPTIONAL — omit the 2nd arg
and every day still carries a 0 in the installs slot.

SOURCE
------
  1. LEAD / DAU / cost / impr / clicks  <-  Sheet "[CT] App Growth - performance
     tracking 2026", tab **raw_retention** (gid=118997184). Export that ONE tab to
     CSV (authenticated Chrome: /export?format=csv&gid=118997184 -> ~/Downloads).
     Columns used: date, account_name, campaign, dau, new dau, dwl, lead, cost,
     clicks, impression, Channel  (cost already in VND).

  2. INSTALLS  <-  Airbridge MMP (BigQuery), counted by Campaign + Event_Date,
     channel normalized to FB/GG. Run this in a Claude BQ session and save the
     auto-saved tool-result file (or extract its `data` array to a plain json):

       SELECT TO_JSON_STRING(ARRAY_AGG(STRUCT(day, key, installs))) AS data FROM (
         SELECT CAST(Event_Date AS STRING) AS day,
           CONCAT(CASE WHEN LOWER(Channel) LIKE 'facebook%' THEN 'FB' ELSE 'GG' END,
                  '|', Campaign) AS key,
           COUNT(*) AS installs
         FROM `chotot-dwh.chotot_airbridge.airbridge_raw_data_app_install`
         WHERE Event_Date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 75 DAY)
                              AND CURRENT_DATE()
           AND Campaign IN (<the demand campaign names from demand_data.json dims>)
         GROUP BY 1,2)

     Only keys that also exist in raw_retention (i.e. real demand campaigns) are
     kept; orphan installs are dropped.

USAGE
-----
  python demand_build.py "<raw_retention.csv>" ["<airbridge_installs.(json|txt)>"]
    -> writes docs/data/demand_data.json (per-day cost/impr/clicks/lead/dwl/dau/
       installs, keyed by "<channel>|<campaign>"; browser sums each preset's
       today-relative window client-side, same abWindow() logic as the other
       snapshots).

OUTPUT SHAPE
------------
  {
    "generated_at": "...Z",
    "data_through": "YYYY-MM-DD",
    "install_through": "YYYY-MM-DD",     # latest Airbridge day (or "")
    "dims": { "<channel>|<campaign>": {"campaign","channel","acct"} },
    "by_day": { "YYYY-MM-DD": { "<key>": [cost,impr,clicks,lead,dwl,dau,installs] } }
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


def load_installs(path):
    """Return (installs_by_day, max_day) from the Airbridge BQ result file.

    Accepts the raw ARRAY_AGG json array [{day,key,installs},...], the MCP
    tool-result wrapper {"rows":[{"f":[{"v":"<json array string>"}]}], ...}, or
    a {"data": "<json array string>"} wrapper. installs_by_day: day -> key -> n.
    """
    raw = json.load(open(path, encoding="utf-8"))
    if isinstance(raw, dict) and "rows" in raw:
        arr = json.loads(raw["rows"][0]["f"][0]["v"])
    elif isinstance(raw, dict) and "data" in raw:
        arr = json.loads(raw["data"]) if isinstance(raw["data"], str) else raw["data"]
    else:
        arr = raw
    out, max_day = {}, ""
    for e in arr or []:
        day, key, n = e["day"], e["key"], int(e["installs"])
        d = out.setdefault(day, {})
        d[key] = d.get(key, 0) + n
        max_day = max(max_day, day)
    return out, max_day


def main():
    if len(sys.argv) < 2:
        sys.exit('usage: python demand_build.py "<raw_retention.csv>" '
                 '["<airbridge_installs.(json|txt)>"]')
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
        # [cost, impr, clicks, lead, dwl, dau, installs] — installs filled below.
        b = by_day.setdefault(day, {}).setdefault(key, [0, 0, 0, 0, 0, 0, 0])
        b[0] += _num(col(r, "cost"))
        b[1] += _num(col(r, "impression"))
        b[2] += _num(col(r, "clicks"))
        b[3] += _num(col(r, "lead"))
        b[4] += _num(col(r, "dwl"))
        b[5] += _num(col(r, "dau"))
        max_day = max(max_day, day)

    # Merge Airbridge installs (add-on secondary metric) into slot [6]. Only keep
    # installs for keys that exist in raw_retention (real demand campaigns); an
    # install-only day gets a fresh 0-filled row so the window math stays correct.
    inst_through, inst_total, dropped = "", 0, 0
    if len(sys.argv) >= 3:
        inst_by_day, inst_through = load_installs(sys.argv[2])
        for day, m in inst_by_day.items():
            if day < cutoff:
                continue
            for key, n in m.items():
                if key not in dims:
                    dropped += n
                    continue
                b = by_day.setdefault(day, {}).setdefault(key, [0, 0, 0, 0, 0, 0, 0])
                b[6] += n
                inst_total += n
                max_day = max(max_day, day)

    out = {
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_through": max_day,
        "install_through": inst_through,
        "dims": dims,
        "by_day": by_day,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT_PATH}  ({len(dims)} campaigns, "
          f"{len(by_day)} days, through {max_day})")
    if len(sys.argv) >= 3:
        print(f"  installs merged: {inst_total:,} (through {inst_through})"
              + (f"  · dropped {dropped:,} orphan installs" if dropped else ""))


if __name__ == "__main__":
    main()
