import argparse
import logging
import sys
import time
from pathlib import Path

try:
    from .db import EtlRun, get_conn, transaction, _conn_kwargs
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scraper.db.loaders.db import EtlRun, get_conn, transaction, _conn_kwargs

logger = logging.getLogger(__name__)

_INPUT_DEFAULT = "data/mineduc/processed/mineduc_alumnos.csv"

# Columnas que el staging espera — subset del CSV real
_STAGING_COLS = [
    "agno", "mrun", "gen_alu", "fec_nac_alu",
    "criterio_sep", "prioritario_alu", "preferente_alu", "ben_sep",
    "rbd", "cod_grado", "cod_ense", "let_cur", "cod_jor", "_source_file",
]

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
    cod_ense        TEXT,
    let_cur         TEXT,
    cod_jor         TEXT,
    _source_file    TEXT
);
"""

_UPSERT_DIM_ALUMNO = """
INSERT INTO gold.dim_alumno (mrun, gen_alu, fec_nac)
SELECT DISTINCT
    mrun::BIGINT,
    NULLIF(gen_alu, '')::SMALLINT,
    CASE
        WHEN fec_nac_alu ~ '^\\d{4}-\\d{2}-\\d{2}$' THEN fec_nac_alu::DATE
        WHEN fec_nac_alu ~ '^\\d{8}$' THEN to_date(fec_nac_alu, 'YYYYMMDD')
        ELSE NULL
    END
FROM gold.stg_alumnos_raw
WHERE mrun ~ '^\\d+$'
ON CONFLICT (mrun) DO UPDATE SET
    gen_alu = COALESCE(EXCLUDED.gen_alu, gold.dim_alumno.gen_alu),
    fec_nac = COALESCE(EXCLUDED.fec_nac, gold.dim_alumno.fec_nac),
    _actualizado_en = now();
"""

# Constraint actualizada: incluye cod_ense para soportar traslados (patch 03)
_UPSERT_FACT_MATRICULA = """
INSERT INTO gold.fact_matricula
    (alumno_id, establecimiento_id, tiempo_id,
     cod_ense, let_cur, cod_jor,
     criterio_sep, prioritario_alu, preferente_alu, ben_sep,
     _source_file)
SELECT
    a.alumno_id,
    e.establecimiento_id,
    t.tiempo_id,
    NULLIF(s.cod_ense,        '')::CHAR(2),
    NULLIF(s.let_cur,         '')::CHAR(2),
    NULLIF(s.cod_jor,         '')::SMALLINT,
    NULLIF(s.criterio_sep,    '')::SMALLINT,
    NULLIF(s.prioritario_alu, '')::SMALLINT,
    NULLIF(s.preferente_alu,  '')::SMALLINT,
    NULLIF(s.ben_sep,         '')::SMALLINT,
    s._source_file
FROM gold.stg_alumnos_raw s
JOIN gold.dim_alumno          a ON a.mrun      = s.mrun::BIGINT
JOIN gold.dim_establecimiento e ON e.rbd        = LPAD(REGEXP_REPLACE(s.rbd, '[^0-9]', '', 'g'), 8, '0')
JOIN gold.dim_tiempo_escolar  t ON t.agno       = s.agno::SMALLINT
                               AND t.cod_grado  = s.cod_grado::SMALLINT
WHERE s.mrun      ~ '^\\d+$'
  AND s.rbd       ~ '^\\d+$'
  AND s.agno      ~ '^\\d{4}$'
  AND s.cod_grado ~ '^\\d+$'
ON CONFLICT ON CONSTRAINT uq_fact_matricula DO UPDATE SET
    let_cur         = EXCLUDED.let_cur,
    cod_jor         = EXCLUDED.cod_jor,
    criterio_sep    = EXCLUDED.criterio_sep,
    prioritario_alu = EXCLUDED.prioritario_alu,
    preferente_alu  = EXCLUDED.preferente_alu,
    ben_sep         = EXCLUDED.ben_sep,
    _source_file    = EXCLUDED._source_file;
"""


def run(source_path: Path, dry_run: bool = False) -> dict:
    source_path = Path(source_path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn    = get_conn()
    run_ctx = EtlRun(conn, "load_alumnos", source_path.name)
    run_ctx.start()

    try:
        _phase("Preparando staging", run_ctx)
        with transaction(conn) as cur:
            cur.execute(_DDL_STAGING)
            cur.execute("TRUNCATE gold.stg_alumnos_raw;")

        if not dry_run:
            _phase("COPY CSV → staging", run_ctx)
            t0 = time.perf_counter()
            rows_copied = _copy_to_staging(conn, source_path)
            logger.info("COPY: %s filas en %.1fs", f"{rows_copied:,}", time.perf_counter() - t0)
            run_ctx.read = rows_copied

            _phase("UPSERT dim_alumno", run_ctx)
            with transaction(conn) as cur:
                cur.execute(_UPSERT_DIM_ALUMNO)
                cur.execute("SELECT COUNT(*) FROM gold.dim_alumno;")
                logger.info("dim_alumno: %s filas totales", f"{cur.fetchone()[0]:,}")

            _phase("UPSERT fact_matricula", run_ctx)
            t0 = time.perf_counter()
            with transaction(conn) as cur:
                cur.execute(_UPSERT_FACT_MATRICULA)
                cur.execute("SELECT COUNT(*) FROM gold.fact_matricula;")
                n = cur.fetchone()[0]
                run_ctx.inserted = n
            logger.info("fact_matricula: %s filas totales en %.1fs",
                        f"{n:,}", time.perf_counter() - t0)
        else:
            logger.info("dry-run: COPY y upserts saltados")

        _phase("Limpiando staging", run_ctx)
        with transaction(conn) as cur:
            cur.execute("DROP TABLE IF EXISTS gold.stg_alumnos_raw;")

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        try:
            with transaction(conn) as cur:
                cur.execute("DROP TABLE IF EXISTS gold.stg_alumnos_raw;")
        except Exception:
            pass
        raise
    finally:
        conn.close()

    return {
        "rows_read":     run_ctx.read,
        "rows_inserted": run_ctx.inserted,
        "rows_skipped":  run_ctx.skipped,
    }


def _copy_to_staging(conn, source_path: Path) -> int:
    csv_cols  = _get_csv_columns(source_path)
    copy_cols = [c for c in _STAGING_COLS if c in csv_cols]
    missing   = [c for c in _STAGING_COLS if c not in csv_cols]
    if missing:
        logger.warning("Columnas ausentes en CSV (se omiten): %s", missing)

    cols_sql = ", ".join(copy_cols)
    copy_sql = (
        f"COPY gold.stg_alumnos_raw ({cols_sql}) "
        f"FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '', ENCODING 'UTF8')"
    )
    with conn.cursor() as cur:
        with open(source_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            cur.copy_expert(copy_sql, fh)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM gold.stg_alumnos_raw;")
        return cur.fetchone()[0]


def _get_csv_columns(source_path: Path) -> set[str]:
    with open(source_path, "r", encoding="utf-8-sig", errors="replace") as fh:
        header = fh.readline().strip()
    return {c.strip().lower() for c in header.split(",")}


def _phase(msg: str, ctx: EtlRun) -> None:
    logger.info("[%s] %s", ctx.loader, msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga alumnos SEP → Gold Layer (COPY masivo)")
    parser.add_argument("--input",   default=_INPUT_DEFAULT, metavar="FILE")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    stats = run(Path(args.input), dry_run=args.dry_run)
    print(f"\n{'═'*52}")
    print(f"  Leídas   : {stats['rows_read']:,}")
    print(f"  Insertadas: {stats['rows_inserted']:,}")
    print(f"{'═'*52}")
    sys.exit(0)


if __name__ == "__main__":
    main()