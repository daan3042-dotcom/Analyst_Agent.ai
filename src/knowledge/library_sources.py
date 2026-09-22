"""
library_sources.py
Uitbreiding van de lokale bibliotheek: naast PDF's kunnen nu ook YouTube-
ondertitels en webartikelen worden geindexeerd, via dezelfde knip- en
embedding-pijplijn als library_index.py voor boeken.

Zelfde privacyprincipe als bij de boeken: dit is jouw eigen, persoonlijke
indexering van content waar je zelf (publiek) toegang toe hebt, en de
bibliotheek geeft nooit lange stukken letterlijk terug aan Claude -- alleen
korte fragmenten als inspiratie voor de eigen redenering.

Vereist twee gratis, kleine packages: youtube-transcript-api (ondertitels,
geen API-key nodig) en trafilatura (artikel-hoofdtekst zonder advertenties/
menu's).
"""

import re

import requests
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi

YOUTUBE_PATTERN = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})")


def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_PATTERN.search(url))


def _extract_youtube_id(url: str) -> str | None:
    match = YOUTUBE_PATTERN.search(url)
    return match.group(1) if match else None


def _fetch_youtube_title(video_id: str) -> str:
    """Haalt de videotitel op via YouTube's publieke oembed-endpoint --
    geen API-key nodig, geen extra package. Valt terug op het video-ID als
    dit om wat voor reden dan ook mislukt."""
    try:
        resp = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("title", video_id)
    except Exception:
        return video_id


def fetch_youtube_transcript(url: str) -> tuple[str, str] | None:
    """Haalt de ondertiteltekst van een openbare YouTube-video op (Nederlands
    of Engels, wat beschikbaar is). Geeft (titel, platte_tekst) terug, of
    None als er geen ondertitels beschikbaar zijn voor deze video."""
    video_id = _extract_youtube_id(url)
    if not video_id:
        return None
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["nl", "en"])
    except Exception:
        return None
    text = " ".join(segment["text"] for segment in transcript)
    if not text.strip():
        return None
    title = _fetch_youtube_title(video_id)
    return title, text


def fetch_article_text(url: str) -> tuple[str, str] | None:
    """Haalt de hoofdtekst van een webartikel op (zonder advertenties, menu's,
    of andere pagina-ruis). Geeft (titel, platte_tekst) terug, of None als
    het ophalen/extraheren mislukt."""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    text = trafilatura.extract(downloaded)
    if not text or not text.strip():
        return None
    metadata = trafilatura.extract_metadata(downloaded)
    title = metadata.title if metadata and metadata.title else url
    return title, text


def fetch_source_text(url: str) -> tuple[str, str] | None:
    """Detecteert automatisch of een URL een YouTube-video of een gewoon
    artikel is, en haalt de tekst op via de bijpassende methode. Geeft
    (titel, platte_tekst) terug, of None als het ophalen om wat voor reden
    dan ook niet lukte."""
    if is_youtube_url(url):
        return fetch_youtube_transcript(url)
    return fetch_article_text(url)
