"""
inspect_sep_csvs.py
────────────────────
Sub-script de diagnóstico. Imprime las primeras N líneas de cada CSV
encontrado en una carpeta (extrayendo RARs si los hay) junto con:
  - Encoding detectado
  - Separador inferido
  - Shape (filas × columnas)
  - Lista de columnas
  - Las primeras N filas como tabla

Uso:
    python inspect_sep_csvs.py --input data/mineduc/raw/alumnos
    python inspect_sep_csvs.py --input data/mineduc/raw/alumnos --no-rar --rows 5
    python inspect_sep_csvs.py --input ./alumnos_csv --no-rar --rows 3 --verbose

Flags:
    --input   DIR   Carpeta con RARs o CSVs  (default: data/mineduc/raw/alumnos)
    --rows    N     Filas de preview          (default: 20)
    --no-rar        Leer CSVs directamente sin extraer RARs
    --verbose       Mostrar encoding y separador detectados
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pandas as pd
import rarfile
from charset_normalizer import from_bytes

log = logging.getLogger(__name__)

_SEPARATORS = [";", ",", "\t", "|"]
_SEP_LABELS = {";": "semicolon", ",": "comma", "\t": "tab", "|": "pipe"}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def detect_encoding(path: Path, sample: int = 65_536) -> str:
    raw = path.read_bytes()[:sample]
    result = from_bytes(raw).best()
    return str(result.encoding) if result else "latin-1"


def sniff_and_read(path: Path, encoding: str, nrows: int) -> tuple[Optional[pd.DataFrame], str]:
    """Prueba separadores y devuelve (df, sep_usado) o (None, '')."""
    for sep in _SEPARATORS:
        try:
            df = pd.read_csv(
                path, sep=sep, encoding=encoding,
                dtype=str, low_memory=False,
                nrows=nrows, on_bad_lines="warn",
            )
            if df.shape[1] >= 2:
                return df, sep
        except Exception:
            continue
    return None, ""


def extract_csvs_from_rar(rar_path: Path, dest: Path) -> list[Path]:
    out: list[Path] = []
    try:
        with rarfile.RarFile(rar_path) as rf:
            for m in rf.infolist():
                if m.filename.lower().endswith(".csv"):
                    rf.extract(m, dest)
                    out.append(dest / m.filename)
    except Exception as exc:
        log.warning("No se pudo abrir %s: %s", rar_path.name, exc)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Inspector
# ─────────────────────────────────────────────────────────────────────────────

def inspect(source_dir: Path, nrows: int, no_rar: bool, verbose: bool) -> None:
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 30)

    if no_rar:
        csv_files = sorted(source_dir.rglob("*.csv"))
        tmp_ctx = None
    else:
        rar_files = sorted(source_dir.rglob("*.rar"))
        tmp_ctx = tempfile.TemporaryDirectory(prefix="sep_inspect_")
        extract_root = Path(tmp_ctx.name)
        csv_files = []
        for rar in rar_files:
            csv_files.extend(extract_csvs_from_rar(rar, extract_root))
        csv_files.sort()

    if not csv_files:
        print(f"[!] Sin CSVs en {source_dir}")
        sys.exit(1)

    try:
        for idx, path in enumerate(csv_files, 1):
            enc = detect_encoding(path)
            df, sep = sniff_and_read(path, enc, nrows)

            header = f"[{idx}/{len(csv_files)}]  {path.name}"
            print("\n" + "═" * 70)
            print(header)
            print("─" * 70)

            if df is None:
                print("  ✗  No se pudo parsear este archivo")
                continue

            # Contar total de filas sin cargar el archivo completo
            try:
                total_rows = sum(1 for _ in open(path, encoding=enc)) - 1
            except Exception:
                total_rows = "?"

            print(f"  Encoding   : {enc}")
            if verbose:
                print(f"  Separador  : {sep!r}  ({_SEP_LABELS.get(sep, sep)})")
            print(f"  Total filas: {total_rows:,}" if isinstance(total_rows, int) else f"  Total filas: {total_rows}")
            print(f"  Columnas   : {df.shape[1]}  →  {list(df.columns)}")
            print(f"  Preview    : primeras {min(nrows, len(df))} filas")
            print()
            print(df.to_string(index=True))

    finally:
        if tmp_ctx:
            tmp_ctx.cleanup()

    print("\n" + "═" * 70)
    print(f"Total archivos inspeccionados: {len(csv_files)}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Inspecciona CSVs de Alumnos SEP.")
    p.add_argument("--input",   default="data/mineduc/raw/alumnos", metavar="DIR")
    p.add_argument("--rows",    default=20, type=int, metavar="N",
                   help="Filas de preview por archivo (default: 20)")
    p.add_argument("--no-rar",  action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s  %(message)s",
    )

    inspect(
        source_dir=Path(args.input).expanduser().resolve(),
        nrows=args.rows,
        no_rar=args.no_rar,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()