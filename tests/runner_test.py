from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from scraper.db.loaders.db import get_conn
from scraper.db.loaders.load_alumnos import run as run_alumnos
from scraper.db.loaders.load_dev import load_establecimientos, load_cargos, load_simce
from scraper.db.loaders.load_sige import run_calificaciones, run_profesores


DEFAULTS = {
    "establecimientos": "data/mineduc/processed/mineduc_establecimientos.csv",
    "alumnos":          "data/mineduc/processed/mineduc_alumnos.csv",
    "cargos":           "data/mineduc/processed/mineduc_cargos.csv",
    "simce_rbd":        "data/simce/processed/simce__rbd.csv",
    "simce_comuna":     "data/simce/processed/simce__comuna.csv",
    "simce_region":     "data/simce/processed/simce__region.csv",
    "sige_cal":         "data/sige/processed/sige_calificaciones.csv",
    "sige_prof":        "data/sige/processed/sige_profesores.csv",
}

# Aviso de idempotencia por loader
IDEMPOTENCIA = {
    "establecimientos":  "✓ upsert seguro (ON CONFLICT rbd)",
    "alumnos":           "✓ upsert seguro (ON CONFLICT uq_fact_matricula)",
    "cargos":            "⚠ puede DUPLICAR filas en fact_docentes si re-ejecutas",
    "simce_rbd":         "✓ upsert seguro (ON CONFLICT estab+tiempo)",
    "simce_comuna":      "✓ upsert seguro (ON CONFLICT DO NOTHING)",
    "simce_region":      "✓ upsert seguro (ON CONFLICT DO NOTHING)",
    "sige_calificaciones": "✓ upsert seguro (ON CONFLICT uq_fact_calificaciones)",
    "sige_profesores":   "✓ INSERT ON CONFLICT DO NOTHING",
}


class _ProgressFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self._t0 = time.perf_counter()

    def reset(self):
        self._t0 = time.perf_counter()

    def filter(self, record):
        record.rel_s = f"+{time.perf_counter() - self._t0:6.1f}s"
        return True


_progress_filter = _ProgressFilter()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_progress_filter)
    fmt = "%(asctime)s %(rel_s)s [%(levelname)s] %(name)s — %(message)s"
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    logging.root.setLevel(level)
    logging.root.handlers = [handler]


def _banner(msg: str, char: str = "═") -> None:
    print(f"\n{char * 62}")
    print(f"  {msg}")
    print(f"{char * 62}")


def _section(name: str, path: Path) -> None:
    print(f"\n{'─' * 62}")
    print(f"  ▶  {name}")
    print(f"     {path}")
    idm = IDEMPOTENCIA.get(name, "")
    if idm:
        print(f"     {idm}")
    print(f"{'─' * 62}")
    _progress_filter.reset()


def _hms(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sc  = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {sc:02d}s"
    if m:
        return f"{m}m {sc:02d}s"
    return f"{sc}s ({seconds:.1f}s)"


def _check_db() -> bool:
    try:
        conn = get_conn(retries=3, delay=2.0)
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), pg_size_pretty(pg_database_size(current_database()))")
            db_name, db_size = cur.fetchone()
        conn.close()
        print(f"  ✓ PostgreSQL OK  — base: {db_name}  tamaño: {db_size}")
        return True
    except Exception as exc:
        print(f"  ✗ No se pudo conectar: {exc}")
        return False


def _run_loader(name: str, fn, path: Path, dry_run, results: dict) -> None:
    if not path.exists():
        logging.warning("[%s] archivo no encontrado — saltando", name)
        results[name] = {"status": "skipped", "reason": "file_not_found",
                         "path": str(path)}
        return

    size_mb = path.stat().st_size / 1_048_576
    _section(name, path)
    logging.info("[%s] tamaño archivo: %.1f MB", name, size_mb)

    t0 = time.perf_counter()
    try:
        if dry_run is None:
            # Loader no acepta dry_run (establecimientos, cargos)
            ret = fn(path)
        else:
            ret = fn(path, dry_run=dry_run)

        elapsed = time.perf_counter() - t0
        stats   = ret if isinstance(ret, dict) else {}
        results[name] = {"status": "ok", "elapsed_s": elapsed,
                         "size_mb": round(size_mb, 1), **stats}

        # Línea de resumen post-loader
        parts = [f"✓ {_hms(elapsed)}"]
        if "rows_read"     in stats: parts.append(f"leídas={stats['rows_read']:,}")
        if "rows_inserted" in stats: parts.append(f"insertadas={stats['rows_inserted']:,}")
        if "rows_skipped"  in stats: parts.append(f"saltadas={stats['rows_skipped']:,}")
        logging.info("[%s] %s", name, "  ".join(parts))

    except Exception as exc:
        elapsed = time.perf_counter() - t0
        results[name] = {"status": "error", "elapsed_s": elapsed, "error": str(exc)}
        logging.error("[%s] ✗ %s  ERROR: %s", name, _hms(elapsed), exc)


def run_establecimientos(paths, dry_run, results):
    p = Path(paths.get("establecimientos", DEFAULTS["establecimientos"]))
    _run_loader("establecimientos", load_establecimientos, p, None, results)


def run_alumnos_loader(paths, dry_run, results):
    p = Path(paths.get("alumnos", DEFAULTS["alumnos"]))
    _run_loader("alumnos", run_alumnos, p, dry_run, results)


def run_cargos_loader(paths, dry_run, results):
    p = Path(paths.get("cargos", DEFAULTS["cargos"]))
    _run_loader("cargos", load_cargos, p, None, results)


def run_simce_loaders(paths, dry_run, results):
    for gran in ("rbd", "comuna", "region"):
        key = f"simce_{gran}"
        p   = Path(paths.get(key, DEFAULTS[key]))
        _run_loader(key, lambda x, g=gran: load_simce(x, granularity=g),
                    p, None, results)


def run_sige_loaders(paths, dry_run, results):
    p_cal  = Path(paths.get("sige_cal",  DEFAULTS["sige_cal"]))
    p_prof = Path(paths.get("sige_prof", DEFAULTS["sige_prof"]))
    _run_loader("sige_calificaciones", run_calificaciones, p_cal,  dry_run, results)
    _run_loader("sige_profesores",     run_profesores,     p_prof, dry_run, results)


_ALL_LOADERS = ["establecimientos", "alumnos", "cargos", "simce", "sige"]


_LOADER_FNS = {
    "establecimientos": run_establecimientos,
    "alumnos":          run_alumnos_loader,
    "cargos":           run_cargos_loader,
    "simce":            run_simce_loaders,
    "sige":             run_sige_loaders,
}

def main() -> None:
    parser = argparse.ArgumentParser(
        description="BuzzINT — Runner de prueba para todos los loaders",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python runner_test.py                           # todos los loaders, verbose activado
  python runner_test.py --only alumnos simce      # solo esos dos
  python runner_test.py --dry-run                 # sin escribir en DB
  python runner_test.py --only cargos --quiet     # sin logs de debug

Sobre idempotencia (re-ejecuciones):
  La mayoría de loaders son seguros para re-ejecutar (upsert).
  EXCEPCIÓN: cargos puede duplicar filas en fact_docentes.
  Si necesitas re-cargar cargos limpiamente:
    TRUNCATE gold.fact_docentes;
    python runner_test.py --only cargos
        """,
    )

    parser.add_argument("--only",    nargs="+", choices=_ALL_LOADERS,
                        metavar="LOADER", help="Correr solo estos loaders")
    parser.add_argument("--dry-run", action="store_true",
                        help="No escribir en la base de datos")
    parser.add_argument("--quiet",   action="store_true",
                        help="Solo INFO, sin DEBUG (por defecto se activa verbose)")

    for key, default in DEFAULTS.items():
        parser.add_argument(f"--{key.replace('_', '-')}", default=default, metavar="FILE")

    args = parser.parse_args()

    # Verbose por defecto — --quiet lo desactiva
    _setup_logging(verbose=not args.quiet)

    paths = {key: getattr(args, key.replace("-", "_"), default)
             for key, default in DEFAULTS.items()}

    _banner("BuzzINT — Loader Test Runner")
    print(f"  Inicio       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Raíz proyecto: {PROJECT_ROOT}")
    print(f"  Modo         : {'DRY-RUN (sin escrituras)' if args.dry_run else 'ESCRITURA REAL'}")
    print(f"  Verbosidad   : {'INFO (--quiet)' if args.quiet else 'DEBUG (completo)'}")

    print("\n  Verificando PostgreSQL...")
    if not _check_db():
        print("  Revisa variables PG_HOST / PG_PORT / PG_DB en tu .env o Docker.")
        sys.exit(1)

    selected = args.only or _ALL_LOADERS
    if "cargos" in selected and not args.dry_run:
        print("\n  ⚠  AVISO: 'cargos' puede duplicar filas en fact_docentes.")
        print("     Si es una re-ejecución, considera: TRUNCATE gold.fact_docentes;")

    results:  dict  = {}
    total_t0: float = time.perf_counter()

    for name in selected:
        _LOADER_FNS[name](paths, args.dry_run, results)

    total_elapsed = time.perf_counter() - total_t0

    _banner("RESUMEN FINAL")

    ok_list      = [(k, v) for k, v in results.items() if v["status"] == "ok"]
    skipped_list = [(k, v) for k, v in results.items() if v["status"] == "skipped"]
    error_list   = [(k, v) for k, v in results.items() if v["status"] == "error"]

    col_w = max((len(k) for k in results), default=10) + 2

    if ok_list:
        print(f"\n  {'LOADER':<{col_w}}  {'TIEMPO':>10}  {'LEÍDAS':>12}  {'INSERTADAS':>12}  MB")
        print(f"  {'─'*col_w}  {'─'*10}  {'─'*12}  {'─'*12}  ─────")
        for k, v in ok_list:
            t   = _hms(v["elapsed_s"])
            r   = f"{v['rows_read']:,}"     if "rows_read"     in v else "—"
            ins = f"{v['rows_inserted']:,}" if "rows_inserted" in v else "—"
            mb  = f"{v.get('size_mb', 0):.1f}"
            print(f"  ✓ {k:<{col_w}}{t:>10}  {r:>12}  {ins:>12}  {mb}")

    for k, v in skipped_list:
        print(f"  – {k:<{col_w}} (archivo no encontrado: {v.get('path', '')})")

    for k, v in error_list:
        print(f"  ✗ {k:<{col_w}} ERROR: {v.get('error', '')[:80]}")

    print(f"\n  Total loaders : {len(results)}  (✓ {len(ok_list)}  – {len(skipped_list)}  ✗ {len(error_list)})")
    print(f"  Tiempo total  : {_hms(total_elapsed)}")
    print(f"  Fin           : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 62}\n")

    sys.exit(0 if not error_list else 1)


if __name__ == "__main__":
    main()