import os
import requests
import pandas as pd
import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
import os
from datetime import datetime, timezone
import time

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# =========================
# HEADER VARIABLES
# =========================
RUN_HEADERS = [
    "id",
    "name",
    "type",
    "distance",
    "moving_time",
    "total_elevation_gain",
    "start_date",
    "avg_hr",
    "max_hr",
    "workout_type"
]

STRENGTH_HEADERS = [
    "id",
    "name",
    "moving_time",
    "start_date"
]

LAP_HEADERS = [
    "activity_id",
    "lap_index",
    "distance",
    "moving_time",
    "elapsed_time",
    "start_date",
    "avg_hr",
    "max_hr"
]

# =========================
# STRAVA AUTH
# =========================

def get_access_token():
    url = "https://www.strava.com/oauth/token"

    payload = {
        "client_id": os.environ["STRAVA_CLIENT_ID"],
        "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }

    res = requests.post(url, data=payload)

    if res.status_code != 200:
        print("ERROR RESPONSE")
        print("STATUS:", res.status_code)
        print("HEADERS:", res.headers)
        print("BODY:", res.text[:500])

    res.raise_for_status()
    return res.json()["access_token"]

# =========================
# STRAVA ACTIVITIES
# =========================

def fetch_all_activities(token, after_timestamp=None):
    headers = {"Authorization": f"Bearer {token}"}

    page = 1
    all_activities = []

    base_url = "https://www.strava.com/api/v3/athlete/activities"

    params_base = {"per_page": 200}
    if after_timestamp:
        params_base["after"] = after_timestamp

    while True:
        params = dict(params_base)
        params["page"] = page

        retry_count = 0

        while True:
            res = requests.get(base_url, headers=headers, params=params)

            if res.status_code == 429:
                retry_count += 1

                wait_time = min(60 * retry_count, 300)  # cap at 5 min
                print(f"Rate limited. Sleeping {wait_time}s (retry {retry_count})...")
                time.sleep(wait_time)
                continue

            break

        print("Status:", res.status_code)
        print("Headers:")
        for k, v in res.headers.items():
            if "rate" in k.lower():
                print(f"  {k}: {v}")
        
        print("Response body:")
        print(res.text[:1000])

        res.raise_for_status()

        data = res.json()

        if not data:
            break

        all_activities.extend(data)

        print(f"Fetched page {page}, got {len(data)} activities")

        page += 1

        # throttle between successful calls
        time.sleep(2.0)

        # prevents runaway loops
        if page > 20:
            print("Safety cap reached (20 pages). Stopping early.")
            break

    return all_activities

def get_existing_activity_ids(sheet):
    raw_ws = sheet.worksheet("Runs")

    # Get all values in first column (activity IDs)
    records = raw_ws.col_values(1)

    # Skip header if you ever add one
    return set(records)

def fetch_laps(token, activity_id):
    url = f"https://www.strava.com/api/v3/activities/{activity_id}/laps"

    headers = {"Authorization": f"Bearer {token}"}

    res = requests.get(url, headers=headers)

    # Some activities (especially treadmill/manual) may not have laps
    if res.status_code != 200:
        return []

    return res.json()
    
# =========================
# GOOGLE SHEETS AUTH
# =========================

def connect_sheet():
    creds_dict = os.environ["GOOGLE_CREDS"]

    creds = Credentials.from_service_account_info(
        json.loads(creds_dict),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )

    client = gspread.authorize(creds)

    sheet = client.open("Marathon Dashboard")
    return sheet


# =========================
# DATA ANALYSIS
# =========================
def classify_workout(laps):
    if not laps:
        return "Unknown"

    distances = [float(l.get("distance", 0)) for l in laps]
    times = [float(l.get("moving_time", 0)) for l in laps]

    total_laps = len(laps)

    # Heuristic 1: long run
    if total_laps <= 2 and sum(distances) > 15000:
        return "Long Run"

    # Heuristic 2: interval workout (many short structured laps)
    if total_laps >= 4:
        avg_lap_dist = sum(distances) / total_laps

        if avg_lap_dist < 2000:
            return "Interval Workout"

    # Heuristic 3: threshold / tempo (medium structured laps)
    if 2 <= total_laps <= 5:
        return "Tempo / Threshold"

    # Heuristic 4: single continuous effort
    if total_laps <= 2:
        return "Easy / Steady"

    return "Mixed / Fartlek"

# =========================
# AI FUNCTIONS
# =========================

def build_ai_context(runs, laps, pace_zones):
    return {
        "recent_runs": runs[-20:],
        "recent_laps": laps[-60:],
        "pace_zones": pace_zones
    }

def build_prompt(context):
    return f"""
You are an elite marathon coach.

You are given:
- Recent Strava run data
- Lap data (including splits and heart rate trends)
- Current pace zones

Your job:
1. Evaluate training status
2. Recommend ONE week of training:
   - Monday: high intensity (tempo or intervals)
   - Wednesday: easy run
   - Saturday: long run
3. Suggest adjustments ONLY to pace zones if justified by data

Rules:
- Do NOT overreact to single workouts
- Make small incremental adjustments only
- Prioritize aerobic development for sub-4 marathon
- Keep structure stable

OUTPUT FORMAT (strict):

WEEKLY RECOMMENDATION:
...

RECOMMENDED MONDAY WORKOUT:
...

RECOMMENDED WEDNESDAY RUN:
...

RECOMMENDED SATURDAY RUN:
...

PACE ZONE UPDATES:
...

SUMMARY:
...

DATA:
{context}
"""

def get_ai_recommendation(context):
    response = client.chat.completions.create(
        model="gpt-5.3-mini",
        messages=[
            {"role": "system", "content": "You are a world-class endurance running coach."},
            {"role": "user", "content": build_prompt(context)}
        ],
        temperature=0.4
    )

    return response.choices[0].message.content
# =========================
# HELPER FUNCTIONS
# =========================
def ensure_headers(ws, headers):
    existing = ws.row_values(1)

    if existing != headers:
        ws.insert_row(headers, 1)

def should_keep_activity(activity):
    activity_type = activity.get("type")

    if activity_type == "Walk":
        return False

    return activity_type in ["Run", "WeightTraining", "Workout"]
    
def get_pace_zones(sheet):
    ws = sheet.worksheet("Pace_Zones")
    return ws.get_all_records()

def write_ai_log(sheet, ai_output):
    ws = sheet.worksheet("AI_Logs")

    from datetime import datetime

    ws.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        ai_output,
        "", "", "", "", ""
    ])

def get_last_sync_time(sheet):
    ws = sheet.worksheet("Sync_State")
    records = ws.get_all_records()

    for row in records:
        if row["key"] == "last_sync":
            return row["value"]

    # fallback if missing
    return "1970-01-01T00:00:00"

def to_unix(timestamp_str):
    dt = datetime.fromisoformat(timestamp_str)
    return int(dt.timestamp())
    
def update_sync_time(sheet):
    ws = sheet.worksheet("Sync_State")

    now = datetime.now(timezone.utc).isoformat()

    records = ws.get_all_records()

    for i, row in enumerate(records, start=2):  # row index starts at 2
        if row["key"] == "last_sync":
            ws.update_cell(i, 2, now)
            print(f"Updated last_sync → {now}")
            return

# =========================
# MAIN
# =========================

def main():
    print("Starting sync...")
    
    sheet = connect_sheet()

    runs_ws = sheet.worksheet("Runs")
    strength_ws = sheet.worksheet("Strength")
    laps_ws = sheet.worksheet("Laps")
    zones_ws = sheet.worksheet("Pace_Zones")

    ensure_headers(runs_ws, RUN_HEADERS)
    ensure_headers(strength_ws, STRENGTH_HEADERS)
    ensure_headers(laps_ws, LAP_HEADERS)

    existing_ids = get_existing_activity_ids(sheet)
    last_sync_str = get_last_sync_time(sheet)
    pace_zones = get_pace_zones(sheet)

    run_rows = []
    strength_rows = []
    lap_rows = []
    last_sync_unix = to_unix(last_sync_str)
    token = get_access_token()
    activities = fetch_all_activities(token, after_timestamp=last_sync_unix)
    
    for a in activities:
        activity_id = str(a.get("id"))
        activity_type = a.get("type")
    
        if activity_id in existing_ids:
            continue
        if activity_type == "Walk":
            continue

        laps = fetch_laps(token, activity_id)
        workout_type = classify_workout(laps)

        if activity_type == "Run":
            run_rows.append([
                activity_id,
                a.get("name"),
                a.get("type"),
                a.get("distance"),
                a.get("moving_time"),
                a.get("total_elevation_gain"),
                a.get("start_date"),
                a.get("average_heartrate"),
                a.get("max_heartrate"),
                workout_type
            ])
        elif activity_type in ["WeightTraining", "Workout"]:
            strength_rows.append([
                activity_id,
                a.get("name"),
                a.get("moving_time"),
                a.get("start_date")
            ])
          
        for lap in laps:
            lap_rows.append([
                activity_id,
                lap.get("lap_index"),
                lap.get("distance"),
                lap.get("moving_time"),
                lap.get("elapsed_time"),
                lap.get("start_date"),
                lap.get("average_heartrate"),
                lap.get("max_heartrate"),
            ])

    # Append rows
    if run_rows:
        runs_ws.append_rows(run_rows, value_input_option="RAW")
    if strength_rows:
        strength_ws.append_rows(strength_rows, value_input_option="RAW")
    if lap_rows:
        laps_ws.append_rows(lap_rows, value_input_option="RAW")

    print(f"Uploaded {len(rows)} activities")

    context = build_ai_context(
        runs=run_rows,
        laps=lap_rows,
        pace_zones=pace_zones
    )

    ai_output = get_ai_recommendation(context)

    write_ai_log(sheet, ai_output)


if __name__ == "__main__":
    main()
