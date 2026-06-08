import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

try:
    from .db import (
        CHUNK_SIZE, EtlRun, batch_upsert, get_conn, transaction,
        upsert_territorio, upsert_establecimiento,
        get_establecimiento_id, get_tiempo_id,
        get_or_create_docente, get_or_create_asignatura,
        _int, _float, _str, _rbd,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scraper.db.loaders.db import (
        CHUNK_SIZE, EtlRun, batch_upsert, get_conn, transaction,
        upsert_territorio, upsert_establecimiento,
        get_establecimiento_id, get_tiempo_id,
        get_or_create_docente, get_or_create_asignatura,
        _int, _float, _str, _rbd,
    )

logger = logging.getLogger(__name__)

_ENS_COLS = [f"ens_{i:02d}" for i in range(1, 12)]


_INSERT_ESTAB_ANUAL = """
INSERT INTO gold.fact_establecimiento_anual
    (establecimiento_id, agno, mat_total, latitud, longitud,
     convenio_pie, pace, pago_matricula, pago_mensual,
     ens_01,ens_02,ens_03,ens_04,ens_05,ens_06,
     ens_07,ens_08,ens_09,ens_10,ens_11, _source_file)
VALUES %s
ON CONFLICT (establecimiento_id, agno) DO UPDATE SET
    mat_total     = EXCLUDED.mat_total,
    latitud       = EXCLUDED.latitud,
    longitud      = EXCLUDED.longitud,
    convenio_pie  = EXCLUDED.convenio_pie,
    pace          = EXCLUDED.pace,
    pago_matricula = EXCLUDED.pago_matricula,
    pago_mensual  = EXCLUDED.pago_mensual,
    ens_01=EXCLUDED.ens_01, ens_02=EXCLUDED.ens_02, ens_03=EXCLUDED.ens_03,
    ens_04=EXCLUDED.ens_04, ens_05=EXCLUDED.ens_05, ens_06=EXCLUDED.ens_06,
    ens_07=EXCLUDED.ens_07, ens_08=EXCLUDED.ens_08, ens_09=EXCLUDED.ens_09,
    ens_10=EXCLUDED.ens_10, ens_11=EXCLUDED.ens_11,
    _source_file  = EXCLUDED._source_file
"""

_INSERT_DOCENTES = """
INSERT INTO gold.fact_docentes
    (establecimiento_id, tiempo_id, docente_id, asignatura_id,
     n_horas, tipo_cargo, cod_cargo, jornada, _source_file)
VALUES %s
"""

_INSERT_SIMCE = """
INSERT INTO gold.fact_simce
    (establecimiento_id, tiempo_id,
     ptje_mat, ptje_lect, ptje_cie, ptje_his, ptje_ing,
     n_eval_mat, n_eval_lect, n_eval_cie,
     gse_predominante, cod_grupo, _source_rar)
VALUES %s
ON CONFLICT (establecimiento_id, tiempo_id) DO UPDATE SET
    ptje_mat  = COALESCE(EXCLUDED.ptje_mat,  gold.fact_simce.ptje_mat),
    ptje_lect = COALESCE(EXCLUDED.ptje_lect, gold.fact_simce.ptje_lect),
    ptje_cie  = COALESCE(EXCLUDED.ptje_cie,  gold.fact_simce.ptje_cie),
    ptje_his  = COALESCE(EXCLUDED.ptje_his,  gold.fact_simce.ptje_his),
    ptje_ing  = COALESCE(EXCLUDED.ptje_ing,  gold.fact_simce.ptje_ing),
    n_eval_mat  = COALESCE(EXCLUDED.n_eval_mat,  gold.fact_simce.n_eval_mat),
    n_eval_lect = COALESCE(EXCLUDED.n_eval_lect, gold.fact_simce.n_eval_lect),
    gse_predominante = COALESCE(EXCLUDED.gse_predominante, gold.fact_simce.gse_predominante)
"""

_SIMCE_COL_MAP = {
    "prom_mate4b_rbd": "ptje_mat", "prom_mate8b_rbd": "ptje_mat",
    "prom_mate2m_rbd": "ptje_mat", "prom_matematica":  "ptje_mat",
    "prom_lect4b_rbd": "ptje_lect","prom_lect8b_rbd": "ptje_lect",
    "prom_lectura":    "ptje_lect",
    "prom_cien4b_rbd": "ptje_cie", "prom_ciencias":    "ptje_cie",
    "prom_hist4b_rbd": "ptje_his", "prom_historia":    "ptje_his",
    "prom_ingl4b_rbd": "ptje_ing", "prom_ingles":      "ptje_ing",
    "alumnos_eval_mate4b": "n_eval_mat", "n_alumnos_rbd": "n_eval_mat",
    "gse_rbd":         "gse_predominante", "grupo_socioeconomico": "gse_predominante",
}

_GRADO_SUFIJOS = {"2b": 2, "4b": 4, "6b": 6, "8b": 8, "2m": 10, "4m": 12}

# Staging sin FK para granularidades agregadas — la columna geo varía por tabla
_INSERT_SIMCE_AGG = {
    "comuna": "INSERT INTO gold.stg_simce_comuna (tiempo_id, cod_com, ptje_mat, ptje_lect, ptje_cie, n_eval_mat, gse_predominante) VALUES %s ON CONFLICT DO NOTHING",
    "region": "INSERT INTO gold.stg_simce_region (tiempo_id, cod_reg, ptje_mat, ptje_lect, ptje_cie, n_eval_mat, gse_predominante) VALUES %s ON CONFLICT DO NOTHING",
}


def load_establecimientos(source_path: Path) -> None:
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn    = get_conn()
    run_ctx = EtlRun(conn, "load_establecimientos", source_path.name)
    run_ctx.start()

    try:
        reader = pd.read_csv(source_path, dtype=str, chunksize=CHUNK_SIZE, on_bad_lines="skip")

        for chunk in reader:
            chunk = chunk.fillna("")
            run_ctx.read += len(chunk)
            rows_batch: list[tuple] = []

            with transaction(conn) as cur:
                for _, row in chunk.iterrows():
                    r = row.to_dict()

                    # Dims (volumen bajo — upsert individual OK)
                    ter_id   = upsert_territorio(cur, r)
                    estab_id = upsert_establecimiento(cur, r, ter_id)
                    if not estab_id:
                        run_ctx.skipped += 1
                        continue

                    agno = _int(r.get("agno"))
                    if not agno:
                        run_ctx.skipped += 1
                        continue

                    lat = _float(r.get("latitud"))
                    lon = _float(r.get("longitud"))
                    if lat is not None and not (-90 <= lat <= 90):
                        lat = None
                    if lon is not None and not (-180 <= lon <= 180):
                        lon = None

                    rows_batch.append((
                        estab_id, agno,
                        _int(r.get("mat_total") or r.get("matricula")),
                        lat, lon,
                        _int(r.get("convenio_pie")),
                        _int(r.get("pace")),
                        _float(r.get("pago_matricula")),
                        _float(r.get("pago_mensual")),
                        *[_int(r.get(f"ens_{i:02d}")) for i in range(1, 12)],
                        _str(r.get("_source_file")),
                    ))

                if rows_batch:
                    n = batch_upsert(cur, _INSERT_ESTAB_ANUAL, rows_batch)
                    run_ctx.inserted += n

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()

    logger.info("Establecimientos: %d leídas, %d insertadas, %d saltadas",
                run_ctx.read, run_ctx.inserted, run_ctx.skipped)


def load_cargos(source_path: Path) -> None:
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn    = get_conn()
    run_ctx = EtlRun(conn, "load_cargos", source_path.name)
    run_ctx.start()

    _estab_cache: dict[str, int | None] = {}
    _asig_cache:  dict[tuple, int | None] = {}

    try:
        reader = pd.read_csv(source_path, dtype=str, chunksize=CHUNK_SIZE, on_bad_lines="skip")

        for chunk in reader:
            chunk = chunk.fillna("")
            run_ctx.read += len(chunk)
            rows_batch: list[tuple] = []

            with transaction(conn) as cur:
                for _, row in chunk.iterrows():
                    r = row.to_dict()

                    rbd_raw = _str(r.get("rbd"))
                    if rbd_raw not in _estab_cache:
                        _estab_cache[rbd_raw] = get_establecimiento_id(cur, rbd_raw)
                    estab_id = _estab_cache[rbd_raw]
                    if not estab_id:
                        run_ctx.skipped += 1
                        continue

                    agno = _int(r.get("agno"))
                    if not agno:
                        run_ctx.skipped += 1
                        continue

                    # Cargos no tienen grado → tiempo_id es NULL (patch 03)
                    docente_id = get_or_create_docente(cur, r)

                    cod_ense  = _str(r.get("cod_ense"))
                    subsector = _str(r.get("subsector") or r.get("nombre_asignatura"))
                    a_key     = (cod_ense or "", subsector or "")
                    if a_key not in _asig_cache:
                        _asig_cache[a_key] = get_or_create_asignatura(cur, cod_ense, subsector)

                    rows_batch.append((
                        estab_id,
                        None,   # tiempo_id NULL para cargos (no tienen grado)
                        docente_id,
                        _asig_cache[a_key],
                        _float(r.get("n_horas") or r.get("horas")),
                        _str(r.get("tipo_cargo") or r.get("nom_cargo")),
                        _int(r.get("cod_cargo")),
                        _int(r.get("cod_jor") or r.get("jornada")),
                        _str(r.get("_source_file")),
                    ))

                if rows_batch:
                    n = batch_upsert(cur, _INSERT_DOCENTES, rows_batch)
                    run_ctx.inserted += n

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()

    logger.info("Cargos: %d leídas, %d insertadas, %d saltadas",
                run_ctx.read, run_ctx.inserted, run_ctx.skipped)


def _parse_grado(raw: str | None) -> int | None:
    if not raw:
        return None
    raw = raw.strip().lower()
    if raw in _GRADO_SUFIJOS:
        return _GRADO_SUFIJOS[raw]
    try:
        return int(raw)
    except ValueError:
        return None


def _infer_grado(col_names: list[str], filename: str) -> int | None:
    for name in col_names + [filename.lower()]:
        for suf, g in _GRADO_SUFIJOS.items():
            if suf in name:
                return g
    return None


def load_simce(source_path: Path, granularity: str = "rbd") -> None:
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    conn    = get_conn()
    run_ctx = EtlRun(conn, "load_simce", source_path.name)
    run_ctx.start()

    _estab_cache: dict[str, int | None] = {}
    _tiempo_cache: dict[tuple, int | None] = {}

    try:
        reader = pd.read_csv(source_path, dtype=str, chunksize=CHUNK_SIZE, on_bad_lines="skip")

        for chunk in reader:
            chunk = chunk.fillna("")
            # Renombrar columnas según mapa de aliases SIMCE
            chunk.columns = [c.strip().lower() for c in chunk.columns]
            chunk.rename(columns={k: v for k, v in _SIMCE_COL_MAP.items() if k in chunk.columns}, inplace=True)
            run_ctx.read += len(chunk)

            grado_inferred = _infer_grado(list(chunk.columns), source_path.name)
            rows_batch: list[tuple] = []

            with transaction(conn) as cur:
                for _, row in chunk.iterrows():
                    r = row.to_dict()

                    agno = _int(r.get("agno"))
                    grado_raw = _str(r.get("grado") or r.get("cod_grado"))
                    cod_grado = _parse_grado(grado_raw) or grado_inferred
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

                    if granularity != "rbd":
                        rows_batch.append((
                            tiempo_id,
                            _str(r.get("cod_com") or r.get("cod_reg")),
                            _float(r.get("ptje_mat")),
                            _float(r.get("ptje_lect")),
                            _float(r.get("ptje_cie")),
                            _int(r.get("n_eval_mat")),
                            _str(r.get("gse_predominante")),
                        ))
                        continue

                    rbd_raw = _str(r.get("rbd"))
                    if rbd_raw not in _estab_cache:
                        _estab_cache[rbd_raw] = get_establecimiento_id(cur, rbd_raw)
                    estab_id = _estab_cache[rbd_raw]
                    if not estab_id:
                        # Intentar crear establecimiento mínimo desde los datos SIMCE
                        ter_id   = upsert_territorio(cur, r)
                        estab_id = upsert_establecimiento(cur, r, ter_id)
                        _estab_cache[rbd_raw] = estab_id
                    if not estab_id:
                        run_ctx.skipped += 1
                        continue

                    rows_batch.append((
                        estab_id, tiempo_id,
                        _float(r.get("ptje_mat")),
                        _float(r.get("ptje_lect")),
                        _float(r.get("ptje_cie")),
                        _float(r.get("ptje_his")),
                        _float(r.get("ptje_ing")),
                        _int(r.get("n_eval_mat")),
                        _int(r.get("n_eval_lect")),
                        _int(r.get("n_eval_cie")),
                        _str(r.get("gse_predominante")),
                        _str(r.get("cod_grupo")),
                        _str(r.get("_source_rar") or r.get("_source_file")),
                    ))

                if rows_batch:
                    if granularity == "rbd":
                        n = batch_upsert(cur, _INSERT_SIMCE, rows_batch)
                    else:
                        sql = _INSERT_SIMCE_AGG[granularity]
                        n   = batch_upsert(cur, sql, rows_batch)
                    run_ctx.inserted += n

        run_ctx.finish()

    except Exception as exc:
        run_ctx.fail(str(exc))
        raise
    finally:
        conn.close()

    logger.info("SIMCE (%s): %d leídas, %d insertadas, %d saltadas",
                granularity, run_ctx.read, run_ctx.inserted, run_ctx.skipped)


def main() -> None:
    parser = argparse.ArgumentParser(description="Loaders ETL para PC de desarrollo")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_alumnos = sub.add_parser("alumnos")
    p_alumnos.add_argument("--input",   default="data/mineduc/processed/mineduc_alumnos.csv")
    p_alumnos.add_argument("--dry-run", action="store_true")

    p_estab = sub.add_parser("establecimientos")
    p_estab.add_argument("--input", default="data/mineduc/processed/mineduc_establecimientos.csv")

    p_cargos = sub.add_parser("cargos")
    p_cargos.add_argument("--input", default="data/mineduc/processed/mineduc_cargos.csv")

    p_simce = sub.add_parser("simce")
    p_simce.add_argument("--input", default="data/simce/processed/simce__rbd.csv")
    p_simce.add_argument("--gran",  default="rbd", choices=["rbd", "comuna", "region"])

    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.cmd == "alumnos":
        from .load_alumnos import run as run_alumnos
        run_alumnos(Path(args.input), dry_run=getattr(args, "dry_run", False))
    elif args.cmd == "establecimientos":
        load_establecimientos(Path(args.input))
    elif args.cmd == "cargos":
        load_cargos(Path(args.input))
    elif args.cmd == "simce":
        load_simce(Path(args.input), args.gran)


if __name__ == "__main__":
    main()