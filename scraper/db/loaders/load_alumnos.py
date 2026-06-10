import argparse
import csv
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

# Columnas que los upserts necesitan del CSV real
_STAGING_COLS = [
    "agno", "mrun", "gen_alu", "fec_nac_alu",
    "criterio_sep", "prioritario_alu", "preferente_alu", "ben_sep",
    "rbd", "cod_grado", "cod_ense", "let_cur", "cod_jor", "_source_file",
]

_UPSERT_DIM_ALUMNO = """
INSERT INTO gold.dim_alumno (mrun, gen_alu, fec_nac)
SELECT DISTINCT
    BTRIM(mrun)::BIGINT,
    NULLIF(BTRIM(gen_alu), '')::SMALLINT,
    CASE
        WHEN BTRIM(fec_nac_alu) ~ '^\\d{4}-\\d{2}-\\d{2}$' THEN BTRIM(fec_nac_alu)::DATE
        WHEN BTRIM(fec_nac_alu) ~ '^\\d{8}$' THEN to_date(BTRIM(fec_nac_alu), 'YYYYMMDD')
        ELSE NULL
    END
FROM gold.stg_alumnos_raw
WHERE BTRIM(mrun) ~ '^\\d+$'
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
    NULLIF(BTRIM(s.cod_ense),        '')::CHAR(2),
    NULLIF(BTRIM(s.let_cur),         '')::CHAR(2),
    NULLIF(BTRIM(s.cod_jor),         '')::SMALLINT,
    NULLIF(BTRIM(s.criterio_sep),    '')::SMALLINT,
    NULLIF(BTRIM(s.prioritario_alu), '')::SMALLINT,
    NULLIF(BTRIM(s.preferente_alu),  '')::SMALLINT,
    NULLIF(BTRIM(s.ben_sep),         '')::SMALLINT,
    s._source_file
FROM gold.stg_alumnos_raw s
JOIN gold.dim_alumno          a ON a.mrun      = BTRIM(s.mrun)::BIGINT
JOIN gold.dim_establecimiento e ON e.rbd        = LPAD(REGEXP_REPLACE(BTRIM(s.rbd), '[^0-9]', '', 'g'), 8, '0')
JOIN gold.dim_tiempo_escolar  t ON t.agno       = BTRIM(s.agno)::SMALLINT
                               AND t.cod_grado  = BTRIM(s.cod_grado)::SMALLINT
WHERE BTRIM(s.mrun)      ~ '^\\d+$'
  AND BTRIM(s.rbd)       ~ '^\\d+$'
  AND BTRIM(s.agno)      ~ '^\\d{4}$'
  AND BTRIM(s.cod_grado) ~ '^\\d+$'
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
            _create_staging(cur, source_path)

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
    missing   = [c for c in _STAGING_COLS if c not in csv_cols]
    if missing:
        logger.warning("Columnas ausentes en CSV (se omiten): %s", missing)

    cols_sql = ", ".join(_quote_ident(c) for c in csv_cols)
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


def _create_staging(cur, source_path: Path) -> None:
    csv_cols = _get_csv_columns(source_path)
    missing = [c for c in _STAGING_COLS if c not in csv_cols]
    if missing:
        raise ValueError(f"Columnas requeridas ausentes en CSV: {missing}")

    cols_sql = ",\n    ".join(f"{_quote_ident(c)} TEXT" for c in csv_cols)
    cur.execute("DROP TABLE IF EXISTS gold.stg_alumnos_raw;")
    cur.execute(f"CREATE UNLOGGED TABLE gold.stg_alumnos_raw (\n    {cols_sql}\n);")


def _get_csv_columns(source_path: Path) -> list[str]:
    with open(source_path, "r", encoding="utf-8-sig", errors="replace") as fh:
        row = next(csv.reader(fh))

    seen: dict[str, int] = {}
    cols: list[str] = []
    for idx, raw in enumerate(row, start=1):
        col = raw.strip().lower() or f"col_{idx}"
        count = seen.get(col, 0) + 1
        seen[col] = count
        cols.append(col if count == 1 else f"{col}__{count}")
    return cols


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


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
