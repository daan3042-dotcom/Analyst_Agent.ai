"""
tools.py
Tool-definities voor Claude: dit is de "menukaart" die het model te zien
krijgt, los van de code die de tools daadwerkelijk uitvoert (die staat in
data_fetch.py). Nieuwe tools voeg je hier toe als extra dict in NEWS_TOOLS,
en handel je af in run_tool().

Let op: WEB_SEARCH_TOOL is een "server tool" -- Anthropic voert de
zoekopdracht zelf uit, binnen dezelfde API-call. Die komt dus NOOIT als
"tool_use"-blok bij run_tool() terecht (zie analyst_agent.py, dat filtert al
op block.type == "tool_use"). Hij staat hier toch, omdat dit de plek is waar
alle tool-definities samenkomen die je aan de Claude-call meegeeft.
"""

from data.commodity_data import fetch_commodity_price, fetch_fx_rate
from data.data_fetch import fetch_recent_news, compute_event_price_reaction
from analysis.self_consistency import assess_with_consistency
from analysis.financial_model import compute_sensitivity, project_scenario, run_monte_carlo_simulation
from knowledge.library_search import search_library

# De tool-definitie die naar Claude gestuurd wordt via de `tools=`-parameter.
# Claude leest alleen "name" en "description" om te beslissen OF en WANNEER
# de tool aangeroepen wordt -- vandaar dat description zo belangrijk is.
NEWS_TOOL = {
    "name": "get_recent_news",
    "description": (
        "Haal recent nieuws en sentiment-scores op voor een aandelenticker. "
        "Gebruik deze tool wanneer je actuele ontwikkelingen, earnings-nieuws "
        "of marktsentiment nodig hebt over een specifiek bedrijf (bijv. voor "
        "sectie 12 'Recent Developments' van het rapport). Geef geen nieuws "
        "op basis van eigen kennis als deze tool beschikbaar is."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {
                "type": "string",
                "description": "De ticker van het bedrijf, bijv. 'NKE' of 'ASML.AS'",
            }
        },
        "required": ["ticker"],
    },
}

# Ingebouwde Anthropic-tool: geen input_schema/beschrijving nodig, dat regelt
# Anthropic zelf. max_uses is een HARDE grens op het aantal zoekopdrachten
# per analyse. Verhoogd van 3 naar 5: sectie 12 vereist nu altijd minstens 1
# gerichte zoekopdracht naar materieel nieuws (volledigheid boven minimale
# kosten, op DD's verzoek), plus ruimte voor incidentele feitverificatie
# elders. Elke zoekopdracht kost apart geld ($10 per 1000 searches) bovenop
# de gewone tokenkosten -- vandaar nog steeds een harde grens, geen onbeperkt
# zoeken.
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}

# Doorzoekt DD's lokale bibliotheek van vakliteratuur (bijv. The Intelligent
# Investor) op betekenis. De beschrijving bevat een expliciete instructie om
# fragmenten nooit letterlijk te citeren -- dit is zowel een auteursrecht-
# veiligheidsmaatregel als de juiste manier om een RAG-tool te gebruiken:
# als inspiratie voor eigen redenering, niet als bron om na te typen.
PLAYBOOK_TOOL = {
    "name": "search_playbook",
    "description": (
        "Doorzoek een curated bibliotheek van financiële vakliteratuur op een "
        "specifiek concept of vraagstuk. Gebruik dit wanneer een gevestigd "
        "theoretisch kader je analyse kan verscherpen (bijv. concentratierisico, "
        "margin of safety, waarderingsdiscipline, gedragsfinanciering bij "
        "scenario-analyse). BELANGRIJK: de teruggegeven fragmenten zijn puur "
        "inspiratie voor je EIGEN redenering -- citeer, parafraseer of "
        "reproduceer ze NOOIT (ook niet gedeeltelijk) in het rapport zelf. "
        "Pas het onderliggende concept toe in je eigen woorden."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Een concreet concept of vraag, bijv. 'hoe beoordeel je concentratierisico in een portfolio'",
            }
        },
        "required": ["query"],
    },
}

# Deterministisch scenario-rekenmodel voor sectie 15. Claude geeft ALLEEN de
# aannames op -- de tool gebruikt zelf de al-geverifieerde SEC-omzet als
# startpunt en rekent de rest uit. Dit voorkomt dat Claude zelf jaar-voor-
# jaar gaat rekenen (waar eerder concrete fouten uit voortkwamen).
PROJECTION_TOOL = {
    "name": "run_financial_projection",
    "description": (
        "Bereken een deterministische, meerjarige projectie voor één scenario "
        "(bijv. bear, base, of bull) in sectie 15 -- inclusief D&A, rentelasten "
        "op bestaande schuld en werkkapitaaleffecten, afgeleid uit de echte "
        "historische SEC-cijfers. Geef ALLEEN de 3 kernaannames op -- "
        "groeipercentage, operating margin, capex als percentage van omzet. "
        "De tool gebruikt zelf de meest recente, geverifieerde omzet als "
        "startpunt en rekent de rest (inclusief een gekoppelde resultaten- "
        "rekening en kasstroom) deterministisch uit. Roep deze tool 3x aan "
        "(één keer per scenario) in plaats van zelf jaar-voor-jaar te rekenen "
        "-- dat leidde eerder tot concrete rekenfouten. Voor het scenario met "
        "naam 'base' krijg je er automatisch een vergelijking bij met wat de "
        "markt momenteel impliciet inprijst (reverse-DCF) -- neem die "
        "vergelijking over in sectie 15 als die wordt meegegeven. Gebruik "
        "daarna ook run_sensitivity_analysis (met je base-case-aannames) om "
        "te laten zien welke aanname de uitkomst het meest stuurt. Optioneel: "
        "geef een grove kans (probability_pct) op voor elk van de 3 scenario's "
        "(moeten samen ~100% zijn) -- zodra alle drie een kans hebben gekregen, "
        "berekent de tool automatisch een kans-gewogen verwachte FCF."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scenario_name": {"type": "string", "description": "bijv. 'bear', 'base', of 'bull'"},
            "revenue_growth_pct": {"type": "number", "description": "aangenomen jaarlijkse omzetgroei in procent, bijv. 5 voor 5% (negatief mag)"},
            "operating_margin_pct": {"type": "number", "description": "aangenomen operating margin in procent"},
            "capex_pct_of_revenue": {"type": "number", "description": "aangenomen capex als percentage van omzet"},
            "years": {"type": "integer", "description": "aantal projectiejaren (1-10), standaard 5"},
            "probability_pct": {"type": "number", "description": "optioneel: geschatte kans (%) dat dit scenario zich voordoet; de 3 scenario's moeten samen ~100% zijn"},
        },
        "required": ["scenario_name", "revenue_growth_pct", "operating_margin_pct", "capex_pct_of_revenue"],
    },
}

# Gevoeligheidsanalyse: laat zien welke van de 3 kernaannames de FCF-uitkomst
# het meest stuurt. Draait volledig in code (6 extra berekeningen op basis
# van dezelfde deterministische projectiefunctie) -- kost dus geen extra
# tokens, alleen wat rekentijd.
SENSITIVITY_TOOL = {
    "name": "run_sensitivity_analysis",
    "description": (
        "Bepaal welke van de 3 kernaannames (omzetgroei, operating margin, "
        "capex%) de FCF-uitkomst het meest stuurt, door elk om beurten met een "
        "vaste stap te verhogen/verlagen terwijl de andere twee gelijk "
        "blijven. Gebruik dit ÉÉN keer in sectie 15, met je base-case-aannames "
        "als invoer, om te laten zien welke 1-2 aannames het meeste "
        "aandacht/onderzoek verdienen -- dit is waardevoller dan alleen "
        "3 losse bear/base/bull-scenario's, want het isoleert PER aanname "
        "hoeveel invloed die heeft."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scenario_name": {"type": "string", "description": "gebruik 'base' -- de sensitivity wordt rond dit scenario berekend"},
            "revenue_growth_pct": {"type": "number", "description": "je base-case aanname voor jaarlijkse omzetgroei in procent"},
            "operating_margin_pct": {"type": "number", "description": "je base-case aanname voor operating margin in procent"},
            "capex_pct_of_revenue": {"type": "number", "description": "je base-case aanname voor capex als percentage van omzet"},
            "years": {"type": "integer", "description": "aantal projectiejaren (1-10), standaard 5"},
        },
        "required": ["scenario_name", "revenue_growth_pct", "operating_margin_pct", "capex_pct_of_revenue"],
    },
}

MONTE_CARLO_TOOL = {
    "name": "run_monte_carlo_simulation",
    "description": (
        "Draait duizenden simulaties met dezelfde bear/base/bull-aannames die "
        "je al voor de losse scenario's gebruikte, in plaats van 3 losse "
        "punten -- geeft een volledige verdeling van mogelijke uitkomsten "
        "terug (mediaan, spreiding, en percentielen zoals 'in 80% van de "
        "gevallen ligt de uitkomst tussen X en Y'). VERPLICHT: gebruik dit "
        "ÉÉN keer in sectie 15, NA je drie losse run_financial_projection-aanroepen, met "
        "dezelfde bear/base/bull-aannames als invoer. Dit voegt geen extra "
        "onzekerheid toe aan de aannames zelf -- het laat alleen zien hoe die "
        "onzekerheid zich vertaalt naar een volledige uitkomstverdeling."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "bear": {
                "type": "object",
                "description": "dezelfde bear-case-aannames als bij je run_financial_projection-aanroep",
                "properties": {
                    "revenue_growth_pct": {"type": "number"},
                    "operating_margin_pct": {"type": "number"},
                    "capex_pct_of_revenue": {"type": "number"},
                },
                "required": ["revenue_growth_pct", "operating_margin_pct", "capex_pct_of_revenue"],
            },
            "base": {
                "type": "object",
                "description": "dezelfde base-case-aannames als bij je run_financial_projection-aanroep",
                "properties": {
                    "revenue_growth_pct": {"type": "number"},
                    "operating_margin_pct": {"type": "number"},
                    "capex_pct_of_revenue": {"type": "number"},
                },
                "required": ["revenue_growth_pct", "operating_margin_pct", "capex_pct_of_revenue"],
            },
            "bull": {
                "type": "object",
                "description": "dezelfde bull-case-aannames als bij je run_financial_projection-aanroep",
                "properties": {
                    "revenue_growth_pct": {"type": "number"},
                    "operating_margin_pct": {"type": "number"},
                    "capex_pct_of_revenue": {"type": "number"},
                },
                "required": ["revenue_growth_pct", "operating_margin_pct", "capex_pct_of_revenue"],
            },
            "years": {"type": "integer", "description": "aantal projectiejaren (1-10), standaard 5, gebruik hetzelfde als bij je scenario's"},
        },
        "required": ["bear", "base", "bull"],
    },
}

# Grondstofprijzen -- relevant voor sectie 8 (Commodity/Market Sensitivity)
# bij bedrijven waarvan de resultaten sterk meebewegen met een specifieke
# grondstofprijs (bijv. aluminium bij Alcoa, koper bij Ero Copper).
COMMODITY_TOOL = {
    "name": "get_commodity_price",
    "description": (
        "Haal recente maandprijzen op voor een grondstof die relevant is voor "
        "dit bedrijf (bijv. aluminium, koper, olie). Gebruik dit voor sectie 8 "
        "(Commodity/Market Sensitivity) als het bedrijf's resultaten sterk "
        "afhangen van één specifieke grondstofprijs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "commodity": {
                "type": "string",
                "description": "Een van: WTI, BRENT, NATURAL_GAS, COPPER, ALUMINUM, WHEAT, CORN, COTTON, SUGAR, COFFEE",
            }
        },
        "required": ["commodity"],
    },
}

# Wisselkoersen -- relevant voor sectie 9 (Macro Exposure) bij bedrijven met
# materiele buitenlandse omzet/kosten in een andere valuta dan de rapportagevaluta.
FX_TOOL = {
    "name": "get_fx_rate",
    "description": (
        "Haal de actuele wisselkoers op tussen twee valuta. Gebruik dit voor "
        "sectie 9 (Macro Exposure) als het bedrijf materiele blootstelling "
        "heeft aan een vreemde valuta (bijv. omzet in EUR, rapportage in USD)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "from_currency": {"type": "string", "description": "3-letterige valutacode, bijv. 'EUR'"},
            "to_currency": {"type": "string", "description": "3-letterige valutacode, bijv. 'USD'"},
        },
        "required": ["from_currency", "to_currency"],
    },
}

# Zelfconsistentie-sampling: voor subjectieve oordelen (moat-score,
# risico-ernst) waar één losse, ongecontroleerde inschatting kan varieren.
# Vraagt hetzelfde oordeel 3x onafhankelijk, en geeft de mediaan terug --
# kost drie kleine, aparte Claude-calls, geen grote extra kosten.
CONSISTENCY_TOOL = {
    "name": "assess_with_consistency",
    "description": (
        "Voor belangrijke SUBJECTIEVE oordelen waar consistentie ertoe doet "
        "(bijv. de economic moat-score in sectie 4, of een risico-ernst-"
        "classificatie in sectie 11) -- in plaats van zelf één keer te "
        "beslissen, vraagt deze tool hetzelfde oordeel 3x onafhankelijk aan "
        "Claude en geeft de mediaan-score terug met een representatieve "
        "onderbouwing. Gebruik dit voor de belangrijkste 1-2 subjectieve "
        "scores per rapport, niet voor elke kleine inschatting -- elke "
        "aanroep kost drie extra, kleine Claude-calls."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "De specifieke vraag/het oordeel, bijv. 'Hoe sterk is de economic moat van dit bedrijf?'"},
            "context_data": {"type": "string", "description": "Relevante feiten om het oordeel op te baseren -- kort en gericht, niet het hele rapport"},
            "score_min": {"type": "number", "description": "Minimumscore van de schaal, bijv. 1"},
            "score_max": {"type": "number", "description": "Maximumscore van de schaal, bijv. 5"},
        },
        "required": ["question", "context_data", "score_min", "score_max"],
    },
}

# Event-study: zet 'dit leek belangrijk nieuws' om in verifieerbare koersdata
# rond een specifieke, gedateerde gebeurtenis.
EVENT_REACTION_TOOL = {
    "name": "get_event_price_reaction",
    "description": (
        "Meet de OBJECTIEVE koersreactie rond een specifieke, gedateerde "
        "gebeurtenis (bijv. een contractaankondiging, regelgevingsbesluit, "
        "of grote nieuwsupdate gevonden via web_search) -- geeft de "
        "procentuele koersverandering op de dag zelf en over een venster "
        "erna terug. Gebruik dit voor sectie 12 (Recent Developments) om "
        "materialiteit te onderbouwen met bewijs i.p.v. je eigen inschatting "
        "van hoe belangrijk iets was."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Het ticker-symbool"},
            "event_date": {"type": "string", "description": "Datum van de gebeurtenis, formaat YYYY-MM-DD"},
            "window_days": {"type": "integer", "description": "Aantal dagen na de gebeurtenis om de cumulatieve reactie over te meten, standaard 30"},
        },
        "required": ["ticker", "event_date"],
    },
}

# Lijst met alle tools die de agent kent -- straks in analyst_agent.py geef je
# dit door als tools=ALL_TOOLS in de Claude-call.
ALL_TOOLS = [NEWS_TOOL, WEB_SEARCH_TOOL, PLAYBOOK_TOOL, PROJECTION_TOOL, SENSITIVITY_TOOL, MONTE_CARLO_TOOL, COMMODITY_TOOL, FX_TOOL, CONSISTENCY_TOOL, EVENT_REACTION_TOOL]


def run_tool(tool_name: str, tool_input: dict, context: dict | None = None) -> dict:
    """Dispatcher: vertaalt een tool-aanroep van Claude naar de juiste
    Python-functie. Als je een nieuwe CLIENT-tool toevoegt aan ALL_TOOLS,
    voeg je hier ook een nieuwe 'if' toe die 'm afhandelt. Server tools
    (zoals web_search) komen hier nooit binnen.

    context is optioneel en bevat bedrijfsspecifieke, al-geverifieerde data
    (bijv. sec_result) voor tools die dat nodig hebben -- zo hoeft Claude
    zelf geen basisgetallen (zoals de startomzet) aan te leveren die hij
    fout zou kunnen overnemen."""
    context = context or {}
    if tool_name == "get_recent_news":
        return fetch_recent_news(tool_input["ticker"])
    if tool_name == "search_playbook":
        return search_library(tool_input["query"])
    if tool_name == "run_financial_projection":
        return project_scenario(tool_input, context)
    if tool_name == "run_sensitivity_analysis":
        return compute_sensitivity(tool_input, context)
    if tool_name == "run_monte_carlo_simulation":
        return run_monte_carlo_simulation(tool_input, context)
    if tool_name == "get_commodity_price":
        return fetch_commodity_price(tool_input["commodity"])
    if tool_name == "get_fx_rate":
        return fetch_fx_rate(tool_input["from_currency"], tool_input["to_currency"])
    if tool_name == "assess_with_consistency":
        return assess_with_consistency(
            context.get("client"), tool_input["question"], tool_input["context_data"],
            tool_input["score_min"], tool_input["score_max"],
        )
    if tool_name == "get_event_price_reaction":
        return compute_event_price_reaction(
            tool_input["ticker"], tool_input["event_date"], tool_input.get("window_days", 30),
        )

    return {"error": f"onbekende tool: {tool_name}"}
