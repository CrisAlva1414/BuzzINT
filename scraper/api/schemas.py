"""
scraper/api/schemas.py
─────────────────────────────────────────────────────────────
Pydantic v2 response models para la API de BuzzINT.

Convención de nombres:
  - *Row    → una fila de serie histórica
  - *Summary → resumen de una o más métricas
  - *Response → envelope completo del endpoint
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ──────────────────────────────────────────────────────────────
# Config base: permite leer desde ORM rows (Row-like objects)
# ──────────────────────────────────────────────────────────────
class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────────────────────
# /health
# ──────────────────────────────────────────────────────────────
class HealthResponse(BaseModel):
    status:   Literal["ok", "error"]
    db:       Literal["ok", "error"]
    version:  str = "1.0"
    rbd_piloto: Optional[str] = None


# ──────────────────────────────────────────────────────────────
# Serie SIMCE
# ──────────────────────────────────────────────────────────────
class SimceSerieRow(_Base):
    agno:          int
    grado:         str                  # '4b', '8b', '2m', etc.
    asignatura:    str                  # 'mat', 'lect', 'cie'
    puntaje:       Optional[float]
    n_evaluados:   Optional[int]
    en_hiatus:     bool
    prom_nacional: Optional[float]
    dif_nacional:  Optional[float]


class SimceTendenciaRow(_Base):
    grado:            str
    asignatura:       str
    segmento:         str               # 'post_hiatus' | 'full'
    valor_real:       Optional[float]
    valor_proyectado: Optional[float]
    tendencia_slope:  Optional[float]
    tendencia_r2:     Optional[float]
    rmse:             Optional[float]
    n_puntos:         Optional[int]
    confianza:        Optional[str]
    alerta:           bool


class SimceResponse(_Base):
    rbd:       str
    nom_rbd:   Optional[str] = None
    serie:     list[SimceSerieRow]
    tendencias: list[SimceTendenciaRow]


# ──────────────────────────────────────────────────────────────
# Serie SIGE
# ──────────────────────────────────────────────────────────────
class SigeSerieRow(_Base):
    agno:            int
    grado:           str
    asignatura:      Optional[str]
    prom_notas:      Optional[float]
    tasa_aprobacion: Optional[float]
    tasa_asistencia: Optional[float]
    n_alumnos:       Optional[int]


class SigeTendenciaRow(_Base):
    grado:            str
    asignatura:       Optional[str]
    valor_real:       Optional[float]
    valor_proyectado: Optional[float]
    tendencia_slope:  Optional[float]
    tendencia_r2:     Optional[float]
    alerta:           bool
    confianza:        Optional[str]


class SigeResponse(_Base):
    rbd:       str
    nom_rbd:   Optional[str] = None
    serie:     list[SigeSerieRow]
    tendencias: list[SigeTendenciaRow]


# ──────────────────────────────────────────────────────────────
# Métricas generales (analytics_establecimiento)
# ──────────────────────────────────────────────────────────────
class MetricaRow(_Base):
    metrica:          str
    agno:             Optional[int]
    grado:            Optional[str]
    asignatura:       Optional[str]
    segmento:         str
    fuente:           str
    valor_real:       Optional[float]
    valor_proyectado: Optional[float]
    tendencia_slope:  Optional[float]
    tendencia_r2:     Optional[float]
    rmse:             Optional[float]
    percentil_gse:    Optional[float]
    percentil_comuna: Optional[float]
    alerta:           bool
    n_puntos:         Optional[int]
    confianza:        Optional[str]
    calculado_en:     Optional[datetime]


class MetricasResponse(_Base):
    rbd:           str
    nom_rbd:       Optional[str] = None
    total_alertas: int = 0
    metricas:      list[MetricaRow]


# ──────────────────────────────────────────────────────────────
# Alertas
# ──────────────────────────────────────────────────────────────
class AlertaRow(_Base):
    metrica:    str
    asignatura: Optional[str]
    grado:      Optional[str]
    segmento:   str
    fuente:     str
    valor_real: Optional[float]
    confianza:  Optional[str]


class AlertasResponse(_Base):
    rbd:     str
    nom_rbd: Optional[str] = None
    alertas: list[AlertaRow]
    total:   int


# ──────────────────────────────────────────────────────────────
# Insights (textos pre-generados por el pipeline)
# ──────────────────────────────────────────────────────────────
class InsightRow(_Base):
    metrica:     str
    grado:       Optional[str]
    asignatura:  Optional[str]
    texto:       str              # texto explicativo en lenguaje llano
    nivel:       str              # 'info' | 'advertencia' | 'critico'


class InsightsResponse(_Base):
    rbd:      str
    nom_rbd:  Optional[str] = None
    insights: list[InsightRow]


# ──────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────
class PipelineRunRequest(BaseModel):
    rbd:     Optional[str] = Field(None, description="RBD a procesar; default: RBD_PILOTO env")
    modules: Optional[list[str]] = Field(
        None,
        description="Módulos a correr. None = todos.",
        examples=[["simce_serie", "simce_tendencia"]],
    )


class PipelineRunResponse(BaseModel):
    run_id:  int
    rbd:     str
    status:  Literal["running", "queued"] = "running"
    message: str = "Pipeline iniciado en background"


class PipelineStatusResponse(BaseModel):
    run_id:       int
    rbd:          str
    status:       str           # running | ok | error
    modules_run:  Optional[list[str]]
    rows_written: int
    error_msg:    Optional[str]
    started_at:   datetime
    finished_at:  Optional[datetime]
    pipeline_version: str