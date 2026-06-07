"""
loaders/load_simce.py
─────────────────────────────────────────────────────────────
Lee data/simce/processed/simce__rbd.csv y puebla fact_simce.
Los archivos __comuna y __region se cargan en stg_simce_*.
Prerequisito: dim_establecimiento y dim_tiempo_escolar ya cargadas.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from loaders.db import (
    EtlRun, get_conn, transaction,
    get_establecimiento_id, get_tiempo_id,
    upsert_territorio, upsert_establecimiento,
    _int, _float, _str,
)

logger = logging.getLogger(__name__)

LOADER_NAME = "load_simce"
CHUNK_SIZE  = 5_000

# Mapeo flexible: nombres de columnas en distintos años de SIMCE → nombre canónico
_COL_MAP: dict[str, str] = {
    # puntajes matemática
    "prom_mate4b_rbd":   "ptje_mat",
    "prom_mate8b_rbd":   "ptje_mat",
    "prom_mate2m_rbd":   "ptje_mat",
    "prom_matematica":   "ptje_mat",
    "ptje_mate_rbd":     "ptje_mat",
    # puntajes lectura
    "prom_lect4b_rbd":   "ptje_lect",
    "prom_lect8b_rbd":   "ptje_lect",
    "prom_lect2m_rbd":   "ptje_lect",
    "prom_lectura":      "ptje_lect",
    "ptje_lect_rbd":     "ptje_lect",
    # puntajes ciencias
    "prom_cien4b_rbd":   "ptje_cie",
    "prom_cien6b_rbd":   "ptje_cie",
    "prom_ciencias":     "ptje_cie",
    "ptje_cie_rbd":      "ptje_cie",
    # puntajes historia
    "prom_hist4b_rbd":   "ptje_his",
    "prom_historia":     "ptje_his",
    "ptje_his_rbd":      "ptje_his",
    # puntajes inglés
    "prom_ingl4b_rbd":   "ptje_ing",
    "prom_ingles":       "ptje_ing",
    # n evaluados
    "alumnos_eval_mate4b": "n_eval_mat",
    "alumnos_eval_mate8b": "n_eval_mat",
    "alumnos_eval":        "n_eval_mat",
    "n_alumnos_rbd":       "n_eval_mat",
    # grupo socioeconómico
    "gse_rbd":             "gse_predominante",
    "grupo_gse":           "gse_predominante",
    "cod_gse":             "gse_predominante",
    "grupo_socioeconomico": "gse_predominante",
    "cod_grupo":           "cod_grupo",
}

# Grado inferido desde el nombre del archivo/columna si no viene en el CSV
_GRADO_FROM_SUFFIX: dict[str, int] = {
    "2b": 2, "4b": 4, "6b": 6, "8b": 8,
    "2m": 10, "4m": 12,
}


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Renombra columnas según _COL_MAP (sin romper las que no están en el mapa)."""
    df.columns = [c.strip().lower() for c in df.columns]
    return df.rename(columns={k: v for k, v in _COL_MAP.items() if k in df.columns})


def _infer_grado(col_names: list[str], filename: str) -> int | None:
    """Intenta inferir el grado desde nombres de columnas o nombre de archivo."""
    for name in col_names + [filename.lower()]:
        for suffix, grado in _GRADO_FROM_SUFFIX.items():
            if suffix in name:
                return grado
    return None


def run(source_path: Path, granularity: str = "rbd") -> None:
    """
    granularity: 'rbd' (default) | 'comuna' | 'region'
    Para 'rbd' → fact_simce
    Para otros → stg_simce_* (staging)
    """
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn = get_conn()
    run_ctx = EtlRun(conn, LOADER_NAME, source_path.name)
    run_ctx.start()

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
            chunk = _normalize_cols(chunk)
            run_ctx.read += len(chunk)

            # Inferir grado desde columnas (si no viene en el CSV)
            grado_inferred = _infer_grado(list(chunk.columns), source_path.name)

            with transaction(conn) as cur:
                for _, row in chunk.iterrows():
                    row = row.to_dict()

                    agno = _int(row.get("agno"))
                    if not agno:
                        run_ctx.skipped += 1
                        continue

                    # cod_grado — puede venir como '4b', '8b', int, o inferirse
                    grado_raw = _str(row.get("grado") or row.get("cod_grado"))
                    cod_grado = _parse_grado(grado_raw) or grado_inferred
                    if not cod_grado:
                        run_ctx.skipped += 1
                        continue

                    if granularity == "rbd":
                        _load_rbd_row(cur, row, agno, cod_grado,
                                      _estab_cache, _tiempo_cache, run_ctx)
                    else:
                        _load_agg_row(cur, row, agno, cod_grado,
                                      granularity, _tiempo_cache, run_ctx)

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()

    logger.info("SIMCE (%s): %d leídas, %d insertadas, %d saltadas",
                granularity, run_ctx.read, run_ctx.inserted, run_ctx.skipped)


def _load_rbd_row(
    cur, row: dict, agno: int, cod_grado: int,
    estab_cache: dict, tiempo_cache: dict, ctx,
) -> None:
    # ── RBD ──────────────────────────────────────────────────────
    rbd_raw = _str(row.get("rbd"))
    if rbd_raw not in estab_cache:
        estab_cache[rbd_raw] = get_establecimiento_id(cur, rbd_raw)
    estab_id = estab_cache[rbd_raw]

    if estab_id is None:
        # El EE puede no estar en dim_establecimiento todavía
        # → intentar crearlo con la info mínima del CSV SIMCE
        ter_id = upsert_territorio(cur, row)
        estab_id = upsert_establecimiento(cur, row, ter_id)
        estab_cache[rbd_raw] = estab_id

    if estab_id is None:
        ctx.skipped += 1
        return

    # ── Tiempo ────────────────────────────────────────────────────
    t_key = (agno, cod_grado)
    if t_key not in tiempo_cache:
        tiempo_cache[t_key] = get_tiempo_id(cur, agno, cod_grado)
    tiempo_id = tiempo_cache[t_key]
    if tiempo_id is None:
        ctx.skipped += 1
        return

    # ── fact_simce ────────────────────────────────────────────────
    cur.execute(
        """
        INSERT INTO gold.fact_simce
            (establecimiento_id, tiempo_id,
             ptje_mat, ptje_lect, ptje_cie, ptje_his, ptje_ing,
             n_eval_mat, n_eval_lect, n_eval_cie,
             gse_predominante, cod_grupo, _source_rar)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (establecimiento_id, tiempo_id) DO UPDATE SET
            ptje_mat  = COALESCE(EXCLUDED.ptje_mat,  gold.fact_simce.ptje_mat),
            ptje_lect = COALESCE(EXCLUDED.ptje_lect, gold.fact_simce.ptje_lect),
            ptje_cie  = COALESCE(EXCLUDED.ptje_cie,  gold.fact_simce.ptje_cie),
            ptje_his  = COALESCE(EXCLUDED.ptje_his,  gold.fact_simce.ptje_his),
            ptje_ing  = COALESCE(EXCLUDED.ptje_ing,  gold.fact_simce.ptje_ing),
            n_eval_mat  = COALESCE(EXCLUDED.n_eval_mat,  gold.fact_simce.n_eval_mat),
            n_eval_lect = COALESCE(EXCLUDED.n_eval_lect, gold.fact_simce.n_eval_lect),
            n_eval_cie  = COALESCE(EXCLUDED.n_eval_cie,  gold.fact_simce.n_eval_cie),
            gse_predominante = COALESCE(EXCLUDED.gse_predominante, gold.fact_simce.gse_predominante),
            cod_grupo        = COALESCE(EXCLUDED.cod_grupo, gold.fact_simce.cod_grupo)
        """,
        (
            estab_id, tiempo_id,
            _float(row.get("ptje_mat")),
            _float(row.get("ptje_lect")),
            _float(row.get("ptje_cie")),
            _float(row.get("ptje_his")),
            _float(row.get("ptje_ing")),
            _int(row.get("n_eval_mat")),
            _int(row.get("n_eval_lect")),
            _int(row.get("n_eval_cie")),
            _str(row.get("gse_predominante")),
            _str(row.get("cod_grupo")),
            _str(row.get("_source_rar") or row.get("_source_file")),
        ),
    )
    ctx.inserted += 1


def _load_agg_row(cur, row, agno, cod_grado, granularity, tiempo_cache, ctx):
    """Carga en stg_simce_comuna o stg_simce_region (sin FK dimensional)."""
    t_key = (agno, cod_grado)
    if t_key not in tiempo_cache:
        tiempo_cache[t_key] = get_tiempo_id(cur, agno, cod_grado)
    tiempo_id = tiempo_cache[t_key]
    if tiempo_id is None:
        ctx.skipped += 1
        return

    table = f"gold.stg_simce_{granularity}"
    geo_col = "cod_com" if granularity == "comuna" else "cod_reg"
    geo_val = _str(row.get(geo_col) or row.get(f"cod_{granularity}"))

    cur.execute(
        f"""
        INSERT INTO {table}
            (tiempo_id, {geo_col},
             ptje_mat, ptje_lect, ptje_cie, n_eval_mat, gse_predominante)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING
        """,
        (
            tiempo_id, geo_val,
            _float(row.get("ptje_mat")),
            _float(row.get("ptje_lect")),
            _float(row.get("ptje_cie")),
            _int(row.get("n_eval_mat")),
            _str(row.get("gse_predominante")),
        ),
    )
    ctx.inserted += 1


def _parse_grado(raw: str | None) -> int | None:
    """'4b' → 4, '2m' → 10, '4' → 4, None → None."""
    if not raw:
        return None
    raw = raw.strip().lower()
    for suffix, g in _GRADO_FROM_SUFFIX.items():
        if raw == suffix:
            return g
    try:
        return int(raw)
    except ValueError:
        return None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    src = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else Path("data/simce/processed/simce__rbd.csv")
    gran = sys.argv[2] if len(sys.argv) > 2 else "rbd"
    run(src, gran)
