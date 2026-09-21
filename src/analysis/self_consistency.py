"""
self_consistency.py
Zelfconsistentie-sampling: een gevestigde techniek (Wang et al., 2022,
"Self-Consistency Improves Chain of Thought Reasoning") hier toegepast op
SUBJECTIEVE oordelen (bijv. een economic moat-score, een risico-ernst-
classificatie) in plaats van wiskundige redeneertaken.

In plaats van Claude één keer, ongecontroleerd, een score te laten bedenken
tijdens het schrijven, vraagt deze module hetzelfde oordeel 3x onafhankelijk
(losse, korte Claude-calls), en neemt de MEDIAAN in plaats van de eerste
losse gok. Dit voorkomt dat een score toevallig hoger of lager uitvalt dan
bij een volgende run van dezelfde analyse.
"""

import json
import statistics

SAMPLING_SYSTEM_PROMPT = """Je beoordeelt een specifieke vraag over een bedrijf, \
puur op basis van de gegeven context -- niets anders. Geef een score en een \
korte, concrete onderbouwing (1-2 zinnen). Antwoord UITSLUITEND met geldige \
JSON, geen inleidende tekst: {"score": <getal>, "rationale": "<onderbouwing>"}"""


def _extract_json_block(text: str) -> dict | None:
    """Zoekt het eerste geldige {...}-blok in de tekst, ook als het model er
    een inleidende zin voor zette -- dezelfde robuuste aanpak als bij de
    gespecialiseerde reviewers, om een JSON-parseerfout niet een hele sample
    te laten verliezen."""
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


def assess_with_consistency(client, question: str, context_data: str,
                              score_min: float, score_max: float, samples: int = 3) -> dict:
    """Vraagt hetzelfde oordeel `samples` keer onafhankelijk aan Claude, en
    neemt de mediaan-score. Eén mislukte sample (bijv. een parseerfout) mag
    de rest niet blokkeren -- pas als ALLE samples mislukken, geven we een
    foutmelding terug in plaats van een gok."""
    user_prompt = f"Vraag: {question}\n\nContext:\n{context_data}\n\nSchaal: {score_min} tot {score_max}."

    scores = []
    rationales = []
    for _ in range(samples):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                system=SAMPLING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_text = "".join(b.text for b in response.content if b.type == "text").strip()
            parsed = _extract_json_block(raw_text)
            if parsed is None:
                continue
            score = float(parsed["score"])
            score = max(score_min, min(score_max, score))  # binnen de schaal houden
            scores.append(score)
            rationales.append(str(parsed.get("rationale", "")))
        except Exception:
            continue

    if not scores:
        return {"error": "kon geen enkele zelfconsistente beoordeling verkrijgen (alle samples mislukten)"}

    median_score = statistics.median(scores)
    # De onderbouwing van de sample die het dichtst bij de mediaan zit, is
    # de meest representatieve -- niet zomaar de eerste of een gemiddelde
    # van teksten (dat zou onsamenhangend worden).
    closest_index = min(range(len(scores)), key=lambda i: abs(scores[i] - median_score))

    return {
        "median_score": median_score,
        "individual_scores": scores,
        "rationale": rationales[closest_index],
        "consistency_note": f"Gebaseerd op {len(scores)} onafhankelijke beoordelingen: {scores}",
    }
