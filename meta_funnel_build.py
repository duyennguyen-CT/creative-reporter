"""Build the Meta side of the Creative Funnel view (docs/data/meta_funnel_data.json).

Unlike google_funnel_build.py, this has no single scheduled BigQuery source yet:
Meta ad-cost data isn't reachable from BigQuery (chotot_marketing.meta_ads_ad is
access-blocked for Kiet's account as of 2026-08-31), so top-funnel cost has to be
pulled per account from the Meta Marketing API / Ads MCP tool and exported to a CSV
first. This script only does the merge.

Inputs:
  1. Bottom-funnel CSV: run tools/queries/shared/meta-content-creative-funnel.sql
     (in chotot-digital) and export to CSV. Columns: date, vertical, utm_campaign,
     utm_content, dau, dwa, dwl_14d, lead_14d, is_mature_cohort, coverage_pct.
  2. Top-funnel cost CSV: date, vertical, utm_content, impressions, clicks, spend_sgd
     — one row per (date, vertical, ad name), summed across ad IDs sharing a name.
     As of 2026-08-31 this covers Chotot_pty_sgd / Chotot_job_sgd / Chotot_gds_elt_sgd
     only (via Ads MCP) — Chotot_veh_sgd and Chotot_gds_c2c_sgd are not yet queryable
     through that tool. VEH rows below will show funnel numbers with no matched cost
     until a source for VEH exists.

Usage:
  python meta_funnel_build.py <bottom_funnel.csv> <top_funnel_cost.csv>
"""
import csv
import json
import pathlib
import sys
from collections import Counter
from urllib.parse import unquote_plus

OUT = pathlib.Path(__file__).parent / "docs" / "data" / "meta_funnel_data.json"
SGD_TO_VND = 20377


def norm(s):
    if not s:
        return s
    return unquote_plus(s).strip()


def num(v):
    if v in (None, "", "None"):
        return None
    try:
        return int(v)
    except ValueError:
        return float(v)


def load_cost(path):
    cost = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["date"], row["vertical"], norm(row["utm_content"]))
            c = cost.setdefault(key, {"impressions": 0, "clicks": 0, "spend_vnd": 0.0})
            c["impressions"] += int(row["impressions"])
            c["clicks"] += int(row["clicks"])
            c["spend_vnd"] += float(row["spend_sgd"]) * SGD_TO_VND
    return cost


def load_funnel(path):
    funnel = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["date"], row["vertical"], norm(row["utm_content"]))
            funnel.setdefault(key, []).append(row)
    return funnel


def merge(cost, funnel):
    rows = []
    for key in set(cost) | set(funnel):
        date, vertical, utm_content = key
        c = cost.get(key)
        frows = funnel.get(key)
        if frows:
            campaigns = sorted({r["utm_campaign"] for r in frows if r["utm_campaign"]})
            utm_campaign = campaigns[0] if len(campaigns) == 1 else ("; ".join(campaigns) if campaigns else None)
            dau = sum(num(r["dau"]) or 0 for r in frows) or None
            dwa = sum(num(r["dwa"]) or 0 for r in frows) if any(r["dwa"] not in ("", "None") for r in frows) else None
            dwl_vals = [num(r["dwl_14d"]) for r in frows if r["dwl_14d"] not in ("", "None")]
            lead_vals = [num(r["lead_14d"]) for r in frows if r["lead_14d"] not in ("", "None")]
            dwl_14d = sum(dwl_vals) if dwl_vals else None
            lead_14d = sum(lead_vals) if lead_vals else None
            is_mature = all(r["is_mature_cohort"] == "True" for r in frows)
            cov_vals = [num(r["coverage_pct"]) for r in frows if r["coverage_pct"] not in ("", "None")]
            coverage_pct = sum(cov_vals) / len(cov_vals) if cov_vals else None
        else:
            utm_campaign = dau = dwa = dwl_14d = lead_14d = coverage_pct = None
            is_mature = False

        if c and frows:
            match_status = "MATCHED"
        elif c:
            match_status = "UNMATCHED_PLATFORM_COST"
        else:
            match_status = "UNMATCHED_FUNNEL"

        rows.append({
            "date": date, "vertical": vertical, "utm_campaign": utm_campaign, "utm_content": utm_content,
            "ad_platform": "META", "match_status": match_status,
            "impressions": c["impressions"] if c else None,
            "clicks": c["clicks"] if c else None,
            "spend_vnd": round(c["spend_vnd"], 0) if c else None,
            "dau": dau, "dwa": dwa, "dwl_14d": dwl_14d, "lead_14d": lead_14d,
            "is_mature_cohort": is_mature,
            "coverage_pct": round(coverage_pct, 1) if coverage_pct is not None else None,
        })
    rows.sort(key=lambda r: (r["date"], r["vertical"], r["utm_content"]))
    return rows


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python meta_funnel_build.py <bottom_funnel.csv> <top_funnel_cost.csv>")
    funnel = load_funnel(sys.argv[1])
    cost = load_cost(sys.argv[2])
    rows = merge(cost, funnel)
    data_through = max(r["date"] for r in rows)
    OUT.write_text(json.dumps({"generated_at": data_through, "data_through": data_through, "rows": rows},
                               ensure_ascii=False, separators=(",", ":")))
    by_vertical = Counter(r["vertical"] for r in rows)
    by_status = Counter(r["match_status"] for r in rows)
    print(f"wrote {OUT}: {len(rows)} rows through {data_through}")
    print(f"  by vertical: {dict(by_vertical)}")
    print(f"  by match_status: {dict(by_status)}")


if __name__ == "__main__":
    main()
