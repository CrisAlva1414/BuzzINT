"""
loaders/load_alumnos.py
─────────────────────────────────────────────────────────────
Lee data/mineduc/processed/mineduc_alumnos.csv y puebla:
  - dim_alumno
  - fact_matricula

Estrategia COPY + staging para manejar ~40M filas:
  1. COPY CSV → stg_alumnos_raw  (tabla staging sin FKs, ~5 min)
  2. UPSERT dim_alumno   desde staging en un solo SQL
  3. UPSERT fact_matricula desde staging + JOINs a dimensiones
  4. DROP staging

Sin loops Python en el camino crítico — todo el trabajo lo hace Postgres.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from loaders.db import EtlRun, get_conn, transaction

logger = logging.getLogger(__name__)

LOADER_NAME = "load_alumnos"


# ──────────────────────────────────────────────────────────────
# DDL de la tabla staging (temporal, sin índices ni FKs)
# ──────────────────────────────────────────────────────────────
_DDL_STAGING = """
CREATE UNLOGGED TABLE IF NOT EXISTS gold.stg_alumnos_raw (
    agno            TEXT,
    mrun            TEXT,
    gen_alu         TEXT,
    fec_nac_alu     TEXT,
    criterio_sep    TEXT,
    prioritario_alu TEXT,
    preferente_alu  TEXT,
    ben_sep         TEXT,
    rbd             TEXT,
    cod_grado       TEXT,
    let_cur         TEXT,
    cod_jor         TEXT,
    _source_file    TEXT
);
"""

_TRUNCATE_STAGING = "TRUNCATE gold.stg_alumnos_raw;"

_DROP_STAGING = "DROP TABLE IF EXISTS gold.stg_alumnos_raw;"

# ──────────────────────────────────────────────────────────────
# Columnas que el COPY espera del CSV
# El CSV puede tener más columnas — usamos solo las que necesitamos
# ──────────────────────────────────────────────────────────────
_STAGING_COLS = [
    "agno", "mrun", "gen_alu", "fec_nac_alu",
    "criterio_sep", "prioritario_alu", "preferente_alu", "ben_sep",
    "rbd", "cod_grado", "let_cur", "cod_jor", "_source_file",
]

# ──────────────────────────────────────────────────────────────
# SQL de resolución FK → dim_alumno
# ──────────────────────────────────────────────────────────────
_UPSERT_DIM_ALUMNO = """
INSERT INTO gold.dim_alumno (mrun, gen_alu, fec_nac)
SELECT DISTINCT
    mrun::BIGINT,
    NULLIF(gen_alu, '')::SMALLINT,
    CASE
        WHEN fec_nac_alu ~ '^\\d{4}-\\d{2}-\\d{2}$' THEN fec_nac_alu::DATE
        WHEN fec_nac_alu ~ '^\\d{8}$'
            THEN to_date(fec_nac_alu, 'YYYYMMDD')
        ELSE NULL
    END
FROM gold.stg_alumnos_raw
WHERE mrun ~ '^\\d+$'
ON CONFLICT (mrun) DO UPDATE SET
    gen_alu = COALESCE(EXCLUDED.gen_alu, gold.dim_alumno.gen_alu),
    fec_nac = COALESCE(EXCLUDED.fec_nac, gold.dim_alumno.fec_nac),
    _actualizado_en = now();
"""

# ──────────────────────────────────────────────────────────────
# SQL de resolución FK → fact_matricula
# ──────────────────────────────────────────────────────────────
_UPSERT_FACT_MATRICULA = """
INSERT INTO gold.fact_matricula
    (alumno_id, establecimiento_id, tiempo_id,
     let_cur, cod_jor, criterio_sep,
     prioritario_alu, preferente_alu, ben_sep,
     _source_file)
SELECT
    a.alumno_id,
    e.establecimiento_id,
    t.tiempo_id,
    NULLIF(s.let_cur,         '')::CHAR(2),
    NULLIF(s.cod_jor,         '')::SMALLINT,
    NULLIF(s.criterio_sep,    '')::SMALLINT,
    NULLIF(s.prioritario_alu, '')::SMALLINT,
    NULLIF(s.preferente_alu,  '')::SMALLINT,
    NULLIF(s.ben_sep,         '')::SMALLINT,
    s._source_file
FROM gold.stg_alumnos_raw s
-- JOIN dimensiones (inner: descarta filas sin FK válida)
JOIN gold.dim_alumno          a ON a.mrun        = s.mrun::BIGINT
JOIN gold.dim_establecimiento e ON e.rbd          = LPAD(REGEXP_REPLACE(s.rbd, '[^0-9]', '', 'g'), 8, '0')
JOIN gold.dim_tiempo_escolar  t ON t.agno         = s.agno::SMALLINT
                                AND t.cod_grado    = s.cod_grado::SMALLINT
WHERE s.mrun      ~ '^\\d+$'
  AND s.rbd       ~ '^\\d+$'
  AND s.agno      ~ '^\\d{4}$'
  AND s.cod_grado ~ '^\\d+$'
ON CONFLICT (alumno_id, establecimiento_id, tiempo_id) DO UPDATE SET
    let_cur         = EXCLUDED.let_cur,
    cod_jor         = EXCLUDED.cod_jor,
    criterio_sep    = EXCLUDED.criterio_sep,
    prioritario_alu = EXCLUDED.prioritario_alu,
    preferente_alu  = EXCLUDED.preferente_alu,
    ben_sep         = EXCLUDED.ben_sep,
    _source_file    = EXCLUDED._source_file;
"""

# ──────────────────────────────────────────────────────────────
# Conteos para el informe ETL
# ──────────────────────────────────────────────────────────────
_COUNT_STAGING      = "SELECT COUNT(*) FROM gold.stg_alumnos_raw;"
_COUNT_DIM_ALUMNO   = "SELECT COUNT(*) FROM gold.dim_alumno;"
_COUNT_MATRICULA    = "SELECT COUNT(*) FROM gold.fact_matricula;"


def run(source_path: Path) -> None:
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn = get_conn()
    run_ctx = EtlRun(conn, LOADER_NAME, source_path.name)
    run_ctx.start()

    try:
        # ── Fase 0: preparar staging ───────────────────────────
        _phase("Preparando tabla staging", run_ctx)
        with transaction(conn) as cur:
            cur.execute(_DDL_STAGING)
            cur.execute(_TRUNCATE_STAGING)

        # ── Fase 1: COPY CSV → staging ─────────────────────────
        # Leemos el header del CSV para saber qué columnas tiene
        # y construimos un COPY solo con las que necesitamos.
        _phase("COPY CSV → stg_alumnos_raw", run_ctx)
        t0 = time.perf_counter()

        csv_cols = _get_csv_columns(source_path)
        copy_cols = [c for c in _STAGING_COLS if c in csv_cols]
        missing   = [c for c in _STAGING_COLS if c not in csv_cols]
        if missing:
            logger.warning("Columnas ausentes en CSV (se rellenan vacías): %s", missing)

        rows_copied = _copy_csv_to_staging(conn, source_path, copy_cols)
        logger.info("COPY completado: %s filas en %.1fs",
                    f"{rows_copied:,}", time.perf_counter() - t0)
        run_ctx.read = rows_copied

        # ── Fase 2: UPSERT dim_alumno ──────────────────────────
        _phase("UPSERT dim_alumno", run_ctx)
        t0 = time.perf_counter()
        with transaction(conn) as cur:
            cur.execute(_UPSERT_DIM_ALUMNO)
            cur.execute(_COUNT_DIM_ALUMNO)
            n_alumnos = cur.fetchone()[0]
        logger.info("dim_alumno: %s filas totales (%.1fs)",
                    f"{n_alumnos:,}", time.perf_counter() - t0)

        # ── Fase 3: UPSERT fact_matricula ──────────────────────
        _phase("UPSERT fact_matricula", run_ctx)
        t0 = time.perf_counter()
        with transaction(conn) as cur:
            cur.execute(_UPSERT_FACT_MATRICULA)
            cur.execute(_COUNT_MATRICULA)
            n_matricula = cur.fetchone()[0]
        logger.info("fact_matricula: %s filas totales (%.1fs)",
                    f"{n_matricula:,}", time.perf_counter() - t0)
        run_ctx.inserted = n_matricula

        # ── Fase 4: limpiar staging ────────────────────────────
        _phase("Limpiando staging", run_ctx)
        with transaction(conn) as cur:
            cur.execute(_DROP_STAGING)

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        # Intentar limpiar staging aunque haya fallado
        try:
            with transaction(conn) as cur:
                cur.execute(_DROP_STAGING)
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _copy_csv_to_staging(conn, source_path: Path, copy_cols: list[str]) -> int:
    """
    Usa COPY para cargar solo las columnas necesarias del CSV.
    Si el CSV tiene columnas extra, las ignoramos vía una vista temporal
    o cargamos todo y descartamos — lo más simple es COPY con columnas explícitas.

    psycopg2 expone copy_expert() que acepta SQL COPY arbitrario.
    """
    cols_sql = ", ".join(copy_cols)

    # COPY FROM STDIN con las columnas que existen en staging
    copy_sql = (
        f"COPY gold.stg_alumnos_raw ({cols_sql}) "
        f"FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '', ENCODING 'UTF8')"
    )

    with conn.cursor() as cur:
        with open(source_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            cur.copy_expert(copy_sql, fh)
        conn.commit()
        # Obtener filas copiadas
        cur.execute(_COUNT_STAGING)
        return cur.fetchone()[0]


def _get_csv_columns(source_path: Path) -> set[str]:
    """Lee solo la primera línea del CSV para obtener el header."""
    with open(source_path, "r", encoding="utf-8-sig", errors="replace") as fh:
        header = fh.readline().strip()
    return {c.strip().lower() for c in header.split(",")}


def _phase(msg: str, ctx: EtlRun) -> None:
    logger.info("[%s] %s", ctx.loader, msg)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    src = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else Path("data/mineduc/processed/mineduc_alumnos.csv")
    run(src)