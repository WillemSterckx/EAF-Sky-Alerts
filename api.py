import requests
import sys
from datetime import datetime, timedelta
from cities import CITIES

WEATHER_API = "https://api.open-meteo.com/v1/forecast"
FLOOD_API = "https://flood-api.open-meteo.com/v1/flood"
CLIMATE_API = "https://climate-api.open-meteo.com/v1/climate"

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

# Severe weather codes that trigger a warning
SEVERE_CODES = {57, 65, 67, 75, 82, 86, 95, 96, 99}

def find_city(identifier):
    """Find city by key or name (case‑insensitive)."""
    if identifier in CITIES:
        info = CITIES[identifier]
        return info["lat"], info["lon"], info["name"]
    identifier_lower = identifier.lower()
    for info in CITIES.values():
        if info["name"].lower() == identifier_lower:
            return info["lat"], info["lon"], info["name"]
    return None

def get_weather_forecast(lat, lon, days):
    """Fetch daily weather forecast for the specified number of days."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ["weathercode", "temperature_2m_max", "temperature_2m_min", "precipitation_sum"],
        "timezone": "auto",
        "forecast_days": days,
    }
    try:
        r = requests.get(WEATHER_API, params=params)
        r.raise_for_status()
        return r.json().get("daily", {})
    except Exception as e:
        print(f"Weather API error: {e}")
        return {}

def format_forecast(daily, city_name, days):
    """Return formatted string for the forecast table."""
    if not daily or not daily.get("time"):
        return "No forecast data available."

    dates = daily["time"]
    codes = daily.get("weathercode", [])
    maxs = daily.get("temperature_2m_max", [])
    mins = daily.get("temperature_2m_min", [])
    precips = daily.get("precipitation_sum", [])

    lines = [
        f"\n{days}-Day Forecast for {city_name}, Tanzania\n",
        f"{'Date':<12} {'Condition':<25} {'Max Temp':<10} {'Min Temp':<10} {'Precip (mm)':<12}",
        "-" * 70
    ]

    for i, d in enumerate(dates):
        date = datetime.strptime(d, "%Y-%m-%d").strftime("%d %b %Y")
        cond = WEATHER_CODES.get(codes[i] if i < len(codes) else None, "Unknown")
        max_t = maxs[i] if i < len(maxs) else "N/A"
        min_t = mins[i] if i < len(mins) else "N/A"
        prec = precips[i] if i < len(precips) else "N/A"
        lines.append(f"{date:<12} {cond:<25} {max_t:<10} {min_t:<10} {prec:<12}")

    return "\n".join(lines)

def check_severe_weather(daily):
    """
    Scan daily weather codes for severe conditions.
    Returns a warning string if any are found, otherwise None.
    """
    if not daily or not daily.get("time") or not daily.get("weathercode"):
        return None

    dates = daily["time"]
    codes = daily["weathercode"]

    severe_days = []
    for i, code in enumerate(codes):
        if i < len(dates) and code in SEVERE_CODES:
            date_str = datetime.strptime(dates[i], "%Y-%m-%d").strftime("%d %b %Y")
            condition = WEATHER_CODES.get(code, "Unknown")
            severe_days.append(f"{date_str} ({condition})")

    if severe_days:
        return "SEVERE WEATHER WARNING: " + ", ".join(severe_days)
    return None

def check_flood_risk(lat, lon):
    """Flood warning if river discharge exceeds 75th percentile on any of next 7 days."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ["river_discharge", "river_discharge_p75"],
        "forecast_days": 7,
    }
    try:
        r = requests.get(FLOOD_API, params=params)
        r.raise_for_status()
        data = r.json().get("daily", {})
        dates = data.get("time", [])
        discharge = data.get("river_discharge", [])
        p75 = data.get("river_discharge_p75", [])
        if not dates or not discharge or not p75:
            return None
        risky = [date for i, date in enumerate(dates)
                 if i < len(discharge) and i < len(p75) and discharge[i] > p75[i]]
        if risky:
            return f"FLOOD WARNING: River discharge above 75th percentile on: {', '.join(risky)}"
    except Exception:
        pass
    return None

def check_drought_risk(lat, lon):
    """Drought warning if next month's forecast precipitation <75% of historical avg."""
    today = datetime.now()
    if today.month == 12:
        next_year, next_month = today.year + 1, 1
    else:
        next_year, next_month = today.year, today.month + 1

    start_next = datetime(next_year, next_month, 1)
    if next_month == 12:
        end_next = datetime(next_year, 12, 31)
    else:
        end_next = datetime(next_year, next_month + 1, 1) - timedelta(days=1)

    # Forecast for next month
    forecast_params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_next.strftime("%Y-%m-%d"),
        "end_date": end_next.strftime("%Y-%m-%d"),
        "models": "EC_Earth3P_HR",
        "monthly": "precipitation_sum",
    }
    try:
        r_forecast = requests.get(CLIMATE_API, params=forecast_params)
        r_forecast.raise_for_status()
        forecast_data = r_forecast.json().get("monthly", {})
        if not forecast_data.get("time"):
            return None
        forecast_mm = forecast_data["precipitation_sum"][0]
    except Exception:
        return None

    # Historical 1990–2020 for same month
    start_hist = datetime(1990, next_month, 1)
    end_hist = datetime(2020, next_month, 1)
    if next_month == 12:
        end_hist = datetime(2020, 12, 31)
    else:
        end_hist = datetime(2020, next_month + 1, 1) - timedelta(days=1)

    hist_params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_hist.strftime("%Y-%m-%d"),
        "end_date": end_hist.strftime("%Y-%m-%d"),
        "models": "EC_Earth3P_HR",
        "monthly": "precipitation_sum",
    }
    try:
        r_hist = requests.get(CLIMATE_API, params=hist_params)
        r_hist.raise_for_status()
        hist_data = r_hist.json().get("monthly", {})
        times = hist_data.get("time", [])
        values = hist_data.get("precipitation_sum", [])
        month_values = [v for t, v in zip(times, values) if datetime.strptime(t, "%Y-%m-%d").month == next_month]
        if not month_values:
            return None
        avg_hist = sum(month_values) / len(month_values)
    except Exception:
        return None

    if avg_hist > 0 and forecast_mm < 0.75 * avg_hist:
        month_name = start_next.strftime("%B %Y")
        return (f"DROUGHT WARNING: Forecast precipitation for {month_name} "
                f"({forecast_mm:.1f} mm) is less than 75% of the historical average "
                f"({avg_hist:.1f} mm).")
    return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Error: Please specify a city.")
        print("Usage: python forecast.py <city name or number> [days]")
        print("       days: optional number of days to forecast (1-16, default 7)")
        print("Available cities: " + ", ".join(info["name"] for info in CITIES.values()))
        sys.exit(1)

    city_arg = sys.argv[1].strip()
    result = find_city(city_arg)
    if not result:
        print(f"Error: '{city_arg}' is not a known city.")
        print("Available cities: " + ", ".join(info["name"] for info in CITIES.values()))
        sys.exit(1)

    # Determine number of days
    days = 7  # default
    if len(sys.argv) >= 3:
        try:
            days = int(sys.argv[2])
            if days < 1 or days > 16:
                print("Error: Number of days must be between 1 and 16.")
                sys.exit(1)
        except ValueError:
            print("Error: Third argument must be an integer (number of days).")
            sys.exit(1)

    lat, lon, city_name = result

    # Weather forecast
    forecast = get_weather_forecast(lat, lon, days)
    output = format_forecast(forecast, city_name, days)
    print(output)

    # Hazard warnings
    severe_warning = check_severe_weather(forecast)
    if severe_warning:
        print("\n" + severe_warning)

    flood_warning = check_flood_risk(lat, lon)
    if flood_warning:
        print("\n" + flood_warning)

    drought_warning = check_drought_risk(lat, lon)
    if drought_warning:
        print("\n" + drought_warning)