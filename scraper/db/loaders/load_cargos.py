"""
loaders/load_cargos.py
─────────────────────────────────────────────────────────────
Lee data/mineduc/processed/mineduc_cargos.csv y puebla:
  - dim_docente
  - dim_asignatura   (upsert por cod_ense + subsector)
  - fact_docentes
Prerequisito: dim_establecimiento y dim_tiempo_escolar ya cargadas.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from loaders.db import (
    EtlRun, get_conn, transaction,
    get_or_create_docente, get_or_create_asignatura,
    get_establecimiento_id, get_tiempo_id,
    _int, _float, _str,
)

logger = logging.getLogger(__name__)

LOADER_NAME = "load_cargos"
CHUNK_SIZE  = 20_000


def run(source_path: Path) -> None:
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn = get_conn()
    run_ctx = EtlRun(conn, LOADER_NAME, source_path.name)
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

                    # ── RBD ────────────────────────────────────────────────
                    rbd_raw = _str(row.get("rbd"))
                    if rbd_raw not in _estab_cache:
                        _estab_cache[rbd_raw] = get_establecimiento_id(cur, rbd_raw)
                    estab_id = _estab_cache[rbd_raw]
                    if estab_id is None:
                        run_ctx.skipped += 1
                        continue

                    # ── Tiempo ─────────────────────────────────────────────
                    agno = _int(row.get("agno"))
                    # Los cargos no tienen cod_grado — usamos grado 0 como
                    # marcador de "nivel establecimiento" en la dimensión
                    if not agno:
                        run_ctx.skipped += 1
                        continue

                    t_key = (agno, 0)
                    if t_key not in _tiempo_cache:
                        # Insertar grado=0 = "nivel establecimiento"
                        cur.execute(
                            """
                            INSERT INTO gold.dim_tiempo_escolar
                                (agno, cod_grado, grado_label, nivel, ciclo)
                            VALUES (%s, 0, 'establecimiento', 'todos', 'todos')
                            ON CONFLICT (agno, cod_grado) DO NOTHING
                            RETURNING tiempo_id
                            """,
                            (agno,),
                        )
                        r = cur.fetchone()
                        if r:
                            _tiempo_cache[t_key] = r[0]
                        else:
                            _tiempo_cache[t_key] = get_tiempo_id(cur, agno, 0)
                    tiempo_id = _tiempo_cache[t_key]

                    # ── dim_docente ────────────────────────────────────────
                    docente_id = get_or_create_docente(cur, row)

                    # ── dim_asignatura ─────────────────────────────────────
                    cod_ense  = _str(row.get("cod_ense"))
                    subsector = _str(row.get("subsector") or row.get("nombre_asignatura"))
                    a_key = (cod_ense or "", subsector or "")
                    if a_key not in _asig_cache:
                        _asig_cache[a_key] = get_or_create_asignatura(cur, cod_ense, subsector)
                    asig_id = _asig_cache[a_key]

                    # ── fact_docentes ──────────────────────────────────────
                    cur.execute(
                        """
                        INSERT INTO gold.fact_docentes
                            (establecimiento_id, tiempo_id, docente_id,
                             asignatura_id, n_horas, tipo_cargo, cod_cargo,
                             jornada, _source_file)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            estab_id, tiempo_id, docente_id,
                            asig_id,
                            _float(row.get("n_horas") or row.get("horas")),
                            _str(row.get("tipo_cargo") or row.get("nom_cargo")),
                            _int(row.get("cod_cargo")),
                            _int(row.get("cod_jor") or row.get("jornada")),
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

    logger.info("Cargos: %d leídas, %d insertadas, %d saltadas",
                run_ctx.read, run_ctx.inserted, run_ctx.skipped)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    src = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else Path("data/mineduc/processed/mineduc_cargos.csv")
    run(src)
