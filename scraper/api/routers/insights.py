"""
scraper/api/routers/insights.py
─────────────────────────────────────────────────────────────
Endpoint /insights/{rbd}

Los insights son textos explicativos en lenguaje llano generados
a partir de las métricas pre-computadas. Se generan aquí en
cada request (son ligeros — solo texto basado en valores ya calculados).

Diseño deliberado: el pipeline calcula los números; la API convierte
esos números en frases comprensibles para un director de colegio,
sin jerga estadística.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from scraper.api.db import get_db
from scraper.api.schemas import InsightRow, InsightsResponse

router = APIRouter(prefix="/insights", tags=["insights"])

# Etiquetas legibles de asignatura y grado
_ASIG_LABEL = {
    "mat":  "Matemática",
    "lect": "Lectura",
    "cie":  "Ciencias",
    "his":  "Historia",
    "ing":  "Inglés",
}
_GRADO_LABEL = {
    "2b": "2° básico", "4b": "4° básico", "6b": "6° básico",
    "8b": "8° básico", "2m": "2° medio",  "4m": "4° medio",
}


def _asig(a: Optional[str]) -> str:
    if not a or a == "__none__":
        return ""
    return _ASIG_LABEL.get(a, a)


def _grado(g: Optional[str]) -> str:
    if not g:
        return ""
    return _GRADO_LABEL.get(g.lower(), g)


def _normalize_rbd(rbd: str) -> str:
    digits = "".join(c for c in rbd if c.isdigit())
    if not digits:
        raise HTTPException(status_code=400, detail=f"RBD inválido: '{rbd}'")
    return digits.zfill(8)


def _gen_insights(rows: list) -> list[InsightRow]:
    """
    Convierte filas de analytics_establecimiento en textos para directores.
    Reglas de negocio:
      - tendencia positiva → informativo
      - tendencia negativa con confianza media/alta → advertencia
      - alerta activa → crítico
      - percentil bajo (< 30) → advertencia
    """
    insights: list[InsightRow] = []

    for r in rows:
        metrica    = r.metrica
        asig_label = _asig(r.asignatura)
        grad_label = _grado(r.grado)
        contexto   = f"{grad_label} — {asig_label}".strip(" — ")

        texto: Optional[str] = None
        nivel: str = "info"

        # ── SIMCE tendencia ────────────────────────────────────
        if metrica == "simce_tendencia":
            slope = r.tendencia_slope
            if slope is None:
                continue
            if r.alerta:
                nivel = "critico"
                texto = (
                    f"⚠ {contexto}: el puntaje SIMCE del último año se aleja "
                    f"de la tendencia histórica. Requiere análisis inmediato."
                )
            elif slope > 0:
                nivel = "info"
                texto = (
                    f"{contexto}: tendencia SIMCE al alza "
                    f"({slope:+.1f} puntos/año en el segmento {r.segmento.replace('_', '-')}). "
                    f"El establecimiento muestra mejora sostenida."
                )
            else:
                nivel = "advertencia" if r.confianza in ("alta", "media") else "info"
                texto = (
                    f"{contexto}: tendencia SIMCE a la baja "
                    f"({slope:+.1f} puntos/año). "
                    + ("Confianza del modelo: " + (r.confianza or "baja") + "." if r.confianza else "")
                )

        # ── SIMCE percentil ────────────────────────────────────
        elif metrica == "simce_percentil":
            pct_com = r.percentil_comuna
            pct_gse = r.percentil_gse
            if pct_com is not None:
                if pct_com < 30:
                    nivel = "advertencia"
                    texto = (
                        f"{contexto}: el establecimiento está en el percentil "
                        f"{pct_com:.0f} comparado con colegios de la misma comuna. "
                        f"Hay margen de mejora significativo."
                    )
                elif pct_com >= 70:
                    nivel = "info"
                    texto = (
                        f"{contexto}: el establecimiento está en el percentil "
                        f"{pct_com:.0f} dentro de su comuna — rendimiento destacado."
                    )

        # ── SIGE tendencia ─────────────────────────────────────
        elif metrica == "sige_tendencia":
            slope = r.tendencia_slope
            if slope is None:
                continue
            if r.alerta:
                nivel = "critico"
                texto = (
                    f"⚠ Calificaciones internas {contexto}: el promedio "
                    f"del último año se aleja de la tendencia histórica."
                )
            elif slope < -0.05:
                nivel = "advertencia"
                texto = (
                    f"Calificaciones internas {contexto}: baja gradual "
                    f"({slope:+.2f} puntos/año). Revisar metodología o contexto del curso."
                )
            elif slope > 0.05:
                nivel = "info"
                texto = (
                    f"Calificaciones internas {contexto}: mejora continua "
                    f"({slope:+.2f} puntos/año)."
                )

        # ── Cruce brecha ───────────────────────────────────────
        elif metrica == "cruce_brecha_interno_simce":
            brecha = r.valor_real
            if brecha is None:
                continue
            if abs(brecha) > 50:
                nivel = "critico"
                texto = (
                    f"⚠ {contexto}: brecha de {brecha:+.0f} puntos entre notas "
                    f"internas y SIMCE. Una diferencia tan grande puede indicar "
                    f"inflación de calificaciones."
                )
            elif brecha > 20:
                nivel = "advertencia"
                texto = (
                    f"{contexto}: las notas internas son {brecha:.0f} puntos "
                    f"más altas que el puntaje SIMCE — posible desalineación "
                    f"entre evaluación interna y estándar nacional."
                )
            elif brecha < -20:
                nivel = "advertencia"
                texto = (
                    f"{contexto}: el puntaje SIMCE supera en {abs(brecha):.0f} puntos "
                    f"al promedio de calificaciones internas — patrón inusual."
                )
            else:
                nivel = "info"
                texto = (
                    f"{contexto}: buena coherencia entre calificaciones internas "
                    f"y puntaje SIMCE (brecha de {brecha:+.0f} puntos)."
                )

        # ── Correlación ────────────────────────────────────────
        elif metrica == "cruce_correlacion_interno_simce":
            r_val = r.valor_real
            if r_val is None:
                continue
            if r_val >= 0.6:
                nivel = "info"
                texto = (
                    f"{contexto}: alta correlación (r={r_val:.2f}) entre notas "
                    f"internas y SIMCE. El sistema de evaluación interna predice bien "
                    f"el rendimiento externo."
                )
            elif r_val < 0.3:
                nivel = "advertencia"
                texto = (
                    f"{contexto}: baja correlación (r={r_val:.2f}) entre notas "
                    f"internas y SIMCE. El sistema de evaluación interna no predice "
                    f"el rendimiento externo."
                )

        if texto:
            insights.append(InsightRow(
                metrica=metrica,
                grado=r.grado if r.grado else None,
                asignatura=r.asignatura if r.asignatura not in (None, "__none__") else None,
                texto=texto,
                nivel=nivel,
            ))

    return insights


@router.get("/{rbd}", response_model=InsightsResponse)
def get_insights(
    rbd: str,
    db: Session = Depends(get_db),
):
    """
    Textos explicativos en lenguaje llano para el director del establecimiento.
    Generados a partir de las métricas pre-computadas por AnalyticsPipeline.

    Niveles:
      - info        → datos positivos o neutros
      - advertencia → métricas preocupantes, acción recomendada
      - critico     → alerta activa, acción inmediata
    """
    rbd = _normalize_rbd(rbd)

    sql = text("""
        SELECT metrica, agno, grado,
               CASE WHEN asignatura = '__none__' THEN NULL ELSE asignatura END AS asignatura,
               segmento, fuente, valor_real, valor_proyectado,
               tendencia_slope, tendencia_r2, rmse,
               percentil_gse, percentil_comuna,
               alerta, n_puntos, confianza
        FROM gold.analytics_establecimiento
        WHERE rbd = :rbd
        ORDER BY fuente, metrica, grado, asignatura
    """)
    rows = db.execute(sql, {"rbd": rbd}).fetchall()

    nom_rbd = db.execute(
        text("SELECT nom_rbd FROM gold.dim_establecimiento WHERE rbd = :rbd"),
        {"rbd": rbd},
    ).scalar()

    insights = _gen_insights(rows)

    # Ordenar: crítico → advertencia → info
    level_order = {"critico": 0, "advertencia": 1, "info": 2}
    insights.sort(key=lambda i: level_order.get(i.nivel, 9))

    return InsightsResponse(
        rbd=rbd,
        nom_rbd=nom_rbd,
        insights=insights,
    )