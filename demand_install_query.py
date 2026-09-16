"""
Print the BigQuery SQL that pulls Airbridge installs for the Demand campaigns
currently in docs/data/demand_data.json, keyed "<FB|GG>|<campaign>".

Run demand_build.py WITHOUT installs first so demand_data.json holds the current
campaign list, then:

    python demand_install_query.py

Run the printed SQL in the BQ MCP (project chotot-dwh), save the result, and feed
it to demand_build.py as the 2nd arg. See REFRESH_RUNBOOK.md step 3.
"""

import json
import pathlib

DEMAND = pathlib.Path(__file__).parent / "docs" / "data" / "demand_data.json"


def build_sql():
    d = json.loads(DEMAND.read_text())
    camps = sorted({v["campaign"] for v in d.get("dims", {}).values()})
    if not camps:
        raise SystemExit("no campaigns in demand_data.json — build it without installs first")
    in_list = ",".join('"' + c.replace('"', '\\"') + '"' for c in camps)
    return f"""SELECT TO_JSON_STRING(ARRAY_AGG(STRUCT(day, key, installs))) AS data FROM (
  SELECT CAST(Event_Date AS STRING) AS day,
    CONCAT(CASE WHEN LOWER(Channel) LIKE 'facebook%' THEN 'FB' ELSE 'GG' END,'|',Campaign) AS key,
    COUNT(*) AS installs
  FROM `chotot-dwh.chotot_airbridge.airbridge_raw_data_app_install`
  WHERE Event_Date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 75 DAY) AND CURRENT_DATE()
    AND Campaign IN ({in_list})
  GROUP BY 1,2)"""


if __name__ == "__main__":
    print(build_sql())
