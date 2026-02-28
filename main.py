import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Form, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

load_dotenv()

from database import get_db, init_db, Article, Keyword, NewsSource, Comment, ArticleRating, ArticleView, SiteVisit
from auth import (
    SESSION_COOKIE, create_session_token, is_authenticated,
    require_auth, verify_admin_password
)
from scheduler import (
    start_scheduler, stop_scheduler, trigger_manual, get_status,
    set_interval, get_interval, get_min_score, set_min_score,
    get_max_articles, set_max_articles,
)

ARCHIVE_AFTER_DAYS = 10  # Po tolika dnech přejdou zprávy z kategorií do archivu


def _get_client_ip(request: Request) -> str:
    """Extrahuje IP adresu klienta – kontroluje X-Forwarded-For (proxy/Railway)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Pozitivní zprávy", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ─── Veřejné stránky ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def homepage(request: Request, db: Session = Depends(get_db)):
    # Zaznamenat návštěvu
    db.add(SiteVisit(path="/", ip_address=_get_client_ip(request)))
    db.commit()

    # Homepage = Hot News: pouze nejnovější scrape (status="hotnews")
    articles = (
        db.query(Article)
        .filter(Article.is_published == True, Article.status == "hotnews")
        .order_by(Article.positivity_score.desc())
        .limit(24)
        .all()
    )
    return templates.TemplateResponse("index.html", {"request": request, "articles": articles})


@app.get("/clanek/{article_id}", response_class=HTMLResponse)
def article_detail(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Článek nenalezen")

    # Track page open
    db.add(ArticleView(article_id=article_id, view_type="open"))
    db.commit()

    comments = (
        db.query(Comment)
        .filter(Comment.article_id == article_id)
        .order_by(Comment.created_at.asc())
        .all()
    )
    ratings = db.query(ArticleRating).filter(ArticleRating.article_id == article_id).all()
    avg_rating = round(sum(r.rating for r in ratings) / len(ratings), 1) if ratings else None
    rating_count = len(ratings)

    return templates.TemplateResponse("article.html", {
        "request": request,
        "article": article,
        "comments": comments,
        "avg_rating": avg_rating,
        "rating_count": rating_count,
    })


@app.get("/clanek/{article_id}/original")
def article_original(article_id: int, db: Session = Depends(get_db)):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Článek nenalezen")
    db.add(ArticleView(article_id=article_id, view_type="click"))
    db.commit()
    return RedirectResponse(article.original_url, status_code=302)


@app.post("/clanek/{article_id}/comment")
def add_comment(
    article_id: int,
    request: Request,
    author_name: str = Form(""),
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    if content.strip():
        db.add(Comment(
            article_id=article_id,
            author_name=author_name.strip() or "Anonym",
            content=content.strip(),
        ))
        db.commit()
    return RedirectResponse(f"/clanek/{article_id}#diskuze", status_code=302)


@app.post("/clanek/{article_id}/rate")
def rate_article(
    article_id: int,
    rating: int = Form(...),
    db: Session = Depends(get_db),
):
    if 1 <= rating <= 5:
        db.add(ArticleRating(article_id=article_id, rating=rating))
        db.commit()
    return RedirectResponse(f"/clanek/{article_id}#hodnoceni", status_code=302)


@app.get("/archiv", response_class=HTMLResponse)
def archive(request: Request, db: Session = Depends(get_db)):
    # Archiv = zprávy starší než ARCHIVE_AFTER_DAYS dní (status="archive")
    articles = (
        db.query(Article)
        .filter(Article.is_published == True, Article.status == "archive")
        .order_by(Article.created_at.desc())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse("archive.html", {
        "request": request,
        "articles": articles,
        "page_title": "📚 Archiv",
        "page_subtitle": f"Zprávy starší než {ARCHIVE_AFTER_DAYS} dní",
    })


# ─── SEO: robots.txt + sitemap.xml ────────────────────────────────────────────

CATEGORIES = ["ekonomika", "domaci", "zahranici", "sport", "zviratka", "veda"]


@app.get("/robots.txt")
def robots_txt(request: Request):
    base = str(request.base_url).rstrip("/")
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /api/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml(request: Request, db: Session = Depends(get_db)):
    base = str(request.base_url).rstrip("/")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    urls = []

    # Statické stránky
    urls.append({"loc": f"{base}/",         "lastmod": today, "changefreq": "daily",  "priority": "1.0"})
    urls.append({"loc": f"{base}/archiv",   "lastmod": today, "changefreq": "weekly", "priority": "0.5"})
    for cat in CATEGORIES:
        urls.append({"loc": f"{base}/kategorie/{cat}", "lastmod": today, "changefreq": "daily", "priority": "0.7"})

    # Všechny publikované články
    articles = (
        db.query(Article)
        .filter(Article.is_published == True)
        .order_by(Article.published_at.desc())
        .all()
    )
    for art in articles:
        lastmod = art.published_at.strftime("%Y-%m-%d") if art.published_at else today
        if art.status == "hotnews":
            priority, changefreq = "0.9", "daily"
        elif art.status == "category":
            priority, changefreq = "0.8", "weekly"
        else:
            priority, changefreq = "0.5", "monthly"
        urls.append({
            "loc": f"{base}/clanek/{art.id}",
            "lastmod": lastmod,
            "changefreq": changefreq,
            "priority": priority,
        })

    url_blocks = "\n".join(
        f"  <url>\n"
        f"    <loc>{u['loc']}</loc>\n"
        f"    <lastmod>{u['lastmod']}</lastmod>\n"
        f"    <changefreq>{u['changefreq']}</changefreq>\n"
        f"    <priority>{u['priority']}</priority>\n"
        f"  </url>"
        for u in urls
    )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{url_blocks}\n"
        "</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


# ───────────────────────────────────────────────────────────────────────────────

CATEGORY_NAMES = {
    "ekonomika": "Ekonomika",
    "domaci": "Domácí",
    "zahranici": "Zahraničí",
    "sport": "Sport",
    "zviratka": "Zvířátka",
    "veda": "Věda a technika",
    "ostatni": "Ostatní",
}

CATEGORY_ICONS = {
    "ekonomika": "💼",
    "domaci": "🏠",
    "zahranici": "🌍",
    "sport": "⚽",
    "zviratka": "🐾",
    "veda": "🧪",
    "ostatni": "🔬",
}


@app.get("/kategorie/{category}", response_class=HTMLResponse)
def category_page(category: str, request: Request, db: Session = Depends(get_db)):
    if category not in CATEGORY_NAMES:
        raise HTTPException(status_code=404, detail="Kategorie nenalezena")
    # Zaznamenat návštěvu
    db.add(SiteVisit(path=f"/kategorie/{category}", ip_address=_get_client_ip(request)))
    db.commit()

    # Kategorie = zprávy po prvním přesunu z hotnews (status="category")
    articles = (
        db.query(Article)
        .filter(Article.is_published == True, Article.category == category, Article.status == "category")
        .order_by(Article.created_at.desc())
        .limit(50)
        .all()
    )
    icon = CATEGORY_ICONS.get(category, "📰")
    return templates.TemplateResponse("archive.html", {
        "request": request,
        "articles": articles,
        "page_title": f"{icon} {CATEGORY_NAMES[category]}",
        "page_subtitle": f"Pozitivní zprávy v kategorii {CATEGORY_NAMES[category]} – posledních {ARCHIVE_AFTER_DAYS} dní",
    })


# ─── Admin: přihlášení ────────────────────────────────────────────────────────

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form(...)):
    if verify_admin_password(password):
        token = create_session_token()
        response = RedirectResponse("/admin", status_code=302)
        response.set_cookie(
            SESSION_COOKIE, token,
            httponly=True, samesite="lax", max_age=60 * 60 * 8
        )
        return response
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "Špatné heslo"}, status_code=401
    )


@app.post("/admin/logout")
def admin_logout():
    response = RedirectResponse("/admin/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ─── Admin: hlavní panel ───────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, db: Session = Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    articles_count = db.query(Article).count()
    published_count = db.query(Article).filter(Article.is_published == True).count()
    keywords_count = db.query(Keyword).count()
    sources_count = db.query(NewsSource).filter(NewsSource.enabled == True).count()

    recent = (
        db.query(Article)
        .order_by(Article.created_at.desc())
        .limit(10)
        .all()
    )

    total_visits = db.query(SiteVisit).count()
    today = datetime.utcnow().date()
    visits_today = db.query(SiteVisit).filter(
        SiteVisit.visited_at >= datetime(today.year, today.month, today.day)
    ).count()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "articles_count": articles_count,
        "published_count": published_count,
        "keywords_count": keywords_count,
        "sources_count": sources_count,
        "recent_articles": recent,
        "scrape_interval": get_interval(),
        "min_score": get_min_score(),
        "max_articles": get_max_articles(),
        "total_visits": total_visits,
        "visits_today": visits_today,
        "active_tab": "dashboard",
    })


# ─── Admin: klíčová slova ──────────────────────────────────────────────────────

@app.get("/admin/keywords", response_class=HTMLResponse)
def admin_keywords(request: Request, db: Session = Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    keywords = db.query(Keyword).order_by(Keyword.keyword_type, Keyword.word).all()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "keywords": keywords,
        "active_tab": "keywords",
    })


@app.post("/admin/keywords/add")
def admin_keywords_add(
    request: Request,
    word: str = Form(...),
    weight: float = Form(...),
    keyword_type: str = Form(...),
    db: Session = Depends(get_db),
):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    word = word.strip().lower()
    if not word:
        return RedirectResponse("/admin/keywords", status_code=302)

    existing = db.query(Keyword).filter(Keyword.word == word).first()
    if existing:
        existing.weight = weight
        existing.keyword_type = keyword_type
    else:
        db.add(Keyword(word=word, weight=abs(weight), keyword_type=keyword_type))
    db.commit()
    return RedirectResponse("/admin/keywords", status_code=302)


@app.post("/admin/keywords/{keyword_id}/delete")
def admin_keywords_delete(
    keyword_id: int, request: Request, db: Session = Depends(get_db)
):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    kw = db.query(Keyword).filter(Keyword.id == keyword_id).first()
    if kw:
        db.delete(kw)
        db.commit()
    return RedirectResponse("/admin/keywords", status_code=302)


# ─── Admin: RSS zdroje ─────────────────────────────────────────────────────────

@app.get("/admin/sources", response_class=HTMLResponse)
def admin_sources(request: Request, db: Session = Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    sources = db.query(NewsSource).order_by(NewsSource.language, NewsSource.name).all()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "sources": sources,
        "active_tab": "sources",
    })


@app.post("/admin/sources/{source_id}/toggle")
def admin_sources_toggle(
    source_id: int, request: Request, db: Session = Depends(get_db)
):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    source = db.query(NewsSource).filter(NewsSource.id == source_id).first()
    if source:
        source.enabled = not source.enabled
        db.commit()
    return RedirectResponse("/admin/sources", status_code=302)


@app.post("/admin/sources/add")
def admin_sources_add(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    language: str = Form("cs"),
    db: Session = Depends(get_db),
):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    db.add(NewsSource(name=name.strip(), url=url.strip(), language=language))
    db.commit()
    return RedirectResponse("/admin/sources", status_code=302)


# ─── Admin: live status scrape ────────────────────────────────────────────────

@app.get("/admin/scrape-status")
def scrape_status(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401)
    return JSONResponse(get_status())


@app.post("/admin/settings/interval")
def admin_set_interval(request: Request, hours: float = Form(...)):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)
    hours = max(0.5, min(24.0, hours))
    set_interval(hours)
    return RedirectResponse("/admin?saved=1", status_code=302)


@app.post("/admin/settings/min-score")
def admin_set_min_score(request: Request, score: float = Form(...)):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)
    score = max(1.0, min(10.0, score))
    set_min_score(score)
    return RedirectResponse("/admin?saved=1", status_code=302)


@app.post("/admin/settings/max-articles")
def admin_set_max_articles(request: Request, n: int = Form(...)):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)
    set_max_articles(n)
    return RedirectResponse("/admin?saved=1", status_code=302)


# ─── Admin: manuální spuštění ─────────────────────────────────────────────────

@app.post("/admin/trigger")
def admin_trigger(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    import threading
    t = threading.Thread(target=trigger_manual, daemon=True)
    t.start()
    return RedirectResponse("/admin?triggered=1", status_code=302)


# ─── Admin: přehled článků ────────────────────────────────────────────────────

@app.get("/admin/articles", response_class=HTMLResponse)
def admin_articles(request: Request, db: Session = Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    articles = db.query(Article).order_by(Article.created_at.desc()).limit(100).all()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "all_articles": articles,
        "active_tab": "articles",
    })


# ─── Admin: statistiky ────────────────────────────────────────────────────────

@app.get("/admin/stats", response_class=HTMLResponse)
def admin_stats(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    sort: str = "date",
    dir: str = "desc",
):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    PER_PAGE = 20

    all_articles = db.query(Article).all()
    stats = []
    for a in all_articles:
        views = db.query(ArticleView).filter(
            ArticleView.article_id == a.id, ArticleView.view_type == "open"
        ).count()
        clicks = db.query(ArticleView).filter(
            ArticleView.article_id == a.id, ArticleView.view_type == "click"
        ).count()
        ratings = db.query(ArticleRating).filter(ArticleRating.article_id == a.id).all()
        avg_r = round(sum(r.rating for r in ratings) / len(ratings), 1) if ratings else None
        comments_count = db.query(Comment).filter(Comment.article_id == a.id).count()
        stats.append({
            "article": a,
            "views": views,
            "clicks": clicks,
            "rating_count": len(ratings),
            "avg_rating": avg_r,
            "comments": comments_count,
        })

    # Řazení
    sort_keys = {
        "title":    lambda r: r["article"].title.lower(),
        "views":    lambda r: r["views"],
        "clicks":   lambda r: r["clicks"],
        "rating":   lambda r: r["avg_rating"] or 0,
        "comments": lambda r: r["comments"],
        "date":     lambda r: r["article"].created_at or datetime.min,
    }
    key_fn = sort_keys.get(sort, sort_keys["date"])
    stats.sort(key=key_fn, reverse=(dir == "desc"))

    # Stránkování
    total = len(stats)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(1, min(page, total_pages))
    page_stats = stats[(page - 1) * PER_PAGE: page * PER_PAGE]

    # Celková návštěvnost
    total_site_visits = db.query(SiteVisit).count()
    today = datetime.utcnow().date()
    visits_today = db.query(SiteVisit).filter(
        SiteVisit.visited_at >= datetime(today.year, today.month, today.day)
    ).count()
    visits_7d = db.query(SiteVisit).filter(
        SiteVisit.visited_at >= datetime.utcnow() - timedelta(days=7)
    ).count()
    total_article_views = db.query(ArticleView).filter(ArticleView.view_type == "open").count()
    total_clicks = db.query(ArticleView).filter(ArticleView.view_type == "click").count()

    unique_ips = db.query(SiteVisit.ip_address).distinct().count()
    recent_visits = (
        db.query(SiteVisit)
        .order_by(SiteVisit.visited_at.desc())
        .limit(30)
        .all()
    )

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "stats": page_stats,
        "active_tab": "stats",
        "stats_page": page,
        "stats_total_pages": total_pages,
        "stats_total": total,
        "stats_sort": sort,
        "stats_dir": dir,
        "total_site_visits": total_site_visits,
        "visits_today": visits_today,
        "visits_7d": visits_7d,
        "total_article_views": total_article_views,
        "total_clicks": total_clicks,
        "unique_ips": unique_ips,
        "recent_visits": recent_visits,
    })


# ─── Admin: návštěvnost / IP log ──────────────────────────────────────────────

@app.get("/admin/visitors", response_class=HTMLResponse)
def admin_visitors(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
):
    if not is_authenticated(request):
        return RedirectResponse("/admin/login", status_code=302)

    PER_PAGE = 50

    # Přehled unikátních IP s počtem návštěv
    ip_stats = (
        db.query(
            SiteVisit.ip_address,
            func.count(SiteVisit.id).label("visits"),
            func.min(SiteVisit.visited_at).label("first_seen"),
            func.max(SiteVisit.visited_at).label("last_seen"),
        )
        .group_by(SiteVisit.ip_address)
        .order_by(func.count(SiteVisit.id).desc())
        .all()
    )

    # Raw log – stránkovaný
    total_log = db.query(SiteVisit).count()
    total_pages = max(1, (total_log + PER_PAGE - 1) // PER_PAGE)
    page = max(1, min(page, total_pages))
    visit_log = (
        db.query(SiteVisit)
        .order_by(SiteVisit.visited_at.desc())
        .offset((page - 1) * PER_PAGE)
        .limit(PER_PAGE)
        .all()
    )

    today = datetime.utcnow().date()
    visits_today = db.query(SiteVisit).filter(
        SiteVisit.visited_at >= datetime(today.year, today.month, today.day)
    ).count()
    visits_7d = db.query(SiteVisit).filter(
        SiteVisit.visited_at >= datetime.utcnow() - timedelta(days=7)
    ).count()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "active_tab": "visitors",
        "ip_stats": ip_stats,
        "visit_log": visit_log,
        "visit_log_page": page,
        "visit_log_total_pages": total_pages,
        "visit_log_total": total_log,
        "unique_ips_count": len(ip_stats),
        "visits_today": visits_today,
        "visits_7d": visits_7d,
        "total_visits": total_log,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
