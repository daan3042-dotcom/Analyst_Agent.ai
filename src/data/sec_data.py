"""
sec_data.py
Haalt financiële data rechtstreeks bij de bron: SEC EDGAR, de Amerikaanse
toezichthouder waar beursgenoteerde bedrijven hun officiële cijfers
(10-K/10-Q) verplicht moeten indienen. Dit is de PRIMAIRE bron -- exacter
en consistenter over meerdere jaren dan wat yfinance soms teruggeeft.

Waarom dit bestaat: bijna elke hardnekkige cijfermatige tegenstrijdigheid
die we deze week tegenkwamen (Alcoa's EBIT/EBITDA-sprong, Petrobras'
wisselende aandelenaantallen) kwam voort uit onvolledige/verouderde
yfinance-data, niet uit een denkfout van Claude. Betere bron aan de basis
is effectiever dan er later met promptregels omheen bouwen.

SEC vereist GEEN API-key, maar WEL een duidelijke, identificerende
User-Agent header bij elk verzoek (fair-access-beleid, geen misbruik van
hun gratis dienst). Zie https://www.sec.gov/os/webmaster-faq#developers
"""

import json
import os
import time
from datetime import date

import requests

# BELANGRIJK, DD: vul hier een echt contactadres in (jouw e-mail of TCE's) --
# SEC vraagt dit expliciet, en een generiek/vals adres kan leiden tot een
# blokkade van je IP-adres door hun systeem.
USER_AGENT = "TCE Financial Analyst Agent your-email@example.com"

TICKER_MAP_CACHE = "sec_ticker_map.json"

# Een gerichte selectie van ~15 XBRL-tags -- de regels die je nodig hebt voor
# forensische ratio-checks (AR vs. omzet, FCF vs. netto winst, aandelental)
# en een gegrond driver-tree-uitgangspunt. Niet de volledige companyfacts-
# dump (die kan honderden tags per bedrijf bevatten).
RELEVANT_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "GrossProfit",
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "AccountsReceivableNetCurrent",
    "InventoryNet",
    "AccountsPayableCurrent",
    "CashAndCashEquivalentsAtCarryingValue",
    "LongTermDebtNoncurrent",
    "LongTermDebtCurrent",
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    # Toegevoegd voor de Altman Z-Score (faillissementsrisico-formule):
    "Assets",
    "Liabilities",
    "AssetsCurrent",
    "LiabilitiesCurrent",
    "RetainedEarningsAccumulatedDeficit",
    # Toegevoegd voor het uitgebreide drie-staten-rekenmodel (rentelast op
    # bestaande schuld afleiden uit echte cijfers i.p.v. een gok):
    "InterestExpense",
]


def _get_ticker_cik_map() -> dict:
    """SEC's statische ticker-naar-CIK-mapping (CIK = hun interne bedrijfs-ID).
    Lokaal gecached in een JSON-bestand zodat we 'm niet bij elke analyse
    opnieuw hoeven te downloaden -- dit bestand verandert zelden."""
    if os.path.exists(TICKER_MAP_CACHE):
        with open(TICKER_MAP_CACHE, encoding="utf-8") as f:
            return json.load(f)

    resp = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()
    # raw ziet er zo uit: {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}
    mapping = {entry["ticker"].upper(): str(entry["cik_str"]).zfill(10) for entry in raw.values()}

    with open(TICKER_MAP_CACHE, "w", encoding="utf-8") as f:
        json.dump(mapping, f)
    return mapping


def _extract_annual_series(tag_data: dict) -> list[dict] | None:
    """Pakt uit de ruwe XBRL-data alleen de JAARLIJKSE (10-K) waarden,
    gededupliceerd en gesorteerd, laatste 6 jaar.

    Twee lessen uit een echte testrun op AAPL, allebei opgelost hier:
    1. SEC's "fy"-veld beschrijft in welke AANGIFTE een cijfer voorkwam
       (elk jaarverslag herhaalt het vorige jaar als vergelijkingscijfer),
       niet welk jaar het cijfer zelf beschrijft -- dat gaf 2-3 identieke
       waarden met verschillende (foute) jaarlabels. We dedupliceren nu op
       einddatum en leiden het jaartal af uit die datum zelf.
    2. SEC's "fp" == "FY"-label bleek niet altijd betrouwbaar -- sommige
       oudere 10-K's bevatten kwartaal-detailcijfers die toch als "FY"
       gelabeld staan. Voor cijfers met een periode (omzet, winst -- met
       een start- EN einddatum) checken we nu zelf of de periode ongeveer
       een vol jaar beslaat (330-400 dagen), i.p.v. het SEC-label te
       vertrouwen. Balansposten (voorraad, kas -- alleen een peildatum,
       geen periode) hebben deze check niet nodig."""
    units = tag_data.get("units", {})
    values = units.get("USD") or units.get("shares") or []

    seen_periods = {}
    for v in values:
        if v.get("form") not in ("10-K", "10-K/A"):
            continue
        end = v.get("end")
        start = v.get("start")
        if not end:
            continue

        if start:
            try:
                d_start = date.fromisoformat(start)
                d_end = date.fromisoformat(end)
            except ValueError:
                continue
            if not (330 <= (d_end - d_start).days <= 400):
                continue  # geen jaarperiode -- waarschijnlijk een kwartaal- of tussentijds cijfer

        seen_periods[end] = v.get("val")

    if not seen_periods:
        return None

    sorted_ends = sorted(seen_periods.keys())[-6:]
    return [
        {"fiscal_year": int(end[:4]), "period_end": end, "value": seen_periods[end]}
        for end in sorted_ends
    ]


def fetch_sec_financials(ticker: str) -> dict:
    """Haalt de belangrijkste meerjarige XBRL-cijfers op voor een ticker,
    rechtstreeks uit SEC-filings. Geeft een compacte dict terug met per
    relevante regel de laatste ~6 jaarwaarden.

    Werkt alleen voor Amerikaanse beursgenoteerde bedrijven (SEC-registratie
    vereist) -- voor buitenlandse noteringen zoals Petrobras (ADR) of
    Alcoa's niet-Amerikaanse peers geeft dit een duidelijke foutmelding
    terug in plaats van te crashen."""
    ticker_sec = ticker.upper().replace(".", "-")  # SEC schrijft bijv. BRK-B, niet BRK.B

    try:
        cik_map = _get_ticker_cik_map()
    except Exception:
        return {"error": "kon SEC ticker-CIK-mapping niet ophalen (netwerkprobleem?)"}

    cik = cik_map.get(ticker_sec)
    if not cik:
        return {"error": f"ticker '{ticker}' niet gevonden in SEC-register -- "
                          f"mogelijk geen Amerikaanse beursnotering (SEC dekt alleen US-filers)"}

    try:
        resp = requests.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        facts = resp.json()
    except Exception:
        return {"error": f"kon SEC companyfacts niet ophalen voor {ticker}"}

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_facts = {}
    for tag in RELEVANT_TAGS:
        tag_data = us_gaap.get(tag)
        if not tag_data:
            continue
        series = _extract_annual_series(tag_data)
        if series:
            annual_facts[tag] = series

    if not annual_facts:
        return {"error": f"geen bruikbare XBRL-jaarcijfers gevonden voor {ticker}"}

    return {"ticker": ticker, "cik": cik, "source": "SEC EDGAR", "annual_facts": annual_facts}


def _parse_form4_xml(xml_text: str) -> list[dict]:
    """Parseert de ruwe Form 4-XML naar losse transacties. Beperkt bewust tot
    nonDerivativeTransaction (echte aandelen, geen opties/derivaten) -- dat
    is het directst interpreteerbare signaal (kocht/verkocht deze persoon
    daadwerkelijk aandelen). Geeft een lege lijst terug bij elk parseerprobleem
    -- liever een ontbrekende transactie dan een crash op onverwachte XML."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    owner_el = root.find(".//reportingOwner/reportingOwnerId/rptOwnerName")
    owner_name = owner_el.text if owner_el is not None else "Onbekend"

    def _is_true(value: str | None) -> bool:
        # Echte SEC-XML gebruikt "true"/"false" als tekst, niet "1"/"0"
        # zoals ik eerder aannam -- dat liet de rol-detectie stilzwijgend
        # altijd terugvallen op "insider" i.p.v. het echte "officer"/
        # "director". Beide vormen nu geaccepteerd, voor de zekerheid.
        return (value or "").strip().lower() in ("1", "true")

    rel_el = root.find(".//reportingOwner/reportingOwnerRelationship")
    role_bits = []
    if rel_el is not None:
        if _is_true(rel_el.findtext("isDirector")):
            role_bits.append("director")
        if _is_true(rel_el.findtext("isOfficer")):
            title = rel_el.findtext("officerTitle") or "officer"
            role_bits.append(title)
        if _is_true(rel_el.findtext("isTenPercentOwner")):
            role_bits.append("10%+ owner")
    role = ", ".join(role_bits) or "insider"

    transactions = []
    for tx in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        try:
            tx_date = tx.findtext(".//transactionDate/value")
            code = tx.findtext(".//transactionCoding/transactionCode")
            shares = float(tx.findtext(".//transactionAmounts/transactionShares/value"))
            price = tx.findtext(".//transactionAmounts/transactionPricePerShare/value")
            price = float(price) if price not in (None, "") else None
            if tx_date and code:
                transactions.append({
                    "owner": owner_name, "role": role, "date": tx_date,
                    "code": code, "shares": shares, "price": price,
                })
        except (TypeError, ValueError):
            continue
    return transactions


def fetch_insider_transactions(ticker: str, max_filings: int = 15) -> dict:
    """Haalt de meest recente Form 4-inzendingen (insider-transacties) op via
    SEC EDGAR en vat ze samen -- ECHTE, gestructureerde koop/verkoop-data i.p.v.
    de toevallige vermelding die websearch soms oplevert. Alleen transactiecode
    'P' (open-market aankoop) en 'S' (open-market verkoop) worden meegenomen
    in de netto-samenvatting; code 'A' (toekenning/grant, routinematige
    beloning) wordt apart geteld maar niet als koop/verkoop-signaal behandeld,
    omdat een toekenning geen vrijwillige investeringsbeslissing weerspiegelt."""
    try:
        cik_map = _get_ticker_cik_map()
    except Exception:
        return {"error": "kon SEC ticker-CIK-mapping niet ophalen (netwerkprobleem?)"}
    cik = cik_map.get(ticker.upper())
    if not cik:
        return {"error": f"CIK niet gevonden voor {ticker}"}

    try:
        resp = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        submissions = resp.json()
    except Exception:
        return {"error": f"kon SEC-inzendingenoverzicht niet ophalen voor {ticker}"}

    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    form4_indices = [i for i, f in enumerate(forms) if f == "4"][:max_filings]
    if not form4_indices:
        return {"error": f"geen Form 4-inzendingen gevonden voor {ticker} (mogelijk geen recente insider-activiteit)"}

    all_transactions = []
    for i in form4_indices:
        accession_no_dashes = accession_numbers[i].replace("-", "")
        doc_name = primary_docs[i]
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{doc_name}"
        try:
            xml_resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            xml_resp.raise_for_status()
            all_transactions.extend(_parse_form4_xml(xml_resp.text))
        except Exception:
            continue
        # Kleine, bewuste pauze tussen de tot 15 losse aanvragen: SEC EDGAR
        # rate-limit't agressieve bursts, en een live run (UUUU) faalde
        # stilzwijgend op ALLE transacties terwijl de parser zelf, getest
        # tegen een echte, representatieve UUUU-Form-4-XML, prima werkt --
        # een plausibele, goedkope voorzorgsmaatregel tegen die oorzaak.
        time.sleep(0.15)

    if not all_transactions:
        return {"error": f"kon geen individuele transacties uit de Form 4-bestanden van {ticker} parseren"}

    buys = [t for t in all_transactions if t["code"] == "P"]
    sells = [t for t in all_transactions if t["code"] == "S"]
    net_shares = sum(t["shares"] for t in buys) - sum(t["shares"] for t in sells)
    buy_value = sum(t["shares"] * t["price"] for t in buys if t["price"])
    sell_value = sum(t["shares"] * t["price"] for t in sells if t["price"])

    return {
        "ticker": ticker,
        "filings_checked": len(form4_indices),
        "open_market_buys": len(buys),
        "open_market_sells": len(sells),
        "net_shares_bought": round(net_shares),
        "buy_value_dollars": round(buy_value) if buy_value else None,
        "sell_value_dollars": round(sell_value) if sell_value else None,
        "recent_transactions": sorted(
            [t for t in all_transactions if t["code"] in ("P", "S")],
            key=lambda t: t["date"], reverse=True,
        )[:10],
    }


if __name__ == "__main__":
    import sys
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    result = fetch_sec_financials(test_ticker)
    print(json.dumps(result, indent=2, default=str))
