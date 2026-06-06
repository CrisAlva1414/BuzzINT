"""
normalizers/establecimientos.py
================================
Normalizer para el Directorio Oficial de Establecimientos Educacionales
(mineduc/raw/establecimientos/).

Rango de cobertura: 1992–2025.

Heterogeneidad por período:
  1992–1997  RARs — contenido desconocido hasta inspección. Probablemente DBF
             o formato propietario. El Ingestor no los puede abrir → se loguean
             como no-soportados y se dejan para manejo manual.
  1998–2003  ZIPs con .xls + .doc/.docx de libro de códigos
             Nota 2003: ZIP anidado con subcarpeta Directoriooficial2003/
  2004–2012  CSVs directos
  2013–2022  RARs modernos (contenido tabular estándar)
  2023–2025  RARs modernos

Esquema canónico de salida: columnas estables en el tiempo.
RBD es la primary key — se normaliza a string de 8 dígitos.
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from .base import BaseNormalizer, ColumnSpec
from .ingestor import IngestedFile

logger = logging.getLogger(__name__)


class EstablecimientosNormalizer(BaseNormalizer):

    SOURCE_NAME = "establecimientos"

    SCHEMA = [
        ColumnSpec("rbd",           dtype="str",   required=True),
        ColumnSpec("agno",          dtype="int",   required=True),
        ColumnSpec("nombre_estab",  dtype="str",   required=True),
        ColumnSpec("cod_region",    dtype="str",   required=False),
        ColumnSpec("nom_region",    dtype="str",   required=False),
        ColumnSpec("cod_provincia", dtype="str",   required=False),
        ColumnSpec("nom_provincia", dtype="str",   required=False),
        ColumnSpec("cod_comuna",    dtype="str",   required=False),
        ColumnSpec("nom_comuna",    dtype="str",   required=False),
        ColumnSpec("dependencia",   dtype="str",   required=False),
        ColumnSpec("cod_depe2",     dtype="str",   required=False),
        ColumnSpec("estado_estab",  dtype="str",   required=False),
        ColumnSpec("cod_ensenanza", dtype="str",   required=False),
        ColumnSpec("rural_sn",      dtype="str",   required=False),
        ColumnSpec("latitud",       dtype="float", required=False),
        ColumnSpec("longitud",      dtype="float", required=False),
        ColumnSpec("telefono",      dtype="str",   required=False),
        ColumnSpec("mail",          dtype="str",   required=False),
        ColumnSpec("direccion",     dtype="str",   required=False),
    ]

    # Mapeo exhaustivo de variantes históricas
    COLUMN_MAP = {
        # RBD
        "rbd":                       "rbd",
        "cod_rbd":                   "rbd",
        "codigo":                    "rbd",

        # Nombre
        "nom_rbd":                   "nombre_estab",
        "nombre":                    "nombre_estab",
        "nombre_establecimiento":    "nombre_estab",

        # Región
        "cod_reg_rbd":               "cod_region",
        "cod_reg":                   "cod_region",
        "region":                    "cod_region",
        "cod_region":                "cod_region",
        "nom_reg_rbd":               "nom_region",
        "nom_region":                "nom_region",

        # Provincia
        "cod_pro_rbd":               "cod_provincia",
        "cod_provincia":             "cod_provincia",
        "nom_pro_rbd":               "nom_provincia",
        "nom_provincia":             "nom_provincia",

        # Comuna
        "cod_com_rbd":               "cod_comuna",
        "cod_com":                   "cod_comuna",
        "cod_comuna":                "cod_comuna",
        "nom_com_rbd":               "nom_comuna",
        "nom_com":                   "nom_comuna",
        "nom_comuna":                "nom_comuna",

        # Dependencia
        "cod_depe":                  "dependencia",
        "dependencia":               "dependencia",
        "cod_depe2":                 "cod_depe2",

        # Estado
        "estado_estab":              "estado_estab",
        "estado":                    "estado_estab",

        # Enseñanza
        "cod_ense":                  "cod_ensenanza",
        "cod_ensenanza":             "cod_ensenanza",

        # Rural
        "rural_sn":                  "rural_sn",
        "rural":                     "rural_sn",

        # Geo
        "latitud":                   "latitud",
        "lat":                       "latitud",
        "longitud":                  "longitud",
        "lon":                       "longitud",
        "lng":                       "longitud",

        # Contacto
        "telefono":                  "telefono",
        "fono":                      "telefono",
        "mail":                      "mail",
        "email":                     "mail",
        "direccion":                 "direccion",
        "dir":                       "direccion",
    }

    def normalize_df(self, df: pd.DataFrame, ingested: IngestedFile) -> pd.DataFrame:
        # Año desde el nombre de archivo
        if "agno" not in df.columns or df["agno"].isna().all():
            agno = _infer_year(ingested.source_path, ingested.inner_path)
            df["agno"] = agno

        # Limpiar RBD
        if "rbd" in df.columns:
            df["rbd"] = (
                df["rbd"].astype(str)
                .str.extract(r"(\d+)")[0]
                .str.zfill(8)
            )
            # Eliminar filas sin RBD válido (subtotales, encabezados duplicados)
            df = df[df["rbd"].str.match(r"^\d{8}$", na=False)]

        # Normalizar dependencia a códigos estándar
        if "dependencia" in df.columns:
            df["dependencia"] = df["dependencia"].map(_norm_dependencia).fillna(df["dependencia"])

        # Latitud/longitud: reemplazar coma decimal chilena
        for col in ("latitud", "longitud"):
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace(",", ".", regex=False)

        return df


_DEPENDENCIA_MAP = {
    "1": "CORP_MUNICIPAL", "municipal": "CORP_MUNICIPAL",
    "2": "PARTICULAR_SUBVENCIONADO", "part. subv.": "PARTICULAR_SUBVENCIONADO",
    "3": "PARTICULAR_PAGADO", "part. pagado": "PARTICULAR_PAGADO",
    "4": "CORP_ADM_DELEGADA",
    "5": "SLEP", "slep": "SLEP",
}

def _norm_dependencia(v: str) -> str | None:
    return _DEPENDENCIA_MAP.get(str(v).lower().strip())

_YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")

def _infer_year(source_path, inner_path) -> int | None:
    for c in [str(source_path), str(inner_path or "")]:
        m = _YEAR_RE.search(c)
        if m:
            y = int(m.group(1))
            if 1990 <= y <= 2030:
                return y
    return None