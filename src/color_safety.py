"""
color_safety.py
Een objectieve, wiskundige veiligheidscontrole voor kleurcombinaties --
geen mening, een berekening. Implementeert de officiele WCAG-contrastratio-
formule (dezelfde standaard die webtoegankelijkheidstools gebruiken).

Waarom dit bestaat: als we Claude ooit vrijheid geven om eigen HTML/CSS te
ontwerpen (i.p.v. alleen vaste grafiek-types), is dit precies het soort
controle die de eerder gevonden bug (donkere tekst op een donkere
achtergrond) automatisch zou hebben gevangen -- voordat het rapport ooit
bij DD terechtkomt.
"""


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)  # #abc -> #aabbcc
    hex_color = hex_color[:6]  # negeer een eventueel alpha-kanaal (8-cijferige hex)
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG-formule: elk kanaal eerst naar lineaire ruimte omzetten, dan
    gewogen optellen (het menselijk oog is gevoeliger voor groen dan voor
    blauw, vandaar de verschillende gewichten)."""
    channels = []
    for c in rgb:
        c_srgb = c / 255
        if c_srgb <= 0.03928:
            c_linear = c_srgb / 12.92
        else:
            c_linear = ((c_srgb + 0.055) / 1.055) ** 2.4
        channels.append(c_linear)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(color_a: str, color_b: str) -> float:
    """Geeft de WCAG-contrastratio terug tussen twee hex-kleuren (1.0 =
    identiek, 21.0 = zwart-op-wit, het maximum)."""
    lum_a = _relative_luminance(_hex_to_rgb(color_a))
    lum_b = _relative_luminance(_hex_to_rgb(color_b))
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def is_readable(text_color: str, background_color: str, large_text: bool = False) -> bool:
    """WCAG AA-drempel: 4.5:1 voor normale tekst, 3:1 voor grote tekst
    (koppen). Dit is de officiele minimumnorm voor webtoegankelijkheid,
    geen eigen inschatting."""
    threshold = 3.0 if large_text else 4.5
    return contrast_ratio(text_color, background_color) >= threshold


if __name__ == "__main__":
    # De exacte situatie die de vroegere bug veroorzaakte: donkere tekst op
    # een donkere achtergrond.
    print("Donker-op-donker (de oude bug):", contrast_ratio("#2a2a2a", "#1a1a1a"),
          "-> leesbaar:", is_readable("#2a2a2a", "#1a1a1a"))
    print("Zwart-op-wit (perfect):", contrast_ratio("#000000", "#ffffff"),
          "-> leesbaar:", is_readable("#000000", "#ffffff"))
    print("Ons huidige paper/ink kleurenpaar:", contrast_ratio("#1a1815", "#f5f3ee"),
          "-> leesbaar:", is_readable("#1a1815", "#f5f3ee"))
