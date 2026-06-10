"""
scraper/api/routers/pipeline.py
─────────────────────────────────────────────────────────────
Endpoints para disparar y consultar el AnalyticsPipeline.

POST /pipeline/run   → corre el pipeline en background
GET  /pipeline/status/{run_id} → consulta el estado del run

El pipeline se ejecuta en un BackgroundTask de FastAPI.
No usa workers externos — adecuado para el OrangePi donde
no se necesita distribuir carga.
"""
from __future__ import annotations

import logging
import os

import psycopg2
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from scraper.api.db import get_db
from scraper.api.schemas import (
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

# Módulos válidos del AnalyticsPipeline
_VALID_MODULES = {
    "simce_serie", "simce_tendencia", "simce_percentil",
    "sige_serie", "cruce",
}


# ──────────────────────────────────────────────────────────────
# Background task
# ──────────────────────────────────────────────────────────────

def _run_pipeline_bg(rbd: str, modules: list[str] | None, run_id: int) -> None:
    """
    Corre AnalyticsPipeline en background.
    Usa psycopg2 directo (no SQLAlchemy) igual que el pipeline.
    """
    try:
        conn = psycopg2.connect(
            host=os.getenv("PG_HOST", "localhost"),
            port=int(os.getenv("PG_PORT", "5432")),
            dbname=os.getenv("PG_DB", "buzzint"),
            user=os.getenv("PG_USER", "buzzint"),
            password=os.getenv("PG_PASSWORD", "buzzint"),
            options="-c search_path=gold,public",
        )
        conn.autocommit = False
    except Exception as exc:
        logger.error("[pipeline] error conectando a DB: %s", exc)
        return

    try:
        from scraper.pipelines.analytics import AnalyticsPipeline
        pipeline = AnalyticsPipeline(conn, rbd=rbd, modules=modules)
        result   = pipeline.run()
        logger.info("[pipeline] run_id=%d finalizado: %s", run_id, result["status"])
    except Exception as exc:
        logger.error("[pipeline] run_id=%d falló: %s", run_id, exc)
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────
# POST /pipeline/run
# ──────────────────────────────────────────────────────────────

@router.post("/run", response_model=PipelineRunResponse, status_code=202)
def run_pipeline(
    body: PipelineRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Dispara el AnalyticsPipeline en background.

    - Si `rbd` no se provee, usa RBD_PILOTO del entorno.
    - Si `modules` no se provee, corre todos los módulos.
    - Retorna inmediatamente con `run_id` para consultar estado.
    """
    rbd = body.rbd or os.getenv("RBD_PILOTO", "")
    if not rbd:
        raise HTTPException(
            status_code=400,
            detail="rbd requerido en el body o como variable de entorno RBD_PILOTO",
        )

    # Normalizar RBD
    digits = "".join(c for c in rbd if c.isdigit())
    if not digits:
        raise HTTPException(status_code=400, detail=f"RBD inválido: '{rbd}'")
    rbd = digits.zfill(8)

    # Validar módulos
    modules = body.modules
    if modules:
        invalid = set(modules) - _VALID_MODULES
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Módulos inválidos: {sorted(invalid)}. "
                       f"Válidos: {sorted(_VALID_MODULES)}",
            )

    # Crear run_log entry para obtener run_id antes de lanzar el background task
    try:
        result = db.execute(
            text("""
                INSERT INTO gold.analytics_run_log
                    (rbd, status, modules_run, pipeline_version)
                VALUES (:rbd, 'running', :modules, '1.0')
                RETURNING run_id
            """),
            {"rbd": rbd, "modules": modules or list(_VALID_MODULES)},
        )
        run_id = result.scalar()
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("[pipeline] error creando run_log: %s", exc)
        raise HTTPException(status_code=500, detail="Error iniciando el pipeline")

    # Lanzar en background
    background_tasks.add_task(_run_pipeline_bg, rbd, modules, run_id)

    return PipelineRunResponse(
        run_id=run_id,
        rbd=rbd,
        status="running",
        message=f"Pipeline iniciado en background (run_id={run_id})",
    )


# ──────────────────────────────────────────────────────────────
# GET /pipeline/status/{run_id}
# ──────────────────────────────────────────────────────────────

@router.get("/status/{run_id}", response_model=PipelineStatusResponse)
def get_pipeline_status(
    run_id: int,
    db: Session = Depends(get_db),
):
    """Consulta el estado de un run del pipeline por su run_id."""
    sql = text("""
        SELECT run_id, rbd, status, modules_run, rows_written,
               error_msg, started_at, finished_at, pipeline_version
        FROM gold.analytics_run_log
        WHERE run_id = :run_id
    """)
    row = db.execute(sql, {"run_id": run_id}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"run_id={run_id} no encontrado")

    return PipelineStatusResponse(
        run_id=row.run_id,
        rbd=row.rbd,
        status=row.status,
        modules_run=row.modules_run,
        rows_written=row.rows_written,
        error_msg=row.error_msg,
        started_at=row.started_at,
        finished_at=row.finished_at,
        pipeline_version=row.pipeline_version,
    )