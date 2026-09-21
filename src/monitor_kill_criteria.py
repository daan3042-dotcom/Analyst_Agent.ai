"""
monitor_kill_criteria.py
Los, licht bewakingsscript: checkt voor opgeslagen bedrijven of de destijds
gestelde, machine-checkbare kill-criteria (zie track_record.py en de
kalibratiescore) inmiddels zijn geraakt -- ZONDER een volledig rapport
opnieuw te hoeven genereren. Haalt alleen verse SEC-cijfers op en rekent
de bekende, geverifieerde metrics opnieuw uit (dezelfde functie als de
hoofdpijplijn) -- geen Claude-aanroep, geen review, geen rapport.

Bedoeld om regelmatig te draaien (bijv. wekelijks), los van een volledige
herzien-analyse, als een snelle "is er iets veranderd"-check.

Gebruik:
    python monitor_kill_criteria.py           -- checkt ALLE opgeslagen bedrijven
    python monitor_kill_criteria.py UUUU       -- checkt alleen deze ticker
"""

import os
import sys

from forensics import compute_verified_metrics
from data.sec_data import fetch_sec_financials
from track_record import TRACK_RECORD_DIR, _evaluate_operator, _load_raw


def check_ticker(ticker: str) -> list[dict]:
    """Checkt de machine-checkbare kill-criteria uit de LAATSTE opgeslagen
    analyse van dit bedrijf tegen VERSE, actuele cijfers.

    Geeft een lijst bevindingen terug -- leeg als er geen eerdere analyse
    bestaat, of als die geen machine-checkbare criteria bevatte (bijv.
    rapporten van vóór de kalibratiescore-uitbreiding)."""
    history = _load_raw(ticker)
    if not history:
        return []
    structured_criteria = history[-1].get("structured_kill_criteria", [])
    if not structured_criteria:
        return []

    sec_result = fetch_sec_financials(ticker)
    fresh_metrics = compute_verified_metrics(sec_result if "error" not in sec_result else None, {})

    findings = []
    for criterion in structured_criteria:
        metric_key = criterion.get("metric_key")
        operator = criterion.get("operator")
        threshold = criterion.get("threshold")
        description = criterion.get("description", "")
        entry = fresh_metrics.get(metric_key)
        actual_value = entry.get("value") if isinstance(entry, dict) else entry

        if actual_value is None:
            findings.append({"ticker": ticker, "description": description, "status": "geen actueel cijfer beschikbaar om te checken"})
            continue
        breached = _evaluate_operator(actual_value, operator, threshold)
        if breached is None:
            continue  # onbekende operator -- niet gokken, gewoon overslaan
        findings.append({
            "ticker": ticker, "description": description,
            "threshold": threshold, "actual_value": actual_value, "breached": breached,
        })
    return findings


def main():
    if len(sys.argv) > 1:
        tickers = [sys.argv[1].upper()]
    else:
        if not os.path.exists(TRACK_RECORD_DIR):
            print("Nog geen opgeslagen analyses gevonden -- niets om te checken.")
            return
        tickers = sorted(f[:-5] for f in os.listdir(TRACK_RECORD_DIR) if f.endswith(".json"))

    if not tickers:
        print("Nog geen opgeslagen analyses gevonden -- niets om te checken.")
        return

    any_breach = False
    any_checkable = False
    for ticker in tickers:
        for finding in check_ticker(ticker):
            if "breached" in finding:
                any_checkable = True
                if finding["breached"]:
                    any_breach = True
                    print(f"[GERAAKT] {finding['ticker']}: {finding['description']} "
                          f"(drempel {finding['threshold']}, actueel {finding['actual_value']})")
                else:
                    print(f"[intact]  {finding['ticker']}: {finding['description']} "
                          f"(drempel {finding['threshold']}, actueel {finding['actual_value']})")
            else:
                print(f"[?]       {finding['ticker']}: {finding['description']} -- {finding['status']}")

    print()
    if not any_checkable:
        print("Geen machine-checkbare kill-criteria gevonden om te controleren "
              "(rapporten van vóór de kalibratiescore-uitbreiding hebben deze nog niet).")
    elif any_breach:
        print("LET OP: minstens 1 kill-criterium geraakt -- zie hierboven.")
    else:
        print("Geen enkel kill-criterium geraakt.")


if __name__ == "__main__":
    main()
