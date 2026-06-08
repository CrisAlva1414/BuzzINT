"""
loaders/load_sige.py
─────────────────────────────────────────────────────────────
Lee data/sige/processed/ y puebla:
  - sige_calificaciones.csv → fact_calificaciones + dim_alumno
  - sige_profesores.csv     → dim_docente + dim_asignatura (enriquece)
Prerequisito: dim_establecimiento y dim_tiempo_escolar ya cargadas.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .db import (
    EtlRun, get_conn, transaction,
    get_or_create_alumno, get_or_create_docente, get_or_create_asignatura,
    get_establecimiento_id, get_tiempo_id,
    _int, _float, _str,
)

logger = logging.getLogger(__name__)

CHUNK_SIZE = 10_000

# Slots de nota que puede traer el CSV (nota_1 .. nota_25)
_NOTA_COLS = [f"nota_{i}" for i in range(1, 26)]


def run_calificaciones(source_path: Path) -> None:
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn = get_conn()
    run_ctx = EtlRun(conn, "load_sige_cal", source_path.name)
    run_ctx.start()

    _estab_cache: dict[str, int | None] = {}
    _tiempo_cache: dict[tuple, int | None] = {}
    _asig_cache: dict[tuple, int | None] = {}

    try:
        reader = pd.read_csv(
            source_path,
            dtype=str,
            chunksize=CHUNK_SIZE,
            on_bad_lines="skip",
        )

        for chunk in reader:
            chunk = chunk.fillna("")
            run_ctx.read += len(chunk)

            with transaction(conn) as cur:
                for _, row in chunk.iterrows():
                    row = row.to_dict()

                    # ── Lookups dimensionales ──────────────────────────────
                    rbd_raw = _str(row.get("rbd"))
                    if rbd_raw not in _estab_cache:
                        _estab_cache[rbd_raw] = get_establecimiento_id(cur, rbd_raw)
                    estab_id = _estab_cache[rbd_raw]
                    if estab_id is None:
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
                    if tiempo_id is None:
                        run_ctx.skipped += 1
                        continue

                    # dim_alumno (run viene como texto "12345678-9" o solo dígitos)
                    alumno_id = _alumno_from_run(cur, row)

                    # dim_asignatura — SIGE no trae cod_ense directo,
                    # pero podemos inferirlo desde grado
                    cod_ense = "11" if cod_grado <= 8 else "23"
                    a_key = (cod_ense, "")
                    if a_key not in _asig_cache:
                        _asig_cache[a_key] = get_or_create_asignatura(cur, cod_ense, None)
                    asig_id = _asig_cache[a_key]

                    # ── Notas (25 slots) ───────────────────────────────────
                    nota_vals = []
                    for nc in _NOTA_COLS:
                        nota_vals.append(_float(row.get(nc)))

                    # ── INSERT fact_calificaciones ─────────────────────────
                    letra    = _str(row.get("letra")) or "A"
                    n_orden  = _int(row.get("n_orden"))

                    cur.execute(
                        """
                        INSERT INTO gold.fact_calificaciones
                            (alumno_id, establecimiento_id, tiempo_id, asignatura_id,
                             letra, n_orden, fecha_acta,
                             nota_1,nota_2,nota_3,nota_4,nota_5,
                             nota_6,nota_7,nota_8,nota_9,nota_10,
                             nota_11,nota_12,nota_13,nota_14,nota_15,
                             nota_16,nota_17,nota_18,nota_19,nota_20,
                             nota_21,nota_22,nota_23,nota_24,nota_25,
                             promedio, prom_literario, asistencia_pct,
                             situacion_final, observaciones, _source_file)
                        VALUES
                            (%s,%s,%s,%s,
                             %s,%s,%s,
                             %s,%s,%s,%s,%s,
                             %s,%s,%s,%s,%s,
                             %s,%s,%s,%s,%s,
                             %s,%s,%s,%s,%s,
                             %s,%s,%s,%s,%s,
                             %s,%s,%s,%s,%s,%s)
                        ON CONFLICT ON CONSTRAINT uq_fact_calificaciones
                        DO UPDATE SET
                            promedio       = EXCLUDED.promedio,
                            asistencia_pct = EXCLUDED.asistencia_pct,
                            situacion_final = EXCLUDED.situacion_final,
                            _source_file   = EXCLUDED._source_file
                        """,
                        (
                            alumno_id, estab_id, tiempo_id, asig_id,
                            letra, n_orden,
                            _str(row.get("fecha_acta")),
                            *nota_vals,
                            _float(row.get("promedio")),
                            _str(row.get("prom_literario")),
                            _int(row.get("asistencia_pct")),
                            _str(row.get("situacion_final")),
                            _str(row.get("observaciones")),
                            _str(row.get("_source_file") or source_path.name),
                        ),
                    )
                    run_ctx.inserted += 1

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()

    logger.info("SIGE calificaciones: %d leídas, %d insertadas, %d saltadas",
                run_ctx.read, run_ctx.inserted, run_ctx.skipped)


def run_profesores(source_path: Path) -> None:
    """
    Enriquece dim_docente y dim_asignatura con datos de sige_profesores.csv.
    No crea una fact table propia — los docentes van a dim_docente y se
    relacionan con fact_docentes al cargar mineduc_cargos.csv.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        logger.warning("sige_profesores.csv no encontrado, saltando: %s", source_path)
        return

    conn = get_conn()
    run_ctx = EtlRun(conn, "load_sige_prof", source_path.name)
    run_ctx.start()

    _asig_cache: dict[tuple, int | None] = {}

    try:
        df = pd.read_csv(source_path, dtype=str).fillna("")

        with transaction(conn) as cur:
            for _, row in df.iterrows():
                row = row.to_dict()
                run_ctx.read += 1

                # Enriquecer dim_asignatura con subsectores reales de SIGE
                cod_ense  = "11" if _int(row.get("grado") or 0) <= 8 else "23"
                subsector = _str(row.get("subsector"))
                if subsector:
                    a_key = (cod_ense, subsector)
                    if a_key not in _asig_cache:
                        _asig_cache[a_key] = get_or_create_asignatura(cur, cod_ense, subsector)

                # Enriquecer dim_docente con habilitación
                mrun = _str(row.get("run_profesor"))
                if mrun:
                    mrun_int = _parse_run(mrun)
                    if mrun_int:
                        cur.execute(
                            """
                            INSERT INTO gold.dim_docente (mrun)
                            VALUES (%s)
                            ON CONFLICT (mrun) DO NOTHING
                            """,
                            (mrun_int,),
                        )
                        run_ctx.inserted += 1

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()


def _alumno_from_run(cur, row: dict) -> int | None:
    """
    SIGE trae el alumno como 'run' (texto, p.ej. '12345678-9').
    Convierte a MRUN numérico y hace upsert en dim_alumno.
    """
    run_raw = _str(row.get("run"))
    if not run_raw:
        return None
    mrun = _parse_run(run_raw)
    if not mrun:
        return None

    # Construir un dict compatible con get_or_create_alumno
    proxy = {
        "mrun": mrun,
        "gen_alu": row.get("sexo"),       # SIGE usa 'sexo' en lugar de gen_alu
        "fec_nac_alu": row.get("fec_nac"),
    }
    from .db import get_or_create_alumno
    return get_or_create_alumno(cur, proxy)


def _parse_run(run_str: str) -> int | None:
    """'12345678-9' | '12345678' → 12345678 (sin dígito verificador)."""
    clean = run_str.replace(".", "").strip()
    if "-" in clean:
        clean = clean.split("-")[0]
    try:
        return int(clean)
    except ValueError:
        return None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    processed_dir = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else Path("data/sige/processed")
    run_calificaciones(processed_dir / "sige_calificaciones.csv")
    run_profesores(processed_dir / "sige_profesores.csv")
