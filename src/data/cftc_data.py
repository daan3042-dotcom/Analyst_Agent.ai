"""
cftc_data.py
Haalt speculatieve futures-positionering op uit de wekelijkse "Commitments
of Traders" (COT)-rapportage van de CFTC (de Amerikaanse toezichthouder op
grondstoffen- en financiele futures) -- hoeveel van de open interest in een
futures-contract wordt aangehouden door "non-commercial" (speculatieve)
partijen versus "commercial" (hedging) partijen, en hoe die netto-positie
recent bewoog. Klassiek marktsentiment-signaal voor grondstofgevoelige
bedrijven, los van alles wat de rest van de agent al berekent.

Dit is bewust een TOOL (net als commodity_data.py), niet iets dat standaard
voor elk bedrijf wordt opgehaald: CFTC-positioneringsdata bestaat alleen
voor onderliggende futures-contracten (grondstoffen, valuta, indices), niet
voor individuele aandelen. Gebruikt daarom dezelfde grondstoflijst als
commodity_data.SUPPORTED_COMMODITIES, zodat Claude niet twee verschillende
namenlijsten hoeft te kennen.

BELANGRIJK, EERLIJKHEID OVER ONZEKERHEID: net als bij finra_data.py kon dit
NIET live getest worden (deze ontwikkelomgeving heeft geen netwerktoegang
tot de CFTC). De dataset-ID en veldnamen hieronder zijn gebaseerd op de
publiek gedocumenteerde, al jarenlang stabiele "Legacy Futures Only"
COT-rapportage -- maar om dezelfde reden als bij FINRA vraagt deze module
EERST het echte schema op (via Socrata's eigen, generieke metadata-
endpoint) en zoekt daarin zelf naar de juiste velden op basis van
sleutelwoorden, in plaats van blind op hardgecodeerde veldnamen te
vertrouwen. Test 'm expliciet en meld het als de veldherkenning misgrijpt."""

import os

import requests

USER_AGENT = "TCE Financial Analyst Agent your-email@example.com"

# "Legacy Futures Only" COT-rapportage -- dekt grondstoffen, valuta en
# indices in één dataset, met de klassieke commercial/non-commercial-split.
DATASET_ID = "6dca-aqww"
METADATA_URL = f"https://publicreporting.cftc.gov/api/views/{DATASET_ID}.json"
DATA_URL = f"https://publicreporting.cftc.gov/resource/{DATASET_ID}.json"

# Optioneel: een Socrata app-token verhoogt de rate limit, maar is niet
# vereist -- de CFTC-API is publiek en werkt ook zonder token (net als FINRA).
APP_TOKEN_ENV_VAR = "CFTC_APP_TOKEN"

# Hergebruikt dezelfde grondstofnamen als commodity_data.SUPPORTED_COMMODITIES,
# vertaald naar een zoekterm die matcht met CFTC's "market_and_exchange_names"-
# veld (bijv. "GOLD - COMMODITY EXCHANGE INC."). Contains-matching in plaats
# van een exacte naam, omdat de exacte beursnaam-suffixen niet live geverifieerd
# konden worden.
_COMMODITY_SEARCH_TERMS = {
    "WTI": "WTI",
    "BRENT": "BRENT",
    "NATURAL_GAS": "NATURAL GAS",
    "COPPER": "COPPER",
    "ALUMINUM": "ALUMINUM",
    "WHEAT": "WHEAT",
    "CORN": "CORN",
    "COTTON": "COTTON",
    "SUGAR": "SUGAR",
    "COFFEE": "COFFEE",
}

# Sleutelwoorden om de juiste velden te herkennen in het (mogelijk afwijkende)
# schema -- eerste match wint, in de gegeven volgorde.
_FIELD_KEYWORDS = {
    "market_name": ["market_and_exchange_names", "contract_market_name", "market_name"],
    "report_date": ["report_date_as_yyyy_mm_dd", "report_date"],
    "open_interest": ["open_interest_all", "openinterest_all", "open_interest"],
    "noncomm_long": ["noncomm_positions_long_all", "noncommercial_positions_long", "noncomm_long"],
    "noncomm_short": ["noncomm_positions_short_all", "noncommercial_positions_short", "noncomm_short"],
    "comm_long": ["comm_positions_long_all", "commercial_positions_long", "comm_long"],
    "comm_short": ["comm_positions_short_all", "commercial_positions_short", "comm_short"],
}


def _auth_headers() -> dict:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    app_token = os.environ.get(APP_TOKEN_ENV_VAR)
    if app_token:
        headers["X-App-Token"] = app_token
    return headers


def _discover_field_names() -> dict | None:
    """Vraagt het ECHTE schema op bij Socrata (CFTC's dataplatform) en zoekt
    de velden die we nodig hebben op basis van sleutelwoorden. Geeft None
    terug als dit niet lukt (netwerkfout, of niet alle velden herkend) --
    dan weten we zeker dat we niet per ongeluk het verkeerde veld gebruiken."""
    try:
        resp = requests.get(METADATA_URL, headers=_auth_headers(), timeout=15)
        resp.raise_for_status()
        columns = resp.json().get("columns", [])
        available_fields = [c["fieldName"] for c in columns if "fieldName" in c]
    except Exception:
        return None

    discovered = {}
    for our_name, keywords in _FIELD_KEYWORDS.items():
        for keyword in keywords:
            match = next((f for f in available_fields if f.lower() == keyword), None)
            if match:
                discovered[our_name] = match
                break
    if len(discovered) != len(_FIELD_KEYWORDS):
        return None  # niet alle velden gevonden -- niet gokken, gewoon niets teruggeven
    return discovered


def fetch_cftc_positioning(commodity: str) -> dict:
    """Haalt de meest recente speculatieve futures-positionering op voor een
    grondstof (moet één van commodity_data.SUPPORTED_COMMODITIES zijn).

    Geeft terug: {"report_date": ..., "open_interest": ..., "noncommercial_long": ...,
    "noncommercial_short": ..., "net_noncommercial_position": ...,
    "net_position_pct_of_open_interest": ...} of {"error": ...} als de grondstof
    onbekend is, het schema niet herkend kon worden, of er geen data was.
    CFTC publiceert dit wekelijks (elke vrijdag, met data t/m de voorgaande
    dinsdag) -- dit is dus altijd enkele dagen oud, nooit real-time."""
    commodity = commodity.upper().strip()
    search_term = _COMMODITY_SEARCH_TERMS.get(commodity)
    if search_term is None:
        return {"error": f"onbekende grondstof '{commodity}', ondersteund: {sorted(_COMMODITY_SEARCH_TERMS)}"}

    fields = _discover_field_names()
    if fields is None:
        return {"error": "kon het CFTC-schema niet herkennen (mogelijk veranderd sinds deze code werd geschreven) -- positioneringsdata niet beschikbaar"}

    try:
        resp = requests.get(
            DATA_URL,
            headers=_auth_headers(),
            params={
                "$where": f"upper({fields['market_name']}) like '%{search_term}%'",
                "$order": f"{fields['report_date']} DESC",
                "$limit": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        return {"error": f"kon geen CFTC-positioneringsdata ophalen voor {commodity}: {e}"}

    if not rows:
        return {"error": f"geen CFTC-positioneringsdata gevonden voor {commodity}"}

    row = rows[0]
    try:
        open_interest = float(row[fields["open_interest"]])
        noncomm_long = float(row[fields["noncomm_long"]])
        noncomm_short = float(row[fields["noncomm_short"]])
        comm_long = float(row[fields["comm_long"]])
        comm_short = float(row[fields["comm_short"]])
    except (KeyError, TypeError, ValueError):
        return {"error": f"onverwachte vorm van de CFTC-data voor {commodity} -- kon de cijfers niet uitlezen"}

    net_noncommercial = noncomm_long - noncomm_short
    net_pct_of_oi = round(net_noncommercial / open_interest * 100, 2) if open_interest else None

    return {
        "commodity": commodity,
        "market_name": row.get(fields["market_name"]),
        "report_date": row.get(fields["report_date"]),
        "open_interest": open_interest,
        "noncommercial_long": noncomm_long,
        "noncommercial_short": noncomm_short,
        "commercial_long": comm_long,
        "commercial_short": comm_short,
        "net_noncommercial_position": net_noncommercial,
        "net_position_pct_of_open_interest": net_pct_of_oi,
    }


if __name__ == "__main__":
    import json
    import sys
    test_commodity = sys.argv[1] if len(sys.argv) > 1 else "WTI"
    print(json.dumps(fetch_cftc_positioning(test_commodity), indent=2, default=str))
