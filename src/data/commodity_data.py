"""
commodity_data.py
Grondstofprijzen en wisselkoersen via Alpha Vantage -- hergebruikt dezelfde
API-key als data_fetch.py's nieuws-tool, geen nieuwe aanmelding nodig.

Dit is bewust een TOOL (Claude roept 'm actief aan met een specifieke
grondstof/valuta), niet iets dat standaard wordt meegestuurd zoals de
SEC-data -- welke grondstof relevant is, verschilt enorm per bedrijf
(aluminium bij Alcoa, koper bij Ero Copper, olie bij een energiebedrijf).
"""

import os

import requests

BASE_URL = "https://www.alphavantage.co/query"

# De specifieke Alpha Vantage-functienamen per grondstof -- dit is geen
# vrije tekst, Alpha Vantage vereist exact deze namen.
SUPPORTED_COMMODITIES = {
    "WTI", "BRENT", "NATURAL_GAS", "COPPER", "ALUMINUM",
    "WHEAT", "CORN", "COTTON", "SUGAR", "COFFEE",
}


def fetch_commodity_price(commodity: str) -> dict:
    """Haalt de meest recente prijspunten op voor een grondstof. commodity
    moet één van SUPPORTED_COMMODITIES zijn (hoofdletterongevoelig)."""
    commodity = commodity.upper().strip()
    if commodity not in SUPPORTED_COMMODITIES:
        return {"error": f"onbekende grondstof '{commodity}', ondersteund: {sorted(SUPPORTED_COMMODITIES)}"}

    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not api_key:
        return {"error": "ALPHAVANTAGE_API_KEY niet gevonden in environment"}

    try:
        resp = requests.get(BASE_URL, params={
            "function": commodity, "interval": "monthly", "apikey": api_key,
        }, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return {"error": f"kon grondstofprijs niet ophalen voor {commodity}"}

    data_points = payload.get("data", [])
    if not data_points:
        return {"error": f"geen data teruggegeven voor {commodity} (mogelijk een API-limietoverschrijding)"}

    recent = [p for p in data_points[:6] if p.get("value") not in (None, ".")]
    return {
        "commodity": commodity,
        "unit": payload.get("unit", "onbekend"),
        "recent_monthly_values": recent,
    }


def fetch_fx_rate(from_currency: str, to_currency: str) -> dict:
    """Haalt de actuele wisselkoers op tussen twee valuta (bijv. EUR->USD)."""
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not api_key:
        return {"error": "ALPHAVANTAGE_API_KEY niet gevonden in environment"}

    try:
        resp = requests.get(BASE_URL, params={
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": from_currency.upper(), "to_currency": to_currency.upper(),
            "apikey": api_key,
        }, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return {"error": f"kon wisselkoers niet ophalen voor {from_currency}->{to_currency}"}

    rate_data = payload.get("Realtime Currency Exchange Rate")
    if not rate_data:
        return {"error": f"geen wisselkoersdata teruggegeven voor {from_currency}->{to_currency}"}

    return {
        "from_currency": rate_data.get("1. From_Currency Code"),
        "to_currency": rate_data.get("3. To_Currency Code"),
        "exchange_rate": rate_data.get("5. Exchange Rate"),
        "last_refreshed": rate_data.get("6. Last Refreshed"),
    }
