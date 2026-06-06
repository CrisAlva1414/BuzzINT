"""
normalizers/sige_pdf.py
========================
Extractor especializado para los PDFs de SIGE.

Los archivos en sige/raw/{año}/ tienen el nombre:
    {RBD}_{COD}_{GRADO}_{LETRA}_{FECHA}_{HORA}.pdf

Cada PDF es un acta de notas/asistencia de un curso específico.
NO son tablas genéricas — necesitan tratamiento propio distinto al ingestor.

Patrón de nombre:
    10098_110_1_A_08012010_185625.pdf
    rbd=10098, cod_rbd=110, grado=1, letra=A, fecha=08/01/2010

El extractor:
  1. Parsea el nombre del archivo para extraer metadatos
  2. Usa pdfplumber para extraer la(s) tabla(s) del PDF
  3. Agrega columnas de metadatos (rbd, grado, letra, agno, fecha_acta)
  4. Retorna DataFrames listos para normalizar

Uso:
    extractor = SigePdfExtractor(output_dir=Path("data/sige/processed"))
    results   = extractor.run_directory(Path("data/sige/raw/2023"))
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Patrón del nombre de archivo SIGE
# {rbd}_{cod}_{grado}_{letra}_{ddmmyyyy}_{hhmmss}.pdf
_SIGE_NAME_RE = re.compile(
    r"^(?P<rbd>\d+)_(?P<cod>\d+)_(?P<grado>\d+)_(?P<letra>[A-Z])_"
    r"(?P<fecha>\d{8})_(?P<hora>\d{6})\.pdf$",
    re.IGNORECASE,
)


@dataclass
class SigeAceta:
    """Resultado de extraer un acta SIGE."""
    rbd:        str
    grado:      int
    letra:      str
    agno:       int
    fecha_acta: str          # dd/mm/yyyy
    df:         pd.DataFrame
    source:     Path
    warnings:   list[str]


class SigePdfExtractor:
    """
    Extractor de actas de notas/asistencia SIGE desde PDFs.

    Los PDFs de SIGE son actas por curso (grado + letra).
    Este extractor los procesa directamente — NO usa el Ingestor genérico.
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run_directory(self, raw_dir: Path) -> list[SigeAceta]:
        """Procesa todos los PDFs en un directorio de año."""
        raw_dir = Path(raw_dir)
        pdfs    = sorted(raw_dir.glob("*.pdf"))
        results = []
        for pdf_path in pdfs:
            acta = self.extract_pdf(pdf_path)
            if acta:
                results.append(acta)
                self._export(acta)
        logger.info(
            "SigePdfExtractor: %s → %d actas procesadas",
            raw_dir.name, len(results)
        )
        return results

    def extract_pdf(self, pdf_path: Path) -> SigeAceta | None:
        meta = _parse_sige_filename(pdf_path.name)
        if meta is None:
            logger.warning("Nombre de archivo SIGE no reconocido: %s", pdf_path.name)
            return None

        try:
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber no instalado: pip install pdfplumber --break-system-packages")
            return None

        warnings: list[str] = []
        frames: list[pd.DataFrame] = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    for table in page.extract_tables() or []:
                        if len(table) < 2:
                            continue
                        header = [
                            str(c).strip() if c else f"col_{i}"
                            for i, c in enumerate(table[0])
                        ]
                        df = pd.DataFrame(table[1:], columns=header).astype(str)
                        df.columns = [
                            re.sub(r"\s+", "_", c.lower()) for c in df.columns
                        ]
                        df = df.dropna(how="all").reset_index(drop=True)
                        if len(df) > 0:
                            frames.append(df)
        except Exception as exc:
            logger.error("Error extrayendo %s: %s", pdf_path.name, exc)
            return None

        if not frames:
            warnings.append("No se encontraron tablas en el PDF")
            combined = pd.DataFrame()
        else:
            combined = pd.concat(frames, ignore_index=True)

        # Agregar columnas de metadatos
        combined["rbd"]        = meta["rbd"]
        combined["grado"]      = meta["grado"]
        combined["letra"]      = meta["letra"]
        combined["agno"]       = meta["agno"]
        combined["fecha_acta"] = meta["fecha_acta"]

        return SigeAceta(
            rbd=meta["rbd"], grado=meta["grado"], letra=meta["letra"],
            agno=meta["agno"], fecha_acta=meta["fecha_acta"],
            df=combined, source=pdf_path, warnings=warnings,
        )

    def _export(self, acta: SigeAceta) -> Path:
        fname  = f"sige__{acta.rbd}__{acta.agno}__g{acta.grado}{acta.letra}.csv"
        output = self.output_dir / fname
        acta.df.to_csv(output, index=False, sep=";", encoding="utf-8-sig")
        logger.info("Exportado: %s (%d filas)", output.name, len(acta.df))
        return output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sige_filename(filename: str) -> dict | None:
    m = _SIGE_NAME_RE.match(filename)
    if not m:
        return None
    fecha_raw = m.group("fecha")   # ddmmyyyy
    fecha_fmt = f"{fecha_raw[:2]}/{fecha_raw[2:4]}/{fecha_raw[4:]}"
    agno      = int(fecha_raw[4:])
    return {
        "rbd":       m.group("rbd"),
        "grado":     int(m.group("grado")),
        "letra":     m.group("letra").upper(),
        "agno":      agno,
        "fecha_acta": fecha_fmt,
    }