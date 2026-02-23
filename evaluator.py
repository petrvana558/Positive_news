import json
import logging
import os
import anthropic

logger = logging.getLogger(__name__)

# Kolik článků posílat Claudovi – zbytek se přeskočí (úspora nákladů)
MAX_CLAUDE_EVALS = 25

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _build_keyword_context(keywords: list) -> str:
    positives = [f'"{k.word}" (+{k.weight})' for k in keywords if k.keyword_type == "positive"]
    negatives = [f'"{k.word}" (-{abs(k.weight)})' for k in keywords if k.keyword_type == "negative"]
    parts = []
    if positives:
        parts.append(f"Pozitivní klíčová slova: {', '.join(positives)}")
    if negatives:
        parts.append(f"Negativní klíčová slova: {', '.join(negatives)}")
    return "\n".join(parts)


def _keyword_boost(text: str, keywords: list) -> float:
    text_lower = text.lower()
    boost = 0.0
    for kw in keywords:
        if kw.word.lower() in text_lower:
            if kw.keyword_type == "positive":
                boost += kw.weight * 0.3
            else:
                boost -= abs(kw.weight) * 0.3
    return max(-3.0, min(3.0, boost))


def evaluate_article(article: dict, keywords: list) -> dict:
    """Ohodnotí pozitivitu článku pomocí Claude. Vrátí score, reason, keywords, category."""
    client = _get_client()
    keyword_context = _build_keyword_context(keywords)
    combined_text = f"{article['title']}\n\n{article.get('description', '')}"

    system_prompt = """Jsi hodnotitel pozitivity zpráv. Ohodnoť zprávu a urči její kategorii.

Škála hodnocení:
1-2: Velmi negativní (tragédie, katastrofy, konflikty)
3-4: Negativní nebo neutrálně negativní
5: Neutrální
6-7: Mírně pozitivní
8-9: Pozitivní, inspirující
10: Velmi pozitivní, výjimečně povzbuzující

Kategorie musí být přesně jedna z: ekonomika, domaci, zahranici, sport, zviratka, veda, ostatni
- ekonomika: finance, byznys, trhy, firmy, startupy, průmysl
- domaci: česká politika, české události, česká společnost
- zahranici: zahraniční politika, světové události, mezinárodní dění
- sport: všechny sporty, olympiáda, mistrovství
- zviratka: zvířata, příroda, záchrana zvířat, mazlíčci, divoká příroda, ekologie
- veda: věda, výzkum, technologie, vesmír, AI, medicína, inovace, objevy
- ostatni: kultura, životní styl, zdraví, cestování

Odpovídej VŽDY pouze validním JSON objektem v tomto formátu:
{"score": 7.5, "reason": "Krátké vysvětlení v češtině", "extracted_keywords": ["slovo1", "slovo2"], "category": "zahranici"}"""

    user_prompt = f"""Ohodnoť pozitivitu a kategorii této zprávy:

Jazyk: {article.get('language', 'cs')}
Titulek: {article['title']}
Perex: {article.get('description', '')}

{keyword_context if keyword_context else ''}

Vrať JSON s hodnocením."""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
        )

        raw = message.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]

        result = json.loads(raw)
        claude_score = float(result.get("score", 5.0))

        boost = _keyword_boost(combined_text, keywords)
        final_score = max(1.0, min(10.0, claude_score + boost))

        return {
            "score": round(final_score, 2),
            "reason": result.get("reason", ""),
            "extracted_keywords": result.get("extracted_keywords", []),
            "category": result.get("category", "ostatni"),
        }

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"Chyba při parsování odpovědi Claudu: {e}")
        return {"score": 5.0, "reason": "Nelze ohodnotit", "extracted_keywords": [], "category": "ostatni"}
    except anthropic.APIError as e:
        logger.error(f"Claude API chyba: {e}")
        return {"score": 5.0, "reason": "API chyba", "extracted_keywords": [], "category": "ostatni"}


def evaluate_batch(articles: list[dict], keywords: list, progress_cb=None) -> list[dict]:
    """
    Ohodnotí seznam článků. Předfiltruje klíčovými slovy a Claudovi pošle
    jen top MAX_CLAUDE_EVALS – výrazná úspora nákladů.
    progress_cb(done, total, title) se volá po každém ohodnoceném článku.
    """
    # Krok 1: spočítat keyword boost pro všechny (bez API)
    for article in articles:
        combined = f"{article['title']}\n\n{article.get('description', '')}"
        article["_kw_pre_score"] = _keyword_boost(combined, keywords)

    # Krok 2: seřadit a vybrat kandidáty pro Claude
    pre_sorted = sorted(articles, key=lambda x: x["_kw_pre_score"], reverse=True)
    candidates = pre_sorted[:MAX_CLAUDE_EVALS]
    skipped = pre_sorted[MAX_CLAUDE_EVALS:]

    logger.info(f"Claude ohodnotí {len(candidates)} článků, {len(skipped)} přeskočeno")

    # Krok 3: Claude ohodnotí kandidáty
    for idx, article in enumerate(candidates, 1):
        evaluation = evaluate_article(article, keywords)
        article["positivity_score"] = evaluation["score"]
        article["score_reason"] = evaluation["reason"]
        article["extracted_keywords"] = evaluation["extracted_keywords"]
        article["category"] = evaluation["category"]
        logger.debug(f"Score {evaluation['score']:.1f} [{evaluation['category']}]: {article['title'][:55]}")
        if progress_cb:
            progress_cb(idx, len(candidates), article["title"])

    # Krok 4: přeskočené články dostanou nízké skóre (bez Claude)
    for article in skipped:
        article["positivity_score"] = max(1.0, min(4.0, 3.0 + article["_kw_pre_score"]))
        article["score_reason"] = "Pre-filtrováno"
        article["extracted_keywords"] = []
        article["category"] = "ostatni"

    return sorted(articles, key=lambda x: x["positivity_score"], reverse=True)
