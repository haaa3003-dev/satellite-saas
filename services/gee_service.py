import requests
import streamlit as st

@st.cache_data(show_spinner=False)
def geocode_place(query: str):
    if not query or len(query.strip()) < 2:
        return []

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": 5,
        "countrycodes": "kr",
        "accept-language": "ko",
    }
    headers = {"User-Agent": "satellite-saas-student-project/1.0"}

    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)
        res.raise_for_status()
        results = res.json()
        return [
            (float(item["lat"]), float(item["lon"]), item["display_name"])
            for item in results
        ]
    except Exception:
        return []