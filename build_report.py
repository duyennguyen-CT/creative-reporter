"""
Meta Ads — Creative Performance Reporter.

Pulls ad/creative-level performance for a list of ad accounts and writes
docs/data/latest.json, which docs/index.html renders as a dashboard.

Run modes:
  - With env var META_ACCESS_TOKEN set -> pulls live data from the Graph API.
  - Without it                        -> keeps the existing seed JSON untouched,
                                         so the dashboard still works offline.

Local:   python build_report.py
CI:       runs inside .github/workflows/weekly.yml on a schedule.
"""

import os
import json
import time
import datetime
import pathlib

# --- CONFIG ---------------------------------------------------------------

META_GRAPH_URL = "https://graph.facebook.com/v21.0"

# label -> ad account id (numeric, without the "act_" prefix)
# Classification follows the team's master account list (Channel=FB, Team=Growth/Brand).
ACCOUNTS = {
    # --- GROWTH (FB) ---
    "Chotot_growth_sgd":       "217167486615130",
    "Chotot_gds_elt_sgd":      "655717678725444",
    "Chotot_job_sgd":          "1009648153146994",
    "Chotot_pty_sgd":          "189567943020118",
    "Chotot_veh_sgd":          "211751247179666",
    "Chotot_pty_app":          "1924712638163043",
    "Chotot_job_app":          "1021879607190581",
    "Chotot_veh_app":          "27596542009984281",
    # --- BRAND (FB) ---
    "Chotot_pty_branding_sgd": "697690298835029",
    "Chotot_job_branding_sgd": "339646702235039",
    "Chotot_gds_brand":        "253279950744492",
    "Chotot_veh_brand":        "937141594251635",
}

# Which group each account belongs to (used only for labelling in the UI).
GROUPS = {
    "Chotot_growth_sgd":       "GROWTH",
    "Chotot_gds_elt_sgd":      "GROWTH",
    "Chotot_job_sgd":          "GROWTH",
    "Chotot_pty_sgd":          "GROWTH",
    "Chotot_veh_sgd":          "GROWTH",
    "Chotot_pty_app":          "GROWTH",
    "Chotot_job_app":          "GROWTH",
    "Chotot_veh_app":          "GROWTH",
    "Chotot_pty_branding_sgd": "BRAND",
    "Chotot_job_branding_sgd": "BRAND",
    "Chotot_gds_brand":        "BRAND",
    "Chotot_veh_brand":        "BRAND",
}

# Time ranges pre-built into the dashboard (dropdown). key = Meta date_preset.
PRESETS = [
    ("yesterday",  "Yesterday"),
    ("last_7d",    "Last 7 days"),
    ("last_14d",   "Last 2 weeks"),
    ("last_30d",   "Last 30 days"),
    ("this_month", "This month"),
    ("last_month", "Last month"),
]
DEFAULT_RANGE = "last_30d"

FIELDS = ("ad_id,ad_name,campaign_name,spend,impressions,reach,clicks,ctr,cpc,cpm,frequency,"
          "actions,video_thruplay_watched_actions")
TOP_N = 25  # creatives per account, sorted by spend

# action_type values counted as an app install (varies by campaign setup)
INSTALL_ACTIONS = ("mobile_app_install", "omni_app_install", "app_install")

TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
OUT_PATH = pathlib.Path(__file__).parent / "docs" / "data" / "latest.json"

# --- META API -------------------------------------------------------------


def _get(url, params, retries=3):
    import requests  # imported lazily so offline/seed mode needs no install
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            data = r.json()
            if "error" in data:
                # 4/17/32/613 = rate limits -> back off and retry
                if data["error"].get("code") in (4, 17, 32, 613) and attempt < retries - 1:
                    time.sleep(2 ** attempt * 5)
                    continue
                print(f"Meta API error: {data['error']}")
                return {}
            return data
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5)
            else:
                print(f"Request failed: {e}")
    return {}


def fetch_creatives(account_id, date_preset):
    """Return a list of ad/creative rows for one account + time range, by spend."""
    url = f"{META_GRAPH_URL}/act_{account_id}/insights"
    params = {
        "level": "ad",
        "fields": FIELDS,
        "date_preset": date_preset,
        "filtering": json.dumps([{"field": "spend", "operator": "GREATER_THAN", "value": "0"}]),
        "sort": "spend_descending",
        "limit": TOP_N,
        "access_token": TOKEN,
    }
    data = _get(url, params)
    rows = []
    for row in data.get("data", []):
        acts = row.get("actions") or []

        def _sum(types, _acts=acts):
            return sum(int(float(a.get("value", 0) or 0))
                       for a in _acts if a.get("action_type") in types)

        reach = int(float(row.get("reach", 0) or 0))
        impr = int(float(row.get("impressions", 0) or 0))
        spend = round(float(row.get("spend", 0) or 0), 2)
        freq = float(row.get("frequency") or (impr / reach if reach else 0))
        installs = _sum(INSTALL_ACTIONS)
        # 3-second video plays (Meta calls action_type "video_view" the 3s metric)
        video_3s = _sum(("video_view",))
        # ThruPlay = watched to completion or >=15s; separate insights field (a list)
        thruplay = sum(int(float(a.get("value", 0) or 0))
                       for a in (row.get("video_thruplay_watched_actions") or []))
        # post engagement excluding 3-second video views
        engagement = max(_sum(("post_engagement",)) - video_3s, 0)
        rows.append({
            "id":          row.get("ad_id", ""),
            "name":        row.get("ad_name", ""),
            "campaign":    row.get("campaign_name", ""),
            "spend":       spend,
            "impressions": impr,
            "reach":       reach,
            "clicks":      int(float(row.get("clicks", 0) or 0)),
            "ctr":         round(float(row.get("ctr", 0) or 0), 2),
            "cpc":         round(float(row.get("cpc", 0) or 0), 2),
            "cpm":         round(float(row.get("cpm", 0) or 0), 2),
            "frequency":   round(freq, 2),
            "installs":    installs,
            "cpi":         round(spend / installs, 2) if installs else 0,
            "engagement":  engagement,
            "eng_rate":    round(engagement / impr * 100, 2) if impr else 0,
            "video_3s":    video_3s,
            "thruplay":    thruplay,
            "hook_rate":   round(video_3s / impr * 100, 2) if impr else 0,
            "hold_rate":   round(thruplay / impr * 100, 2) if impr else 0,
        })
    return rows


def classify_format(creative):
    """Map a creative object to a coarse format bucket used by the dashboard."""
    if not creative:
        return "Other"
    oss = creative.get("object_story_spec") or {}
    link = oss.get("link_data") or {}
    if creative.get("video_id") or creative.get("object_type") == "VIDEO" or oss.get("video_data"):
        return "Video"
    # carousel cards live under object_story_spec.link_data.child_attachments
    if link.get("child_attachments"):
        return "Carousel"
    if creative.get("image_hash") or creative.get("object_type") == "PHOTO" or link.get("image_hash"):
        return "Image"
    # object_story_spec/asset_feed-based dynamic/catalog ads report as SHARE, no asset
    if creative.get("object_type") == "SHARE":
        return "Dynamic"
    # STATUS and other rare object types → keep the buckets clean
    return "Other"


def post_url_from(creative):
    """Build a clickable Facebook (or Instagram) permalink for the ad's post.

    `effective_object_story_id` is "<page_id>_<post_id>" -> the canonical FB
    permalink is facebook.com/<page_id>/posts/<post_id> (same post the Ads
    Manager "Facebook Post with Comments" link opens). Falls back to the
    Instagram permalink when there's no FB page post.
    """
    osid = creative.get("effective_object_story_id")
    if osid and "_" in osid:
        pid, post = osid.split("_", 1)
        return f"https://www.facebook.com/{pid}/posts/{post}"
    return creative.get("instagram_permalink_url") or ""


def fetch_creative_meta(ad_ids):
    """Return {ad_id: {format, post_url}} by batch-reading each ad's creative."""
    out = {}
    ids = list(ad_ids)
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        params = {
            "ids": ",".join(batch),
            "fields": "creative{object_type,video_id,image_hash,object_story_spec,"
                      "effective_object_story_id,instagram_permalink_url}",
            "access_token": TOKEN,
        }
        data = _get(META_GRAPH_URL, params)
        for ad_id, node in (data or {}).items():
            if isinstance(node, dict):
                cr = node.get("creative") or {}
                out[ad_id] = {"format": classify_format(cr), "post_url": post_url_from(cr)}
    return out


def preset_window(preset):
    """Return (since, until) dates that approximate Meta's definition of the preset."""
    t = datetime.date.today()
    y = t - datetime.timedelta(days=1)
    if preset == "yesterday":
        s = e = y
    elif preset == "last_7d":
        e, s = y, t - datetime.timedelta(days=7)
    elif preset == "last_14d":
        e, s = y, t - datetime.timedelta(days=14)
    elif preset == "last_30d":
        e, s = y, t - datetime.timedelta(days=30)
    elif preset == "this_month":
        s, e = t.replace(day=1), t
    elif preset == "last_month":
        e = t.replace(day=1) - datetime.timedelta(days=1)
        s = e.replace(day=1)
    else:
        s = e = t
    return s, e


def period_label(preset):
    """Human date range that approximates Meta's definition of the preset."""
    s, e = preset_window(preset)
    return f"{s:%-d %b} – {e:%-d %b %Y}"


def previous_range(preset):
    """The immediately-preceding, equal-length window for a preset (for WoW-style deltas)."""
    t = datetime.date.today()
    y = t - datetime.timedelta(days=1)
    if preset == "yesterday":
        d = y - datetime.timedelta(days=1)
        return d, d
    if preset == "last_7d":
        return t - datetime.timedelta(days=14), t - datetime.timedelta(days=8)
    if preset == "last_14d":
        return t - datetime.timedelta(days=28), t - datetime.timedelta(days=15)
    if preset == "last_30d":
        return t - datetime.timedelta(days=60), t - datetime.timedelta(days=31)
    if preset == "this_month":
        start = t.replace(day=1)
        elapsed = (t - start).days
        prev_end = start - datetime.timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        return prev_start, prev_start + datetime.timedelta(days=elapsed)
    if preset == "last_month":
        end = t.replace(day=1) - datetime.timedelta(days=1)
        start = end.replace(day=1)
        prev_end = start - datetime.timedelta(days=1)
        return prev_end.replace(day=1), prev_end
    return None


def fetch_prev(account_id, since, until):
    """Return {ad_id: {spend,impressions,clicks,installs}} for a prior window."""
    url = f"{META_GRAPH_URL}/act_{account_id}/insights"
    params = {
        "level": "ad",
        "fields": "ad_id,spend,impressions,clicks,actions",
        "time_range": json.dumps({"since": str(since), "until": str(until)}),
        "filtering": json.dumps([{"field": "spend", "operator": "GREATER_THAN", "value": "0"}]),
        "sort": "spend_descending",
        "limit": 150,
        "access_token": TOKEN,
    }
    data = _get(url, params)
    out = {}
    for row in data.get("data", []):
        installs = sum(int(float(a.get("value", 0) or 0))
                       for a in (row.get("actions") or [])
                       if a.get("action_type") in INSTALL_ACTIONS)
        out[row.get("ad_id", "")] = {
            "spend": round(float(row.get("spend", 0) or 0), 2),
            "impressions": int(float(row.get("impressions", 0) or 0)),
            "clicks": int(float(row.get("clicks", 0) or 0)),
            "installs": installs,
        }
    return out


def build_from_api():
    ranges = {}
    ad_ids = set()
    for preset, label in PRESETS:
        prev = previous_range(preset)
        cur_since, cur_until = preset_window(preset)
        accounts = []
        for acc_label, acc_id in ACCOUNTS.items():
            print(f"Pulling [{preset}] {acc_label} ({acc_id}) ...")
            creatives = fetch_creatives(acc_id, preset)
            print(f"  -> {len(creatives)} creatives")
            # attach prior-period raw metrics (for WoW deltas on CTR/CPI)
            prev_map = fetch_prev(acc_id, prev[0], prev[1]) if prev else {}
            for c in creatives:
                p = prev_map.get(c["id"]) or {}
                c["prev_spend"] = p.get("spend", 0)
                c["prev_impressions"] = p.get("impressions", 0)
                c["prev_clicks"] = p.get("clicks", 0)
                c["prev_installs"] = p.get("installs", 0)
            for c in creatives:
                if c["id"]:
                    ad_ids.add(c["id"])
            accounts.append({
                "id": acc_id,
                "label": acc_label,
                "group": GROUPS.get(acc_label, ""),
                "creatives": creatives,
            })
        ranges[preset] = {
            "label": label,
            "period_label": period_label(preset),
            "since": str(cur_since),
            "until": str(cur_until),
            "prev_since": str(prev[0]) if prev else None,
            "prev_until": str(prev[1]) if prev else None,
            "accounts": accounts,
        }

    # Creative format + post permalink are attributes of the ad, not a time
    # range — fetch once and attach to every occurrence of the ad.
    print(f"Resolving creative meta (format + post url) for {len(ad_ids)} ads ...")
    meta_map = fetch_creative_meta(ad_ids)
    for rng in ranges.values():
        for acc in rng["accounts"]:
            for c in acc["creatives"]:
                m = meta_map.get(c["id"]) or {}
                c["format"] = m.get("format", "Other")
                c["post_url"] = m.get("post_url", "")

    return {
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "currency": "SGD",
        "default_range": DEFAULT_RANGE,
        "ranges": ranges,
    }


def main():
    if not TOKEN:
        print("META_ACCESS_TOKEN not set — keeping existing seed data at")
        print(f"  {OUT_PATH}")
        print("Set the token to pull live data:  export META_ACCESS_TOKEN=...")
        return

    report = build_from_api()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    dflt = report["ranges"].get(DEFAULT_RANGE) or next(iter(report["ranges"].values()))
    total = sum(c["spend"] for a in dflt["accounts"] for c in a["creatives"])
    print(f"Wrote {OUT_PATH} — {len(report['ranges'])} ranges, "
          f"{DEFAULT_RANGE} total spend SGD {total:,.2f}")


if __name__ == "__main__":
    main()
