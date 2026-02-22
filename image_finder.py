import logging
import os
import requests

logger = logging.getLogger(__name__)

UNSPLASH_BASE = "https://api.unsplash.com"
FALLBACK_IMAGE = "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800&q=80"
FALLBACK_ALT = "Pozitivní zprávy"


def find_image(query: str) -> dict:
    """
    Vyhledá fotku na Unsplash dle dotazu.

    Vrátí: {"url": str, "alt": str, "photographer": str, "photographer_url": str}
    """
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    if not access_key:
        logger.warning("UNSPLASH_ACCESS_KEY není nastaven, používám fallback obrázek")
        return {"url": FALLBACK_IMAGE, "alt": query, "photographer": "", "photographer_url": ""}

    try:
        response = requests.get(
            f"{UNSPLASH_BASE}/search/photos",
            params={
                "query": query,
                "per_page": 5,
                "orientation": "landscape",
                "content_filter": "high",
            },
            headers={"Authorization": f"Client-ID {access_key}"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        if not results:
            logger.info(f"Unsplash: žádné výsledky pro '{query}', zkouším 'positive'")
            return find_image("positive news happiness")

        photo = results[0]
        return {
            "url": photo["urls"]["regular"],
            "alt": photo.get("alt_description") or query,
            "photographer": photo["user"]["name"],
            "photographer_url": photo["user"]["links"]["html"],
        }

    except requests.HTTPError as e:
        logger.warning(f"Unsplash HTTP chyba: {e}")
    except (KeyError, ValueError) as e:
        logger.warning(f"Unsplash parsování chyba: {e}")
    except requests.RequestException as e:
        logger.warning(f"Unsplash request chyba: {e}")

    return {"url": FALLBACK_IMAGE, "alt": query, "photographer": "", "photographer_url": ""}
