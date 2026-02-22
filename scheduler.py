import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from database import SessionLocal, Article, Keyword, NewsSource, Setting
from scraper import fetch_all_feeds
from evaluator import evaluate_batch
from article_generator import generate_article
from image_finder import find_image

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Europe/Prague")

# ─── Live status ──────────────────────────────────────────────────────────────

_status = {
    "running": False,
    "started_at": None,
    "phase": "idle",          # idle | fetching | evaluating | generating | done | error
    "phase_detail": "",
    "evaluated": 0,
    "total_to_evaluate": 0,
    "last_run_at": None,
    "last_run_result": "",
}


def get_status() -> dict:
    s = dict(_status)
    if s["running"] and s["started_at"]:
        s["elapsed_secs"] = int((datetime.now() - s["started_at"]).total_seconds())
    else:
        s["elapsed_secs"] = 0
    s["started_at"] = s["started_at"].strftime("%H:%M:%S") if s["started_at"] else None
    return s


def _set(phase: str, detail: str = "", **kw):
    _status["phase"] = phase
    _status["phase_detail"] = detail
    _status.update(kw)


# ─── Settings helpers ─────────────────────────────────────────────────────────

def _get_setting(key: str, default: str) -> str:
    db = SessionLocal()
    try:
        row = db.query(Setting).filter(Setting.key == key).first()
        return row.value if row else default
    finally:
        db.close()


def _save_setting(key: str, value: str):
    db = SessionLocal()
    try:
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value))
        db.commit()
    finally:
        db.close()


def _get_interval_hours() -> float:
    return float(_get_setting("scrape_interval_hours", "2.0"))


def get_min_score() -> float:
    return float(_get_setting("min_publish_score", "6.0"))


def set_min_score(score: float):
    _save_setting("min_publish_score", str(round(score, 1)))


# ─── Hlavní job ───────────────────────────────────────────────────────────────

def run_scrape_job():
    """Hlavní job: stáhne zprávy, ohodnotí, vygeneruje top článků splňujících min. skóre."""
    if _status["running"]:
        logger.info("Scrape job už běží, přeskakuji")
        return

    _status["running"] = True
    _status["started_at"] = datetime.now()
    _status["evaluated"] = 0
    _status["total_to_evaluate"] = 0
    logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] Spouštím scrape job...")

    db = SessionLocal()
    try:
        sources = db.query(NewsSource).filter(NewsSource.enabled == True).all()
        keywords = db.query(Keyword).all()

        if not sources:
            logger.warning("Žádné aktivní zdroje RSS!")
            _set("error", "Žádné aktivní RSS zdroje")
            return

        # 1. Stažení feedů
        _set("fetching", "Stahuji RSS feedy…")
        articles = fetch_all_feeds(sources)
        if not articles:
            logger.warning("Žádné články ze zdrojů")
            _set("done", "Žádné nové články")
            return

        # 2. Filtrovat již uložené URL
        existing_urls = {row[0] for row in db.query(Article.original_url).all()}
        new_articles = [a for a in articles if a["url"] not in existing_urls]
        logger.info(f"Nových článků k hodnocení: {len(new_articles)}")

        if not new_articles:
            logger.info("Žádné nové články, přeskakuji")
            _set("done", "Žádné nové články k hodnocení")
            _save_setting("last_run_ts", str(time.time()))
            return

        # 3. Hodnocení
        from evaluator import MAX_CLAUDE_EVALS
        will_eval = min(len(new_articles), MAX_CLAUDE_EVALS)
        _set("evaluating", f"0 / {will_eval} ohodnoceno",
             evaluated=0, total_to_evaluate=will_eval)

        def _progress_cb(done: int, total: int, title: str):
            _status["evaluated"] = done
            _status["phase_detail"] = f"{done} / {total} ohodnoceno – {title[:40]}"

        ranked = evaluate_batch(new_articles, keywords, progress_cb=_progress_cb)

        # 4. Filtrovat dle minimálního skóre, max 6 článků
        min_score = get_min_score()
        eligible = [a for a in ranked if a["positivity_score"] >= min_score]
        top6 = eligible[:6]

        if not top6:
            msg = f"Žádný článek nedosáhl min. skóre {min_score:.1f} (max bylo {ranked[0]['positivity_score']:.1f})"
            logger.warning(msg)
            _set("done", msg)
            _save_setting("last_run_ts", str(time.time()))
            _status["last_run_at"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            _status["last_run_result"] = msg
            return

        logger.info(f"Top {len(top6)} článků (min. skóre {min_score}): {[a['title'][:40] for a in top6]}")

        # 5. Odznačit staré is_published
        db.query(Article).filter(Article.is_published == True).update({"is_published": False})

        # 6. Generovat nové články
        for i, raw in enumerate(top6, 1):
            _set("generating", f"Generuji článek {i}/{len(top6)}: {raw['title'][:45]}…")
            generated = generate_article(raw)
            image = find_image(generated["image_query"])

            article = Article(
                title=generated["headline"],
                content=generated["content"],
                original_url=raw["url"],
                source_name=raw["source_name"],
                published_at=raw["published_at"],
                positivity_score=raw["positivity_score"],
                image_url=image["url"],
                image_alt=image["alt"],
                is_published=True,
                language=raw["language"],
                category=raw.get("category", "ostatni"),
            )
            db.add(article)
            logger.info(f"Přidán článek: {generated['headline'][:60]} (score: {raw['positivity_score']})")

        db.commit()

        result_msg = f"OK – přidáno {len(top6)} článků (min. skóre {min_score:.1f})"
        logger.info("Scrape job dokončen.")
        _set("done", result_msg)
        _status["last_run_at"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        _status["last_run_result"] = result_msg
        _save_setting("last_run_ts", str(time.time()))

    except Exception as e:
        logger.error(f"Chyba v scrape jobu: {e}", exc_info=True)
        _set("error", str(e)[:120])
        db.rollback()
    finally:
        _status["running"] = False
        db.close()


# ─── Scheduler lifecycle ──────────────────────────────────────────────────────

def start_scheduler():
    import threading
    hours = _get_interval_hours()
    scheduler.add_job(
        run_scrape_job,
        trigger=IntervalTrigger(hours=hours),
        id="scrape_job",
        name="Scrape pozitivních zpráv",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info(f"Scheduler spuštěn – interval {hours}h")

    # Spustit startup scrape jen pokud uplynul dostatečný čas od posledního runu
    last_ts = float(_get_setting("last_run_ts", "0"))
    elapsed_hours = (time.time() - last_ts) / 3600
    if elapsed_hours >= hours:
        logger.info(f"Spouštím startup scrape (poslední run byl před {elapsed_hours:.1f}h)")
        threading.Thread(target=run_scrape_job, daemon=True).start()
    else:
        remaining = hours - elapsed_hours
        logger.info(f"Přeskakuji startup scrape – poslední run byl před {elapsed_hours:.1f}h, zbývá {remaining:.1f}h do dalšího")


def set_interval(hours: float):
    """Změní interval scheduleru za běhu a uloží do DB."""
    _save_setting("scrape_interval_hours", str(hours))
    if scheduler.running:
        scheduler.reschedule_job(
            "scrape_job",
            trigger=IntervalTrigger(hours=hours),
        )
        logger.info(f"Interval změněn na {hours}h")


def get_interval() -> float:
    return _get_interval_hours()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler zastaven")


def trigger_manual():
    run_scrape_job()
