"""
lineage.py
Herkomst-manifest: voor de belangrijkste geverifieerde cijfers in het
rapport legt dit expliciet vast WELKE bron, WELK jaar/periode, en (waar
relevant) WELKE afleiding erachter zit -- zodat "waar komt dit getal
vandaan" nooit een raadsel is, en het rapport controleerbaar/reproduceerbaar
is in plaats van een zwarte doos.

Dit bouwt GEEN nieuwe data -- het documenteert alleen de herkomst van data
die al elders in de pijplijn (forensics.py, altman_z.py, piotroski_score.py,
reverse_dcf.py, fred_data.py, peer_analysis.py) berekend is. Puur Python,
geen extra API-calls, geen extra kosten.
"""

# (metric-key in verified_metrics, weergavenaam, korte afleiding-toelichting)
_VERIFIED_METRIC_FIELDS = [
    ("sec_operating_margin", "Operating margin", "OperatingIncomeLoss / Revenues"),
    ("sec_net_margin", "Net margin", "NetIncomeLoss / Revenues"),
    ("sec_free_cashflow", "Vrije kasstroom", "Operating cash flow - capex"),
    ("sec_revenue_yoy_growth", "Omzetgroei YoY", "Revenues, twee opeenvolgende jaren"),
    ("sec_net_income_yoy_growth", "Nettowinstgroei YoY", "NetIncomeLoss, twee opeenvolgende jaren"),
    ("sec_ebitda", "EBITDA", "OperatingIncomeLoss + D&A"),
    ("sec_net_debt", "Net schuld", "Langlopende + kortlopende schuld min cash"),
    ("sec_net_debt_to_ebitda", "Net debt/EBITDA", "Net schuld gedeeld door EBITDA"),
    ("sec_interest_coverage_ratio", "Rentedekking", "EBIT / rentelasten"),
    ("sec_normalized_ev_to_ebitda", "Genormaliseerde EV/EBITDA", "Market cap + net schuld, gedeeld door EBITDA"),
    ("sec_cash_conversion_cycle_days", "Cash conversion cycle", "DSO + DIO - DPO"),
    ("sec_roic", "ROIC", "NOPAT / geinvesteerd kapitaal (aanname: 25% belastingtarief)"),
]


def build_lineage_manifest(sec_result: dict | None, verified_metrics: dict | None,
                            altman_result: dict | None, piotroski_result: dict | None,
                            reverse_dcf_result: dict | None, macro_snapshot: dict | None,
                            peer_comparison: dict | None) -> list[dict]:
    """Bouwt een platte lijst herkomst-items voor de belangrijkste cijfers in
    het rapport. Elk item: {metric, value, source, period, note}. Ontbrekende
    of foutieve brondata wordt gewoon overgeslagen (geen crash, geen gok)."""
    manifest = []
    source_label = None
    if sec_result and "error" not in sec_result:
        source_label = sec_result.get("source", "SEC EDGAR")

    def add(metric, value, source, period=None, note=None):
        if value is None or source is None:
            return
        manifest.append({"metric": metric, "value": value, "source": source, "period": period, "note": note})

    if verified_metrics:
        for key, label, note in _VERIFIED_METRIC_FIELDS:
            entry = verified_metrics.get(key)
            if entry is None:
                continue
            if isinstance(entry, dict) and "value" in entry:
                add(label, entry["value"], source_label, entry.get("fiscal_year"), note)

        dupont = verified_metrics.get("sec_dupont_decomposition")
        if dupont:
            add("DuPont ROE-decompositie", dupont.get("roe"), source_label, dupont.get("fiscal_year"),
                "Nettomarge x omzet/activa x hefboom")

    if altman_result and "error" not in altman_result:
        add("Altman Z-Score", altman_result.get("z_score"), source_label,
            note="Berekend uit 5 SEC-balans-/resultatenposten")

    if piotroski_result and "error" not in piotroski_result:
        add("Piotroski F-Score", f"{piotroski_result['score']}/{piotroski_result['max_score']}",
            source_label, piotroski_result.get("fiscal_year"),
            "9-punts checklist over twee opeenvolgende jaren SEC-data")

    if reverse_dcf_result and "error" not in reverse_dcf_result:
        add("WACC (reverse-DCF)", f"{reverse_dcf_result['wacc']*100:.1f}%",
            "Berekend (CAPM)", note="Marktkapitalisatie, beta, schuldstructuur")
        add("Geïmpliceerde FCF-groei", f"{reverse_dcf_result['implied_annual_fcf_growth']*100:.1f}%",
            "Berekend (reverse-DCF)")

    if macro_snapshot and "error" not in macro_snapshot:
        for key, entry in macro_snapshot.items():
            if isinstance(entry, dict):
                add(key, entry.get("value"), "FRED", entry.get("date"))

    if peer_comparison and "error" not in peer_comparison:
        for entry in peer_comparison.values():
            if not isinstance(entry, dict) or "premium_discount_pct" not in entry:
                continue
            add(
                f"Peer-vergelijking: {entry.get('label', '')}",
                f"{entry['premium_discount_pct']*100:+.1f}%",
                "Yahoo Finance (bedrijf + peers)",
                note=f"t.o.v. gemiddelde van {entry.get('peer_count')} peer(s)",
            )

    return manifest
