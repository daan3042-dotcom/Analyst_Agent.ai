"""
fred_data.py
Actuele macro-cijfers (rentestanden, inflatie, werkloosheid) rechtstreeks
van FRED (Federal Reserve Economic Data) -- grondt sectie 9 (Macro Exposure)
in echte, actuele cijfers i.p.v. Claude's mogelijk verouderde trainingsdata
over "de rente is momenteel hoog/laag."

Vereist een GRATIS FRED-API-key (aanvragen op https://fred.stlouisfed.org/docs/api/api_key.html),
in te stellen als FRED_API_KEY, zelfde manier als de andere keys.

Dit wordt ALTIJD opgehaald (net als de SEC-data), niet als losse tool --
macro-context is voor vrijwel elk bedrijf relevant, in tegenstelling tot
een specifieke grondstof of valuta.
"""

import os

import requests

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# De belangrijkste, meest algemeen relevante macro-reeksen -- FRED heeft
# duizenden reeksen, dit is een bewust kleine, brede selectie.
SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "10y_treasury_yield": "DGS10",
    "cpi_inflation_index": "CPIAUCSL",
    "unemployment_rate": "UNRATE",
}


def _fetch_latest_value(series_id: str, api_key: str) -> dict | None:
    try:
        resp = requests.get(BASE_URL, params={
            "series_id": series_id, "api_key": api_key, "file_type": "json",
            "sort_order": "desc", "limit": 1,
        }, timeout=15)
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
    except Exception:
        return None

    if not observations or observations[0].get("value") in (None, "."):
        return None
    return {"value": observations[0]["value"], "date": observations[0]["date"]}


def fetch_macro_snapshot() -> dict:
    """Haalt de meest recente waarde op voor een klein setje kernindicatoren.
    Geeft per reeks die WEL beschikbaar was een waarde+datum terug; reeksen
    die niet opgehaald konden worden, worden gewoon weggelaten (geen gok)."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return {"error": "FRED_API_KEY niet gevonden in environment"}

    snapshot = {}
    for label, series_id in SERIES.items():
        result = _fetch_latest_value(series_id, api_key)
        if result:
            snapshot[label] = result

    if not snapshot:
        return {"error": "geen enkele macro-reeks kon worden opgehaald (check de API-key of netwerktoegang)"}

    return snapshot
