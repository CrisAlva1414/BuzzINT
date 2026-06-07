"""
normalizar_cargos.py
====================
Consolida todos los CSV/RAR de data/mineduc/raw/cargos en un único
data/mineduc/processed/mineduc_cargos.csv

Maneja:
  - Encodings mixtos (utf-8-sig, utf-8, latin-1, cp1252)
  - Separadores mixtos (;  ,  TAB  |)
  - Esquema variable por año (columnas aparecen/desaparecen)
  - DOC_FEC_NAC en formato AAAAMMDD (8 dígitos) o AAAAMM (6 dígitos)
  - Archivos RAR con CSVs internos
  - Procesamiento por chunks → memoria constante

Uso:
    python normalizar_cargos.py [--chunk-size 20000] [--raw-dir ...] [--out ...]
"""

import argparse
import os
import re
import sys
import tempfile
import logging
from pathlib import Path

import pandas as pd

# ──────────────────────────────────────────────
# CONSTANTES
# ──────────────────────────────────────────────
CHUNK_SIZE_DEFAULT   = 20_000
RAW_DIR_DEFAULT      = "data/mineduc/raw/cargos"
OUTPUT_DEFAULT       = "data/mineduc/processed/mineduc_cargos.csv"

ENCODINGS_FALLBACK   = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
SEPARADORES          = [";", ",", "\t", "|"]

# Orden preferido en el CSV final (columnas de identidad primero)
PRIORITY_COLS = [
    "agno", "rbd", "dgv_rbd", "nom_rbd",
    "cod_reg_rbd", "cod_pro_rbd", "cod_com_rbd", "nom_com_rbd",
    "cod_deprov_rbd", "nom_deprov_rbd",
    "cod_depe", "cod_depe2", "rural_rbd", "estado_estab",
    "clave", "mrun",
    "doc_genero", "doc_fec_nac",
]

META_COLS = ["_source_file"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# DETECCIÓN DE ENCODING / SEPARADOR
# ──────────────────────────────────────────────
def detectar_formato(path: str | Path) -> tuple[str, str]:
    """
    Devuelve (encoding, separador) que consiguen ≥2 columnas.
    Lee 200 filas para que caracteres especiales que no están en las
    primeras filas no engañen al detector (ej: latin-1 detectado como utf-8).
    Usa charset-normalizer si está disponible para mayor precisión.
    """
    # Intento rápido con charset-normalizer (más fiable que fuerza bruta)
    try:
        from charset_normalizer import from_path
        result = from_path(path).best()
        if result is not None:
            detected_enc = str(result.encoding)
            for sep in SEPARADORES:
                try:
                    df = pd.read_csv(path, encoding=detected_enc, sep=sep, nrows=5)
                    if df.shape[1] >= 2:
                        return detected_enc, sep
                except Exception:
                    pass
    except ImportError:
        pass

    # Fallback: fuerza bruta leyendo más filas para atrapar chars especiales
    for enc in ENCODINGS_FALLBACK:
        for sep in SEPARADORES:
            try:
                df = pd.read_csv(path, encoding=enc, sep=sep, nrows=200)
                if df.shape[1] >= 2:
                    return enc, sep
            except Exception:
                pass
    raise ValueError(f"No se pudo detectar formato para: {path}")


# ──────────────────────────────────────────────
# NORMALIZACIÓN DE NOMBRES DE COLUMNAS
# ──────────────────────────────────────────────
def normalizar_col(nombre: str) -> str:
    """AGNO → agno, GRADO.1_1 → grado_1_1"""
    return re.sub(r"[.\s]+", "_", nombre.strip().lower())


# ──────────────────────────────────────────────
# INFERIR AÑO DESDE NOMBRE DE ARCHIVO
# ──────────────────────────────────────────────
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)")

def inferir_agno(nombre_archivo: str) -> str | None:
    matches = _YEAR_RE.findall(nombre_archivo)
    # Tomar el año que parece "año de datos" (ej. 2013 en "Docentes_2013_...")
    # Si hay varios, preferir el segundo (fecha de datos, no de publicación)
    if len(matches) >= 2:
        return matches[1]
    if matches:
        return matches[0]
    return None


# ──────────────────────────────────────────────
# NORMALIZAR DOC_FEC_NAC
# ──────────────────────────────────────────────
def normalizar_fec_nac(serie: pd.Series) -> pd.Series:
    """
    AAAAMMDD (8 dígitos)  →  AAAA-MM-DD
    AAAAMM   (6 dígitos)  →  AAAA-MM
    Cualquier otra cosa   →  vacío
    """
    def _conv(val):
        if pd.isna(val):
            return ""
        s = str(val).strip().split(".")[0]   # quitar decimales si viene float
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        if len(s) == 6 and s.isdigit():
            return f"{s[:4]}-{s[4:]}"
        return s   # dejar como está si no encaja
    return serie.map(_conv)


# ──────────────────────────────────────────────
# FASE 1: PRE-SCAN → construir esquema unificado
# ──────────────────────────────────────────────
def prescan_columnas(archivos: list[Path]) -> list[str]:
    """
    Lee solo el header de cada CSV (incluidos los dentro de RAR).
    Devuelve la lista ordenada de columnas canónicas.
    """
    todas: set[str] = set()

    for path in archivos:
        if path.suffix.lower() == ".rar":
            cols = _headers_de_rar(path)
        else:
            cols = _headers_de_csv(path)
        todas.update(cols)

    # Ordenar: primero las prioritarias (en orden), luego el resto alfabético
    ordenadas = []
    for c in PRIORITY_COLS:
        if c in todas:
            ordenadas.append(c)
    resto = sorted(todas - set(ordenadas) - set(META_COLS))
    ordenadas += resto
    ordenadas += META_COLS   # metadatos al final

    return ordenadas


def _headers_de_csv(path: Path) -> list[str]:
    try:
        enc, sep = detectar_formato(path)
        df = pd.read_csv(path, encoding=enc, sep=sep, nrows=0, encoding_errors="replace")
        return [normalizar_col(c) for c in df.columns]
    except Exception as e:
        log.warning(f"[pre-scan] {path.name}: {e}")
        return []


def _headers_de_rar(path: Path) -> list[str]:
    try:
        import rarfile
    except ImportError:
        log.error("rarfile no instalado: pip install rarfile")
        return []
    cols: list[str] = []
    try:
        with rarfile.RarFile(path) as rf:
            for entry in rf.infolist():
                if entry.filename.lower().endswith(".csv"):
                    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                        tmp_path = Path(tmp.name)
                    try:
                        rf.extract(entry, path=tmp_path.parent)
                        extracted = tmp_path.parent / entry.filename
                        cols += _headers_de_csv(extracted)
                    finally:
                        for f in [tmp_path, extracted]:
                            try:
                                f.unlink()
                            except Exception:
                                pass
    except Exception as e:
        log.warning(f"[pre-scan RAR] {path.name}: {e}")
    return cols


# ──────────────────────────────────────────────
# FASE 2: PROCESAMIENTO REAL
# ──────────────────────────────────────────────
def procesar_archivo(
    path: Path,
    esquema_final: list[str],
    output_path: Path,
    chunk_size: int,
    primer_archivo: bool,
) -> int:
    """Procesa un CSV suelto. Devuelve filas escritas."""
    total = 0
    try:
        enc, sep = detectar_formato(path)
        agno_inferido = inferir_agno(path.name)
    except Exception as e:
        log.error(f"[skip] {path.name}: {e}")
        return 0

    log.info(f"  → {path.name}  (enc={enc}, sep={repr(sep)}, agno_inf={agno_inferido})")

    reader = pd.read_csv(
        path,
        encoding=enc,
        sep=sep,
        chunksize=chunk_size,
        dtype=str,
        low_memory=False,
        encoding_errors="replace",  # carácter inválido → '?' en vez de crash
    )

    for chunk in reader:
        total += procesar_chunk(
            chunk,
            agno_inferido=agno_inferido,
            nombre_archivo=path.name,
            esquema_final=esquema_final,
            output_path=output_path,
            escribir_header=primer_archivo and (total == 0),
        )
        primer_archivo = False   # header solo en el primer chunk del primer archivo

    return total


def procesar_chunk(
    chunk: pd.DataFrame,
    agno_inferido: str | None,
    nombre_archivo: str,
    esquema_final: list[str],
    output_path: Path,
    escribir_header: bool,
) -> int:
    # 1. Normalizar nombres de columnas
    chunk.columns = [normalizar_col(c) for c in chunk.columns]

    # 2. Columna agno: si no existe, inyectar desde nombre de archivo
    if "agno" not in chunk.columns:
        chunk["agno"] = agno_inferido or ""

    # 3. Normalizar DOC_FEC_NAC
    if "doc_fec_nac" in chunk.columns:
        chunk["doc_fec_nac"] = normalizar_fec_nac(chunk["doc_fec_nac"])

    # 4. Metadato fuente
    chunk["_source_file"] = nombre_archivo

    # 5. Completar columnas ausentes del esquema final
    for col in esquema_final:
        if col not in chunk.columns:
            chunk[col] = ""

    # 6. Reordenar al esquema final
    chunk = chunk[esquema_final]

    # 7. Reemplazar NaN por cadena vacía
    chunk = chunk.fillna("")

    # 8. Escribir inmediatamente (modo append)
    chunk.to_csv(
        output_path,
        mode="a",
        index=False,
        header=escribir_header,
        encoding="utf-8-sig",
    )

    n = len(chunk)
    del chunk
    return n


def procesar_rar(
    path: Path,
    esquema_final: list[str],
    output_path: Path,
    chunk_size: int,
    primer_archivo: bool,
) -> int:
    try:
        import rarfile
    except ImportError:
        log.error("rarfile no instalado: pip install rarfile")
        return 0

    total = 0
    try:
        with rarfile.RarFile(path) as rf:
            for entry in rf.infolist():
                if not entry.filename.lower().endswith(".csv"):
                    continue
                with tempfile.TemporaryDirectory() as tmpdir:
                    rf.extract(entry, path=tmpdir)
                    extracted = Path(tmpdir) / entry.filename
                    filas = procesar_archivo(
                        extracted, esquema_final, output_path,
                        chunk_size, primer_archivo
                    )
                    total += filas
                    primer_archivo = False
    except Exception as e:
        log.error(f"[RAR] {path.name}: {e}")
    return total


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Normaliza CSVs MINEDUC cargos")
    parser.add_argument("--raw-dir",    default=RAW_DIR_DEFAULT)
    parser.add_argument("--out",        default=OUTPUT_DEFAULT)
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE_DEFAULT)
    args = parser.parse_args()

    raw_dir     = Path(args.raw_dir)
    output_path = Path(args.out)
    chunk_size  = args.chunk_size

    if not raw_dir.exists():
        log.error(f"Directorio no encontrado: {raw_dir}")
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Recopilar archivos
    archivos = sorted(
        p for p in raw_dir.iterdir()
        if p.suffix.lower() in {".csv", ".rar"}
    )
    if not archivos:
        log.error(f"No hay CSV ni RAR en {raw_dir}")
        sys.exit(1)

    log.info(f"Archivos encontrados: {len(archivos)}")

    # ── Fase 1: Pre-scan ──
    log.info("Fase 1: pre-scan de columnas …")
    esquema_final = prescan_columnas(archivos)
    log.info(f"  Esquema unificado: {len(esquema_final)} columnas")

    # Limpiar salida anterior
    if output_path.exists():
        output_path.unlink()

    # ── Fase 2: Procesamiento ──
    log.info("Fase 2: procesando archivos …")
    total_filas  = 0
    primer_archi = True

    for path in archivos:
        if path.suffix.lower() == ".rar":
            n = procesar_rar(path, esquema_final, output_path, chunk_size, primer_archi)
        else:
            n = procesar_archivo(path, esquema_final, output_path, chunk_size, primer_archi)

        total_filas += n
        primer_archi = False
        log.info(f"     {path.name}: {n:,} filas")

    log.info(f"✓ Listo. Total filas: {total_filas:,}")
    log.info(f"  Salida: {output_path}")


if __name__ == "__main__":
    main()