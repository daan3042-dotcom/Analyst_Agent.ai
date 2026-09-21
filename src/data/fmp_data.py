"""
fmp_data.py
Financial Modeling Prep (FMP) als TWEEDE-BESTE brondata voor bedrijven
zonder Amerikaanse SEC-dekking (bijv. Canadese noteringen zoals Ero Copper).

KERNONTWERP: dit bestand vertaalt FMP's data naar EXACT dezelfde vorm als
sec_data.py's annual_facts (dezelfde XBRL-taglabels als keys, dezelfde
{fiscal_year, period_end, value}-structuur per jaar). Daardoor hoeven
forensics.py, altman_z.py en financial_model.py NIET aangepast te worden --
ze werken al door met "een dict met annual_facts", ongeacht of die van SEC
of van FMP komt.

EERLIJKHEID: dit is bewust een TWEEDE-BESTE bron, niet gelijkwaardig aan
SEC. FMP's gratis laag is minder diep en minder gestandaardiseerd dan SEC's
XBRL-data (zie ook de eerdere SEC-bugs die we vonden -- vergelijkbare
datakwaliteitsproblemen kunnen hier ook optreden, en zijn met deze
tweede-beste bron minder goed te controleren). Zie het als "beter dan
alleen yfinance," niet als "net zo goed als SEC."

Vereist een gratis FMP-API-key (aan te vragen op
https://site.financialmodelingprep.com/developer/docs), in te stellen als
FMP_API_KEY, zelfde manier als je andere keys.
"""

import os

import requests

BASE_URL = "https://financialmodelingprep.com/api/v3"
MAX_YEARS = 6


def _get_api_key() -> str | None:
    return os.environ.get("FMP_API_KEY")


def _fetch_statement(ticker: str, statement: str, api_key: str) -> list[dict]:
    """Haalt één van de drie jaarrekening-eindpunten op (income-statement,
    balance-sheet-statement, cash-flow-statement). Geeft een lege lijst
    terug bij een fout -- de aanroeper behandelt dat als "dit stuk data
    ontbreekt," niet als een harde crash."""
    try:
        resp = requests.get(
            f"{BASE_URL}/{statement}/{ticker}",
            params={"period": "annual", "limit": MAX_YEARS, "apikey": api_key},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _to_annual_series(rows: list[dict], value_field: str, take_abs: bool = False) -> list[dict]:
    """Zet FMP's rijen (nieuwste eerst, één dict per jaar) om naar EXACT
    dezelfde vorm als sec_data.py's annual_facts-reeksen: oudste eerst,
    met fiscal_year/period_end/value. take_abs corrigeert het
    tekenverschil bij capex (FMP rapporteert dat als negatief, SEC als
    positief bedrag) zodat forensics.py's formules niet per ongeluk gaan
    optellen in plaats van aftrekken."""
    series = []
    for row in rows:
        date_str = row.get("date")
        value = row.get(value_field)
        if date_str is None or value is None:
            continue
        try:
            fiscal_year = int(date_str[:4])
        except (ValueError, TypeError):
            continue
        if take_abs:
            value = abs(value)
        series.append({"fiscal_year": fiscal_year, "period_end": date_str, "value": value})
    return sorted(series, key=lambda v: v["period_end"])


def fetch_fmp_financials(ticker: str) -> dict:
    """Hoofdfunctie: geeft data terug in EXACT dezelfde vorm als
    sec_data.fetch_sec_financials() -- {"ticker", "source", "annual_facts": {...}}
    of {"error": "..."}. De keys in annual_facts zijn dezelfde XBRL-
    taglabels als SEC gebruikt, zodat de rest van de pijplijn niet hoeft te
    weten of de data van SEC of FMP komt."""
    api_key = _get_api_key()
    if not api_key:
        return {"error": "FMP_API_KEY niet gevonden in environment"}

    income = _fetch_statement(ticker, "income-statement", api_key)
    balance = _fetch_statement(ticker, "balance-sheet-statement", api_key)
    cashflow = _fetch_statement(ticker, "cash-flow-statement", api_key)

    if not income and not balance and not cashflow:
        return {"error": f"geen FMP-data gevonden voor {ticker} (onbekende ticker, of buiten de gratis laag)"}

    annual_facts = {}

    def add(tag: str, rows: list[dict], value_field: str, take_abs: bool = False):
        series = _to_annual_series(rows, value_field, take_abs=take_abs)
        if series:
            annual_facts[tag] = series

    # Resultatenrekening
    add("Revenues", income, "revenue")
    add("NetIncomeLoss", income, "netIncome")
    add("OperatingIncomeLoss", income, "operatingIncome")
    add("GrossProfit", income, "grossProfit")
    add("DepreciationDepletionAndAmortization", income, "depreciationAndAmortization")
    add("InterestExpense", income, "interestExpense")
    add("WeightedAverageNumberOfDilutedSharesOutstanding", income, "weightedAverageShsOutDil")
    add("WeightedAverageNumberOfSharesOutstandingBasic", income, "weightedAverageShsOut")

    # Balans
    add("Assets", balance, "totalAssets")
    add("Liabilities", balance, "totalLiabilities")
    add("AssetsCurrent", balance, "totalCurrentAssets")
    add("LiabilitiesCurrent", balance, "totalCurrentLiabilities")
    add("CashAndCashEquivalentsAtCarryingValue", balance, "cashAndCashEquivalents")
    add("AccountsReceivableNetCurrent", balance, "netReceivables")
    add("InventoryNet", balance, "inventory")
    add("LongTermDebtNoncurrent", balance, "longTermDebt")
    add("LongTermDebtCurrent", balance, "shortTermDebt")
    add("RetainedEarningsAccumulatedDeficit", balance, "retainedEarnings")

    # Kasstroomoverzicht -- let op take_abs=True voor capex (zie docstring hierboven)
    add("NetCashProvidedByUsedInOperatingActivities", cashflow, "netCashProvidedByOperatingActivities")
    add("PaymentsToAcquirePropertyPlantAndEquipment", cashflow, "capitalExpenditure", take_abs=True)

    if not annual_facts:
        return {"error": f"FMP gaf data terug voor {ticker}, maar geen van de verwachte velden was bruikbaar"}

    return {"ticker": ticker, "source": "FMP", "annual_facts": annual_facts}
