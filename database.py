import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, DateTime, Text, text
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:////app/data/pozitivni_zpravy.db"
os.makedirs("/app/data", exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=False)
    original_url = Column(String(2000), nullable=False)
    source_name = Column(String(200))
    published_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    positivity_score = Column(Float, default=5.0)
    image_url = Column(String(2000))
    image_alt = Column(String(500))
    is_published = Column(Boolean, default=False)
    language = Column(String(10), default="cs")
    category = Column(String(50), default="ostatni")
    # Tok zpráv: hotnews → category → archive
    status = Column(String(20), default="hotnews")


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True, index=True)
    word = Column(String(200), nullable=False, unique=True)
    weight = Column(Float, default=1.0)  # kladné = pozitivní, záporné = negativní
    keyword_type = Column(String(20), default="positive")  # "positive" nebo "negative"


class NewsSource(Base):
    __tablename__ = "news_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    url = Column(String(2000), nullable=False)
    language = Column(String(10), default="cs")
    enabled = Column(Boolean, default=True)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(String(2000))


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, nullable=False, index=True)
    author_name = Column(String(100), default="Anonym")
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ArticleRating(Base):
    __tablename__ = "article_ratings"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, nullable=False, index=True)
    rating = Column(Integer, nullable=False)  # 1–5
    created_at = Column(DateTime, default=datetime.utcnow)


class ArticleView(Base):
    __tablename__ = "article_views"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, nullable=False, index=True)
    view_type = Column(String(20), default="open")  # "open" | "click"
    viewed_at = Column(DateTime, default=datetime.utcnow)


class SiteVisit(Base):
    __tablename__ = "site_visits"

    id = Column(Integer, primary_key=True, index=True)
    path = Column(String(200), default="/")
    ip_address = Column(String(100))
    visited_at = Column(DateTime, default=datetime.utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    # Migrate: přidat ip_address do site_visits pokud chybí (existující DB)
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE site_visits ADD COLUMN ip_address VARCHAR(100)"))
            conn.commit()
        except Exception:
            pass  # sloupec už existuje
        try:
            conn.execute(text("ALTER TABLE articles ADD COLUMN status VARCHAR(20) DEFAULT 'category'"))
            conn.commit()
        except Exception:
            pass  # sloupec už existuje
    db = SessionLocal()
    try:
        _seed_defaults(db)
    finally:
        db.close()


def _seed_defaults(db):
    # Výchozí RSS zdroje – přidá chybějící (funguje i pro existující DB)
    default_sources = [
        ("iDnes.cz", "https://servis.idnes.cz/rss.aspx?c=zpravodajstvi", "cs"),
        ("Novinky.cz", "https://www.novinky.cz/rss2/", "cs"),
        ("ČT24", "https://ct24.ceskatelevize.cz/rss/hlavni-zpravy", "cs"),
        ("Aktuálně.cz", "https://zpravy.aktualne.cz/rss/", "cs"),
        ("ČTK / České noviny", "https://www.ceskenoviny.cz/zpravy/rss/home.rss", "cs"),
        ("BBC News", "http://feeds.bbci.co.uk/news/rss.xml", "en"),
        ("Good News Network", "https://www.goodnewsnetwork.org/feed/", "en"),
        ("Reuters", "https://feeds.reuters.com/reuters/topNews", "en"),
    ]
    existing_urls = {src.url for src in db.query(NewsSource).all()}
    for name, url, lang in default_sources:
        if url not in existing_urls:
            db.add(NewsSource(name=name, url=url, language=lang))

    # Výchozí klíčová slova – přidá chybějící (funguje i pro existující DB)
    default_keywords = [
        # Pozitivní – česky
        ("úspěch", 1.5, "positive"),
        ("radost", 1.5, "positive"),
        ("naděje", 1.2, "positive"),
        ("pomoc", 1.2, "positive"),
        ("láska", 1.5, "positive"),
        ("vítězství", 1.3, "positive"),
        ("přátelství", 1.2, "positive"),
        ("inovace", 1.0, "positive"),
        ("zdraví", 1.0, "positive"),
        ("pozitivní", 1.0, "positive"),
        ("rekord", 0.8, "positive"),
        ("zlepšení", 0.8, "positive"),
        ("objev", 1.2, "positive"),
        ("průlom", 1.3, "positive"),
        ("záchrana", 1.3, "positive"),
        ("ochrana", 1.0, "positive"),
        ("solidarita", 1.2, "positive"),
        ("dobročinnost", 1.3, "positive"),
        ("inspirace", 1.2, "positive"),
        ("odvaha", 1.1, "positive"),
        ("rozvoj", 0.9, "positive"),
        ("obnova", 0.9, "positive"),
        ("mír", 1.4, "positive"),
        ("harmonie", 1.0, "positive"),
        ("štěstí", 1.4, "positive"),
        ("prosperita", 1.0, "positive"),
        ("sbírka", 0.9, "positive"),
        ("dobrovolníci", 1.2, "positive"),
        ("ocenění", 0.9, "positive"),
        ("uzdravení", 1.3, "positive"),
        ("příroda", 0.8, "positive"),
        ("zvíře", 0.8, "positive"),
        ("mazlíček", 0.9, "positive"),
        # Pozitivní – anglicky
        ("success", 1.5, "positive"),
        ("hope", 1.2, "positive"),
        ("joy", 1.5, "positive"),
        ("breakthrough", 1.3, "positive"),
        ("inspire", 1.2, "positive"),
        ("rescue", 1.3, "positive"),
        ("recovery", 1.1, "positive"),
        ("celebrate", 1.1, "positive"),
        ("achieve", 1.0, "positive"),
        ("discover", 1.1, "positive"),
        ("peace", 1.4, "positive"),
        ("miracle", 1.3, "positive"),
        ("volunteer", 1.2, "positive"),
        ("charity", 1.1, "positive"),
        ("award", 0.9, "positive"),
        ("protect", 1.0, "positive"),
        ("animal", 0.8, "positive"),
        ("wildlife", 0.9, "positive"),
        # Negativní – česky
        ("smrt", -2.0, "negative"),
        ("tragédie", -2.0, "negative"),
        ("válka", -2.0, "negative"),
        ("krize", -1.5, "negative"),
        ("katastrofa", -2.0, "negative"),
        ("útok", -1.8, "negative"),
        ("nehoda", -1.5, "negative"),
        ("teror", -2.0, "negative"),
        ("vražda", -2.0, "negative"),
        ("zkáza", -1.8, "negative"),
        ("korupce", -1.5, "negative"),
        ("podvod", -1.5, "negative"),
        # Negativní – anglicky
        ("war", -2.0, "negative"),
        ("disaster", -2.0, "negative"),
        ("crisis", -1.5, "negative"),
        ("attack", -1.8, "negative"),
        ("terror", -2.0, "negative"),
        ("murder", -2.0, "negative"),
        ("fraud", -1.5, "negative"),
        ("corruption", -1.5, "negative"),
    ]
    existing_words = {kw.word for kw in db.query(Keyword).all()}
    for word, weight, ktype in default_keywords:
        if word not in existing_words:
            db.add(Keyword(word=word, weight=abs(weight), keyword_type=ktype))

    db.commit()
