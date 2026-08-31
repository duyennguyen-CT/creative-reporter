"""Convert a BigQuery CSV export into the static Google Creative Funnel data file.

Usage:
  python google_funnel_build.py <kiet_utm_content_creative_dashboard_google.csv>
"""
import csv
import json
import pathlib
import sys

OUT = pathlib.Path(__file__).parent / "docs" / "data" / "google_funnel_data.json"
NUMBERS = {"impressions", "clicks", "spend_vnd", "dau", "dwa", "dwl_14d", "lead_14d", "coverage_pct"}


def number(value):
    value = (value or "").strip()
    if not value:
        return None
    return float(value) if "." in value else int(value)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python google_funnel_build.py <BigQuery CSV export>")
    with open(sys.argv[1], encoding="utf-8-sig", newline="") as source:
        rows = []
        for raw in csv.DictReader(source):
            row = {k: (number(v) if k in NUMBERS else v) for k, v in raw.items()}
            row["is_mature_cohort"] = str(raw.get("is_mature_cohort", "")).lower() == "true"
            rows.append(row)
    data_through = max((r.get("date", "") for r in rows), default="")
    OUT.write_text(json.dumps({"generated_at": data_through, "data_through": data_through, "rows": rows}, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT}: {len(rows)} rows through {data_through}")


if __name__ == "__main__":
    main()
