"""
financial_model.py
Deterministisch, meerjarig scenario-rekenmodel. Claude geeft ALLEEN de
kernaannames op (omzetgroei%, operating margin%, capex% van omzet) via de
run_financial_projection-tool -- exact dezelfde 4 velden als voorheen, dus
niets nieuws waar Claude fout in kan gaan. Al het extra realisme (D&A,
werkkapitaal, rentelasten op bestaande schuld) wordt door de CODE zelf
afgeleid uit de echte, historische SEC-cijfers, niet door Claude aangeleverd.

Dit implementeert Fase III uit "The Analyst's Method": aannames (LLM) ->
berekening (code) -> narratief (LLM) -- nu met een echte koppeling tussen
resultatenrekening, balans en kasstroom, in plaats van de eerdere
vereenvoudigde EBIT-na-belasting-min-capex-benadering.

Vereenvoudigingen die BEWUST gemaakt zijn (transparant, geen verborgen
aannames):
- Bestaande schuld blijft constant over de projectieperiode (geen aflossing
  of nieuwe uitgifte gemodelleerd) -- een volledig aflossingsschema is per
  bedrijf te verschillend om generiek te modelleren.
- D&A, werkkapitaalintensiteit en de rentevoet worden afgeleid uit de
  MEEST RECENTE twee jaar historische data. Bij weinig historische spreiding
  kan dit minder representatief zijn voor een sterk veranderend bedrijf.
"""

import random

DEFAULT_TAX_RATE = 0.21  # Amerikaans vennootschapsbelastingtarief, zelfde aanname als reverse_dcf.py
DEFAULT_INTEREST_RATE = 0.05  # terugvalaanname als er geen bruikbare historische rentelast is
MAX_PROJECTION_YEARS = 10


def _latest_two(series):
    if not series or len(series) < 2:
        return None
    return series[-2], series[-1]


def _value_for_year(series, fiscal_year):
    if not series:
        return None
    match = next((v for v in series if v["fiscal_year"] == fiscal_year), None)
    return match["value"] if match else None


def _get_base_revenue(context: dict) -> float | None:
    """Haalt de meest recente, geverifieerde omzet op uit SEC-data (de
    primaire bron). Geeft None terug als die data niet beschikbaar is
    (bijv. bij een niet-Amerikaanse notering) -- geen gok, een duidelijk
    'kan niet' in plaats daarvan."""
    from forensics import _pick_revenue_series

    sec_result = context.get("sec_result") or {}
    facts = sec_result.get("annual_facts", {})
    revenue_series = _pick_revenue_series(facts)
    if revenue_series:
        return revenue_series[-1]["value"]
    return None


def _derive_model_inputs(context: dict, base_revenue: float) -> dict:
    """Leidt de extra, realistischere modelinputs af uit de historische
    SEC-cijfers -- D&A als % van omzet, werkkapitaalintensiteit (hoeveel
    extra werkkapitaal een dollar omzetgroei historisch opslokte), bestaande
    schuld, en de impliciete rentevoet daarop. Elke afgeleide waarde heeft
    een expliciete terugvalwaarde als de brondata ontbreekt -- nooit een
    crash, wel een transparante aanname."""
    from forensics import _pick_revenue_series

    sec_result = context.get("sec_result") or {}
    facts = sec_result.get("annual_facts", {})
    revenue_series = _pick_revenue_series(facts) or []

    # D&A als percentage van omzet, uit het meest recente jaar.
    da_series = facts.get("DepreciationDepletionAndAmortization") or facts.get("DepreciationAmortizationAndAccretionNet")
    da_pct = 0.0
    if da_series and revenue_series:
        latest_da = da_series[-1]
        rev_same_year = _value_for_year(revenue_series, latest_da["fiscal_year"])
        if rev_same_year:
            da_pct = latest_da["value"] / rev_same_year

    # Werkkapitaalintensiteit: (verandering in AR + voorraad) / verandering in
    # omzet, over de laatste twee beschikbare jaren. Begrensd tussen -0.5 en
    # 0.5 zodat één ongebruikelijk jaar de hele projectie niet laat ontsporen.
    wc_intensity = 0.0
    ar_pair = _latest_two(facts.get("AccountsReceivableNetCurrent"))
    inv_pair = _latest_two(facts.get("InventoryNet"))
    rev_pair = _latest_two(revenue_series)
    if rev_pair and (ar_pair or inv_pair):
        revenue_change = rev_pair[1]["value"] - rev_pair[0]["value"]
        if revenue_change:
            wc_change = 0.0
            if ar_pair:
                wc_change += ar_pair[1]["value"] - ar_pair[0]["value"]
            if inv_pair:
                wc_change += inv_pair[1]["value"] - inv_pair[0]["value"]
            wc_intensity = max(-0.5, min(0.5, wc_change / revenue_change))

    # Bestaande schuld en de impliciete rentevoet daarop.
    lt_debt = facts.get("LongTermDebtNoncurrent")
    st_debt = facts.get("LongTermDebtCurrent")
    total_debt = 0.0
    if lt_debt:
        total_debt += lt_debt[-1]["value"]
    if st_debt:
        total_debt += st_debt[-1]["value"]

    interest_rate = DEFAULT_INTEREST_RATE
    interest_series = facts.get("InterestExpense")
    if interest_series and total_debt > 0:
        latest_interest = interest_series[-1]
        debt_same_year_lt = _value_for_year(lt_debt, latest_interest["fiscal_year"]) or 0
        debt_same_year_st = _value_for_year(st_debt, latest_interest["fiscal_year"]) or 0
        debt_same_year = debt_same_year_lt + debt_same_year_st
        if debt_same_year > 0:
            implied_rate = latest_interest["value"] / debt_same_year
            if 0 < implied_rate < 0.25:  # sanity-grens; buiten dit bereik is de tag waarschijnlijk niet vergelijkbaar
                interest_rate = implied_rate

    # Startpunt kas, voor een doorlopende kassaldo-projectie.
    cash_series = facts.get("CashAndCashEquivalentsAtCarryingValue")
    base_cash = cash_series[-1]["value"] if cash_series else 0.0

    return {
        "da_pct_of_revenue": da_pct,
        "working_capital_intensity": wc_intensity,
        "existing_debt": total_debt,
        "interest_rate": interest_rate,
        "base_cash": base_cash,
    }


SENSITIVITY_DELTAS = {
    "revenue_growth_pct": 2.0,
    "operating_margin_pct": 2.0,
    "capex_pct_of_revenue": 1.0,
}


def compute_sensitivity(tool_input: dict, context: dict) -> dict:
    """Berekent hoeveel de FCF in het laatste projectiejaar verandert als je
    ÉÉN aanname met een vaste stap ophoogt of verlaagt, terwijl de andere
    twee gelijk blijven -- dit laat zien welke aanname de uitkomst het meest
    stuurt (Fase III uit "The Analyst's Method": "welke 1-2 aannames drijven
    90% van de uitkomst"). Draait volledig in code -- 6 extra berekeningen,
    0 extra tokens, want het is gewoon zesmaal dezelfde deterministische
    functie met een net iets andere invoer."""
    base_result = project_scenario(tool_input, context)
    if "error" in base_result:
        return base_result

    base_fcf_final = base_result["projection"][-1]["fcf"]
    sensitivities = []

    for field, delta in SENSITIVITY_DELTAS.items():
        for direction, sign in (("omhoog", 1), ("omlaag", -1)):
            perturbed_input = dict(tool_input)
            perturbed_input[field] = tool_input[field] + sign * delta
            perturbed_result = project_scenario(perturbed_input, context)
            if "error" in perturbed_result:
                continue
            perturbed_fcf = perturbed_result["projection"][-1]["fcf"]
            sensitivities.append({
                "assumption": field,
                "direction": direction,
                "delta_procentpunt": sign * delta,
                "fcf_change_final_year": round(perturbed_fcf - base_fcf_final),
            })

    impact_by_field: dict[str, list[float]] = {}
    for s in sensitivities:
        impact_by_field.setdefault(s["assumption"], []).append(abs(s["fcf_change_final_year"]))
    ranking = sorted(impact_by_field.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))

    return {
        "base_case_final_year_fcf": base_fcf_final,
        "sensitivities": sensitivities,
        "ranking_most_to_least_impactful": [field for field, _ in ranking],
    }


def project_scenario(tool_input: dict, context: dict) -> dict:
    """Rekent één scenario door: neemt de aannames van Claude (groei, marge,
    capex%) plus de uit historische SEC-data afgeleide modelinputs, en
    berekent jaar-voor-jaar een echt gekoppelde resultatenrekening,
    kasstroom en lopend kassaldo -- niet langer alleen EBIT-na-belasting-
    min-capex, maar met D&A-terugtelling, rentelasten op bestaande schuld,
    en een werkkapitaalcorrectie op basis van historisch gedrag."""
    base_revenue = _get_base_revenue(context)
    if base_revenue is None:
        return {"error": "geen geverifieerde startomzet beschikbaar (SEC-data ontbreekt voor dit bedrijf) -- projectie niet mogelijk"}

    try:
        growth = float(tool_input["revenue_growth_pct"]) / 100
        margin = float(tool_input["operating_margin_pct"]) / 100
        capex_pct = float(tool_input["capex_pct_of_revenue"]) / 100
        years = int(tool_input.get("years", 5))
    except (KeyError, ValueError, TypeError):
        return {"error": "ongeldige of ontbrekende aannames (verwacht: revenue_growth_pct, "
                          "operating_margin_pct, capex_pct_of_revenue, optioneel years)"}

    years = max(1, min(years, MAX_PROJECTION_YEARS))
    model_inputs = _derive_model_inputs(context, base_revenue)

    projection = []
    revenue = base_revenue
    cash = model_inputs["base_cash"]
    debt = model_inputs["existing_debt"]

    for year in range(1, years + 1):
        previous_revenue = revenue
        revenue *= (1 + growth)
        revenue_change = revenue - previous_revenue

        ebit = revenue * margin
        da = revenue * model_inputs["da_pct_of_revenue"]
        interest = debt * model_inputs["interest_rate"]
        pretax_income = ebit - interest
        tax = max(0.0, pretax_income) * DEFAULT_TAX_RATE  # geen belastingvoordeel verondersteld bij een verlies
        net_income = pretax_income - tax

        wc_change = model_inputs["working_capital_intensity"] * revenue_change
        capex = revenue * capex_pct
        fcf = net_income + da - wc_change - capex
        cash += fcf

        projection.append({
            "year": year,
            "revenue": round(revenue),
            "ebit": round(ebit),
            "da": round(da),
            "interest_expense": round(interest),
            "net_income": round(net_income),
            "working_capital_change": round(wc_change),
            "capex": round(capex),
            "fcf": round(fcf),
            "cumulative_cash": round(cash),
        })

    result = {
        "scenario_name": tool_input.get("scenario_name", "onbenoemd"),
        "base_revenue": round(base_revenue),
        "assumptions": {
            "revenue_growth_pct": tool_input["revenue_growth_pct"],
            "operating_margin_pct": tool_input["operating_margin_pct"],
            "capex_pct_of_revenue": tool_input["capex_pct_of_revenue"],
        },
        "model_inputs_derived_from_sec_history": {
            "da_pct_of_revenue": round(model_inputs["da_pct_of_revenue"], 4),
            "working_capital_intensity": round(model_inputs["working_capital_intensity"], 4),
            "existing_debt": round(model_inputs["existing_debt"]),
            "interest_rate": round(model_inputs["interest_rate"], 4),
        },
        "tax_rate_assumption": DEFAULT_TAX_RATE,
        "projection": projection,
    }

    # Reconciliatie met de reverse-DCF, ALLEEN voor het base case: twee
    # onafhankelijke bronnen voor "welke groei is realistisch" (wat de markt
    # inprijst vs. wat Claude zelf aanneemt) -- een groot verschil is op
    # zichzelf een analytisch interessant, automatisch te detecteren signaal.
    scenario_name = str(tool_input.get("scenario_name", "")).strip().lower()
    reverse_dcf = context.get("reverse_dcf_result")
    if scenario_name == "base" and reverse_dcf and "error" not in reverse_dcf:
        market_growth = reverse_dcf["implied_annual_fcf_growth"]
        assumed_growth = growth
        gap = assumed_growth - market_growth
        if abs(gap) > 0.03:  # meer dan 3 procentpunt verschil is de moeite waard om te benoemen
            richting = "optimistischer" if gap > 0 else "voorzichtiger"
            result["market_comparison"] = (
                f"De markt prijst momenteel een jaarlijkse FCF-groei van {market_growth*100:.1f}% in "
                f"(via reverse-DCF), terwijl dit base case uitgaat van {assumed_growth*100:.1f}% -- "
                f"dit base case is dus {richting} dan wat de huidige koers impliceert. Vermeld dit "
                f"verschil expliciet in sectie 15, puur beschrijvend (geen oordeel of de markt of het "
                f"base case 'gelijk heeft')."
            )
        else:
            result["market_comparison"] = (
                f"De markt prijst momenteel een jaarlijkse FCF-groei van {market_growth*100:.1f}% in "
                f"(via reverse-DCF), wat dicht bij de aanname van dit base case ({assumed_growth*100:.1f}%) ligt."
            )

    # Kans-gewogen verwachte FCF: als Claude voor DIT scenario een kans
    # opgaf (probability_pct), houden we 'm bij in een teller die over de
    # drie scenario-aanroepen heen blijft bestaan (context["scenario_accumulator"]
    # is bewust hetzelfde dict-object bij elke aanroep binnen één analyse).
    # Zodra alle drie (bear/base/bull) een kans hebben, berekent de CODE de
    # kans-gewogen verwachte FCF -- geen LLM-rekenwerk.
    probability = tool_input.get("probability_pct")
    if probability is not None and isinstance(context, dict):
        accumulator = context.setdefault("scenario_accumulator", {})
        try:
            accumulator[scenario_name] = {
                "probability": float(probability) / 100,
                "final_year_fcf": projection[-1]["fcf"],
            }
        except (ValueError, TypeError):
            pass
        else:
            required = {"bear", "base", "bull"}
            if required.issubset(accumulator.keys()):
                total_prob = sum(v["probability"] for v in accumulator.values())
                if abs(total_prob - 1.0) < 0.05:
                    weighted_fcf = sum(v["probability"] * v["final_year_fcf"] for v in accumulator.values())
                    result["probability_weighted_expected_fcf"] = {
                        "value": round(weighted_fcf),
                        "probabilities_used": {k: round(v["probability"], 3) for k, v in accumulator.items()},
                        "note": (
                            "Kans-gewogen verwachte FCF (laatste projectiejaar) over de 3 "
                            "scenario's, berekend in code op basis van jouw opgegeven kansen. "
                            "Vermeld dit als aanvullend, puur beschrijvend datapunt in sectie 15."
                        ),
                    }
                else:
                    result["probability_weighting_warning"] = (
                        f"De drie opgegeven kansen tellen op tot {total_prob*100:.0f}%, niet 100% -- "
                        f"kans-gewogen FCF is daarom niet berekend."
                    )

    return result


def run_monte_carlo_simulation(tool_input: dict, context: dict) -> dict:
    """Monte Carlo-simulatie: i.p.v. drie losse scenario's (bear/base/bull)
    trekt dit duizenden keren een WILLEKEURIGE waarde voor omzetgroei, marge,
    en capex% uit een driehoeksverdeling (bear = minimum, base = meest
    waarschijnlijke waarde, bull = maximum), en berekent voor elke trekking
    de Jaar-N vrije kasstroom met dezelfde deterministische projectielogica
    als project_scenario. Het resultaat is een volledige verdeling van
    mogelijke uitkomsten (mediaan, spreiding, percentielen) in plaats van
    drie losse punten -- en kost GEEN extra API-tokens, puur Python-
    rekenwerk met de al aangeleverde aannames."""
    base_revenue = _get_base_revenue(context)
    if base_revenue is None:
        return {"error": "geen geverifieerde startomzet beschikbaar (SEC-data ontbreekt voor dit bedrijf) -- simulatie niet mogelijk"}

    try:
        bear, base, bull = tool_input["bear"], tool_input["base"], tool_input["bull"]
        for scenario in (bear, base, bull):
            float(scenario["revenue_growth_pct"])
            float(scenario["operating_margin_pct"])
            float(scenario["capex_pct_of_revenue"])
        years = int(tool_input.get("years", 5))
        n_simulations = int(tool_input.get("n_simulations", 5000))
    except (KeyError, ValueError, TypeError):
        return {"error": "ongeldige of ontbrekende invoer -- verwacht bear/base/bull, elk met "
                          "revenue_growth_pct, operating_margin_pct, capex_pct_of_revenue"}

    years = max(1, min(years, MAX_PROJECTION_YEARS))
    n_simulations = max(100, min(n_simulations, 20000))
    model_inputs = _derive_model_inputs(context, base_revenue)

    def sample_triangular(bear_val, base_val, bull_val):
        # random.triangular verwacht low <= mode <= high; bear/bull kunnen in
        # willekeurige volgorde staan, dus sorteren we defensief en klemmen
        # we de meest-waarschijnlijke waarde (mode) binnen dat bereik.
        lo, hi = sorted([float(bear_val), float(bull_val)])
        mode = min(max(float(base_val), lo), hi)
        if lo == hi:
            return lo
        return random.triangular(lo, hi, mode)

    final_year_fcfs = []
    for _ in range(n_simulations):
        growth = sample_triangular(bear["revenue_growth_pct"], base["revenue_growth_pct"], bull["revenue_growth_pct"]) / 100
        margin = sample_triangular(bear["operating_margin_pct"], base["operating_margin_pct"], bull["operating_margin_pct"]) / 100
        capex_pct = sample_triangular(bear["capex_pct_of_revenue"], base["capex_pct_of_revenue"], bull["capex_pct_of_revenue"]) / 100

        revenue = base_revenue
        debt = model_inputs["existing_debt"]
        fcf = 0.0
        for _year in range(1, years + 1):
            previous_revenue = revenue
            revenue *= (1 + growth)
            revenue_change = revenue - previous_revenue
            ebit = revenue * margin
            da = revenue * model_inputs["da_pct_of_revenue"]
            interest = debt * model_inputs["interest_rate"]
            pretax_income = ebit - interest
            tax = max(0.0, pretax_income) * DEFAULT_TAX_RATE
            net_income = pretax_income - tax
            wc_change = model_inputs["working_capital_intensity"] * revenue_change
            capex = revenue * capex_pct
            fcf = net_income + da - wc_change - capex
        final_year_fcfs.append(fcf)

    final_year_fcfs.sort()
    n = len(final_year_fcfs)

    def percentile(p):
        idx = min(n - 1, max(0, int(p / 100 * n)))
        return round(final_year_fcfs[idx])

    mean_fcf = sum(final_year_fcfs) / n
    variance = sum((x - mean_fcf) ** 2 for x in final_year_fcfs) / n

    # Histogram van de RUWE simulatie-uitkomsten -- dit is wat een echte
    # bell-curve-vormige grafiek mogelijk maakt (een dichtheidsverdeling),
    # in plaats van alleen een platte p10-p90-balk.
    n_bins = 24
    bin_min, bin_max = final_year_fcfs[0], final_year_fcfs[-1]
    bin_width = (bin_max - bin_min) / n_bins if bin_max > bin_min else 1
    bin_counts = [0] * n_bins
    for value in final_year_fcfs:
        bin_index = min(n_bins - 1, int((value - bin_min) / bin_width))
        bin_counts[bin_index] += 1
    bin_centers = [round(bin_min + (i + 0.5) * bin_width) for i in range(n_bins)]

    return {
        "n_simulations": n_simulations,
        "years": years,
        "target_metric": f"Jaar-{years} vrije kasstroom",
        "mean": round(mean_fcf),
        "median": percentile(50),
        "std_dev": round(variance ** 0.5),
        "p10": percentile(10),
        "p25": percentile(25),
        "p75": percentile(75),
        "p90": percentile(90),
        "min": round(final_year_fcfs[0]),
        "max": round(final_year_fcfs[-1]),
        "histogram_bin_centers": bin_centers,
        "histogram_counts": bin_counts,
    }
