"""
consistency_check.py
De ontbrekende schakel uit "The Analyst's Method": we geven Claude nu wel
al de juiste, vooraf berekende cijfers (forensics.py), maar we controleerden
tot nu toe nooit of die cijfers ook daadwerkelijk correct terugkomen in de
uiteindelijke tekst. Bij een echte testrun (Alcoa) kreeg Claude het juiste
FCF-cijfer aangereikt, en gebruikte het toch een ander, fout cijfer verderop
in het rapport.

Dit haalt de kerncijfers die het rapport ZELF noemt uit de platte tekst, en
vergelijkt ze met wat we al zeker weten. Dit is bewust GEEN vervanging van
de LLM-reviewer -- het is een aanvullend, deterministisch vangnet specifiek
voor de cijfers die we zelf al hebben uitgerekend.
"""

import json
import re

DOLLAR_PATTERN = re.compile(r'\$\s?([\d,]+\.?\d*)\s*(million|billion|M|B)?', re.IGNORECASE)
PERCENT_PATTERN = re.compile(r'(-?\d+\.?\d*)\s?%')
RATIO_PATTERN = re.compile(r'(-?\d+\.?\d*)\s?x\b', re.IGNORECASE)


def _find_ratios_near(text: str, keywords: list[str], window: int = 80) -> list[float]:
    """Zoekt 'X.XXx'-ratio's (bijv. '8.82x', of een negatieve '-0.6x' bij
    een netto-kaspositie) dicht bij een van de keywords. Pakt per vermelding
    ALLEEN de dichtstbijzijnde match, niet alles binnen het venster -- anders
    kan een andere, toevallig nabije ratio (bijv. bij twee cijfers die in
    dezelfde zin worden 'verbonden', zoals onze eigen schrijfregels juist
    aanmoedigen) ten onrechte als tegenstrijdigheid worden aangemerkt."""
    found = []
    text_lower = text.lower()
    for keyword in keywords:
        for m in re.finditer(re.escape(keyword.lower()), text_lower):
            start, end = max(0, m.start() - window), min(len(text), m.end() + window)
            candidates = list(RATIO_PATTERN.finditer(text[start:end]))
            if not candidates:
                continue
            keyword_mid = m.start() + (m.end() - m.start()) / 2 - start
            closest = min(candidates, key=lambda c: abs((c.start() + c.end()) / 2 - keyword_mid))
            try:
                found.append(float(closest.group(1)))
            except ValueError:
                continue
    return found


def _find_dollar_figures_near(text: str, keywords: list[str], window: int = 100) -> list[float]:
    """Zoekt dollarbedragen (bijv. "$433 million", "$1.2B") die dicht bij
    een van de keywords in de tekst staan. Geeft de waarden terug in
    absolute dollars."""
    found = []
    text_lower = text.lower()
    for keyword in keywords:
        for m in re.finditer(re.escape(keyword.lower()), text_lower):
            start, end = max(0, m.start() - window), min(len(text), m.end() + window)
            for dm in DOLLAR_PATTERN.finditer(text[start:end]):
                try:
                    value = float(dm.group(1).replace(",", ""))
                except ValueError:
                    continue
                unit = (dm.group(2) or "").lower()
                if unit in ("million", "m"):
                    value *= 1e6
                elif unit in ("billion", "b"):
                    value *= 1e9
                found.append(value)
    return found


def _find_percentages_near(text: str, keywords: list[str], window: int = 80) -> list[float]:
    """Zoekt percentages (bijv. "5.9%") dicht bij een keyword. Geeft terug
    als fractie (5.9% -> 0.059). Zelfde 'alleen de dichtstbijzijnde match'-
    logica als _find_ratios_near, om dezelfde reden."""
    found = []
    text_lower = text.lower()
    for keyword in keywords:
        for m in re.finditer(re.escape(keyword.lower()), text_lower):
            start, end = max(0, m.start() - window), min(len(text), m.end() + window)
            candidates = list(PERCENT_PATTERN.finditer(text[start:end]))
            if not candidates:
                continue
            keyword_mid = m.start() + (m.end() - m.start()) / 2 - start
            closest = min(candidates, key=lambda c: abs((c.start() + c.end()) / 2 - keyword_mid))
            try:
                found.append(float(closest.group(1)) / 100)
            except ValueError:
                continue
    return found


def _extract_metric_value(verified_metrics: dict, key: str):
    """Sommige geverifieerde cijfers zijn een dict ({'fiscal_year':..,
    'value':..}), andere een kaal getal -- deze helper haalt in beide
    gevallen de numerieke waarde eruit, of None als het cijfer ontbreekt."""
    entry = verified_metrics.get(key)
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _check_percentage_metric(issues: list, analysis_text: str, verified_metrics: dict,
                              key: str, label: str, keywords: list[str], tolerance: float) -> None:
    expected = _extract_metric_value(verified_metrics, key)
    if expected is None:
        return
    cited = _find_percentages_near(analysis_text, keywords)
    mismatches = sorted({round(v * 100, 1) for v in cited if abs(v - expected) > tolerance})
    if mismatches:
        issues.append({
            "check": f"{label}-consistentie (output-controle)",
            "message": (
                f"Het rapport noemt {label.lower()} van {mismatches}% die meer dan "
                f"{tolerance*100:.1f} procentpunt afwijken van de geverifieerde waarde "
                f"({expected*100:.1f}%). Controleer of het juiste, meegegeven cijfer overal "
                f"consistent is gebruikt."
            ),
        })


def _check_ratio_metric(issues: list, analysis_text: str, verified_metrics: dict,
                         key: str, label: str, keywords: list[str], tolerance: float) -> None:
    expected = _extract_metric_value(verified_metrics, key)
    if expected is None:
        return
    cited = _find_ratios_near(analysis_text, keywords)
    mismatches = sorted({v for v in cited if abs(v - expected) > tolerance})
    if mismatches:
        issues.append({
            "check": f"{label}-consistentie (output-controle)",
            "message": (
                f"Het rapport noemt {label.lower()} van {mismatches}x die meer dan "
                f"{tolerance}x afwijken van de geverifieerde waarde ({expected}x). Let vooral "
                f"op als het geverifieerde cijfer een ander TEKEN heeft -- een verkeerd teken "
                f"is per definitie fout, niet slechts een afrondingsverschil."
            ),
        })


# Uitbreiding na 3 losse live-bugs van hetzelfde patroon (operating margin,
# net debt/EBITDA, en de verzonnen peer-average) -- in plaats van te wachten
# tot de volgende geverifieerde ratio ook een keer stuk gaat, dekt dit nu
# systematisch ELK geverifieerd cijfer waarvoor dat praktisch is, met
# dezelfde twee generieke checkers hierboven.
PERCENTAGE_METRIC_CHECKS = [
    ("sec_net_margin", "Net margin", ["net margin", "nettomarge"]),
    ("sec_revenue_yoy_growth", "Omzetgroei YoY", ["revenue growth", "revenue increased", "omzetgroei"]),
    ("sec_net_income_yoy_growth", "Nettowinstgroei YoY", ["net income growth", "nettowinstgroei"]),
    ("sec_roic", "ROIC", ["roic", "return on invested capital"]),
    ("sec_roic_vs_wacc_spread", "ROIC-WACC spread", ["roic-wacc spread", "roic vs. wacc", "roic minus wacc"]),
]
RATIO_METRIC_CHECKS = [
    ("sec_interest_coverage_ratio", "Rentedekking", ["interest coverage"]),
    ("sec_normalized_ev_to_ebitda", "Genormaliseerde EV/EBITDA", ["ev/ebitda", "ev-to-ebitda", "enterprise value/ebitda", "ev to ebitda"]),
]


def check_output_consistency(analysis_text: str, verified_metrics: dict,
                              dollar_tolerance: float = 0.15,
                              pct_tolerance: float = 0.015,
                              ratio_tolerance: float = 0.3,
                              peers: list | None = None,
                              sensitivity_tool_results: list | None = None) -> list[dict]:
    """Vergelijkt cijfers die het rapport ZELF noemt met de vooraf berekende
    waarden. Geeft een lijst van issues terug bij een materieel verschil --
    dit is een hard, deterministisch signaal, geen giswerk van een reviewer."""
    issues = []

    if not peers:
        chart_pattern = re.compile(r"```chart\s*\n(.*?)\n```", re.DOTALL)
        for match in chart_pattern.finditer(analysis_text):
            try:
                chart = json.loads(match.group(1))
            except (json.JSONDecodeError, AttributeError):
                continue
            if chart.get("type") == "radar" and len(chart.get("series", [])) > 1:
                extra_names = [s.get("name", "") for s in chart["series"][1:]]
                issues.append({
                    "check": "Verzonnen peer-vergelijking (output-controle)",
                    "message": (
                        f"Het rapport bevat een radardiagram met een tweede reeks "
                        f"({extra_names}), maar er zijn geen --peers opgegeven voor deze "
                        f"analyse. Dit is een verzonnen vergelijking -- gebruik in dat geval "
                        f"maar EEN reeks (alleen het bedrijf zelf)."
                    ),
                })

    # De rest van de checks hieronder zijn afhankelijk van verified_metrics --
    # de peer-check en de gevoeligheidsanalyse-check hierboven/hieronder NIET,
    # dus die moeten altijd draaien, ook als een bedrijf (zoals OKLO) toevallig
    # geen enkel geverifieerd cijfer heeft.
    _check_sensitivity_chart_matches_tool(issues, analysis_text, sensitivity_tool_results)

    if not verified_metrics:
        return issues

    sec_fcf = verified_metrics.get("sec_free_cashflow")
    if sec_fcf and sec_fcf.get("value") not in (None, 0):
        expected = sec_fcf["value"]
        cited = _find_dollar_figures_near(analysis_text, ["free cash flow", "FCF"])
        mismatches = sorted({v for v in cited if abs(v - expected) / abs(expected) > dollar_tolerance})
        if mismatches:
            issues.append({
                "check": "FCF-consistentie (output-controle)",
                "message": (
                    f"Het rapport noemt FCF-bedrag(en) {[f'${v:,.0f}' for v in mismatches]} die meer dan "
                    f"{dollar_tolerance*100:.0f}% afwijken van de geverifieerde SEC-waarde "
                    f"(${expected:,.0f}, FY{sec_fcf['fiscal_year']}). Het juiste, meegegeven cijfer is "
                    f"kennelijk niet overal consistent gebruikt."
                ),
            })

    sec_margin = verified_metrics.get("sec_operating_margin")
    if sec_margin and sec_margin.get("value") is not None:
        expected = sec_margin["value"]
        cited = _find_percentages_near(analysis_text, ["operating margin"])
        mismatches = sorted({round(v * 100, 1) for v in cited if abs(v - expected) > pct_tolerance})
        if mismatches:
            issues.append({
                "check": "Operating margin-consistentie (output-controle)",
                "message": (
                    f"Het rapport noemt operating margin(s) van {mismatches}% die meer dan "
                    f"{pct_tolerance*100:.0f} procentpunt afwijken van de geverifieerde waarde "
                    f"({expected*100:.1f}%, FY{sec_margin['fiscal_year']}). Controleer of het juiste, "
                    f"meegegeven cijfer overal consistent is gebruikt."
                ),
            })

    sec_net_debt_ebitda = verified_metrics.get("sec_net_debt_to_ebitda")
    if sec_net_debt_ebitda is not None:
        expected = sec_net_debt_ebitda
        cited = _find_ratios_near(
            analysis_text, ["net debt/ebitda", "net debt to ebitda", "net debt-to-ebitda"]
        )
        mismatches = sorted({v for v in cited if abs(v - expected) > ratio_tolerance})
        if mismatches:
            issues.append({
                "check": "Net debt/EBITDA-consistentie (output-controle)",
                "message": (
                    f"Het rapport noemt net debt/EBITDA van {mismatches}x die meer dan "
                    f"{ratio_tolerance}x afwijken van de geverifieerde waarde "
                    f"({expected}x). Let vooral op als het geverifieerde cijfer NEGATIEF is "
                    f"(een netto-kaspositie) -- een positief genoemd cijfer is dan per definitie "
                    f"fout, niet slechts een afrondingsverschil."
                ),
            })

    for key, label, keywords in PERCENTAGE_METRIC_CHECKS:
        _check_percentage_metric(issues, analysis_text, verified_metrics, key, label, keywords, pct_tolerance)
    for key, label, keywords in RATIO_METRIC_CHECKS:
        _check_ratio_metric(issues, analysis_text, verified_metrics, key, label, keywords, ratio_tolerance)

    return issues


_SENSITIVITY_KEYWORD_MAP = {
    "revenue_growth_pct": ["omzetgroei", "revenue growth"],
    "operating_margin_pct": ["operating margin", "marge"],
    "capex_pct_of_revenue": ["capex"],
}


def _check_sensitivity_chart_matches_tool(issues: list, analysis_text: str,
                                           sensitivity_tool_results: list | None,
                                           tolerance: float = 0.05) -> None:
    """Vergelijkt een heatmap-grafiek voor gevoeligheidsanalyse in de tekst
    met de ECHTE, door run_sensitivity_analysis berekende cijfers -- vangt
    het geval waarin Claude de juiste tool aanroept, maar bij het overzetten
    naar de grafiek andere (verzonnen of verkeerd overgeschreven) getallen
    gebruikt. Reproduceerde een echte live bug (UUUU): de tool zelf rangschikte
    omzetgroei als de meest impactvolle aanname, maar de gerenderde grafiek
    toonde 'm juist als de MINST impactvolle, met heel andere bedragen."""
    if not sensitivity_tool_results:
        return

    chart_pattern = re.compile(r"```chart\s*\n(.*?)\n```", re.DOTALL)
    real = sensitivity_tool_results[-1]  # meestal maar 1 aanroep per rapport
    real_by_assumption = {}
    for s in real.get("sensitivities", []):
        real_by_assumption.setdefault(s["assumption"], {})[s["direction"]] = s["fcf_change_final_year"]

    for match in chart_pattern.finditer(analysis_text):
        try:
            chart = json.loads(match.group(1))
        except (json.JSONDecodeError, AttributeError):
            continue
        if chart.get("type") != "heatmap":
            continue
        rows, values = chart.get("rows", []), chart.get("values", [])
        if not rows or not values or len(rows) != len(values):
            continue

        for row_label, row_values in zip(rows, values):
            row_label_lower = str(row_label).lower()
            matched_key = next(
                (key for key, kws in _SENSITIVITY_KEYWORD_MAP.items() if any(kw in row_label_lower for kw in kws)),
                None,
            )
            if matched_key is None or matched_key not in real_by_assumption:
                continue
            real_pair = sorted(v for v in real_by_assumption[matched_key].values() if v is not None)
            chart_pair = sorted(v for v in row_values if v is not None)
            if len(real_pair) != 2 or len(chart_pair) != 2:
                continue
            mismatch = any(
                abs(r - c) / max(abs(r), 1) > tolerance for r, c in zip(real_pair, chart_pair)
            )
            if mismatch:
                issues.append({
                    "check": "Gevoeligheidsanalyse-grafiek komt niet overeen met tool-uitkomst (output-controle)",
                    "message": (
                        f"De heatmap-grafiek noemt voor '{row_label}' de waarden {row_values}, maar de "
                        f"ECHTE, door run_sensitivity_analysis berekende uitkomst voor deze aanname is "
                        f"{list(real_by_assumption[matched_key].values())}. Gebruik de exacte cijfers die "
                        f"de tool teruggaf -- verzin of herschrijf ze nooit."
                    ),
                })
