
import streamlit as st
import requests
import pandas as pd
import sqlite3
import time

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


# ============================================================
# 1. STREAMLIT BEÁLLÍTÁSOK
# ============================================================

st.set_page_config(
    page_title="Weather App",
    page_icon="🌤️",
    layout="wide"
)

DATABASE_PATH = Path("weather_logs.db")

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


# ============================================================
# 2. SQLITE ADATBÁZIS
# ============================================================

def get_connection():
    return sqlite3.connect(
        DATABASE_PATH,
        timeout=10
    )


def init_db():
    """Létrehozza a keresési napló tábláját."""

    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_logs (
                city TEXT,
                temperature REAL,
                humidity INTEGER,
                wind_speed REAL,
                timestamp TEXT
            )
        """)


def log_search(city, temp, hum, wind):
    """Elmenti a felhasználó által indított keresést."""

    timestamp = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO search_logs (
                city,
                temperature,
                humidity,
                wind_speed,
                timestamp
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            city,
            float(temp),
            int(hum),
            float(wind),
            timestamp
        ))


def get_search_history(limit=20):
    """Lekéri az utolsó kereséseket."""

    with get_connection() as conn:
        return pd.read_sql_query("""
            SELECT
                city AS City,
                temperature AS Temperature,
                humidity AS Humidity,
                wind_speed AS Wind,
                timestamp AS Timestamp
            FROM search_logs
            ORDER BY rowid DESC
            LIMIT ?
        """, conn, params=(limit,))


init_db()


# ============================================================
# 3. API LEKÉRÉSEK ÉS HIBAKEZELÉS
# ============================================================

def request_api(url, params, max_attempts=3):
    """HTTP lekérés újrapróbálkozással."""

    for attempt in range(max_attempts):

        try:
            response = requests.get(
                url,
                params=params,
                timeout=10
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                raise ValueError(
                    "Az API válasza nem megfelelő formátumú."
                )

            if data.get("error"):
                raise ValueError(
                    data.get("reason", "Ismeretlen API-hiba.")
                )

            return data

        except requests.exceptions.HTTPError as error:

            status = error.response.status_code

            # Ezeket a hibákat nem érdemes újrapróbálni.
            if status not in (429, 500, 502, 503, 504):
                raise RuntimeError(
                    f"HTTP-hiba: {status}"
                ) from error

            if attempt == max_attempts - 1:
                raise RuntimeError(
                    f"Az API nem elérhető. HTTP: {status}"
                ) from error

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        ) as error:

            if attempt == max_attempts - 1:
                raise RuntimeError(
                    "Hálózati hiba vagy időtúllépés."
                ) from error

        except requests.exceptions.RequestException as error:
            raise RuntimeError(
                f"HTTP-kommunikációs hiba: {error}"
            ) from error

        except ValueError as error:
            raise RuntimeError(
                f"Hibás API-válasz: {error}"
            ) from error

        time.sleep(2 ** attempt)

    raise RuntimeError("Az API-lekérés sikertelen.")


# ============================================================
# 4. VÁROS KOORDINÁTÁINAK LEKÉRÉSE
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def get_coordinates(city_name):

    params = {
        "name": city_name.strip(),
        "count": 10,
        "language": "en",
        "format": "json"
    }

    data = request_api(GEOCODING_URL, params)

    return data.get("results", [])


# ============================================================
# 5. IDŐJÁRÁSI ADATOK LEKÉRÉSE
# ============================================================

@st.cache_data(ttl=600, show_spinner=False)
def get_weather_data(lat, lon):

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "wind_speed_10m"
        ),
        "hourly": "temperature_2m",
        "forecast_hours": 120,
        "timezone": "auto",
        "wind_speed_unit": "kmh",
        "temperature_unit": "celsius"
    }

    data = request_api(WEATHER_URL, params)

    if "current" not in data or "hourly" not in data:
        raise ValueError(
            "Hiányoznak az időjárási adatok."
        )

    return data


# ============================================================
# 6. ELŐREJELZÉSI ADATOK FELDOLGOZÁSA
# ============================================================

def prepare_forecast(weather_data):

    hourly = weather_data["hourly"]

    forecast_df = pd.DataFrame({
        "Time": pd.to_datetime(hourly["time"]),
        "Temperature (°C)": hourly["temperature_2m"]
    })

    timezone_name = weather_data.get(
        "timezone", "UTC"
    )

    # Az API helyi időt ad vissza, időzónaeltolás nélkül.
    # A város időzónáját külön hozzárendeljük.
    forecast_df["Time"] = (
        forecast_df["Time"]
        .dt.tz_localize(
            ZoneInfo(timezone_name),
            ambiguous="NaT",
            nonexistent="shift_forward"
        )
    )

    forecast_df = forecast_df.dropna(
        subset=["Time"]
    )

    now = pd.Timestamp.now(
        tz=ZoneInfo(timezone_name)
    )

    # Csak a jelenlegi és jövőbeli órák.
    forecast_df = forecast_df[
        forecast_df["Time"] >= now.floor("h")
    ].copy()

    forecast_df = forecast_df.set_index("Time")

    return forecast_df


# ============================================================
# 7. SESSION STATE
# ============================================================

if "selected_weather" not in st.session_state:
    st.session_state.selected_weather = None

if "selected_location" not in st.session_state:
    st.session_state.selected_location = None

if "search_results" not in st.session_state:
    st.session_state.search_results = []


# ============================================================
# 8. FELHASZNÁLÓI FELÜLET
# ============================================================

st.title("🌤️ Robot Dreams Python - Weather App")

st.write(
    "Weather Map, Data Visualization and SQLite Logging"
)

st.divider()

with st.form("city_search_form"):

    city_input = st.text_input(
        "Enter city name",
        value="Diósd"
    )

    search_button = st.form_submit_button(
        "🔍 Search City",
        type="primary"
    )


# ============================================================
# 9. VÁROSKERESÉS
# ============================================================

if search_button:

    city_name = city_input.strip()

    if len(city_name) < 2:

        st.warning(
            "Please enter at least 2 characters."
        )

    else:

        try:
            with st.spinner("Searching for city..."):

                locations = get_coordinates(city_name)

            st.session_state.search_results = locations

            # Új keresésnél a korábbi adatokat töröljük.
            st.session_state.selected_weather = None
            st.session_state.selected_location = None

            if not locations:
                st.warning(
                    f"City not found: {city_name}"
                )

        except Exception as error:
            st.error(f"Geocoding error: {error}")


# ============================================================
# 10. TALÁLAT KIVÁLASZTÁSA
# ============================================================

locations = st.session_state.search_results

if locations:

    location_options = {
        index: (
            f"{location.get('name', '')}, "
            f"{location.get('admin1', '')}, "
            f"{location.get('country', '')}"
        )
        for index, location in enumerate(locations)
    }

    selected_index = st.selectbox(
        "Select the correct location",
        options=list(location_options.keys()),
        format_func=lambda index: location_options[index]
    )

    if st.button(
        "🌦️ Get Weather",
        type="primary"
    ):

        location = locations[selected_index]

        lat = location["latitude"]
        lon = location["longitude"]

        try:
            with st.spinner("Loading weather data..."):

                weather_data = get_weather_data(
                    lat, lon
                )

            current = weather_data["current"]

            temp = current["temperature_2m"]
            hum = current["relative_humidity_2m"]
            wind = current["wind_speed_10m"]

            exact_city = location["name"]
            country = location.get("country", "")

            # Csak tényleges felhasználói lekérésnél mentünk.
            log_search(
                f"{exact_city}, {country}",
                temp,
                hum,
                wind
            )

            st.session_state.selected_weather = weather_data
            st.session_state.selected_location = location

            st.success(
                "Weather data loaded successfully!"
            )

        except (RuntimeError, ValueError, KeyError) as error:
            st.error(f"Weather API error: {error}")

        except sqlite3.Error as error:
            st.error(f"Database error: {error}")


# ============================================================
# 11. AKTUÁLIS IDŐJÁRÁS
# ============================================================

weather_data = st.session_state.selected_weather
location = st.session_state.selected_location

if weather_data is not None and location is not None:

    current = weather_data["current"]

    temp = current["temperature_2m"]
    hum = current["relative_humidity_2m"]
    wind = current["wind_speed_10m"]

    exact_city = location["name"]
    country = location.get("country", "")

    lat = location["latitude"]
    lon = location["longitude"]

    st.divider()

    st.header(
        f"Current Weather in {exact_city}, {country}"
    )

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "🌡️ Temperature",
        f"{temp:.1f} °C"
    )

    col2.metric(
        "💧 Humidity",
        f"{hum} %"
    )

    col3.metric(
        "💨 Wind Speed",
        f"{wind:.1f} km/h"
    )

    # --------------------------------------------------------
    # TÉRKÉP
    # --------------------------------------------------------

    st.subheader("📍 Weather Map")

    map_df = pd.DataFrame({
        "lat": [lat],
        "lon": [lon]
    })

    st.map(map_df, zoom=11)

    # --------------------------------------------------------
    # ÓRÁNKÉNTI ELŐREJELZÉS
    # --------------------------------------------------------

    st.subheader(
        "📈 Hourly Temperature Forecast (Next 5 Days)"
    )

    try:
        forecast_df = prepare_forecast(
            weather_data
        )

        if not forecast_df.empty:

            st.line_chart(
                forecast_df[["Temperature (°C)"]],
                x_label="Date and Time",
                y_label="Temperature (°C)"
            )

            with st.expander(
                "Show Forecast Data"
            ):
                st.dataframe(
                    forecast_df,
                    use_container_width=True
                )

                csv_data = forecast_df.to_csv().encode(
                    "utf-8-sig"
                )

                st.download_button(
                    label="📥 Download Forecast CSV",
                    data=csv_data,
                    file_name="weather_forecast.csv",
                    mime="text/csv"
                )

        else:
            st.info(
                "No future forecast data available."
            )

    except (ValueError, KeyError, TypeError) as error:
        st.error(
            f"Forecast processing error: {error}"
        )


# ============================================================
# 12. SQLITE KERESÉSI ELŐZMÉNYEK
# ============================================================

st.divider()

with st.expander("🗄️ Search History (SQLite)"):

    try:
        history_df = get_search_history()

        if not history_df.empty:

            st.dataframe(
                history_df,
                use_container_width=True,
                hide_index=True
            )

            history_csv = history_df.to_csv(
                index=False
            ).encode("utf-8-sig")

            st.download_button(
                "📥 Download Search History",
                data=history_csv,
                file_name="weather_search_history.csv",
                mime="text/csv"
            )

        else:
            st.info("No searches recorded yet.")

    except sqlite3.Error as error:
        st.error(f"Database error: {error}")


# ============================================================
# 13. LÁBLÉC
# ============================================================

st.divider()

st.caption(
    "Weather data provided by Open-Meteo | "
    "Developed with Python, Streamlit, Pandas and SQLite"
)
