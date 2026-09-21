"""
altman_z.py
De Altman Z-Score (Edward Altman, 1968): een gevestigde, formule-gebaseerde
faillissementsrisico-indicator, samengesteld uit vijf balansratio's. Dit is
GEEN eigen inschatting van Claude of van ons -- het is een decennia oude,
empirisch gevalideerde formule uit de financiële vakliteratuur, hier simpelweg
correct uitgerekend in code (precies het soort "regel-gebaseerde
professionele heuristiek" waar The Analyst's Method om vroeg).

Z = 1.2×X1 + 1.4×X2 + 3.3×X3 + 0.6×X4 + 1.0×X5
X1 = werkkapitaal / totale activa
X2 = ingehouden winst / totale activa
X3 = EBIT / totale activa
X4 = marktwaarde eigen vermogen / totale verplichtingen
X5 = omzet / totale activa

Klassieke drempelwaarden (voor industriële/commerciële bedrijven):
Z > 2.99   -> "veilige zone"
1.81-2.99  -> "grijze zone"
Z < 1.81   -> "risicozone"

Let op: dit is oorspronkelijk gevalideerd voor industriële bedrijven: bij
financiële instellingen, banken en sommige dienstensectoren is de formule
minder betekenisvol (activa/omzet-verhoudingen werken daar anders). Dat
nemen we mee als disclaimer, niet als reden om de score te verbergen.
"""

from forensics import _pick_revenue_series


def _latest(series):
    return series[-1] if series else None


def compute_altman_z(sec_result: dict, company_data: dict) -> dict:
    """Berekent de Altman Z-Score uit de meest recente SEC-jaarcijfers.
    Geeft een duidelijke foutmelding terug (nooit een gok) als een van de
    benodigde balansposten ontbreekt -- niet elk bedrijf rapporteert exact
    dezelfde XBRL-tags."""
    if not sec_result or "error" in sec_result:
        return {"error": "geen SEC-data beschikbaar -- Altman Z-Score vereist Amerikaanse jaarcijfers"}

    facts = sec_result.get("annual_facts", {})
    total_assets = _latest(facts.get("Assets"))
    total_liabilities = _latest(facts.get("Liabilities"))
    current_assets = _latest(facts.get("AssetsCurrent"))
    current_liabilities = _latest(facts.get("LiabilitiesCurrent"))
    retained_earnings = _latest(facts.get("RetainedEarningsAccumulatedDeficit"))
    ebit = _latest(facts.get("OperatingIncomeLoss"))
    revenue = _latest(_pick_revenue_series(facts))
    market_cap = company_data.get("market_cap")

    required = {
        "totale activa": total_assets, "totale verplichtingen": total_liabilities,
        "vlottende activa": current_assets, "kortlopende verplichtingen": current_liabilities,
        "ingehouden winst": retained_earnings, "EBIT": ebit, "omzet": revenue,
        "marktkapitalisatie": market_cap,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return {"error": f"onvoldoende data voor Altman Z-Score, ontbreekt: {', '.join(missing)}"}

    ta = total_assets["value"]
    if ta == 0:
        return {"error": "totale activa is 0 -- kan Altman Z-Score niet zinvol berekenen"}

    working_capital = current_assets["value"] - current_liabilities["value"]
    x1 = working_capital / ta
    x2 = retained_earnings["value"] / ta
    x3 = ebit["value"] / ta
    x4 = market_cap / total_liabilities["value"] if total_liabilities["value"] else 0
    x5 = revenue["value"] / ta

    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5

    if z > 2.99:
        zone = "veilige zone"
    elif z > 1.81:
        zone = "grijze zone"
    else:
        zone = "risicozone"

    return {
        "fiscal_year": total_assets["fiscal_year"],
        "z_score": round(z, 2),
        "zone": zone,
        "components": {
            "werkkapitaal_ratio": round(x1, 4),
            "ingehouden_winst_ratio": round(x2, 4),
            "ebit_ratio": round(x3, 4),
            "eigen_vermogen_vs_schuld": round(x4, 4),
            "omzet_ratio": round(x5, 4),
        },
    }
