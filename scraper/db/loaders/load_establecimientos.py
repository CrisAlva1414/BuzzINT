"""
loaders/load_establecimientos.py
─────────────────────────────────────────────────────────────
Lee data/mineduc/processed/mineduc_establecimientos.csv y
puebla:
  - dim_territorio
  - dim_establecimiento  (SCD Type-1)
  - fact_establecimiento_anual
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .db import (
    EtlRun, get_conn, transaction,
    upsert_territorio, upsert_establecimiento,
    _int, _float, _rbd, _str,
)

logger = logging.getLogger(__name__)

LOADER_NAME = "load_establecimientos"
CHUNK_SIZE  = 10_000

# Columnas ENS que van a fact_establecimiento_anual
_ENS_COLS = [f"ens_{i:02d}" for i in range(1, 12)]


def run(source_path: Path) -> None:
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn = get_conn()
    run_ctx = EtlRun(conn, LOADER_NAME, source_path.name)
    run_ctx.start()

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

                    # ── 1. Territorio ──────────────────────────────────────
                    ter_id = upsert_territorio(cur, row)

                    # ── 2. Establecimiento ─────────────────────────────────
                    estab_id = upsert_establecimiento(cur, row, ter_id)
                    if estab_id is None:
                        run_ctx.skipped += 1
                        continue

                    # ── 3. fact_establecimiento_anual ──────────────────────
                    agno = _int(row.get("agno"))
                    if not agno:
                        run_ctx.skipped += 1
                        continue

                    ens_vals = {col: _int(row.get(col)) for col in _ENS_COLS}

                    lat = _float(row.get("latitud"))
                    lon = _float(row.get("longitud"))

                    if lat is not None and not (-90 <= lat <= 90):
                        lat = None
                    if lon is not None and not (-180 <= lon <= 180):
                        lon = None

                    cur.execute(
                        """
                        INSERT INTO gold.fact_establecimiento_anual
                            (establecimiento_id, agno, mat_total, latitud, longitud,
                             convenio_pie, pace, pago_matricula, pago_mensual,
                             ens_01,ens_02,ens_03,ens_04,ens_05,ens_06,
                             ens_07,ens_08,ens_09,ens_10,ens_11,
                             _source_file)
                        VALUES
                            (%(estab_id)s, %(agno)s, %(mat_total)s,
                             %(lat)s, %(lon)s,
                             %(conv_pie)s, %(pace)s,
                             %(p_mat)s, %(p_men)s,
                             %(e01)s,%(e02)s,%(e03)s,%(e04)s,%(e05)s,%(e06)s,
                             %(e07)s,%(e08)s,%(e09)s,%(e10)s,%(e11)s,
                             %(src)s)
                        ON CONFLICT (establecimiento_id, agno) DO UPDATE SET
                            mat_total     = EXCLUDED.mat_total,
                            latitud       = EXCLUDED.latitud,
                            longitud      = EXCLUDED.longitud,
                            convenio_pie  = EXCLUDED.convenio_pie,
                            pace          = EXCLUDED.pace,
                            pago_matricula = EXCLUDED.pago_matricula,
                            pago_mensual  = EXCLUDED.pago_mensual,
                            ens_01=EXCLUDED.ens_01, ens_02=EXCLUDED.ens_02,
                            ens_03=EXCLUDED.ens_03, ens_04=EXCLUDED.ens_04,
                            ens_05=EXCLUDED.ens_05, ens_06=EXCLUDED.ens_06,
                            ens_07=EXCLUDED.ens_07, ens_08=EXCLUDED.ens_08,
                            ens_09=EXCLUDED.ens_09, ens_10=EXCLUDED.ens_10,
                            ens_11=EXCLUDED.ens_11,
                            _source_file = EXCLUDED._source_file
                        """,
                        dict(
                            estab_id=estab_id,
                            agno=agno,
                            mat_total=_int(row.get("mat_total") or row.get("matricula")),
                            lat=lat,
                            lon=lon,
                            conv_pie=_int(row.get("convenio_pie")),
                            pace=_int(row.get("pace")),
                            p_mat=_float(row.get("pago_matricula")),
                            p_men=_float(row.get("pago_mensual")),
                            **{f"e{i:02d}": ens_vals.get(f"ens_{i:02d}") for i in range(1, 12)},
                            src=_str(row.get("_source_file")),
                        ),
                    )
                    run_ctx.inserted += 1

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()

    logger.info("Establecimientos: %d leídas, %d insertadas/actualizadas, %d saltadas",
                run_ctx.read, run_ctx.inserted, run_ctx.skipped)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    src = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else Path("data/mineduc/processed/mineduc_establecimientos.csv")
    run(src)
