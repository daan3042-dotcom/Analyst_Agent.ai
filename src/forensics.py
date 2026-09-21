"""
forensics.py
Forensische, REGEL-GEBASEERDE controles op de meerjarige SEC-cijfers uit
sec_data.py. Dit implementeert de kern-architectuurfix uit "The Analyst's
Method": laat Claude niet zelf ratio's berekenen en beoordelen -- dat bleek
deze week herhaaldelijk de bron van hardnekkige fouten (de EBIT/EBITDA-
sprong bij Alcoa, wisselende aandelenaantallen bij Petrobras). In plaats
daarvan rekenen we het HIER, in code, uit, en geven we Claude alleen de
UITKOMST (de vlag) om te narreren -- geen ruwe cijfers om zelf op te
kauwen en mogelijk verkeerd over te redeneren.

Elke check geeft een dict terug: {"check", "severity", "message"}.
severity is "info" (neutrale constatering), "watch" (de moeite waard om te
benoemen), of "flag" (concreet signaal, mogelijk een data-kwaliteitskwestie).
"""


from reverse_dcf import estimate_wacc


def _latest_two(series: list[dict]) -> tuple[dict, dict] | None:
    """Geeft de laatste twee jaarwaarden terug (oud, nieuw), of None als er
    niet genoeg jaren beschikbaar zijn voor deze specifieke XBRL-tag."""
    if not series or len(series) < 2:
        return None
    return series[-2], series[-1]


def _pct_change(old, new) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (new - old) / abs(old)


def _value_for_year(series: list[dict] | None, fiscal_year: int):
    if not series:
        return None
    match = next((v for v in series if v["fiscal_year"] == fiscal_year), None)
    return match["value"] if match else None


def _pick_revenue_series(facts: dict) -> list[dict] | None:
    """De omzet-XBRL-tag verschilt per bedrijf en periode (Apple gebruikt
    sinds 2018 een andere tag dan daarvoor) -- pak wat beschikbaar is."""
    return facts.get("RevenueFromContractWithCustomerExcludingAssessedTax") or facts.get("Revenues")


def compute_verified_metrics(sec_result: dict | None, company_data: dict) -> dict:
    """Berekent een klein setje standaardratio's/cijfers ÉÉN keer, in code,
    en geeft ze als kant-en-klare, geverifieerde waarden mee aan Claude.

    Waarom dit bestaat: bij een echte testrun (Alcoa) rekende het model zelf
    een operating margin uit als 18,4%, terwijl $758M / $12.831B feitelijk
    5,9% is -- en berekende het een FCF van $961M waar $1.051M - $618M =
    $433M had moeten zijn. Dit zijn geen databron-discrepanties (waar de
    flags hierboven voor zijn), dit zijn simpele rekenfouten. Deze functie
    voorkomt dat het model die berekeningen zelf nog hoeft te doen: we geven
    de uitkomst al kant-en-klaar mee, gelabeld met de bron.
    """
    metrics = {}

    # yfinance berekent deze ratio's zelf al -- geef ze als vaste referentie
    # mee zodat het model ze niet zelf opnieuw hoeft (en kan) herberekenen.
    metrics["yfinance_operating_margin"] = company_data.get("operating_margins")
    metrics["yfinance_profit_margin"] = company_data.get("profit_margins")
    metrics["yfinance_return_on_equity"] = company_data.get("return_on_equity")
    metrics["yfinance_return_on_assets"] = company_data.get("return_on_assets")
    metrics["yfinance_free_cashflow"] = company_data.get("free_cashflow")
    metrics["yfinance_operating_cashflow"] = company_data.get("operating_cashflow")

    if not sec_result or "error" in sec_result:
        return metrics

    facts = sec_result.get("annual_facts", {})
    revenue = _pick_revenue_series(facts)
    ebit = facts.get("OperatingIncomeLoss")
    net_income = facts.get("NetIncomeLoss")
    ocf = facts.get("NetCashProvidedByUsedInOperatingActivities")
    capex = facts.get("PaymentsToAcquirePropertyPlantAndEquipment")

    if revenue and ebit:
        fy = ebit[-1]["fiscal_year"]
        rev_value = _value_for_year(revenue, fy)
        if rev_value:
            metrics["sec_operating_margin"] = {
                "fiscal_year": fy, "value": round(ebit[-1]["value"] / rev_value, 4),
            }

    if revenue and net_income:
        fy = net_income[-1]["fiscal_year"]
        rev_value = _value_for_year(revenue, fy)
        if rev_value:
            metrics["sec_net_margin"] = {
                "fiscal_year": fy, "value": round(net_income[-1]["value"] / rev_value, 4),
            }

    if ocf:
        fy = ocf[-1]["fiscal_year"]
        capex_value = _value_for_year(capex, fy) or 0
        metrics["sec_free_cashflow"] = {
            "fiscal_year": fy, "value": ocf[-1]["value"] - capex_value,
        }

    # YoY-groeipercentages -- vooraf berekend om exact het soort fout te
    # voorkomen dat we bij Delta zagen (Claude noemde "+44.8% YoY" waar de
    # cijfers zelf "42.9%" impliceerden). Voorkomen is betrouwbaarder dan
    # achteraf proberen te detecteren.
    rev_pair = _latest_two(revenue) if revenue else None
    if rev_pair:
        growth = _pct_change(rev_pair[0]["value"], rev_pair[1]["value"])
        if growth is not None:
            metrics["sec_revenue_yoy_growth"] = {"fiscal_year": rev_pair[1]["fiscal_year"], "value": round(growth, 4)}

    # Meerjarige CAGR i.p.v. alleen jaar-op-jaar -- voorkomt dat een enkel
    # uitzonderlijk jaar (goed of slecht) het hele groeiverhaal domineert.
    # Vereist minstens 3 jaar spreiding om zinvol te zijn (anders is dit
    # gewoon de YoY-groei hierboven nogmaals); gebruikt tot 5 jaar terug,
    # afhankelijk van hoeveel jaar SEC-data beschikbaar is.
    if revenue and len(revenue) >= 4:
        years_back = min(6, len(revenue)) - 1
        start, end = revenue[-1 - years_back], revenue[-1]
        n_years = end["fiscal_year"] - start["fiscal_year"]
        if n_years >= 3 and start["value"] > 0:
            cagr = (end["value"] / start["value"]) ** (1 / n_years) - 1
            metrics["sec_revenue_cagr"] = {
                "start_fiscal_year": start["fiscal_year"],
                "end_fiscal_year": end["fiscal_year"],
                "years": n_years,
                "value": round(cagr, 4),
            }

    ni_pair = _latest_two(net_income) if net_income else None
    if ni_pair:
        growth = _pct_change(ni_pair[0]["value"], ni_pair[1]["value"])
        if growth is not None:
            metrics["sec_net_income_yoy_growth"] = {"fiscal_year": ni_pair[1]["fiscal_year"], "value": round(growth, 4)}

    # EBITDA, net schuld, en de daarvan afgeleide ratio's -- ÉÉN consistente
    # berekening i.p.v. dat Claude op meerdere plekken in het rapport zelf
    # net iets andere versies uitrekent (de bron van de onopgeloste EV/EBITDA-
    # discrepanties die we bij zowel Alcoa als Delta zagen).
    da = facts.get("DepreciationDepletionAndAmortization") or facts.get("DepreciationAmortizationAndAccretionNet")
    ebitda_value, ebitda_year = None, None
    if ebit and da:
        latest_ebit = ebit[-1]
        da_same_year = _value_for_year(da, latest_ebit["fiscal_year"])
        if da_same_year is not None:
            ebitda_value = latest_ebit["value"] + da_same_year
            ebitda_year = latest_ebit["fiscal_year"]
            metrics["sec_ebitda"] = {"fiscal_year": ebitda_year, "value": round(ebitda_value)}

    lt_debt = facts.get("LongTermDebtNoncurrent")
    st_debt = facts.get("LongTermDebtCurrent")
    cash = facts.get("CashAndCashEquivalentsAtCarryingValue")
    net_debt_value, net_debt_year = None, None
    if lt_debt:
        net_debt_year = lt_debt[-1]["fiscal_year"]
        total_debt = lt_debt[-1]["value"] + (_value_for_year(st_debt, net_debt_year) or 0)
        cash_value = _value_for_year(cash, net_debt_year) or 0
        net_debt_value = total_debt - cash_value
        metrics["sec_net_debt"] = {"fiscal_year": net_debt_year, "value": round(net_debt_value)}

    if net_debt_value is not None and ebitda_value:
        metrics["sec_net_debt_to_ebitda"] = round(net_debt_value / ebitda_value, 2)

    interest = facts.get("InterestExpense")
    if ebit and interest:
        latest_ebit = ebit[-1]
        interest_same_year = _value_for_year(interest, latest_ebit["fiscal_year"])
        if interest_same_year:
            metrics["sec_interest_coverage_ratio"] = round(latest_ebit["value"] / interest_same_year, 2)

    market_cap = company_data.get("market_cap")
    if market_cap and net_debt_value is not None and ebitda_value:
        ev = market_cap + net_debt_value
        metrics["sec_normalized_ev_to_ebitda"] = round(ev / ebitda_value, 2)

    # Cash conversion cycle -- hoeveel dagen kapitaal vastzit tussen inkoop
    # en incasso. COGS wordt afgeleid (omzet - brutowinst) omdat SEC-filers
    # geen aparte "cost of goods sold"-tag consistent gebruiken.
    inventory = facts.get("InventoryNet")
    receivables = facts.get("AccountsReceivableNetCurrent")
    payables = facts.get("AccountsPayableCurrent")
    gross_profit = facts.get("GrossProfit")
    if revenue and gross_profit:
        fy_ccc = gross_profit[-1]["fiscal_year"]
        rev_value = _value_for_year(revenue, fy_ccc)
        cogs_value = (rev_value - gross_profit[-1]["value"]) if rev_value else None
        if cogs_value and cogs_value > 0:
            dso = (_value_for_year(receivables, fy_ccc) or 0) / rev_value * 365 if rev_value else None
            dio = (_value_for_year(inventory, fy_ccc) or 0) / cogs_value * 365
            dpo = (_value_for_year(payables, fy_ccc) or 0) / cogs_value * 365
            if dso is not None:
                metrics["sec_cash_conversion_cycle_days"] = {
                    "fiscal_year": fy_ccc,
                    "dso": round(dso, 1), "dio": round(dio, 1), "dpo": round(dpo, 1),
                    "value": round(dso + dio - dpo, 1),
                }

    # ROIC vs. WACC -- schept het bedrijf waarde boven zijn kapitaalkosten,
    # of vernietigt het die? NOPAT gebruikt een vast, expliciet benoemd
    # belastingtarief (geen aparte belasting-tag beschikbaar in onze SEC-
    # selectie) -- dit is een aanname, geen geverifieerd cijfer, en wordt
    # ook zo aan Claude meegegeven.
    ASSUMED_TAX_RATE = 0.25
    if ebit:
        fy_roic = ebit[-1]["fiscal_year"]
        assets_value = _value_for_year(assets, fy_roic) if (assets := facts.get("Assets")) else None
        liabilities_value = _value_for_year(facts.get("Liabilities"), fy_roic)
        lt_debt_value = _value_for_year(lt_debt, fy_roic) if lt_debt else None
        if assets_value is not None and liabilities_value is not None and lt_debt_value is not None:
            equity_value = assets_value - liabilities_value
            invested_capital = lt_debt_value + equity_value
            if invested_capital > 0:
                nopat = ebit[-1]["value"] * (1 - ASSUMED_TAX_RATE)
                roic = nopat / invested_capital
                metrics["sec_roic"] = {
                    "fiscal_year": fy_roic, "value": round(roic, 4),
                    "assumed_tax_rate": ASSUMED_TAX_RATE,
                }
                wacc = estimate_wacc(company_data)
                if wacc is not None:
                    metrics["sec_roic_vs_wacc_spread"] = round(roic - wacc, 4)

    # DuPont-decompositie -- ontleedt ROE in drie oorzaken (marge, efficiëntie,
    # hefboom) i.p.v. één los cijfer, zodat een margeverandering niet
    # verward wordt met een hefboom- of efficiëntie-verandering.
    net_income = facts.get("NetIncomeLoss")
    if net_income and revenue:
        fy_dupont = net_income[-1]["fiscal_year"]
        rev_value = _value_for_year(revenue, fy_dupont)
        assets_value = _value_for_year(facts.get("Assets"), fy_dupont)
        liabilities_value = _value_for_year(facts.get("Liabilities"), fy_dupont)
        if rev_value and assets_value and liabilities_value is not None:
            equity_value = assets_value - liabilities_value
            if equity_value > 0:
                net_margin = net_income[-1]["value"] / rev_value
                asset_turnover = rev_value / assets_value
                equity_multiplier = assets_value / equity_value
                metrics["sec_dupont_decomposition"] = {
                    "fiscal_year": fy_dupont,
                    "net_margin": round(net_margin, 4),
                    "asset_turnover": round(asset_turnover, 4),
                    "equity_multiplier": round(equity_multiplier, 2),
                    "roe": round(net_margin * asset_turnover * equity_multiplier, 4),
                }

    return metrics


def compute_forensic_flags(sec_result: dict) -> list[dict]:
    """Hoofdfunctie: neemt de output van fetch_sec_financials() en geeft een
    lijst van flags terug. Geeft een lege lijst terug (nooit een fout) als
    er onvoldoende data is -- SEC-dekking en welke tags een bedrijf
    gebruikt varieert sterk, dus ontbrekende data is normaal, geen bug."""
    if not sec_result or "error" in sec_result:
        return []

    facts = sec_result.get("annual_facts", {})
    flags = []

    # 1. Debiteuren (AR) groeien sneller dan omzet -- mogelijk signaal van
    #    kwaliteit-van-omzet-problemen (tragere klantbetalingen, of
    #    agressievere omzetverantwoording).
    revenue = _pick_revenue_series(facts)
    ar = facts.get("AccountsReceivableNetCurrent")
    pair_rev = _latest_two(revenue) if revenue else None
    pair_ar = _latest_two(ar) if ar else None
    if pair_rev and pair_ar:
        rev_growth = _pct_change(pair_rev[0]["value"], pair_rev[1]["value"])
        ar_growth = _pct_change(pair_ar[0]["value"], pair_ar[1]["value"])
        if rev_growth is not None and ar_growth is not None and (ar_growth - rev_growth) > 0.15:
            flags.append({
                "check": "AR vs. omzetgroei",
                "severity": "watch",
                "message": (
                    f"Debiteuren groeiden {ar_growth * 100:.1f}% terwijl omzet {rev_growth * 100:.1f}% groeide "
                    f"(FY{pair_ar[0]['fiscal_year']} -> FY{pair_ar[1]['fiscal_year']}) -- een verschil van meer "
                    f"dan 15 procentpunt kan duiden op vertragende klantbetalingen of agressievere omzetboeking."
                ),
            })

    # 2. Voorraad groeit sneller dan omzet -- mogelijk signaal van
    #    verzwakkende vraag of een verhoogd afschrijvingsrisico.
    inventory = facts.get("InventoryNet")
    pair_inv = _latest_two(inventory) if inventory else None
    if pair_rev and pair_inv:
        rev_growth = _pct_change(pair_rev[0]["value"], pair_rev[1]["value"])
        inv_growth = _pct_change(pair_inv[0]["value"], pair_inv[1]["value"])
        if rev_growth is not None and inv_growth is not None and (inv_growth - rev_growth) > 0.15:
            flags.append({
                "check": "Voorraad vs. omzetgroei",
                "severity": "watch",
                "message": (
                    f"Voorraad groeide {inv_growth * 100:.1f}% terwijl omzet {rev_growth * 100:.1f}% groeide "
                    f"(FY{pair_inv[0]['fiscal_year']} -> FY{pair_inv[1]['fiscal_year']}) -- kan wijzen op "
                    f"verzwakkende vraag of een verhoogd risico op toekomstige voorraadafschrijvingen."
                ),
            })

    # 3. Vrije kasstroom vs. netto winst -- een grote afwijking is een
    #    klassiek signaal om de kwaliteit van de winst kritisch te bekijken.
    ocf = facts.get("NetCashProvidedByUsedInOperatingActivities")
    capex = facts.get("PaymentsToAcquirePropertyPlantAndEquipment")
    net_income = facts.get("NetIncomeLoss")
    if ocf and net_income:
        latest_ocf = ocf[-1]
        fy = latest_ocf["fiscal_year"]
        latest_ni_value = _value_for_year(net_income, fy)
        latest_capex_value = _value_for_year(capex, fy) or 0
        fcf_value = latest_ocf["value"] - latest_capex_value
        if latest_ni_value and latest_ni_value > 0:
            ratio = fcf_value / latest_ni_value
            if ratio < 0.6 or ratio > 1.8:
                flags.append({
                    "check": "FCF vs. netto winst",
                    "severity": "watch",
                    "message": (
                        f"Vrije kasstroom (${fcf_value / 1e9:.2f}B) week in FY{fy} sterk af van netto winst "
                        f"(${latest_ni_value / 1e9:.2f}B, ratio {ratio:.2f}x) -- de moeite waard om te "
                        f"onderzoeken wat het verschil verklaart (werkkapitaal, eenmalige posten, non-cash lasten)."
                    ),
                })

    # 4. Diluted shares LAGER dan basic shares in hetzelfde jaar -- dit is
    #    wiskundig ongebruikelijk (de treasury-stock-methode maakt diluted
    #    altijd >= basic). Precies de fout die bij Alcoa leidde tot een
    #    verzonnen, foute verklaring -- hier gevangen vóórdat Claude het ziet.
    diluted = facts.get("WeightedAverageNumberOfDilutedSharesOutstanding")
    basic = facts.get("WeightedAverageNumberOfSharesOutstandingBasic")
    if diluted and basic:
        latest_diluted = diluted[-1]
        fy = latest_diluted["fiscal_year"]
        matching_basic = _value_for_year(basic, fy)
        if matching_basic is not None and latest_diluted["value"] < matching_basic:
            flags.append({
                "check": "Diluted vs. basic aandelental",
                "severity": "flag",
                "message": (
                    f"In FY{fy} is het diluted aandelental ({latest_diluted['value']:,}) LAGER dan het basic "
                    f"aandelental ({matching_basic:,}) -- dit is wiskundig ongebruikelijk. Benoem dit als een "
                    f"onverklaarde databron-discrepantie; verzin geen technische reden hiervoor."
                ),
            })

    # 5. EBIT daalt terwijl een EBITDA-proxy (EBIT + D&A) juist stijgt --
    #    exact het patroon dat bij Alcoa tot een onjuiste, verzonnen
    #    verklaring leidde. Hier berekend, niet aan het taalmodel overgelaten.
    ebit = facts.get("OperatingIncomeLoss")
    da = facts.get("DepreciationDepletionAndAmortization") or facts.get("DepreciationAmortizationAndAccretionNet")
    pair_ebit = _latest_two(ebit) if ebit else None
    if pair_ebit and da:
        fy_old, fy_new = pair_ebit[0]["fiscal_year"], pair_ebit[1]["fiscal_year"]
        da_old, da_new = _value_for_year(da, fy_old), _value_for_year(da, fy_new)
        if da_old is not None and da_new is not None:
            ebitda_old = pair_ebit[0]["value"] + da_old
            ebitda_new = pair_ebit[1]["value"] + da_new
            ebit_change = _pct_change(pair_ebit[0]["value"], pair_ebit[1]["value"])
            ebitda_change = _pct_change(ebitda_old, ebitda_new)
            if ebit_change is not None and ebitda_change is not None and ebit_change < 0 < ebitda_change:
                flags.append({
                    "check": "EBIT vs. EBITDA-richting",
                    "severity": "flag",
                    "message": (
                        f"EBIT daalde {abs(ebit_change) * 100:.1f}% (FY{fy_old} -> FY{fy_new}) terwijl de "
                        f"EBITDA-proxy (EBIT + D&A) juist steeg ({ebitda_change * 100:.1f}%) -- dit tegengestelde "
                        f"patroon verdient een expliciete, geverifieerde verklaring (bijv. een specifieke, "
                        f"gekwantificeerde D&A-stijging), geen aanname."
                    ),
                })

    # 6. Langlopende schuld sterk gestegen -- puur informationeel signaal
    #    voor sectie 7 (Debt Structure), geen kwaliteitsprobleem op zich.
    lt_debt = facts.get("LongTermDebtNoncurrent")
    pair_lt = _latest_two(lt_debt) if lt_debt else None
    if pair_lt:
        lt_change = _pct_change(pair_lt[0]["value"], pair_lt[1]["value"])
        if lt_change is not None and lt_change > 0.25:
            flags.append({
                "check": "Langlopende schuld-groei",
                "severity": "info",
                "message": (
                    f"Langlopende schuld steeg {lt_change * 100:.1f}% "
                    f"(FY{pair_lt[0]['fiscal_year']} -> FY{pair_lt[1]['fiscal_year']})."
                ),
            })

    # 7. Operating income en net income hebben TEGENGESTELDE tekens in
    #    hetzelfde jaar -- klassiek signaal van een grote, niet-operationele
    #    post (bijv. warrant-herwaardering bij een SPAC-fusiejaar). Dit is
    #    precies het patroon dat live een echte, verwarrende inconsistentie
    #    veroorzaakte (LEU/OKLO): Claude probeerde het gat zelf te verklaren
    #    zonder dit vooraf als apart signaal te krijgen. Puur informationeel
    #    -- geen probleem op zich, wel iets om expliciet te benoemen i.p.v.
    #    zelf te reconciliëren.
    ebit_series = facts.get("OperatingIncomeLoss")
    net_income_series = facts.get("NetIncomeLoss")
    if ebit_series and net_income_series:
        ebit_by_year = {e["fiscal_year"]: e["value"] for e in ebit_series}
        ni_by_year = {e["fiscal_year"]: e["value"] for e in net_income_series}
        for fy in sorted(set(ebit_by_year) & set(ni_by_year)):
            op_income, net_income_val = ebit_by_year[fy], ni_by_year[fy]
            same_sign = (op_income >= 0) == (net_income_val >= 0)
            if not same_sign and abs(net_income_val - op_income) > 1_000_000:
                flags.append({
                    "check": "Operating income vs. net income (tekenverschil)",
                    "severity": "watch",
                    "message": (
                        f"FY{fy}: operating income/loss (${op_income/1e6:.1f}M) en net income/loss "
                        f"(${net_income_val/1e6:.1f}M) hebben tegengestelde tekens -- wijst vaak op een "
                        f"grote, niet-operationele post in dat jaar (bijv. een SPAC-fusiegerelateerde "
                        f"warrant-herwaardering). Vermeld dit als apart signaal i.p.v. het verschil zelf "
                        f"te proberen verklaren of te reconciliëren."
                    ),
                })

    return flags
