from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import rarfile
from charset_normalizer import from_bytes

log = logging.getLogger(__name__)

SEPARATORS   = [";", ",", "\t", "|", "~"]
SEP_LABELS   = {";": "semicolon", ",": "comma", "\t": "tab", "|": "pipe", "~": "tilde"}
PREVIEW_ROWS = 5          # filas de preview en salida LLM (sobrescrito por --rows)
SAMPLE_BYTES = 131_072    # 128 KB para detección de encoding
MIN_COLS     = 2          # mínimo de columnas para aceptar un separador


def detect_encoding(path: Path, sample: int = SAMPLE_BYTES) -> str:
    raw = path.read_bytes()[:sample]

    # BOM explícito tiene prioridad absoluta
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"

    # Candidatos ordenados por frecuencia en archivos MINEDUC chilenos
    candidates = ["utf-8", "utf-8-sig", "cp1252", "iso-8859-1", "latin1"]
    for enc in candidates:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            pass

    # Fallback: charset-normalizer con umbral de confianza
    result = from_bytes(raw).best()
    if result and result.encoding:
        return str(result.encoding)

    log.warning("No se detectó encoding para %s — usando latin1", path.name)
    return "latin1"


def sniff_and_read(
    path: Path,
    encoding: str,
    nrows: int,
) -> tuple[Optional[pd.DataFrame], str]:
    import io
    try:
        raw_text = path.read_bytes().decode(encoding, errors="replace")
        # Normalizar line endings antes de parsear
        raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        # Strip BOM si quedó como carácter
        raw_text = raw_text.lstrip("\ufeff")
    except Exception:
        raw_text = None

    for sep in SEPARATORS:
        # Intento 1: desde texto pre-normalizado (fix CRLF+BOM)
        if raw_text is not None:
            try:
                df = pd.read_csv(
                    io.StringIO(raw_text),
                    sep=sep,
                    dtype=str,
                    low_memory=False,
                    nrows=nrows,
                    on_bad_lines="warn",
                    skipinitialspace=True,
                )
                if df.shape[1] >= MIN_COLS:
                    # Normalizar campos con solo espacios → NaN
                    df = df.apply(lambda s: s.str.strip().replace("", pd.NA))
                    return df, sep
            except Exception:
                pass

        # Intento 2: directo desde archivo (fallback)
        try:
            df = pd.read_csv(
                path,
                sep=sep,
                encoding=encoding,
                dtype=str,
                low_memory=False,
                nrows=nrows,
                on_bad_lines="warn",
                skipinitialspace=True,
            )
            if df.shape[1] >= MIN_COLS:
                df = df.apply(lambda s: s.str.strip().replace("", pd.NA))
                return df, sep
        except Exception:
            continue

    return None, ""


def count_rows(path: Path, encoding: str) -> int | str:
    try:
        with open(path, encoding=encoding, errors="replace", newline="") as fh:
            # Contar \\n y \\r\\n por igual usando splitlines
            content = fh.read()
            return max(0, len(content.splitlines()) - 1)  # -1 para excluir header
    except Exception:
        return "?"


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


def profile_columns(df: pd.DataFrame) -> list[dict]:
    profiles = []
    for col in df.columns:
        series = df[col].replace("", pd.NA)
        null_count = int(series.isna().sum())
        total      = len(series)
        non_null   = series.dropna()

        # Tipo inferido
        if non_null.empty:
            inferred = "empty"
        else:
            try:
                pd.to_numeric(non_null)
                inferred = "numeric"
            except (ValueError, TypeError):
                try:
                    pd.to_datetime(non_null, infer_datetime_format=True, errors="raise")
                    inferred = "date"
                except Exception:
                    inferred = "string"

        card = int(non_null.nunique()) if not non_null.empty else 0
        sample_vals = non_null.head(3).tolist()

        profiles.append({
            "col": col,
            "type": inferred,
            "nulls": null_count,
            "null_pct": round(null_count / total * 100, 1) if total > 0 else 0,
            "cardinality": card,
            "sample": sample_vals,
        })
    return profiles


def format_file_block(
    idx: int,
    total: int,
    path: Path,
    df: Optional[pd.DataFrame],
    sep: str,
    enc: str,
    total_rows: int | str,
    nrows: int,
) -> str:
    lines: list[str] = []
    lines.append(f"## FILE {idx}/{total}: {path.name}")
    lines.append(f"source: {path}")
    lines.append(f"encoding: {enc}")
    lines.append(f"separator: {SEP_LABELS.get(sep, repr(sep))}")
    lines.append(f"rows_total: {total_rows:,}" if isinstance(total_rows, int) else f"rows_total: {total_rows}")

    if df is None:
        lines.append("status: PARSE_FAILED")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"cols_count: {df.shape[1]}")
    lines.append(f"cols: {list(df.columns)}")
    lines.append("")

    # Perfil compacto de columnas
    lines.append("### COLUMN PROFILE")
    lines.append("col | type | null_pct | cardinality | sample_values")
    lines.append("--- | ---- | -------- | ----------- | -------------")
    for p in profile_columns(df):
        sample_str = ", ".join(str(v) for v in p["sample"])
        lines.append(
            f"{p['col']} | {p['type']} | {p['null_pct']}% | {p['cardinality']} | {sample_str}"
        )

    # Preview: primeras N filas como JSON lines (compacto, sin espacios extra)
    lines.append("")
    lines.append(f"### DATA PREVIEW ({min(nrows, len(df))} rows as JSON-lines)")
    preview_df = df.head(nrows)
    for _, row in preview_df.iterrows():
        # Eliminar valores vacíos para reducir tokens
        record = {k: v for k, v in row.items() if pd.notna(v) and str(v).strip() != ""}
        lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))

    lines.append("")
    return "\n".join(lines)


def inspect(
    source_dir: Path,
    nrows: int,
    no_rar: bool,
    output_file: Optional[Path],
) -> None:
    # Recolectar archivos
    if no_rar:
        csv_files = sorted(source_dir.rglob("*.csv"))
        tmp_ctx   = None
    else:
        rar_files = sorted(source_dir.rglob("*.rar"))
        tmp_ctx   = tempfile.TemporaryDirectory(prefix="sep_inspect_")
        extract_root = Path(tmp_ctx.name)
        csv_files    = []
        for rar in rar_files:
            csv_files.extend(extract_csvs_from_rar(rar, extract_root))
        csv_files.sort()

    if not csv_files:
        print(f"[!] Sin CSVs en {source_dir}")
        sys.exit(1)

    # Header del reporte
    header_lines = [
        "# SEP CSV INSPECTION REPORT",
        f"generated: {datetime.now().isoformat(timespec='seconds')}",
        f"source_dir: {source_dir}",
        f"files_found: {len(csv_files)}",
        f"preview_rows: {nrows}",
        "",
        "---",
        "",
    ]

    all_blocks: list[str] = []

    try:
        for idx, path in enumerate(csv_files, 1):
            enc     = detect_encoding(path)
            df, sep = sniff_and_read(path, enc, nrows)
            rows    = count_rows(path, enc) if df is not None else "?"

            block = format_file_block(idx, len(csv_files), path, df, sep, enc, rows, nrows)
            all_blocks.append(block)

            # Siempre imprimir a stdout
            print(block)
            print("---")

    finally:
        if tmp_ctx:
            tmp_ctx.cleanup()

    # Footer
    footer = f"\n## SUMMARY\ntotal_files: {len(csv_files)}\n"

    # Consolidar y guardar si se pidió
    if output_file:
        full_report = "\n".join(header_lines) + "\n".join(all_blocks) + footer
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(full_report, encoding="utf-8")
        print(f"\n[✓] Reporte guardado en: {output_file}")
    else:
        print(footer)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Inspecciona CSVs/RARs de MINEDUC/SEP. Salida LLM-optimizada.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
            Ejemplos:
            python inspect_sep_csvs.py --input data/mineduc/raw/cargos
            python inspect_sep_csvs.py --input data/ --rows 10 --output report.md
            python inspect_sep_csvs.py --input data/ --no-rar --output reports/sep_inspect.md
        """,
    )
    p.add_argument(
        "--input", default="data/mineduc/raw/cargos", metavar="DIR",
        help="Directorio raíz con RARs/CSVs (default: data/mineduc/raw/cargos)",
    )
    p.add_argument(
        "--rows", default=5, type=int, metavar="N",
        help="Filas de preview por archivo (default: 5)",
    )
    p.add_argument(
        "--no-rar", action="store_true",
        help="Buscar CSVs directamente, sin extraer RARs",
    )
    p.add_argument(
        "--output", default=None, metavar="FILE",
        help="Guardar reporte consolidado en este archivo .md (opcional)",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Logging de debug",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s  %(message)s",
    )

    inspect(
        source_dir  = Path(args.input).expanduser().resolve(),
        nrows       = args.rows,
        no_rar      = args.no_rar,
        output_file = Path(args.output) if args.output else None,
    )


if __name__ == "__main__":
    main()