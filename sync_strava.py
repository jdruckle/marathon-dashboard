import os
import requests
import pandas as pd
import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials


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
# MAIN
# =========================

def main():
    print("Starting sync...")

    token = get_access_token()
    activities = fetch_all_activities(token)

    sheet = connect_sheet()

    raw_ws = sheet.worksheet("Raw_Strava")
    laps_ws = sheet.worksheet("Laps")

    existing_ids = get_existing_activity_ids(sheet)

    rows = []
    lap_rows = []
    
    for a in activities:
        activity_id = str(a.get("id"))
    
        if activity_id in existing_ids:
            continue
    
        rows.append([
            activity_id,
            a.get("name"),
            a.get("type"),
            a.get("distance"),
            a.get("moving_time"),
            a.get("total_elevation_gain"),
            a.get("start_date")
        ])

        laps = fetch_laps(token, activity_id)
    
        for lap in laps:
            lap_rows.append([
                activity_id,
                lap.get("lap_index"),
                lap.get("distance"),
                lap.get("moving_time"),
                lap.get("elapsed_time"),
                lap.get("start_date"),
            ])

    # Append rows
    if rows:
        raw_ws.append_rows(rows, value_input_option="RAW")
    if lap_rows:
        laps_ws.append_rows(lap_rows, value_input_option="RAW")

    print(f"Uploaded {len(rows)} activities")


if __name__ == "__main__":
    main()
