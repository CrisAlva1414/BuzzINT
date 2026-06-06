from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import pandas as pd

logger = logging.getLogger(__name__)


_TABULAR_EXTENSIONS  = {".csv", ".xlsx", ".xls"}
_ARCHIVE_EXTENSIONS  = {".zip", ".rar"}
_PDF_EXTENSION       = ".pdf"
_CODEBOOK_EXTENSIONS = {".doc", ".docx", ".pdf"}   # se ignoran salvo skip_codebooks=False

# Encodings a probar en orden para CSVs chilenos
_CSV_ENCODINGS   = ["utf-8-sig", "latin-1", "cp1252", "utf-8"]
# Separadores a probar en orden
_CSV_SEPARATORS  = [";", ",", "\t", "|"]

# Patrones de hojas/archivos que son solo metadata, no datos
_COVER_RE = re.compile(
    r"^(portada|instrucciones?|leeme|readme|notas?|cover|info|acerca|about|metadata"
    r"|libro\s*de\s*c[oó]digos?|codebook|esquema|clave|diccionario)",
    re.IGNORECASE,
)

# Patrón para detectar archivos de codebook por nombre de archivo
_CODEBOOK_FILE_RE = re.compile(
    r"(_cod(igos?)?|_codebook|libro.?cod|esquema|clave\.pdf|_clave\.|publ_clave)",
    re.IGNORECASE,
)


@dataclass
class IngestedFile:
    df:             pd.DataFrame
    source_path:    Path         # Archivo original en Bronze
    inner_path:     str | None   # Ruta dentro del ZIP/RAR
    sheet_name:     str | None   # Hoja (None si no es Excel)
    format:         str          # "csv" | "xlsx" | "xls" | "pdf"
    encoding_used:  str | None
    separator_used: str | None
    is_codebook:    bool = False  # True si parece libro de códigos, no datos
    warnings:       list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        parts = [self.source_path.name]
        if self.inner_path:
            parts.append(self.inner_path)
        if self.sheet_name:
            parts.append(f"[{self.sheet_name}]")
        return " > ".join(parts)


class Ingestor:
    def __init__(
        self,
        skip_pdf:       bool = True,
        skip_codebooks: bool = True,
        min_rows:       int  = 2,
    ) -> None:
        self.skip_pdf       = skip_pdf
        self.skip_codebooks = skip_codebooks
        self.min_rows       = min_rows


    def ingest(self, path: Path) -> list[IngestedFile]:
        path   = Path(path)
        # Resolver doble extensión .rar.rar antes de inferir el tipo
        suffix = _resolve_suffix(path)
        results: list[IngestedFile] = []

        try:
            if suffix == ".zip":
                results = list(self._ingest_zip(path))
            elif suffix == ".rar":
                results = list(self._ingest_rar(path))
            elif suffix in _TABULAR_EXTENSIONS:
                r = self._ingest_tabular(path, inner_path=None)
                if r:
                    results = [r]
            elif suffix == _PDF_EXTENSION:
                if not self.skip_pdf:
                    results = list(self._ingest_pdf(path, inner_path=None))
                else:
                    logger.debug("PDF saltado (skip_pdf=True): %s", path.name)
            else:
                logger.warning("Extensión no soportada '%s': %s", suffix, path.name)
        except Exception as exc:
            logger.error("Error ingesting %s: %s", path, exc, exc_info=True)

        # Filtrar por min_rows y codebooks
        filtered = []
        for r in results:
            if r.is_codebook and self.skip_codebooks:
                logger.debug("Codebook excluido: %s", r.label)
                continue
            if len(r.df) < self.min_rows:
                logger.debug("DataFrame demasiado pequeño (%d filas): %s", len(r.df), r.label)
                continue
            filtered.append(r)

        logger.info("Ingestado %s → %d DataFrame(s)", path.name, len(filtered))
        return filtered


    def _ingest_zip(self, zip_path: Path) -> Iterator[IngestedFile]:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            logger.debug("ZIP %s: %d miembros", zip_path.name, len(members))

            for member in members:
                mpath  = Path(member)
                suffix = _resolve_suffix(mpath)

                # Saltar directorios, ocultos, __MACOSX, etc.
                if member.endswith("/") or mpath.name.startswith(".") or "__MACOSX" in member:
                    continue

                is_cb = _is_codebook_path(member)

                if suffix in _TABULAR_EXTENSIONS:
                    raw = zf.read(member)
                    for r in self._ingest_bytes(raw, suffix, zip_path, inner_path=member):
                        r.is_codebook = is_cb
                        yield r

                elif suffix == ".zip":
                    # ZIP anidado — recursar en memoria
                    raw = zf.read(member)
                    for r in self._ingest_zip_bytes(raw, zip_path, prefix=member):
                        yield r

                elif suffix == ".rar":
                    # RAR dentro de ZIP — poco frecuente pero ocurre
                    raw = zf.read(member)
                    for r in self._ingest_rar_bytes(raw, zip_path, inner_path=member):
                        yield r

                elif suffix == _PDF_EXTENSION and not self.skip_pdf:
                    raw = zf.read(member)
                    for r in self._ingest_pdf_bytes(raw, zip_path, inner_path=member):
                        r.is_codebook = is_cb
                        yield r

                else:
                    logger.debug("Saltando miembro no tabular en ZIP: %s", member)

    def _ingest_zip_bytes(
        self, raw: bytes, source_path: Path, prefix: str
    ) -> Iterator[IngestedFile]:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as inner_zf:
                for member in inner_zf.namelist():
                    mpath  = Path(member)
                    suffix = _resolve_suffix(mpath)
                    if member.endswith("/") or mpath.name.startswith("."):
                        continue
                    if suffix in _TABULAR_EXTENSIONS:
                        inner_raw = inner_zf.read(member)
                        full_path = f"{prefix}/{member}"
                        for r in self._ingest_bytes(inner_raw, suffix, source_path, inner_path=full_path):
                            r.is_codebook = _is_codebook_path(member)
                            yield r
        except Exception as exc:
            logger.error("Error en ZIP anidado %s/%s: %s", source_path.name, prefix, exc)

    def _ingest_rar(self, rar_path: Path) -> Iterator[IngestedFile]:
        try:
            import rarfile
        except ImportError:
            logger.error("rarfile no instalado: pip install rarfile --break-system-packages")
            return

        # rarfile no soporta streams para RARs — necesita path en disco
        actual_path = _unwrap_double_rar(rar_path)

        try:
            with rarfile.RarFile(str(actual_path)) as rf:
                members = rf.namelist()
                logger.debug("RAR %s: %d miembros", rar_path.name, len(members))

                for member in members:
                    mpath  = Path(member)
                    suffix = _resolve_suffix(mpath)

                    if member.endswith("/") or mpath.name.startswith("."):
                        continue

                    is_cb = _is_codebook_path(member)

                    if suffix in _TABULAR_EXTENSIONS:
                        raw = rf.read(member)
                        for r in self._ingest_bytes(raw, rar_path, inner_path=member, suffix=suffix):
                            r.is_codebook = is_cb
                            yield r

                    elif suffix == _PDF_EXTENSION and not self.skip_pdf:
                        raw = rf.read(member)
                        for r in self._ingest_pdf_bytes(raw, rar_path, inner_path=member):
                            r.is_codebook = is_cb
                            yield r

                    else:
                        logger.debug("Saltando miembro no tabular en RAR: %s", member)
        except Exception as exc:
            logger.error("Error abriendo RAR %s: %s", rar_path.name, exc)

    def _ingest_rar_bytes(
        self, raw: bytes, source_path: Path, inner_path: str
    ) -> Iterator[IngestedFile]:
        import tempfile, os
        try:
            import rarfile
        except ImportError:
            logger.error("rarfile no instalado")
            return
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".rar", delete=False) as f:
                f.write(raw)
                tmp_path = Path(f.name)
            with rarfile.RarFile(str(tmp_path)) as rf:
                for member in rf.namelist():
                    mpath  = Path(member)
                    suffix = _resolve_suffix(mpath)
                    if member.endswith("/"):
                        continue
                    if suffix in _TABULAR_EXTENSIONS:
                        member_raw = rf.read(member)
                        for r in self._ingest_bytes(
                            member_raw, suffix, source_path,
                            inner_path=f"{inner_path}/{member}"
                        ):
                            yield r
        except Exception as exc:
            logger.error("Error en RAR-en-ZIP %s: %s", inner_path, exc)
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()


    def _ingest_tabular(self, path: Path, inner_path: str | None) -> IngestedFile | None:
        suffix = _resolve_suffix(path)
        raw    = path.read_bytes()
        items  = list(self._ingest_bytes(raw, suffix, path, inner_path))
        return items[0] if items else None

    def _ingest_bytes(
        self,
        raw:         bytes,
        suffix:      str,
        source_path: Path,
        inner_path:  str | None,
    ) -> Iterator[IngestedFile]:
        # Aceptar sufijo tanto como arg posicional (desde _ingest_rar)
        # como desde el path mismo
        if suffix == ".csv":
            r = self._read_csv(raw, source_path, inner_path)
            if r:
                yield r
        elif suffix in (".xlsx", ".xls"):
            yield from self._read_excel(raw, suffix, source_path, inner_path)


    def _read_csv(
        self, raw: bytes, source_path: Path, inner_path: str | None
    ) -> IngestedFile | None:
        label    = _label(source_path, inner_path)
        warnings = []

        # Detectar encoding
        encoding_used = None
        for enc in _CSV_ENCODINGS:
            try:
                text = raw.decode(enc)
                encoding_used = enc
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if encoding_used is None:
            logger.error("No se pudo decodificar %s", label)
            return None

        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Detectar separador
        separator_used = None
        for sep in _CSV_SEPARATORS:
            try:
                candidate = pd.read_csv(
                    io.StringIO(text), sep=sep, nrows=5,
                    dtype=str, on_bad_lines="skip",
                )
                if candidate.shape[1] > 1:
                    separator_used = sep
                    break
            except Exception:
                continue

        if separator_used is None:
            warnings.append("Separador no detectado — usando ','")
            separator_used = ","

        try:
            df = pd.read_csv(
                io.StringIO(text), sep=separator_used,
                dtype=str, on_bad_lines="warn",
            )
        except Exception as exc:
            logger.error("Error leyendo CSV %s: %s", label, exc)
            return None

        df = _clean_df(df)
        return IngestedFile(
            df=df, source_path=source_path, inner_path=inner_path,
            sheet_name=None, format="csv",
            encoding_used=encoding_used, separator_used=separator_used,
            warnings=warnings,
        )


    def _read_excel(
        self, raw: bytes, suffix: str, source_path: Path, inner_path: str | None
    ) -> Iterator[IngestedFile]:
        try:
            engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
            xf     = pd.ExcelFile(io.BytesIO(raw), engine=engine)
        except Exception as exc:
            logger.error("No se pudo abrir Excel %s: %s", _label(source_path, inner_path), exc)
            return

        for sheet_name in xf.sheet_names:
            sheet_str = str(sheet_name)

            # Detectar hojas de portada/códigos
            is_cb = bool(_COVER_RE.match(sheet_str.strip()))
            is_cb = is_cb or _is_codebook_path(inner_path or "")

            try:
                df = xf.parse(sheet_name, dtype=str, header=None)
            except Exception as exc:
                logger.warning("Error leyendo hoja '%s': %s", sheet_str, exc)
                continue

            df, header_row = _detect_header(df)
            df = _clean_df(df)

            warnings = []
            if header_row > 0:
                warnings.append(f"Encabezado en fila {header_row} (no en fila 0)")

            yield IngestedFile(
                df=df, source_path=source_path, inner_path=inner_path,
                sheet_name=sheet_str, format=suffix.lstrip("."),
                encoding_used=None, separator_used=None,
                is_codebook=is_cb, warnings=warnings,
            )


    def _ingest_pdf(self, path: Path, inner_path: str | None) -> Iterator[IngestedFile]:
        yield from self._ingest_pdf_bytes(path.read_bytes(), path, inner_path)

    def _ingest_pdf_bytes(
        self, raw: bytes, source_path: Path, inner_path: str | None
    ) -> Iterator[IngestedFile]:
        try:
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber no instalado: pip install pdfplumber --break-system-packages")
            return

        is_cb = _is_codebook_path(inner_path or str(source_path))

        try:
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    for tbl_idx, table in enumerate(page.extract_tables() or []):
                        if not table or len(table) < 2:
                            continue
                        header = [
                            str(c).strip() if c else f"col_{i}"
                            for i, c in enumerate(table[0])
                        ]
                        df = pd.DataFrame(table[1:], columns=header).astype(str)
                        df = _clean_df(df)
                        yield IngestedFile(
                            df=df, source_path=source_path, inner_path=inner_path,
                            sheet_name=f"page_{page_num}_table_{tbl_idx}",
                            format="pdf", encoding_used=None, separator_used=None,
                            is_codebook=is_cb,
                        )
        except Exception as exc:
            logger.error("Error extrayendo tablas PDF %s: %s",
                         _label(source_path, inner_path), exc)


def _resolve_suffix(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".rar.rar"):
        return ".rar"
    return path.suffix.lower()


def _unwrap_double_rar(path: Path) -> Path:
    # rarfile abre por path — simplemente pasar el path original funciona
    # siempre que el archivo en disco sea un RAR válido.
    return path


def _is_codebook_path(path_str: str) -> bool:
    return bool(_CODEBOOK_FILE_RE.search(path_str))


def _label(source_path: Path, inner_path: str | None) -> str:
    parts = [source_path.name]
    if inner_path:
        parts.append(inner_path)
    return " > ".join(parts)


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    # Nombres de columna: lowercase, strip, espacios → _
    df.columns = [
        re.sub(r"\s+", "_", str(c).strip().lower())
        for c in df.columns
    ]
    # Eliminar columnas Unnamed
    df = df.loc[:, ~df.columns.str.match(r"^unnamed:?\s*\d*$")]
    # Filas completamente vacías
    df = df.dropna(how="all")
    # Strip de celdas string
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].str.strip()
    return df.reset_index(drop=True)


def _detect_header(df: pd.DataFrame, threshold: float = 0.5) -> tuple[pd.DataFrame, int]:
    for i, row in df.iterrows():
        if row.notna().mean() >= threshold:
            new_header = [
                str(c).strip() if pd.notna(c) else f"col_{j}"
                for j, c in enumerate(row.tolist())
            ]
            new_df = df.iloc[int(str(i)) + 1:].copy()
            new_df.columns = new_header
            return new_df.reset_index(drop=True), int(str(i))
    return df, 0