"""Daily weather-check scheduler for EAF-Sky-Alerts.

Reads subscriber configuration from subscribers.json (city → list of
recipients) and triggers the notification layer whenever a warning is
detected.  The actual sending logic lives in a separate module (see the
sms/weather_data_connected branch); this module only handles the
scheduling and the weather checks.

Usage:
  # Run once immediately (used by GitHub Actions or for ad-hoc checks):
  python scheduler.py --run-now

  # Run as a long-running daemon (checks daily at CHECK_TIME, default 06:00):
  python scheduler.py

Environment variables:
  CHECK_TIME  — Time to run the daily check in HH:MM format (default: 06:00)
"""

import json
import os
import sys
import time
from datetime import datetime

import schedule

from api import (
    check_drought_risk,
    check_flood_risk,
    check_severe_weather,
    find_city,
    get_weather_forecast,
)

SUBSCRIBERS_FILE = os.path.join(os.path.dirname(__file__), "subscribers.json")


def load_subscribers() -> dict[str, list[str]]:
    """Load the city→recipients mapping from subscribers.json.

    Returns:
        A dict mapping city names to lists of recipient identifiers.
        Returns an empty dict if the file is missing or malformed.
    """
    try:
        with open(SUBSCRIBERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Subscribers file not found: {SUBSCRIBERS_FILE}")
        return {}
    except json.JSONDecodeError as e:
        print(f"Error reading subscribers file: {e}")
        return {}


def check_and_alert() -> None:
    """Check weather for every subscribed city and dispatch alerts on warnings.

    For each city in subscribers.json the function collects all active
    warnings (severe weather, flood, drought).  When at least one warning
    is found it calls ``send_alert`` — imported at runtime so the
    notification module can be supplied by the SMS branch.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Running daily weather check...")

    subscribers = load_subscribers()
    if not subscribers:
        print("No subscribers found. Skipping weather check.")
        return

    # Import the notification sender at runtime so that the SMS
    # branch can provide it without requiring changes here.
    try:
        from notifier import send_alert  # noqa: PLC0415
    except ImportError:
        print("[WARNING] Notification module (notifier) not available. Warnings will only be logged.")
        send_alert = None

    for city_identifier, recipients in subscribers.items():
        if not recipients:
            continue

        result = find_city(city_identifier)
        if not result:
            print(f"Unknown city '{city_identifier}' in subscribers.json. Skipping.")
            continue

        lat, lon, city_name = result

        warnings: list[str] = []

        forecast = get_weather_forecast(lat, lon, 7)
        severe = check_severe_weather(forecast)
        if severe:
            warnings.append(severe)

        flood = check_flood_risk(lat, lon)
        if flood:
            warnings.append(flood)

        drought = check_drought_risk(lat, lon)
        if drought:
            warnings.append(drought)

        if warnings:
            print(
                f"{city_name}: {len(warnings)} warning(s) detected. "
                f"Notifying {len(recipients)} subscriber(s)."
            )
            if send_alert is not None:
                send_alert(recipients, city_name, warnings)
            else:
                print(
                    f"[INFO] Notification module not available. "
                    f"Warnings for {city_name}: {warnings}"
                )
        else:
            print(f"{city_name}: No warnings.")

    done_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{done_ts}] Daily weather check complete.")


def run_scheduler() -> None:
    """Start the long-running daily scheduler."""
    check_time = os.environ.get("CHECK_TIME", "06:00")
    print(f"Scheduler started. Weather checks will run daily at {check_time} (system timezone — UTC when running in GitHub Actions).")
    schedule.every().day.at(check_time).do(check_and_alert)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--run-now":
        check_and_alert()
    else:
        run_scheduler()
