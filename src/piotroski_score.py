"""
piotroski_score.py
De Piotroski F-Score (Joseph Piotroski, 2000): een gevestigde, formule-
gebaseerde checklist van 9 ja/nee-criteria die de fundamentele financiële
gezondheid van een bedrijf meet -- net als de Altman Z-Score GEEN eigen
inschatting, maar een decennia oude, empirisch gevalideerde methode uit de
financiële vakliteratuur, hier simpelweg correct uitgerekend in code.

Score = som van 9 punten (0-9), elk 0 of 1:

Winstgevendheid (4 punten):
1. Positieve netto winst (ROA > 0)
2. Positieve operationele kasstroom
3. ROA hoger dan vorig jaar
4. Operationele kasstroom hoger dan netto winst (kwaliteit van de winst --
   winst die niet door kasstroom wordt gedekt is een waarschuwingssignaal)

Hefboom/liquiditeit (3 punten):
5. Langlopende schuld (t.o.v. activa) lager dan vorig jaar
6. Current ratio hoger dan vorig jaar
7. Geen nieuwe aandelen uitgegeven (geen verwatering)

Operationele efficiëntie (2 punten):
8. Brutomarge hoger dan vorig jaar
9. Omzet/activa (asset turnover) hoger dan vorig jaar

Klassieke interpretatie: 8-9 = sterk fundamenteel profiel, 0-2 = zwak.
Net als bij Altman Z: oorspronkelijk gevalideerd op industriële/waarde-
aandelen, minder betekenisvol bij financiële instellingen.
"""

from forensics import _latest_two, _pick_revenue_series


def compute_piotroski_score(sec_result: dict) -> dict:
    """Berekent de Piotroski F-Score uit twee opeenvolgende jaren SEC-data.
    Geeft een duidelijke foutmelding terug (nooit een gok) als een van de
    benodigde reeksen te kort is of ontbreekt."""
    if not sec_result or "error" in sec_result:
        return {"error": "geen SEC-data beschikbaar -- Piotroski F-Score vereist Amerikaanse jaarcijfers"}

    facts = sec_result.get("annual_facts", {})
    revenue = _pick_revenue_series(facts)

    required_series = {
        "netto winst": facts.get("NetIncomeLoss"),
        "totale activa": facts.get("Assets"),
        "operationele kasstroom": facts.get("NetCashProvidedByUsedInOperatingActivities"),
        "langlopende schuld": facts.get("LongTermDebtNoncurrent"),
        "vlottende activa": facts.get("AssetsCurrent"),
        "kortlopende verplichtingen": facts.get("LiabilitiesCurrent"),
        "aandelental": facts.get("WeightedAverageNumberOfSharesOutstandingBasic"),
        "brutowinst": facts.get("GrossProfit"),
        "omzet": revenue,
    }
    missing = [name for name, series in required_series.items() if not series or len(series) < 2]
    if missing:
        return {"error": f"onvoldoende data (twee opeenvolgende jaren nodig) voor Piotroski F-Score, ontbreekt: {', '.join(missing)}"}

    net_income = _latest_two(required_series["netto winst"])
    assets = _latest_two(required_series["totale activa"])
    ocf = _latest_two(required_series["operationele kasstroom"])
    lt_debt = _latest_two(required_series["langlopende schuld"])
    current_assets = _latest_two(required_series["vlottende activa"])
    current_liabilities = _latest_two(required_series["kortlopende verplichtingen"])
    shares = _latest_two(required_series["aandelental"])
    gross_profit = _latest_two(required_series["brutowinst"])
    rev = _latest_two(required_series["omzet"])

    if any(pair is None for pair in [net_income, assets, ocf, lt_debt, current_assets,
                                       current_liabilities, shares, gross_profit, rev]):
        return {"error": "onvoldoende overlappende jaren tussen de benodigde reeksen voor Piotroski F-Score"}

    prior_assets, latest_assets = assets
    if prior_assets["value"] == 0 or latest_assets["value"] == 0:
        return {"error": "totale activa is 0 in een van beide jaren -- kan Piotroski F-Score niet zinvol berekenen"}

    roa_prior = net_income[0]["value"] / prior_assets["value"]
    roa_latest = net_income[1]["value"] / latest_assets["value"]

    criteria = {
        "positieve_netto_winst": net_income[1]["value"] > 0,
        "positieve_operationele_kasstroom": ocf[1]["value"] > 0,
        "roa_hoger_dan_vorig_jaar": roa_latest > roa_prior,
        "kasstroom_hoger_dan_winst": ocf[1]["value"] > net_income[1]["value"],
        "schuldratio_lager_dan_vorig_jaar": (lt_debt[1]["value"] / latest_assets["value"]) < (lt_debt[0]["value"] / prior_assets["value"]),
        "current_ratio_hoger_dan_vorig_jaar": (current_assets[1]["value"] / current_liabilities[1]["value"]) > (current_assets[0]["value"] / current_liabilities[0]["value"]) if current_liabilities[0]["value"] and current_liabilities[1]["value"] else False,
        "geen_nieuwe_aandelen_uitgegeven": shares[1]["value"] <= shares[0]["value"],
        "brutomarge_hoger_dan_vorig_jaar": (gross_profit[1]["value"] / rev[1]["value"]) > (gross_profit[0]["value"] / rev[0]["value"]) if rev[0]["value"] and rev[1]["value"] else False,
        "omzet_activa_ratio_hoger_dan_vorig_jaar": (rev[1]["value"] / latest_assets["value"]) > (rev[0]["value"] / prior_assets["value"]),
    }

    score = sum(1 for passed in criteria.values() if passed)

    if score >= 8:
        interpretation = "sterk fundamenteel profiel"
    elif score >= 5:
        interpretation = "gemiddeld fundamenteel profiel"
    else:
        interpretation = "zwak fundamenteel profiel"

    return {
        "fiscal_year": latest_assets["fiscal_year"],
        "score": score,
        "max_score": 9,
        "interpretation": interpretation,
        "criteria": criteria,
    }
