"""
scraper/api/routers/metrics.py
─────────────────────────────────────────────────────────────
Endpoints de métricas analytics para un establecimiento.

Todos los endpoints son read-only — leen desde las tablas
gold.analytics_* pre-computadas por AnalyticsPipeline.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from scraper.api.db import get_db
from scraper.api.schemas import (
    AlertasResponse,
    AlertaRow,
    MetricaRow,
    MetricasResponse,
    SimceResponse,
    SimceSerieRow,
    SimceTendenciaRow,
    SigeResponse,
    SigeSerieRow,
    SigeTendenciaRow,
)

router = APIRouter(prefix="/metrics", tags=["metrics"])


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _normalize_rbd(rbd: str) -> str:
    digits = "".join(c for c in rbd if c.isdigit())
    if not digits:
        raise HTTPException(status_code=400, detail=f"RBD inválido: '{rbd}'")
    return digits.zfill(8)


def _get_nom_rbd(db: Session, rbd: str) -> Optional[str]:
    r = db.execute(
        text("SELECT nom_rbd FROM gold.dim_establecimiento WHERE rbd = :rbd"),
        {"rbd": rbd},
    ).fetchone()
    return r[0] if r else None


# ──────────────────────────────────────────────────────────────
# GET /metrics/{rbd}  — resumen de todas las métricas
# ──────────────────────────────────────────────────────────────
@router.get("/{rbd}", response_model=MetricasResponse)
def get_metricas(
    rbd: str,
    segmento: Optional[str] = Query(None, description="Filtrar por segmento: post_hiatus | full | current"),
    fuente: Optional[str]   = Query(None, description="Filtrar por fuente: simce | sige | cruce"),
    db: Session = Depends(get_db),
):
    """
    Resumen de todas las métricas calculadas para un establecimiento.
    Incluye puntaje SIMCE, tendencias, percentiles, SIGE y cruces.
    """
    rbd = _normalize_rbd(rbd)

    filters = "WHERE ae.rbd = :rbd"
    params: dict = {"rbd": rbd}

    if segmento:
        filters += " AND ae.segmento = :segmento"
        params["segmento"] = segmento
    if fuente:
        filters += " AND ae.fuente = :fuente"
        params["fuente"] = fuente

    sql = text(f"""
        SELECT
            ae.metrica, ae.agno, ae.grado,
            CASE WHEN ae.asignatura = '__none__' THEN NULL ELSE ae.asignatura END AS asignatura,
            ae.segmento, ae.fuente,
            ae.valor_real, ae.valor_proyectado,
            ae.tendencia_slope, ae.tendencia_r2, ae.rmse,
            ae.percentil_gse, ae.percentil_comuna,
            ae.alerta, ae.n_puntos, ae.confianza, ae.calculado_en
        FROM gold.analytics_establecimiento ae
        {filters}
        ORDER BY ae.fuente, ae.metrica, ae.grado, ae.asignatura, ae.agno DESC
    """)

    rows = db.execute(sql, params).fetchall()
    metricas = [MetricaRow.model_validate(dict(r._mapping)) for r in rows]
    total_alertas = sum(1 for m in metricas if m.alerta)

    return MetricasResponse(
        rbd=rbd,
        nom_rbd=_get_nom_rbd(db, rbd),
        total_alertas=total_alertas,
        metricas=metricas,
    )


# ──────────────────────────────────────────────────────────────
# GET /metrics/{rbd}/simce  — serie SIMCE completa + tendencias
# ──────────────────────────────────────────────────────────────
@router.get("/{rbd}/simce", response_model=SimceResponse)
def get_simce(
    rbd: str,
    db: Session = Depends(get_db),
):
    """Serie SIMCE histórica completa con benchmarks y tendencias calculadas."""
    rbd = _normalize_rbd(rbd)

    # Serie histórica
    sql_serie = text("""
        SELECT agno, grado, asignatura, puntaje, n_evaluados,
               en_hiatus, prom_nacional, dif_nacional
        FROM gold.analytics_simce_serie
        WHERE rbd = :rbd
        ORDER BY agno, grado, asignatura
    """)
    serie_rows = db.execute(sql_serie, {"rbd": rbd}).fetchall()

    # Tendencias de analytics_establecimiento
    sql_tend = text("""
        SELECT grado,
               CASE WHEN asignatura = '__none__' THEN NULL ELSE asignatura END AS asignatura,
               segmento, valor_real, valor_proyectado,
               tendencia_slope, tendencia_r2, rmse, n_puntos, confianza, alerta
        FROM gold.analytics_establecimiento
        WHERE rbd = :rbd AND metrica = 'simce_tendencia'
        ORDER BY grado, asignatura, segmento
    """)
    tend_rows = db.execute(sql_tend, {"rbd": rbd}).fetchall()

    return SimceResponse(
        rbd=rbd,
        nom_rbd=_get_nom_rbd(db, rbd),
        serie=[SimceSerieRow.model_validate(dict(r._mapping)) for r in serie_rows],
        tendencias=[SimceTendenciaRow.model_validate(dict(r._mapping)) for r in tend_rows],
    )


# ──────────────────────────────────────────────────────────────
# GET /metrics/{rbd}/simce/{grado}  — filtrado por grado
# ──────────────────────────────────────────────────────────────
@router.get("/{rbd}/simce/{grado}", response_model=SimceResponse)
def get_simce_by_grado(
    rbd: str,
    grado: str,
    db: Session = Depends(get_db),
):
    """
    Serie SIMCE filtrada por grado.
    Grados válidos: 2b, 4b, 6b, 8b, 2m.
    """
    rbd   = _normalize_rbd(rbd)
    grado = grado.lower()

    sql_serie = text("""
        SELECT agno, grado, asignatura, puntaje, n_evaluados,
               en_hiatus, prom_nacional, dif_nacional
        FROM gold.analytics_simce_serie
        WHERE rbd = :rbd AND grado = :grado
        ORDER BY agno, asignatura
    """)
    serie_rows = db.execute(sql_serie, {"rbd": rbd, "grado": grado}).fetchall()

    sql_tend = text("""
        SELECT grado,
               CASE WHEN asignatura = '__none__' THEN NULL ELSE asignatura END AS asignatura,
               segmento, valor_real, valor_proyectado,
               tendencia_slope, tendencia_r2, rmse, n_puntos, confianza, alerta
        FROM gold.analytics_establecimiento
        WHERE rbd = :rbd AND metrica = 'simce_tendencia' AND grado = :grado
        ORDER BY asignatura, segmento
    """)
    tend_rows = db.execute(sql_tend, {"rbd": rbd, "grado": grado}).fetchall()

    return SimceResponse(
        rbd=rbd,
        nom_rbd=_get_nom_rbd(db, rbd),
        serie=[SimceSerieRow.model_validate(dict(r._mapping)) for r in serie_rows],
        tendencias=[SimceTendenciaRow.model_validate(dict(r._mapping)) for r in tend_rows],
    )


# ──────────────────────────────────────────────────────────────
# GET /metrics/{rbd}/sige  — serie SIGE + tendencias
# ──────────────────────────────────────────────────────────────
@router.get("/{rbd}/sige", response_model=SigeResponse)
def get_sige(
    rbd: str,
    db: Session = Depends(get_db),
):
    """Serie SIGE de calificaciones internas con tendencias por asignatura."""
    rbd = _normalize_rbd(rbd)

    sql_serie = text("""
        SELECT agno, grado, asignatura, prom_notas,
               tasa_aprobacion, tasa_asistencia, n_alumnos
        FROM gold.analytics_sige_serie
        WHERE rbd = :rbd
        ORDER BY agno, grado, asignatura
    """)
    serie_rows = db.execute(sql_serie, {"rbd": rbd}).fetchall()

    sql_tend = text("""
        SELECT grado,
               CASE WHEN asignatura = '__none__' THEN NULL ELSE asignatura END AS asignatura,
               valor_real, valor_proyectado, tendencia_slope, tendencia_r2, alerta, confianza
        FROM gold.analytics_establecimiento
        WHERE rbd = :rbd AND metrica = 'sige_tendencia'
        ORDER BY grado, asignatura
    """)
    tend_rows = db.execute(sql_tend, {"rbd": rbd}).fetchall()

    return SigeResponse(
        rbd=rbd,
        nom_rbd=_get_nom_rbd(db, rbd),
        serie=[SigeSerieRow.model_validate(dict(r._mapping)) for r in serie_rows],
        tendencias=[SigeTendenciaRow.model_validate(dict(r._mapping)) for r in tend_rows],
    )


# ──────────────────────────────────────────────────────────────
# GET /metrics/{rbd}/sige/{grado}  — SIGE filtrado por grado
# ──────────────────────────────────────────────────────────────
@router.get("/{rbd}/sige/{grado}", response_model=SigeResponse)
def get_sige_by_grado(
    rbd: str,
    grado: str,
    db: Session = Depends(get_db),
):
    """Serie SIGE filtrada por grado (ej: '4b', '8b', '4')."""
    rbd   = _normalize_rbd(rbd)
    grado = grado.lower()

    sql_serie = text("""
        SELECT agno, grado, asignatura, prom_notas,
               tasa_aprobacion, tasa_asistencia, n_alumnos
        FROM gold.analytics_sige_serie
        WHERE rbd = :rbd AND grado = :grado
        ORDER BY agno, asignatura
    """)
    serie_rows = db.execute(sql_serie, {"rbd": rbd, "grado": grado}).fetchall()

    sql_tend = text("""
        SELECT grado,
               CASE WHEN asignatura = '__none__' THEN NULL ELSE asignatura END AS asignatura,
               valor_real, valor_proyectado, tendencia_slope, tendencia_r2, alerta, confianza
        FROM gold.analytics_establecimiento
        WHERE rbd = :rbd AND metrica = 'sige_tendencia' AND grado = :grado
        ORDER BY asignatura
    """)
    tend_rows = db.execute(sql_tend, {"rbd": rbd, "grado": grado}).fetchall()

    return SigeResponse(
        rbd=rbd,
        nom_rbd=_get_nom_rbd(db, rbd),
        serie=[SigeSerieRow.model_validate(dict(r._mapping)) for r in serie_rows],
        tendencias=[SigeTendenciaRow.model_validate(dict(r._mapping)) for r in tend_rows],
    )


# ──────────────────────────────────────────────────────────────
# GET /metrics/{rbd}/alertas  — solo métricas con alerta=TRUE
# ──────────────────────────────────────────────────────────────
@router.get("/{rbd}/alertas", response_model=AlertasResponse)
def get_alertas(
    rbd: str,
    db: Session = Depends(get_db),
):
    """
    Métricas con alerta activa para el establecimiento.
    Útil para el panel de control del director.
    """
    rbd = _normalize_rbd(rbd)

    sql = text("""
        SELECT metrica,
               CASE WHEN asignatura = '__none__' THEN NULL ELSE asignatura END AS asignatura,
               grado, segmento, fuente, valor_real, confianza
        FROM gold.analytics_establecimiento
        WHERE rbd = :rbd AND alerta = TRUE
        ORDER BY fuente, metrica, grado
    """)
    rows = db.execute(sql, {"rbd": rbd}).fetchall()
    alertas = [AlertaRow.model_validate(dict(r._mapping)) for r in rows]

    return AlertasResponse(
        rbd=rbd,
        nom_rbd=_get_nom_rbd(db, rbd),
        alertas=alertas,
        total=len(alertas),
    )