"""
render.py
Alles wat met de VISUELE kant van het rapport te maken heeft: merk-kleuren
opzoeken en de tekst van Claude verpakken in een HTML-bestand met TCE-
huisstijl. Bewust los van framework.py -- dat bestand gaat over de inhoud
(wat er geschreven wordt), dit bestand gaat over de vorm (hoe het eruitziet).
"""

import html
import json
import math
import re
import sys

import anthropic

# Fallback-palet: gebruikt als het model niet zeker genoeg is over de
# officiele merk-kleuren van het bedrijf. Let op: dit wordt NIET als
# achtergrond gebruikt (zie render_html) -- alleen als accentkleur, dus een
# "saai" fallback is hier geen probleem.
FALLBACK_COLORS = {"primary": "#2B2620", "secondary": "#B8935F", "accent": "#8A6E3F"}

BRAND_COLOR_SYSTEM_PROMPT = """Je zoekt de officiële merk-kleuren (brand colors) \
van beursgenoteerde bedrijven op met de web_search-tool -- gok niet op eigen \
kennis, zoek het echt op (bijv. het brand guidelines-document of de investor \
relations-pagina van het bedrijf).

Antwoord UITSLUITEND met geldige JSON, zonder markdown-codeblokken, in exact \
deze vorm:
{"primary": "#hexcode", "secondary": "#hexcode", "accent": "#hexcode", "confident": true of false}

Zet "confident" op false als je de officiële kleuren ook na zoeken niet met \
zekerheid kon vaststellen -- dan wordt er een fallback-palet gebruikt in \
plaats van een gok."""


def get_brand_colors(client: anthropic.Anthropic, ticker: str, long_name: str) -> tuple[dict, dict]:
    """Kleine, losse Claude-call die de merk-kleuren opvraagt, nu met een
    ECHTE websearch (max_uses=2, klein gehouden want dit is maar 1 los feitje
    per rapport) in plaats van Claude's eigen, soms verouderde of onzekere
    kennis. Bij twijfel of een parse-fout vallen we terug op FALLBACK_COLORS.
    Geeft een (colors, usage)-tuple terug, ook bij een fout (usage dan leeg)."""
    empty_usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    prompt = f"Bedrijf: {long_name or ticker} (ticker: {ticker})"
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=BRAND_COLOR_SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
            messages=[{"role": "user", "content": prompt}],
        )
        cache_write = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
        print(
            f"      [tokens] Merk-kleuren: {response.usage.input_tokens} input / "
            f"{response.usage.output_tokens} output",
            file=sys.stderr,
        )
        usage = {"input": response.usage.input_tokens, "output": response.usage.output_tokens,
                  "cache_write": cache_write, "cache_read": cache_read}

        raw = "".join(b.text for b in response.content if b.type == "text").strip()
        cleaned = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(cleaned)
        if not parsed.get("confident", False):
            return FALLBACK_COLORS, usage
        return {
            "primary": parsed.get("primary", FALLBACK_COLORS["primary"]),
            "secondary": parsed.get("secondary", FALLBACK_COLORS["secondary"]),
            "accent": parsed.get("accent", FALLBACK_COLORS["accent"]),
        }, usage
    except Exception:
        return FALLBACK_COLORS, empty_usage


def _strip_stray_markdown(text: str) -> str:
    """Vangnet: verwijdert markdown-opmaak die Claude soms toch toevoegt
    ondanks de instructie in SYSTEM_PROMPT (bijv. na een zelfcorrectie-
    herschrijving). Zonder dit brak **14. Titel** de sectieherkenning
    hieronder volledig -- dit voorkomt dat één afwijkend "**" het hele
    rapport laat terugvallen op de fallback (1 groot ongestructureerd blok)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # **vet** -> vet
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # # Kop -> Kop
    return text


def _split_into_sections(analysis_text: str) -> list[dict]:
    """Splitst de platte tekst van Claude op in secties, elk met een los
    sectienummer en titel -- zodat we het nummer apart kunnen stylen in de
    navigatie en als koptekst-element."""
    pattern = r"\n(?=\d{1,2}\.\s)"
    raw_sections = re.split(pattern, _strip_stray_markdown(analysis_text).strip())

    sections = []
    for raw in raw_sections:
        raw = raw.strip()
        if not raw:
            continue
        lines = raw.split("\n", 1)
        header_line = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""

        match = re.match(r"^(\d{1,2})\.\s*(.*)", header_line)
        if match:
            num, title = match.group(1), match.group(2).strip()
        else:
            num, title = "", header_line

        # Claude schrijft soms "Company Overview — What the Company Does, ..."
        # (titel + omschrijving aan elkaar) omdat die vorm ook in de prompt
        # staat als leidraad. We tonen bewust ALLEEN het stuk voor het
        # streepje -- dit is een harde knip in de code, niet afhankelijk van
        # of Claude zich aan de instructie houdt.
        title = re.split(r"\s[—–-]\s", title)[0].strip()

        sections.append({"num": num, "title": title, "body": body})

    # Vangnet: als het EERSTE herkende blok geen sectie "1" is, is het een
    # ongevraagde preambule (masthead-regel, "Now let me search..."-narratie,
    # etc.) die Claude er soms toch voor plakt, ondanks de instructie om dat
    # niet te doen. Dit gebeurde meerdere keren opnieuw ondanks expliciete
    # prompt-regels -- vandaar deze harde, deterministische knip in de code
    # in plaats van te vertrouwen op de instructie alleen.
    if sections and sections[0]["num"] != "1":
        sections = sections[1:]

    if len(sections) < 5:
        return [{"num": "", "title": "Analyse", "body": analysis_text.strip()}]
    return sections


def _paragraphs_html(body: str) -> str:
    """Zet platte alinea's om naar <p>-tags, met html.escape zodat tekens als
    & of < uit de financiele data de HTML niet kunnen breken."""
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and p.strip() != "---"]
    return "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)


def _fmt_money(x) -> str:
    """Zet een groot getal om naar een leesbare vorm ($1.2B, $340M, etc.)."""
    if x is None:
        return "–"
    abs_x = abs(x)
    if abs_x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if abs_x >= 1e6:
        return f"${x / 1e6:.1f}M"
    return f"${x:,.0f}"


def _fmt_pct(x) -> str:
    """yfinance geeft percentages als fractie (0.15), niet als 15 -- vandaar de *100."""
    if x is None:
        return "–"
    return f"{x * 100:.1f}%"


def _fmt_ratio(x) -> str:
    if x is None:
        return "–"
    return f"{x:.1f}"


def _sign_class(x) -> str:
    """Geeft een CSS-klasse terug voor kleurcodering: groen bij positief,
    rood bij negatief, neutraal bij None of nul."""
    if x is None or x == 0:
        return ""
    return "pos" if x > 0 else "neg"


# Vaste, merk-onafhankelijke kleuren voor de kerncijfer-kaartjes -- bewust
# NIET gekoppeld aan de (onvoorspelbare) merk-kleuren van het bedrijf, om
# hetzelfde leesbaarheidsprobleem als eerder te voorkomen. Puur decoratieve
# variatie tussen de kaartjes.
CARD_ACCENTS = ["#4A7C74", "#B8935F", "#4A6FA5", "#B25F45", "#5C8A5C", "#8A6E8F", "#6B8CAE", "#A67A3D"]


def _metrics_panel(company_data: dict) -> str:
    """Bouwt een kaartjes-grid met de belangrijkste kerncijfers uit
    data_fetch.py -- rechtstreeks uit de brondata, geen Claude-interpretatie
    nodig, dus altijd exact zo betrouwbaar als de yfinance-data zelf."""
    currency = company_data.get("currency") or ""
    price = company_data.get("current_price")
    price_str = f"{price:,.2f} {currency}".strip() if price is not None else "–"

    cards = [
        ("Current Price", price_str, ""),
        ("Market Cap", _fmt_money(company_data.get("market_cap")), ""),
        ("P/E (Trailing)", _fmt_ratio(company_data.get("trailing_pe")), ""),
        ("Net Margin", _fmt_pct(company_data.get("profit_margins")), ""),
        ("Return on Equity", _fmt_pct(company_data.get("return_on_equity")), ""),
        ("Revenue Growth", _fmt_pct(company_data.get("revenue_growth")),
         _sign_class(company_data.get("revenue_growth"))),
        ("Dividend Yield", _fmt_pct(company_data.get("dividend_yield")), ""),
        ("Debt/Equity", _fmt_ratio(company_data.get("debt_to_equity")), ""),
    ]

    card_html = "\n".join(
        f'<div class="metric-card" style="border-top-color:{CARD_ACCENTS[i % len(CARD_ACCENTS)]}">'
        f'<div class="metric-label">{html.escape(label)}</div>'
        f'<div class="metric-value {sign_class}">{html.escape(value)}</div>'
        f'</div>'
        for i, (label, value, sign_class) in enumerate(cards)
    )
    return f'<div class="metrics-grid">{card_html}</div>'


def _hero_snapshot(company_data: dict) -> str:
    """Kleine, compacte kerncijfer-set voor in de hero-header zelf (naast de
    volledige metrics-grid verderop in het rapport) -- geeft meteen bovenaan
    concrete, betrouwbare cijfers i.p.v. alleen tekst."""
    currency = company_data.get("currency") or ""
    price = company_data.get("current_price")
    price_str = f"{price:,.2f} {currency}".strip() if price is not None else "–"

    cells = [
        ("Share Price", price_str),
        ("Market Cap", _fmt_money(company_data.get("market_cap"))),
        ("P/E (Trailing)", _fmt_ratio(company_data.get("trailing_pe"))),
        ("Dividend Yield", _fmt_pct(company_data.get("dividend_yield"))),
    ]
    cells_html = "".join(
        f'<div class="snap-cell"><div class="lbl">{html.escape(label)}</div>'
        f'<div class="val">{html.escape(value)}</div></div>'
        for label, value in cells
    )
    return f'<div class="snap-grid">{cells_html}</div>'


def _peer_table(ticker: str, company_data: dict, peer_data: dict | None) -> str:
    """Bouwt een vergelijkingstabel: het geanalyseerde bedrijf (uitgelicht)
    plus elke peer, met dezelfde ratio's naast elkaar -- rechtstreeks uit de
    data_fetch.py-cijfers, precies wat sectie 6 van het framework vraagt."""
    if not peer_data:
        return ""

    def _row(name: str, d: dict, highlight: bool) -> str:
        rev_growth = d.get("revenue_growth")
        cls = "peer-row peer-highlight" if highlight else "peer-row"
        return (
            f'<tr class="{cls}">'
            f'<td class="peer-name">{html.escape(name)}</td>'
            f'<td>{_fmt_ratio(d.get("trailing_pe"))}</td>'
            f'<td>{_fmt_ratio(d.get("ev_to_ebitda"))}</td>'
            f'<td>{_fmt_pct(d.get("profit_margins"))}</td>'
            f'<td>{_fmt_pct(d.get("return_on_equity"))}</td>'
            f'<td>{_fmt_ratio(d.get("debt_to_equity"))}</td>'
            f'<td class="{_sign_class(rev_growth)}">{_fmt_pct(rev_growth)}</td>'
            f'</tr>'
        )

    rows = [_row(ticker, company_data, highlight=True)]
    for peer_ticker, peer_info in peer_data.items():
        if "error" in peer_info:
            continue
        name = peer_info.get("long_name") or peer_ticker
        rows.append(_row(name, peer_info, highlight=False))

    return f"""<div class="peer-table-wrap">
<table class="peer-table">
<thead><tr>
<th>Company</th><th>P/E</th><th>EV/EBITDA</th><th>Margin</th><th>ROE</th><th>Debt/EV</th><th>Revenue Growth</th>
</tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</div>"""


def _extract_charts(body: str) -> tuple[str, list[dict]]:
    """Zoekt naar ```chart ... ```-blokken in de tekst van Claude, parst ze
    als JSON, en verwijdert ze uit de platte tekst (anders zou de rauwe JSON
    als leestekst in het rapport verschijnen). Een blok dat niet als geldige
    JSON parseert wordt stil overgeslagen -- liever geen grafiek dan kapotte
    HTML of zichtbare rommel in het rapport."""
    pattern = re.compile(r"```chart\s*\n(.*?)\n```", re.DOTALL)
    charts = []

    def _collect(match):
        try:
            charts.append(json.loads(match.group(1)))
        except (json.JSONDecodeError, AttributeError):
            pass
        return ""

    cleaned = pattern.sub(_collect, body)
    return cleaned.strip(), charts


def _render_bar_chart(chart: dict) -> str:
    title = html.escape(chart.get("title", ""))
    unit = html.escape(chart.get("unit", ""))
    labels = chart.get("labels", [])
    values = chart.get("values", [])
    if not labels or not values:
        return ""
    max_v = max(values) or 1

    bars = "".join(
        f'<div class="bar-col">'
        f'<div class="bar-value">{html.escape(str(v))}{unit}</div>'
        f'<div class="bar-fill" style="height:{max(v / max_v * 100, 2):.0f}%; '
        f'background:{CARD_ACCENTS[i % len(CARD_ACCENTS)]}"></div>'
        f'<div class="bar-label">{html.escape(str(l))}</div>'
        f'</div>'
        for i, (l, v) in enumerate(zip(labels, values))
    )
    return f'<div class="chart-block"><div class="chart-title">{title}</div><div class="bar-row">{bars}</div></div>'


SCENARIO_COLORS = ["#3E7A4F", "var(--brand-2)", "#A6402F"]  # bull / base / bear -- base gebruikt de 2e merk-kleur


def _render_scenario_cards(chart: dict) -> str:
    title = html.escape(chart.get("title", ""))
    cases = chart.get("cases", [])
    if not cases:
        return ""

    cards = "".join(
        f'<div class="scenario-card" style="border-top-color:{SCENARIO_COLORS[i % len(SCENARIO_COLORS)]}">'
        f'<div class="scenario-label">{html.escape(c.get("label", ""))}</div>'
        f'<div class="scenario-value">{html.escape(c.get("value", ""))}</div>'
        f'<p class="scenario-desc">{html.escape(c.get("description", ""))}</p>'
        f'</div>'
        for i, c in enumerate(cases)
    )
    return f'<div class="chart-block"><div class="chart-title">{title}</div><div class="scenario-grid">{cards}</div></div>'


def _render_composition(chart: dict) -> str:
    title = html.escape(chart.get("title", ""))
    items = chart.get("items", [])
    if not items:
        return ""

    rows = "".join(
        f'<div class="comp-row">'
        f'<div class="comp-label">{html.escape(item.get("label", ""))}</div>'
        f'<div class="comp-bar-track"><div class="comp-bar-fill" '
        f'style="width:{float(item.get("pct", 0)):.0f}%; background:{CARD_ACCENTS[i % len(CARD_ACCENTS)]}"></div></div>'
        f'<div class="comp-pct">{html.escape(str(item.get("pct", "")))}%</div>'
        f'</div>'
        for i, item in enumerate(items)
    )
    return f'<div class="chart-block"><div class="chart-title">{title}</div><div class="comp-list">{rows}</div></div>'


VALUECHAIN_STATUS_COLORS = {
    "constrained": "#A6402F",
    "balanced": "var(--brand)",
    "surplus": "#3E7A4F",
}


def _render_valuechain(chart: dict) -> str:
    title = html.escape(chart.get("title", ""))
    stages = chart.get("stages", [])
    if not stages:
        return ""

    stage_blocks = []
    for i, stage in enumerate(stages):
        status = stage.get("status", "balanced")
        color = VALUECHAIN_STATUS_COLORS.get(status, "var(--ink-soft)")
        stage_blocks.append(
            f'<div class="vc-stage" style="border-top-color:{color}">'
            f'<div class="vc-label">{html.escape(stage.get("label", ""))}</div>'
            f'<div class="vc-status" style="color:{color}">{html.escape(status)}</div>'
            f'<div class="vc-note">{html.escape(stage.get("note", ""))}</div>'
            f'</div>'
        )
        if i < len(stages) - 1:
            stage_blocks.append('<div class="vc-arrow">→</div>')

    return f'<div class="chart-block"><div class="chart-title">{title}</div><div class="vc-row">{"".join(stage_blocks)}</div></div>'


def _render_timeline(chart: dict) -> str:
    title = html.escape(chart.get("title", ""))
    events = chart.get("events", [])
    if not events:
        return ""

    items = "".join(
        f'<div class="tl-item">'
        f'<div class="tl-year">{html.escape(str(e.get("year", "")))}</div>'
        f'<div class="tl-content">'
        f'<div class="tl-event">{html.escape(e.get("event", ""))}</div>'
        f'<div class="tl-detail">{html.escape(e.get("detail", ""))}</div>'
        f'</div></div>'
        for e in events
    )
    return f'<div class="chart-block"><div class="chart-title">{title}</div><div class="timeline">{items}</div></div>'


RISK_SEVERITY_COLORS = {
    "elevated": ("#3B1418", "#D96B5A"),
    "moderate": ("#3B2E10", "#C9A34A"),
    "low": ("#12301C", "#5FA871"),
}


def _render_risk_matrix(chart: dict) -> str:
    """Risicomatrix: een ECHTE 2D-raster (waarschijnlijkheid x impact, 1-5),
    met elk risico op zijn werkelijke positie geplaatst en kleurintensiteit
    die oploopt naar de rechterbovenhoek (hoge kans + hoge impact). Anders
    dan risk-table (een platte lijst) laat dit in een oogopslag zien WELKE
    risico's zich in de gevarenzone bevinden, niet alleen dat ze bestaan."""
    title = html.escape(chart.get("title", ""))
    risks = chart.get("risks", [])
    if not risks:
        return ""

    grid = {}
    for r in risks:
        likelihood = max(1, min(5, int(r.get("likelihood", 1))))
        impact = max(1, min(5, int(r.get("impact", 1))))
        grid.setdefault((likelihood, impact), []).append(str(r.get("name", "")))

    cells = []
    for impact in range(5, 0, -1):
        for likelihood in range(1, 6):
            score = likelihood * impact
            alpha = 0.08 + (score / 25) * 0.62
            names = grid.get((likelihood, impact), [])
            names_html = "<br>".join(html.escape(n) for n in names)
            cells.append(
                f'<div class="risk-matrix-cell" style="background:rgba(166,64,47,{alpha:.2f});">{names_html}</div>'
            )

    title_html = f'<div class="chart-title">{title}</div>' if title else ""
    return (
        f'<div class="chart-block risk-matrix-wrap">{title_html}'
        f'<div class="risk-matrix-axis-y">Impact →</div>'
        f'<div class="risk-matrix-grid">{"".join(cells)}</div>'
        f'<div class="risk-matrix-axis-x">Waarschijnlijkheid →</div>'
        f'</div>'
    )


def _render_grouped_bar(chart: dict) -> str:
    """Gegroepeerde staafdiagram: meerdere genoemde reeksen (bijv. Bedrijf/
    Peer A/Peer B/Sector) naast elkaar per categorie (bijv. per multiple:
    P/E, EV/EBITDA, P/S) -- waar de bestaande 'bar' en 'stacked-bar' types
    niet in voorzien (die zijn resp. enkele reeks en gestapeld, niet naast
    elkaar gegroepeerd)."""
    title = html.escape(chart.get("title", ""))
    categories = chart.get("categories", [])
    series = chart.get("series", [])
    unit = html.escape(chart.get("unit", ""))
    if not categories or not series or any(len(s.get("values", [])) != len(categories) for s in series):
        return ""

    all_values = [v for s in series for v in s["values"]]
    max_v = max(all_values) if all_values else 1
    max_v = max_v or 1
    colors = ["var(--brand)", "var(--brand-2)"] + CARD_ACCENTS
    n_series = len(series)

    group_html = []
    for c_idx, cat in enumerate(categories):
        bars = "".join(
            f'<div class="grouped-bar" style="height:{max(2, (s["values"][c_idx] / max_v) * 100):.1f}%; '
            f'background:{colors[s_idx % len(colors)]};" title="{html.escape(s.get("name", ""))}: {s["values"][c_idx]}{unit}"></div>'
            for s_idx, s in enumerate(series)
        )
        group_html.append(
            f'<div class="grouped-bar-group"><div class="grouped-bar-bars">{bars}</div>'
            f'<div class="grouped-bar-label">{html.escape(str(cat))}</div></div>'
        )

    legend = "".join(
        f'<div class="radar-legend-item"><span class="regime-swatch" style="background:{colors[i % len(colors)]};"></span>{html.escape(s.get("name", ""))}</div>'
        for i, s in enumerate(series)
    )

    title_html = f'<div class="chart-title">{title}</div>' if title else ""
    return (
        f'<div class="chart-block">{title_html}'
        f'<div class="radar-legend" style="margin-bottom:12px;">{legend}</div>'
        f'<div class="grouped-bar-chart">{"".join(group_html)}</div>'
        f'</div>'
    )


def _render_risk_table(chart: dict) -> str:
    title = html.escape(chart.get("title", ""))
    rows = chart.get("rows", [])
    if not rows:
        return ""

    row_html = []
    for r in rows:
        severity = r.get("severity", "moderate")
        bg, fg = RISK_SEVERITY_COLORS.get(severity, RISK_SEVERITY_COLORS["moderate"])
        row_html.append(
            f'<tr>'
            f'<td class="risk-name">{html.escape(r.get("name", ""))}</td>'
            f'<td class="risk-horizon">{html.escape(r.get("horizon", ""))}</td>'
            f'<td><span class="risk-pill" style="background:{bg};color:{fg}">'
            f'{html.escape(severity.capitalize())}</span></td>'
            f'<td class="risk-note">{html.escape(r.get("note", ""))}</td>'
            f'</tr>'
        )
    return f"""<div class="chart-block"><div class="chart-title">{title}</div>
<table class="risk-table"><thead><tr><th>Risk</th><th>Horizon</th><th>Severity</th><th>Note</th></tr></thead>
<tbody>{''.join(row_html)}</tbody></table></div>"""


def _render_callout(chart: dict) -> str:
    tag = html.escape(chart.get("tag", ""))
    text = html.escape(chart.get("text", ""))
    if not text:
        return ""
    tag_html = f'<div class="callout-tag">{tag}</div>' if tag else ""
    return f'<div class="chart-block callout">{tag_html}<p class="callout-text">{text}</p></div>'


def _render_rating(chart: dict) -> str:
    label = html.escape(chart.get("label", ""))
    score = int(chart.get("score", 0))
    max_score = int(chart.get("max", 5))
    note = html.escape(chart.get("note", ""))
    dots = "".join(
        f'<span class="dot {"dot-full" if i < score else "dot-empty"}">●</span>'
        for i in range(max_score)
    )
    return (
        f'<div class="chart-block rating-block">'
        f'<div class="rating-label">{label}</div>'
        f'<div class="rating-dots">{dots}</div>'
        f'<p class="rating-note">{note}</p>'
        f'</div>'
    )


def _render_waterfall(chart: dict) -> str:
    """Voor een cijfermatige 'brug' (bijv. netto winst -> vrije kasstroom via
    een reeks correcties) -- past direct op wat forensics.py al berekent."""
    title = html.escape(chart.get("title", ""))
    start_label = html.escape(chart.get("start_label", ""))
    start_value = chart.get("start_value")
    end_label = html.escape(chart.get("end_label", ""))
    steps = chart.get("steps", [])
    if start_value is None or not steps:
        return ""

    running = start_value
    rows = [f'<div class="waterfall-row waterfall-start"><span class="wf-label">{start_label}</span>'
            f'<span class="wf-value">{start_value:,.0f}</span></div>']
    for step in steps:
        label = html.escape(str(step.get("label", "")))
        value = step.get("value", 0)
        running += value
        sign_class = "wf-positive" if value >= 0 else "wf-negative"
        sign = "+" if value >= 0 else ""
        rows.append(
            f'<div class="waterfall-row"><span class="wf-label">{label}</span>'
            f'<span class="wf-value {sign_class}">{sign}{value:,.0f}</span></div>'
        )
    rows.append(f'<div class="waterfall-row waterfall-end"><span class="wf-label">{end_label}</span>'
                f'<span class="wf-value">{running:,.0f}</span></div>')

    return f'<div class="chart-block"><div class="chart-title">{title}</div><div class="waterfall">{"".join(rows)}</div></div>'


def _render_gauge(chart: dict) -> str:
    """Voor een enkele score binnen zones (bijv. de Altman Z-Score) --
    toont de waarde als marker op een gesegmenteerde balk."""
    title = html.escape(chart.get("title", ""))
    value = chart.get("value")
    min_v = chart.get("min", 0)
    max_v = chart.get("max", 5)
    zones = chart.get("zones", [])
    note = html.escape(chart.get("note", ""))
    if value is None or not zones or max_v <= min_v:
        return ""

    span = max_v - min_v
    position_pct = max(0, min(100, (value - min_v) / span * 100))
    zone_segments = []
    prev_max = min_v
    for zone in zones:
        zone_max = zone.get("max", max_v)
        width_pct = max(0, (zone_max - prev_max) / span * 100)
        zone_segments.append(
            f'<div class="gauge-zone" style="width:{width_pct:.1f}%; background:{html.escape(zone.get("color", "var(--rule)"))};" '
            f'title="{html.escape(zone.get("label", ""))}"></div>'
        )
        prev_max = zone_max

    note_html = f'<p class="gauge-note">{note}</p>' if note else ""
    return (
        f'<div class="chart-block"><div class="chart-title">{title}</div>'
        f'<div class="gauge-track">{"".join(zone_segments)}'
        f'<div class="gauge-marker" style="left:{position_pct:.1f}%;"></div></div>'
        f'<div class="gauge-value">{value}</div>'
        f'{note_html}'
        f'</div>'
    )


def _render_heatmap(chart: dict) -> str:
    """Voor een rijen x kolommen-grid van waarden (bijv. de gevoeligheids-
    analyse: elke aanname x richting) -- kleurintensiteit toont de omvang
    en het teken van het effect."""
    title = html.escape(chart.get("title", ""))
    rows = chart.get("rows", [])
    cols = chart.get("cols", [])
    values = chart.get("values", [])
    if not rows or not cols or not values:
        return ""

    flat = [abs(v) for row in values for v in row if v is not None]
    max_abs = max(flat) if flat else 1

    header_cells = "".join(f'<div class="heatmap-cell heatmap-header">{html.escape(str(c))}</div>' for c in cols)
    body_rows = []
    for row_label, row_values in zip(rows, values):
        cells = [f'<div class="heatmap-cell heatmap-rowlabel">{html.escape(str(row_label))}</div>']
        for v in row_values:
            if v is None:
                cells.append('<div class="heatmap-cell"></div>')
                continue
            intensity = min(1.0, abs(v) / max_abs) if max_abs else 0
            color = f"rgba(166,64,47,{intensity:.2f})" if v < 0 else f"rgba(74,124,116,{intensity:.2f})"
            cells.append(f'<div class="heatmap-cell" style="background:{color};">{v:,.0f}</div>')
        body_rows.append(f'<div class="heatmap-row">{"".join(cells)}</div>')

    return (
        f'<div class="chart-block"><div class="chart-title">{title}</div>'
        f'<div class="heatmap"><div class="heatmap-row">'
        f'<div class="heatmap-cell heatmap-header"></div>{header_cells}</div>'
        f'{"".join(body_rows)}</div></div>'
    )


def _render_metric_cards(chart: dict) -> str:
    """Rij losse kaarten met een label, een groot cijfer, en optioneel een
    trendpijl -- voor een snel overzicht van kerncijfers (sectie 1 of 5),
    visueel anders dan een tabel."""
    title = html.escape(chart.get("title", ""))
    cards = chart.get("cards", [])
    if not cards:
        return ""
    card_html = []
    for card in cards:
        label = html.escape(str(card.get("label", "")))
        value = html.escape(str(card.get("value", "")))
        trend = card.get("trend")  # "up", "down", of None
        trend_html = ""
        if trend in ("up", "down"):
            arrow = "▲" if trend == "up" else "▼"
            trend_class = "trend-up" if trend == "up" else "trend-down"
            trend_detail = html.escape(str(card.get("trend_detail", "")))
            trend_html = f'<span class="metric-trend {trend_class}">{arrow} {trend_detail}</span>'
        card_html.append(
            f'<div class="metric-card"><div class="metric-card-label">{label}</div>'
            f'<div class="metric-card-value">{value}</div>{trend_html}</div>'
        )
    return f'<div class="chart-block"><div class="chart-title">{title}</div><div class="metric-cards">{"".join(card_html)}</div></div>'


def _render_stacked_bar(chart: dict) -> str:
    """Eén balk opgedeeld in gekleurde segmenten -- voor een totaal dat uit
    onderdelen bestaat (bijv. kapitaalstructuur: schuld vs. eigen vermogen),
    anders dan 'composition' (aparte balken per categorie)."""
    title = html.escape(chart.get("title", ""))
    segments = chart.get("segments", [])
    if not segments:
        return ""
    total = sum(s.get("value", 0) for s in segments) or 1
    bar_html = []
    legend_html = []
    for i, seg in enumerate(segments):
        label = html.escape(str(seg.get("label", "")))
        value = seg.get("value", 0)
        pct = value / total * 100
        color = CARD_ACCENTS[i % len(CARD_ACCENTS)]
        bar_html.append(f'<div class="stacked-segment" style="width:{pct:.1f}%; background:{color};" title="{label}: {pct:.1f}%"></div>')
        legend_html.append(f'<div class="stacked-legend-item"><span class="legend-dot" style="background:{color};"></span>{label} ({pct:.0f}%)</div>')
    return (
        f'<div class="chart-block"><div class="chart-title">{title}</div>'
        f'<div class="stacked-bar">{"".join(bar_html)}</div>'
        f'<div class="stacked-legend">{"".join(legend_html)}</div></div>'
    )


def _render_line_trend(chart: dict) -> str:
    """Meerjarige trendlijn (SVG) -- toont een traject over tijd, anders dan
    een staafdiagram per jaar. Ondersteunt zowel EEN reeks (labels/values,
    het oorspronkelijke formaat) als MEERDERE genoemde reeksen tegelijk
    (series: [{name, values}, ...]) -- voor bijv. koers-vs-benchmark,
    margeontwikkeling over meerdere marges, of aandeel-vs-grondstofprijs.

    Labelt NIET elk punt: bij veel datapunten (bijv. een lopende-beta-reeks
    met 15+ metingen) overlapten de tekstlabels elkaar volledig als elk punt
    een eigen label kreeg -- nu wordt een leesbaar aantal labels gekozen
    (rond de 8) en de rest alleen als punt getekend, zonder tekst eronder."""
    title = html.escape(chart.get("title", ""))
    labels = chart.get("labels", [])
    unit = html.escape(chart.get("unit", ""))

    raw_series = chart.get("series")
    if raw_series:
        series = [(s.get("name", ""), s.get("values", [])) for s in raw_series]
    else:
        series = [("", chart.get("values", []))]

    if not labels or not series or any(len(v) != len(labels) for _, v in series):
        return ""

    width, height, padding = 600, 150, 28
    all_values = [v for _, vals in series for v in vals]
    min_v, max_v = min(all_values), max(all_values)
    span = (max_v - min_v) or 1
    n = len(labels)
    step = (width - 2 * padding) / max(n - 1, 1)

    max_labels = 8
    label_interval = max(1, round(n / max_labels))
    colors = ["var(--brand)", "var(--brand-2)"] + CARD_ACCENTS

    def to_xy(i, v):
        x = padding + i * step
        y = height - padding - ((v - min_v) / span) * (height - 2 * padding)
        return x, y

    axis_labels = []
    for i in range(n):
        if i % label_interval == 0 or i == n - 1:
            x, _ = to_xy(i, min_v)
            axis_labels.append(f'<text x="{x:.1f}" y="{height - 6}" font-size="10" text-anchor="middle" fill="var(--ink-soft)">{html.escape(str(labels[i]))}</text>')

    lines_svg, legend_items = [], []
    for s_idx, (name, values) in enumerate(series):
        color = colors[s_idx % len(colors)]
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in (to_xy(i, v) for i, v in enumerate(values)))
        dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}" />' for x, y in (to_xy(i, v) for i, v in enumerate(values)))
        lines_svg.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2" />{dots}')
        if name:
            legend_items.append(f'<div class="radar-legend-item"><span class="regime-swatch" style="background:{color};"></span>{html.escape(str(name))}</div>')
        elif len(series) == 1:
            last_x, last_y = to_xy(n - 1, values[-1])
            lines_svg.append(f'<text x="{last_x:.1f}" y="{last_y - 10:.1f}" font-size="11" text-anchor="middle" fill="var(--ink)">{html.escape(str(values[-1]))}{unit}</text>')

    title_html = f'<div class="chart-title">{title}</div>' if title else ""
    legend_html = f'<div class="radar-legend" style="margin-bottom:8px;">{"".join(legend_items)}</div>' if legend_items else ""
    svg = f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px;">{"".join(lines_svg)}{"".join(axis_labels)}</svg>'
    return f'<div class="chart-block">{title_html}{legend_html}{svg}</div>'


def _render_radar(chart: dict) -> str:
    """Radardiagram (SVG) voor een multidimensionale profielvergelijking --
    bijv. moat-sterkte/financiele gezondheid/kwaliteit in een oogopslag, of
    bedrijf-vs-peer-gemiddelde over meerdere assen tegelijk. Ondersteunt tot
    2 reeksen (bijv. 'Bedrijf' vs. 'Sector-gemiddelde'). Gebruikt uitsluitend
    CSS-variabelen voor kleur (--brand/--brand-2), nooit een vast hexgetal,
    zodat dit altijd meekleurt met de merkkleuren van het bedrijf."""
    title = html.escape(chart.get("title", ""))
    axes = chart.get("axes", [])
    series = chart.get("series", [])
    if not axes or not series or any(len(s.get("values", [])) != len(axes) for s in series):
        return ""

    n = len(axes)
    size, center, max_r = 320, 160, 95
    max_value = max(v for s in series for v in s["values"]) or 1

    def point_at(i, value):
        angle = (2 * 3.14159265 * i / n) - (3.14159265 / 2)
        r = (value / max_value) * max_r
        return center + r * math.cos(angle), center + r * math.sin(angle)

    # Achtergrondraster: 3 concentrische ringen als schaalreferentie
    rings = []
    for frac in (0.33, 0.66, 1.0):
        ring_points = " ".join(f"{center + frac * max_r * math.cos((2*3.14159265*i/n) - 3.14159265/2):.1f},{center + frac * max_r * math.sin((2*3.14159265*i/n) - 3.14159265/2):.1f}" for i in range(n))
        rings.append(f'<polygon points="{ring_points}" fill="none" stroke="var(--rule, #ddd)" stroke-width="1" />')

    # Asremmen + labels
    axis_lines, labels = [], []
    for i, axis_name in enumerate(axes):
        x, y = point_at(i, max_value)
        axis_lines.append(f'<line x1="{center}" y1="{center}" x2="{x:.1f}" y2="{y:.1f}" stroke="var(--rule, #ddd)" stroke-width="1" />')
        lx, ly = point_at(i, max_value * 1.18)
        labels.append(f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="10" text-anchor="middle" fill="var(--ink-soft)">{html.escape(str(axis_name))}</text>')

    colors = ["var(--brand)", "var(--brand-2)"]
    polygons, legend = [], []
    for s_idx, s in enumerate(series[:2]):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (point_at(i, v) for i, v in enumerate(s["values"])))
        color = colors[s_idx]
        polygons.append(f'<polygon points="{pts}" fill="{color}" fill-opacity="0.18" stroke="{color}" stroke-width="2" />')
        if s.get("name"):
            legend.append(f'<div class="radar-legend-item"><span class="regime-swatch" style="background:{color};"></span>{html.escape(str(s["name"]))}</div>')

    title_html = f'<div class="chart-title">{title}</div>' if title else ""
    legend_html = f'<div class="radar-legend">{"".join(legend)}</div>' if legend else ""
    svg = f'<svg viewBox="0 0 {size} {size}" width="100%" style="max-width:320px;">{"".join(rings)}{"".join(axis_lines)}{"".join(polygons)}{"".join(labels)}</svg>'
    return f'<div class="chart-block">{title_html}{svg}{legend_html}</div>'


def _render_scatter(chart: dict) -> str:
    """Scatterplot (SVG) voor de relatie tussen twee variabelen -- bijv.
    risico vs. rendement per scenario, of peers uitgezet op twee ratio's
    tegelijk. Elk punt krijgt een klein label (bedrijfsnaam/scenarionaam)."""
    title = html.escape(chart.get("title", ""))
    points = chart.get("points", [])
    x_label = html.escape(chart.get("x_label", ""))
    y_label = html.escape(chart.get("y_label", ""))
    if not points:
        return ""

    width, height, padding = 480, 320, 44
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = (max_x - min_x) or 1
    span_y = (max_y - min_y) or 1

    def to_xy(px, py):
        x = padding + (px - min_x) / span_x * (width - 2 * padding)
        y = height - padding - (py - min_y) / span_y * (height - 2 * padding)
        return x, y

    dots = []
    for p in points:
        x, y = to_xy(p["x"], p["y"])
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="var(--brand)" fill-opacity="0.75" />')
        if p.get("label"):
            dots.append(f'<text x="{x:.1f}" y="{y-9:.1f}" font-size="9.5" text-anchor="middle" fill="var(--ink-soft)">{html.escape(str(p["label"]))}</text>')

    axes_lines = (
        f'<line x1="{padding}" y1="{height-padding}" x2="{width-padding}" y2="{height-padding}" stroke="var(--ink-soft)" stroke-width="1" />'
        f'<line x1="{padding}" y1="{padding}" x2="{padding}" y2="{height-padding}" stroke="var(--ink-soft)" stroke-width="1" />'
    )
    axis_labels = (
        f'<text x="{width/2:.1f}" y="{height-8}" font-size="10.5" text-anchor="middle" fill="var(--ink-soft)">{x_label}</text>'
        f'<text x="12" y="{height/2:.1f}" font-size="10.5" text-anchor="middle" fill="var(--ink-soft)" transform="rotate(-90 12 {height/2:.1f})">{y_label}</text>'
    ) if x_label or y_label else ""

    title_html = f'<div class="chart-title">{title}</div>' if title else ""
    svg = f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px;">{axes_lines}{axis_labels}{"".join(dots)}</svg>'
    return f'<div class="chart-block">{title_html}{svg}</div>'


def _render_donut(chart: dict) -> str:
    """Cirkeldiagram (SVG) voor een percentage-verdeling -- een ronde
    variant naast de balk-gebaseerde 'composition', voor als een rond
    beeld beter past (bijv. omzet naar regio of aandeelhoudersstructuur)."""
    title = html.escape(chart.get("title", ""))
    labels = chart.get("labels", [])
    values = chart.get("values", [])
    if not labels or not values or len(labels) != len(values):
        return ""

    total = sum(values) or 1
    radius, circumference = 40, 2 * 3.14159265 * 40
    offset = 0
    circles = []
    legend = []
    for i, (label, value) in enumerate(zip(labels, values)):
        pct = value / total
        dash = pct * circumference
        color = CARD_ACCENTS[i % len(CARD_ACCENTS)]
        circles.append(
            f'<circle r="{radius}" cx="50" cy="50" fill="transparent" stroke="{color}" stroke-width="16" '
            f'stroke-dasharray="{dash:.2f} {circumference - dash:.2f}" stroke-dashoffset="{-offset:.2f}" />'
        )
        offset += dash
        legend.append(f'<div class="stacked-legend-item"><span class="legend-dot" style="background:{color};"></span>{html.escape(str(label))} ({pct*100:.0f}%)</div>')

    svg = f'<svg viewBox="0 0 100 100" width="170" height="170" style="transform:rotate(-90deg);">{"".join(circles)}</svg>'
    return (
        f'<div class="chart-block donut-block"><div class="chart-title">{title}</div>'
        f'<div class="donut-row">{svg}<div class="stacked-legend donut-legend">{"".join(legend)}</div></div></div>'
    )


def _render_bell_curve_svg(bin_centers: list, bin_counts: list, p10, median, p90) -> str:
    """Tekent het histogram van de Monte Carlo-uitkomsten als een gevulde
    SVG-vlakgrafiek -- bij een driehoeksverdeling als invoer krijgt de
    uitkomst vanzelf een klok-/bergvorm, zonder dat we een curve hoeven te
    forceren. Zelfde SVG-aanpak als de bestaande line-trend/donut-types."""
    width, height, padding, top_margin = 600, 140, 10, 18
    min_x, max_x = bin_centers[0], bin_centers[-1]
    span_x = (max_x - min_x) or 1
    max_count = max(bin_counts) or 1

    def to_xy(bin_center, count):
        x = padding + (bin_center - min_x) / span_x * (width - 2 * padding)
        y = (height - padding) - (count / max_count) * (height - padding - top_margin)
        return x, y

    points = [to_xy(c, n) for c, n in zip(bin_centers, bin_counts)]
    path_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    baseline_y = height - padding
    area_path = f"M{padding},{baseline_y} L{path_points} L{width - padding},{baseline_y} Z"

    def fmt(value):
        return f"${value/1e9:.2f}B" if abs(value) >= 1e9 else f"${value/1e6:.0f}M"

    markers = []
    for value, label, color in [(p10, "P10", "var(--ink-soft)"), (median, "Mediaan", "var(--brand-2)"), (p90, "P90", "var(--ink-soft)")]:
        x = padding + (value - min_x) / span_x * (width - 2 * padding)
        markers.append(
            f'<line x1="{x:.1f}" y1="{top_margin}" x2="{x:.1f}" y2="{baseline_y}" '
            f'stroke="{color}" stroke-width="1.5" stroke-dasharray="3,3" />'
            f'<text x="{x:.1f}" y="{top_margin - 6}" font-size="10.5" '
            f'text-anchor="middle" fill="{color}" font-weight="600">{fmt(value)}</text>'
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" class="bell-curve-svg" xmlns="http://www.w3.org/2000/svg">'
        f'<path d="{area_path}" fill="var(--brand)" fill-opacity="0.25" stroke="var(--brand)" stroke-width="2" />'
        + "".join(markers) +
        '</svg>'
    )


def _render_distribution(chart: dict) -> str:
    """Toont een Monte Carlo-uitkomstverdeling. Is histogramdata aanwezig
    (histogram_bin_centers/histogram_counts), dan wordt een echte, gevulde
    belcurve getekend (SVG); anders valt dit terug op de eenvoudigere
    horizontale p10-p90-balk met mediaan-markering."""
    p10, p25, median, p75, p90 = (chart.get(k) for k in ("p10", "p25", "median", "p75", "p90"))
    if None in (p10, p25, median, p75, p90) or p90 == p10:
        return ""
    title = html.escape(chart.get("title", ""))
    target_label = html.escape(chart.get("target_metric", ""))

    def fmt(value):
        return f"${value/1e9:.2f}B" if abs(value) >= 1e9 else f"${value/1e6:.0f}M"

    title_html = f'<div class="chart-title">{title}</div>' if title else ""

    bin_centers = chart.get("histogram_bin_centers")
    bin_counts = chart.get("histogram_counts")
    if bin_centers and bin_counts and len(bin_centers) == len(bin_counts):
        curve_html = _render_bell_curve_svg(bin_centers, bin_counts, p10, median, p90)
        return (
            f'<div class="chart-block distribution-chart">{title_html}'
            f'<div class="dist-label">{target_label} -- verdeling over {sum(bin_counts)} simulaties</div>'
            f'{curve_html}'
            f'<div class="dist-scale">'
            f'<span>P10: {fmt(p10)}</span><span class="dist-median-label">Mediaan: {fmt(median)}</span><span>P90: {fmt(p90)}</span>'
            f'</div>'
            f'</div>'
        )

    total_span = p90 - p10
    core_left_pct = (p25 - p10) / total_span * 100
    core_width_pct = (p75 - p25) / total_span * 100
    median_pct = (median - p10) / total_span * 100
    return (
        f'<div class="chart-block distribution-chart">{title_html}'
        f'<div class="dist-label">{target_label} -- P10 t/m P90 verdeling</div>'
        f'<div class="dist-track">'
        f'<div class="dist-core" style="left:{core_left_pct:.1f}%; width:{core_width_pct:.1f}%;"></div>'
        f'<div class="dist-median" style="left:{median_pct:.1f}%;"></div>'
        f'</div>'
        f'<div class="dist-scale">'
        f'<span>P10: {fmt(p10)}</span><span class="dist-median-label">Mediaan: {fmt(median)}</span><span>P90: {fmt(p90)}</span>'
        f'</div>'
        f'</div>'
    )


def _render_regime_price_chart(prices: list, dates: list, history: list, color_map: dict) -> str:
    """Koerslijn met een gekleurd vlak per regime erachter -- het regime van
    dag t hoort bij het rendement closes[t-1] -> closes[t], dus dag 0 krijgt
    (bij gebrek aan een eigen rendement) hetzelfde regime als dag 1, precies
    zoals de HMM-decodering het al aan koers[1:] toekent."""
    n_days = len(prices)
    day_state = [history[0]] + [history[i - 1] for i in range(1, n_days)]

    width, height = 600, 190
    pad_l, pad_r, pad_t, pad_b = 46, 8, 10, 24
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    total_days = max(n_days - 1, 1)

    p_min, p_max = min(prices), max(prices)
    pad_v = (p_max - p_min) * 0.08 or max(p_max, 1) * 0.02
    y_min, y_max = p_min - pad_v, p_max + pad_v
    span = (y_max - y_min) or 1

    def x(d):
        return pad_l + (d / total_days) * plot_w

    def y(p):
        return pad_t + (1 - (p - y_min) / span) * plot_h

    # opeenvolgende dagen met hetzelfde regime groeperen tot vlakken
    bands, band_state, band_start = [], day_state[0], 0
    for i in range(1, n_days + 1):
        if i == n_days or day_state[i] != band_state:
            bands.append((band_state, band_start, i))
            if i < n_days:
                band_state, band_start = day_state[i], i

    band_rects = "".join(
        f'<rect x="{x(start):.1f}" y="{pad_t}" width="{(x(end) - x(start)):.1f}" height="{plot_h}" '
        f'fill="{color_map.get(state, "var(--ink-soft)")}" fill-opacity="0.14" />'
        for state, start, end in bands
    )

    gridlines = []
    for step in range(5):
        val = y_min + span * (step / 4)
        yy = y(val)
        gridlines.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}" stroke="var(--line, #DDD5C4)" stroke-width="1" />')
        gridlines.append(f'<text x="{pad_l - 6}" y="{yy + 3:.1f}" font-size="9.5" text-anchor="end" fill="var(--ink-soft)">{val:.1f}</text>')

    x_labels = []
    if dates and len(dates) == n_days:
        step = max(1, round(n_days / 6))
        idxs = list(range(0, n_days, step))
        if idxs[-1] != n_days - 1:
            idxs.append(n_days - 1)
        for i in idxs:
            anchor = "start" if i == 0 else "end" if i == n_days - 1 else "middle"
            x_labels.append(
                f'<text x="{x(i):.1f}" y="{pad_t + plot_h + 16}" font-size="9.5" text-anchor="{anchor}" '
                f'fill="var(--ink-soft)">{html.escape(str(dates[i]))}</text>'
            )

    points = " ".join(f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(prices))
    last_x, last_y = x(n_days - 1), y(prices[-1])

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px;">'
        f'{band_rects}{"".join(gridlines)}'
        f'<polyline points="{points}" fill="none" stroke="var(--ink)" stroke-width="1.75" '
        f'stroke-linejoin="round" stroke-linecap="round" />'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="var(--ink)" />'
        f'{"".join(x_labels)}'
        f'</svg>'
    )


def _render_regime_transition_table(transition_matrix: dict, color_map: dict) -> str:
    """Gefitte overgangsmatrix (deze run) als kleine tabel -- geen vaste
    aanname, maar wat het HMM daadwerkelijk uit de koershistorie schatte."""
    labels = list(transition_matrix.keys())
    header = "".join(
        f'<th style="color:{color_map.get(lbl, "var(--ink-soft)")};">{html.escape(str(lbl))}</th>'
        for lbl in labels
    )
    rows = []
    for from_label in labels:
        to_probs = transition_matrix.get(from_label, {})
        cells = "".join(
            f'<td class="{"regime-diag" if from_label == to_label else ""}">{float(to_probs.get(to_label, 0)):.2f}</td>'
            for to_label in labels
        )
        color = color_map.get(from_label, "var(--ink-soft)")
        rows.append(f'<tr><td class="regime-rowhead" style="color:{color};">{html.escape(str(from_label))}</td>{cells}</tr>')
    return (
        '<div class="regime-card">'
        '<div class="regime-card-title">Overgangswaarschijnlijkheden (gefit, deze run)</div>'
        f'<table class="regime-tm"><tr><th></th>{header}</tr>{"".join(rows)}</table>'
        '</div>'
    )


def _render_regime_days_list(regime_days: dict, total_days: int, color_map: dict) -> str:
    """Aantal dagen per regime in het weergegeven venster, met percentage."""
    rows = []
    for label, count in regime_days.items():
        pct = round(count / total_days * 100) if total_days else 0
        color = color_map.get(label, "var(--ink-soft)")
        rows.append(
            '<div class="regime-dur-row">'
            f'<span class="regime-dur-name"><span class="regime-swatch" style="background:{color};"></span>{html.escape(str(label))}</span>'
            f'<span class="regime-dur-num">{count} dagen &middot; {pct}%</span>'
            '</div>'
        )
    return (
        '<div class="regime-card">'
        f'<div class="regime-card-title">Dagen per regime (laatste {total_days} handelsdagen)</div>'
        f'{"".join(rows)}</div>'
    )


def _render_regime_timeline(chart: dict) -> str:
    """Toont de HMM-regimegeschiedenis als een koerslijn met een gekleurd
    vlak per regime erachter, plus de gefitte overgangsmatrix en het aantal
    dagen per regime -- i.p.v. alleen een kale stroken-tijdlijn. Vaste,
    semantische kleuren voor de bekende regime-labels (kalm=groen-achtig,
    onrustig=rood-achtig), zodat de kleur altijd hetzelfde betekent ongeacht
    welk bedrijf. Valt terug op de oude stroken-tijdlijn als er geen
    koersdata is meegegeven (bijv. een ouder chart-JSON-formaat)."""
    title = html.escape(chart.get("title", ""))
    history = chart.get("history", [])
    if not history:
        return ""

    color_map = {
        "kalm/laag-volatiel": "#4A7C64",
        "gemiddeld-volatiel": "#B8935F",
        "onrustig/hoog-volatiel": "#A6402F",
    }
    fallback_colors = CARD_ACCENTS
    unique_labels = list(dict.fromkeys(history))  # volgorde bewaren, dupes eruit
    for i, label in enumerate(unique_labels):
        if label not in color_map:
            color_map[label] = fallback_colors[i % len(fallback_colors)]

    prices = chart.get("prices", [])
    dates = chart.get("dates", [])
    chart_html = ""
    if prices and len(prices) == len(history) + 1:
        chart_html = _render_regime_price_chart(prices, dates, history, color_map)

    if not chart_html:
        segments = "".join(
            f'<div class="regime-segment" style="background:{color_map[label]};" title="{html.escape(str(label))}"></div>'
            for label in history
        )
        chart_html = f'<div class="regime-strip">{segments}</div>'

    legend = "".join(
        f'<div class="regime-legend-item"><span class="regime-swatch" style="background:{color_map[label]};"></span>{html.escape(str(label))}</div>'
        for label in unique_labels
    )

    extra_cards = ""
    transition_matrix = chart.get("transition_matrix")
    if transition_matrix:
        extra_cards += _render_regime_transition_table(transition_matrix, color_map)
    regime_days = chart.get("regime_days")
    if regime_days:
        extra_cards += _render_regime_days_list(regime_days, len(history), color_map)
    grid_html = f'<div class="regime-grid">{extra_cards}</div>' if extra_cards else ""

    title_html = f'<div class="chart-title">{title}</div>' if title else ""
    return (
        f'<div class="chart-block regime-timeline">{title_html}'
        f'{chart_html}'
        f'<div class="regime-legend">{legend}</div>'
        f'{grid_html}'
        f'</div>'
    )


def _render_kill_criteria_recap(chart: dict) -> str:
    """Compacte, kleine afsluitende opsomming van de concrete kill-criteria
    onderaan sectie 17 -- een geheugensteun met de exacte drempelwaarden,
    apart van de doorlopende tekst erboven, zodat je 'm niet kan missen bij
    een snelle herlezing.

    Elk element in 'criteria' mag ofwel een losse string zijn (oude vorm,
    en kwalitatieve criteria die niet aan een cijfer te koppelen zijn), ofwel
    een object {"description": ..., "metric_key": ..., ...} (nieuwe,
    machine-checkbare vorm) -- hier tonen we in beide gevallen alleen de
    leesbare omschrijving; de machine-checkbare velden worden apart, uit de
    ruwe chart-JSON, gebruikt door track_record.py voor een toekomstige
    kalibratiescore, niet hier in de weergave."""
    descriptions = []
    for c in chart.get("criteria", []):
        text = c.get("description", "") if isinstance(c, dict) else str(c)
        if text.strip():
            descriptions.append(html.escape(text))
    if not descriptions:
        return ""
    items = "".join(f"<li>{c}</li>" for c in descriptions)
    return f'<div class="chart-block kill-criteria-recap"><div class="kcr-label">Kill-criteria op een rij</div><ol>{items}</ol></div>'


def _render_quote_block(chart: dict) -> str:
    """Een uitgelicht citaat (bijv. management-guidance of een treffende
    uitspraak uit een earnings call) -- groter en editorialer dan 'callout',
    dat een gelabeld vak is."""
    quote = html.escape(chart.get("quote", ""))
    attribution = html.escape(chart.get("attribution", ""))
    if not quote:
        return ""
    attribution_html = f'<div class="quote-attribution">— {attribution}</div>' if attribution else ""
    return f'<div class="chart-block quote-block"><div class="quote-text">{quote}</div>{attribution_html}</div>'


def _render_fact_sheet(chart: dict) -> str:
    """Compacte label:waarde-lijst voor structurele bedrijfsfeiten (opgericht,
    HQ, beursnoteringen, bestuur, aandelental) -- tekst-dragend, geen cijfer-
    visualisatie. Past goed in sectie 1 (Company Overview)."""
    title = html.escape(chart.get("title", ""))
    facts = chart.get("facts", [])
    if not facts:
        return ""
    rows = "".join(
        f'<div class="fact-row"><div class="fact-label">{html.escape(str(f.get("label", "")))}</div>'
        f'<div class="fact-value">{html.escape(str(f.get("value", "")))}</div></div>'
        for f in facts if f.get("label") and f.get("value") is not None
    )
    title_html = f'<div class="chart-title">{title}</div>' if title else ""
    return f'<div class="chart-block fact-sheet">{title_html}{rows}</div>'


def _render_profile_cards(chart: dict) -> str:
    """Kaarten voor personen of entiteiten (management, bestuur, grote
    aandeelhouders) -- naam, een klein label/tag, en een beschrijvende
    alinea. Tekst-dragend, in tegenstelling tot metric-cards (puur cijfers)."""
    profiles = chart.get("profiles", [])
    if not profiles:
        return ""
    cards = "".join(
        f'<div class="profile-card">'
        f'<div class="profile-name">{html.escape(str(p.get("name", "")))}</div>'
        + (f'<div class="profile-tag">{html.escape(str(p.get("tag", "")))}</div>' if p.get("tag") else "")
        + f'<p class="profile-desc">{html.escape(str(p.get("description", "")))}</p></div>'
        for p in profiles if p.get("name")
    )
    return f'<div class="chart-block profile-cards-grid">{cards}</div>'


def _render_segment_cards(chart: dict) -> str:
    """Kaarten voor bedrijfssegmenten/business units -- tag, titel,
    beschrijving, EN een kleine statistiekenrij onderaan. Rijker dan
    metric-cards: combineert narratieve tekst met een paar kerncijfers per
    segment in één kaart."""
    segments = chart.get("segments", [])
    if not segments:
        return ""
    cards = []
    for s in segments:
        if not s.get("title"):
            continue
        tag_html = f'<div class="segment-tag">{html.escape(str(s.get("tag", "")))}</div>' if s.get("tag") else ""
        subtitle_html = f'<div class="segment-subtitle">{html.escape(str(s.get("subtitle", "")))}</div>' if s.get("subtitle") else ""
        stats = s.get("stats", [])
        stats_html = ""
        if stats:
            stat_items = "".join(
                f'<div class="segment-stat"><div class="segment-stat-label">{html.escape(str(st.get("label", "")))}</div>'
                f'<div class="segment-stat-value">{html.escape(str(st.get("value", "")))}</div></div>'
                for st in stats if st.get("label")
            )
            stats_html = f'<div class="segment-stats">{stat_items}</div>'
        cards.append(
            f'<div class="segment-card">{tag_html}<div class="segment-title">{html.escape(str(s["title"]))}</div>'
            f'{subtitle_html}<p class="segment-desc">{html.escape(str(s.get("description", "")))}</p>{stats_html}</div>'
        )
    if not cards:
        return ""
    return f'<div class="chart-block segment-cards-grid">{"".join(cards)}</div>'


def _render_data_table(chart: dict) -> str:
    """Generieke, brede financiële tabel (bijv. een meerjarig of meer-
    kwartaals overzicht) -- flexibeler dan risk-table, dat specifiek voor
    risico's is bedoeld. Ondersteunt een optionele 'footnote' -- verplicht
    als een cel verwijst naar 'see note' oid: die verwijzing moet ALTIJD een
    zichtbare, direct-onder-de-tabel-geplaatste uitleg krijgen, niet ergens
    verstopt in de lopende tekst (reproduceerde eerder een echt, verwarrend
    geval bij een OKLO-rapport)."""
    title = html.escape(chart.get("title", ""))
    columns = chart.get("columns", [])
    rows = chart.get("rows", [])
    footnote = chart.get("footnote", "")
    if not columns or not rows:
        return ""
    header = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    title_html = f'<div class="chart-title">{title}</div>' if title else ""
    footnote_html = f'<div class="data-table-footnote">{html.escape(footnote)}</div>' if footnote else ""
    return f'<div class="chart-block data-table-wrap">{title_html}<table class="data-table"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>{footnote_html}</div>'


def _render_comparison_columns(chart: dict) -> str:
    """Twee kolommen naast elkaar met tegengestelde punten (bull/bear,
    case-for/case-against) -- specifiek bedoeld voor sectie 18 (Variant
    Perception), waar het presenteren van beide kanten voluit bij de eigen
    synthese hoort."""
    title = html.escape(chart.get("title", ""))
    left_label = html.escape(chart.get("left_label", ""))
    right_label = html.escape(chart.get("right_label", ""))
    left_points = [html.escape(str(p)) for p in chart.get("left_points", [])]
    right_points = [html.escape(str(p)) for p in chart.get("right_points", [])]
    if not left_points or not right_points:
        return ""
    left_items = "".join(f"<li>{p}</li>" for p in left_points)
    right_items = "".join(f"<li>{p}</li>" for p in right_points)
    title_html = f'<div class="chart-title">{title}</div>' if title else ""
    return (
        f'<div class="chart-block comparison-columns">{title_html}'
        f'<div class="comparison-grid">'
        f'<div class="comparison-col comparison-left"><div class="comparison-label">{left_label}</div><ul>{left_items}</ul></div>'
        f'<div class="comparison-col comparison-right"><div class="comparison-label">{right_label}</div><ul>{right_items}</ul></div>'
        f'</div></div>'
    )


def _render_milestone_progress(chart: dict) -> str:
    """Voortgangsbalk richting een specifiek doel (bijv. bouwvoortgang van
    een fabriek, % van jaarguidance behaald) -- één concrete waarde tegen
    een doel, anders dan 'gauge' (dat zones/risico-categorieen toont)."""
    title = html.escape(chart.get("title", ""))
    label = html.escape(chart.get("label", ""))
    current = chart.get("current")
    target = chart.get("target")
    unit = html.escape(chart.get("unit", "%"))
    if current is None or target is None or target == 0:
        return ""
    pct = max(0, min(100, current / target * 100))
    return (
        f'<div class="chart-block"><div class="chart-title">{title}</div>'
        f'<div class="milestone-label">{label}</div>'
        f'<div class="milestone-track"><div class="milestone-fill" style="width:{pct:.1f}%;"></div></div>'
        f'<div class="milestone-value">{current}{unit} / {target}{unit} ({pct:.0f}%)</div></div>'
    )


def _render_custom(chart: dict) -> str:
    """Vrij door Claude ontworpen HTML/CSS, ALLEEN gerenderd na een
    geslaagde validatie (validate_custom_html.py). Bij afkeuring wordt het
    component stilzwijgend overgeslagen (lege string) i.p.v. onveilige of
    kapotte HTML door te laten -- de tekst eromheen blijft gewoon staan."""
    from reporting.validate_custom_html import validate_custom_html

    html_content = chart.get("html", "")
    check = validate_custom_html(html_content)
    if not check["valid"]:
        return f'<!-- custom-component afgekeurd: {check["reason"]} -->'

    # Ingesloten in een vak met overflow:hidden en een max-breedte, zodat
    # zelfs een technisch geldig maar te groot element de pagina-layout
    # niet kan verstoren.
    return f'<div class="chart-block custom-block">{html_content}</div>'


def _render_chart(chart: dict) -> str:
    renderers = {
        "bar": _render_bar_chart,
        "scenario": _render_scenario_cards,
        "composition": _render_composition,
        "valuechain": _render_valuechain,
        "timeline": _render_timeline,
        "risk-table": _render_risk_table,
        "risk-matrix": _render_risk_matrix,
        "grouped-bar": _render_grouped_bar,
        "callout": _render_callout,
        "rating": _render_rating,
        "waterfall": _render_waterfall,
        "gauge": _render_gauge,
        "heatmap": _render_heatmap,
        "metric-cards": _render_metric_cards,
        "stacked-bar": _render_stacked_bar,
        "line-trend": _render_line_trend,
        "radar": _render_radar,
        "scatter": _render_scatter,
        "donut": _render_donut,
        "quote-block": _render_quote_block,
        "milestone-progress": _render_milestone_progress,
        "kill-criteria-recap": _render_kill_criteria_recap,
        "distribution": _render_distribution,
        "regime-timeline": _render_regime_timeline,
        "fact-sheet": _render_fact_sheet,
        "profile-cards": _render_profile_cards,
        "segment-cards": _render_segment_cards,
        "data-table": _render_data_table,
        "comparison-columns": _render_comparison_columns,
        "custom": _render_custom,
    }
    renderer = renderers.get(chart.get("type"))
    return renderer(chart) if renderer else ""


def render_html(ticker: str, long_name: str, timestamp_str: str,
                 peers: list[str] | None, extra_context: str,
                 review: dict, analysis_text: str, colors: dict,
                 company_data: dict, peer_data: dict | None,
                 used_library_books: list[str] | None = None,
                 executive_summary: str = "",
                 lineage_manifest: list[dict] | None = None) -> str:
    """Bouwt het complete HTML-bestand: een donkere zijbalk met inhoudsopgave
    naast een rustige, altijd-leesbare hoofdkolom. De merk-kleuren van het
    bedrijf komen alleen terug als accent (navigatie, sectienummers, label)."""
    sections = _split_into_sections(analysis_text)

    nav_items = "\n".join(
        f'<a href="#s{s["num"] or i}"><span class="nav-num">{html.escape(s["num"] or str(i).zfill(2))}</span> '
        f'{html.escape(s["title"])}</a>'
        for i, s in enumerate(sections, start=1)
    )
    nav_snapshot = '<a href="#snapshot"><span class="nav-num">--</span> Snapshot</a>'

    def _render_section_body(body: str) -> str:
        cleaned_body, charts = _extract_charts(body)
        charts_html = "".join(_render_chart(c) for c in charts)
        return _paragraphs_html(cleaned_body) + charts_html

    VARIANT_PERCEPTION_BANNER = (
        '<div class="variant-perception-banner">'
        '<strong>Ander register dan de rest van dit rapport.</strong> Dit is een analytisch '
        'vermoeden met bewust lagere bewijsrigueur, geen aanbeveling en geen koersdoel. '
        'Alle 17 secties hiervoor zijn strikt neutraal; alleen deze sectie bevat een eigen '
        'directionele inschatting, uitsluitend voor intern TCE-gebruik.'
        '</div>'
    )

    def _section_extra_class(section_num: str | None) -> str:
        return " variant-perception" if section_num == "18" else ""

    def _section_banner(section_num: str | None) -> str:
        return VARIANT_PERCEPTION_BANNER if section_num == "18" else ""

    section_blocks = "\n".join(
        f'<section class="section{_section_extra_class(s["num"])}" id="s{s["num"] or i}">'
        f'<h2><span class="sec-num">{html.escape(s["num"] or str(i).zfill(2))}</span>'
        f'<span class="sec-title">{html.escape(s["title"])}</span></h2>'
        f'{_section_banner(s["num"])}'
        f'{_render_section_body(s["body"])}</section>'
        for i, s in enumerate(sections, start=1)
    )

    metrics_html = _metrics_panel(company_data)
    peer_table_html = _peer_table(ticker, company_data, peer_data)
    snapshot_block = f"""<section class="section snapshot" id="snapshot">
<h2><span class="sec-num">--</span><span class="sec-title">Snapshot</span></h2>
{metrics_html}
{peer_table_html}
</section>"""

    review_badge = (
        '<span class="badge badge-ok">Approved</span>'
        if review.get("approved")
        else '<span class="badge badge-warn">Issues Found</span>'
    )
    issues_html = ""
    if not review.get("approved") and review.get("issues"):
        items = "\n".join(f"<li>{html.escape(i)}</li>" for i in review["issues"])
        count = len(review["issues"])
        label = "issue" if count == 1 else "issues"
        issues_html = (
            f'<details class="issues"><summary><strong>Quality Check: {count} {label} found</strong> '
            f'<span class="issues-hint">(click to view)</span></summary><ul>{items}</ul></details>'
        )

    executive_summary_html = ""
    if executive_summary:
        executive_summary_html = (
            '<div class="exec-summary"><div class="exec-summary-label">Executive Summary</div>'
            f'<p>{html.escape(executive_summary)}</p></div>'
        )

    lineage_html = ""
    if lineage_manifest:
        rows = "".join(
            f'<tr><td>{html.escape(str(item["metric"]))}</td>'
            f'<td>{html.escape(str(item["value"]))}</td>'
            f'<td>{html.escape(str(item["source"]))}'
            + (f' ({html.escape(str(item["period"]))})' if item.get("period") else "")
            + '</td>'
            f'<td>{html.escape(str(item["note"])) if item.get("note") else ""}</td></tr>'
            for item in lineage_manifest
        )
        lineage_html = (
            '<details class="lineage"><summary><strong>Data Provenance</strong> '
            f'<span class="issues-hint">({len(lineage_manifest)} metrics, click to view)</span></summary>'
            '<table class="lineage-table"><thead><tr><th>Metric</th><th>Value</th><th>Source</th><th>Derivation</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></details>'
        )

    # Alleen tonen als de bibliotheek deze keer daadwerkelijk is geraadpleegd
    # -- geen lege of misleidende regel als search_playbook niet werd gebruikt.
    library_footer_html = ""
    if used_library_books:
        book_items = "".join(f"<span>{html.escape(b)}</span>" for b in used_library_books)
        library_footer_html = f"""<div class="footer-col">
      <div class="footer-label">Library Inspiration</div>
      <div class="footer-sources">{book_items}</div>
    </div>"""
    display_name = long_name or ticker
    hero_snapshot_html = _hero_snapshot(company_data)
    country_bit = f" &middot; {html.escape(company_data.get('country'))}" if company_data.get("country") else ""

    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(display_name)} — TCE Deep Dive</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{
    --brand: {colors['primary']};
    --brand-2: {colors['secondary']};
    --content-max: 1180px;
    --paper: #F5F1E8;
    --ink: #241F1A;
    --ink-soft: rgba(36, 31, 26, 0.62);
    --rule: #DCD4C2;
    --rail: #201B16;
    --rail-text: #EDE7D8;
    --rail-text-soft: rgba(237, 231, 216, 0.55);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: 'IBM Plex Sans', sans-serif;
  }}
  a {{ text-decoration: none; color: inherit; }}
  a:focus-visible, button:focus-visible {{ outline: 2px solid var(--brand); outline-offset: 2px; }}

  /* Hero: vast donker fundament (leesbaarheid altijd gegarandeerd), de
     merk-kleur van het bedrijf komt terug in de gloed, de rand en de cijfers
     -- dit is de enige plek waar we bewust een sterk visueel statement maken. */
  header.hero {{
    background:
      radial-gradient(120% 140% at 15% 0%, #2a2a2c 0%, var(--rail) 55%, #000 100%);
    color: var(--rail-text);
    padding: 64px 56px 44px;
    position: relative;
    overflow: hidden;
    border-bottom: 3px solid var(--brand);
  }}
  header.hero::before {{
    content: "";
    position: absolute; inset: 0;
    background:
      radial-gradient(circle at 88% 10%, color-mix(in srgb, var(--brand) 30%, transparent), transparent 45%),
      radial-gradient(circle at 100% 100%, color-mix(in srgb, var(--brand) 14%, transparent), transparent 40%);
    pointer-events: none;
  }}
  .hero-inner {{ max-width: var(--content-max); margin: 0 auto; position: relative; z-index: 2; text-align: center; }}
  .eyebrow {{
    font-family: 'DM Mono', monospace;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-size: 0.7rem;
    color: var(--brand-2);
    margin-bottom: 20px;
    display: flex; gap: 12px; flex-wrap: wrap; align-items: center; justify-content: center;
  }}
  .eyebrow span.divider {{ opacity: 0.5; }}
  header.hero h1 {{
    font-family: 'Cormorant Garamond', serif;
    font-size: clamp(2.2rem, 4.4vw, 3.4rem);
    font-weight: 700;
    margin: 0 0 6px;
    line-height: 1.1;
  }}
  .hero-sub {{
    font-family: 'DM Mono', monospace;
    font-size: 0.85rem;
    color: #cfcfd2;
    margin-bottom: 22px;
  }}
  .hero-meta-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    align-items: center;
    justify-content: center;
    margin-bottom: 26px;
    font-family: 'DM Mono', monospace;
    font-size: 0.78rem;
    color: #cfcfd2;
  }}
  .hero-meta-row .meta-item {{ border-left: 1px solid rgba(255,255,255,0.2); padding-left: 14px; }}
  .hero-meta-row .meta-item:first-child {{ border-left: none; padding-left: 0; }}
  .badge {{
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    padding: 3px 10px;
    border-radius: 3px;
  }}
  .badge-ok {{ background: var(--brand); color: var(--paper); }}
  .badge-warn {{ background: #A6402F; color: #FFF; }}
  .snap-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 1px;
    background: rgba(255,255,255,0.14);
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 4px;
    overflow: hidden;
    max-width: 640px;
    margin: 0 auto;
  }}
  .snap-cell {{ background: rgba(0,0,0,0.35); padding: 14px 16px; }}
  .snap-cell .lbl {{
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #a9a7a0;
    margin-bottom: 6px;
  }}
  .snap-cell .val {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 600;
    font-size: 1.2rem;
    color: var(--brand-2);
  }}

  /* Sticky topnav: vervangt de vroegere zijbalk -- dit lost meteen het
     "niet gecentreerd"-gevoel op, want er is geen asymmetrische kolom meer
     die de inhoud naar links duwt. */
  nav.toc {{
    background: rgba(246, 242, 233, 0.88);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--rule);
    position: sticky; top: 0; z-index: 10;
    font-family: 'DM Mono', monospace;
  }}
  nav.toc::after {{
    content: "";
    position: absolute; top: 0; right: 0; bottom: 0; width: 40px;
    background: linear-gradient(to right, transparent, rgba(246, 242, 233, 0.95));
    pointer-events: none;
  }}
  nav.toc .toc-inner {{
    max-width: var(--content-max); margin: 0 auto; padding: 12px 56px;
    display: flex; gap: 18px; flex-wrap: nowrap;
    overflow-x: auto;
    cursor: grab;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    scrollbar-width: none;
    -ms-overflow-style: none;
  }}
  nav.toc .toc-inner.grabbing {{ cursor: grabbing; }}
  nav.toc .toc-inner a {{ flex-shrink: 0; white-space: nowrap; }}
  nav.toc .toc-inner::-webkit-scrollbar {{ display: none; }}
  nav.toc a {{ color: var(--ink-soft); border-bottom: 1px solid transparent; padding-bottom: 2px; transition: color 0.2s, border-color 0.2s; }}
  nav.toc a:hover {{ color: var(--ink); border-bottom-color: var(--brand); }}
  nav.toc a.active {{ color: var(--ink); border-bottom-color: var(--brand); }}
  nav.toc .nav-num {{ color: var(--brand); }}

  main {{ padding: 44px 56px 96px; }}
  .content {{
    max-width: var(--content-max);
    margin: 0 auto;
  }}
  .issues {{
    font-family: 'DM Mono', monospace;
    font-size: 0.85rem;
    background: rgba(166, 64, 47, 0.08);
    border-left: 3px solid #A6402F;
    padding: 12px 16px;
    margin: 0 0 32px;
    max-width: 68ch;
  }}
  .issues summary {{
    cursor: pointer;
    color: var(--ink);
    list-style: revert;
  }}
  .issues summary::marker {{ color: #A6402F; }}
  .issues .issues-hint {{
    font-weight: 400;
    color: var(--ink-soft);
    font-size: 0.78rem;
  }}

  .lineage {{
    font-family: 'DM Mono', monospace;
    font-size: 0.85rem;
    background: rgba(74, 124, 116, 0.06);
    border-left: 3px solid var(--brand);
    padding: 12px 16px;
    margin: 0 0 32px;
    max-width: none;
  }}
  .lineage summary {{
    cursor: pointer;
    color: var(--ink);
    list-style: revert;
    font-family: 'Cormorant Garamond', serif;
    font-size: 1rem;
  }}
  .lineage summary::marker {{ color: var(--brand); }}
  .lineage[open] summary {{ margin-bottom: 14px; }}
  .lineage-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
  }}
  .lineage-table th {{
    text-align: left;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--ink-soft);
    padding: 8px 10px;
    border-bottom: 2px solid var(--ink);
    font-weight: 500;
  }}
  .lineage-table td {{
    padding: 7px 10px;
    border-bottom: 1px solid var(--line, #DDD5C4);
    color: var(--ink);
    vertical-align: top;
  }}
  .lineage-table tr:last-child td {{ border-bottom: none; }}
  .issues[open] summary {{ margin-bottom: 10px; }}
  .issues ul {{ margin: 0; padding-left: 20px; }}

  .exec-summary {{
    background: var(--rail);
    color: var(--rail-text);
    border-left: 3px solid var(--brand);
    padding: 22px 26px;
    margin: 0 0 40px;
    max-width: 72ch;
    border-radius: 4px;
  }}
  .exec-summary-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--brand-2);
    margin-bottom: 10px;
  }}
  .exec-summary p {{
    color: var(--rail-text);
    margin: 0;
    max-width: none;
    font-size: 1.05rem;
    line-height: 1.6;
  }}
  .issues li {{ margin-bottom: 6px; }}

  .kill-criteria-recap {{
    background: #fff;
    border: 1px solid var(--border, #DDD5C4);
    border-left: 3px solid var(--brand-2, #A6402F);
    border-radius: 4px;
    padding: 16px 20px;
    margin: 20px 0;
    max-width: 740px;
  }}
  .kcr-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--ink-soft);
    margin-bottom: 8px;
  }}
  .kill-criteria-recap ol {{
    margin: 0;
    padding-left: 18px;
  }}
  .kill-criteria-recap li {{
    font-size: 0.92rem;
    line-height: 1.5;
    margin-bottom: 6px;
    color: var(--ink);
  }}

  /* Distribution: Monte Carlo-uitkomstverdeling als vereenvoudigde box-plot */
  .distribution-chart {{
    background: #fff;
    border: 1px solid var(--line, #DDD5C4);
    border-radius: 4px;
    padding: 22px 26px;
    margin: 20px 0;
    max-width: 740px;
  }}
  .dist-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--ink-soft);
    margin-bottom: 16px;
  }}
  .dist-track {{
    position: relative;
    height: 14px;
    background: var(--cream, #F6F2E9);
    border: 1px solid var(--line, #DDD5C4);
    border-radius: 7px;
    margin-bottom: 10px;
  }}
  .dist-core {{
    position: absolute;
    top: -1px;
    height: 14px;
    background: var(--brand);
    border-radius: 7px;
    opacity: 0.55;
  }}
  .dist-median {{
    position: absolute;
    top: -4px;
    width: 3px;
    height: 22px;
    background: var(--brand-2);
    transform: translateX(-1.5px);
  }}
  .dist-scale {{
    display: flex;
    justify-content: space-between;
    font-size: 0.85rem;
    color: var(--ink-soft);
  }}
  .dist-median-label {{ color: var(--brand-2); font-weight: 600; }}
  .bell-curve-svg {{ width: 100%; height: auto; margin-bottom: 10px; }}

  .regime-timeline {{
    background: #fff;
    border: 1px solid var(--line, #DDD5C4);
    border-radius: 4px;
    padding: 20px 24px;
    margin: 20px 0;
  }}
  .regime-strip {{
    display: flex;
    height: 24px;
    border-radius: 3px;
    overflow: hidden;
    margin-bottom: 12px;
  }}
  .regime-segment {{ flex: 1 1 0; min-width: 1px; }}
  .regime-legend {{ display: flex; flex-wrap: wrap; gap: 16px; }}
  .regime-legend-item {{
    display: flex; align-items: center; gap: 6px;
    font-size: 0.85rem; color: var(--ink-soft);
  }}
  .regime-swatch {{
    display: inline-block; width: 12px; height: 12px; border-radius: 2px;
  }}
  .regime-grid {{
    display: grid;
    grid-template-columns: 1.15fr 1fr;
    gap: 16px;
    margin-top: 16px;
  }}
  @media (max-width: 640px) {{ .regime-grid {{ grid-template-columns: 1fr; }} }}
  .regime-card {{
    border: 1px solid var(--line, #DDD5C4);
    border-radius: 4px;
    padding: 14px 16px;
  }}
  .regime-card-title {{ font-size: 0.8rem; color: var(--ink-soft); margin-bottom: 10px; }}
  .regime-tm {{ width: 100%; border-collapse: collapse; }}
  .regime-tm td, .regime-tm th {{
    text-align: center; padding: 6px 4px; font-size: 0.78rem;
  }}
  .regime-tm th {{ color: var(--ink-soft); font-weight: 500; font-size: 0.72rem; }}
  .regime-tm td.regime-rowhead {{ text-align: left; font-size: 0.72rem; padding-left: 2px; }}
  .regime-tm td.regime-diag {{ font-weight: 600; }}
  .regime-dur-row {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 7px 0; border-bottom: 1px solid var(--line, #DDD5C4); font-size: 0.82rem;
  }}
  .regime-dur-row:last-child {{ border-bottom: none; }}
  .regime-dur-name {{ display: flex; align-items: center; gap: 8px; }}
  .regime-dur-num {{ color: var(--ink-soft); }}
  .radar-legend {{ display: flex; gap: 16px; margin-top: 8px; }}
  .radar-legend-item {{
    display: flex; align-items: center; gap: 6px;
    font-size: 0.85rem; color: var(--ink-soft);
  }}

  .risk-matrix-wrap {{ max-width: 620px; }}
  .risk-matrix-grid {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 3px;
  }}
  .risk-matrix-cell {{
    aspect-ratio: 1.3;
    border-radius: 3px;
    display: flex; align-items: center; justify-content: center;
    text-align: center;
    font-size: 0.68rem;
    color: var(--ink);
    padding: 4px;
    line-height: 1.3;
  }}
  .risk-matrix-axis-x {{ text-align: center; font-size: 0.75rem; color: var(--ink-soft); margin-top: 8px; }}
  .risk-matrix-axis-y {{ font-size: 0.75rem; color: var(--ink-soft); margin-bottom: 8px; }}

  .grouped-bar-chart {{
    display: flex;
    align-items: flex-end;
    gap: 24px;
    height: 220px;
    padding-top: 10px;
  }}
  .grouped-bar-group {{ display: flex; flex-direction: column; align-items: center; flex: 1; height: 100%; }}
  .grouped-bar-bars {{
    display: flex; align-items: flex-end; gap: 3px;
    height: 190px; width: 100%; justify-content: center;
  }}
  .grouped-bar {{ width: 14px; border-radius: 2px 2px 0 0; min-height: 2px; }}
  .grouped-bar-label {{ font-size: 0.75rem; color: var(--ink-soft); margin-top: 8px; text-align: center; }}

  /* Fact-sheet: compacte label:waarde-lijst */
  .fact-sheet {{
    background: #fff;
    border: 1px solid var(--line, #DDD5C4);
    border-top: 3px solid var(--brand);
    border-radius: 4px;
    padding: 20px 26px;
    margin: 20px 0;
    max-width: 740px;
  }}
  .fact-row {{
    display: grid;
    grid-template-columns: 180px 1fr;
    gap: 16px;
    padding: 9px 0;
    border-bottom: 1px solid var(--line, #DDD5C4);
  }}
  .fact-row:last-child {{ border-bottom: none; }}
  .fact-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--ink-soft);
    padding-top: 2px;
  }}
  .fact-value {{ font-size: 1rem; color: var(--ink); }}

  /* Profile-cards: personen/entiteiten met naam, tag, beschrijving */
  .profile-cards-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 16px;
    margin: 20px 0;
  }}
  .profile-card {{
    background: #fff;
    border: 1px solid var(--line, #DDD5C4);
    border-radius: 4px;
    padding: 20px 22px;
  }}
  .profile-name {{ font-size: 1.15rem; font-weight: 600; color: var(--ink); margin-bottom: 6px; }}
  .profile-tag {{
    display: inline-block;
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--brand);
    border: 1px solid var(--brand);
    border-radius: 3px;
    padding: 3px 8px;
    margin-bottom: 10px;
  }}
  .profile-desc {{ font-size: 0.95rem; line-height: 1.55; color: var(--ink-soft); margin: 0; }}

  /* Segment-cards: bedrijfsonderdelen met tekst + kleine statistiekenrij */
  .segment-cards-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
    margin: 20px 0;
  }}
  .segment-card {{
    background: #fff;
    border: 1px solid var(--line, #DDD5C4);
    border-top: 3px solid var(--brand-2);
    border-radius: 4px;
    padding: 20px 22px;
  }}
  .segment-tag {{
    display: inline-block;
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--ink-soft);
    background: var(--cream, #F6F2E9);
    border-radius: 3px;
    padding: 3px 8px;
    margin-bottom: 10px;
  }}
  .segment-title {{ font-size: 1.2rem; font-weight: 600; color: var(--ink); margin-bottom: 2px; }}
  .segment-subtitle {{ font-size: 0.85rem; color: var(--ink-soft); margin-bottom: 10px; }}
  .segment-desc {{ font-size: 0.92rem; line-height: 1.55; color: var(--ink-soft); margin: 0 0 14px; }}
  .segment-stats {{
    display: flex;
    flex-wrap: wrap;
    gap: 18px;
    border-top: 1px solid var(--line, #DDD5C4);
    padding-top: 12px;
  }}
  .segment-stat-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    text-transform: uppercase;
    color: var(--ink-soft);
    margin-bottom: 2px;
  }}
  .segment-stat-value {{ font-size: 1rem; font-weight: 600; color: var(--ink); }}

  /* Data-table: generieke brede financiele tabel */
  .data-table-wrap {{ margin: 20px 0; overflow-x: auto; }}
  .data-table-footnote {{
    font-size: 0.8rem;
    color: var(--ink-soft);
    margin-top: 8px;
    font-style: italic;
  }}
  table.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92rem;
  }}
  table.data-table th {{
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--ink-soft);
    text-align: left;
    padding: 10px 14px;
    border-bottom: 2px solid var(--ink);
  }}
  table.data-table td {{
    padding: 9px 14px;
    border-bottom: 1px solid var(--line, #DDD5C4);
    color: var(--ink);
  }}
  table.data-table tr:last-child td {{ border-bottom: none; }}

  /* Comparison-columns: bull/bear, case-for/case-against */
  .comparison-columns {{ margin: 20px 0; }}
  .comparison-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }}
  @media (max-width: 640px) {{ .comparison-grid {{ grid-template-columns: 1fr; }} }}
  .comparison-col {{
    background: #fff;
    border-radius: 4px;
    padding: 18px 22px;
    border-top: 3px solid;
  }}
  .comparison-left {{ border-top-color: #4A7C64; }}
  .comparison-right {{ border-top-color: var(--brand-2); }}
  .comparison-label {{ font-size: 1.05rem; font-weight: 600; margin-bottom: 10px; color: var(--ink); }}
  .comparison-col ul {{ margin: 0; padding-left: 18px; }}
  .comparison-col li {{ font-size: 0.92rem; line-height: 1.5; margin-bottom: 8px; color: var(--ink-soft); }}

  /* Sectie 18 (Variant Perception) -- bewust visueel anders dan de rest,
     zodat nooit per ongeluk gelezen wordt als onderdeel van de neutrale
     17 secties ervoor. */
  .section.variant-perception {{
    border: 1px solid #B8935F;
    border-top: 3px solid #A6402F;
    background: rgba(166, 64, 47, 0.04);
    padding: 28px 32px;
    border-radius: 4px;
  }}
  .variant-perception-banner {{
    font-family: 'DM Mono', monospace;
    font-size: 0.85rem;
    line-height: 1.6;
    background: rgba(166, 64, 47, 0.1);
    border-left: 3px solid #A6402F;
    padding: 14px 18px;
    margin: 0 0 24px;
    max-width: 68ch;
    color: var(--ink);
  }}

  .section {{
    margin-bottom: 48px;
    padding-top: 28px;
    border-top: 1px solid var(--rule);
    scroll-margin-top: 56px;
  }}
  .section:first-of-type {{ border-top: none; padding-top: 0; }}
  .snapshot {{ border-top: none; padding-top: 0; }}
  h2 {{
    display: flex;
    align-items: baseline;
    gap: 14px;
    font-family: 'Cormorant Garamond', serif;
    font-weight: 600;
    font-size: 1.6rem;
    margin: 0 0 16px;
    color: var(--ink);
  }}
  .sec-num {{
    font-family: 'DM Mono', monospace;
    font-size: 0.85rem;
    color: var(--brand);
  }}
  p {{
    max-width: none;
    margin: 0 0 14px;
    font-size: 1.12rem;
    line-height: 1.65;
    color: var(--ink);
  }}

  .metrics-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1px;
    background: var(--rule);
    border: 1px solid var(--rule);
    margin-bottom: 24px;
  }}
  .metric-card {{
    background: var(--paper);
    border-top: 3px solid;
    padding: 16px 18px 18px;
  }}
  .metric-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.03em;
    color: var(--ink-soft);
    margin-bottom: 8px;
  }}
  .metric-value {{
    font-family: 'Cormorant Garamond', serif;
    font-size: 1.7rem;
    font-weight: 600;
  }}
  .pos {{ color: #3E7A4F; }}
  .neg {{ color: #A6402F; }}

  .peer-table-wrap {{ overflow-x: auto; margin-bottom: 8px; }}
  .peer-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.95rem;
  }}
  .peer-table th {{
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.03em;
    text-align: right;
    background: var(--rail);
    color: var(--brand-2);
    padding: 9px 10px;
  }}
  .peer-table th:first-child {{ text-align: left; }}
  .peer-table td {{
    text-align: right;
    padding: 9px 10px;
    border-bottom: 1px solid var(--rule);
    font-family: 'DM Mono', monospace;
    font-size: 0.88rem;
  }}
  .peer-table td.peer-name {{
    text-align: left;
    font-family: 'Cormorant Garamond', serif;
    font-size: 1.05rem;
  }}
  .peer-highlight {{ background: rgba(0, 0, 0, 0.035); font-weight: 600; }}

  .chart-block {{ margin: 20px 0 28px; }}
  .chart-title {{
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    letter-spacing: 0.03em;
    color: var(--ink-soft);
    margin-bottom: 12px;
  }}

  .bar-row {{
    display: flex;
    align-items: flex-end;
    gap: 14px;
    height: 180px;
    border-bottom: 1px solid var(--rule);
    padding-bottom: 8px;
  }}
  .bar-col {{
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    align-items: center;
    height: 100%;
  }}
  .bar-value {{ font-family: 'DM Mono', monospace; font-size: 0.78rem; margin-bottom: 4px; }}
  .bar-fill {{ width: 56%; border-radius: 2px 2px 0 0; }}
  .bar-label {{ margin-top: 8px; font-size: 0.95rem; }}

  .scenario-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1px;
    background: var(--rule);
    border: 1px solid var(--rule);
  }}
  .scenario-card {{ background: var(--paper); border-top: 3px solid; padding: 14px 16px 18px; }}
  .scenario-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.03em;
    color: var(--ink-soft);
    margin-bottom: 6px;
  }}
  .scenario-value {{ font-family: 'Cormorant Garamond', serif; font-size: 1.6rem; font-weight: 600; margin-bottom: 8px; }}
  .scenario-desc {{ font-size: 0.92rem; line-height: 1.4; margin: 0; color: var(--ink-soft); }}

  .comp-list {{ display: flex; flex-direction: column; gap: 10px; }}
  .comp-row {{ display: flex; align-items: center; gap: 12px; }}
  .comp-label {{ width: 34%; font-size: 0.98rem; flex-shrink: 0; }}
  .comp-bar-track {{ flex: 1; height: 10px; background: var(--rule); border-radius: 2px; overflow: hidden; }}
  .comp-bar-fill {{ height: 100%; }}
  .comp-pct {{ width: 44px; text-align: right; font-family: 'DM Mono', monospace; font-size: 0.82rem; flex-shrink: 0; }}

  .vc-row {{ display: flex; align-items: stretch; gap: 4px; flex-wrap: nowrap; overflow-x: auto; }}
  .vc-stage {{
    flex: 1;
    min-width: 140px;
    background: var(--paper);
    border-top: 3px solid;
    border: 1px solid var(--rule);
    border-top-width: 3px;
    padding: 12px 14px;
  }}
  .vc-label {{ font-size: 1.02rem; font-weight: 600; margin-bottom: 4px; }}
  .vc-status {{
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    margin-bottom: 6px;
  }}
  .vc-note {{ font-size: 0.88rem; color: var(--ink-soft); }}
  .vc-arrow {{ display: flex; align-items: center; color: var(--ink-soft); font-size: 1.1rem; }}

  .timeline {{ display: flex; flex-direction: column; gap: 18px; }}
  .tl-item {{ display: flex; gap: 16px; }}
  .tl-year {{
    font-family: 'DM Mono', monospace;
    font-size: 0.82rem;
    color: var(--brand);
    width: 52px;
    flex-shrink: 0;
    padding-top: 2px;
  }}
  .tl-content {{ border-left: 2px solid var(--rule); padding-left: 16px; }}
  .tl-event {{ font-weight: 600; font-size: 1.05rem; margin-bottom: 2px; }}
  .tl-detail {{ font-size: 0.95rem; color: var(--ink-soft); line-height: 1.45; }}

  .risk-table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
  .risk-table th {{
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.03em;
    text-align: left;
    background: var(--rail);
    color: var(--brand-2);
    padding: 9px 10px;
  }}
  .risk-table td {{ padding: 10px; border-bottom: 1px solid var(--rule); vertical-align: top; }}
  .risk-name {{ font-weight: 600; }}
  .risk-horizon {{ font-family: 'DM Mono', monospace; font-size: 0.82rem; white-space: nowrap; }}
  .risk-note {{ color: var(--ink-soft); font-size: 0.9rem; }}
  .risk-pill {{
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    padding: 3px 9px;
    border-radius: 3px;
    white-space: nowrap;
  }}

  .callout {{
    background: rgba(36, 31, 26, 0.045);
    border-left: 3px solid var(--brand);
    padding: 18px 24px;
  }}
  .callout-tag {{
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: var(--brand);
    margin-bottom: 8px;
  }}
  .callout-text {{ font-style: italic; font-size: 1.15rem; margin: 0; line-height: 1.5; }}

  .rating-block {{ display: flex; flex-direction: column; gap: 4px; }}
  .rating-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    color: var(--ink-soft);
  }}
  .rating-dots {{ font-size: 1.3rem; letter-spacing: 4px; }}
  .dot-full {{ color: var(--brand); }}
  .dot-empty {{ color: var(--rule); }}
  .rating-note {{ font-size: 0.92rem; color: var(--ink-soft); margin: 2px 0 0; }}

  /* Waterfall: een cijfermatige "brug" van start- naar eindwaarde */
  .waterfall {{ max-width: 740px; }}
  .waterfall-row {{
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 8px 0; border-bottom: 1px solid var(--rule);
    font-family: 'DM Mono', monospace; font-size: 0.9rem;
  }}
  .waterfall-row.waterfall-start, .waterfall-row.waterfall-end {{ font-weight: 600; border-bottom: 2px solid var(--ink); }}
  .wf-positive {{ color: #3E7A4F; }}
  .wf-negative {{ color: #A6402F; }}

  /* Gauge: één score binnen zones (bijv. Altman Z-Score) */
  .gauge-track {{
    position: relative; display: flex; height: 20px; border-radius: 10px;
    overflow: hidden; max-width: 480px; margin: 10px 0;
  }}
  .gauge-zone {{ height: 100%; }}
  .gauge-marker {{
    position: absolute; top: -4px; width: 3px; height: 28px;
    background: var(--ink); transform: translateX(-50%);
  }}
  .gauge-value {{ font-family: 'Cormorant Garamond', serif; font-size: 1.6rem; font-weight: 600; }}
  .gauge-note {{ font-size: 0.85rem; color: var(--ink-soft); max-width: 60ch; }}

  /* Heatmap: rijen x kolommen-grid (bijv. gevoeligheidsanalyse) */
  .heatmap {{ display: flex; flex-direction: column; max-width: 720px; overflow-x: auto; }}
  .heatmap-row {{ display: flex; }}
  .heatmap-cell {{
    flex: 1; min-width: 90px; padding: 8px 10px; text-align: center;
    font-family: 'DM Mono', monospace; font-size: 0.82rem;
    border: 1px solid var(--rule);
  }}
  .heatmap-header {{ font-weight: 600; background: var(--rail); color: var(--brand-2); border: none; }}
  .heatmap-rowlabel {{ text-align: left; font-weight: 600; background: transparent; border: none; }}

  /* Custom-block: bevat vrij door Claude ontworpen HTML/CSS na validatie --
     overflow:hidden en een max-breedte zorgen dat een technisch geldig maar
     te groot element de pagina-layout niet kan verstoren. */
  .custom-block {{ overflow: hidden; max-width: 100%; }}

  /* Metric-cards: rij losse kaarten met kerncijfer + optionele trend */
  .metric-cards {{ display: flex; flex-wrap: wrap; gap: 14px; }}
  .metric-card {{
    background: var(--paper-alt, #fbf9f3); border: 1px solid var(--rule);
    border-radius: 4px; padding: 14px 18px; min-width: 140px;
  }}
  .metric-card-label {{ font-family: 'DM Mono', monospace; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ink-soft); }}
  .metric-card-value {{ font-family: 'Cormorant Garamond', serif; font-size: 1.7rem; font-weight: 600; margin-top: 4px; }}
  .metric-trend {{ font-family: 'DM Mono', monospace; font-size: 0.78rem; display: block; margin-top: 4px; }}
  .trend-up {{ color: #3E7A4F; }}
  .trend-down {{ color: #A6402F; }}

  /* Stacked-bar: één balk in gekleurde segmenten */
  .stacked-bar {{ display: flex; height: 22px; border-radius: 4px; overflow: hidden; max-width: 560px; }}
  .stacked-segment {{ height: 100%; }}
  .stacked-legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; font-family: 'DM Mono', monospace; font-size: 0.78rem; }}
  .stacked-legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}

  /* Donut: cirkeldiagram met legenda */
  .donut-row {{ display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }}
  .donut-legend {{ flex-direction: column; gap: 6px; margin-top: 0; }}

  /* Quote-block: uitgelicht, editorial citaat */
  .quote-block {{ border-left: 3px solid var(--brand); padding-left: 20px; }}
  .quote-text {{ font-family: 'Cormorant Garamond', serif; font-style: italic; font-size: 1.3rem; line-height: 1.5; color: var(--ink); }}
  .quote-attribution {{ font-family: 'DM Mono', monospace; font-size: 0.82rem; color: var(--ink-soft); margin-top: 8px; }}

  /* Milestone-progress: voortgang naar een specifiek doel */
  .milestone-label {{ font-size: 0.95rem; color: var(--ink-soft); margin-bottom: 6px; }}
  .milestone-track {{ background: var(--rule); border-radius: 10px; height: 14px; max-width: 480px; overflow: hidden; }}
  .milestone-fill {{ background: var(--brand); height: 100%; }}
  .milestone-value {{ font-family: 'DM Mono', monospace; font-size: 0.85rem; margin-top: 6px; }}

  .report-footer {{
    background: var(--rail);
    color: var(--rail-text-soft);
    margin-top: 56px;
    padding: 40px 56px;
  }}
  .footer-inner {{
    max-width: var(--content-max);
    margin: 0 auto;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    flex-wrap: wrap;
    gap: 24px;
  }}
  .footer-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--brand-2);
    margin-bottom: 10px;
  }}
  .footer-sources {{
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    color: var(--rail-text-soft);
  }}
  .footer-credit {{
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--rail-text-soft);
    white-space: nowrap;
  }}
  .footer-mark {{
    color: var(--rail-text);
    border: 1px solid var(--brand-2);
    padding: 2px 8px;
    letter-spacing: 0.1em;
  }}
</style>
</head>
<body>
<nav class="toc">
  <div class="toc-inner">
    {nav_snapshot}
    {nav_items}
  </div>
</nav>

<header class="hero">
  <div class="hero-inner">
    <div class="eyebrow">
      <span>THE COLLECTIVE EDGE</span><span class="divider">/</span>
      <span>COMPANY DEEP DIVE</span><span class="divider">/</span>
      <span>{html.escape(timestamp_str.split(' ')[0])}</span>
    </div>
    <h1>{html.escape(display_name)}</h1>
    <div class="hero-sub">{html.escape(ticker)}{country_bit}</div>
    <div class="hero-meta-row">
      {f'<span class="meta-item">{html.escape(extra_context)}</span>' if extra_context else ''}
      {review_badge}
    </div>
    {hero_snapshot_html}
  </div>
</header>

<main>
<div class="content">
  {snapshot_block}
  {section_blocks}
  {executive_summary_html}
  {issues_html}
  {lineage_html}
</div>
<footer class="report-footer">
  <div class="footer-inner">
    <div class="footer-col">
      <div class="footer-label">Data Sources</div>
      <div class="footer-sources">
        <span>Yahoo Finance (yfinance) — financial statements &amp; market data</span>
        <span>Alpha Vantage News Sentiment — recent news coverage</span>
        <span>Claude (Anthropic) web search — supplementary verification &amp; recent developments</span>
      </div>
    </div>
    {library_footer_html}
    <div class="footer-credit">
      <span class="footer-mark">TCE</span>
      <span>The Collective Edge · {html.escape(ticker)} Deep Dive · {html.escape(timestamp_str)}</span>
    </div>
  </div>
</footer>
</main>
<script>
  // Sleep-om-te-scrollen voor de horizontale navigatiebalk -- puur een
  // gemaksfunctie, de balk werkt ook zonder JS al (overflow-x: auto geeft
  // een gewone scrollbalk/trackpad-scroll).
  (function() {{
    var toc = document.querySelector('nav.toc .toc-inner');
    if (!toc) return;
    var isDown = false, startX, scrollLeft;
    toc.addEventListener('mousedown', function(e) {{
      isDown = true;
      toc.classList.add('grabbing');
      startX = e.pageX - toc.offsetLeft;
      scrollLeft = toc.scrollLeft;
    }});
    window.addEventListener('mouseup', function() {{
      isDown = false;
      toc.classList.remove('grabbing');
    }});
    toc.addEventListener('mousemove', function(e) {{
      if (!isDown) return;
      e.preventDefault();
      var x = e.pageX - toc.offsetLeft;
      toc.scrollLeft = scrollLeft - (x - startX);
    }});
  }})();

  // Markeert de nav-link van de sectie die momenteel in beeld is, zodat je
  // tijdens het scrollen altijd ziet waar je in het rapport bent -- zelfde
  // patroon als in de referentierapporten.
  (function() {{
    var sections = document.querySelectorAll('main .section[id]');
    var navLinks = document.querySelectorAll('nav.toc .toc-inner a');
    if (!sections.length || !navLinks.length) return;
    var observer = new IntersectionObserver(function(entries) {{
      entries.forEach(function(entry) {{
        if (!entry.isIntersecting) return;
        navLinks.forEach(function(a) {{ a.classList.remove('active'); }});
        var active = document.querySelector('nav.toc .toc-inner a[href="#' + entry.target.id + '"]');
        if (active) active.classList.add('active');
      }});
    }}, {{ rootMargin: '-20% 0px -70% 0px' }});
    sections.forEach(function(s) {{ observer.observe(s); }});
  }})();
</script>
</body>
</html>"""
