"""
normalizar_directorio_ee.py
────────────────────────────────────────────────────────────────────────────────
Normalización Bronze → Silver para archivos MINEDUC "Directorio Oficial de
Establecimientos Educacionales" (2013-2025).

Fuentes soportadas:
  • CSVs sueltos     (.csv)
  • RARs con CSVs   (.rar)

Uso:
    python normalizar_directorio_ee.py \
        --origen   /ruta/a/raw/establecimientos \
        --salida   /ruta/a/silver/directorio_ee.csv \
        --chunk    20000

Dependencias:
    pip install pandas rarfile chardet
    (rarfile requiere que `unrar` o `bsdtar` esté instalado en el sistema)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import logging
from pathlib import Path
from typing import Generator

import pandas as pd
import rarfile

# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTES DE CONFIGURACIÓN
# ──────────────────────────────────────────────────────────────────────────────

CHUNK_SIZE_DEFAULT: int = 20_000

# Orden prioritario de columnas en el esquema final.
# Cualquier columna extra (ESPE_*, MAT_ENS_*) se añade al final.
SCHEMA_PRIORITY: list[str] = [
    "agno",
    "rbd",
    "dgv_rbd",
    "nom_rbd",
    "mrun",
    "rut_sostenedor",
    "p_juridica",
    "cod_reg_rbd",
    "nom_reg_rbd_a",
    "cod_pro_rbd",
    "cod_com_rbd",
    "nom_com_rbd",
    "cod_deprov_rbd",
    "nom_deprov_rbd",
    "cod_depe",
    "cod_depe2",
    "rural_rbd",
    "latitud",
    "longitud",
    "convenio_pie",
    "pace",
    "ens_01", "ens_02", "ens_03", "ens_04", "ens_05",
    "ens_06", "ens_07", "ens_08", "ens_09", "ens_10", "ens_11",
    "mat_ens_1", "mat_ens_2", "mat_ens_3", "mat_ens_4",
    "mat_ens_5", "mat_ens_6", "mat_ens_7", "mat_ens_8",
    "mat_total",
    "matricula",
    "estado_estab",
    "ori_religiosa",
    "ori_otro_glosa",
    "pago_matricula",
    "pago_mensual",
    "espe_01", "espe_02", "espe_03", "espe_04", "espe_05",
    "espe_06", "espe_07", "espe_08", "espe_09", "espe_10", "espe_11",
    # metadatos inyectados
    "_source_file",
]

# Separadores a probar (en orden de probabilidad para estos archivos)
SEPARADORES: list[str] = [";", ",", "\t", "|"]

# Encodings a probar en orden
ENCODINGS_FALLBACK: list[str] = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]

# BOM alias: columnas con nombre roto por BOM que mapean a su canónico
BOM_ALIASES: dict[str, str] = {
    "ïagno": "agno",
    "\ufeffagno": "agno",
}

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# UTILIDADES
# ──────────────────────────────────────────────────────────────────────────────

def normalizar_nombre_columna(col: str) -> str:
    """
    Convierte un nombre de columna a su forma canónica lowercase, sin BOM.
    Ejemplo: 'ïagno' → 'agno', 'AGNO' → 'agno', 'NOM_REG_RBD_A' → 'nom_reg_rbd_a'
    """
    col = col.strip()
    # Resolver alias de BOM primero
    if col in BOM_ALIASES:
        return BOM_ALIASES[col]
    # Eliminar BOM unicode residual en cualquier posición inicial
    col = col.lstrip("\ufeff\ufffe\xef\xbb\xbf")
    return col.lower()


def detectar_formato(ruta: str | Path, nrows_probe: int = 5) -> tuple[str, str]:
    """
    Detecta encoding y separador de un CSV probando combinaciones.
    Retorna (encoding, separador) o lanza RuntimeError.
    """
    ruta = Path(ruta)
    for enc in ENCODINGS_FALLBACK:
        for sep in SEPARADORES:
            try:
                df_probe = pd.read_csv(
                    ruta,
                    encoding=enc,
                    sep=sep,
                    nrows=nrows_probe,
                    dtype=str,
                    on_bad_lines="skip",
                )
                if len(df_probe.columns) >= 2:
                    return enc, sep
            except Exception:
                continue
    raise RuntimeError(f"No se pudo detectar formato para: {ruta}")


def inferir_agno(nombre_archivo: str) -> str | None:
    """
    Extrae el año de 4 dígitos desde el nombre del archivo.
    Ej: '20220914_Directorio_Oficial_EE_2022_...' → '2022'
        'Directorio_oficial_EE_2016.csv'          → '2016'
    """
    # Busca patrón de año ≥ 2000 en el nombre
    matches = re.findall(r"(20\d{2})", nombre_archivo)
    if matches:
        # Prefiere el año de 4 dígitos que no sea la fecha de publicación
        # (el primero en nombres modernos es la fecha YYYYMMDD, el segundo el año)
        if len(matches) >= 2:
            return matches[1]
        return matches[0]
    return None


def normalizar_latlon(valor: str) -> str:
    """
    Normaliza coordenadas que usan coma decimal (ej: '-18,4872') a punto.
    Preserva valores con punto o vacíos.
    """
    if pd.isna(valor) or str(valor).strip() == "":
        return ""
    v = str(valor).strip()
    # Detectar si tiene coma como separador decimal (no como separador de miles)
    # Patrón: signo opcional, dígitos, coma, dígitos
    if re.match(r"^-?\d+,\d+$", v):
        return v.replace(",", ".")
    return v


# ──────────────────────────────────────────────────────────────────────────────
# FASE 1 – PRE-SCAN DE COLUMNAS
# ──────────────────────────────────────────────────────────────────────────────

def _leer_header_csv(ruta: Path) -> set[str]:
    """Lee únicamente el header de un CSV y devuelve columnas normalizadas."""
    enc, sep = detectar_formato(ruta)
    df_head = pd.read_csv(ruta, encoding=enc, sep=sep, nrows=0, dtype=str)
    return {normalizar_nombre_columna(c) for c in df_head.columns}


def prescan_columnas(directorio: Path) -> list[str]:
    """
    Fase 1: recorre todos los archivos (CSV y RAR) leyendo sólo el header.
    Construye y ordena el esquema final de columnas.
    """
    log.info("── FASE 1: Pre-scan de columnas ──")
    columnas_detectadas: set[str] = set()

    for ruta in _iterar_archivos(directorio):
        try:
            cols = _leer_header_csv(ruta)
            columnas_detectadas.update(cols)
            log.debug("  Header leído: %s → %d cols", ruta.name, len(cols))
        except Exception as e:
            log.warning("  Skipping header de %s: %s", ruta.name, e)

    # Construir esquema: primero las columnas en SCHEMA_PRIORITY que existan,
    # luego el resto ordenado alfabéticamente (columnas no conocidas del futuro).
    columnas_prioritarias = [c for c in SCHEMA_PRIORITY if c in columnas_detectadas]
    columnas_extra = sorted(
        columnas_detectadas - set(SCHEMA_PRIORITY) - {"_source_file"}
    )
    esquema = columnas_prioritarias + columnas_extra

    # Asegurar metadatos obligatorios al final si no están
    if "_source_file" not in esquema:
        esquema.append("_source_file")

    log.info("  Esquema final: %d columnas detectadas.", len(esquema))
    return esquema


# ──────────────────────────────────────────────────────────────────────────────
# ITERADOR DE ARCHIVOS (CSV directo o extraído de RAR)
# ──────────────────────────────────────────────────────────────────────────────

def _iterar_archivos(directorio: Path) -> Generator[Path, None, None]:
    """
    Genera rutas de CSVs procesables: los sueltos y los extraídos de RARs
    (temporalmente).  Los temporales se limpian automáticamente.
    """
    for ruta in sorted(directorio.iterdir()):
        if ruta.suffix.lower() == ".csv":
            yield ruta
        elif ruta.suffix.lower() == ".rar":
            yield from _extraer_csvs_de_rar(ruta)


def _extraer_csvs_de_rar(ruta_rar: Path) -> Generator[Path, None, None]:
    """
    Extrae cada CSV de un RAR en un directorio temporal y lo devuelve.
    Limpia cada temporal después de que el caller lo procese.
    """
    try:
        with rarfile.RarFile(str(ruta_rar)) as rf:
            nombres_csv = [n for n in rf.namelist() if n.lower().endswith(".csv")]
            for nombre in nombres_csv:
                with tempfile.TemporaryDirectory() as tmpdir:
                    rf.extract(nombre, path=tmpdir)
                    ruta_csv = Path(tmpdir) / nombre
                    log.info("  Extraído de RAR: %s", nombre)
                    yield ruta_csv
                    # Al salir del with, tmpdir se elimina automáticamente
    except Exception as e:
        log.error("Error al abrir RAR %s: %s", ruta_rar.name, e)


# ──────────────────────────────────────────────────────────────────────────────
# FASE 2 – PROCESAMIENTO REAL
# ──────────────────────────────────────────────────────────────────────────────

def procesar_archivo(
    ruta: Path,
    esquema_final: list[str],
    archivo_salida: Path,
    chunk_size: int,
    primera_escritura: bool,
) -> tuple[int, bool]:
    """
    Procesa un CSV en chunks y escribe al archivo de salida.
    Retorna (filas_procesadas, nueva_primera_escritura).
    """
    nombre_archivo = ruta.name

    try:
        enc, sep = detectar_formato(ruta)
    except RuntimeError as e:
        log.error("  %s — ignorado: %s", nombre_archivo, e)
        return 0, primera_escritura

    agno_inferido = inferir_agno(nombre_archivo)
    if agno_inferido:
        log.info("  Procesando: %s (año inferido: %s)", nombre_archivo, agno_inferido)
    else:
        log.info("  Procesando: %s (año no inferido desde nombre)", nombre_archivo)

    total_filas = 0

    try:
        reader = pd.read_csv(
            ruta,
            encoding=enc,
            sep=sep,
            dtype=str,           # Todo como string para evitar coerciones
            chunksize=chunk_size,
            on_bad_lines="skip",
        )

        for chunk in reader:
            chunk, primera_escritura = procesar_chunk(
                chunk=chunk,
                agno_inferido=agno_inferido,
                nombre_archivo=nombre_archivo,
                esquema_final=esquema_final,
                archivo_salida=archivo_salida,
                primera_escritura=primera_escritura,
            )
            total_filas += len(chunk)
            # Liberar memoria explícitamente
            del chunk

    except Exception as e:
        log.error("  Error procesando %s: %s", nombre_archivo, e)

    log.info("  → %d filas escritas desde %s", total_filas, nombre_archivo)
    return total_filas, primera_escritura


def procesar_chunk(
    chunk: pd.DataFrame,
    agno_inferido: str | None,
    nombre_archivo: str,
    esquema_final: list[str],
    archivo_salida: Path,
    primera_escritura: bool,
) -> tuple[pd.DataFrame, bool]:
    """
    Transforma un chunk y lo escribe al CSV de salida.
    Retorna (chunk_procesado, nueva_primera_escritura).
    """
    # ── 1. Normalizar nombres de columnas ────────────────────────────────────
    chunk.columns = [normalizar_nombre_columna(c) for c in chunk.columns]

    # ── 2. Resolver año ──────────────────────────────────────────────────────
    if "agno" not in chunk.columns:
        if agno_inferido:
            chunk["agno"] = agno_inferido
        else:
            chunk["agno"] = ""
    else:
        # Asegurar que agno no esté vacío para filas donde viene del CSV
        if agno_inferido:
            chunk["agno"] = chunk["agno"].fillna("").replace("", agno_inferido)

    # ── 3. Normalizar LATITUD / LONGITUD (coma → punto decimal) ─────────────
    for col_geo in ("latitud", "longitud"):
        if col_geo in chunk.columns:
            chunk[col_geo] = chunk[col_geo].apply(normalizar_latlon)

    # ── 4. Inyectar metadato _source_file ────────────────────────────────────
    chunk["_source_file"] = nombre_archivo

    # ── 5. Completar columnas ausentes con "" ────────────────────────────────
    for col in esquema_final:
        if col not in chunk.columns:
            chunk[col] = ""

    # ── 6. Reordenar al esquema final (columnas extra del chunk van al final) ─
    cols_en_esquema = [c for c in esquema_final if c in chunk.columns]
    cols_extra = [c for c in chunk.columns if c not in set(esquema_final)]
    chunk = chunk[cols_en_esquema + cols_extra]

    # ── 7. Reemplazar NaN por "" ─────────────────────────────────────────────
    chunk = chunk.fillna("")

    # ── 8. Escribir al archivo de salida ────────────────────────────────────
    chunk.to_csv(
        archivo_salida,
        mode="a",
        index=False,
        header=primera_escritura,   # Header solo en la primera escritura
        encoding="utf-8",
        lineterminator="\n",
    )

    return chunk, False   # primera_escritura = False a partir de aquí


# ──────────────────────────────────────────────────────────────────────────────
# ENTRYPOINT PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────────

def main(directorio_origen: str, archivo_salida: str, chunk_size: int) -> None:
    dir_origen = Path(directorio_origen)
    ruta_salida = Path(archivo_salida)

    if not dir_origen.is_dir():
        log.error("El directorio de origen no existe: %s", dir_origen)
        sys.exit(1)

    ruta_salida.parent.mkdir(parents=True, exist_ok=True)

    # Eliminar salida previa si existe
    if ruta_salida.exists():
        ruta_salida.unlink()
        log.info("Archivo de salida previo eliminado.")

    # ── FASE 1: Pre-scan ─────────────────────────────────────────────────────
    esquema_final = prescan_columnas(dir_origen)
    log.info("Columnas en esquema: %s", esquema_final)

    # ── FASE 2: Procesamiento real ───────────────────────────────────────────
    log.info("── FASE 2: Procesamiento real (chunk_size=%d) ──", chunk_size)
    total_global = 0
    primera_escritura = True

    for ruta_csv in _iterar_archivos(dir_origen):
        filas, primera_escritura = procesar_archivo(
            ruta=ruta_csv,
            esquema_final=esquema_final,
            archivo_salida=ruta_salida,
            chunk_size=chunk_size,
            primera_escritura=primera_escritura,
        )
        total_global += filas

    log.info("────────────────────────────────────────────────────")
    log.info("PROCESO COMPLETADO")
    log.info("  Archivo de salida : %s", ruta_salida)
    log.info("  Total filas escritas: %d", total_global)
    log.info("────────────────────────────────────────────────────")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Normaliza CSVs del Directorio Oficial de Establecimientos MINEDUC."
    )
    parser.add_argument(
        "--input",
        default="data/mineduc/raw/establecimientos",
        help="Directorio con los archivos .csv y/o .rar de origen.",
    )
    parser.add_argument(
        "--output",
        default="data/mineduc/processed/mineduc_establecimientos.csv",
        help="Ruta del CSV de salida normalizado.",
    )
    parser.add_argument(
        "--chunk",
        type=int,
        default=CHUNK_SIZE_DEFAULT,
        help=f"Filas por chunk (default: {CHUNK_SIZE_DEFAULT}).",
    )
    args = parser.parse_args()
    main(
        directorio_origen=args.input,
        archivo_salida=args.output,
        chunk_size=args.chunk,
    )