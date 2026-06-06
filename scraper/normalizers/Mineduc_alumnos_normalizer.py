from __future__ import annotations

import argparse
import csv
import logging
import re
import tempfile
from pathlib import Path
from typing import Generator, Optional, Tuple

import pandas as pd
import rarfile
from charset_normalizer import from_bytes

log = logging.getLogger(__name__)


class BaseNormalizer:
    def normalize(self, source_dir: Path, **kwargs) -> None:
        raise NotImplementedError


CHUNK_SIZE = 20_000

COLUMN_ALIASES: dict[str, str] = {
    "agno": "agno", "anio": "agno", "año": "agno", "year": "agno",
    "mrun": "mrun", "cod_mrun": "mrun",
    "rbd": "rbd", "cod_rbd": "rbd",
    "dgv_rbd": "dgv_rbd", "nom_rbd": "nom_rbd", "nombre_rbd": "nom_rbd",
    "let_rbd": "let_rbd", "num_rbd": "num_rbd",
    "gen_alu": "gen_alu", "cod_gen_alu": "gen_alu", "sexo": "gen_alu",
    "fec_nac_alu": "fec_nac_alu", "fec_defun_alu": "fec_defun_alu",
    "criterio_sep": "criterio_sep", "cod_sep": "criterio_sep",
    "condicion_sep": "criterio_sep", "condicion": "criterio_sep",
    "prioritario_alu": "prioritario_alu", "preferente_alu": "preferente_alu",
    "ben_sep": "ben_sep",
    "convenio_sep": "convenio_sep", "año_ingreso_sep": "año_ingreso_sep",
    "clasificacion_sep": "clasificacion_sep", "ee_gratuito": "ee_gratuito",
    "estado_estab": "estado_estab", "grado_sep": "grado_sep",
    "cod_reg_rbd": "cod_reg_rbd", "cod_region": "cod_reg_rbd", "region": "cod_reg_rbd",
    "nom_reg_rbd_a": "nom_reg_rbd_a",
    "cod_pro_rbd": "cod_pro_rbd", "cod_provincia": "cod_pro_rbd",
    "cod_com_rbd": "cod_com_rbd", "cod_comuna_rbd": "cod_com_rbd",
    "nom_com_rbd": "nom_com_rbd",
    "cod_deprov_rbd": "cod_deprov_rbd", "nom_deprov_rbd": "nom_deprov_rbd",
    "cod_depe": "cod_depe", "dependencia": "cod_depe",
    "cod_depe2": "cod_depe2", "rural_rbd": "rural_rbd", "cod_rural": "rural_rbd",
    "nombre_slep": "nombre_slep",
    "cod_ense": "cod_ense", "cod_ense2": "cod_ense2", "cod_ense3": "cod_ense3",
    "cod_grado": "cod_grado", "grado": "cod_grado", "cod_grado2": "cod_grado2",
    "let_cur": "let_cur", "cod_jor": "cod_jor",
}

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
    "let_rbd", "num_rbd",
    "_source_file",
]

_SEPARATORS: list[str] = [",", ";", "\t", "|"]
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def _normalize_fec_nac(series: pd.Series) -> pd.Series:
    def _convert(val: str) -> str:
        v = str(val).strip()
        if len(v) == 8 and v.isdigit():
            return f"{v[:4]}-{v[4:6]}-{v[6:]}"
        if len(v) == 6 and v.isdigit():
            return f"{v[:4]}-{v[4:]}"
        return v
    return series.astype(str).map(_convert)


def _normalize_col(name: str) -> str:
    clean = name.strip().lower().replace(" ", "_").replace("-", "_")
    return COLUMN_ALIASES.get(clean, clean)


def _infer_year(path: Path) -> Optional[str]:
    for candidate in [path.stem, path.parent.name]:
        m = _YEAR_RE.search(candidate)
        if m:
            return m.group(1)
    return None


def _detect_encoding(path: Path, sample_bytes: int = 131_072) -> str:
    raw = path.read_bytes()[:sample_bytes]
    last_nl = raw.rfind(b"\n")
    if last_nl > 0:
        raw = raw[:last_nl]
    result = from_bytes(raw).best()
    enc = str(result.encoding) if result else "latin-1"
    log.debug("  encoding detectado: %s", enc)
    return enc


def _sniff(path: Path, forced_encoding: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    base_encs: list[str] = (
        [forced_encoding] if forced_encoding
        else ["utf-8-sig", "utf-8", "latin-1", _detect_encoding(path)]
    )
    for enc in dict.fromkeys(base_encs + ["latin-1", "cp1252"]):
        for sep in _SEPARATORS:
            try:
                df_head = pd.read_csv(
                    path, sep=sep, encoding=enc, dtype=str,
                    nrows=5, on_bad_lines="skip",
                )
                if df_head.shape[1] >= 2:
                    log.debug("  sniff → enc=%s sep=%r cols=%d", enc, sep, df_head.shape[1])
                    return enc, sep
            except Exception:
                pass
    return None, None


class SepAlumnosNormalizer(BaseNormalizer):

    def __init__(
        self,
        extract_dir: Optional[Path] = None,
        forced_encoding: Optional[str] = None,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        self._extract_dir = extract_dir
        self._forced_enc = forced_encoding
        self._chunk_size = chunk_size
        self._tmp_ctx: Optional[tempfile.TemporaryDirectory] = None  # type: ignore[type-arg]


    def cleanup(self) -> None:
        if self._tmp_ctx:
            self._tmp_ctx.cleanup()
            self._tmp_ctx = None

    def __enter__(self) -> "SepAlumnosNormalizer":
        return self

    def __exit__(self, *_) -> None:
        self.cleanup()


    def normalize(
        self,
        source_dir: Path,
        output_path: Path,
        no_rar: bool = False,
    ) -> dict:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # ── Fase 1: pre-scan de headers ───────────────────────────────────────
        # Itera el generador una primera vez leyendo SOLO nrows=0 (header).
        # Los CSVs temp se borran igual que en la fase 2.
        log.info("Pre-scan de columnas (solo headers)...")
        final_cols = self._prescan_columns(source_dir, no_rar)
        if not final_cols:
            raise FileNotFoundError(f"No se encontraron CSVs procesables en {source_dir}")
        log.info("Columnas finales (%d): %s", len(final_cols), final_cols)

        # ── Fase 2: procesamiento real ────────────────────────────────────────
        total_rows = 0
        skipped: list[str] = []

        with open(output_path, "w", newline="", encoding="utf-8-sig") as out_fh:
            writer = csv.DictWriter(
                out_fh,
                fieldnames=final_cols,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()

            for csv_path, is_temp in self._iter_sources(source_dir, no_rar):
                try:
                    rows = self._stream_to_writer(csv_path, writer)
                    if rows is None:
                        skipped.append(csv_path.name)
                    else:
                        total_rows += rows
                finally:
                    # Borrar CSV extraído de RAR INMEDIATAMENTE.
                    # Así el temp nunca tiene más de un CSV simultáneamente.
                    if is_temp and csv_path.exists():
                        csv_path.unlink()
                        log.debug("  temp eliminado: %s", csv_path.name)

        if skipped:
            log.warning("%d archivo(s) omitido(s): %s", len(skipped), skipped)

        log.info("Completado: %d filas → %s", total_rows, output_path)
        return {"total_rows": total_rows, "skipped": skipped, "output_columns": final_cols}


    def _iter_sources(
        self, source_dir: Path, no_rar: bool
    ) -> Generator[Tuple[Path, bool], None, None]:
        if no_rar:
            for p in sorted(source_dir.rglob("*.csv")):
                yield p, False
            return

        rar_files = sorted(source_dir.rglob("*.rar"))
        log.info("%d RAR(s) en %s", len(rar_files), source_dir)
        extract_root = self._ensure_extract_dir()

        for rar_path in rar_files:
            log.info("Extrayendo: %s", rar_path.name)
            try:
                with rarfile.RarFile(rar_path) as rf:
                    csv_members = [
                        m for m in rf.infolist()
                        if m.filename.lower().endswith(".csv")
                    ]
                    if not csv_members:
                        log.warning("  Sin CSVs en %s", rar_path.name)
                        continue
                    for member in csv_members:
                        # Extraer solo este member, ceder control, borrar, continuar
                        rf.extract(member, extract_root)
                        out_path = extract_root / member.filename
                        log.debug("  → extraído: %s", member.filename)
                        yield out_path, True
                        # El finally del caller ya borró el archivo antes de
                        # llegar aquí de vuelta — pero si no existe, unlink
                        # tampoco falla (lo comprobamos en el caller)
            except Exception as exc:
                log.error("  Error con %s: %s", rar_path.name, exc)

    def _ensure_extract_dir(self) -> Path:
        if self._extract_dir:
            self._extract_dir.mkdir(parents=True, exist_ok=True)
            return self._extract_dir
        self._tmp_ctx = tempfile.TemporaryDirectory(prefix="sep_alumnos_")
        return Path(self._tmp_ctx.name)


    def _prescan_columns(self, source_dir: Path, no_rar: bool) -> list[str]:

        seen: set[str] = set()
        extra: list[str] = []

        for csv_path, is_temp in self._iter_sources(source_dir, no_rar):
            try:
                enc, sep = _sniff(csv_path, self._forced_enc)
                if enc is None:
                    continue
                header_df = pd.read_csv(
                    csv_path, sep=sep, encoding=enc, dtype=str, nrows=0
                )
                for raw_col in header_df.columns:
                    canonical = _normalize_col(raw_col.lstrip("\ufeff"))
                    if canonical not in seen:
                        seen.add(canonical)
                        if canonical not in set(OUTPUT_COLUMN_ORDER):
                            extra.append(canonical)
            except Exception as exc:
                log.debug("  pre-scan falló %s: %s", csv_path.name, exc)
            finally:
                # Pre-scan SIEMPRE borra los temp: para RARs, _iter_sources
                # re-extrae frescos en la fase 2 porque itera el RAR de nuevo.
                # Para no_rar, is_temp=False siempre → nunca borra originales.
                if is_temp and csv_path.exists():
                    csv_path.unlink()
                    log.debug("  pre-scan temp eliminado: %s", csv_path.name)

        priority = [c for c in OUTPUT_COLUMN_ORDER if c in seen]
        for mandatory in ("agno", "_source_file"):
            if mandatory not in priority:
                priority.append(mandatory)
        return priority + [c for c in extra if c not in set(priority)]


    def _stream_to_writer(
        self,
        path: Path,
        writer: csv.DictWriter,
    ) -> Optional[int]:
        log.info("Procesando: %s", path.name)

        enc, sep = _sniff(path, self._forced_enc)
        if enc is None or sep is None:
            log.warning("  ✗ No se pudo determinar encoding/separador")
            return None

        year = _infer_year(path)
        rows_written = 0

        tried_encs = [enc] + ([] if enc in ("latin-1", "cp1252") else ["latin-1", "cp1252"])

        for attempt_enc in tried_encs:
            try:
                # NOTA: dtype=str + sin low_memory evita que pandas
                # haga un pre-scan completo del archivo para inferir tipos.
                reader = pd.read_csv(
                    path,
                    sep=sep,
                    encoding=attempt_enc,
                    dtype=str,           # <- clave: sin inferencia de tipos
                    on_bad_lines="skip",
                    chunksize=self._chunk_size,
                    # low_memory NO se pasa: con dtype=str es irrelevante
                    # y omitirlo evita el flag que activaba el pre-scan
                )
                bad_lines = 0
                import warnings
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    for chunk in reader:
                        rows_written += self._write_chunk(chunk, writer, year, path.name)
                    bad_lines = sum(
                        1 for w in caught
                        if issubclass(w.category, pd.errors.ParserWarning)
                    )
                if bad_lines:
                    log.warning("  ⚠ %d fila(s) omitidas por formato incorrecto", bad_lines)
                if attempt_enc != enc:
                    log.info("  (fallback encoding: %s)", attempt_enc)
                log.info("  ✓ %d filas escritas", rows_written)
                return rows_written

            except UnicodeDecodeError:
                log.debug("  enc=%s falló, probando siguiente...", attempt_enc)
                rows_written = 0  # resetear para el próximo intento
                continue
            except Exception as exc:
                log.error("  Error leyendo %s: %s", path.name, exc)
                return None

        log.error("  ✗ Sin encoding válido: %s", path.name)
        return None

    def _write_chunk(
        self,
        chunk: pd.DataFrame,
        writer: csv.DictWriter,
        inferred_year: Optional[str],
        source_name: str,
    ) -> int:
        chunk = chunk.copy()
        chunk.columns = [_normalize_col(c.lstrip("\ufeff")) for c in chunk.columns]

        if "agno" not in chunk.columns:
            chunk["agno"] = inferred_year or "desconocido"

        if "fec_nac_alu" in chunk.columns:
            chunk["fec_nac_alu"] = _normalize_fec_nac(chunk["fec_nac_alu"])

        chunk["_source_file"] = source_name

        # reindex: columnas ausentes → cadena vacía (no NaN)
        chunk = chunk.reindex(columns=writer.fieldnames, fill_value="")
        chunk = chunk.fillna("")
        writer.writerows(chunk.to_dict("records"))
        return len(chunk)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Normaliza CSVs de Alumnos SEP en un único archivo unificado."
    )
    p.add_argument("--input",       default="data/mineduc/raw/alumnos",                   metavar="DIR")
    p.add_argument("--output",      default="data/mineduc/processed/mineduc_alumnos.csv", metavar="FILE")
    p.add_argument("--extract-dir", default=None,                                          metavar="DIR")
    p.add_argument("--chunk-size",  default=CHUNK_SIZE, type=int,                         metavar="N",
                   help=f"Filas por chunk (default: {CHUNK_SIZE:,})")
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
        stats = normalizer.normalize(input_dir, output_path, no_rar=args.no_rar)

    print("\n" + "═" * 58)
    print(f"  Output : {output_path}")
    print(f"  Filas  : {stats['total_rows']:,}")
    print(f"  Cols   : {len(stats['output_columns'])}")
    if stats["skipped"]:
        print(f"  Omitidos: {len(stats['skipped'])}")
    print("═" * 58)


if __name__ == "__main__":
    main()