"""
track_record.py
De enige laag die "ervaring over tijd" toevoegt, in tegenstelling tot alle
andere onderdelen van deze agent (die maken ÉÉN rapport, op ÉÉN moment,
beter). Slaat sectie 17 (Monitoring & Kill-Criteria) en de kerncijfers van
elke analyse lokaal op per ticker. Bij een latere, hernieuwde analyse van
hetzelfde bedrijf krijgt Claude die vorige monitoring-sectie te zien, en
kan hij expliciet evalueren: klopten de eerdere waarschuwingen?

UPDATE: bewaart nu de VOLLEDIGE geschiedenis per ticker (elke run toegevoegd
aan een lijst), niet meer alleen de laatste stand -- dit legt de basis voor
een toekomstige kalibratiescore ("klopten onze eerdere aannames, over alle
bedrijven en alle runs heen?") en voor DD's eigen wens om zes maanden later
te kunnen zien of een eerdere aanname klopte. load_previous_report() geeft
nog steeds alleen de MEEST RECENTE snapshot terug (zelfde vorm als altijd),
zodat alle bestaande code die deze functie aanroept ongewijzigd blijft
werken. Leest ook bestaande, oudere bestanden (een los dict i.p.v. een
lijst) probleemloos -- geen migratiestap nodig.
"""

import json
import os
import re
from datetime import datetime

TRACK_RECORD_DIR = "track_record"


def _extract_section_17(analysis_text: str) -> str | None:
    """Haalt de platte tekst van sectie 17 (Monitoring & Kill-Criteria) uit
    de volledige analysetekst. Geeft None terug als de sectie niet
    gevonden kan worden -- geen gok, gewoon niets om te vergelijken."""
    match = re.search(
        r"17\.\s*Monitoring\s*&\s*Kill-Criteria\s*\n(.*?)(?=\n\d{1,2}\.\s|\Z)",
        analysis_text, re.DOTALL,
    )
    if not match:
        return None
    text = match.group(1).strip()
    return text if text else None


def _extract_structured_kill_criteria(analysis_text: str) -> list[dict]:
    """Haalt de MACHINE-CHECKBARE kill-criteria uit de 'kill-criteria-recap'-
    grafiek (indien aanwezig) -- alleen de criteria die Claude daadwerkelijk
    aan een geverifieerd cijfer kon koppelen (metric_key/operator/threshold),
    niet de losse, kwalitatieve tekstzinnen. Dit is de basis voor de
    kalibratiescore: alleen HIERMEE kan een latere run automatisch
    controleren of een drempelwaarde daadwerkelijk is geraakt. Geeft een
    lege lijst terug als de grafiek ontbreekt of geen enkel criterium
    gestructureerd was -- oudere rapporten (van vóór deze uitbreiding)
    hebben dit nooit, en dat is geen fout, gewoon nog geen data."""
    structured = []
    # Er kunnen meerdere chart-blokken in sectie 17 staan (bijv. ook een
    # 'heatmap' voor de gevoeligheidsanalyse van een eerdere sectie kan er
    # per ongeluk tussen zitten door de tekst-slicing hierboven) -- loop
    # daarom over ALLE chart-blokken vanaf sectie 17, en gebruik alleen het
    # type dat we zoeken.
    section_17_text = analysis_text[analysis_text.find("17."):] if "17." in analysis_text else ""
    for chart_match in re.finditer(r"```chart\s*\n(.*?)\n```", section_17_text, re.DOTALL):
        try:
            chart = json.loads(chart_match.group(1))
        except (json.JSONDecodeError, AttributeError):
            continue
        if chart.get("type") != "kill-criteria-recap":
            continue
        for criterion in chart.get("criteria", []):
            if isinstance(criterion, dict) and all(k in criterion for k in ("metric_key", "operator", "threshold")):
                structured.append({
                    "description": criterion.get("description", ""),
                    "metric_key": criterion["metric_key"],
                    "operator": criterion["operator"],
                    "threshold": criterion["threshold"],
                })
    return structured


def _load_raw(ticker: str) -> list[dict]:
    """Leest het opgeslagen bestand voor deze ticker en geeft het ALTIJD als
    lijst terug, ongeacht of het bestand in het oude (los dict) of nieuwe
    (lijst) formaat staat. Lege lijst bij een ontbrekend of corrupt bestand
    -- geen gok, gewoon "geen geschiedenis" in dat geval."""
    path = os.path.join(TRACK_RECORD_DIR, f"{ticker.upper()}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]  # oud formaat: een los dict wordt een geschiedenis van 1
    return []


def load_previous_report(ticker: str) -> dict | None:
    """Geeft de MEEST RECENTE opgeslagen snapshot voor deze ticker terug (of
    None als er nog geen eerdere analyse is) -- zelfde vorm als altijd, zodat
    bestaande code hier niets van hoeft te weten."""
    history = _load_raw(ticker)
    return history[-1] if history else None


def load_full_history(ticker: str) -> list[dict]:
    """Geeft de VOLLEDIGE geschiedenis van snapshots voor deze ticker terug,
    oud naar nieuw. Nog niet gebruikt in de hoofdpijplijn -- bedoeld als
    basis voor een toekomstige kalibratiescore over meerdere runs heen."""
    return _load_raw(ticker)


def save_report_snapshot(ticker: str, analysis_text: str, verified_metrics: dict) -> None:
    """Voegt dit rapport toe aan de geschiedenis voor deze ticker (bewaart
    ALLE eerdere snapshots, overschrijft niets)."""
    os.makedirs(TRACK_RECORD_DIR, exist_ok=True)
    history = _load_raw(ticker)
    history.append({
        "ticker": ticker.upper(),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "section_17_kill_criteria": _extract_section_17(analysis_text),
        "structured_kill_criteria": _extract_structured_kill_criteria(analysis_text),
        "verified_metrics": verified_metrics,
    })
    path = os.path.join(TRACK_RECORD_DIR, f"{ticker.upper()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, default=str, ensure_ascii=False)


def _evaluate_operator(actual: float, operator: str, threshold: float) -> bool | None:
    """Geeft True terug als het kill-criterium daadwerkelijk GERAAKT is (het
    cijfer voldoet aan de 'slechte' voorwaarde), False als de these nog
    intact is, en None bij een onbekende operator -- nooit gokken bij een
    operator die we niet herkennen, gewoon overslaan."""
    if operator == "<":
        return actual < threshold
    if operator == ">":
        return actual > threshold
    if operator == "<=":
        return actual <= threshold
    if operator == ">=":
        return actual >= threshold
    return None


def compute_calibration_score() -> dict:
    """Kalibratiescore over ALLE opgeslagen bedrijven heen: voor elk
    machine-checkbaar kill-criterium uit een OUDERE snapshot, checkt dit of
    een LATERE snapshot van HETZELFDE bedrijf laat zien of het criterium
    daadwerkelijk werd geraakt.

    Werkt alleen voor tickers met 2+ snapshots (een eerste-keer-analyse
    heeft nog niets om tegen te vergelijken) EN alleen voor criteria die
    destijds een metric_key/operator/threshold hadden (zie
    _extract_structured_kill_criteria) -- oudere rapporten van vóór die
    uitbreiding leveren dus nog niets op, en dat is geen fout, gewoon nog
    geen data om op te bouwen.

    Geeft terug: {"total_checkable_criteria": int, "held": int,
    "breached": int, "details": [...]}. 'held' = de these bleef intact,
    'breached' = het kill-criterium werd daadwerkelijk geraakt."""
    if not os.path.exists(TRACK_RECORD_DIR):
        return {"total_checkable_criteria": 0, "held": 0, "breached": 0, "details": []}

    details = []
    for filename in sorted(os.listdir(TRACK_RECORD_DIR)):
        if not filename.endswith(".json"):
            continue
        ticker = filename[:-5]
        history = _load_raw(ticker)
        if len(history) < 2:
            continue  # geen latere run om tegen te vergelijken

        for i, older in enumerate(history[:-1]):
            for criterion in older.get("structured_kill_criteria", []):
                metric_key = criterion.get("metric_key")
                operator = criterion.get("operator")
                threshold = criterion.get("threshold")
                if metric_key is None or operator is None or threshold is None:
                    continue
                # Check tegen ELKE latere snapshot (niet alleen de eerst-
                # volgende), zodat een criterium met een langere horizon
                # ook op een verdere run gecontroleerd kan worden.
                for newer in history[i + 1:]:
                    entry = newer.get("verified_metrics", {}).get(metric_key)
                    actual_value = entry.get("value") if isinstance(entry, dict) else entry
                    if actual_value is None:
                        continue
                    breached = _evaluate_operator(actual_value, operator, threshold)
                    if breached is None:
                        continue
                    details.append({
                        "ticker": ticker,
                        "description": criterion.get("description", ""),
                        "metric_key": metric_key,
                        "threshold_set_on": older["date"],
                        "checked_against_date": newer["date"],
                        "threshold": threshold,
                        "actual_value": actual_value,
                        "breached": breached,
                    })

    held = sum(1 for d in details if not d["breached"])
    breached = sum(1 for d in details if d["breached"])
    return {"total_checkable_criteria": len(details), "held": held, "breached": breached, "details": details}


if __name__ == "__main__":
    # Draai dit los met: python track_record.py
    result = compute_calibration_score()
    print(f"Kalibratiescore -- {result['total_checkable_criteria']} checkbare kill-criteria gevonden "
          f"(over alle bedrijven met 2+ analyses)\n")
    if result["total_checkable_criteria"] == 0:
        print("Nog geen data om te kalibreren -- dit begint pas te vullen zodra je een bedrijf")
        print("een tweede keer analyseert NA deze uitbreiding (oudere rapporten hadden nog geen")
        print("machine-checkbare kill-criteria).")
    else:
        pct_held = result["held"] / result["total_checkable_criteria"] * 100
        print(f"These intact gebleven: {result['held']} ({pct_held:.0f}%)")
        print(f"Kill-criterium geraakt: {result['breached']} ({100 - pct_held:.0f}%)\n")
        for d in result["details"]:
            status = "GERAAKT" if d["breached"] else "intact"
            print(f"  [{status}] {d['ticker']}: {d['description']}")
            print(f"           drempel: {d['threshold']} | werkelijk ({d['checked_against_date']}): {d['actual_value']}")
