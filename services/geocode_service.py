import requests
import streamlit as st

@st.cache_data(show_spinner=False)
def geocode_place(query: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 5, "countrycodes": "kr", "accept-language": "ko"}
    res = requests.get(url, params=params, headers={"User-Agent": "satellite-saas-project/1.0"})
    return [(float(i["lat"]), float(i["lon"]), i["display_name"]) for i in res.json()]