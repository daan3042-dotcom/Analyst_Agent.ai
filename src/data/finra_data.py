"""
finra_data.py
Haalt short interest-data op bij FINRA (de Amerikaanse toezichthouder voor
broker-dealers) -- hoeveel aandelen er "short" staan, en of dat aantal
stijgt of daalt. Een klassiek marktsentiment-signaal, los van alles wat de
rest van de agent al berekent.

BELANGRIJK, EERLIJKHEID OVER ONZEKERHEID: FINRA's publieke dataset-naam
voor dit soort data is in het verleden veranderd (het oude "equityShortInterest"
dataset is per 30 april 2021 vervangen door "equityShortInterestStandardized",
met een aangepaste veldstructuur). Ik kon dit NIET live testen (deze
ontwikkelomgeving heeft geen netwerktoegang tot FINRA), dus de EXACTE
huidige veldnamen zijn onzeker. Om dat op te vangen vraagt deze module EERST
het echte schema op via FINRA's eigen metadata-endpoint (goed gedocumenteerd
en stabiel), en zoekt daarin zelf naar de juiste velden op basis van
sleutelwoorden -- in plaats van te gokken op hardgecodeerde veldnamen die
inmiddels veranderd kunnen zijn. Dit is dus de MINST zekere van alle
databronnen in dit project; test 'm expliciet en meld het als de
veldherkenning misgrijpt."""

import requests

USER_AGENT = "TCE Financial Analyst Agent bufkes101221@gmail.com"
METADATA_URL = "https://api.finra.org/metadata/group/otcMarket/name/equityShortInterestStandardized"
DATA_URL = "https://api.finra.org/data/group/otcMarket/name/equityShortInterestStandardized"

# Sleutelwoorden om de juiste velden te herkennen in het (mogelijk
# veranderde) schema -- eerste match wint, in de gegeven volgorde.
_FIELD_KEYWORDS = {
    "symbol": ["symbolcode", "issuesymbolidentifier", "symbol"],
    "settlement_date": ["settlementdate"],
    "current_short": ["currentshortpositionquantity", "currentshortsharenumber", "currentshortposition", "shortposition"],
    "avg_daily_volume": ["averagedailyvolumequantity", "averageshortsharenumber", "averagedailyvolume"],
    "days_to_cover": ["daystocoverquantity", "daystocovernumber", "daystocover"],
}


def _discover_field_names() -> dict | None:
    """Vraagt het ECHTE schema op bij FINRA en zoekt de velden die we nodig
    hebben op basis van sleutelwoorden. Geeft None terug als dit niet lukt
    (netwerkfout, of geen van de sleutelwoorden matcht iets) -- dan weten we
    zeker dat we niet per ongeluk het verkeerde veld gebruiken."""
    try:
        resp = requests.get(METADATA_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        available_fields = [f["name"] for f in resp.json().get("fields", [])]
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


def fetch_short_interest(ticker: str) -> dict:
    """Haalt de meest recente short interest op voor een ticker.

    Geeft terug: {"settlement_date": ..., "current_short_shares": ...,
    "average_daily_volume": ..., "days_to_cover": ...} of {"error": ...}
    als het schema niet herkend kon worden, de ticker niets opleverde, of
    er een netwerkprobleem was. FINRA publiceert dit maar tweewekelijks --
    dit is dus altijd enkele dagen tot twee weken oud, nooit real-time."""
    fields = _discover_field_names()
    if fields is None:
        return {"error": "kon het FINRA-schema niet herkennen (mogelijk veranderd sinds deze code werd geschreven) -- short interest niet beschikbaar"}

    try:
        resp = requests.post(
            DATA_URL,
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
            json={
                "compareFilters": [
                    {"compareType": "EQUAL", "fieldName": fields["symbol"], "fieldValue": ticker.upper()},
                ],
                "sortFields": [f"-{fields['settlement_date']}"],
                "limit": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        return {"error": f"kon geen short-interest-data ophalen bij FINRA voor {ticker}: {e}"}

    if not rows:
        return {"error": f"geen short-interest-data gevonden bij FINRA voor {ticker}"}

    row = rows[0]
    try:
        current_short = float(row[fields["current_short"]])
        avg_daily_volume = float(row[fields["avg_daily_volume"]])
        days_to_cover = float(row[fields["days_to_cover"]])
    except (KeyError, TypeError, ValueError):
        return {"error": f"onverwachte vorm van de FINRA-data voor {ticker} -- kon de cijfers niet uitlezen"}

    return {
        "settlement_date": row.get(fields["settlement_date"]),
        "current_short_shares": current_short,
        "average_daily_volume": avg_daily_volume,
        "days_to_cover": days_to_cover,
    }


if __name__ == "__main__":
    import json
    import sys
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(json.dumps(fetch_short_interest(test_ticker), indent=2, default=str))
