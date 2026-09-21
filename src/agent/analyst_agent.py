"""
analyst_agent.py
De "agent": haalt data op, stuurt 'm samen met het TCE-analyseframework naar
de Claude API, en schrijft het resultaat weg als tekst (met metadata-header).

Sinds deze versie kan Claude tijdens het schrijven zelf besluiten om de
get_recent_news tool aan te roepen (bijv. voor sectie 12). Dit is dus niet
langer een pure rechte lijn, maar een kleine agent-loop: model -> eventueel
tool-aanroep -> resultaat terug -> model gaat verder -> ... -> klaar.

Gebruik:
    export ANTHROPIC_API_KEY="sk-ant-..."
    export ALPHAVANTAGE_API_KEY="..."
    python3 analyst_agent.py NKE --peers ADS.DE PUMA.DE --context "Focus extra op China-omzet"
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
from datetime import datetime

if __name__ == "__main__":
    # Zorgt dat "python src/agent/analyst_agent.py" blijft werken nu dit
    # bestand in een subpakket zit: src/ (de ouder van agent/) moet op
    # sys.path staan voor de package-imports hieronder (data.data_fetch e.d.).
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic

from data_fetch import (
    compute_options_analysis,
    compute_regime_detection,
    compute_rolling_beta,
    compute_sharpe_sortino,
    compute_value_at_risk,
    fetch_company_data,
    fetch_historical_volatility,
    fetch_peer_data,
)
from altman_z import compute_altman_z
from piotroski_score import compute_piotroski_score
from consistency_check import check_output_consistency
from finra_data import fetch_short_interest
from forensics import compute_forensic_flags, compute_verified_metrics
from fred_data import fetch_macro_snapshot
from fmp_data import fetch_fmp_financials
from reverse_dcf import compute_intrinsic_value_estimate, compute_reverse_dcf
from sec_data import fetch_insider_transactions, fetch_sec_financials
from framework import (
    EXECUTIVE_SUMMARY_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT_COMPLETENESS,
    REVIEW_SYSTEM_PROMPT_CROSSREF,
    REVIEW_SYSTEM_PROMPT_NEUTRALITY,
    REVIEW_SYSTEM_PROMPT_NUMBERS,
    SYSTEM_PROMPT,
    build_analysis_prompt,
    build_executive_summary_prompt,
    build_review_prompt,
    build_revision_prompt,
)
from peer_analysis import compute_peer_comparison
from lineage import build_lineage_manifest
from render import get_brand_colors, render_html
from track_record import load_previous_report, save_report_snapshot
from tools import ALL_TOOLS, run_tool

MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 3
MAX_TOOL_ROUNDS = 8  # veiligheidsgrens; verhoogd omdat run_financial_projection tot 3x wordt aangeroepen (bear/base/bull)
MAX_ANALYSIS_TOKENS = 28000  # verhoogd van 16000: bij een live Alcoa-run liep het rapport HIER 3x op rij (origineel + 2 correctierondes) tegen het plafond aan, met sectie 17/18 volledig ontbrekend als gevolg -- het rapport is dit seizoen flink gegroeid (18 secties, 24 grafiektypes, meer te narreren geverifieerde cijfers) zonder dat dit plafond meegroeide
MAX_REVISION_ROUNDS = 2  # hoeveel keer de agent zichzelf mag proberen te corrigeren


def check_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "FOUT: ANTHROPIC_API_KEY staat niet in je environment.\n"
            "Zet 'm met: export ANTHROPIC_API_KEY=\"sk-ant-...\""
        )


def validate_company_data(ticker: str, company_data: dict) -> None:
    if not company_data.get("long_name"):
        print(
            f"WAARSCHUWING: geen bedrijfsnaam gevonden voor '{ticker}'. "
            f"Klopt de ticker? (bijv. 'ASML.AS' i.p.v. 'ASML' voor Euronext)",
            file=sys.stderr,
        )


def call_claude_with_retry(client: anthropic.Anthropic, messages: list):
    """Zelfde retry-logica als eerder, nu herbruikbaar voor elke stap in de loop
    (zowel de eerste call als elke vervolg-call na een tool-resultaat).

    cache_control hier is bewust ALLEEN op deze call gezet, niet op de
    review/revisie-calls elders. Reden: binnen deze loop is de voorgaande
    berichtengeschiedenis (het hele framework + bedrijfsdata) bij ronde 2
    letterlijk identiek aan ronde 1 -- dat is precies waar caching op werkt
    (hergebruik van EXACT dezelfde tekst). Review en revisie krijgen elke
    keer een ANDER rapport als input, dus daar is niets identieks om te
    hergebruiken -- caching zou daar alleen extra kosten (cache-writes zijn
    1.25x de normale prijs) zonder ooit een cache-hit terug te verdienen.

    UPDATE 2: overgezet op streaming (client.messages.stream(...) i.p.v.
    client.messages.create(...)). Reden: de Anthropic SDK weigert een
    NIET-streamende aanvraag te doen zodra de combinatie van max_tokens en
    model potentieel langer dan 10 minuten zou kunnen duren -- dat gebeurde
    hier live zodra MAX_ANALYSIS_TOKENS naar 28000 ging. stream.get_final_message()
    geeft exact hetzelfde Message-object terug als .create() deed (dezelfde
    .content/.stop_reason/.usage), dus de rest van de code hoeft niet te
    weten dat dit nu streamt.

    UPDATE 3: cache-TTL van 5 minuten naar 1 uur (ttl: "1h", vereist de
    extended-cache-ttl-2025-04-11 bèta-header). Reden: een echte LEU-run liet
    een scheve cache-write/read-verhouding zien (160K write vs. 228K read --
    normaal zou read veel hoger moeten liggen dan write). Waarschijnlijke
    oorzaak: de volledige pijplijn (hoofdanalyse + tot 2x review + tot 2x
    correctie) kan makkelijk langer dan 5 minuten duren, waardoor de cache
    binnen EEN ENKELE run al verloopt -- dit is dus geen herhaling van de
    eerdere, bewust afgewezen cross-run-caching-discussie (DD draait maar
    ~1x/dag), maar een apart, waarschijnlijk wel degelijk nuttig scenario:
    hergebruik BINNEN dezelfde run. 1-uurs cache-writes kosten wel 2x de
    basisprijs (i.p.v. 1,25x bij 5 minuten) -- dit is dus alleen een netto
    UPDATE 4: except-clausule verbreed van (APIStatusError, APIConnectionError)
    naar een brede Exception-vangst. Reden: een echte crash liet zien dat een
    verbindingsonderbreking MIDDEN in het streamen (een netwerkhapering, heel
    normaal bij lang-openstaande streaming-verbindingen) niet als
    anthropic.APIConnectionError binnenkomt, maar als een rauwe, ongewikkelde
    httpx-leesfout die de SDK niet netjes omzet -- onze smalle except-clausule
    ving dit dus niet op en het hele script crashte in plaats van gewoon
    opnieuw te proberen. Dit try-blok bevat UITSLUITEND de API-aanroep zelf
    (niets van de tool-uitvoeringslogica, die zit in de aanroepende lus in
    run_analysis) -- een brede Exception-vangst hier is dus veilig en verbergt
    geen echte codefouten elders."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_ANALYSIS_TOKENS,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
                tools=ALL_TOOLS,
                messages=messages,
                cache_control={"type": "ephemeral", "ttl": "1h"},
                extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
            ) as stream:
                return stream.get_final_message()
        except Exception as e:
            last_error = e
            wait = 2 ** attempt
            print(
                f"API-call mislukt (poging {attempt}/{MAX_RETRIES}): {e}. "
                f"Nieuwe poging over {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise RuntimeError(f"API-call bleef falen na {MAX_RETRIES} pogingen") from last_error


def run_analysis(ticker: str, peers: list[str] | None, extra_context: str) -> tuple[str, dict, dict, str, dict, dict | None, list[str], str, list[dict]]:
    print(f"[1/3] Data ophalen voor {ticker}...", file=sys.stderr)
    company_data = fetch_company_data(ticker)
    validate_company_data(ticker, company_data)

    peer_data = None
    peer_comparison = None
    if peers:
        print(f"[1/3] Data ophalen voor concurrenten: {peers}...", file=sys.stderr)
        peer_data = fetch_peer_data(peers)
        peer_comparison = compute_peer_comparison(company_data, peer_data)
        if "error" not in peer_comparison:
            print(f"      Peer-vergelijking berekend voor: {list(peer_comparison.keys())}.", file=sys.stderr)

    previous_report = load_previous_report(ticker)
    if previous_report:
        print(f"      Vorige analyse gevonden (van {previous_report.get('date')}) -- wordt meegenomen.", file=sys.stderr)
    else:
        print(f"      Geen vorige analyse van {ticker} gevonden (eerste keer, of nog niet eerder gedraaid).", file=sys.stderr)

    print(f"[1/3] SEC EDGAR-cijfers ophalen (primaire bron)...", file=sys.stderr)
    sec_result = fetch_sec_financials(ticker)
    if "error" in sec_result:
        print(f"      SEC-data niet beschikbaar: {sec_result['error']}", file=sys.stderr)
        print(f"      Val terug op Financial Modeling Prep (FMP) als tweede-beste bron...", file=sys.stderr)
        fmp_result = fetch_fmp_financials(ticker)
        if "error" not in fmp_result:
            sec_result = fmp_result  # zelfde variabele, zodat alle downstream-code (forensics, Altman Z, rekenmodel) onveranderd doorwerkt
            print(f"      FMP-data gebruikt in plaats van SEC voor {ticker}.", file=sys.stderr)
        else:
            print(f"      FMP ook niet beschikbaar: {fmp_result['error']}", file=sys.stderr)

    if "error" in sec_result:
        forensic_flags = []
    else:
        forensic_flags = compute_forensic_flags(sec_result)
        print(f"      {sec_result.get('source', 'SEC EDGAR')}-data opgehaald. {len(forensic_flags)} forensisch signaal/signalen gevonden.", file=sys.stderr)
        for flag in forensic_flags:
            print(f"      [{flag['severity'].upper()}] {flag['check']}", file=sys.stderr)
    verified_metrics = compute_verified_metrics(sec_result, company_data)

    volatility_result = fetch_historical_volatility(ticker)
    if "error" in volatility_result:
        print(f"      Historische volatiliteit niet beschikbaar: {volatility_result['error']}", file=sys.stderr)
    else:
        verified_metrics["historical_annualized_volatility_pct"] = volatility_result["annualized_volatility_pct"]
        print(f"      Historische volatiliteit (1 jaar): {volatility_result['annualized_volatility_pct']}%.", file=sys.stderr)

    macro_snapshot = fetch_macro_snapshot()
    if "error" in macro_snapshot:
        print(f"      Macro-cijfers (FRED) niet beschikbaar: {macro_snapshot['error']}", file=sys.stderr)
    else:
        print(f"      Macro-cijfers (FRED) opgehaald: {list(macro_snapshot.keys())}.", file=sys.stderr)

    # Value at Risk -- pure wiskunde op basis van koers + al-berekende volatiliteit,
    # geen nieuwe databron nodig, dus altijd berekenen als de ingredienten er zijn.
    var_result = {"error": "koers of volatiliteit ontbreekt"}
    if "error" not in volatility_result and company_data.get("current_price"):
        var_result = compute_value_at_risk(company_data["current_price"], volatility_result["annualized_volatility_pct"])
        if "error" not in var_result:
            print(f"      Value at Risk (95%, 1 dag): {var_result['var_1day_95pct_pct']}%.", file=sys.stderr)

    # Sharpe/Sortino -- vereist de risicovrije rente uit FRED, dus pas ná de macro-fetch.
    sharpe_sortino_result = {"error": "koersdata of risicovrije rente ontbreekt"}
    if "error" not in volatility_result and "error" not in macro_snapshot and "fed_funds_rate" in macro_snapshot:
        risk_free_rate = float(macro_snapshot["fed_funds_rate"]["value"])
        sharpe_sortino_result = compute_sharpe_sortino(volatility_result.get("closes", []), risk_free_rate)
        if "error" not in sharpe_sortino_result:
            print(f"      Sharpe-ratio: {sharpe_sortino_result['sharpe_ratio']}, Sortino-ratio: {sharpe_sortino_result['sortino_ratio']}.", file=sys.stderr)

    # Lopende beta -- een aparte S&P 500-vergelijking, dus een eigen nette
    # foutmelding als dit om wat voor reden dan ook niet lukt.
    rolling_beta_result = compute_rolling_beta(ticker)
    if "error" in rolling_beta_result:
        print(f"      Lopende beta niet beschikbaar: {rolling_beta_result['error']}", file=sys.stderr)
    else:
        print(f"      Lopende beta berekend: {len(rolling_beta_result['betas'])} metingen tegen de S&P 500.", file=sys.stderr)

    regime_result = compute_regime_detection(ticker)
    if "error" in regime_result:
        print(f"      Regimedetectie (HMM) niet beschikbaar: {regime_result['error']}", file=sys.stderr)
    else:
        print(
            f"      Regimedetectie (HMM): huidig regime '{regime_result['current_regime']}', "
            f"al {regime_result['days_in_current_regime']} handelsdagen.",
            file=sys.stderr,
        )

    insider_result = fetch_insider_transactions(ticker)
    if "error" in insider_result:
        print(f"      Insider-transacties (SEC Form 4) niet beschikbaar: {insider_result['error']}", file=sys.stderr)
    else:
        print(
            f"      Insider-transacties: {insider_result['open_market_buys']} aankopen, "
            f"{insider_result['open_market_sells']} verkopen (laatste {insider_result['filings_checked']} Form 4's).",
            file=sys.stderr,
        )

    # Optie-impliciete volatiliteit -- hergebruikt de al-berekende historische
    # volatiliteit hierboven voor een eerlijke IV-vs-HV-vergelijking, geen
    # dubbele berekening nodig.
    historical_vol_for_options = volatility_result.get("annualized_volatility_pct") if "error" not in volatility_result else None
    options_result = compute_options_analysis(ticker, historical_volatility_pct=historical_vol_for_options)
    if "error" in options_result:
        print(f"      Optie-impliciete volatiliteit niet beschikbaar: {options_result['error']}", file=sys.stderr)
    else:
        print(
            f"      Optie-IV (ATM, {options_result['days_to_expiration']}d): {options_result['atm_implied_volatility_pct']}%.",
            file=sys.stderr,
        )

    short_interest_result = fetch_short_interest(ticker)
    if "error" in short_interest_result:
        print(f"      Short interest (FINRA) niet beschikbaar: {short_interest_result['error']}", file=sys.stderr)
    else:
        print(
            f"      Short interest ({short_interest_result['settlement_date']}): "
            f"{short_interest_result['days_to_cover']} dagen om te dekken.",
            file=sys.stderr,
        )

    reverse_dcf_result = compute_reverse_dcf(company_data)
    if "error" in reverse_dcf_result:
        print(f"      Reverse-DCF niet mogelijk: {reverse_dcf_result['error']}", file=sys.stderr)
    else:
        print(
            f"      Reverse-DCF: WACC {reverse_dcf_result['wacc']*100:.1f}%, "
            f"geimpliceerde FCF-groei {reverse_dcf_result['implied_annual_fcf_growth']*100:.1f}%.",
            file=sys.stderr,
        )

    altman_result = compute_altman_z(sec_result, company_data)
    if "error" in altman_result:
        print(f"      Altman Z-Score niet mogelijk: {altman_result['error']}", file=sys.stderr)
    else:
        print(
            f"      Altman Z-Score: {altman_result['z_score']} ({altman_result['zone']}).",
            file=sys.stderr,
        )

    piotroski_result = compute_piotroski_score(sec_result)
    if "error" in piotroski_result:
        print(f"      Piotroski F-Score niet mogelijk: {piotroski_result['error']}", file=sys.stderr)
    else:
        print(
            f"      Piotroski F-Score: {piotroski_result['score']}/9 ({piotroski_result['interpretation']}).",
            file=sys.stderr,
        )

    # Intrinsic-value-schatting (forward-DCF) -- UITSLUITEND bedoeld als
    # input voor sectie 18 (Variant Perception). Gebruikt de base-case
    # FCF-groei uit de reverse-DCF als redelijk uitgangspunt indien
    # beschikbaar, anders de conservatieve terminal-groeivoet.
    intrinsic_value_result = None
    if "error" not in reverse_dcf_result:
        base_growth_assumption = reverse_dcf_result.get("implied_annual_fcf_growth", 0.025)
        intrinsic_value_result = compute_intrinsic_value_estimate(company_data, base_growth_assumption)
        if "error" in intrinsic_value_result:
            print(f"      Intrinsic-value-schatting niet mogelijk: {intrinsic_value_result['error']}", file=sys.stderr)
        else:
            print(
                f"      Intrinsic-value-schatting (alleen voor sectie 18): "
                f"${intrinsic_value_result['intrinsic_value_per_share']}/aandeel.",
                file=sys.stderr,
            )

    print("[2/3] Prompt opbouwen en analyse aanvragen bij Claude...", file=sys.stderr)
    user_prompt = build_analysis_prompt(company_data, peer_data, extra_context, sec_result,
                                         forensic_flags, verified_metrics, reverse_dcf_result,
                                         altman_result, macro_snapshot, previous_report, peer_comparison,
                                         piotroski_result, intrinsic_value_result,
                                         var_result, sharpe_sortino_result, rolling_beta_result,
                                         regime_result, insider_result, options_result, short_interest_result)

    client = anthropic.Anthropic()

    # De messages-lijst is het "geheugen" van dit gesprek met Claude. Elke
    # stap in de loop voegt hier iets aan toe: eerst onze vraag, dan Claude's
    # (deel-)antwoord, dan het tool-resultaat, enzovoort.
    messages = [{"role": "user", "content": user_prompt}]
    used_library_books = set()  # houdt bij welke boeken search_playbook daadwerkelijk teruggaf
    sensitivity_tool_results = []  # bewaart de ECHTE run_sensitivity_analysis-uitkomsten, om
    # later te controleren of de heatmap-grafiek in de tekst deze cijfers ook daadwerkelijk
    # correct overneemt i.p.v. ze te verzinnen/verkeerd over te schrijven (zie UUUU-bug)
    scenario_accumulator = {}  # dezelfde dict-referentie bij elke tool-aanroep, zodat kans-gewogen FCF over de 3 scenario's heen kan worden opgeteld

    start = time.time()
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_write_tokens = 0
    total_cache_read_tokens = 0

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        response = call_claude_with_retry(client, messages)
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        # cache_creation_input_tokens/cache_read_input_tokens bestaan alleen als
        # caching daadwerkelijk actief was op deze call -- getattr met default 0
        # voorkomt een crash als een oudere API-versie deze velden niet teruggeeft.
        total_cache_write_tokens += getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        total_cache_read_tokens += getattr(response.usage, "cache_read_input_tokens", 0) or 0

        # web_search is een SERVER tool: Anthropic voert 'm zelf uit, binnen
        # dezelfde call, en dat komt NOOIT als "tool_use"-blok terug (vandaar
        # dat dit los van de stop_reason-check hieronder staat -- anders zou
        # een websearch die zonder onze eigen tool erbij gebeurt, hier
        # onzichtbaar blijven, ook als stop_reason meteen "end_turn" is).
        for block in response.content:
            if block.type == "server_tool_use" and block.name == "web_search":
                query = block.input.get("query", "") if hasattr(block, "input") else ""
                print(f"      -> web_search({query!r})", file=sys.stderr)

        # stop_reason vertelt WAAROM Claude gestopt is met genereren.
        # "tool_use" betekent: Claude wil een tool aanroepen en wacht op het
        # resultaat voordat hij verdergaat. Alles anders (bijv. "end_turn")
        # betekent: Claude is klaar met zijn antwoord.
        if response.stop_reason != "tool_use":
            text_blocks = [b.text for b in response.content if b.type == "text"]
            analysis_text = "\n".join(text_blocks)
            if response.stop_reason == "max_tokens":
                print(
                    f"[2/3] LET OP: output afgekapt -- MAX_ANALYSIS_TOKENS "
                    f"({MAX_ANALYSIS_TOKENS}) was niet genoeg voor dit rapport. "
                    f"Verhoog MAX_ANALYSIS_TOKENS in analyst_agent.py en probeer opnieuw.",
                    file=sys.stderr,
                )
            # Toevoegen aan de geschiedenis, ook al stopt de loop hierna --
            # nodig zodat de zelfcheck-beurt hierna een geldige, correct
            # afwisselende assistant/user-conversatie aantreft.
            messages.append({"role": "assistant", "content": response.content})
            break

        print(f"[2/3] Ronde {round_num}: Claude roept een tool aan...", file=sys.stderr)

        # Claude's antwoord (inclusief de tool-aanroep) moet je terugzetten
        # in de messages-lijst -- anders "vergeet" het model dat het deze
        # tool al aanriep en waarom.
        messages.append({"role": "assistant", "content": response.content})

        # Eén Claude-antwoord kan meerdere tool-aanroepen tegelijk bevatten
        # (bijv. nieuws opvragen voor 2 verschillende tickers). Daarom loopt
        # dit over alle blokken, niet alleen het eerste.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"      -> {block.name}({block.input})", file=sys.stderr)
            result = run_tool(block.name, block.input,
                               context={"sec_result": sec_result, "reverse_dcf_result": reverse_dcf_result,
                                        "scenario_accumulator": scenario_accumulator, "client": client})
            if block.name == "search_playbook" and isinstance(result, dict) and "fragments" in result:
                for fragment in result["fragments"]:
                    used_library_books.add(fragment["book"])
            if block.name == "run_sensitivity_analysis" and isinstance(result, dict) and "sensitivities" in result:
                sensitivity_tool_results.append(result)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,  # koppelt het resultaat aan de juiste aanroep
                "content": str(result),
            })

        # De tool-resultaten gaan terug als een user-bericht -- dat is de
        # vaste vorm die de API verwacht voor tool_result blokken.

        if not tool_results:
            # Randgeval, live gezien bij Alcoa: stop_reason was "tool_use",
            # maar er zat geen enkel daadwerkelijk tool_use-blok in het
            # antwoord (bijv. na een server-side websearch zonder vervolg-
            # actie). Een LEEG user-bericht wordt door de API hard geweigerd
            # ("must have non-empty content") -- dat brak de hele run.
            # Herstel: haal de zojuist toegevoegde, kapotte assistant-beurt
            # er weer af, en probeer de ronde gewoon opnieuw met dezelfde
            # gespreksstaat als ervoor. MAX_TOOL_ROUNDS heeft ruim voldoende
            # marge om dit een paar keer te kunnen opvangen.
            messages.pop()
            print(
                "[2/3] LET OP: model gaf 'tool_use' als stopreden zonder een "
                "daadwerkelijke tool-aanroep. Ronde wordt opnieuw geprobeerd.",
                file=sys.stderr,
            )
            continue

        messages.append({"role": "user", "content": tool_results})
    else:
        raise RuntimeError(
            f"Meer dan {MAX_TOOL_ROUNDS} tool-rondes nodig -- iets loopt vast, "
            f"gestopt als veiligheidsmaatregel."
        )

    elapsed = time.time() - start

    if not analysis_text.strip():
        raise RuntimeError(
            "Claude gaf een lege response terug -- geen tekst om op te slaan."
        )

    # Ingebouwde zelfcheck, VOORDAT de (duurdere) externe reviewers het zien.
    # Dit is bewust GEEN nieuwe, losse call met een eigen system prompt (zoals
    # de reviewers) -- het is een extra beurt in DEZELFDE, al-lopende
    # gesprekssessie, zodat de al-opgebouwde cache (systeemprompt + tool-
    # geschiedenis) hergebruikt wordt en dit veel goedkoper is dan een volle
    # correctieronde. Vergelijkbaar idee als zelfconsistentie-sampling, maar
    # toegepast op het hele rapport i.p.v. één score.
    print("[3/3] Interne zelfcheck (vóór de externe kwaliteitscontrole)...", file=sys.stderr)
    self_check_prompt = (
        "Lees het rapport dat je hierboven schreef nog één keer terug, "
        "UITSLUITEND op interne tegenstrijdigheden: hetzelfde specifieke "
        "feit (een datum, naam, of bedrag bij een genoemde gebeurtenis) dat "
        "op meer dan één plek anders wordt vermeld, of een berekening/brug "
        "(zoals een FCF-brug) die niet optelt tot een cijfer dat je elders "
        "al noemt. Vind je zoiets, herschrijf dan het VOLLEDIGE rapport "
        "(alle 18 secties, zelfde sectienummers en -titels) met de "
        "correctie -- verwijder het probleem, verzin er geen nieuwe "
        "verklaring bij. Vind je niets, geef het rapport dan ONVERANDERD "
        "opnieuw terug, volledig, alle 18 secties.\n\n"
        "BELANGRIJK, dit is geen bijzaak: je antwoord IS het rapport, zelf, "
        "letterlijk beginnend bij '1.'. Zet er NOOIT een zin voor of "
        "erdoorheen die je eigen leesproces beschrijft, zoals 'Ik lees het "
        "rapport terug op interne tegenstrijdigheden', 'Gevonden "
        "inconsistentie: ...', of 'Ik corrigeer de grafiek...' -- dat is "
        "precies het soort proces-narratie dat nergens in het eindrapport "
        "hoort, en gebeurde hier eerder abusievelijk wél. Geen inleidende "
        "zin, geen samenvatting van wat je wel of niet hebt aangepast, "
        "geen commentaar op je eigen stap -- alleen de 18 sectiekoppen en "
        "hun inhoud, direct beginnend bij '1.'."
    )
    messages.append({"role": "user", "content": self_check_prompt})
    self_check_response = call_claude_with_retry(client, messages)
    total_input_tokens += self_check_response.usage.input_tokens
    total_output_tokens += self_check_response.usage.output_tokens
    total_cache_write_tokens += getattr(self_check_response.usage, "cache_creation_input_tokens", 0) or 0
    total_cache_read_tokens += getattr(self_check_response.usage, "cache_read_input_tokens", 0) or 0

    if self_check_response.stop_reason == "tool_use":
        # Onwaarschijnlijk voor een leesbeurt, maar geen reden om hier een
        # hele nieuwe tool-lus voor op te tuigen -- val terug op de tekst
        # van vóór de zelfcheck, die is en blijft geldig.
        print(
            "[3/3] Zelfcheck riep onverwacht een tool aan -- oorspronkelijke "
            "tekst behouden.",
            file=sys.stderr,
        )
    else:
        self_check_text = "\n".join(
            b.text for b in self_check_response.content if b.type == "text"
        ).strip()
        if self_check_text:
            analysis_text = self_check_text

    print(
        f"[3/3] Klaar in {elapsed:.1f}s "
        f"({total_input_tokens} input / {total_output_tokens} output tokens totaal, "
        f"waarvan {total_cache_write_tokens} cache-write en "
        f"{total_cache_read_tokens} cache-read tokens).",
        file=sys.stderr,
    )

    print("[4/4] Kwaliteitscontrole...", file=sys.stderr)
    review, usage = review_report(client, analysis_text)
    total_input_tokens += usage["input"]
    total_output_tokens += usage["output"]
    total_cache_write_tokens += usage["cache_write"]
    total_cache_read_tokens += usage["cache_read"]

    if review["approved"]:
        print("[4/4] Goedgekeurd door kwaliteitscontrole.", file=sys.stderr)
    else:
        print("[4/4] Kwaliteitscontrole vond issues:", file=sys.stderr)
        for issue in review["issues"]:
            print(f"      - {issue}", file=sys.stderr)

        for round_num in range(1, MAX_REVISION_ROUNDS + 1):
            print(f"[4/4] Zelfcorrectie ronde {round_num}: rapport herschrijven...", file=sys.stderr)
            analysis_text, usage = revise_report(client, analysis_text, review["issues"])
            total_input_tokens += usage["input"]
            total_output_tokens += usage["output"]
            total_cache_write_tokens += usage["cache_write"]
            total_cache_read_tokens += usage["cache_read"]

            print(f"[4/4] Kwaliteitscontrole na herschrijving...", file=sys.stderr)
            review, usage = review_report(client, analysis_text)
            total_input_tokens += usage["input"]
            total_output_tokens += usage["output"]
            total_cache_write_tokens += usage["cache_write"]
            total_cache_read_tokens += usage["cache_read"]

            if review["approved"]:
                print("[4/4] Goedgekeurd na zelfcorrectie.", file=sys.stderr)
                break
            print(f"[4/4] Nog steeds issues na ronde {round_num}:", file=sys.stderr)
            for issue in review["issues"]:
                print(f"      - {issue}", file=sys.stderr)
        else:
            print(
                f"[4/4] LET OP -- na {MAX_REVISION_ROUNDS} zelfcorrectie-poging(en) nog "
                f"steeds issues. Rapport wordt gemarkeerd voor handmatige review.",
                file=sys.stderr,
            )

    # Deterministisch vangnet: check op de duidelijkste vorm van "losse
    # externe koersdoel/rating" ONGEACHT wat de LLM-reviewer concludeerde.
    # Dit voorkomt dat we voor de 4e keer op deze exacte fout vertrouwen op
    # een instructie die eerder 3x niet standhield.
    # Sectie 18 ("Variant Perception") is de bewuste, geïsoleerde uitzondering
    # op de neutraliteitsregel achter deze check -- daar mag een bank-rating
    # of koersdoel wél ter sprake komen als onderbouwing van een marktvermoeden.
    # We knippen sectie 18 er daarom uit VOORDAT we op dit patroon zoeken, zodat
    # de check zijn oorspronkelijke, strikte werking op secties 1-17 behoudt.
    text_excluding_section_18 = re.split(r"\n18\.\s*Variant Perception", analysis_text)[0]

    bank_match = _BANK_RATING_PATTERN.search(text_excluding_section_18)
    if bank_match and review["approved"]:
        review = {"approved": False, "issues": review.get("issues", [])}
        review["issues"].append(
            f"[Automatische check] Mogelijk een los extern koersdoel/rating "
            f"zonder tegenwicht gevonden: \"...{bank_match.group(0)}...\". "
            f"Dit patroon is eerder meerdere keren gemist door de kwaliteitscontrole."
        )
        print(
            f"[4/4] LET OP -- automatische check vond alsnog een bank-koersdoel-patroon, "
            f"ondanks goedkeuring door de reviewer. Rapport wordt alsnog gemarkeerd.",
            file=sys.stderr,
        )

    # Tweede deterministisch vangnet: controleert of kerncijfers die het
    # rapport zelf noemt (FCF, operating margin) overeenkomen met wat we al
    # hebben berekend -- ving bij een echte testrun een fout die de
    # LLM-reviewer zelf miste (het juiste cijfer was meegegeven, maar werd
    # verderop toch fout geciteerd).
    consistency_issues = check_output_consistency(analysis_text, verified_metrics, peers=peers,
                                                    sensitivity_tool_results=sensitivity_tool_results)
    if consistency_issues and review["approved"]:
        review = {"approved": False, "issues": review.get("issues", [])}
        for issue in consistency_issues:
            review["issues"].append(f"[Automatische check] {issue['check']}: {issue['message']}")
        print(
            f"[4/4] LET OP -- output-controle vond {len(consistency_issues)} cijfermatige "
            f"inconsistentie(s), ondanks goedkeuring door de reviewer.",
            file=sys.stderr,
        )

    # Derde deterministisch vangnet: als onze eigen code de Altman Z-Score
    # niet kon berekenen, mag Claude er ook geen eigen vervangende schatting
    # van maken -- dit ving een echte fout waarbij Claude zelf een "Estimated
    # Altman Z-Score" verzon (compleet met gauge-grafiek) terwijl de code
    # expliciet had gemeld dat de benodigde data ontbrak.
    if "error" in altman_result and re.search(r"altman\s*z", analysis_text, re.IGNORECASE):
        review = {"approved": False, "issues": review.get("issues", [])}
        review["issues"].append(
            "[Automatische check] Altman Z-Score genoemd in de tekst, terwijl de code "
            f"expliciet meldde dat deze niet berekenbaar was ({altman_result['error']}). "
            "Verwijder elke vermelding of eigen schatting van een Altman Z-Score."
        )
        print(
            "[4/4] LET OP -- Altman Z-Score genoemd ondanks dat de code 'm niet kon "
            "berekenen. Rapport wordt alsnog gemarkeerd.",
            file=sys.stderr,
        )

    print("[5/5] Executive summary schrijven...", file=sys.stderr)
    executive_summary = ""
    try:
        summary_prompt = build_executive_summary_prompt(analysis_text, previous_report)
        summary_response = call_claude_with_retry_simple(
            client, EXECUTIVE_SUMMARY_SYSTEM_PROMPT, summary_prompt, max_tokens=400,
        )
        _log_stage_usage("Executive summary", summary_response)
        executive_summary = "".join(b.text for b in summary_response.content if b.type == "text").strip()
    except Exception as e:
        print(f"      Executive summary kon niet gegenereerd worden: {e}", file=sys.stderr)

    save_report_snapshot(ticker, analysis_text, verified_metrics)
    print(f"      Trackrecord opgeslagen voor {ticker} (voor een toekomstige hernieuwde analyse).", file=sys.stderr)

    lineage_manifest = build_lineage_manifest(
        sec_result, verified_metrics, altman_result, piotroski_result,
        reverse_dcf_result, macro_snapshot, peer_comparison,
    )
    print(f"      Herkomst-manifest opgebouwd: {len(lineage_manifest)} cijfers met traceerbare bron.", file=sys.stderr)

    print("[5/5] Merk-kleuren opzoeken voor de HTML-styling...", file=sys.stderr)
    colors, usage = get_brand_colors(client, ticker, company_data.get("long_name"))
    total_input_tokens += usage["input"]
    total_output_tokens += usage["output"]
    total_cache_write_tokens += usage["cache_write"]
    total_cache_read_tokens += usage["cache_read"]

    # Dit is het ENIGE cijfer dat 1-op-1 vergelijkbaar is met wat je in het
    # Claude Platform (console.anthropic.com) onder "usage" ziet voor deze
    # run -- alle eerdere per-stage regels hierboven zijn deelsommen.
    print(
        f"[TOTAAL] Volledige analyse: {total_input_tokens} input / {total_output_tokens} "
        f"output / {total_cache_write_tokens} cache-write / {total_cache_read_tokens} "
        f"cache-read tokens (alle calls samen).",
        file=sys.stderr,
    )

    return (analysis_text, review, colors, company_data.get("long_name"), company_data,
            peer_data, sorted(used_library_books), executive_summary, lineage_manifest)


# Deterministisch vangnet, los van de LLM-reviewer: dit patroon (naam van een
# bank + koersdoel/rating-taal) kwam 3 sessies op rij terug ondanks steeds
# scherpere instructies. In plaats van te blijven vertrouwen op "dit keer
# houdt de instructie wel stand," checken we hier hard, na alle
# correctierondes, op de duidelijkste vorm van dit specifieke probleem.
_BANK_RATING_PATTERN = re.compile(
    r"\b(Wells Fargo|Piper Sandler|Cantor Fitzgerald|Jefferies|Guggenheim|UBS|"
    r"Goldman Sachs|Morgan Stanley|JPMorgan|J\.P\. Morgan|Citi|Citigroup|"
    r"Bank of America|BofA|Barclays|Deutsche Bank|RBC|Evercore|Baird|"
    r"Oppenheimer|Raymond James|Stifel|Truist|KeyBanc|Needham)\b"
    r"[^.]{0,80}\b(price target|rating|initiat\w*|Buy|Sell|Overweight|"
    r"Underweight|Outperform|Neutral|Hold)\b",
    re.IGNORECASE,
)


def review_report(client: anthropic.Anthropic, analysis_text: str) -> tuple[dict, dict]:
    """Vier gespecialiseerde, PARALLELLE Claude-calls in plaats van één
    generalist die alles tegelijk moet checken: één puur op cijfermatige
    consistentie, één puur op neutraliteit, één puur op volledigheid/opmaak,
    en één puur op kruisverwijzingen (hetzelfde feit -- datum, naam, bedrag
    -- consistent vermeld op elke plek waar het terugkomt). Elke reviewer
    heeft een smallere, scherpere system prompt met alleen de checks die bij
    zijn rol horen -- de gedachte is dat een reviewer die maar op één ding
    hoeft te letten, meer vangt dan één die alles tegelijk moet doen (dezelfde
    reden waarom een mens dat ook niet in één keer goed doet). De kruis-
    verwijzing-reviewer is er specifiek bijgekomen na een live bug (Google-
    analyse): dezelfde Wiz-overname kreeg in twee secties een andere
    sluitingsdatum, iets wat de andere drie reviewers niet als hun taak
    zagen om te vangen.

    Geeft, net als voorheen, een (verdict, usage)-tuple terug -- verdict is
    goedgekeurd als ALLE VIER de reviewers akkoord zijn; issues van alle vier
    worden samengevoegd (met een label welke reviewer 'm vond); usage is de
    som van alle vier de calls."""
    review_prompt = build_review_prompt(analysis_text)
    reviewers = [
        ("Cijfers", REVIEW_SYSTEM_PROMPT_NUMBERS),
        ("Neutraliteit", REVIEW_SYSTEM_PROMPT_NEUTRALITY),
        ("Volledigheid/Opmaak", REVIEW_SYSTEM_PROMPT_COMPLETENESS),
        ("Kruisverwijzing", REVIEW_SYSTEM_PROMPT_CROSSREF),
    ]

    def _extract_json_block(text: str) -> dict | None:
        """Zoekt het eerste geldige {...}-blok in de tekst, ook als het model
        er (tegen de instructie in) een inleidende zin voor zette. Robuuster
        dan alleen code-fences strippen -- ving een echte bug waarbij de
        Cijfers-reviewer twee keer op rij met een inleidende zin + lopende
        tekst antwoordde in plaats van pure JSON."""
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return None
        return None

    def _run_one(label_and_prompt):
        label, system_prompt = label_and_prompt
        # UPDATE: 2000 -> 4000 was al eerder verhoogd (Alcoa) na een letterlijke
        # afkapping midden in de JSON. Bij PLTR gebeurde dit ALSNOG, nu bij 4000,
        # bij een rapport met ongewoon veel kruisverwijzing-bevindingen -- zelfde
        # patroon als bij MAX_ANALYSIS_TOKENS eerder: naarmate de reviewers grondiger
        # worden, groeit ook hun benodigde ruimte mee. Verder verhoogd naar 8000.
        response = call_claude_with_retry_simple(client, system_prompt, review_prompt, max_tokens=8000)
        usage = _log_stage_usage(f"Kwaliteitscontrole ({label})", response)
        raw_text = "".join(b.text for b in response.content if b.type == "text").strip()
        verdict = _extract_json_block(raw_text)
        if verdict is not None:
            return label, {
                "approved": bool(verdict.get("approved", False)),
                "issues": list(verdict.get("issues", [])),
            }, usage
        else:
            # Onderscheid tonen tussen 'afgekapt door max_tokens' (duidelijk
            # oorzaak, makkelijk te herkennen) en een andere JSON-fout, zodat
            # dit soort log-regel meteen de juiste diagnose meegeeft i.p.v.
            # alleen de kale, afgekapte tekst te tonen.
            reason = (
                f"reviewer-antwoord afgekapt door max_tokens-limiet (stop_reason: {response.stop_reason})"
                if response.stop_reason == "max_tokens"
                else "kon reviewer-antwoord niet als JSON lezen"
            )
            return label, {
                "approved": False,
                "issues": [f"{reason.capitalize()} -- volledige tekst genegeerd: {raw_text[:300]}"],
            }, usage

    all_approved = True
    all_issues = []
    total_usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for label, verdict, usage in executor.map(_run_one, reviewers):
            if not verdict["approved"]:
                all_approved = False
            for issue in verdict["issues"]:
                all_issues.append(f"[{label}] {issue}")
            for key in total_usage:
                total_usage[key] += usage[key]

    return {"approved": all_approved, "issues": all_issues}, total_usage


def _log_stage_usage(label: str, response) -> dict:
    """Print een tokenregel voor DEZE ene call, en geef de cijfers terug zodat
    de aanroeper ze kan optellen bij een lopend totaal. Dit bestond eerder
    alleen voor de hoofdanalyse-loop -- kwaliteitscontrole, zelfcorrectie en
    de merk-kleuren-call bleven onzichtbaar, terwijl die wel gewoon meetellen
    in wat het Claude Platform je factureert."""
    usage = response.usage
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    print(
        f"      [tokens] {label}: {usage.input_tokens} input / {usage.output_tokens} output"
        + (f" (+ {cache_write} cache-write / {cache_read} cache-read)" if cache_write or cache_read else ""),
        file=sys.stderr,
    )
    return {"input": usage.input_tokens, "output": usage.output_tokens,
            "cache_write": cache_write, "cache_read": cache_read}


def call_claude_with_retry_simple(client: anthropic.Anthropic, system: str, user_prompt: str,
                                   max_tokens: int = 2000):
    """Zelfde retry-idee als call_claude_with_retry, maar zonder tools --
    gebruikt voor calls die geen databronnen nodig hebben, alleen tekst die
    je meegeeft in user_prompt (kwaliteitscontrole en revisie).

    system krijgt hier een EXPLICIETE cache_control-markering: in alle drie
    de toepassingen van deze functie (reviewers, zelfcorrectie, executive
    summary) is de system-tekst een statische constante die hetzelfde blijft
    over meerdere aanroepen heen -- een reviewer draait tot 3x per run, en
    de zelfcorrectie-call gebruikt letterlijk dezelfde SYSTEM_PROMPT-tekst
    als de hoofdanalyse. Dat is precies waar caching op werkt.

    Ook hier op streaming overgezet (zelfde reden als call_claude_with_retry):
    de zelfcorrectie-aanroep gebruikt max_tokens=MAX_ANALYSIS_TOKENS (28000),
    en de SDK weigert dat zonder streaming.

    UPDATE: except-clausule ook hier verbreed naar Exception, zelfde reden en
    zelfde veilige scope als bij call_claude_with_retry (zie die docstring)."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
                messages=[{"role": "user", "content": user_prompt}],
                extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
            ) as stream:
                return stream.get_final_message()
        except Exception as e:
            last_error = e
            wait = 2 ** attempt
            print(
                f"Call mislukt (poging {attempt}/{MAX_RETRIES}): {e}. "
                f"Nieuwe poging over {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise RuntimeError(f"Call bleef falen na {MAX_RETRIES} pogingen") from last_error


def revise_report(client: anthropic.Anthropic, analysis_text: str, issues: list[str]) -> tuple[str, dict]:
    """Stuurt het rapport + gevonden issues terug naar Claude (in dezelfde
    analist-rol, SYSTEM_PROMPT) met het verzoek het rapport gecorrigeerd terug
    te geven. Gebruikt dezelfde max_tokens-ceiling als de oorspronkelijke
    analyse, want een herschreven rapport is net zo lang als het origineel.
    Geeft een (tekst, usage)-tuple terug."""
    revision_prompt = build_revision_prompt(analysis_text, issues)
    response = call_claude_with_retry_simple(
        client, SYSTEM_PROMPT, revision_prompt, max_tokens=MAX_ANALYSIS_TOKENS
    )
    usage = _log_stage_usage("Zelfcorrectie", response)
    if response.stop_reason == "max_tokens":
        print(
            f"[4/4] LET OP: herschreven rapport ook afgekapt op MAX_ANALYSIS_TOKENS "
            f"({MAX_ANALYSIS_TOKENS}).",
            file=sys.stderr,
        )
    return "".join(b.text for b in response.content if b.type == "text").strip(), usage


def save_output(ticker: str, long_name: str, peers: list[str] | None, extra_context: str,
                 analysis_text: str, review: dict, colors: dict,
                 company_data: dict, peer_data: dict | None, used_library_books: list[str],
                 executive_summary: str = "", lineage_manifest: list[dict] | None = None) -> str:
    os.makedirs("output", exist_ok=True)
    timestamp = datetime.now()
    date_str = timestamp.strftime("%Y-%m-%d")
    suffix = "" if review["approved"] else "-NEEDS_REVIEW"
    filename = f"output/{ticker}-deepdive-{date_str}{suffix}.html"

    html_content = render_html(
        ticker=ticker,
        long_name=long_name,
        timestamp_str=timestamp.strftime("%Y-%m-%d %H:%M"),
        peers=peers,
        extra_context=extra_context,
        review=review,
        analysis_text=analysis_text,
        colors=colors,
        company_data=company_data,
        peer_data=peer_data,
        used_library_books=used_library_books,
        executive_summary=executive_summary,
        lineage_manifest=lineage_manifest,
    )

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)
    return filename


def main():
    check_api_key()

    parser = argparse.ArgumentParser(description="TCE Financial Analyst Agent")
    parser.add_argument("ticker", help="Ticker symbol, bijv. NKE, ASML.AS")
    parser.add_argument("--peers", nargs="*", default=None,
                         help="Ticker-symbolen van concurrenten voor ratio-vergelijking")
    parser.add_argument("--context", default="",
                         help="Extra context/instructies voor deze specifieke analyse")
    args = parser.parse_args()

    try:
        analysis, review, colors, long_name, company_data, peer_data, used_library_books, executive_summary, lineage_manifest = run_analysis(
            args.ticker, args.peers, args.context
        )
    except RuntimeError as e:
        sys.exit(f"FOUT: {e}")

    path = save_output(args.ticker, long_name, args.peers, args.context, analysis, review,
                        colors, company_data, peer_data, used_library_books, executive_summary,
                        lineage_manifest)
    print(f"\nRapport opgeslagen: {path}")


if __name__ == "__main__":
    main()
