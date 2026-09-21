"""
validate_custom_html.py
Veiligheidsnet voor door Claude vrij geschreven HTML/CSS-componenten (de
"custom"-grafiektype in render.py). Dit vervangt GEEN menselijke esthetische
beoordeling -- het vangt de OBJECTIEVE faalwijzen: onleesbare kleuren,
onbalans-HTML, en CSS die buiten zijn eigen vakje kan breken. Bij een fout
wordt het component NIET gerenderd -- de aanroeper valt terug op een veilige,
vaste weergave (zie render.py's _render_chart).
"""

import re

from reporting.color_safety import contrast_ratio

# CSS/HTML-patronen die een los component buiten zijn eigen vakje kunnen
# laten breken, of die simpelweg niet in een los ingesloten stukje HTML
# horen. Bewust ruim geinterpreteerd: liever een keer onterecht afwijzen
# dan een keer een gebroken of onveilige pagina toelaten.
BANNED_PATTERNS = [
    (r"<script", "bevat een <script>-tag, niet toegestaan in een los component"),
    (r"position\s*:\s*fixed", "gebruikt position:fixed, kan de pagina-layout breken"),
    (r"position\s*:\s*absolute", "gebruikt position:absolute, kan buiten zijn vakje uitsteken"),
    (r"position\s*:\s*sticky", "gebruikt position:sticky, kan de pagina-layout verstoren"),
    (r"@import", "bevat een CSS @import, niet toegestaan (geen externe bronnen)"),
    (r"<iframe", "bevat een <iframe>, niet toegestaan"),
    (r"on\w+\s*=", "bevat een inline event-handler (bijv. onclick=), niet toegestaan"),
]

# Herkent tekst/achtergrond-kleurparen die als hex-kleur binnen dezelfde
# CSS-regel staan -- dit is een heuristiek (vangt niet elke mogelijke
# CSS-notatie), maar vangt exact het patroon dat de eerdere leesbaarheidsbug
# veroorzaakte (letterlijke hex-kleuren voor tekst en achtergrond).
HEX_COLOR_PAIR_PATTERN = re.compile(
    r"color\s*:\s*(#[0-9a-fA-F]{3,8})[^;{}]*;[^{}]*?background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8})",
    re.IGNORECASE | re.DOTALL,
)

VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
MIN_CONTRAST_RATIO = 4.5  # WCAG AA voor normale tekst


def _tags_are_balanced(html_str: str) -> bool:
    """Simpele stack-gebaseerde check: elke openende tag moet een passende
    sluitende tag hebben, in de juiste volgorde. Geen volledige HTML-parser,
    maar genoeg om duidelijk kapotte/onbalans-HTML te vangen."""
    tag_pattern = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)[^>]*?(/?)>")
    stack = []
    for match in tag_pattern.finditer(html_str):
        closing, tag, self_closing = match.groups()
        tag = tag.lower()
        if tag in VOID_TAGS or self_closing:
            continue
        if closing:
            if not stack or stack[-1] != tag:
                return False
            stack.pop()
        else:
            stack.append(tag)
    return len(stack) == 0


def validate_custom_html(html_str: str) -> dict:
    """Hoofdfunctie: geeft {"valid": True} terug, of {"valid": False,
    "reason": "..."} met een concrete, begrijpelijke reden voor afwijzing.
    Nooit een crash -- onverwachte invoer leidt tot afwijzing, niet tot een
    uitzondering die de rest van het rapport zou kunnen laten stuklopen."""
    try:
        if not html_str or not html_str.strip():
            return {"valid": False, "reason": "leeg component"}

        if not _tags_are_balanced(html_str):
            return {"valid": False, "reason": "HTML-tags niet in balans (een open tag mist een sluitende tag, of andersom)"}

        for pattern, explanation in BANNED_PATTERNS:
            if re.search(pattern, html_str, re.IGNORECASE):
                return {"valid": False, "reason": explanation}

        for text_color, bg_color in HEX_COLOR_PAIR_PATTERN.findall(html_str):
            try:
                ratio = contrast_ratio(text_color, bg_color)
            except (ValueError, IndexError):
                continue  # onherkenbare kleurnotatie -- geen blokkerende fout, gewoon overslaan
            if ratio < MIN_CONTRAST_RATIO:
                return {
                    "valid": False,
                    "reason": (
                        f"onvoldoende contrast tussen tekstkleur {text_color} en achtergrond "
                        f"{bg_color} (ratio {ratio:.2f}, minimaal {MIN_CONTRAST_RATIO} vereist)"
                    ),
                }

        return {"valid": True}
    except Exception as e:  # laatste vangnet -- afwijzen, nooit de renderpijplijn laten crashen
        return {"valid": False, "reason": f"onverwachte fout tijdens validatie: {e}"}
