"""
library_index.py
Bouwt (of ververst) de doorzoekbare bibliotheek van PDF-bronnen. Dit is een
LOS script dat je zelf lokaal draait wanneer je een boek toevoegt -- het
hoort niet bij de live analyse-pijplijn (analyst_agent.py), want indexeren
gebeurt eenmalig per boek, niet bij elke analyse.

Gebruik:
    python library_index.py

Zet je PDF's in de map ./library/pdfs/ (wordt aangemaakt als 'ie nog niet
bestaat). De doorzoekbare index komt in ./library/chroma_db/ te staan.

BELANGRIJK: dit script verwerkt alleen de PDF's die JIJ lokaal op je eigen
laptop hebt staan. Er gaat niets van de inhoud van deze boeken naar Claude
of naar de chat -- de tekst zelf blijft op jouw machine; alleen de korte
fragmenten die als embedding worden aangeboden gaan naar Voyage AI's API
(nodig om de vectoren te berekenen), niet naar Anthropic.

UPDATE: gebruikt Voyage AI's embeddings-API via RECHTSTREEKSE HTTP-
aanroepen (requests), niet via het voyageai-pakket. Reden: het voyageai-
pakket trekt zelf langchain_core -> uuid_utils binnen (een gecompileerd
pakket, ongebruikt voor onze doeleinden), wat op dezelfde Windows-machine
alweer werd geblokkeerd door hetzelfde beveiligingsbeleid dat eerder
scipy/scikit-learn blokkeerde. requests is pure Python, geen gecompileerde
dependencies, en wordt al probleemloos gebruikt door de rest van deze
agent.

VEREIST EENMALIG HERINDEXEREN als je van het oude sentence-transformers-
model komt: die vectoren zijn niet compatibel met Voyage's vectoren
(andere afmetingen, andere betekenisruimte) -- verwijder eerst
./library/chroma_db/ voordat je dit script draait.
"""

import os

import chromadb
import pdfplumber
import requests

from library_sources import fetch_source_text

PDF_DIR = "library/pdfs"
URLS_FILE = "library/urls.txt"
DB_DIR = "library/chroma_db"
CHUNK_WORDS = 400
CHUNK_OVERLAP = 50
EMBED_MODEL = "voyage-4"
EMBED_BATCH_SIZE = 128  # Voyage's API-limiet per aanroep
VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"


def extract_text(pdf_path: str) -> str:
    """Haalt alle platte tekst uit een PDF, pagina voor pagina."""
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n\n".join(text_parts)


def chunk_text(text: str, chunk_words: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Knipt platte tekst op in overlappende stukken van ongeveer chunk_words
    woorden. De overlap voorkomt dat een belangrijke zin precies op de knip
    valt en daardoor half verloren gaat in beide fragmenten."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_words
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_words - overlap
    return chunks


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Eén Voyage-aanroep (max EMBED_BATCH_SIZE teksten), rechtstreeks via
    requests -- zie de module-docstring voor waarom niet het voyageai-pakket."""
    api_key = os.environ.get("VOYAGE_API_KEY")
    response = requests.post(
        VOYAGE_EMBEDDINGS_URL,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={"input": texts, "model": EMBED_MODEL, "input_type": "document"},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()["data"]
    return [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]


def embed_documents(client, chunks: list[str]) -> list[list[float]]:
    """Berekent embeddings voor een lijst fragmenten, in batches van
    EMBED_BATCH_SIZE -- Voyage's API accepteert niet een onbeperkt aantal
    teksten in één aanroep. Het 'client'-argument blijft voor compatibiliteit
    met bestaande aanroepen/tests staan, maar wordt niet meer gebruikt --
    de HTTP-aanroep zelf gebeurt in _embed_batch()."""
    embeddings = []
    for i in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[i:i + EMBED_BATCH_SIZE]
        embeddings.extend(_embed_batch(batch))
    return embeddings


def process_urls(collection, client):
    """Verwerkt library/urls.txt: één URL per regel, YouTube-video's of
    gewone artikelen (automatisch gedetecteerd). Maakt het bestand aan met
    een korte uitleg als het nog niet bestaat -- net als de PDF-map."""
    if not os.path.exists(URLS_FILE):
        os.makedirs(os.path.dirname(URLS_FILE), exist_ok=True)
        with open(URLS_FILE, "w", encoding="utf-8") as f:
            f.write(
                "# Zet hier één URL per regel -- YouTube-video's of gewone\n"
                "# artikelen worden automatisch herkend. Regels die met # \n"
                "# beginnen worden genegeerd.\n"
            )
        print(f"      '{URLS_FILE}' aangemaakt. Zet daar links in en draai dit script opnieuw.")
        return

    with open(URLS_FILE, encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    if not urls:
        return

    for url in urls:
        # Dedupliceren op de URL zelf (niet op titel) -- zo weten we zeker
        # dat een al verwerkte link nooit dubbel wordt opgehaald en opnieuw
        # ge-embed, zelfs als de titel ooit anders zou worden opgehaald.
        existing = collection.get(where={"source_url": url}, limit=1)
        if existing["ids"]:
            print(f"      {url}: al geindexeerd, overgeslagen.")
            continue

        print(f"[2/3] Ophalen: {url}...")
        result = fetch_source_text(url)
        if result is None:
            print(f"      WAARSCHUWING: kon geen bruikbare tekst ophalen van {url} "
                  f"(geen ondertitels beschikbaar, of de pagina kon niet worden gelezen).")
            continue

        title, text = result
        chunks = chunk_text(text)
        print(f"      '{title}': {len(chunks)} fragmenten gevonden, embeddings genereren via Voyage AI...")
        embeddings = embed_documents(client, chunks)

        ids = [f"{title}::{i}" for i in range(len(chunks))]
        metadatas = [{"book": title, "source_url": url, "chunk_index": i} for i in range(len(chunks))]

        collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
        print(f"      Klaar: {len(chunks)} fragmenten toegevoegd aan de bibliotheek.")


def main():
    if not os.environ.get("VOYAGE_API_KEY"):
        print("FOUT: VOYAGE_API_KEY-omgevingsvariabele ontbreekt. Maak een sleutel aan op "
              "https://www.voyageai.com/ en zet 'm als omgevingsvariabele voordat je dit script draait.")
        return

    os.makedirs(PDF_DIR, exist_ok=True)
    pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]

    db_client = chromadb.PersistentClient(path=DB_DIR)
    collection = db_client.get_or_create_collection("library")

    if not pdf_files:
        print(f"      Geen PDF's gevonden in '{PDF_DIR}'.")

    for filename in pdf_files:
        book_title = os.path.splitext(filename)[0]

        # Sla boeken over die al geindexeerd zijn -- zo kun je dit script
        # veilig opnieuw draaien elke keer dat je een nieuw boek toevoegt,
        # zonder dat alles opnieuw verwerkt wordt.
        existing = collection.get(where={"book": book_title}, limit=1)
        if existing["ids"]:
            print(f"      {book_title}: al geindexeerd, overgeslagen.")
            continue

        print(f"[2/3] Verwerken: {book_title}...")
        path = os.path.join(PDF_DIR, filename)
        text = extract_text(path)
        if not text.strip():
            print(f"      WAARSCHUWING: geen tekst gevonden in {filename} "
                  f"(mogelijk een gescande afbeelding-PDF zonder doorzoekbare tekst).")
            continue

        chunks = chunk_text(text)
        print(f"      {len(chunks)} fragmenten gevonden, embeddings genereren via Voyage AI...")
        embeddings = embed_documents(None, chunks)

        ids = [f"{book_title}::{i}" for i in range(len(chunks))]
        metadatas = [{"book": book_title, "chunk_index": i} for i in range(len(chunks))]

        collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
        print(f"      Klaar: {len(chunks)} fragmenten toegevoegd aan de bibliotheek.")

    process_urls(collection, None)

    print(f"[3/3] Bibliotheek bevat nu {collection.count()} fragmenten in totaal.")


if __name__ == "__main__":
    main()
