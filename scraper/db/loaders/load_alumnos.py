"""
loaders/load_alumnos.py
─────────────────────────────────────────────────────────────
Lee data/mineduc/processed/mineduc_alumnos.csv y puebla:
  - dim_alumno
  - fact_matricula
Prerequisito: dim_establecimiento y dim_tiempo_escolar ya cargadas.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from loaders.db import (
    EtlRun, get_conn, transaction,
    get_or_create_alumno, get_establecimiento_id, get_tiempo_id,
    _int, _str,
)

logger = logging.getLogger(__name__)

LOADER_NAME = "load_alumnos"
CHUNK_SIZE  = 20_000


def run(source_path: Path) -> None:
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn = get_conn()
    run_ctx = EtlRun(conn, LOADER_NAME, source_path.name)
    run_ctx.start()

    # Cache en memoria para evitar lookups repetidos por RBD y tiempo
    _estab_cache: dict[str, int | None] = {}
    _tiempo_cache: dict[tuple, int | None] = {}

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

                    # ── RBD lookup ─────────────────────────────────────────
                    rbd_raw = _str(row.get("rbd"))
                    if rbd_raw not in _estab_cache:
                        _estab_cache[rbd_raw] = get_establecimiento_id(cur, rbd_raw)
                    estab_id = _estab_cache[rbd_raw]
                    if estab_id is None:
                        run_ctx.skipped += 1
                        continue

                    # ── Tiempo lookup ──────────────────────────────────────
                    agno      = _int(row.get("agno"))
                    cod_grado = _int(row.get("cod_grado") or row.get("grado"))
                    if not agno or not cod_grado:
                        run_ctx.skipped += 1
                        continue

                    t_key = (agno, cod_grado)
                    if t_key not in _tiempo_cache:
                        _tiempo_cache[t_key] = get_tiempo_id(cur, agno, cod_grado)
                    tiempo_id = _tiempo_cache[t_key]
                    if tiempo_id is None:
                        # año/grado fuera del rango seed → insertar dinámicamente
                        cur.execute(
                            """
                            INSERT INTO gold.dim_tiempo_escolar
                                (agno, cod_grado, grado_label, nivel, ciclo)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (agno, cod_grado) DO NOTHING
                            RETURNING tiempo_id
                            """,
                            (agno, cod_grado,
                             f"{cod_grado}° " + ("básico" if cod_grado <= 8 else "medio"),
                             "basico" if cod_grado <= 8 else "medio",
                             "primer_ciclo" if cod_grado <= 4
                                else "segundo_ciclo" if cod_grado <= 8 else "EM"),
                        )
                        r = cur.fetchone()
                        if r:
                            tiempo_id = r[0]
                        else:
                            tiempo_id = get_tiempo_id(cur, agno, cod_grado)
                        _tiempo_cache[t_key] = tiempo_id

                    if tiempo_id is None:
                        run_ctx.skipped += 1
                        continue

                    # ── dim_alumno ─────────────────────────────────────────
                    alumno_id = get_or_create_alumno(cur, row)
                    if alumno_id is None:
                        run_ctx.skipped += 1
                        continue

                    # ── fact_matricula ─────────────────────────────────────
                    cur.execute(
                        """
                        INSERT INTO gold.fact_matricula
                            (alumno_id, establecimiento_id, tiempo_id,
                             let_cur, cod_jor, criterio_sep,
                             prioritario_alu, preferente_alu, ben_sep,
                             _source_file)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (alumno_id, establecimiento_id, tiempo_id)
                        DO UPDATE SET
                            let_cur         = EXCLUDED.let_cur,
                            cod_jor         = EXCLUDED.cod_jor,
                            criterio_sep    = EXCLUDED.criterio_sep,
                            prioritario_alu = EXCLUDED.prioritario_alu,
                            preferente_alu  = EXCLUDED.preferente_alu,
                            ben_sep         = EXCLUDED.ben_sep,
                            _source_file    = EXCLUDED._source_file
                        """,
                        (
                            alumno_id, estab_id, tiempo_id,
                            _str(row.get("let_cur")),
                            _int(row.get("cod_jor")),
                            _int(row.get("criterio_sep")),
                            _int(row.get("prioritario_alu")),
                            _int(row.get("preferente_alu")),
                            _int(row.get("ben_sep")),
                            _str(row.get("_source_file")),
                        ),
                    )
                    run_ctx.inserted += 1

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()

    logger.info("Alumnos: %d leídas, %d insertadas, %d saltadas",
                run_ctx.read, run_ctx.inserted, run_ctx.skipped)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    src = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else Path("data/mineduc/processed/mineduc_alumnos.csv")
    run(src)
