"""
reverse_dcf.py
Reverse-DCF: in plaats van "wat is dit bedrijf waard" te vragen, draaien we
de vraag om -- "welke groei prijst de markt al in, gegeven de huidige
koers?" Dit is een deterministische berekening (geen LLM), precies het
soort rekenwerk dat volgens "The Analyst's Method" uit code moet komen,
niet uit een taalmodel.

BELANGRIJK, en dit is geen bijzaak: dit is GEEN koersdoel en GEEN advies.
Het is een omgekeerde rekensom -- "gegeven de huidige prijs, wat moet waar
zijn" -- geen voorspelling van wat er gaat gebeuren. Zie het als een
plausibiliteitscheck, niet als een mening.

Werkwijze: een twee-fase DCF. Fase 1 (PROJECTION_YEARS): vrije kasstroom
groeit met de OP TE LOSSEN groeivoet g. Fase 2 (eeuwigdurend, na jaar N):
groei valt terug op een vaste, behoudende terminal-groeivoet. We zoeken de
waarde van g waarbij de contante waarde van alle toekomstige kasstromen
precies gelijk is aan de huidige ondernemingswaarde (enterprise value).
"""

# Vaste, transparante aannames -- allemaal hier bovenaan, makkelijk aan te
# passen als je een andere macro-inschatting hebt. Dit zijn bewust brede,
# generieke marktaannames, geen bedrijfsspecifieke schattingen.
RISK_FREE_RATE = 0.045        # circa de 10-jaars Amerikaanse staatsobligatierente
EQUITY_RISK_PREMIUM = 0.05    # historisch gangbare aandelenrisicopremie
DEBT_SPREAD = 0.015           # opslag boven de risicovrije rente voor bedrijfsschuld
CORPORATE_TAX_RATE = 0.21     # Amerikaans vennootschapsbelastingtarief
TERMINAL_GROWTH = 0.025       # lange-termijn eeuwigdurende groeivoet (~verwachte nominale bbp-groei)
PROJECTION_YEARS = 10


def estimate_wacc(company_data: dict) -> float | None:
    """Schat de WACC via een eenvoudig CAPM-model voor de kosten van eigen
    vermogen, gewogen met de kosten van vreemd vermogen. Geeft None terug
    als de benodigde data (beta, marktkapitalisatie) ontbreekt -- beter een
    duidelijk "kan niet berekenen" dan een gok."""
    beta = company_data.get("beta")
    market_cap = company_data.get("market_cap")
    total_debt = company_data.get("total_debt") or 0
    if beta is None or not market_cap:
        return None

    cost_of_equity = RISK_FREE_RATE + beta * EQUITY_RISK_PREMIUM
    cost_of_debt_after_tax = (RISK_FREE_RATE + DEBT_SPREAD) * (1 - CORPORATE_TAX_RATE)

    total_capital = market_cap + total_debt
    weight_equity = market_cap / total_capital
    weight_debt = total_debt / total_capital

    return weight_equity * cost_of_equity + weight_debt * cost_of_debt_after_tax


def _dcf_value(fcf0: float, g: float, wacc: float, years: int) -> float:
    """Totale DCF-waarde (expliciete projectieperiode + terminal value) bij
    een gegeven groeivoet g. Fase 1 groeit met g, fase 2 (eeuwigdurend) met
    de vaste TERMINAL_GROWTH."""
    if wacc <= TERMINAL_GROWTH:
        return float("inf")  # ongeldige aanname (WACC zou nooit onder de terminal-groei mogen liggen)

    pv_explicit = 0.0
    fcf = fcf0
    for year in range(1, years + 1):
        fcf *= (1 + g)
        pv_explicit += fcf / ((1 + wacc) ** year)

    terminal_fcf = fcf * (1 + TERMINAL_GROWTH)
    terminal_value = terminal_fcf / (wacc - TERMINAL_GROWTH)
    pv_terminal = terminal_value / ((1 + wacc) ** years)

    return pv_explicit + pv_terminal


def solve_implied_growth(fcf0: float, target_value: float, wacc: float,
                          years: int = PROJECTION_YEARS,
                          low: float = -0.30, high: float = 0.50,
                          tolerance: float = 1e6, max_iterations: int = 100) -> float | None:
    """Binaire zoekactie naar de groeivoet g waarbij de DCF-waarde gelijk is
    aan target_value (de huidige enterprise value). Geeft None terug als er
    geen oplossing binnen [low, high] bestaat -- d.w.z. de huidige
    waardering is met deze aannames niet plausibel via een DCF te verklaren."""
    if fcf0 <= 0:
        return None  # reverse-DCF is niet zinvol op een negatieve startwaarde

    value_at_low = _dcf_value(fcf0, low, wacc, years)
    value_at_high = _dcf_value(fcf0, high, wacc, years)
    if not (value_at_low <= target_value <= value_at_high):
        return None

    for _ in range(max_iterations):
        mid = (low + high) / 2
        value_at_mid = _dcf_value(fcf0, mid, wacc, years)
        if abs(value_at_mid - target_value) < tolerance:
            return mid
        if value_at_mid < target_value:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def compute_reverse_dcf(company_data: dict) -> dict:
    """Hoofdfunctie: geeft de door de markt impliciet ingeprijsde FCF-groei
    terug, gebaseerd op de huidige ondernemingswaarde. Puur informationeel."""
    market_cap = company_data.get("market_cap")
    total_debt = company_data.get("total_debt") or 0
    total_cash = company_data.get("total_cash") or 0
    fcf0 = company_data.get("free_cashflow")

    if not market_cap or not fcf0:
        return {"error": "onvoldoende data (marktkapitalisatie of vrije kasstroom ontbreekt)"}

    wacc = estimate_wacc(company_data)
    if wacc is None:
        return {"error": "kon WACC niet schatten (beta ontbreekt in de brondata)"}

    enterprise_value = market_cap + total_debt - total_cash

    implied_growth = solve_implied_growth(fcf0, enterprise_value, wacc)
    if implied_growth is None:
        return {
            "error": "de geïmpliceerde groei valt buiten een plausibel bereik (-30% tot +50%) -- "
                     "de huidige waardering is met deze aannames niet goed via een DCF te verklaren",
            "wacc": round(wacc, 4),
        }

    return {
        "wacc": round(wacc, 4),
        "terminal_growth_assumption": TERMINAL_GROWTH,
        "projection_years": PROJECTION_YEARS,
        "current_fcf": fcf0,
        "enterprise_value": enterprise_value,
        "implied_annual_fcf_growth": round(implied_growth, 4),
    }


def compute_intrinsic_value_estimate(company_data: dict, assumed_fcf_growth: float,
                                      years: int = PROJECTION_YEARS) -> dict:
    """Forward-DCF: het OMGEKEERDE van compute_reverse_dcf. Gegeven een
    aangenomen groeivoet (bijv. uit het base-case scenario), berekent dit
    een intrinsieke waarde per aandeel.

    BELANGRIJK, en dit is geen bijzaak: dit geeft, in tegenstelling tot de
    reverse-DCF hierboven, een concreet waarderingscijfer terug dat direct
    tegen de koers afgezet kan worden -- in de praktijk bijna een koersdoel.
    Dit mag daarom UITSLUITEND als input voor sectie 18 (Variant Perception)
    gebruikt worden, nooit voor de neutrale secties 1-17. Deze functie wordt
    bewust niet aangeroepen vanuit het standaard verified_metrics-pad."""
    market_cap = company_data.get("market_cap")
    total_debt = company_data.get("total_debt") or 0
    total_cash = company_data.get("total_cash") or 0
    fcf0 = company_data.get("free_cashflow")
    shares_outstanding = company_data.get("shares_outstanding")

    if not fcf0 or fcf0 <= 0:
        return {"error": "vrije kasstroom ontbreekt of is niet positief -- forward-DCF niet zinvol"}
    if not shares_outstanding:
        return {"error": "aandelental ontbreekt -- kan geen waarde per aandeel berekenen"}

    wacc = estimate_wacc(company_data)
    if wacc is None:
        return {"error": "kon WACC niet schatten (beta ontbreekt in de brondata)"}

    enterprise_value_estimate = _dcf_value(fcf0, assumed_fcf_growth, wacc, years)
    if enterprise_value_estimate == float("inf"):
        return {"error": "WACC ligt onder de terminal-groeivoet -- geen zinvolle DCF-waarde te berekenen"}

    equity_value_estimate = enterprise_value_estimate - total_debt + total_cash
    if equity_value_estimate <= 0:
        return {"error": "berekende eigen-vermogenswaarde is niet positief met deze aannames"}

    intrinsic_value_per_share = equity_value_estimate / shares_outstanding

    result = {
        "wacc": round(wacc, 4),
        "assumed_fcf_growth": assumed_fcf_growth,
        "terminal_growth_assumption": TERMINAL_GROWTH,
        "projection_years": years,
        "intrinsic_value_per_share": round(intrinsic_value_per_share, 2),
    }

    current_price = company_data.get("current_price")
    if current_price:
        result["current_price"] = current_price
        result["implied_premium_discount_pct"] = round(
            (current_price - intrinsic_value_per_share) / intrinsic_value_per_share, 4
        )
    return result


if __name__ == "__main__":
    # Snelle handmatige check met plausibele Alcoa-achtige cijfers.
    test_data = {
        "market_cap": 12_300_000_000, "total_debt": 3_000_000_000, "total_cash": 900_000_000,
        "free_cashflow": 433_000_000, "beta": 1.6,
    }
    print(compute_reverse_dcf(test_data))
