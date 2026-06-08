#!/usr/bin/env python3
"""
loaders/orchestrate.py
─────────────────────────────────────────────────────────────
Orquestador de carga Gold Layer BuzzINT.

Orden de ejecución (respeta dependencias FK):
  1. load_establecimientos  → dim_territorio + dim_establecimiento
                              + fact_establecimiento_anual
  2. load_simce             → fact_simce (+ rellena estab si faltan)
  3. load_alumnos           → dim_alumno + fact_matricula
  4. load_cargos            → dim_docente + fact_docentes
  5. load_sige              → fact_calificaciones
                              + enriquece dim_alumno + dim_asignatura

Uso:
  python -m loaders.orchestrate                        # rutas por defecto
  python -m loaders.orchestrate --data-root /mi/data   # raíz personalizada
  python -m loaders.orchestrate --only simce alumnos   # pasos selectivos
  python -m loaders.orchestrate --dry-run              # valida rutas, no carga
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class Step:
    name:    str
    fn:      Callable
    sources: list[Path]
    kwargs:  dict = field(default_factory=dict)


def build_steps(root: Path) -> list[Step]:
    from . import (
        load_establecimientos,
        load_simce,
        load_alumnos,
        load_cargos,
        load_sige,
    )

    mineduc = root / "mineduc" / "processed"
    simce   = root / "simce"   / "processed"
    sige    = root / "sige"    / "processed"

    steps = [
        Step(
            name="establecimientos",
            fn=load_establecimientos.run,
            sources=[mineduc / "mineduc_establecimientos.csv"],
        ),
        Step(
            name="simce_rbd",
            fn=load_simce.run,
            sources=[simce / "simce__rbd.csv"],
            kwargs={"granularity": "rbd"},
        ),
        Step(
            name="simce_comuna",
            fn=load_simce.run,
            sources=[simce / "simce__comuna.csv"],
            kwargs={"granularity": "comuna"},
        ),
        Step(
            name="simce_region",
            fn=load_simce.run,
            sources=[simce / "simce__region.csv"],
            kwargs={"granularity": "region"},
        ),
        Step(
            name="alumnos",
            fn=load_alumnos.run,
            sources=[mineduc / "mineduc_alumnos.csv"],
        ),
        Step(
            name="cargos",
            fn=load_cargos.run,
            sources=[mineduc / "mineduc_cargos.csv"],
        ),
        Step(
            name="sige_calificaciones",
            fn=load_sige.run_calificaciones,
            sources=[sige / "sige_calificaciones.csv"],
        ),
        Step(
            name="sige_profesores",
            fn=load_sige.run_profesores,
            sources=[sige / "sige_profesores.csv"],
        ),
    ]
    return steps


def run_all(
    data_root: Path,
    only: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    steps   = build_steps(data_root)
    results = {}

    for step in steps:
        if only and step.name not in only:
            logger.info("[SKIP] %s (no en --only)", step.name)
            results[step.name] = "skipped"
            continue

        missing = [s for s in step.sources if not s.exists()]
        if missing:
            logger.warning("[WARN] %s — archivo(s) no encontrado(s): %s",
                           step.name, [str(m) for m in missing])
            results[step.name] = "missing_source"
            continue

        if dry_run:
            logger.info("[DRY]  %s → %s", step.name,
                        [str(s) for s in step.sources])
            results[step.name] = "dry_ok"
            continue

        t0 = time.perf_counter()
        try:
            for src in step.sources:
                logger.info("▶ %s  %s", step.name, src.name)
                step.fn(src, **step.kwargs)
            elapsed = time.perf_counter() - t0
            logger.info("✓ %s  (%.1fs)", step.name, elapsed)
            results[step.name] = "ok"
        except Exception as exc:
            logger.error("✗ %s  ERROR: %s", step.name, exc, exc_info=True)
            results[step.name] = f"error: {exc}"

    return results


def _print_summary(results: dict[str, str]) -> None:
    print("\n" + "═" * 52)
    print("  BuzzINT — Resumen de carga Gold Layer")
    print("═" * 52)
    for name, status in results.items():
        icon = "✓" if status == "ok" \
            else "○" if status in ("skipped", "dry_ok", "missing_source") \
            else "✗"
        print(f"  {icon}  {name:<28}  {status}")
    print("═" * 52)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orquestador ETL Gold Layer BuzzINT"
    )
    parser.add_argument(
        "--data-root",
        default=os.getenv("BUZZINT_DATA_ROOT", "data"),
        metavar="DIR",
        help="Raíz del directorio de datos (default: ./data)",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="STEP",
        help="Ejecutar solo estos pasos (nombres separados por espacio)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validar rutas y dependencias sin cargar datos",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    data_root = Path(args.data_root).expanduser().resolve()
    logger.info("Data root: %s", data_root)

    results = run_all(
        data_root=data_root,
        only=args.only,
        dry_run=args.dry_run,
    )

    _print_summary(results)

    # Exit code 1 si algún paso falló
    if any(v.startswith("error") for v in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
