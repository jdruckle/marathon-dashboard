import os
import requests
import pandas as pd
import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# =========================
# HEADER VARIABLES
# =========================
RAW_HEADERS = [
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
        print("STRAVA ERROR RESPONSE:", res.text)

    res.raise_for_status()
    return res.json()["access_token"]

# =========================
# STRAVA ACTIVITIES
# =========================

def fetch_all_activities(token, max_pages=10):
    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {token}"}

    all_activities = []

    for page in range(1, max_pages + 1):
        params = {
            "per_page": 200,
            "page": page
        }

        res = requests.get(url, headers=headers, params=params)
        res.raise_for_status()

        batch = res.json()

        if not batch:
            break

        all_activities.extend(batch)

    return all_activities

def get_existing_activity_ids(sheet):
    raw_ws = sheet.worksheet("Raw_Strava")

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

# =========================
# MAIN
# =========================

def main():
    print("Starting sync...")

    token = get_access_token()
    activities = fetch_all_activities(token)

    sheet = connect_sheet()

    runs_ws = sheet.worksheet("Runs")
    strength_ws = sheet.worksheet("Strength")
    laps_ws = sheet.worksheet("Laps")

    ensure_headers(runs_ws, RUN_HEADERS)
    ensure_headers(strength_ws, STRENGTH_HEADERS)
    ensure_headers(laps_ws, LAP_HEADERS)

    existing_ids = get_existing_activity_ids(sheet)

    run_rows = []
    strength_rows = []
    lap_rows = []
    
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


if __name__ == "__main__":
    main()
