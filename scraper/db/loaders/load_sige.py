import argparse
import csv
import logging
import sys
from pathlib import Path

try:
    from .db import (
        CHUNK_SIZE, EtlRun, batch_upsert, get_conn, transaction,
        get_establecimiento_id, get_or_create_alumno, get_or_create_asignatura,
        get_tiempo_id, _int, _float, _str, _date, _rbd,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scraper.db.loaders.db import (
        CHUNK_SIZE, EtlRun, batch_upsert, get_conn, transaction,
        get_establecimiento_id, get_or_create_alumno, get_or_create_asignatura,
        get_tiempo_id, _int, _float, _str, _date, _rbd,
    )

logger = logging.getLogger(__name__)

_CAL_DEFAULT  = "data/sige/processed/sige_calificaciones.csv"
_PROF_DEFAULT = "data/sige/processed/sige_profesores.csv"

_NOTA_COLS = [f"nota_{i}" for i in range(1, 26)]

# SQL con %s para execute_values
_INSERT_CAL = """
INSERT INTO gold.fact_calificaciones
    (alumno_id, establecimiento_id, tiempo_id, asignatura_id,
     letra, n_orden, fecha_acta,
     nota_1,nota_2,nota_3,nota_4,nota_5,nota_6,nota_7,nota_8,nota_9,nota_10,
     nota_11,nota_12,nota_13,nota_14,nota_15,nota_16,nota_17,nota_18,nota_19,nota_20,
     nota_21,nota_22,nota_23,nota_24,nota_25,
     promedio, prom_literario, asistencia_pct, situacion_final, observaciones, _source_file)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_fact_calificaciones DO UPDATE SET
    promedio        = EXCLUDED.promedio,
    asistencia_pct  = EXCLUDED.asistencia_pct,
    situacion_final = EXCLUDED.situacion_final,
    _source_file    = EXCLUDED._source_file
"""


def _parse_run(run_str: str) -> int | None:
    clean = run_str.replace(".", "").strip()
    if "-" in clean:
        clean = clean.split("-")[0]
    try:
        return int(clean)
    except ValueError:
        return None


def _read_csv_chunks(path: Path, chunk_size: int):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        chunk  = []
        for row in reader:
            chunk.append(row)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk


def run_calificaciones(source_path: Path, dry_run: bool = False) -> None:
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn    = get_conn()
    run_ctx = EtlRun(conn, "load_sige_cal", source_path.name)
    run_ctx.start()

    # Caches de lookup dimensional — evitan N queries por fila
    _estab_cache: dict[str, int | None] = {}
    _tiempo_cache: dict[tuple, int | None] = {}
    _asig_cache: dict[tuple, int | None] = {}

    try:
        for chunk in _read_csv_chunks(source_path, CHUNK_SIZE):
            run_ctx.read += len(chunk)
            rows_batch: list[tuple] = []

            with transaction(conn) as cur:
                for row in chunk:
                    rbd_raw = _str(row.get("rbd"))
                    if rbd_raw not in _estab_cache:
                        _estab_cache[rbd_raw] = get_establecimiento_id(cur, rbd_raw)
                    estab_id = _estab_cache[rbd_raw]
                    if not estab_id:
                        run_ctx.skipped += 1
                        continue

                    agno      = _int(row.get("agno"))
                    cod_grado = _int(row.get("grado"))
                    if not agno or not cod_grado:
                        run_ctx.skipped += 1
                        continue

                    t_key = (agno, cod_grado)
                    if t_key not in _tiempo_cache:
                        _tiempo_cache[t_key] = get_tiempo_id(cur, agno, cod_grado)
                    tiempo_id = _tiempo_cache[t_key]
                    if not tiempo_id:
                        run_ctx.skipped += 1
                        continue

                    # dim_alumno — SIGE trae run con dígito verificador
                    mrun = _parse_run(_str(row.get("run")) or "")
                    alumno_id = None
                    if mrun:
                        proxy     = {"mrun": mrun, "gen_alu": row.get("sexo"), "fec_nac_alu": row.get("fec_nac")}
                        alumno_id = get_or_create_alumno(cur, proxy)

                    # dim_asignatura — inferida desde grado
                    cod_ense = "11" if cod_grado <= 8 else "23"
                    a_key    = (cod_ense, "")
                    if a_key not in _asig_cache:
                        _asig_cache[a_key] = get_or_create_asignatura(cur, cod_ense, None)
                    asig_id = _asig_cache[a_key]

                    letra   = _str(row.get("letra")) or "A"
                    n_orden = _int(row.get("n_orden"))

                    notas = tuple(_float(row.get(nc)) for nc in _NOTA_COLS)

                    rows_batch.append((
                        alumno_id, estab_id, tiempo_id, asig_id,
                        letra, n_orden, _str(row.get("fecha_acta")),
                        *notas,
                        _float(row.get("promedio")),
                        _str(row.get("prom_literario")),
                        _int(row.get("asistencia_pct")),
                        _str(row.get("situacion_final")),
                        _str(row.get("observaciones")),
                        source_path.name,
                    ))

                if rows_batch and not dry_run:
                    n = batch_upsert(cur, _INSERT_CAL, rows_batch)
                    run_ctx.inserted += n

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()

    logger.info("SIGE cal: %d leídas, %d insertadas, %d saltadas",
                run_ctx.read, run_ctx.inserted, run_ctx.skipped)


def run_profesores(source_path: Path, dry_run: bool = False) -> None:
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        logger.warning("sige_profesores.csv no encontrado — saltando: %s", source_path)
        return

    conn    = get_conn()
    run_ctx = EtlRun(conn, "load_sige_prof", source_path.name)
    run_ctx.start()

    _asig_cache: dict[tuple, int | None] = {}

    try:
        for chunk in _read_csv_chunks(source_path, CHUNK_SIZE):
            run_ctx.read += len(chunk)
            docente_rows: list[tuple] = []

            with transaction(conn) as cur:
                for row in chunk:
                    cod_ense  = "11" if _int(row.get("grado") or 0) <= 8 else "23"
                    subsector = _str(row.get("subsector"))
                    if subsector:
                        a_key = (cod_ense, subsector)
                        if a_key not in _asig_cache:
                            _asig_cache[a_key] = get_or_create_asignatura(cur, cod_ense, subsector)

                    mrun = _parse_run(_str(row.get("run_profesor")) or "")
                    if mrun:
                        docente_rows.append((mrun,))

                if docente_rows and not dry_run:
                    execute_sql = (
                        "INSERT INTO gold.dim_docente (mrun) VALUES %s "
                        "ON CONFLICT (mrun) DO NOTHING"
                    )
                    batch_upsert(cur, execute_sql, docente_rows)
                    run_ctx.inserted += len(docente_rows)

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga datos SIGE en Gold Layer")
    parser.add_argument("--cal",     default=_CAL_DEFAULT,  metavar="FILE")
    parser.add_argument("--prof",    default=_PROF_DEFAULT, metavar="FILE")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    run_calificaciones(Path(args.cal),  dry_run=args.dry_run)
    run_profesores(Path(args.prof), dry_run=args.dry_run)


if __name__ == "__main__":
    main()