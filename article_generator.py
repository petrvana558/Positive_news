import json
import logging
import os
import anthropic

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def generate_article(article: dict) -> dict:
    """
    Vygeneruje pozitivní český článek z původní zprávy pomocí Claude.

    Vrátí: {"headline": str, "content": str, "image_query": str}
    """
    client = _get_client()

    keywords_str = ""
    if article.get("extracted_keywords"):
        keywords_str = f"Klíčová slova tématu: {', '.join(article['extracted_keywords'])}"

    system_prompt = """Jsi redaktor webu Pozitivní zprávy, který píše inspirující a povzbuzující
články v češtině. Tvůj styl je:
- Teplý, přátelský a optimistický
- Zaměřený na naději, pokrok a dobro ve světě
- Srozumitelný pro širokého čtenáře
- Přesný – nepřehánět, ale zdůraznit pozitivní aspekty

VŽDY piš v češtině, i když je původní zpráva anglicky.
Odpovídej VŽDY pouze validním JSON objektem."""

    user_prompt = f"""Na základě této zprávy napiš pozitivní článek v češtině:

Původní titulek: {article['title']}
Původní perex: {article.get('description', '')}
Zdroj: {article.get('source_name', 'Neznámý')}
{keywords_str}

Napiš JSON v tomto přesném formátu:
{{
  "headline": "Chytlavý a pozitivní český titulek (max 100 znaků)",
  "content": "Plný text článku v češtině, 3-4 odstavce, cca 300-400 slov. Každý odstavec odděl znakem \\n\\n. Článek musí být fakticky podložený původní zprávou, ale napsaný pozitivně a inspirativně.",
  "image_query": "Stručné anglické klíčové slovo pro hledání fotky na Unsplash (1-3 slova, bez uvozovek)"
}}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
        )

        raw = message.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]

        result = json.loads(raw)

        return {
            "headline": result.get("headline", article["title"])[:500],
            "content": result.get("content", ""),
            "image_query": result.get("image_query", "positive news"),
        }

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Chyba při parsování článku: {e}")
        return {
            "headline": article["title"],
            "content": article.get("description", ""),
            "image_query": "happy people",
        }
    except anthropic.APIError as e:
        logger.error(f"Claude API chyba při generování článku: {e}")
        return {
            "headline": article["title"],
            "content": article.get("description", ""),
            "image_query": "positive",
        }
