"""
library_search.py
Doorzoekt de lokale bibliotheek (opgebouwd door library_index.py) op
BETEKENIS, niet op letterlijke woorden -- een zoekvraag over
"concentratierisico" vindt ook fragmenten die het hebben over "te veel
kapitaal in één sector beleggen", ook al delen ze geen woorden.

Dit wordt aangeroepen door tools.py wanneer de agent tijdens een analyse
besluit dat hij iets wil opzoeken in de bibliotheek. Geeft ALTIJD een klein
aantal korte fragmenten terug (nooit hele hoofdstukken), met vermelding van
uit welk boek elk fragment komt.

UPDATE 2: gebruikt Voyage AI's embeddings-API via RECHTSTREEKSE HTTP-
aanroepen (requests), NIET via het voyageai-pakket zelf. Reden: het
voyageai-pakket trekt zelf langchain_core binnen (voor een chunking-
hulpfunctie die we niet eens gebruiken), en DIE trekt op zijn beurt
uuid_utils binnen -- een ANDER gecompileerd pakket, dat op precies dezelfde
Windows-machine ook alweer geblokkeerd werd (na scipy/scikit-learn eerder).
requests is een pure-Python HTTP-bibliotheek zonder gecompileerde
dependencies, en wordt al probleemloos gebruikt door de rest van deze
agent (sec_data.py, data_fetch.py, etc.) -- dus dit voorkomt elke kans op
eenzelfde soort verrassing opnieuw.

BELANGRIJK: de import hieronder blijft BEWUST defensief (try/except), niet
een kale top-level import -- bibliotheek-zoeken is een van de negen tools,
geen kernfunctie; een importprobleem hier (nu heel onwaarschijnlijk, maar
niet uitgesloten) mag nooit de andere acht tools blokkeren."""

import os

try:
    import chromadb
    import requests
    _IMPORT_ERROR = None
except Exception as e:  # bewust breed, zelfde reden als eerder
    chromadb = None
    requests = None
    _IMPORT_ERROR = str(e)

DB_DIR = "library/chroma_db"
TOP_K = 3  # aantal fragmenten dat wordt teruggegeven per zoekopdracht
EMBED_MODEL = "voyage-4"
VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"

_collection = None


def _get_collection():
    global _collection
    if _collection is None:
        db_client = chromadb.PersistentClient(path=DB_DIR)
        _collection = db_client.get_or_create_collection("library")
    return _collection


def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    """Roept Voyage AI's embeddings-endpoint rechtstreeks aan via requests
    -- geen extra SDK-pakket, dus geen kans op een onverwachte gecompileerde
    dependency diep daarin verstopt."""
    api_key = os.environ.get("VOYAGE_API_KEY")
    response = requests.post(
        VOYAGE_EMBEDDINGS_URL,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={"input": texts, "model": EMBED_MODEL, "input_type": input_type},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()["data"]
    # Voyage garandeert de volgorde via het 'index'-veld, niet noodzakelijk
    # de volgorde van de lijst zelf -- expliciet sorteren om zeker te zijn.
    return [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]


def search_library(query: str) -> dict:
    """Zoekt de TOP_K meest relevante fragmenten voor de zoekvraag.

    Geeft een dict terug: {"fragments": [{"book": ..., "text": ...}, ...]}
    of {"error": ...} als de bibliotheek nog leeg is, als chromadb/requests
    niet geladen konden worden, als VOYAGE_API_KEY ontbreekt, of als de
    API-aanroep zelf faalt -- in elk van die gevallen blijft de rest van de
    agent gewoon werken, alleen deze ene tool niet. De fragmenten zijn
    bewust kort (zie CHUNK_WORDS in library_index.py) -- bedoeld om de
    eigen redenering van Claude te voeden, nooit om letterlijk te citeren."""
    if _IMPORT_ERROR is not None:
        return {"error": f"bibliotheek-zoeken niet beschikbaar (kon requests/chromadb niet laden: {_IMPORT_ERROR})"}
    if not os.environ.get("VOYAGE_API_KEY"):
        return {"error": "bibliotheek-zoeken niet beschikbaar (VOYAGE_API_KEY-omgevingsvariabele ontbreekt)"}

    collection = _get_collection()
    if collection.count() == 0:
        return {"error": "Bibliotheek is nog leeg. Draai eerst library_index.py om boeken te indexeren."}

    try:
        query_embedding = _embed([query], input_type="query")
    except Exception as e:
        return {"error": f"kon geen embedding ophalen bij Voyage AI: {e}"}

    results = collection.query(query_embeddings=query_embedding, n_results=TOP_K)

    fragments = [
        {"book": meta["book"], "text": doc}
        for doc, meta in zip(results["documents"][0], results["metadatas"][0])
    ]
    return {"fragments": fragments}
