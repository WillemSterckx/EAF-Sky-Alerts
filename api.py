import requests
import sys
from datetime import datetime
from cities import CITIES

BASE_URL = "https://api.open-meteo.com/v1/forecast"

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

def find_city(identifier):
    """Find city by key or name (case‑insensitive). Returns (lat, lon, name) or None."""
    if identifier in CITIES:
        info = CITIES[identifier]
        return info["lat"], info["lon"], info["name"]

    identifier_lower = identifier.lower()
    for info in CITIES.values():
        if info["name"].lower() == identifier_lower:
            return info["lat"], info["lon"], info["name"]
    return None

def get_forecast(lat, lon, days=7):
    """Fetch forecast for the specified number of days."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ["weathercode", "temperature_2m_max", "temperature_2m_min", "precipitation_sum"],
        "timezone": "auto",
        "forecast_days": days,
    }
    try:
        response = requests.get(BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("daily", {})
    except requests.exceptions.RequestException as e:
        print(f"Error fetching forecast: {e}")
        sys.exit(1)

def display_tomorrow_forecast(daily, city_name):
    """Print forecast for the next day only."""
    if not daily or not daily.get("time"):
        print("No forecast data available.")
        return

    date_str = daily["time"][0]
    weathercode = daily["weathercode"][0] if daily.get("weathercode") else None
    max_temp = daily["temperature_2m_max"][0] if daily.get("temperature_2m_max") else "N/A"
    min_temp = daily["temperature_2m_min"][0] if daily.get("temperature_2m_min") else "N/A"
    precip = daily["precipitation_sum"][0] if daily.get("precipitation_sum") else "N/A"

    date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d %b %Y")
    condition = WEATHER_CODES.get(weathercode, "Unknown")

    print(f"\nTomorrow's Forecast for {city_name}, Tanzania – {date}\n")
    print(f"{'Condition':<25} {'Max Temp':<10} {'Min Temp':<10} {'Precip (mm)':<12}")
    print("-" * 60)
    print(f"{condition:<25} {max_temp:<10} {min_temp:<10} {precip:<12}")

def display_weekly_forecast(daily, city_name):
    """Print the daily breakdown for the next 7 days."""
    if not daily:
        print("No forecast data available.")
        return

    dates = daily.get("time", [])
    weathercodes = daily.get("weathercode", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])

    if not dates:
        print("No dates in forecast.")
        return

    print(f"\n7-Day Forecast for {city_name}, Tanzania\n")
    print(f"{'Date':<12} {'Condition':<25} {'Max Temp':<10} {'Min Temp':<10} {'Precip (mm)':<12}")
    print("-" * 70)

    for i, date_str in enumerate(dates):
        date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d %b %Y")
        code = weathercodes[i] if i < len(weathercodes) else None
        condition = WEATHER_CODES.get(code, "Unknown")
        max_t = max_temps[i] if i < len(max_temps) else "N/A"
        min_t = min_temps[i] if i < len(min_temps) else "N/A"
        prec = precip[i] if i < len(precip) else "N/A"

        print(f"{date:<12} {condition:<25} {max_t:<10} {min_t:<10} {prec:<12}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Error: Please specify a city.")
        print("Usage: python forecast.py <city name or number> [daily|weekly]")
        print("Available cities: " + ", ".join(info["name"] for info in CITIES.values()))
        sys.exit(1)

    city_arg = sys.argv[1].strip()
    result = find_city(city_arg)

    if not result:
        print(f"Error: '{city_arg}' is not a known city.")
        print("Available cities: " + ", ".join(info["name"] for info in CITIES.values()))
        sys.exit(1)

    # Determine forecast mode
    mode = "weekly"  # default
    if len(sys.argv) >= 3:
        arg = sys.argv[2].strip().lower()
        if arg in ["daily", "weekly"]:
            mode = arg
        else:
            print(f"Warning: Unknown mode '{arg}'. Using 'weekly'.")

    lat, lon, city_name = result

    if mode == "daily":
        forecast = get_forecast(lat, lon, days=1)
        display_tomorrow_forecast(forecast, city_name)
    else:  # weekly
        forecast = get_forecast(lat, lon, days=7)
        display_weekly_forecast(forecast, city_name)