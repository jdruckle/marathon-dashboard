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

    rows = []

    for a in activities:
        rows.append([
            a.get("id"),
            a.get("name"),
            a.get("type"),
            a.get("distance"),
            a.get("moving_time"),
            a.get("total_elevation_gain"),
            a.get("start_date")
        ])

    # Append rows
    if rows:
        raw_ws.append_rows(rows, value_input_option="RAW")

    print(f"Uploaded {len(rows)} activities")


if __name__ == "__main__":
    main()
