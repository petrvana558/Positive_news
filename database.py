import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, DateTime, Text
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
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

    # Výchozí klíčová slova
    if db.query(Keyword).count() == 0:
        keywords = [
            # Pozitivní
            Keyword(word="úspěch", weight=1.5, keyword_type="positive"),
            Keyword(word="radost", weight=1.5, keyword_type="positive"),
            Keyword(word="naděje", weight=1.2, keyword_type="positive"),
            Keyword(word="pomoc", weight=1.2, keyword_type="positive"),
            Keyword(word="láska", weight=1.5, keyword_type="positive"),
            Keyword(word="vítězství", weight=1.3, keyword_type="positive"),
            Keyword(word="přátelství", weight=1.2, keyword_type="positive"),
            Keyword(word="inovace", weight=1.0, keyword_type="positive"),
            Keyword(word="zdraví", weight=1.0, keyword_type="positive"),
            Keyword(word="pozitivní", weight=1.0, keyword_type="positive"),
            Keyword(word="rekord", weight=0.8, keyword_type="positive"),
            Keyword(word="zlepšení", weight=0.8, keyword_type="positive"),
            Keyword(word="success", weight=1.5, keyword_type="positive"),
            Keyword(word="hope", weight=1.2, keyword_type="positive"),
            Keyword(word="joy", weight=1.5, keyword_type="positive"),
            Keyword(word="breakthrough", weight=1.3, keyword_type="positive"),
            Keyword(word="inspire", weight=1.2, keyword_type="positive"),
            # Negativní
            Keyword(word="smrt", weight=-2.0, keyword_type="negative"),
            Keyword(word="tragédie", weight=-2.0, keyword_type="negative"),
            Keyword(word="válka", weight=-2.0, keyword_type="negative"),
            Keyword(word="krize", weight=-1.5, keyword_type="negative"),
            Keyword(word="katastrofa", weight=-2.0, keyword_type="negative"),
            Keyword(word="útok", weight=-1.8, keyword_type="negative"),
            Keyword(word="nehoda", weight=-1.5, keyword_type="negative"),
            Keyword(word="war", weight=-2.0, keyword_type="negative"),
            Keyword(word="disaster", weight=-2.0, keyword_type="negative"),
            Keyword(word="crisis", weight=-1.5, keyword_type="negative"),
            Keyword(word="attack", weight=-1.8, keyword_type="negative"),
        ]
        db.add_all(keywords)

    db.commit()
