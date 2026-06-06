"""
scraper/normalizers/csv_normalizer.py
──────────────────────────────────────
Normaliza y consolida los CSVs de Alumnos SEP (Prioritarios / Preferentes /
Beneficiarios) desde una carpeta de RARs o CSVs ya extraídos hacia un único
DataFrame / CSV unificado.

Compatibilidad probada con datos reales 2008–2025 (18 archivos, ~30M filas):

  Año    Cols  Issues conocidos
  ────  ─────  ────────────────────────────────────────────────────────────────
  2008     22  LET_RBD / NUM_RBD extras (legacy); sin PRIORITARIO/PREFERENTE
  2009     22  ídem
  2010     20  sin PRIORITARIO/PREFERENTE; sin LET_RBD/NUM_RBD
  2011     20  sin PRIORITARIO/PREFERENTE
  2013     20  sin PRIORITARIO/PREFERENTE
  2014     24  sin PRIORITARIO/PREFERENTE; sin DEPROV/EE_GRATUITO
  2015     29  sin PRIORITARIO/PREFERENTE; sin ESTADO_ESTAB
  2016–23  33  esquema estable
  2020     33  BOM (\\ufeff) en primera columna (AGNO) → stripeado automático
  2024     36  3 columnas nuevas: FEC_DEFUN_ALU, NOM_REG_RBD_A, NOMBRE_SLEP
  2025     34  NOMBRE_SLEP presente; FEC_DEFUN_ALU / NOM_REG_RBD_A ausentes

  FEC_NAC_ALU:
    2008–2013  →  YYYYMMDD  (8 dígitos)   →  normalizado a YYYY-MM-DD
    2014+      →  YYYYMM    (6 dígitos)   →  normalizado a YYYY-MM (sin día)

  NOM_RBD puede contener comas sin quoting RFC-4180 en algunos registros.
  Esas filas se omiten con advertencia (típicamente < 20 filas en 3 M).

Uso standalone:
    python csv_normalizer.py --input data/mineduc/raw/alumnos
    python csv_normalizer.py --input data/mineduc/raw/alumnos --no-rar --verbose
    python csv_normalizer.py --input data/mineduc/raw/alumnos \\
        --output data/mineduc/silver/sep_alumnos.csv
"""

from __future__ import annotations

import argparse
import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

import pandas as pd
import rarfile
from charset_normalizer import from_bytes  # incluido vía httpx

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Contrato base (stub hasta que normalizers/base.py esté implementado)
# ─────────────────────────────────────────────────────────────────────────────

class BaseNormalizer:
    """Stub mínimo. Reemplazar con la clase definitiva de normalizers/base.py."""

    def normalize(self, source_dir: Path, **kwargs) -> pd.DataFrame:
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

# Tamaño de chunk para leer CSVs grandes (filas por chunk).
# A ~150 bytes/fila promedio: 100_000 filas ≈ 15 MB de RAM por chunk.
# Ajustar si el host tiene poca RAM disponible.
CHUNK_SIZE = 100_000

# Mapa variante_en_csv → nombre_canónico.
# Agregar aquí cualquier alias nuevo que aparezca en versiones futuras.
COLUMN_ALIASES: dict[str, str] = {
    # ── Año ───────────────────────────────────────────────────────────────────
    "agno":                 "agno",
    "anio":                 "agno",
    "año":                  "agno",
    "year":                 "agno",
    # ── MRUN ─────────────────────────────────────────────────────────────────
    "mrun":                 "mrun",
    "cod_mrun":             "mrun",
    # ── Establecimiento ───────────────────────────────────────────────────────
    "rbd":                  "rbd",
    "cod_rbd":              "rbd",
    "dgv_rbd":              "dgv_rbd",
    "nom_rbd":              "nom_rbd",
    "nombre_rbd":           "nom_rbd",
    # Legacy 2008-2009
    "let_rbd":              "let_rbd",
    "num_rbd":              "num_rbd",
    # ── Alumno ────────────────────────────────────────────────────────────────
    "gen_alu":              "gen_alu",
    "cod_gen_alu":          "gen_alu",
    "sexo":                 "gen_alu",
    "fec_nac_alu":          "fec_nac_alu",
    "fec_defun_alu":        "fec_defun_alu",          # nuevo en 2024
    # ── Condición SEP ─────────────────────────────────────────────────────────
    "criterio_sep":         "criterio_sep",
    "cod_sep":              "criterio_sep",
    "condicion_sep":        "criterio_sep",
    "condicion":            "criterio_sep",
    "prioritario_alu":      "prioritario_alu",
    "preferente_alu":       "preferente_alu",
    "ben_sep":              "ben_sep",
    # ── Establecimiento SEP ───────────────────────────────────────────────────
    "convenio_sep":         "convenio_sep",
    "año_ingreso_sep":      "año_ingreso_sep",
    "clasificacion_sep":    "clasificacion_sep",
    "ee_gratuito":          "ee_gratuito",
    "estado_estab":         "estado_estab",
    "grado_sep":            "grado_sep",
    # ── Geografía ─────────────────────────────────────────────────────────────
    "cod_reg_rbd":          "cod_reg_rbd",
    "cod_region":           "cod_reg_rbd",
    "region":               "cod_reg_rbd",
    "nom_reg_rbd_a":        "nom_reg_rbd_a",          # nuevo en 2024
    "cod_pro_rbd":          "cod_pro_rbd",
    "cod_provincia":        "cod_pro_rbd",
    "cod_com_rbd":          "cod_com_rbd",
    "cod_comuna_rbd":       "cod_com_rbd",
    "nom_com_rbd":          "nom_com_rbd",
    "cod_deprov_rbd":       "cod_deprov_rbd",
    "nom_deprov_rbd":       "nom_deprov_rbd",
    # ── Dependencia / Zona ────────────────────────────────────────────────────
    "cod_depe":             "cod_depe",
    "dependencia":          "cod_depe",
    "cod_depe2":            "cod_depe2",
    "rural_rbd":            "rural_rbd",
    "cod_rural":            "rural_rbd",
    "nombre_slep":          "nombre_slep",            # nuevo en 2024
    # ── Enseñanza / Grado ─────────────────────────────────────────────────────
    "cod_ense":             "cod_ense",
    "cod_ense2":            "cod_ense2",
    "cod_ense3":            "cod_ense3",
    "cod_grado":            "cod_grado",
    "grado":                "cod_grado",
    "cod_grado2":           "cod_grado2",
    "let_cur":              "let_cur",
    "cod_jor":              "cod_jor",
}

# Columnas en orden preferido para el CSV final.
# Columnas no listadas se anexan al final (extensiones futuras).
OUTPUT_COLUMN_ORDER: list[str] = [
    "agno", "mrun", "gen_alu", "fec_nac_alu", "fec_defun_alu",
    "criterio_sep", "prioritario_alu", "preferente_alu", "ben_sep",
    "rbd", "dgv_rbd", "nom_rbd",
    "cod_reg_rbd", "nom_reg_rbd_a", "cod_pro_rbd", "cod_com_rbd", "nom_com_rbd",
    "cod_deprov_rbd", "nom_deprov_rbd",
    "cod_depe", "cod_depe2", "rural_rbd", "estado_estab",
    "nombre_slep", "convenio_sep", "año_ingreso_sep", "clasificacion_sep", "ee_gratuito",
    "cod_ense", "cod_ense2", "cod_ense3",
    "cod_grado", "cod_grado2", "let_cur", "cod_jor", "grado_sep",
    # Legacy (solo 2008-2009)
    "let_rbd", "num_rbd",
    # Provenance
    "_source_file",
]

# Separadores a probar en orden de prevalencia real en el dataset MINEDUC
_SEPARATORS: list[str] = [",", ";", "\t", "|"]

# Regex para inferir año desde nombre de archivo o carpeta
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


# ─────────────────────────────────────────────────────────────────────────────
# Normalización de FEC_NAC_ALU
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_fec_nac(series: pd.Series) -> pd.Series:
    """
    Unifica el formato de fecha de nacimiento:
      - 8 dígitos  (YYYYMMDD, 2008-2013) → 'YYYY-MM-DD'
      - 6 dígitos  (YYYYMM,   2014+)     → 'YYYY-MM'
      - cualquier otra cosa              → se deja tal cual
    """
    def _convert(val: str) -> str:
        v = str(val).strip()
        if len(v) == 8 and v.isdigit():
            return f"{v[:4]}-{v[4:6]}-{v[6:]}"
        if len(v) == 6 and v.isdigit():
            return f"{v[:4]}-{v[4:]}"
        return v
    return series.astype(str).map(_convert)


# ─────────────────────────────────────────────────────────────────────────────
# SepAlumnosNormalizer
# ─────────────────────────────────────────────────────────────────────────────

class SepAlumnosNormalizer(BaseNormalizer):
    """
    Consolida los CSVs anuales de Alumnos SEP en un DataFrame limpio y uniforme.

    Diseñado para ser agnóstico a:
    - Años presentes (2008–∞)
    - Separadores (,  ;  \\t  |)
    - Encodings y BOM (latin-1, utf-8, utf-8-sig, cp1252…)
    - Columnas legacy (LET_RBD, NUM_RBD en 2008-2009)
    - Columnas nuevas (FEC_DEFUN_ALU, NOM_REG_RBD_A, NOMBRE_SLEP desde 2024)
    - Formato de FEC_NAC_ALU (YYYYMMDD vs YYYYMM)
    - Filas con comas sin quoting en campos de texto (se omiten con advertencia)
    - Archivos de >3M filas (lectura por chunks para control de RAM)
    """

    def __init__(
        self,
        extract_dir: Optional[Path] = None,
        forced_encoding: Optional[str] = None,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        self._extract_dir = extract_dir
        self._forced_encoding = forced_encoding
        self._chunk_size = chunk_size
        self._tmp_ctx: Optional[tempfile.TemporaryDirectory] = None  # type: ignore[type-arg]

    # ── Public API ────────────────────────────────────────────────────────────

    def normalize(self, source_dir: Path, no_rar: bool = False) -> pd.DataFrame:
        """
        Lee todos los CSVs de *source_dir* (extrayendo RARs si no se indica
        --no-rar) y devuelve un DataFrame consolidado.
        """
        csv_files = self._collect_csvs(source_dir, no_rar=no_rar)
        if not csv_files:
            raise FileNotFoundError(f"No se encontraron CSVs en {source_dir}")

        frames: list[pd.DataFrame] = []
        skipped: list[Path] = []

        for path in csv_files:
            df = self._process_single(path)
            if df is not None:
                frames.append(df)
            else:
                skipped.append(path)

        if not frames:
            raise RuntimeError("Ningún CSV pudo procesarse correctamente.")

        if skipped:
            log.warning("%d archivo(s) omitido(s):", len(skipped))
            for p in skipped:
                log.warning("  ✗ %s", p.name)

        unified = self._align_and_concat(frames)
        log.info(
            "Consolidación completada: %d filas × %d columnas | años: %s",
            len(unified), len(unified.columns),
            sorted(unified["agno"].dropna().unique().tolist()),
        )
        return unified

    def to_csv(self, df: pd.DataFrame, output_path: Path) -> None:
        """Escribe el DataFrame a CSV (utf-8-sig para compatibilidad con Excel)."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        log.info("CSV exportado → %s  (%d filas)", output_path, len(df))

    # ── RAR / CSV discovery ───────────────────────────────────────────────────

    def _collect_csvs(self, source_dir: Path, no_rar: bool) -> list[Path]:
        if no_rar:
            files = sorted(source_dir.rglob("*.csv"))
            log.info("Modo --no-rar: %d CSV(s) encontrados en %s", len(files), source_dir)
            return files

        rar_files = sorted(source_dir.rglob("*.rar"))
        log.info("%d RAR(s) encontrados en %s", len(rar_files), source_dir)

        extract_root = self._ensure_extract_dir()
        csv_paths: list[Path] = []
        for rar_path in rar_files:
            log.info("Extrayendo: %s", rar_path.name)
            extracted = self._extract_rar(rar_path, extract_root)
            if not extracted:
                log.warning("  Sin CSVs dentro de %s", rar_path.name)
            csv_paths.extend(extracted)
        return sorted(csv_paths)

    def _ensure_extract_dir(self) -> Path:
        if self._extract_dir:
            self._extract_dir.mkdir(parents=True, exist_ok=True)
            return self._extract_dir
        self._tmp_ctx = tempfile.TemporaryDirectory(prefix="sep_alumnos_")
        return Path(self._tmp_ctx.name)

    def cleanup(self) -> None:
        if self._tmp_ctx:
            self._tmp_ctx.cleanup()
            self._tmp_ctx = None

    def __enter__(self) -> "SepAlumnosNormalizer":
        return self

    def __exit__(self, *_) -> None:
        self.cleanup()

    # ── RAR extraction ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_rar(rar_path: Path, dest: Path) -> list[Path]:
        extracted: list[Path] = []
        try:
            with rarfile.RarFile(rar_path) as rf:
                for member in rf.infolist():
                    if member.filename.lower().endswith(".csv"):
                        rf.extract(member, dest)
                        extracted.append(dest / member.filename)
                        log.debug("  → %s", member.filename)
        except Exception as exc:
            log.error("  Error abriendo %s: %s", rar_path.name, exc)
        return extracted

    # ── Single-file processing ────────────────────────────────────────────────

    def _process_single(self, path: Path) -> Optional[pd.DataFrame]:
        log.info("Leyendo: %s", path.name)

        enc, sep = self._sniff(path)
        if enc is None or sep is None:
            log.warning("  ✗ No se pudo determinar encoding/separador: %s", path.name)
            return None

        df = self._read_chunked(path, enc, sep)
        if df is None:
            return None

        # 1. Strip BOM de nombres de columna (issue real en archivo 2020)
        df.columns = [c.lstrip("\ufeff") for c in df.columns]

        # 2. Normalizar nombres de columna → aliases canónicos
        df.columns = [self._normalize_col(c) for c in df.columns]

        # 3. Inferir agno si no está en el CSV
        if "agno" not in df.columns:
            year = self._infer_year(path)
            df["agno"] = year or "desconocido"
            log.debug("  agno inferido: %s", df["agno"].iloc[0])

        # 4. Normalizar FEC_NAC_ALU (YYYYMMDD vs YYYYMM)
        if "fec_nac_alu" in df.columns:
            df["fec_nac_alu"] = _normalize_fec_nac(df["fec_nac_alu"])

        # 5. Provenance
        df["_source_file"] = path.name

        log.info("  ✓ %d filas × %d cols", len(df), len(df.columns))
        return df

    # ── Encoding/separator detection (sniffer sobre las primeras N líneas) ────

    def _sniff(self, path: Path) -> tuple[Optional[str], Optional[str]]:
        """
        Lee solo el header (primera línea) para determinar separador y encoding
        sin cargar el archivo completo.
        Devuelve (encoding, separador) o (None, None) si falla todo.
        """
        encodings = (
            [self._forced_encoding]
            if self._forced_encoding
            else ["utf-8-sig", "utf-8", "latin-1", self._detect_encoding(path)]
        )

        for enc in dict.fromkeys(encodings):  # deduplica preservando orden
            for sep in _SEPARATORS:
                try:
                    # Leer solo 5 filas para validar que el separador da ≥2 columnas
                    df_head = pd.read_csv(
                        path,
                        sep=sep,
                        encoding=enc,
                        dtype=str,
                        nrows=5,
                        on_bad_lines="skip",
                    )
                    if df_head.shape[1] >= 2:
                        log.debug("  sniff → enc=%s sep=%r cols=%d", enc, sep, df_head.shape[1])
                        return enc, sep
                except Exception as exc:
                    log.debug("  sniff enc=%s sep=%r: %s", enc, sep, exc)

        return None, None

    # ── Chunked CSV reading ───────────────────────────────────────────────────

    def _read_chunked(
        self, path: Path, enc: str, sep: str
    ) -> Optional[pd.DataFrame]:
        """
        Lee el CSV en chunks para controlar el uso de RAM.
        Usa on_bad_lines='skip' para tolerar filas con comas sin quoting
        en campos de texto (NOM_RBD principalmente).
        Las líneas omitidas se reportan al final como advertencia de conteo.
        """
        chunks: list[pd.DataFrame] = []
        skipped_rows = 0

        # Capturamos las advertencias de pandas para contarlas en lugar de
        # imprimirlas una por una (pueden ser miles de líneas en stderr)
        import warnings

        try:
            reader = pd.read_csv(
                path,
                sep=sep,
                encoding=enc,
                dtype=str,
                on_bad_lines="skip",       # omite filas mal formadas en lugar de abortar
                chunksize=self._chunk_size,
                low_memory=False,
            )
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                for chunk in reader:
                    chunks.append(chunk)
                # Contar cuántas filas se saltaron (cada warning = 1 fila)
                skipped_rows = sum(
                    1 for w in caught
                    if issubclass(w.category, pd.errors.ParserWarning)
                )

        except Exception as exc:
            log.error("  Error leyendo %s: %s", path.name, exc)
            return None

        if not chunks:
            log.warning("  ✗ El archivo quedó vacío tras la lectura: %s", path.name)
            return None

        if skipped_rows > 0:
            log.warning(
                "  ⚠ %d fila(s) omitidas por formato incorrecto (comas sin quoting en texto)",
                skipped_rows,
            )

        return pd.concat(chunks, ignore_index=True)

    # ── Alignment + concat ────────────────────────────────────────────────────

    @staticmethod
    def _align_and_concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
        # Unión ordenada de todas las columnas encontradas entre todos los años
        seen: set[str] = set()
        all_cols: list[str] = []
        for df in frames:
            for c in df.columns:
                if c not in seen:
                    all_cols.append(c)
                    seen.add(c)

        # Columnas conocidas primero (en su orden preferido), extras al final
        priority = [c for c in OUTPUT_COLUMN_ORDER if c in seen]
        extra    = [c for c in all_cols if c not in set(OUTPUT_COLUMN_ORDER)]
        final_cols = priority + extra

        aligned = [df.reindex(columns=final_cols) for df in frames]
        unified = pd.concat(aligned, ignore_index=True, sort=False)

        if "agno" in unified.columns:
            unified = unified.sort_values("agno", kind="stable").reset_index(drop=True)

        return unified

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_col(name: str) -> str:
        clean = name.strip().lower().replace(" ", "_").replace("-", "_")
        return COLUMN_ALIASES.get(clean, clean)

    @staticmethod
    def _infer_year(path: Path) -> Optional[str]:
        for candidate in [path.stem, path.parent.name]:
            m = _YEAR_RE.search(candidate)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _detect_encoding(path: Path, sample_bytes: int = 65_536) -> str:
        raw = path.read_bytes()[:sample_bytes]
        result = from_bytes(raw).best()
        enc = str(result.encoding) if result else "latin-1"
        log.debug("  encoding detectado: %s", enc)
        return enc


# ─────────────────────────────────────────────────────────────────────────────
# CLI standalone
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Normaliza CSVs de Alumnos SEP en un único archivo unificado."
    )
    p.add_argument("--input",       default="data/mineduc/raw/alumnos",                   metavar="DIR")
    p.add_argument("--output",      default="data/mineduc/silver/sep_alumnos_unified.csv", metavar="FILE")
    p.add_argument("--extract-dir", default=None,                                          metavar="DIR")
    p.add_argument("--chunk-size",  default=CHUNK_SIZE, type=int,                         metavar="N",
                   help=f"Filas por chunk al leer CSVs grandes (default: {CHUNK_SIZE:,})")
    p.add_argument("--no-rar",   action="store_true")
    p.add_argument("--encoding", default=None, metavar="ENC")
    p.add_argument("--verbose",  action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    input_dir   = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    extract_dir = Path(args.extract_dir).expanduser().resolve() if args.extract_dir else None

    with SepAlumnosNormalizer(
        extract_dir=extract_dir,
        forced_encoding=args.encoding,
        chunk_size=args.chunk_size,
    ) as normalizer:
        df = normalizer.normalize(input_dir, no_rar=args.no_rar)
        normalizer.to_csv(df, output_path)

        print("\n" + "═" * 58)
        print(f"  Output : {output_path}")
        print(f"  Filas  : {len(df):,}")
        print(f"  Cols   : {len(df.columns)}")
        print(f"  Orden  : {list(df.columns)}")
        if "agno" in df.columns:
            print(f"  Años   : {sorted(df['agno'].dropna().unique().tolist())}")
        print("═" * 58)


if __name__ == "__main__":
    main()