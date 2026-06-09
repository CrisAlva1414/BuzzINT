"""
scraper/analytics/pipeline.py
─────────────────────────────────────────────────────────────
AnalyticsPipeline — Sprint 3, Fase A

Calcula métricas pre-computadas para un establecimiento piloto
y las escribe en gold.analytics_establecimiento y tablas auxiliares.

NO corre en cada request HTTP. Se invoca al final de cada ETL pipeline
o manualmente vía CLI.

Módulos internos:
  1. SimceSerieModule     — extrae y enriquece la serie SIMCE del RBD
  2. SimceTendenciaModule — regresión lineal por segmento temporal
  3. SimcePercentilModule — posición vs pares (GSE + depe + comuna)
  4. SigeSerieModule      — agrega calificaciones SIGE por año/grado/asig
  5. CruceModule          — brecha interno-SIMCE y correlación
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

# Versión del pipeline — incrementar al cambiar lógica de cálculo
PIPELINE_VERSION = "1.0"

# ──────────────────────────────────────────────────────────────
# Configuración por defecto (se sobreescribe desde analytics_config)
# ──────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "hiatus_start":      2019,
    "hiatus_end":        2021,
    "post_hiatus_start": 2022,
    "alert_threshold":   1.5,
    "min_points_trend":  3,
    "min_points_corr":   4,
}

# Mapeo grado SIMCE (string del CSV) → cod_grado BuzzINT
GRADO_SIMCE_MAP = {
    "2b": 2,
    "4b": 4,
    "6b": 6,
    "8b": 8,
    "2m": 10,
}

# Asignaturas SIMCE disponibles en los CSVs
ASIGNATURAS_SIMCE = {
    "mat":  ["ptje_mat", "prom_mate4b_rbd", "prom_mate8b_rbd", "prom_mate2m_rbd"],
    "lect": ["ptje_lect", "prom_lect4b_rbd", "prom_lect8b_rbd"],
    "cie":  ["ptje_cie",  "prom_cien4b_rbd"],
    "his":  ["ptje_his",  "prom_hist4b_rbd"],
    "ing":  ["ptje_ing",  "prom_ingl4b_rbd"],
}


# ──────────────────────────────────────────────────────────────
# Helpers estadísticos
# ──────────────────────────────────────────────────────────────

def linear_regression(years: list[int], values: list[float]) -> dict:
    """
    Regresión lineal simple sobre (años, valores).
    Retorna slope, intercept, r2, rmse, y función de proyección.
    Requiere al menos 2 puntos; retorna None si hay menos.
    """
    x = np.array(years, dtype=float)
    y = np.array(values, dtype=float)

    # Filtrar NaN
    mask = ~np.isnan(y)
    x, y = x[mask], y[mask]

    if len(x) < 2:
        return None

    result = stats.linregress(x, y)
    y_pred = result.slope * x + result.intercept
    residuals = y - y_pred
    rmse = float(np.sqrt(np.mean(residuals ** 2)))

    return {
        "slope":     float(result.slope),
        "intercept": float(result.intercept),
        "r2":        float(result.rvalue ** 2),
        "rmse":      rmse,
        "n_points":  len(x),
        "predict":   lambda yr: float(result.slope * yr + result.intercept),
    }


def confidence_label(n: int) -> str:
    if n >= 6:   return "alta"
    if n >= 4:   return "media"
    if n >= 3:   return "baja"
    return "insuficiente"


def percentile_of(value: float, population: list[float]) -> Optional[float]:
    """Percentil del valor dentro de la población (0-100)."""
    pop = [v for v in population if not np.isnan(v)]
    if not pop or np.isnan(value):
        return None
    return float(np.sum(np.array(pop) <= value) / len(pop) * 100)


def is_hiatus(agno: int, cfg: dict) -> bool:
    return cfg["hiatus_start"] <= agno <= cfg["hiatus_end"]


# ──────────────────────────────────────────────────────────────
# AnalyticsPipeline
# ──────────────────────────────────────────────────────────────

class AnalyticsPipeline:
    """
    Orquestador del analytics layer.

    Uso:
        pipeline = AnalyticsPipeline(conn, rbd="00001234")
        pipeline.run()
    """

    def __init__(self, conn, rbd: str, modules: list[str] | None = None) -> None:
        """
        conn    : conexión psycopg2 activa (sin autocommit)
        rbd     : RBD del establecimiento piloto (8 dígitos, zero-padded)
        modules : lista de módulos a correr; None = todos
                  opciones: 'simce_serie', 'simce_tendencia', 'simce_percentil',
                             'sige_serie', 'cruce'
        """
        self.conn    = conn
        self.rbd     = rbd.zfill(8)
        self.modules = modules or ["simce_serie", "simce_tendencia",
                                   "simce_percentil", "sige_serie", "cruce"]
        self.cfg     = {**DEFAULT_CONFIG}
        self._run_id: int | None = None
        self._rows_written = 0

    # ── Entrada pública ────────────────────────────────────────

    def run(self) -> dict:
        """Corre todos los módulos configurados. Retorna resumen."""
        self._load_config()
        self._start_log()
        errors = []

        try:
            dispatch = {
                "simce_serie":      self._run_simce_serie,
                "simce_tendencia":  self._run_simce_tendencia,
                "simce_percentil":  self._run_simce_percentil,
                "sige_serie":       self._run_sige_serie,
                "cruce":            self._run_cruce,
            }
            for mod in self.modules:
                if mod not in dispatch:
                    logger.warning("[analytics] módulo desconocido: %s", mod)
                    continue
                logger.info("[analytics] ▶ %s", mod)
                try:
                    n = dispatch[mod]()
                    self._rows_written += n
                    logger.info("[analytics] ✓ %s → %d filas", mod, n)
                except Exception as exc:
                    logger.error("[analytics] ✗ %s → %s", mod, exc)
                    errors.append(f"{mod}: {exc}")

        finally:
            status = "error" if errors else "ok"
            self._finish_log(status, "; ".join(errors) if errors else None)

        return {
            "rbd":          self.rbd,
            "rows_written": self._rows_written,
            "modules_run":  self.modules,
            "errors":       errors,
            "status":       "error" if errors else "ok",
        }

    # ── Configuración ──────────────────────────────────────────

    def _load_config(self) -> None:
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT key, value FROM gold.analytics_config")
                for key, val in cur.fetchall():
                    if key in ("hiatus_start", "hiatus_end", "post_hiatus_start",
                               "min_points_trend", "min_points_corr"):
                        self.cfg[key] = int(val)
                    elif key == "alert_threshold":
                        self.cfg[key] = float(val)
                    elif key == "rbd_piloto" and val:
                        # Sobreescribir rbd si está configurado en DB
                        if not os.getenv("RBD_PILOTO"):
                            self.rbd = val.zfill(8)
            logger.debug("[analytics] config cargada: %s", self.cfg)
        except Exception as exc:
            logger.warning("[analytics] no se pudo leer analytics_config: %s — usando defaults", exc)

    # ── Módulo 1: SIMCE Serie ──────────────────────────────────

    def _run_simce_serie(self) -> int:
        """
        Extrae la serie histórica SIMCE del RBD piloto desde fact_simce
        y la enriquece con benchmarks comunales de stg_simce_comuna.
        Escribe en analytics_simce_serie.
        """
        df_rbd = self._query_simce_rbd()
        if df_rbd.empty:
            logger.warning("[simce_serie] sin datos para RBD %s", self.rbd)
            return 0

        df_comuna = self._query_simce_comuna_benchmark(df_rbd)
        rows = self._build_simce_serie_rows(df_rbd, df_comuna)

        if not rows:
            return 0

        self._upsert_simce_serie(rows)
        return len(rows)

    def _query_simce_rbd(self) -> pd.DataFrame:
        sql = """
            SELECT
                t.agno,
                t.cod_grado,
                e.rbd,
                e.cod_depe,
                ter.cod_com,
                fs.ptje_mat,
                fs.ptje_lect,
                fs.ptje_cie,
                fs.ptje_his,
                fs.ptje_ing,
                fs.n_eval_mat,
                fs.n_eval_lect,
                fs.gse_predominante
            FROM gold.fact_simce fs
            JOIN gold.dim_tiempo_escolar t   ON t.tiempo_id = fs.tiempo_id
            JOIN gold.dim_establecimiento e  ON e.establecimiento_id = fs.establecimiento_id
            LEFT JOIN gold.dim_territorio ter ON ter.territorio_id = e.territorio_id
            WHERE e.rbd = %s
            ORDER BY t.agno, t.cod_grado
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (self.rbd,))
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)

    def _query_simce_comuna_benchmark(self, df_rbd: pd.DataFrame) -> pd.DataFrame:
        """Trae promedios nacionales de stg_simce_comuna para los (agno, grado) del RBD."""
        if df_rbd.empty:
            return pd.DataFrame()

        combos = df_rbd[["agno", "cod_grado"]].drop_duplicates()
        # Mapear cod_grado → grado string para buscar en stg_simce_comuna
        grado_inv = {v: k for k, v in GRADO_SIMCE_MAP.items()}

        results = []
        with self.conn.cursor() as cur:
            for _, row in combos.iterrows():
                grado_str = grado_inv.get(int(row.cod_grado))
                if not grado_str:
                    continue
                # stg_simce_comuna tiene promedios por año/grado a nivel nacional
                # Usamos el promedio de todas las comunas como proxy del promedio nacional
                cur.execute("""
                    SELECT
                        tiempo_id,
                        AVG(ptje_mat)  AS prom_nat_mat,
                        AVG(ptje_lect) AS prom_nat_lect,
                        AVG(ptje_cie)  AS prom_nat_cie
                    FROM gold.stg_simce_comuna sc
                    JOIN gold.dim_tiempo_escolar t ON t.tiempo_id = sc.tiempo_id
                    WHERE t.agno = %s
                    GROUP BY sc.tiempo_id
                """, (int(row.agno),))
                r = cur.fetchone()
                if r:
                    results.append({
                        "agno":         int(row.agno),
                        "cod_grado":    int(row.cod_grado),
                        "prom_nat_mat":  r[1],
                        "prom_nat_lect": r[2],
                        "prom_nat_cie":  r[3],
                    })

        return pd.DataFrame(results) if results else pd.DataFrame()

    def _build_simce_serie_rows(
        self, df_rbd: pd.DataFrame, df_bench: pd.DataFrame
    ) -> list[dict]:
        grado_inv = {v: k for k, v in GRADO_SIMCE_MAP.items()}
        asig_map = {
            "mat":  ("ptje_mat",  "prom_nat_mat"),
            "lect": ("ptje_lect", "prom_nat_lect"),
            "cie":  ("ptje_cie",  "prom_nat_cie"),
        }
        rows = []

        bench_idx = {}
        if not df_bench.empty:
            for _, br in df_bench.iterrows():
                bench_idx[(int(br.agno), int(br.cod_grado))] = br

        for _, r in df_rbd.iterrows():
            agno      = int(r.agno)
            cod_grado = int(r.cod_grado)
            grado_str = grado_inv.get(cod_grado, str(cod_grado))
            bench     = bench_idx.get((agno, cod_grado))
            en_hiatus = is_hiatus(agno, self.cfg)

            for asig, (col_rbd, col_bench) in asig_map.items():
                puntaje = _safe_float(r.get(col_rbd))
                if puntaje is None:
                    continue

                prom_nac = _safe_float(bench.get(col_bench)) if bench is not None else None
                dif_nac  = round(puntaje - prom_nac, 2) if prom_nac is not None else None

                rows.append({
                    "rbd":           self.rbd,
                    "agno":          agno,
                    "grado":         grado_str,
                    "asignatura":    asig,
                    "puntaje":       puntaje,
                    "n_evaluados":   _safe_int(r.get("n_eval_mat" if asig == "mat" else "n_eval_lect")),
                    "en_hiatus":     en_hiatus,
                    "prom_nacional": prom_nac,
                    "dif_nacional":  dif_nac,
                })

        return rows

    def _upsert_simce_serie(self, rows: list[dict]) -> None:
        sql = """
            INSERT INTO gold.analytics_simce_serie
                (rbd, agno, grado, asignatura, puntaje, n_evaluados,
                 en_hiatus, prom_nacional, dif_nacional)
            VALUES (%(rbd)s, %(agno)s, %(grado)s, %(asignatura)s, %(puntaje)s,
                    %(n_evaluados)s, %(en_hiatus)s, %(prom_nacional)s, %(dif_nacional)s)
            ON CONFLICT (rbd, agno, grado, asignatura) DO UPDATE SET
                puntaje       = EXCLUDED.puntaje,
                n_evaluados   = EXCLUDED.n_evaluados,
                en_hiatus     = EXCLUDED.en_hiatus,
                prom_nacional = EXCLUDED.prom_nacional,
                dif_nacional  = EXCLUDED.dif_nacional,
                calculado_en  = now()
        """
        with self.conn.cursor() as cur:
            for row in rows:
                cur.execute(sql, row)
        self.conn.commit()


    def _run_simce_tendencia(self) -> int:
        sql = """
            SELECT agno, grado, asignatura, puntaje
            FROM gold.analytics_simce_serie
            WHERE rbd = %s AND puntaje IS NOT NULL
            ORDER BY agno
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (self.rbd,))
            cols = [d[0] for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)

        if df.empty:
            return 0

        rows_written = 0
        for (grado, asig), grp in df.groupby(["grado", "asignatura"]):
            grp = grp.sort_values("agno")

            # Segmento post-hiatus
            post = grp[grp["agno"] >= self.cfg["post_hiatus_start"]]
            rows_written += self._write_tendencia(
                grado, asig, post, "post_hiatus"
            )

            # Segmento full (excluyendo años de hiatus)
            full = grp[~grp["agno"].apply(lambda a: is_hiatus(a, self.cfg))]
            rows_written += self._write_tendencia(
                grado, asig, full, "full"
            )

        return rows_written

    def _write_tendencia(
        self, grado: str, asig: str,
        df: pd.DataFrame, segmento: str
    ) -> int:
        min_pts = self.cfg["min_points_trend"]
        df = df.dropna(subset=["puntaje"])

        if len(df) < 2:
            return 0

        years  = df["agno"].tolist()
        values = df["puntaje"].tolist()
        reg    = linear_regression(years, values)

        if reg is None:
            return 0

        n           = reg["n_points"]
        ultimo_agno = max(years)
        ultimo_val  = df[df["agno"] == ultimo_agno]["puntaje"].iloc[0]
        proj_next   = reg["predict"](ultimo_agno + 1)

        alerta = False
        if reg["rmse"] > 0:
            alerta = abs(ultimo_val - reg["predict"](ultimo_agno)) > (
                self.cfg["alert_threshold"] * reg["rmse"]
            )

        self._upsert_analytics(dict(
            rbd=self.rbd,
            agno=int(ultimo_agno),
            grado=grado,
            segmento=segmento,
            metrica="simce_tendencia",
            asignatura=asig,
            fuente="simce",
            valor_real=float(ultimo_val),
            valor_proyectado=float(proj_next),
            tendencia_slope=reg["slope"],
            tendencia_r2=reg["r2"],
            rmse=reg["rmse"],
            alerta=alerta,
            n_puntos=n,
            confianza=confidence_label(n),
        ))
        return 1


    def _run_simce_percentil(self) -> int:
        # Traer datos del establecimiento piloto
        sql_piloto = """
            SELECT agno, grado, asignatura, puntaje
            FROM gold.analytics_simce_serie
            WHERE rbd = %s AND puntaje IS NOT NULL
        """
        with self.conn.cursor() as cur:
            cur.execute(sql_piloto, (self.rbd,))
            df_piloto = pd.DataFrame(cur.fetchall(),
                                     columns=["agno", "grado", "asig", "puntaje"])

        if df_piloto.empty:
            return 0

        # Traer info del establecimiento (cod_com, cod_depe, gse)
        info = self._get_establecimiento_info()
        rows_written = 0

        for _, row in df_piloto.iterrows():
            agno  = int(row["agno"])
            grado = row["grado"]
            asig  = row["asig"]
            val   = float(row["puntaje"])

            # Percentil comunal
            pop_com = self._get_simce_population(
                agno, grado, asig,
                filter_type="comuna",
                filter_val=info.get("cod_com"),
            )
            pct_com = percentile_of(val, pop_com) if pop_com else None

            # Percentil GSE
            pop_gse = self._get_simce_population(
                agno, grado, asig,
                filter_type="gse",
                filter_val=(info.get("cod_depe"), info.get("gse")),
            )
            pct_gse = percentile_of(val, pop_gse) if pop_gse else None

            if pct_com is None and pct_gse is None:
                continue

            self._upsert_analytics(dict(
                rbd=self.rbd,
                agno=agno,
                grado=grado,
                segmento="current",
                metrica="simce_percentil",
                asignatura=asig,
                fuente="simce",
                valor_real=val,
                percentil_gse=pct_gse,
                percentil_comuna=pct_com,
                n_puntos=len(pop_gse or pop_com or []),
                confianza="alta",
                alerta=False,
            ))
            rows_written += 1

        return rows_written

    def _get_establecimiento_info(self) -> dict:
        sql = """
            SELECT e.cod_depe, ter.cod_com, fs.gse_predominante
            FROM gold.dim_establecimiento e
            LEFT JOIN gold.dim_territorio ter ON ter.territorio_id = e.territorio_id
            LEFT JOIN gold.fact_simce fs      ON fs.establecimiento_id = e.establecimiento_id
            WHERE e.rbd = %s
            ORDER BY fs.tiempo_id DESC
            LIMIT 1
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (self.rbd,))
            r = cur.fetchone()
        if not r:
            return {}
        return {"cod_depe": r[0], "cod_com": r[1], "gse": r[2]}

    def _get_simce_population(
        self,
        agno: int,
        grado: str,
        asig: str,
        filter_type: str,
        filter_val,
    ) -> list[float]:
        asig_col = {
            "mat":  "fs.ptje_mat",
            "lect": "fs.ptje_lect",
            "cie":  "fs.ptje_cie",
        }.get(asig)

        if not asig_col or filter_val is None:
            return []

        grado_int = GRADO_SIMCE_MAP.get(grado)
        if grado_int is None:
            return []

        if filter_type == "comuna":
            sql = f"""
                SELECT {asig_col}
                FROM gold.fact_simce fs
                JOIN gold.dim_tiempo_escolar t  ON t.tiempo_id = fs.tiempo_id
                JOIN gold.dim_establecimiento e ON e.establecimiento_id = fs.establecimiento_id
                LEFT JOIN gold.dim_territorio ter ON ter.territorio_id = e.territorio_id
                WHERE t.agno = %s AND t.cod_grado = %s
                  AND ter.cod_com = %s
                  AND {asig_col} IS NOT NULL
            """
            params = (agno, grado_int, filter_val)

        elif filter_type == "gse":
            cod_depe, gse = filter_val
            if not cod_depe or not gse:
                return []
            sql = f"""
                SELECT {asig_col}
                FROM gold.fact_simce fs
                JOIN gold.dim_tiempo_escolar t  ON t.tiempo_id = fs.tiempo_id
                JOIN gold.dim_establecimiento e ON e.establecimiento_id = fs.establecimiento_id
                WHERE t.agno = %s AND t.cod_grado = %s
                  AND e.cod_depe = %s
                  AND fs.gse_predominante = %s
                  AND {asig_col} IS NOT NULL
            """
            params = (agno, grado_int, cod_depe, gse)
        else:
            return []

        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return [float(r[0]) for r in cur.fetchall() if r[0] is not None]


    def _run_sige_serie(self) -> int:
        sql = """
            SELECT
                t.agno,
                t.cod_grado,
                COALESCE(asig.subsector, 'Sin asignatura') AS asignatura,
                ROUND(AVG(fc.promedio) FILTER (WHERE fc.promedio IS NOT NULL), 2)
                    AS prom_notas,
                ROUND(
                    100.0 * COUNT(*) FILTER (WHERE fc.situacion_final = 'APR')
                    / NULLIF(COUNT(*) FILTER (WHERE fc.situacion_final IS NOT NULL), 0),
                    2
                ) AS tasa_aprobacion,
                ROUND(AVG(fc.asistencia_pct) FILTER (WHERE fc.asistencia_pct IS NOT NULL), 2)
                    AS tasa_asistencia,
                COUNT(DISTINCT fc.alumno_id) AS n_alumnos
            FROM gold.fact_calificaciones fc
            JOIN gold.dim_establecimiento e  ON e.establecimiento_id = fc.establecimiento_id
            JOIN gold.dim_tiempo_escolar t   ON t.tiempo_id = fc.tiempo_id
            LEFT JOIN gold.dim_asignatura asig ON asig.asignatura_id = fc.asignatura_id
            WHERE e.rbd = %s
            GROUP BY t.agno, t.cod_grado, asig.subsector
            ORDER BY t.agno, t.cod_grado
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (self.rbd,))
            cols = [d[0] for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)

        if df.empty:
            logger.info("[sige_serie] sin datos SIGE para RBD %s", self.rbd)
            return 0

        grado_inv = {v: k for k, v in GRADO_SIMCE_MAP.items()}
        upsert_sql = """
            INSERT INTO gold.analytics_sige_serie
                (rbd, agno, grado, asignatura, prom_notas,
                 tasa_aprobacion, tasa_asistencia, n_alumnos)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (rbd, agno, grado, asignatura) DO UPDATE SET
                prom_notas      = EXCLUDED.prom_notas,
                tasa_aprobacion = EXCLUDED.tasa_aprobacion,
                tasa_asistencia = EXCLUDED.tasa_asistencia,
                n_alumnos       = EXCLUDED.n_alumnos,
                calculado_en    = now()
        """
        count = 0
        with self.conn.cursor() as cur:
            for _, r in df.iterrows():
                grado_str = grado_inv.get(int(r.cod_grado), str(r.cod_grado))
                cur.execute(upsert_sql, (
                    self.rbd,
                    int(r.agno),
                    grado_str,
                    str(r.asignatura)[:80],
                    _safe_float(r.prom_notas),
                    _safe_float(r.tasa_aprobacion),
                    _safe_float(r.tasa_asistencia),
                    _safe_int(r.n_alumnos),
                ))
                count += 1

        # Calcular también tendencia por asignatura (escribe en analytics_establecimiento)
        self._calc_sige_tendencias(df, grado_inv)
        self.conn.commit()
        return count

    def _calc_sige_tendencias(
        self, df: pd.DataFrame, grado_inv: dict
    ) -> None:
        for (cod_grado, asig), grp in df.groupby(["cod_grado", "asignatura"]):
            grp = grp.sort_values("agno").dropna(subset=["prom_notas"])
            if len(grp) < 2:
                continue

            grado_str = grado_inv.get(int(cod_grado), str(cod_grado))
            years     = grp["agno"].tolist()
            values    = grp["prom_notas"].tolist()
            reg       = linear_regression(years, values)

            if reg is None:
                continue

            ultimo_agno = max(years)
            ultimo_val  = float(grp[grp["agno"] == ultimo_agno]["prom_notas"].iloc[0])
            proj_next   = reg["predict"](ultimo_agno + 1)

            alerta = False
            if reg["rmse"] > 0:
                alerta = abs(ultimo_val - reg["predict"](ultimo_agno)) > (
                    self.cfg["alert_threshold"] * reg["rmse"]
                )

            self._upsert_analytics(dict(
                rbd=self.rbd,
                agno=int(ultimo_agno),
                grado=grado_str,
                segmento="full",
                metrica="sige_tendencia",
                asignatura=str(asig)[:40],
                fuente="sige",
                valor_real=ultimo_val,
                valor_proyectado=float(proj_next),
                tendencia_slope=reg["slope"],
                tendencia_r2=reg["r2"],
                rmse=reg["rmse"],
                alerta=alerta,
                n_puntos=reg["n_points"],
                confianza=confidence_label(reg["n_points"]),
            ))

    def _run_cruce(self) -> int:
        # Traer SIMCE post-carga
        sql_simce = """
            SELECT agno, grado, asignatura, puntaje
            FROM gold.analytics_simce_serie
            WHERE rbd = %s AND puntaje IS NOT NULL
        """
        with self.conn.cursor() as cur:
            cur.execute(sql_simce, (self.rbd,))
            df_simce = pd.DataFrame(cur.fetchall(),
                                    columns=["agno", "grado", "asig", "puntaje_simce"])

        if df_simce.empty:
            return 0

        # Traer SIGE promedio global por año/grado (agregado, sin filtro asignatura)
        sql_sige = """
            SELECT agno, grado,
                   ROUND(AVG(prom_notas) FILTER (WHERE prom_notas IS NOT NULL), 2)
                       AS prom_global
            FROM gold.analytics_sige_serie
            WHERE rbd = %s
            GROUP BY agno, grado
        """
        with self.conn.cursor() as cur:
            cur.execute(sql_sige, (self.rbd,))
            df_sige = pd.DataFrame(cur.fetchall(),
                                   columns=["agno", "grado", "prom_global"])

        if df_sige.empty:
            return 0

        rows_written = 0
        min_corr = self.cfg["min_points_corr"]

        for (grado, asig), grp_simce in df_simce.groupby(["grado", "asig"]):
            grp_simce = grp_simce.sort_values("agno")

            # Cruzar: el SIGE del año previo al SIMCE
            pairs = []
            for _, sr in grp_simce.iterrows():
                agno_simce = int(sr["agno"])
                agno_sige  = agno_simce - 1

                sige_row = df_sige[
                    (df_sige["agno"] == agno_sige) &
                    (df_sige["grado"] == grado)
                ]
                if sige_row.empty:
                    # Intentar mismo año si no hay previo
                    sige_row = df_sige[
                        (df_sige["agno"] == agno_simce) &
                        (df_sige["grado"] == grado)
                    ]

                if not sige_row.empty and sige_row["prom_global"].iloc[0] is not None:
                    pairs.append({
                        "agno":          agno_simce,
                        "prom_interno":  float(sige_row["prom_global"].iloc[0]),
                        "puntaje_simce": float(sr["puntaje_simce"]),
                    })

            if not pairs:
                continue

            # Brecha del par más reciente
            ultimo_par = max(pairs, key=lambda p: p["agno"])
            brecha = round(
                ultimo_par["prom_interno"] - ultimo_par["puntaje_simce"], 2
            )

            self._upsert_analytics(dict(
                rbd=self.rbd,
                agno=int(ultimo_par["agno"]),
                grado=grado,
                segmento="current",
                metrica="cruce_brecha_interno_simce",
                asignatura=asig,
                fuente="cruce",
                valor_real=brecha,
                n_puntos=len(pairs),
                confianza=confidence_label(len(pairs)),
                alerta=abs(brecha) > 50,  # brecha > 50 ptos es señal clara
            ))
            rows_written += 1

            # Correlación Pearson si hay suficientes pares
            if len(pairs) >= min_corr:
                internos = [p["prom_interno"]  for p in pairs]
                simces   = [p["puntaje_simce"] for p in pairs]
                r, pval  = stats.pearsonr(internos, simces)

                self._upsert_analytics(dict(
                    rbd=self.rbd,
                    agno=int(ultimo_par["agno"]),
                    grado=grado,
                    segmento="full",
                    metrica="cruce_correlacion_interno_simce",
                    asignatura=asig,
                    fuente="cruce",
                    valor_real=round(float(r), 4),
                    n_puntos=len(pairs),
                    confianza=confidence_label(len(pairs)),
                    alerta=False,
                ))
                rows_written += 1

        self.conn.commit()
        return rows_written


    def _upsert_analytics(self, row: dict) -> None:
        # Rellenar campos opcionales con None
        defaults = {
            "agno": None, "grado": None, "asignatura": None,
            "valor_real": None, "valor_proyectado": None,
            "tendencia_slope": None, "tendencia_r2": None, "rmse": None,
            "percentil_gse": None, "percentil_comuna": None,
            "alerta": False, "n_puntos": None, "confianza": None,
        }
        r = {**defaults, **row}

        sql = """
            INSERT INTO gold.analytics_establecimiento
                (rbd, agno, grado, segmento, metrica, asignatura, fuente,
                 valor_real, valor_proyectado, tendencia_slope, tendencia_r2,
                 rmse, percentil_gse, percentil_comuna, alerta,
                 n_puntos, confianza, pipeline_version)
            VALUES
                (%(rbd)s, %(agno)s, %(grado)s, %(segmento)s, %(metrica)s,
                 %(asignatura)s, %(fuente)s,
                 %(valor_real)s, %(valor_proyectado)s, %(tendencia_slope)s,
                 %(tendencia_r2)s, %(rmse)s, %(percentil_gse)s,
                 %(percentil_comuna)s, %(alerta)s, %(n_puntos)s,
                 %(confianza)s, %(pipeline_version)s)
            ON CONFLICT (rbd, agno, grado, segmento, metrica, asignatura)
            DO UPDATE SET
                valor_real        = EXCLUDED.valor_real,
                valor_proyectado  = EXCLUDED.valor_proyectado,
                tendencia_slope   = EXCLUDED.tendencia_slope,
                tendencia_r2      = EXCLUDED.tendencia_r2,
                rmse              = EXCLUDED.rmse,
                percentil_gse     = EXCLUDED.percentil_gse,
                percentil_comuna  = EXCLUDED.percentil_comuna,
                alerta            = EXCLUDED.alerta,
                n_puntos          = EXCLUDED.n_puntos,
                confianza         = EXCLUDED.confianza,
                calculado_en      = now(),
                pipeline_version  = EXCLUDED.pipeline_version
        """
        # Asegurar que NULL en asignatura no rompa el UNIQUE (usa COALESCE en SQL)
        if r.get("asignatura") is None:
            r["asignatura"] = "__none__"

        with self.conn.cursor() as cur:
            cur.execute(sql, {**r, "pipeline_version": PIPELINE_VERSION})


    def _start_log(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gold.analytics_run_log
                    (rbd, status, modules_run, pipeline_version)
                VALUES (%s, 'running', %s, %s)
                RETURNING run_id
                """,
                (self.rbd, self.modules, PIPELINE_VERSION),
            )
            self._run_id = cur.fetchone()[0]
        self.conn.commit()
        logger.info("[analytics] run_id=%s  RBD=%s", self._run_id, self.rbd)

    def _finish_log(self, status: str, error_msg: str | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE gold.analytics_run_log
                SET status=%s, rows_written=%s, error_msg=%s, finished_at=now()
                WHERE run_id=%s
                """,
                (status, self._rows_written, error_msg, self._run_id),
            )
        self.conn.commit()
        logger.info(
            "[analytics] run_id=%s finalizado — status=%s rows=%d",
            self._run_id, status, self._rows_written,
        )


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> Optional[int]:
    f = _safe_float(v)
    return int(f) if f is not None else None


def main() -> None:
    import argparse
    import sys
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Corre el AnalyticsPipeline para un RBD")
    parser.add_argument("--rbd",     default=os.getenv("RBD_PILOTO", ""),
                        help="RBD del establecimiento (default: RBD_PILOTO env var)")
    parser.add_argument("--modules", nargs="+",
                        choices=["simce_serie", "simce_tendencia", "simce_percentil",
                                 "sige_serie", "cruce"],
                        help="Módulos a correr (default: todos)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.rbd:
        print("ERROR: --rbd requerido o configurar RBD_PILOTO en .env")
        sys.exit(1)

    try:
        import psycopg2
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
        print(f"ERROR conectando a PostgreSQL: {exc}")
        sys.exit(1)

    pipeline = AnalyticsPipeline(conn, rbd=args.rbd, modules=args.modules)
    result   = pipeline.run()
    conn.close()

    print(f"\n{'═'*52}")
    print(f"  RBD          : {result['rbd']}")
    print(f"  Status       : {result['status']}")
    print(f"  Filas escritas: {result['rows_written']}")
    print(f"  Módulos      : {', '.join(result['modules_run'])}")
    if result["errors"]:
        print(f"  Errores      : {result['errors']}")
    print(f"{'═'*52}")
    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()