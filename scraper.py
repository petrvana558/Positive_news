import feedparser
import requests
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "PozitivniZpravy/1.0 (+https://pozitivni-zpravy.cz)"
}


def _parse_date(entry) -> datetime:
    """Parsuje datum z RSS entry, fallback na aktuální čas."""
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc).replace(tzinfo=None)
            except Exception:
                pass
    return datetime.utcnow()


def _clean_html(text: Optional[str]) -> str:
    """Odstraní základní HTML tagy z textu."""
    if not text:
        return ""
    import re
    return re.sub(r"<[^>]+>", " ", text).strip()


def fetch_feed(url: str, language: str) -> list[dict]:
    """Stáhne a parsuje jeden RSS feed. Vrátí list článků."""
    articles = []
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.content)

        for entry in feed.entries[:20]:  # max 20 z každého zdroje
            title = _clean_html(getattr(entry, "title", ""))
            description = _clean_html(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            )
            link = getattr(entry, "link", "")

            if not title or not link:
                continue

            articles.append({
                "title": title[:500],
                "description": description[:1000],
                "url": link,
                "source_name": feed.feed.get("title", url),
                "language": language,
                "published_at": _parse_date(entry),
            })

    except requests.RequestException as e:
        logger.warning(f"Chyba při stahování feedu {url}: {e}")
    except Exception as e:
        logger.error(f"Neočekávaná chyba při parsování feedu {url}: {e}")

    return articles


def fetch_all_feeds(sources: list) -> list[dict]:
    """Stáhne všechny aktivní RSS feedy a vrátí sloučený list článků."""
    all_articles = []
    for source in sources:
        if not source.enabled:
            continue
        logger.info(f"Stahuji feed: {source.name}")
        articles = fetch_feed(source.url, source.language)
        logger.info(f"  → {len(articles)} článků z {source.name}")
        all_articles.extend(articles)

    # Deduplikace podle URL
    seen_urls = set()
    unique = []
    for a in all_articles:
        if a["url"] not in seen_urls:
            seen_urls.add(a["url"])
            unique.append(a)

    logger.info(f"Celkem unikátních článků: {len(unique)}")
    return unique
