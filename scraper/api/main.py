"""
scraper/api/main.py
─────────────────────────────────────────────────────────────
BuzzINT FastAPI — Sprint 3, Fase B

Arranque:
    uvicorn scraper.api.main:app --host 0.0.0.0 --port 8000 --reload

O desde el root del proyecto:
    python -m scraper.api.main
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from scraper.api.db import dispose_engine, get_db, init_engine
from scraper.api.routers import insights, metrics, pipeline
from scraper.api.schemas import HealthResponse

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Lifespan: inicializar y liberar recursos
# ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa el engine al arrancar, lo cierra al apagar."""
    logger.info("[buzzint-api] iniciando — inicializando engine SQLAlchemy")
    init_engine()
    logger.info("[buzzint-api] engine listo")
    yield
    logger.info("[buzzint-api] cerrando engine")
    dispose_engine()


# ──────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="BuzzINT API",
    description=(
        "API de analytics educacional para el establecimiento piloto.\n\n"
        "Lee métricas pre-computadas por `AnalyticsPipeline` desde el Gold Layer "
        "(PostgreSQL). No calcula en cada request — solo lee."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS abierto — red local sin auth (MVP)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────
# Routers
# ──────────────────────────────────────────────────────────────

app.include_router(metrics.router)
app.include_router(insights.router)
app.include_router(pipeline.router)


# ──────────────────────────────────────────────────────────────
# /health
# ──────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["health"])
def health():
    """
    Verifica que la API y la base de datos están operativas.
    Úsalo para monitoreo o para confirmar que el servicio levantó bien.
    """
    db_status = "error"
    try:
        # get_db() es un generator — instanciar manualmente para el health check
        db_gen = get_db()
        db = next(db_gen)
        db.execute(text("SELECT 1"))
        db_status = "ok"
        try:
            next(db_gen)
        except StopIteration:
            pass
    except Exception as exc:
        logger.warning("[health] DB check falló: %s", exc)

    return HealthResponse(
        status="ok" if db_status == "ok" else "error",
        db=db_status,
        version="1.0.0",
        rbd_piloto=os.getenv("RBD_PILOTO"),
    )


# ──────────────────────────────────────────────────────────────
# Entry point CLI
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    from scraper.core.logging import configure

    configure(level=os.getenv("LOG_LEVEL", "INFO"))

    uvicorn.run(
        "scraper.api.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("BUZZINT_ENV", "dev") == "dev",
        log_level=os.getenv("LOG_LEVEL", "INFO").lower(),
    )