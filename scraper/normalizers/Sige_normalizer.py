"""
python '/home/pc01/Proyectos/BuzzINT/scraper/normalizers/Sige_normalizer.py' '/home/pc01/Proyectos/BuzzINT/data/sige/raw' '/home/pc01/Proyectos/BuzzINT/data/sige/processed'
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_FILENAME_RE = re.compile(
    r"^(?P<rbd>\d+)_(?P<cod>\d+)_(?P<grado>\d+)_(?P<letra>[A-Z])_"
    r"(?P<fecha>\d{8})_(?P<hora>\d{6})\.pdf$",
    re.IGNORECASE,
)

_DESIGN_OFFSETS = {
    "con_comuna": dict(n_orden=0, nombre=1, run=2, fec_nac=3, comuna=4,    sexo=5, notas_start=6,  prom=31, prom_lit=32, asist=33, sf=34, obs=35),
    "sin_comuna": dict(n_orden=0, nombre=1, run=2, fec_nac=3, comuna=None, sexo=4, notas_start=5,  prom=30, prom_lit=31, asist=32, sf=33, obs=34),
}

# Columnas esperadas en la tabla de profesores (6 cols)
_PROF_HEADER_MARKERS = {"subsectores", "subsector", "nombre profesor"}


@dataclass
class SigeActa:
    rbd:        str
    agno:       int
    grado:      int
    letra:      str
    fecha_acta: str
    rows:       list[dict]
    prof_rows:  list[dict]
    source:     Path
    warnings:   list[str] = field(default_factory=list)


class SigeNormalizer:
    CAL_FILE  = "sige_calificaciones.csv"
    PROF_FILE = "sige_profesores.csv"

    def __init__(self, raw_root: Path, output_dir: Path) -> None:
        self.raw_root   = Path(raw_root)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> tuple[Path, Path]:
        all_cal, all_prof = [], []
        for year_dir in sorted(self.raw_root.iterdir()):
            if year_dir.is_dir():
                for pdf_path in sorted(year_dir.glob("*.pdf")):
                    acta = self._extract(pdf_path)
                    if acta:
                        all_cal.extend(acta.rows)
                        all_prof.extend(acta.prof_rows)
        return self._export(all_cal, all_prof)

    def run_directory(self, year_dir: Path) -> tuple[Path, Path]:
        all_cal, all_prof = [], []
        for pdf_path in sorted(Path(year_dir).glob("*.pdf")):
            acta = self._extract(pdf_path)
            if acta:
                all_cal.extend(acta.rows)
                all_prof.extend(acta.prof_rows)
        return self._export(all_cal, all_prof)

    def _extract(self, pdf_path: Path) -> SigeActa | None:
        meta = _parse_filename(pdf_path.name)
        if meta is None:
            logger.warning("Nombre no reconocido como SIGE: %s", pdf_path.name)
            return None

        try:
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber no instalado: pip install pdfplumber --break-system-packages")
            return None

        warnings: list[str] = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                all_tables, header_text = [], ""
                for page in pdf.pages:
                    all_tables.extend(page.extract_tables() or [])
                    header_text += (page.extract_text() or "") + "\n"
        except Exception as exc:
            logger.error("Error abriendo %s: %s", pdf_path.name, exc)
            return None

        alumnos_tbl = _find_alumnos_table(all_tables)
        profesores_tbl = _find_profesores_table(all_tables)

        if not alumnos_tbl or len(alumnos_tbl) < 3:
            logger.warning("Sin tabla de alumnos: %s", pdf_path.name)
            return None

        agno = _extract_agno(header_text) or meta["agno_file"]
        if agno != meta["agno_file"]:
            warnings.append(f"Año header ({agno}) ≠ nombre archivo ({meta['agno_file']}); se usa header.")

        off       = _DESIGN_OFFSETS["con_comuna" if len(alumnos_tbl[0]) >= 36 else "sin_comuna"]
        rows      = _parse_alumnos(alumnos_tbl, off, meta, agno, warnings)
        prof_rows = _parse_profesores(profesores_tbl, meta, agno, warnings) if profesores_tbl else []

        if not prof_rows:
            warnings.append("Sin tabla de profesores detectada.")

        for w in warnings:
            logger.warning("[%s] %s", pdf_path.name, w)

        return SigeActa(
            rbd=meta["rbd"], agno=agno, grado=meta["grado"],
            letra=meta["letra"], fecha_acta=meta["fecha_acta"],
            rows=rows, prof_rows=prof_rows,
            source=pdf_path, warnings=warnings,
        )

    def _export(self, cal_rows: list[dict], prof_rows: list[dict]) -> tuple[Path, Path]:
        out_cal  = self.output_dir / self.CAL_FILE
        out_prof = self.output_dir / self.PROF_FILE

        if cal_rows:
            df_new = pd.DataFrame(cal_rows)
            if out_cal.exists():
                df_old     = pd.read_csv(out_cal, dtype=str)
                keys       = ["rbd", "agno", "grado", "letra", "n_orden"]
                df_combined = (
                    pd.concat([df_old, df_new.astype(str)], ignore_index=True)
                    .drop_duplicates(subset=keys, keep="last")
                    .reset_index(drop=True)
                )
            else:
                df_combined = df_new
            df_combined.to_csv(out_cal, index=False, encoding="utf-8-sig")
            logger.info("sige_calificaciones.csv → %d filas (%s)", len(df_combined), out_cal)
        else:
            logger.warning("Sin filas de calificaciones para exportar.")

        if prof_rows:
            df_new_p = pd.DataFrame(prof_rows)
            if out_prof.exists():
                df_old_p     = pd.read_csv(out_prof, dtype=str)
                keys_p       = ["rbd", "agno", "grado", "letra", "n_asig"]
                df_combined_p = (
                    pd.concat([df_old_p, df_new_p.astype(str)], ignore_index=True)
                    .drop_duplicates(subset=keys_p, keep="last")
                    .reset_index(drop=True)
                )
            else:
                df_combined_p = df_new_p
            df_combined_p.to_csv(out_prof, index=False, encoding="utf-8-sig")
            logger.info("sige_profesores.csv → %d filas (%s)", len(df_combined_p), out_prof)
        else:
            logger.warning("Sin filas de profesores para exportar.")

        return out_cal, out_prof


def _find_alumnos_table(tables: list) -> list | None:
    candidates = [t for t in tables if len(t) >= 3]
    if not candidates:
        return None
    return max(candidates, key=lambda t: sum(
        1 for r in t if r and r[0] and str(r[0]).strip().isdigit()
    ))


def _find_profesores_table(tables: list) -> list | None:
    for tbl in tables:
        if not tbl or len(tbl[0]) != 6:
            continue
        for row in tbl[:3]:
            cells = [str(c or "").strip().lower() for c in row]
            combined = " ".join(cells)
            if any(m in combined for m in _PROF_HEADER_MARKERS):
                return tbl
    return None


def _parse_alumnos(tbl, off, meta, agno, warnings):
    data_rows    = [r for r in tbl if r[0] and str(r[0]).strip().isdigit()]
    active_slots = _detect_active_slots(data_rows, off["notas_start"])
    rows = []

    for raw in data_rows:
        r = list(raw) + [None] * 5

        record = {
            "rbd":             meta["rbd"],
            "agno":            agno,
            "grado":           meta["grado"],
            "letra":           meta["letra"],
            "fecha_acta":      meta["fecha_acta"],
            "n_orden":         _safe_int(r[off["n_orden"]]),
            "nombre_completo": _s(r[off["nombre"]]),
            "run":             _s(r[off["run"]]),
            "fec_nac":         _s(r[off["fec_nac"]]),
            "sexo":            _s(r[off["sexo"]]),
        }

        if off["comuna"] is not None:
            record["comuna"] = _s(r[off["comuna"]])

        for slot in active_slots:
            col = off["notas_start"] + slot
            record[f"nota_{slot + 1}"] = _nota(r[col] if col < len(r) else None)

        record.update({
            "promedio":        _nota(r[off["prom"]]),
            "prom_literario":  _s(r[off["prom_lit"]]),
            "asistencia_pct":  _safe_int(r[off["asist"]]),
            "situacion_final": _s(r[off["sf"]]),
            "observaciones":   _s(r[off["obs"]]),
        })
        rows.append(record)

    if not rows:
        warnings.append("Sin filas de alumnos válidas.")
    return rows


def _parse_profesores(tbl, meta, agno, warnings) -> list[dict]:
    rows = []
    # Saltar filas de encabezado (no numéricas en col 0)
    data_rows = [r for r in tbl if r and r[0] and str(r[0]).strip().isdigit()]

    for raw in data_rows:
        r = list(raw) + [None] * 3          # padding por si faltan celdas
        rows.append({
            "rbd":        meta["rbd"],
            "agno":       agno,
            "grado":      meta["grado"],
            "letra":      meta["letra"],
            "fecha_acta": meta["fecha_acta"],
            "n_asig":     _safe_int(r[0]),
            "subsector":  _s(r[1]),
            "nombre_profesor": _s(r[2]),
            "run_profesor":    _s(r[3]),
            "habilitacion":    _s(r[4]),   # TIT / HAB / HAB/AUT / etc.
        })

    if not rows:
        warnings.append("Tabla de profesores sin filas de datos.")
    return rows


def _detect_active_slots(data_rows, notas_start):
    active = []
    for slot in range(25):
        col = notas_start + slot
        if any(_nota(r[col] if col < len(r) else None) is not None for r in data_rows):
            active.append(slot)
        elif active:
            break
    return active


def _extract_agno(text):
    m = re.search(
        r"\d{4,6}-\d\s+\d+[°º]\s*(?:básico|medio)\s*[A-Z]\s+(20\d{2}|199\d)",
        text, re.IGNORECASE,
    )
    if m:
        return int(m.group(1))
    years = [int(y) for y in re.findall(r"\b(20\d{2}|199\d)\b", text) if 1990 <= int(y) <= 2030]
    return max(years) if years else None


def _parse_filename(filename):
    m = _FILENAME_RE.match(filename)
    if not m:
        return None
    fecha_raw = m.group("fecha")
    return {
        "rbd":        m.group("rbd").zfill(8),
        "grado":      int(m.group("grado")),
        "letra":      m.group("letra").upper(),
        "agno_file":  int(fecha_raw[4:]),
        "fecha_acta": f"{fecha_raw[:2]}/{fecha_raw[2:4]}/{fecha_raw[4:]}",
    }


def _s(v):
    if v is None: return None
    s = str(v).strip()
    return s if s and s != "None" else None

def _safe_int(v):
    try: return int(str(v).strip())
    except (ValueError, TypeError, AttributeError): return None

def _nota(v):
    if v is None or str(v).strip() in ("", "-", "None"): return None
    try: return float(str(v).strip().replace(",", "."))
    except ValueError: return None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) < 3:
        print("Uso: python Sige_normalizer.py <raw_root> <output_dir>")
        sys.exit(1)

    norm = SigeNormalizer(raw_root=Path(sys.argv[1]), output_dir=Path(sys.argv[2]))
    out_cal, out_prof = norm.run()

    df_cal  = pd.read_csv(out_cal)
    df_prof = pd.read_csv(out_prof)

    print(f"\n── sige_calificaciones.csv → {len(df_cal)} filas x {len(df_cal.columns)} cols")
    print(df_cal.to_string(max_rows=6))

    print(f"\n── sige_profesores.csv → {len(df_prof)} filas x {len(df_prof.columns)} cols")
    print(df_prof.to_string(max_rows=10))