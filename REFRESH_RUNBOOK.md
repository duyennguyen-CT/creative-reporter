# Data refresh runbook

The dashboard reads six JSON files in `docs/data/`. They refresh on **different
cadences and sources** — the per-source "Data đến" strip at the top of the
dashboard shows each one's own as-of date (green ≤2d, amber ≤9d, red ≥10d).

This runbook is the single source of truth for bringing them current. Steps that
need BigQuery or the Google Sheet must run in a Claude Code session that has the
**BigQuery MCP** and **Drive MCP** connected (CI has neither).

| File | Source | Refresh |
|---|---|---|
| `latest.json` | Meta Marketing API (`build_report.py`) | ✅ **Auto** — GitHub Actions, Mon 09:00 ICT |
| `airbridge_installs.json` | BigQuery Airbridge (Meta channel) | ⚠️ Manual — step 1 below |
| `google_data.json` | Google Sheet `gg_raw_creative` + BigQuery Airbridge (`google.adwords`) | ⚠️ Manual — step 2 |
| `demand_data.json` | Google Sheet `raw_retention` + BigQuery Airbridge (all channels) | ⚠️ Manual — step 3 |
| `google_funnel_data.json` | BigQuery attribution query (repo `chotot-digital`) | ⛔ Blocked — see "Funnel" |
| `meta_funnel_data.json` | BigQuery attribution + `chotot_marketing.meta_ads_ad` cost | ⛔ Blocked — see "Funnel" |

Only `latest.json` refreshes on its own. Everything below is the manual /
scheduled work. Whenever a step's result is too big to inline, the MCP saves it
to a `tool-results/*.txt` file — pass that path straight to the build script,
they all accept the MCP wrapper shape.

---

## One-time setup per session

```bash
python3 -m venv /tmp/refresh-venv && /tmp/refresh-venv/bin/pip install -q openpyxl
```

`openpyxl` is only needed to split the Google Sheet (steps 2–3); the build
scripts themselves are stdlib-only.

---

## Step 1 — Airbridge installs (Meta) → `airbridge_installs.json`

Fixes Meta creative **CPI** (installs come from the MMP, not Meta's self-report).

1. Print the SQL and run it in the BQ MCP (project `chotot-dwh`):
   ```bash
   python airbridge_query.py 30      # 30-day window self-heals gaps
   ```
2. Merge the saved result:
   ```bash
   python airbridge_merge.py <bq-result.txt>
   ```

## Step 2 — Google Ads → `google_data.json`

1. Download the sheet as `.xlsx` via Drive MCP and split out the tab:
   - File: **`[CT] App Growth - performance tracking 2026`**
     (`fileId = 1eLdUTKfR9yHcUnnEfyIouZlCiVDPvR6yn3igxdoy8eE`),
     export mime `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.
   - ```bash
     /tmp/refresh-venv/bin/python extract_sheet_tabs.py <workbook.xlsx> /tmp/sheet
     ```
2. Pull Google-channel installs in the BQ MCP:
   ```sql
   SELECT TO_JSON_STRING(ARRAY_AGG(STRUCT(day, key, installs))) AS data FROM (
     SELECT CAST(Event_Date AS STRING) AS day,
       CONCAT(CAST(Campaign_ID AS STRING),'_',CAST(Ad_Group_ID AS STRING)) AS key,
       COUNT(*) AS installs
     FROM `chotot-dwh.chotot_airbridge.airbridge_raw_data_app_install`
     WHERE Channel='google.adwords'
       AND Event_Date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 75 DAY) AND CURRENT_DATE()
     GROUP BY 1,2)
   ```
3. Build:
   ```bash
   python google_build.py /tmp/sheet/gg_raw_creative.csv <bq-result.txt>
   ```

## Step 3 — Demand → `demand_data.json`

1. Split the sheet (same download as step 2; reuse `/tmp/sheet`).
2. Build once **without** installs to get the campaign list:
   ```bash
   python demand_build.py /tmp/sheet/raw_retention.csv
   ```
   Read the `dims[*].campaign` names from `docs/data/demand_data.json`.
3. Pull installs for those campaigns in the BQ MCP (fill the IN-list):
   ```sql
   SELECT TO_JSON_STRING(ARRAY_AGG(STRUCT(day, key, installs))) AS data FROM (
     SELECT CAST(Event_Date AS STRING) AS day,
       CONCAT(CASE WHEN LOWER(Channel) LIKE 'facebook%' THEN 'FB' ELSE 'GG' END,'|',Campaign) AS key,
       COUNT(*) AS installs
     FROM `chotot-dwh.chotot_airbridge.airbridge_raw_data_app_install`
     WHERE Event_Date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 75 DAY) AND CURRENT_DATE()
       AND Campaign IN (<campaign names>)
     GROUP BY 1,2)
   ```
4. Rebuild **with** installs:
   ```bash
   python demand_build.py /tmp/sheet/raw_retention.csv <bq-result.txt>
   ```

## Step 4 — commit & push

```bash
git add docs/data/*.json && git commit -m "data: weekly refresh $(date -u +%F)" && git push
```

GitHub Pages redeploys in ~1 min. The freshness strip will show the new dates.

---

## Funnel (blocked)

`google_funnel_data.json` / `meta_funnel_data.json` power the **Google & Meta**
view. Both are built from an **attribution query that lives in the `chotot-digital`
repo** (`tools/queries/shared/meta-content-creative-funnel.sql` and its Google
twin), which maps `utm_content` → creative → matured DAU/lead cohorts. That SQL
is not in this repo, so the funnel can't be rebuilt from here yet.

**Meta cost is NOT the blocker.** The old note said `chotot_marketing.meta_ads_ad`
was access-blocked (for Kiet's account), forcing a manual Meta-API cost pull. From
the BQ MCP used for the other steps that table **is readable** and carries full
ad-level cost + video/engagement metrics. So automating the Meta funnel needs
**BigQuery read access to `chotot-dwh.chotot_marketing`** for whoever/whatever runs
the refresh — a Data-team grant, not Meta edit rights. To finish the funnel:
1. Get the two attribution SQLs from `chotot-digital` (or have Kiet share them).
2. Source Meta top-funnel cost from `chotot_marketing.meta_ads_ad` instead of the
   manual CSV, and add both to steps above.
