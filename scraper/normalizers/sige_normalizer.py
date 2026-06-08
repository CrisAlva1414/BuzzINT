import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

try:
    from .normalizer_base import NormalizerManifest
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scraper.normalizers.normalizer_base import NormalizerManifest

logger = logging.getLogger(__name__)

_INPUT_DEFAULT  = "data/sige/raw"
_OUTPUT_DEFAULT = "data/sige/processed"

_FILENAME_RE = re.compile(
    r"^(?P<rbd>\d+)_(?P<cod>\d+)_(?P<grado>\d+)_(?P<letra>[A-Z])_"
    r"(?P<fecha>\d{8})_(?P<hora>\d{6})\.pdf$",
    re.IGNORECASE,
)

_DESIGN_OFFSETS = {
    "con_comuna": dict(n_orden=0, nombre=1, run=2, fec_nac=3, comuna=4,    sexo=5, notas_start=6,  prom=31, prom_lit=32, asist=33, sf=34, obs=35),
    "sin_comuna": dict(n_orden=0, nombre=1, run=2, fec_nac=3, comuna=None, sexo=4, notas_start=5,  prom=30, prom_lit=31, asist=32, sf=33, obs=34),
}

_PROF_MARKERS = {"subsectores", "subsector", "nombre profesor"}

# Claves de deduplicación para upsert
_KEYS_CAL  = ["rbd", "agno", "grado", "letra", "n_orden"]
_KEYS_PROF = ["rbd", "agno", "grado", "letra", "n_asig"]


def _parse_filename(filename: str) -> dict | None:
    m = _FILENAME_RE.match(filename)
    if not m:
        return None
    fecha = m.group("fecha")
    return {
        "rbd":        m.group("rbd").zfill(8),
        "grado":      int(m.group("grado")),
        "letra":      m.group("letra").upper(),
        "agno_file":  int(fecha[4:]),
        "fecha_acta": f"{fecha[:2]}/{fecha[2:4]}/{fecha[4:]}",
    }


def _extract_agno(text: str) -> int | None:
    years = [int(y) for y in re.findall(r"\b(20\d{2}|199\d)\b", text) if 1990 <= int(y) <= 2030]
    return max(years) if years else None


def _find_alumnos_table(tables: list) -> list | None:
    candidates = [t for t in tables if len(t) >= 3]
    if not candidates:
        return None
    return max(candidates, key=lambda t: sum(1 for r in t if r and r[0] and str(r[0]).strip().isdigit()))


def _find_profesores_table(tables: list) -> list | None:
    for tbl in tables:
        if not tbl or len(tbl[0]) != 6:
            continue
        for row in tbl[:3]:
            if any(m in " ".join(str(c or "").strip().lower() for c in row) for m in _PROF_MARKERS):
                return tbl
    return None


def _detect_active_slots(data_rows: list, notas_start: int) -> list[int]:
    active = []
    for slot in range(25):
        col = notas_start + slot
        if any(_nota(r[col] if col < len(r) else None) is not None for r in data_rows):
            active.append(slot)
        elif active:
            break
    return active


def _parse_alumnos(tbl: list, off: dict, meta: dict, agno: int) -> list[dict]:
    data_rows    = [r for r in tbl if r[0] and str(r[0]).strip().isdigit()]
    active_slots = _detect_active_slots(data_rows, off["notas_start"])
    rows = []
    for raw in data_rows:
        r = list(raw) + [None] * 5
        record = {
            "rbd": meta["rbd"], "agno": agno, "grado": meta["grado"],
            "letra": meta["letra"], "fecha_acta": meta["fecha_acta"],
            "n_orden": _safe_int(r[off["n_orden"]]),
            "nombre_completo": _s(r[off["nombre"]]),
            "run": _s(r[off["run"]]),
            "fec_nac": _s(r[off["fec_nac"]]),
            "sexo": _s(r[off["sexo"]]),
        }
        if off["comuna"] is not None:
            record["comuna"] = _s(r[off["comuna"]])
        for slot in active_slots:
            col = off["notas_start"] + slot
            record[f"nota_{slot + 1}"] = _nota(r[col] if col < len(r) else None)
        record.update({
            "promedio": _nota(r[off["prom"]]),
            "prom_literario": _s(r[off["prom_lit"]]),
            "asistencia_pct": _safe_int(r[off["asist"]]),
            "situacion_final": _s(r[off["sf"]]),
            "observaciones": _s(r[off["obs"]]),
        })
        rows.append(record)
    return rows


def _parse_profesores(tbl: list, meta: dict, agno: int) -> list[dict]:
    rows = []
    for raw in [r for r in tbl if r and r[0] and str(r[0]).strip().isdigit()]:
        r = list(raw) + [None] * 3
        rows.append({
            "rbd": meta["rbd"], "agno": agno, "grado": meta["grado"],
            "letra": meta["letra"], "fecha_acta": meta["fecha_acta"],
            "n_asig": _safe_int(r[0]),
            "subsector": _s(r[1]),
            "nombre_profesor": _s(r[2]),
            "run_profesor": _s(r[3]),
            "habilitacion": _s(r[4]),
        })
    return rows


def _extract_pdf(pdf_path: Path) -> tuple[list[dict], list[dict]]:
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber no instalado: pip install pdfplumber --break-system-packages")
        return [], []

    meta = _parse_filename(pdf_path.name)
    if meta is None:
        logger.warning("Nombre no reconocido: %s", pdf_path.name)
        return [], []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            all_tables = []
            header_text = ""
            for page in pdf.pages:
                all_tables.extend(page.extract_tables() or [])
                header_text += (page.extract_text() or "") + "\n"
    except Exception as exc:
        logger.error("Error abriendo %s: %s", pdf_path.name, exc)
        return [], []

    alumnos_tbl    = _find_alumnos_table(all_tables)
    profesores_tbl = _find_profesores_table(all_tables)

    if not alumnos_tbl or len(alumnos_tbl) < 3:
        logger.warning("Sin tabla de alumnos: %s", pdf_path.name)
        return [], []

    agno = _extract_agno(header_text) or meta["agno_file"]
    off  = _DESIGN_OFFSETS["con_comuna" if len(alumnos_tbl[0]) >= 36 else "sin_comuna"]

    cal_rows  = _parse_alumnos(alumnos_tbl, off, meta, agno)
    prof_rows = _parse_profesores(profesores_tbl, meta, agno) if profesores_tbl else []

    return cal_rows, prof_rows


def _upsert_csv(output: Path, df_new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if output.exists():
        df_old = pd.read_csv(output, dtype=str)
        valid_keys = [k for k in keys if k in df_new.columns and k in df_old.columns]
        return (
            pd.concat([df_old, df_new.astype(str)], ignore_index=True)
            .drop_duplicates(subset=valid_keys, keep="last")
            .reset_index(drop=True)
        )
    return df_new


def normalize(
    source_dir: Path,
    output_dir: Path,
    mode: str = "full",
) -> dict:
    source_dir = Path(source_dir).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest  = NormalizerManifest.load(output_dir)
    all_cal   = []
    all_prof  = []
    stats     = {"processed": 0, "skipped": 0}

    pdfs = sorted(source_dir.rglob("*.pdf"))
    logger.info("%d PDF(s) encontrados", len(pdfs))

    for pdf_path in pdfs:
        if mode == "delta" and manifest.is_processed(pdf_path):
            logger.info("skip: %s", pdf_path.name)
            stats["skipped"] += 1
            continue

        cal_rows, prof_rows = _extract_pdf(pdf_path)
        all_cal.extend(cal_rows)
        all_prof.extend(prof_rows)

        if cal_rows:
            stats["processed"] += 1
            manifest.mark_normalized(pdf_path, len(cal_rows), output_dir)

    out_cal  = output_dir / "sige_calificaciones.csv"
    out_prof = output_dir / "sige_profesores.csv"

    if all_cal:
        df = _upsert_csv(out_cal, pd.DataFrame(all_cal), _KEYS_CAL)
        df.to_csv(out_cal, index=False, encoding="utf-8-sig")
        logger.info("sige_calificaciones.csv → %d filas", len(df))

    if all_prof:
        df = _upsert_csv(out_prof, pd.DataFrame(all_prof), _KEYS_PROF)
        df.to_csv(out_prof, index=False, encoding="utf-8-sig")
        logger.info("sige_profesores.csv → %d filas", len(df))

    stats["pending_for_db"] = manifest.pending_for_db()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Normaliza actas PDF del SIGE")
    parser.add_argument("--input",   default=_INPUT_DEFAULT,  metavar="DIR")
    parser.add_argument("--output",  default=_OUTPUT_DEFAULT, metavar="DIR")
    parser.add_argument("--mode",    default="full", choices=["full", "delta"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    stats = normalize(
        source_dir = Path(args.input),
        output_dir = Path(args.output),
        mode       = args.mode,
    )

    print(f"\n{'═'*52}")
    print(f"  Procesados : {stats['processed']}")
    print(f"  Omitidos   : {stats['skipped']}")
    print(f"  Pendiente DB: {len(stats['pending_for_db'])} archivo(s)")
    print(f"{'═'*52}")
    sys.exit(0)


def _s(v) -> str | None:
    if v is None: return None
    s = str(v).strip()
    return s if s and s != "None" else None

def _safe_int(v) -> int | None:
    try: return int(str(v).strip())
    except (ValueError, TypeError, AttributeError): return None

def _nota(v) -> float | None:
    if v is None or str(v).strip() in ("", "-", "None"): return None
    try: return float(str(v).strip().replace(",", "."))
    except ValueError: return None


if __name__ == "__main__":
    main()