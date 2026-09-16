"""
Print the BigQuery SQL that pulls Airbridge Google-channel (google.adwords)
installs per day, keyed "<campaign_id>_<ad_group_id>" to match the sheet's
gg_raw_creative Campaign ID / Ad group ID.

    python google_install_query.py

Run the printed SQL in the BQ MCP (project chotot-dwh), save the result, and feed
it to google_build.py as the 2nd arg. See REFRESH_RUNBOOK.md step 2.
"""


def build_sql(days=75):
    return f"""SELECT TO_JSON_STRING(ARRAY_AGG(STRUCT(day, key, installs))) AS data FROM (
  SELECT CAST(Event_Date AS STRING) AS day,
    CONCAT(CAST(Campaign_ID AS STRING),'_',CAST(Ad_Group_ID AS STRING)) AS key,
    COUNT(*) AS installs
  FROM `chotot-dwh.chotot_airbridge.airbridge_raw_data_app_install`
  WHERE Channel='google.adwords'
    AND Event_Date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {days} DAY) AND CURRENT_DATE()
  GROUP BY 1,2)"""


if __name__ == "__main__":
    print(build_sql())
