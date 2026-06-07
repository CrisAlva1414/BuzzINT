from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


# CSVs procesados que el pipeline produce por defecto
DEFAULT_FILES = [
    "data/mineduc/processed/mineduc_cargos.csv",
    "data/mineduc/processed/mineduc_alumnos.csv",
    "data/mineduc/processed/mineduc_matricula.csv",
    "data/mineduc/processed/mineduc_asistencia.csv",
    "data/mineduc/processed/mineduc_rendimiento.csv",
    "data/mineduc/processed/mineduc_simce.csv",
]

CHUNK_SIZE        = 50_000   # filas por chunk al escanear valores
SAMPLE_ROWS       = 200_000  # máximo de filas a escanear para estadísticas
AGNO_MIN          = 1990
AGNO_MAX          = 2030
MIN_ROWS_WARN     = 1_000    # aviso si el archivo tiene muy pocas filas
ENCODINGS_TRY     = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]

# Columnas que DEBEN existir en cualquier CSV MINEDUC procesado
MANDATORY_COLS    = {"agno", "_source_file"}

# Columnas que deben ser numéricas cuando existen
NUMERIC_COLS      = {
    "agno", "rbd", "dgv_rbd", "mrun",
    "cod_reg_rbd", "cod_pro_rbd", "cod_com_rbd",
    "cod_depe", "rural_rbd",
}

# Patrones de fechas válidas (post-normalización)
DATE_PATTERNS     = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),   # AAAA-MM-DD
    re.compile(r"^\d{4}-\d{2}$"),          # AAAA-MM
]
DATE_COLS         = {"fec_nac_alu", "doc_fec_nac", "fec_defun_alu"}

CRITICAL = "CRITICAL"
WARNING  = "WARNING"
INFO     = "INFO"


@dataclass
class Issue:
    level:   str          # CRITICAL | WARNING | INFO
    code:    str          # identificador corto para el orquestador
    message: str
    detail:  Optional[str] = None


@dataclass
class FileReport:
    path:          str
    ok:            bool  = True
    encoding:      Optional[str] = None
    separator:     Optional[str] = None
    row_count:     Optional[int] = None
    col_count:     Optional[int] = None
    columns:       list[str]     = field(default_factory=list)
    issues:        list[Issue]   = field(default_factory=list)

    def add(self, level: str, code: str, message: str, detail: str = None):
        self.issues.append(Issue(level, code, message, detail))
        if level == CRITICAL:
            self.ok = False

    def has_critical(self) -> bool:
        return any(i.level == CRITICAL for i in self.issues)


@dataclass
class ValidationReport:
    generated_at:  str           = field(default_factory=lambda: datetime.now().isoformat())
    passed:        bool          = True
    files_ok:      int           = 0
    files_failed:  int           = 0
    files:         list[FileReport] = field(default_factory=list)

    def add_file(self, fr: FileReport):
        self.files.append(fr)
        if fr.has_critical():
            self.files_failed += 1
            self.passed = False
        else:
            self.files_ok += 1

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def detect_format(path: Path) -> tuple[Optional[str], Optional[str]]:
    for enc in ENCODINGS_TRY:
        for sep in [";", ",", "\t", "|"]:
            try:
                df = pd.read_csv(
                    path, encoding=enc, sep=sep,
                    nrows=5, dtype=str,
                    on_bad_lines="skip",
                    encoding_errors="replace",
                )
                if df.shape[1] >= 2:
                    return enc, sep
            except Exception:
                pass
    return None, None


def check_parseable(path: Path, report: FileReport) -> tuple[Optional[str], Optional[str]]:
    if not path.exists():
        report.add(CRITICAL, "FILE_NOT_FOUND", f"Archivo no encontrado: {path}")
        return None, None

    if path.stat().st_size == 0:
        report.add(CRITICAL, "FILE_EMPTY", "El archivo tiene 0 bytes")
        return None, None

    enc, sep = detect_format(path)
    if enc is None:
        report.add(CRITICAL, "NOT_PARSEABLE",
                   "No se pudo detectar encoding/separador válido",
                   "Se intentaron: utf-8-sig, utf-8, latin-1, cp1252 × ; , TAB |")
        return None, None

    report.encoding  = enc
    report.separator = repr(sep)
    report.add(INFO, "FORMAT_OK", f"Formato detectado: enc={enc} sep={repr(sep)}")
    return enc, sep


def check_structure(path: Path, enc: str, sep: str, report: FileReport) -> Optional[pd.DataFrame]:
    try:
        header_df = pd.read_csv(
            path, encoding=enc, sep=sep,
            nrows=0, dtype=str,
            encoding_errors="replace",
        )
    except Exception as e:
        report.add(CRITICAL, "HEADER_UNREADABLE", f"No se pudo leer el header: {e}")
        return None

    cols = [c.strip().lower() for c in header_df.columns]
    report.columns   = cols
    report.col_count = len(cols)

    if len(cols) < 2:
        report.add(CRITICAL, "TOO_FEW_COLUMNS",
                   f"Solo {len(cols)} columna(s) — posible separador incorrecto")
        return None

    # Columnas duplicadas
    dupes = [c for c in set(cols) if cols.count(c) > 1]
    if dupes:
        report.add(CRITICAL, "DUPLICATE_COLUMNS",
                   f"{len(dupes)} columna(s) duplicada(s)",
                   ", ".join(dupes))

    # Columnas obligatorias
    missing_mandatory = MANDATORY_COLS - set(cols)
    if missing_mandatory:
        report.add(CRITICAL, "MISSING_MANDATORY_COLS",
                   f"Columnas obligatorias ausentes: {missing_mandatory}")
    else:
        report.add(INFO, "MANDATORY_COLS_OK", "Columnas obligatorias presentes")

    return header_df


def check_row_count(path: Path, enc: str, sep: str, report: FileReport) -> int:
    total = 0
    try:
        for chunk in pd.read_csv(
            path, encoding=enc, sep=sep,
            chunksize=CHUNK_SIZE, dtype=str,
            on_bad_lines="skip",
            encoding_errors="replace",
        ):
            total += len(chunk)
    except Exception as e:
        report.add(WARNING, "ROW_COUNT_FAILED", f"No se pudo contar filas completas: {e}")
        return total

    report.row_count = total

    if total == 0:
        report.add(CRITICAL, "NO_DATA_ROWS", "El archivo tiene header pero 0 filas de datos")
    elif total < MIN_ROWS_WARN:
        report.add(WARNING, "FEW_ROWS",
                   f"Solo {total:,} filas — ¿proceso incompleto?")
    else:
        report.add(INFO, "ROW_COUNT_OK", f"{total:,} filas")

    return total


def check_values(path: Path, enc: str, sep: str, cols: list[str], report: FileReport):
    rows_seen     = 0
    agno_bad      = 0
    numeric_bad   : dict[str, int] = {}
    date_bad      : dict[str, int] = {}
    null_counts   : dict[str, int] = {}
    source_files  : set[str]       = set()

    active_numeric = NUMERIC_COLS & set(cols)
    active_date    = DATE_COLS    & set(cols)

    try:
        for chunk in pd.read_csv(
            path, encoding=enc, sep=sep,
            chunksize=CHUNK_SIZE, dtype=str,
            on_bad_lines="skip",
            encoding_errors="replace",
        ):
            chunk.columns = [c.strip().lower() for c in chunk.columns]
            chunk = chunk.fillna("")

            # Agno en rango
            if "agno" in chunk.columns:
                agno_num = pd.to_numeric(chunk["agno"], errors="coerce")
                agno_bad += int(
                    ((agno_num < AGNO_MIN) | (agno_num > AGNO_MAX) | agno_num.isna()).sum()
                )

            # Columnas numéricas
            for col in active_numeric:
                if col in chunk.columns:
                    bad = pd.to_numeric(chunk[col], errors="coerce").isna() & (chunk[col] != "")
                    numeric_bad[col] = numeric_bad.get(col, 0) + int(bad.sum())

            # Columnas de fecha
            for col in active_date:
                if col in chunk.columns:
                    non_empty = chunk[col][chunk[col] != ""]
                    bad_mask  = ~non_empty.apply(
                        lambda v: any(p.match(str(v)) for p in DATE_PATTERNS)
                    )
                    date_bad[col] = date_bad.get(col, 0) + int(bad_mask.sum())

            # Nulos en columnas obligatorias
            for col in MANDATORY_COLS:
                if col in chunk.columns:
                    n = int((chunk[col] == "").sum())
                    null_counts[col] = null_counts.get(col, 0) + n

            # _source_file: diversidad (¿se procesaron varios años?)
            if "_source_file" in chunk.columns:
                source_files.update(chunk["_source_file"].unique())

            rows_seen += len(chunk)
            if rows_seen >= SAMPLE_ROWS:
                report.add(INFO, "SAMPLE_ONLY",
                           f"Validación de valores sobre muestra de {rows_seen:,} filas")
                break

    except Exception as e:
        report.add(WARNING, "VALUE_CHECK_FAILED", f"Error durante validación de valores: {e}")
        return

    # Agno
    if agno_bad > 0:
        report.add(CRITICAL, "AGNO_OUT_OF_RANGE",
                   f"{agno_bad:,} fila(s) con agno fuera de [{AGNO_MIN}–{AGNO_MAX}]")
    else:
        report.add(INFO, "AGNO_OK", f"Valores de agno en rango [{AGNO_MIN}–{AGNO_MAX}]")

    # Numéricos
    for col, n in numeric_bad.items():
        if n > 0:
            pct = n / max(rows_seen, 1) * 100
            lvl = CRITICAL if pct > 5 else WARNING
            report.add(lvl, f"NUMERIC_BAD_{col.upper()}",
                       f"'{col}': {n:,} valores no numéricos ({pct:.1f}% de la muestra)")

    # Fechas
    for col, n in date_bad.items():
        if n > 0:
            pct = n / max(rows_seen, 1) * 100
            lvl = WARNING   # fechas mal formateadas son warning, no crítico
            report.add(lvl, f"DATE_BAD_{col.upper()}",
                       f"'{col}': {n:,} valores con formato de fecha inesperado ({pct:.1f}%)")

    # Nulos en obligatorias
    for col, n in null_counts.items():
        if n > 0:
            pct = n / max(rows_seen, 1) * 100
            lvl = CRITICAL if pct > 10 else WARNING
            report.add(lvl, f"NULL_MANDATORY_{col.upper()}",
                       f"'{col}': {n:,} filas vacías ({pct:.1f}%)")

    # _source_file diversidad
    if source_files:
        report.add(INFO, "SOURCE_FILES",
                   f"{len(source_files)} archivo(s) fuente detectado(s)",
                   ", ".join(sorted(source_files)[:10]) + ("…" if len(source_files) > 10 else ""))


def validate_file(path: Path, log: logging.Logger) -> FileReport:
    report = FileReport(path=str(path))
    log.info("━" * 60)
    log.info("Validando: %s", path.name)

    # V1 — parseable
    enc, sep = check_parseable(path, report)
    if enc is None:
        return report

    # V2 — estructura
    header = check_structure(path, enc, sep, report)
    if header is None:
        return report

    cols = report.columns

    # V3 — conteo de filas (chunked)
    check_row_count(path, enc, sep, report)

    # V4 — valores
    check_values(path, enc, sep, cols, report)

    # Resumen por archivo
    n_crit = sum(1 for i in report.issues if i.level == CRITICAL)
    n_warn = sum(1 for i in report.issues if i.level == WARNING)
    status = "✗ FAILED" if n_crit else ("⚠ WARNED" if n_warn else "✓ OK")
    log.info("%s  —  %d crítico(s), %d aviso(s)", status, n_crit, n_warn)

    return report


def validate_all(paths: list[Path], log: logging.Logger) -> ValidationReport:
    report = ValidationReport()
    for p in paths:
        fr = validate_file(p, log)
        report.add_file(fr)
    return report


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Valida CSVs procesados MINEDUC")
    p.add_argument(
        "--files", nargs="+", metavar="CSV",
        help="CSVs a validar (default: los 6 del pipeline)"
    )
    p.add_argument(
        "--report-dir", default="data/mineduc/processed",
        metavar="DIR", help="Directorio donde guardar validate_report.json"
    )
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main() -> None:
    args   = _parse_args()
    level  = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("validator")

    paths = [Path(f) for f in (args.files or DEFAULT_FILES)]

    log.info("Iniciando validación de %d archivo(s)…", len(paths))
    report = validate_all(paths, log)

    report_dir  = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "validate_report.json"

    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report.to_dict(), fh, ensure_ascii=False, indent=2)

    log.info("━" * 60)
    log.info("RESUMEN FINAL")
    log.info("  Archivos OK     : %d", report.files_ok)
    log.info("  Archivos FAILED : %d", report.files_failed)
    log.info("  Reporte JSON    : %s", report_path)

    if not report.passed:
        log.error("❌  VALIDACIÓN FALLIDA — pipeline detenido")
        sys.exit(1)
    else:
        log.info("✅  Validación completada sin errores críticos")
        sys.exit(0)


if __name__ == "__main__":
    main()