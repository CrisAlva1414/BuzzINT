"""
scraper/api/db.py
─────────────────────────────────────────────────────────────
SQLAlchemy engine y session factory para la API de BuzzINT.

Usa SQLAlchemy (no psycopg2 directo) porque la API solo hace
queries read-only con ORM liviano. Los loaders ETL siguen con psycopg2.

El engine se crea una sola vez al arrancar la app (lifespan).
Cada request obtiene su propia session vía get_db().
"""
from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

# ──────────────────────────────────────────────────────────────
# DSN
# ──────────────────────────────────────────────────────────────

def _build_dsn() -> str:
    host     = os.getenv("PG_HOST",     "localhost")
    port     = os.getenv("PG_PORT",     "5432")
    dbname   = os.getenv("PG_DB",       "buzzint")
    user     = os.getenv("PG_USER",     "buzzint")
    password = os.getenv("PG_PASSWORD", "buzzint")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"


# ──────────────────────────────────────────────────────────────
# Engine (singleton — se instancia en lifespan)
# ──────────────────────────────────────────────────────────────
_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine() -> None:
    """Inicializar engine. Llamar una sola vez desde el lifespan de FastAPI."""
    global _engine, _SessionLocal

    _engine = create_engine(
        _build_dsn(),
        pool_size=3,          # OrangePi: pool pequeño
        max_overflow=2,
        pool_pre_ping=True,   # detectar conexiones muertas
        connect_args={
            "options": "-c search_path=gold,public -c statement_timeout=30000"
        },
    )

    # Forzar search_path en cada conexión nueva del pool
    @event.listens_for(_engine, "connect")
    def set_search_path(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET search_path TO gold, public")
        cursor.close()

    _SessionLocal = sessionmaker(
        bind=_engine,
        autocommit=False,
        autoflush=False,
    )


def dispose_engine() -> None:
    """Cerrar pool al apagar la app."""
    global _engine
    if _engine:
        _engine.dispose()
        _engine = None


def get_db() -> Generator[Session, None, None]:
    """
    Dependency de FastAPI. Provee una Session por request.

    Uso:
        @router.get("/foo")
        def foo(db: Session = Depends(get_db)):
            ...
    """
    if _SessionLocal is None:
        raise RuntimeError("Engine no inicializado — llamar init_engine() primero")

    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()