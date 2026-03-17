from flask import Flask, request, jsonify, render_template_string
import requests
import time
import uuid
from cities import CITIES

app = Flask(__name__)

# =========================
# CONFIG
# =========================
API_KEY = "uk_YwL2KQh3DCQu3NvEpqzazRHZkcOvTkyEzTEzf--mVAQEvxlAKXGTYJAe_lXibhs3".strip()
SEND_URL = "https://api.httpsms.com/v1/messages/send"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

HEADERS = {
    "x-api-key": API_KEY,
    "Accept": "application/json",
    "Content-Type": "application/json",
}

FROM_NUMBER = "+32470029660"

contacts = [
    {"name": "Jonas", "phone": "+32471608901"},
    {"name": "David", "phone": "+32472822152"},
    {"name": "Kobe", "phone": "+32468467557"},
]

DEFAULT_CITY_KEY = "1"

CONTACT_CITY_KEYS = {
    "+32471608901": "1",
    "+32472822152": "2",
    "+32468467557": "4",
}

WEATHER_CODES = {
    0: "Clear",
    1: "Mostly clear",
    2: "Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Heavy freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Strong rain showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm",
    99: "Strong thunderstorm",
}

EXTREME_WEATHER_CODES = {65, 67, 75, 82, 86, 95, 96, 99}

PHONE_TO_NAME = {contact["phone"]: contact["name"] for contact in contacts}


# =========================
# HELPERS
# =========================
def get_contact_name(phone_number: str) -> str:
    return PHONE_TO_NAME.get(phone_number, "friend")


def get_city_info(city_key: str):
    info = CITIES.get(city_key)
    if not info:
        info = CITIES[DEFAULT_CITY_KEY]
    return info["lat"], info["lon"], info["name"]


def get_city_for_phone(phone_number: str):
    city_key = CONTACT_CITY_KEYS.get(phone_number, DEFAULT_CITY_KEY)
    return get_city_info(city_key)


def get_forecast(lat: float, lon: float, days: int = 4):
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": [
            "weathercode",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
        ],
        "timezone": "auto",
        "forecast_days": days,
    }

    response = requests.get(WEATHER_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("daily", {})


def detect_extreme_days(daily: dict):
    results = []
    dates = daily.get("time", [])
    weathercodes = daily.get("weathercode", [])
    precip = daily.get("precipitation_sum", [])

    for i in range(len(dates)):
        code = weathercodes[i] if i < len(weathercodes) else None
        rain = precip[i] if i < len(precip) else 0

        is_extreme = (code in EXTREME_WEATHER_CODES) or (rain is not None and rain >= 30)

        if is_extreme:
            results.append({
                "index": i,
                "condition": WEATHER_CODES.get(code, "Bad weather"),
            })

    return results


def build_extreme_alert(city_name: str, extreme_days: list[dict], person_name: str | None = None) -> str:
    greeting = f"{person_name}, " if person_name else ""

    if not extreme_days:
        return f"{greeting}No extreme weather expected in {city_name}. Reply 1 2 3 or 4 for forecast."

    first = extreme_days[0]
    labels = ["today", "tomorrow", "in 3 days", "in 4 days"]
    when = labels[first["index"]] if first["index"] < len(labels) else "soon"

    return (
        f"{greeting}Alert for {city_name}. {first['condition']} {when}. "
        f"Reply 1 2 3 or 4 for forecast."
    )


def build_forecast_message(daily: dict, days_requested: int) -> str:
    weathercodes = daily.get("weathercode", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])

    labels = ["Today", "Tomorrow", "In 3 days", "In 4 days"]
    lines = []

    max_days = min(days_requested, 4, len(weathercodes), len(max_temps), len(min_temps))

    for i in range(max_days):
        code = weathercodes[i] if i < len(weathercodes) else None
        condition = WEATHER_CODES.get(code, "Unknown")

        max_temp = int(round(max_temps[i])) if i < len(max_temps) else "N/A"
        min_temp = int(round(min_temps[i])) if i < len(min_temps) else "N/A"

        lines.append(f"{labels[i]} - {condition}. High {max_temp}C Low {min_temp}C")

    if not lines:
        return "Forecast not available."

    return "\n".join(lines)


def weather_reply_for_phone(phone_number: str, days_requested: int) -> str:
    lat, lon, _city_name = get_city_for_phone(phone_number)
    daily = get_forecast(lat, lon, days=4)
    return build_forecast_message(daily, days_requested)


def send_sms(to_number: str, message: str, from_number: str = FROM_NUMBER):
    payload = {
        "content": message,
        "from": from_number,
        "to": to_number,
        "request_id": str(uuid.uuid4()),
    }

    response = requests.post(SEND_URL, headers=HEADERS, json=payload, timeout=30)
    print(f"[SEND] to={to_number} status={response.status_code} body={response.text}")
    return response


def send_bulk_alert(city_key: str = DEFAULT_CITY_KEY):
    lat, lon, city_name = get_city_info(city_key)
    daily = get_forecast(lat, lon, days=4)
    extreme_days = detect_extreme_days(daily)

    summary = build_extreme_alert(city_name, extreme_days)

    for contact in contacts:
        person_message = build_extreme_alert(city_name, extreme_days, contact["name"])
        send_sms(contact["phone"], person_message)
        time.sleep(1)

    return summary


def send_custom_alert(message: str):
    for contact in contacts:
        send_sms(contact["phone"], f"{contact['name']}, {message}")
        time.sleep(1)


# =========================
# WEB UI
# =========================
HOME_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Farmer Weather SMS</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; }
    h1 { margin-bottom: 10px; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin-bottom: 18px; }
    button { padding: 10px 16px; margin-right: 10px; cursor: pointer; }
    input, textarea, select { width: 100%; padding: 10px; margin-top: 8px; margin-bottom: 12px; }
    .small { color: #666; font-size: 14px; }
  </style>
</head>
<body>
  <h1>Farmer Weather SMS</h1>
  <p class="small">Send weather alerts and simple forecast messages.</p>

  <div class="card">
    <h2>Send extreme weather alert</h2>
    <form action="/trigger-extreme-alert" method="post">
      <label for="city_key">City</label>
      <select name="city_key" id="city_key">
        {% for key, city in cities.items() %}
          <option value="{{ key }}" {% if key == default_city_key %}selected{% endif %}>
            {{ key }} - {{ city["name"] }}
          </option>
        {% endfor %}
      </select>
      <button type="submit">Send alert</button>
    </form>
  </div>

  <div class="card">
    <h2>Send custom SMS</h2>
    <form action="/send-custom-alert" method="post">
      <label for="message">Message</label>
      <textarea name="message" id="message" rows="5" placeholder="Type a simple weather message"></textarea>
      <button type="submit">Send custom SMS</button>
    </form>
  </div>

  <div class="card">
    <h2>SMS menu</h2>
    <p>Users reply with:</p>
    <ul>
      <li><strong>1</strong> = today</li>
      <li><strong>2</strong> = today + tomorrow</li>
      <li><strong>3</strong> = today + tomorrow + in 3 days</li>
      <li><strong>4</strong> = 4-day simple forecast</li>
      <li><strong>MENU</strong> = show menu again</li>
    </ul>
  </div>
</body>
</html>
"""


# =========================
# ROUTES
# =========================
@app.route("/", methods=["GET"])
def home():
    return render_template_string(
        HOME_HTML,
        cities=CITIES,
        default_city_key=DEFAULT_CITY_KEY,
    )


@app.route("/trigger-extreme-alert", methods=["POST"])
def trigger_extreme_alert():
    city_key = request.form.get("city_key", DEFAULT_CITY_KEY).strip()
    summary = send_bulk_alert(city_key)
    return jsonify({"ok": True, "message": summary})


@app.route("/send-custom-alert", methods=["POST"])
def send_custom_alert_route():
    message = (request.form.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "Message is required"}), 400

    send_custom_alert(message)
    return jsonify({"ok": True, "message": "Custom SMS queued"})


@app.route("/webhook", methods=["POST"])
def webhook():
    event = request.get_json(silent=True) or {}
    print("[WEBHOOK]", event)

    event_type = event.get("type")
    if event_type != "message.phone.received":
        return jsonify({"ok": True, "ignored": True})

    data = event.get("data", {})
    incoming_text = (data.get("content") or "").strip().lower()
    sender_number = data.get("contact")

    if not sender_number:
        return jsonify({"ok": False, "error": "Missing sender number"}), 400

    sender_name = get_contact_name(sender_number)
    _lat, _lon, city_name = get_city_for_phone(sender_number)

    if incoming_text == "1":
        reply = f"{sender_name}, {city_name}\n{weather_reply_for_phone(sender_number, 1)}"
    elif incoming_text == "2":
        reply = f"{sender_name}, {city_name}\n{weather_reply_for_phone(sender_number, 2)}"
    elif incoming_text == "3":
        reply = f"{sender_name}, {city_name}\n{weather_reply_for_phone(sender_number, 3)}"
    elif incoming_text == "4":
        reply = f"{sender_name}, {city_name}\n{weather_reply_for_phone(sender_number, 4)}"
    elif incoming_text == "menu":
        reply = (
            f"{sender_name}, reply:\n"
            f"1 for today\n"
            f"2 for 2 days\n"
            f"3 for 3 days\n"
            f"4 for 4 days"
        )
    else:
        reply = (
            f"{sender_name}, {city_name} weather menu:\n"
            f"1 today\n"
            f"2 two days\n"
            f"3 three days\n"
            f"4 four days"
        )

    send_sms(
        to_number=sender_number,
        message=reply,
        from_number=FROM_NUMBER,
    )

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=False)