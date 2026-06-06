"""
normalizers/evaluacion.py
==========================
Normalizer para Evaluación Docente (mineduc/raw/evaluacion/).

Estructura del Bronze:
  - ZIPs (2004–2019) y RARs (2017, 2020–2024) por año
  - Cada contenedor tiene 2 o 3 datasets temáticos:
      · "bbdd indicadores"           → resultados por dimensión global
      · "bbdd resultado por dim."    → detalle por dimensión
      · "bbdd cuestionario compl."   → cuestionario complementario
  - Cada dataset viene como par: {tema}_datos.xls + {tema}_codigos.xls
    (en años 2004–2011) o como un solo CSV (2012 en adelante).
  - Los _codigos son libros de código → el Ingestor los marca como is_codebook=True.

Genera tres CSVs por año (si hay datos):
    evaluacion_docente__{agno}__indicadores.csv
    evaluacion_docente__{agno}__dimensiones.csv
    evaluacion_docente__{agno}__cuestionario.csv
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from .base import BaseNormalizer, ColumnSpec
from .ingestor import IngestedFile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detección de tema por nombre de archivo
# ---------------------------------------------------------------------------

_TEMA_RE = re.compile(
    r"(indicador|resultado.por.dim|dimensi[oó]n|cuestionario)",
    re.IGNORECASE,
)

def _detect_tema(label: str) -> str:
    """Clasifica un IngestedFile en uno de los tres temas."""
    m = _TEMA_RE.search(label)
    if not m:
        return "desconocido"
    t = m.group(1).lower()
    if "indicador" in t:
        return "indicadores"
    if "dim" in t:
        return "dimensiones"
    if "cuestionario" in t:
        return "cuestionario"
    return "desconocido"


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

class EvaluacionDocenteNormalizer(BaseNormalizer):

    SOURCE_NAME = "evaluacion_docente"

    # Esquema mínimo común a los tres datasets
    SCHEMA = [
        ColumnSpec("rbd",         dtype="str",   required=False, description="RBD del establecimiento"),
        ColumnSpec("agno",        dtype="int",   required=True,  description="Año de evaluación"),
        ColumnSpec("tema",        dtype="str",   required=True,  description="Subtipo: indicadores/dimensiones/cuestionario"),
        ColumnSpec("id_docente",  dtype="str",   required=False, description="Identificador anónimo del docente"),
        ColumnSpec("resultado",   dtype="str",   required=False, description="Resultado global (Destacado/Competente/Básico/Insatisfactorio)"),
    ]

    # El COLUMN_MAP de Evaluación Docente es muy heterogéneo entre años.
    # Se define como callable para poder inspeccionarlo dinámicamente.
    COLUMN_MAP = None   # Sobreescrito en normalize_df

    def normalize_df(self, df: pd.DataFrame, ingested: IngestedFile) -> pd.DataFrame:
        warnings: list[str] = []

        # 1. Inferir año
        agno = _infer_year_from_path(ingested.source_path, ingested.inner_path)
        df["agno"] = agno

        # 2. Inferir tema
        df["tema"] = _detect_tema(ingested.label)

        # 3. Aplicar mapeo dinámico de columnas según las columnas presentes
        df = _apply_dynamic_map(df, _EVALUACION_COL_MAP)

        # 4. Limpiar RBD si existe
        if "rbd" in df.columns:
            df["rbd"] = (
                df["rbd"].astype(str)
                .str.extract(r"(\d+)")[0]
                .str.zfill(8)
            )

        return df

    def _build_output_path(self, ingested):
        """Sobreescribir para incluir el tema en el nombre de salida."""
        import re as _re
        agno = _infer_year_from_path(ingested.source_path, ingested.inner_path) or "XXXX"
        tema = _detect_tema(ingested.label)
        fname = f"evaluacion_docente__{agno}__{tema}.csv"
        fname = _re.sub(r"[^\w\.\-]", "_", fname)
        return self.output_dir / fname


# ---------------------------------------------------------------------------
# Mapeo dinámico de columnas (cubre variantes 2004–2024)
# ---------------------------------------------------------------------------

_EVALUACION_COL_MAP: dict[str, str] = {
    # Identificación docente
    "id_docente":     "id_docente",
    "folio":          "id_docente",
    "rut_docente":    "id_docente",
    "id":             "id_docente",

    # RBD / establecimiento
    "rbd":            "rbd",
    "cod_rbd":        "rbd",
    "cod_estab":      "rbd",

    # Resultado final
    "resultado":      "resultado",
    "nivel_global":   "resultado",
    "resultado_final":"resultado",
    "nivel_final":    "resultado",
    "cat_global":     "resultado",
}


def _apply_dynamic_map(df: pd.DataFrame, col_map: dict[str, str]) -> pd.DataFrame:
    normalized = {k.lower().strip(): v for k, v in col_map.items()}
    rename = {
        col: normalized[col.lower().strip()]
        for col in df.columns
        if col.lower().strip() in normalized
    }
    return df.rename(columns=rename)


_YEAR_RE = re.compile(r"(20\d{2}|\d{4})")

def _infer_year_from_path(source_path, inner_path) -> int | None:
    for candidate in [str(source_path), str(inner_path or "")]:
        m = _YEAR_RE.search(candidate)
        if m:
            year = int(m.group(1))
            if 2000 <= year <= 2030:
                return year
    return None