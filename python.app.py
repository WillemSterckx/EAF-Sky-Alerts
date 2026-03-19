from flask import Flask, request, redirect, flash, get_flashed_messages, render_template_string, jsonify
import os
import time
import uuid
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = "dev-secret-change-this"


# =========================
# CONFIG
# =========================

API_KEY = "uk_P8gCZcTVk_QUfF0TQ7EPgK6OUpQIhhBxO5WcMqlAJmCl-3Rrbj97X38G-Q7PdnpK"

SEND_URL = "https://api.httpsms.com/v1/messages/send"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

DB_NAME = os.getenv("DB_NAME", "weather_sms")
DB_USER = os.getenv("DB_USER", "davidgoodman")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))

FROM_NUMBER = "+32470029660"


def get_sms_headers():
    return {
        "x-api-key": API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# =========================
# WEATHER CODES
# =========================

WEATHER_CODES = {
    0: "Clear",
    1: "Mostly clear",
    2: "Cloudy",
    3: "Overcast",
    61: "Rain",
    63: "Rain",
    65: "Heavy rain",
    80: "Rain showers",
    82: "Strong rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}

EXTREME_EVENT_RULES = {
    "thunderstorm": {"label": "Thunderstorm alert"},
    "heavy_rain": {"label": "Heavy rain alert"},
    "hail": {"label": "Hail alert"},
}


# =========================
# DATABASE
# =========================

def get_db_connection():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        cursor_factory=RealDictCursor,
    )


def create_support_tables():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS registration_sessions (
                    phone_number TEXT PRIMARY KEY,
                    step TEXT NOT NULL,
                    wants_weather BOOLEAN,
                    frequency TEXT,
                    days_ahead INTEGER
                )
            """)
        conn.commit()


def get_all_cities():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, lat, lon
                FROM cities
                ORDER BY name
            """)
            return cur.fetchall()


def get_city_row_by_name(city_name):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM cities
                WHERE LOWER(name) = LOWER(%s)
                LIMIT 1
            """, (city_name,))
            return cur.fetchone()


def get_first_city():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM cities
                ORDER BY name
                LIMIT 1
            """)
            return cur.fetchone()


def get_all_subscribers():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM weather_subscribers
                ORDER BY name, city
            """)
            return cur.fetchall()


def get_subscribers_for_area(city):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM weather_subscribers
                WHERE LOWER(city) = LOWER(%s)
                ORDER BY name
            """, (city,))
            return cur.fetchall()


def get_subscriber_by_phone(phone):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM weather_subscribers
                WHERE phone_number = %s
                LIMIT 1
            """, (phone,))
            return cur.fetchone()


def add_subscriber(name, phone, city, frequency, days):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_subscribers
                (name, phone_number, city, frequency, days_ahead)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (phone_number)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    city = EXCLUDED.city,
                    frequency = EXCLUDED.frequency,
                    days_ahead = EXCLUDED.days_ahead
                RETURNING *
            """, (name, phone, city, frequency, days))
            row = cur.fetchone()
        conn.commit()
        return row


def remove_subscriber(subscriber_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM weather_subscribers
                WHERE id = %s
                RETURNING *
            """, (subscriber_id,))
            row = cur.fetchone()
        conn.commit()
        return row


# =========================
# REGISTRATION SESSIONS
# =========================

def get_registration_session(phone_number):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM registration_sessions
                WHERE phone_number = %s
                LIMIT 1
            """, (phone_number,))
            return cur.fetchone()


def upsert_registration_session(phone_number, step, wants_weather=None, frequency=None, days_ahead=None):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO registration_sessions (phone_number, step, wants_weather, frequency, days_ahead)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (phone_number)
                DO UPDATE SET
                    step = EXCLUDED.step,
                    wants_weather = COALESCE(EXCLUDED.wants_weather, registration_sessions.wants_weather),
                    frequency = COALESCE(EXCLUDED.frequency, registration_sessions.frequency),
                    days_ahead = COALESCE(EXCLUDED.days_ahead, registration_sessions.days_ahead)
            """, (phone_number, step, wants_weather, frequency, days_ahead))
        conn.commit()


def delete_registration_session(phone_number):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM registration_sessions
                WHERE phone_number = %s
            """, (phone_number,))
        conn.commit()


# =========================
# WEATHER
# =========================

def get_forecast(lat, lon, days=7):
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ["weathercode", "temperature_2m_max", "temperature_2m_min"],
        "forecast_days": days,
        "timezone": "auto",
    }

    r = requests.get(WEATHER_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()["daily"]


def build_forecast_message(city_name, forecast, days_requested):
    weather_codes = forecast.get("weathercode", [])
    max_temps = forecast.get("temperature_2m_max", [])
    min_temps = forecast.get("temperature_2m_min", [])

    labels = ["Today", "Tomorrow", "In 3 days", "In 4 days", "In 5 days", "In 6 days", "In 7 days"]
    lines = [f"{city_name} forecast:"]

    max_days = min(days_requested, len(weather_codes), len(max_temps), len(min_temps), len(labels))

    for i in range(max_days):
        condition = WEATHER_CODES.get(weather_codes[i], "Unknown")
        high = int(round(max_temps[i])) if max_temps[i] is not None else "N/A"
        low = int(round(min_temps[i])) if min_temps[i] is not None else "N/A"
        lines.append(f"{labels[i]} - {condition}. High {high}C Low {low}C")

    if len(lines) == 1:
        lines.append("Forecast not available.")

    return "\n".join(lines)


# =========================
# SMS
# =========================

def send_sms(number, message):
    payload = {
        "content": message,
        "from": FROM_NUMBER,
        "to": number,
        "request_id": str(uuid.uuid4())
    }

    r = requests.post(
        SEND_URL,
        headers=get_sms_headers(),
        json=payload,
        timeout=30
    )

    print("SMS RESPONSE", r.status_code, r.text)

    if r.status_code >= 400:
        raise RuntimeError(r.text)

    return r


def send_forecast_prompt(number):
    send_sms(
        number,
        "Do you want to see the forecast for the upcoming days?\nReply with a number from 1 to 7."
    )


# =========================
# ALERT SYSTEM
# =========================

def build_alert_message(city, event_key="", custom_message=""):
    parts = []

    if event_key:
        event_label = EXTREME_EVENT_RULES[event_key]["label"]
        parts.append(f"{event_label} for {city}.")

    if custom_message.strip():
        parts.append(custom_message.strip())

    return " ".join(parts).strip()


# =========================
# REGISTRATION FLOW HELPERS
# =========================

def get_registration_profile(phone_number):
    existing = get_subscriber_by_phone(phone_number)
    default_city = get_first_city()

    if existing:
        profile_name = existing["name"] or "User"
        profile_city = existing["city"] or (default_city["name"] if default_city else "Unknown")
    else:
        profile_name = "User"
        profile_city = default_city["name"] if default_city else "Unknown"

    return {
        "name": profile_name,
        "city": profile_city,
    }


# =========================
# FRONTEND
# =========================

HOME_HTML = """
<!doctype html>
<html>
<head>
  <title>East Africa Alert System</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --green:#3d6b4f;
      --earth:#8a5a3b;
      --sand:#f4efe6;
      --blue:#4b6f8a;
      --dark:#1f2933;
      --border:#d9e0e7;
      --danger:#b91c1c;
      --success:#15803d;
      --card:#ffffff;
      --muted:#667085;
    }

    *{ box-sizing:border-box; }

    body{
      margin:0;
      font-family:Arial, sans-serif;
      background:var(--sand);
      color:var(--dark);
    }

    .page{
      max-width:1140px;
      margin:36px auto;
      padding:0 20px 36px;
    }

    .hero{
      background:#fff;
      border:1px solid var(--border);
      border-radius:18px;
      padding:24px;
      margin-bottom:20px;
      box-shadow:0 10px 24px rgba(0,0,0,0.05);
    }

    .hero-strip{
      display:flex;
      gap:8px;
      margin-bottom:14px;
    }

    .hero-strip span{
      height:8px;
      border-radius:999px;
      display:block;
    }

    .strip-green{ background:#3d6b4f; width:36%; }
    .strip-earth{ background:#8a5a3b; width:22%; }
    .strip-blue{ background:#4b6f8a; width:42%; }

    .hero h1{
      margin:0 0 8px;
      font-size:2rem;
    }

    .hero p{
      margin:0;
      color:var(--muted);
      line-height:1.5;
    }

    .toolbar{
      display:flex;
      gap:10px;
      margin-bottom:20px;
      flex-wrap:wrap;
    }

    button{
      padding:10px 14px;
      border-radius:10px;
      border:1px solid var(--border);
      cursor:pointer;
      font-weight:700;
      transition:0.15s ease;
    }

    button:hover{
      transform:translateY(-1px);
    }

    .toggle{
      background:#fff;
      color:var(--dark);
    }

    .toggle.active{
      background:var(--green);
      color:#fff;
      border-color:var(--green);
    }

    .primary{
      background:var(--green);
      color:#fff;
      border:none;
    }

    .secondary{
      background:#fff;
      color:var(--dark);
    }

    .danger{
      background:var(--danger);
      color:#fff;
      border:none;
    }

    .card{
      background:var(--card);
      border:1px solid var(--border);
      border-radius:16px;
      padding:20px;
      margin-bottom:20px;
      box-shadow:0 8px 18px rgba(0,0,0,0.04);
    }

    .card h2{
      margin:0 0 10px;
      font-size:1.2rem;
    }

    .card p{
      margin:0 0 14px;
      color:var(--muted);
    }

    .grid{
      display:grid;
      grid-template-columns:1fr 1fr;
      gap:20px;
    }

    @media (max-width: 920px){
      .grid{ grid-template-columns:1fr; }
    }

    label{
      display:block;
      margin:14px 0 8px;
      font-weight:700;
      font-size:0.95rem;
    }

    input, select, textarea{
      width:100%;
      padding:11px 12px;
      border-radius:10px;
      border:1px solid var(--border);
      background:#fff;
      font-size:0.98rem;
      outline:none;
    }

    input:focus, select:focus, textarea:focus{
      border-color:var(--blue);
      box-shadow:0 0 0 3px rgba(75,111,138,0.12);
    }

    .hidden{
      display:none;
    }

    .toast-stack{
      position:fixed;
      top:16px;
      right:16px;
      z-index:9999;
      display:flex;
      flex-direction:column;
      gap:10px;
      max-width:420px;
    }

    .toast{
      padding:14px 16px;
      border-radius:12px;
      color:white;
      font-weight:700;
      box-shadow:0 12px 24px rgba(0,0,0,0.18);
    }

    .success{
      background:var(--success);
    }

    .error{
      background:var(--danger);
    }

    .filter-grid{
      display:grid;
      grid-template-columns:1fr 1fr 1fr;
      gap:10px;
      margin-bottom:14px;
    }

    @media (max-width: 760px){
      .filter-grid{ grid-template-columns:1fr; }
    }

    .table-wrap{
      overflow:auto;
      border:1px solid var(--border);
      border-radius:14px;
      background:#fff;
    }

    table{
      width:100%;
      border-collapse:collapse;
    }

    th, td{
      padding:12px 10px;
      text-align:left;
      border-bottom:1px solid var(--border);
      vertical-align:middle;
    }

    th{
      background:#fafafa;
      font-size:0.9rem;
      color:var(--muted);
    }

    tr:last-child td{
      border-bottom:none;
    }

    .empty{
      color:var(--muted);
      padding:12px 0;
    }

    .small-note{
      font-size:0.92rem;
      color:var(--muted);
      margin-top:10px;
    }
  </style>
</head>
<body>
  <div class="toast-stack">
    {% for category, message in flashes %}
      <div class="toast {{ 'success' if category == 'success' else 'error' }}">{{ message }}</div>
    {% endfor %}
  </div>

  <div class="page">
    <div class="hero">
      <div class="hero-strip">
        <span class="strip-green"></span>
        <span class="strip-earth"></span>
        <span class="strip-blue"></span>
      </div>
      <h1>East Africa Alert System</h1>
      <p>Send area-based alerts, register subscribers, and coordinate communication in a simple field-ready dashboard.</p>
    </div>

    <div class="toolbar">
      <button id="btn-admin" class="toggle active" type="button" onclick="showView('admin')">Operations view</button>
      <button id="btn-helper" class="toggle" type="button" onclick="showView('helper')">Local helper view</button>
    </div>

    <div id="admin-view">
      <div class="card">
        <h2>Send alert</h2>
        <p>Choose one city, or send to all subscribers. You can send an alert type label, a custom SMS, or both together.</p>

        <form method="post" action="/trigger-alert">
          <label for="city">Area</label>
          <select name="city" id="city">
            <option value="ALL">All cities</option>
            {% for c in cities %}
              <option value="{{ c['name'] }}">{{ c['name'] }}</option>
            {% endfor %}
          </select>

          <label for="event">Alert type</label>
          <select name="event" id="event">
            <option value="">Custom message only</option>
            {% for key, e in events.items() %}
              <option value="{{ key }}">{{ e['label'] }}</option>
            {% endfor %}
          </select>

          <label for="custom">Custom message</label>
          <textarea id="custom" name="custom" rows="4" placeholder="Write your own alert message..."></textarea>

          <button class="primary" type="submit">Send alert</button>
        </form>

        <div class="small-note">
          If you choose “All cities”, you can still send an alert label plus a custom message to everyone.
        </div>
      </div>

      <div class="card">
        <h2>Registration campaign</h2>
        <p>Send a registration SMS flow to all current numbers in the system. Users can opt into alerts and weather reports directly from their phone.</p>

        <form method="post" action="/send-registration-campaign">
          <button class="primary" type="submit">Send registration SMS to all users</button>
        </form>

        <div class="small-note">
          Registration flow: 1 = yes, 2 = no. Then weather reports, frequency, days ahead, confirmation, and first forecast.
        </div>
      </div>
    </div>

    <div id="helper-view" class="hidden">
      <div class="grid">
        <div class="card">
          <h2>Add subscriber</h2>
          <p>Register a new subscriber and assign their city and notification settings.</p>

          <form method="post" action="/subscribers/add">
            <label for="name">Name</label>
            <input id="name" name="name" placeholder="Name" required>

            <label for="phone">Phone number</label>
            <input id="phone" name="phone" placeholder="+255..." required>

            <label for="helper-city">City</label>
            <select id="helper-city" name="city" required>
              {% for c in cities %}
                <option value="{{ c['name'] }}">{{ c['name'] }}</option>
              {% endfor %}
            </select>

            <label for="frequency">Frequency</label>
            <select id="frequency" name="frequency" required>
              <option value="daily">daily</option>
              <option value="weekly">weekly</option>
            </select>

            <label for="days">Days ahead</label>
            <select id="days" name="days" required>
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
              <option value="5">5</option>
              <option value="6">6</option>
              <option value="7">7</option>
            </select>

            <button class="primary" type="submit">Add subscriber</button>
          </form>
        </div>

        <div class="card">
          <h2>Filter subscribers</h2>
          <p>Search instantly by name, phone number, or city.</p>

          <div class="filter-grid">
            <input id="filter-name" placeholder="Filter by name">
            <input id="filter-phone" placeholder="Filter by phone">
            <input id="filter-city" placeholder="Filter by city">
          </div>

          <div class="small-note">
            You can combine all three filters at the same time.
          </div>
        </div>
      </div>

      <div class="card">
        <h2>Subscribers</h2>

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Phone</th>
                <th>City</th>
                <th>Frequency</th>
                <th>Days ahead</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {% for s in subscribers %}
                <tr class="subscriber-row"
                    data-name="{{ s.name|lower }}"
                    data-phone="{{ s.phone_number|lower }}"
                    data-city="{{ s.city|lower }}">
                  <td>{{ s.name }}</td>
                  <td>{{ s.phone_number }}</td>
                  <td>{{ s.city }}</td>
                  <td>{{ s.frequency }}</td>
                  <td>{{ s.days_ahead }}</td>
                  <td>
                    <form method="post" action="/subscribers/remove">
                      <input type="hidden" name="id" value="{{ s.id }}">
                      <button class="danger" type="submit">Remove</button>
                    </form>
                  </td>
                </tr>
              {% else %}
                <tr>
                  <td colspan="6" class="empty">No subscribers found.</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <script>
    function setButtonStates(view){
      const adminBtn = document.getElementById("btn-admin");
      const helperBtn = document.getElementById("btn-helper");

      adminBtn.classList.remove("active");
      helperBtn.classList.remove("active");

      if(view === "admin"){
        adminBtn.classList.add("active");
      } else {
        helperBtn.classList.add("active");
      }
    }

    function showView(view){
      document.getElementById("admin-view").classList.add("hidden");
      document.getElementById("helper-view").classList.add("hidden");

      if(view === "admin"){
        document.getElementById("admin-view").classList.remove("hidden");
      } else {
        document.getElementById("helper-view").classList.remove("hidden");
      }

      setButtonStates(view);
      localStorage.setItem("ea-alert-view", view);
    }

    function applyFilters(){
      const nameFilter = (document.getElementById("filter-name")?.value || "").toLowerCase();
      const phoneFilter = (document.getElementById("filter-phone")?.value || "").toLowerCase();
      const cityFilter = (document.getElementById("filter-city")?.value || "").toLowerCase();

      document.querySelectorAll(".subscriber-row").forEach(row => {
        const name = row.dataset.name || "";
        const phone = row.dataset.phone || "";
        const city = row.dataset.city || "";

        const match =
          name.includes(nameFilter) &&
          phone.includes(phoneFilter) &&
          city.includes(cityFilter);

        row.style.display = match ? "" : "none";
      });
    }

    const savedView = localStorage.getItem("ea-alert-view") || "admin";
    showView(savedView);

    const filterName = document.getElementById("filter-name");
    const filterPhone = document.getElementById("filter-phone");
    const filterCity = document.getElementById("filter-city");

    if(filterName) filterName.addEventListener("input", applyFilters);
    if(filterPhone) filterPhone.addEventListener("input", applyFilters);
    if(filterCity) filterCity.addEventListener("input", applyFilters);

    setTimeout(() => {
      document.querySelectorAll(".toast").forEach(t => t.remove());
    }, 3000);
  </script>
</body>
</html>
"""


# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return render_template_string(
        HOME_HTML,
        cities=get_all_cities(),
        subscribers=get_all_subscribers(),
        events=EXTREME_EVENT_RULES,
        flashes=get_flashed_messages(with_categories=True)
    )


@app.route("/trigger-alert", methods=["POST"])
def trigger_alert():
    try:
        city = request.form["city"]
        event = request.form.get("event", "").strip()
        custom = request.form.get("custom", "").strip()

        if city == "ALL":
            recipients = get_all_subscribers()
            if not recipients:
                flash("No subscribers found.", "error")
                return redirect("/")

            if not event and not custom:
                flash("Please select an alert type or enter a custom message.", "error")
                return redirect("/")

            message = build_alert_message("all areas", event, custom)
        else:
            recipients = get_subscribers_for_area(city)
            if not recipients:
                flash(f"No subscribers found for {city}.", "error")
                return redirect("/")

            if not event and not custom:
                flash("Please select an alert type or enter a custom message.", "error")
                return redirect("/")

            message = build_alert_message(city, event, custom)

        for s in recipients:
            send_sms(s["phone_number"], f"{s['name']}, {message}")
            time.sleep(1)
            send_forecast_prompt(s["phone_number"])
            time.sleep(1)

        flash(f"Alert sent to {len(recipients)} users.", "success")

    except Exception as e:
        flash(str(e), "error")

    return redirect("/")


@app.route("/send-registration-campaign", methods=["POST"])
def send_registration_campaign():
    try:
        recipients = get_all_subscribers()

        if not recipients:
            flash("No users found to send the registration campaign to.", "error")
            return redirect("/")

        sent_count = 0

        for subscriber in recipients:
            send_sms(
                subscriber["phone_number"],
                "Do you want to register for the alerts system?\n1 = Yes\n2 = No"
            )
            upsert_registration_session(subscriber["phone_number"], "ask_register")
            sent_count += 1
            time.sleep(1)

        flash(f"Registration campaign sent to {sent_count} users.", "success")

    except Exception as e:
        flash(str(e), "error")

    return redirect("/")


@app.route("/subscribers/add", methods=["POST"])
def sub_add():
    try:
        add_subscriber(
            request.form["name"],
            request.form["phone"],
            request.form["city"],
            request.form["frequency"],
            request.form["days"]
        )
        flash("Subscriber added.", "success")

    except Exception as e:
        flash(str(e), "error")

    return redirect("/")


@app.route("/subscribers/remove", methods=["POST"])
def sub_remove():
    try:
        removed = remove_subscriber(request.form["id"])
        if removed:
            flash("Subscriber removed.", "success")
        else:
            flash("Subscriber not found.", "error")

    except Exception as e:
        flash(str(e), "error")

    return redirect("/")


# =========================
# WEBHOOK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(silent=True) or {}

        sender = data.get("data", {}).get("contact")
        text = (data.get("data", {}).get("content") or "").strip()

        if not sender:
            return jsonify({"ok": True, "ignored": "missing sender"})

        lowered = text.lower()

        # STOP unsubscribe
        if lowered == "stop":
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM weather_subscribers WHERE phone_number = %s",
                        (sender,)
                    )
                    conn.commit()

            delete_registration_session(sender)
            send_sms(sender, "You are unsubscribed")
            return jsonify({"ok": True})

        # Check registration session first
        session = get_registration_session(sender)

        if session:
            step = session["step"]

            if step == "ask_register":
                if text == "1":
                    profile = get_registration_profile(sender)

                    add_subscriber(
                        profile["name"],
                        sender,
                        profile["city"],
                        "alerts_only",
                        1
                    )

                    upsert_registration_session(sender, "ask_weather")
                    send_sms(
                        sender,
                        "You are registered for the alert system. Do you also want the weather reports?\n1 = Yes\n2 = No"
                    )
                elif text == "2":
                    delete_registration_session(sender)
                    send_sms(sender, "You have not been registered for the alert system.")
                else:
                    send_sms(sender, "Please reply:\n1 = Yes\n2 = No")

                return jsonify({"ok": True})

            if step == "ask_weather":
                if text == "1":
                    upsert_registration_session(sender, "ask_frequency", wants_weather=True)
                    send_sms(sender, "How often do you want the weather reports?\n1 = Daily\n2 = Weekly")
                elif text == "2":
                    delete_registration_session(sender)
                    send_sms(sender, "You have been registered for the alert system.")
                else:
                    send_sms(sender, "Please reply:\n1 = Yes\n2 = No")

                return jsonify({"ok": True})

            if step == "ask_frequency":
                if text == "1":
                    upsert_registration_session(sender, "ask_days_ahead", wants_weather=True, frequency="daily")
                    send_sms(sender, "How many days ahead do you want to be shown? Reply with a number from 1 to 7.")
                elif text == "2":
                    upsert_registration_session(sender, "ask_days_ahead", wants_weather=True, frequency="weekly")
                    send_sms(sender, "How many days ahead do you want to be shown? Reply with a number from 1 to 7.")
                else:
                    send_sms(sender, "How often do you want the weather reports?\n1 = Daily\n2 = Weekly")

                return jsonify({"ok": True})

            if step == "ask_days_ahead":
                try:
                    days_ahead = int(text)
                except ValueError:
                    send_sms(sender, "Please reply with a number from 1 to 7.")
                    return jsonify({"ok": True})

                if days_ahead < 1 or days_ahead > 7:
                    send_sms(sender, "Please reply with a number from 1 to 7.")
                    return jsonify({"ok": True})

                profile = get_registration_profile(sender)
                frequency = session["frequency"] or "daily"

                add_subscriber(
                    profile["name"],
                    sender,
                    profile["city"],
                    frequency,
                    days_ahead
                )

                delete_registration_session(sender)

                city_row = get_city_row_by_name(profile["city"])
                send_sms(sender, "You have been registered for the alert system and weather reports.")

                if city_row:
                    time.sleep(1)
                    weather_forecast = get_forecast(city_row["lat"], city_row["lon"], days=days_ahead)
                    forecast_message = build_forecast_message(profile["city"], weather_forecast, days_ahead)
                    send_sms(sender, forecast_message)

                return jsonify({"ok": True})

        # Forecast reply only for existing subscribers, after registration flow is handled
        if text.isdigit():
            requested_days = int(text)

            if 1 <= requested_days <= 7:
                subscriber = get_subscriber_by_phone(sender)

                if not subscriber:
                    return jsonify({"ok": True, "ignored": "number reply from non-subscriber"})

                subscriber_city = subscriber.get("city")
                if not subscriber_city or subscriber_city == "Unknown":
                    default_city = get_first_city()
                    if not default_city:
                        send_sms(sender, "Forecast is not available right now.")
                        return jsonify({"ok": True})

                    weather_forecast = get_forecast(default_city["lat"], default_city["lon"], days=requested_days)
                    forecast_message = build_forecast_message(default_city["name"], weather_forecast, requested_days)
                    send_sms(sender, forecast_message)
                    return jsonify({"ok": True})

                city_row = get_city_row_by_name(subscriber_city)
                if not city_row:
                    send_sms(sender, "Forecast city could not be found.")
                    return jsonify({"ok": True})

                weather_forecast = get_forecast(city_row["lat"], city_row["lon"], days=requested_days)
                forecast_message = build_forecast_message(subscriber_city, weather_forecast, requested_days)
                send_sms(sender, forecast_message)
                return jsonify({"ok": True})

        return jsonify({"ok": True, "ignored": True})

    except Exception as e:
        print("WEBHOOK ERROR:", str(e))
        return jsonify({"ok": False, "error": str(e)}), 200

# =========================
# START
# =========================

create_support_tables()

if __name__ == "__main__":
    app.run(port=5001, debug=True)