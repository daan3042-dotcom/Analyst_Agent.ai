"""
peer_analysis.py
Berekent, in code, hoeveel de kern-ratio's van het bedrijf afwijken van het
gemiddelde van de opgegeven concurrenten -- i.p.v. dat Claude dit zelf in
lopende tekst uitrekent. Gebruikt de PEERS die je al meegeeft via --peers
(peer_data uit data_fetch.fetch_peer_data); dit bouwt geen automatische
peer-selectie (daarvoor bestaat geen betrouwbare gratis API -- yfinance
heeft geen "vind vergelijkbare bedrijven"-eindpunt), maar maakt de
vergelijking tussen de al-gegeven peers wel objectief en consistent.
"""

# De ratio's waarvoor we een premium/discount berekenen -- allemaal al
# aanwezig in zowel company_data (yfinance) als peer_data.
COMPARABLE_RATIOS = {
    "trailing_pe": "P/E (trailing)",
    "ev_to_ebitda": "EV/EBITDA",
    "profit_margins": "Netto marge",
    "return_on_equity": "ROE",
}


def compute_peer_comparison(company_data: dict, peer_data: dict | None) -> dict:
    """Geeft per vergelijkbare ratio het peer-gemiddelde en het premium/
    discount van het bedrijf t.o.v. dat gemiddelde terug. Peers zonder
    bruikbare waarde voor een ratio worden voor DIE ratio overgeslagen
    (niet de hele peer), zodat één ontbrekend cijfer niet de rest verpest."""
    if not peer_data:
        return {"error": "geen peer-data meegegeven (gebruik --peers bij het draaien van de agent)"}

    valid_peers = {name: data for name, data in peer_data.items() if isinstance(data, dict) and "error" not in data}
    if not valid_peers:
        return {"error": "geen enkele peer had bruikbare data"}

    comparison = {}
    for field, label in COMPARABLE_RATIOS.items():
        company_value = company_data.get(field)
        peer_values = [d[field] for d in valid_peers.values() if d.get(field) is not None]
        if company_value is None or not peer_values:
            continue

        peer_average = sum(peer_values) / len(peer_values)
        if peer_average == 0:
            continue

        premium_pct = (company_value - peer_average) / abs(peer_average)
        comparison[field] = {
            "label": label,
            "company_value": round(company_value, 4),
            "peer_average": round(peer_average, 4),
            "peer_count": len(peer_values),
            "premium_discount_pct": round(premium_pct, 4),  # positief = premium, negatief = discount
        }

    if not comparison:
        return {"error": "geen enkele ratio kon vergeleken worden (ontbrekende data bij bedrijf of peers)"}

    return comparison
