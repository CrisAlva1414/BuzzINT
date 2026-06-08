import csv
import hashlib
import json
import logging
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Iterator, Optional

import pandas as pd
import rarfile

logger = logging.getLogger(__name__)

CHUNK_SIZE    = 20_000
MANIFEST_FILE = "normalizer_manifest.json"

_ENCODINGS   = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
_SEPARATORS  = [";", ",", "\t", "|"]
_YEAR_RE     = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


@dataclass
class SourceEntry:
    source_file:  str
    source_hash:  str
    rows_written: int
    processed_at: str
    output_file:  str


@dataclass
class NormalizerManifest:
    manifest_path: Path = field(repr=False)
    entries: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, output_dir: Path) -> "NormalizerManifest":
        path = output_dir / MANIFEST_FILE
        entries = {}
        if path.exists():
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        return cls(manifest_path=path, entries=entries)

    def is_processed(self, source_path: Path) -> bool:
        h = _sha256(source_path)
        entry = self.entries.get(h, {})
        return entry.get("status") in ("normalized", "loaded")

    def mark_normalized(self, source_path: Path, rows: int, output_file: Path) -> None:
        h = _sha256(source_path)
        self.entries[h] = {
            "source_file":  source_path.name,
            "source_hash":  h,
            "rows_written": rows,
            "processed_at": _now_iso(),
            "output_file":  str(output_file),
            "status":       "normalized",
        }
        self.save()

    def mark_loaded(self, source_hash: str) -> None:
        if source_hash in self.entries:
            self.entries[source_hash]["status"] = "loaded"
            self.entries[source_hash]["loaded_at"] = _now_iso()
            self.save()

    def pending_for_db(self) -> list[dict]:
        return [e for e in self.entries.values() if e.get("status") == "normalized"]

    def save(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _infer_year(path: Path) -> Optional[str]:
    for candidate in [path.stem, path.parent.name]:
        m = _YEAR_RE.search(candidate)
        if m:
            return m.group(1)
    return None


def _normalize_col(name: str) -> str:
    clean = name.strip().lstrip("\ufeff").lower().replace(" ", "_").replace("-", "_")
    return re.sub(r"_+", "_", clean)


def _sniff(path: Path, forced_enc: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    base = [forced_enc] if forced_enc else _ENCODINGS
    for enc in dict.fromkeys(base + ["latin-1", "cp1252"]):
        for sep in _SEPARATORS:
            try:
                df = pd.read_csv(path, sep=sep, encoding=enc, dtype=str, nrows=5, on_bad_lines="skip")
                if df.shape[1] >= 2:
                    return enc, sep
            except Exception:
                pass
    return None, None


def iter_sources(
    source_dir: Path,
    no_rar: bool = False,
    extract_dir: Optional[Path] = None,
) -> Generator[tuple[Path, bool], None, None]:
    if no_rar:
        for p in sorted(source_dir.rglob("*.csv")):
            yield p, False
        return

    for rar_path in sorted(source_dir.rglob("*.rar")):
        logger.info("Extrayendo: %s", rar_path.name)
        try:
            with rarfile.RarFile(rar_path) as rf:
                members = [m for m in rf.infolist() if m.filename.lower().endswith(".csv")]
                if not members:
                    logger.warning("Sin CSVs en %s", rar_path.name)
                    continue
                root = _ensure_extract_dir(extract_dir)
                for member in members:
                    rf.extract(member, root)
                    out = root / member.filename
                    yield out, True
        except Exception as exc:
            logger.error("Error con %s: %s", rar_path.name, exc)

    for p in sorted(source_dir.glob("*.csv")):
        yield p, False


def _ensure_extract_dir(extract_dir: Optional[Path]) -> Path:
    if extract_dir:
        extract_dir.mkdir(parents=True, exist_ok=True)
        return extract_dir
    # Directorio temporal persistente en el proceso — el caller borra los files
    tmp = tempfile.mkdtemp(prefix="buzzint_norm_")
    return Path(tmp)


def prescan_columns(
    source_dir: Path,
    priority_cols: list[str],
    meta_cols: list[str],
    no_rar: bool = False,
    forced_enc: Optional[str] = None,
) -> list[str]:
    seen: set[str] = set()

    for csv_path, is_temp in iter_sources(source_dir, no_rar):
        try:
            enc, sep = _sniff(csv_path, forced_enc)
            if enc is None:
                continue
            header = pd.read_csv(csv_path, sep=sep, encoding=enc, dtype=str, nrows=0)
            for raw in header.columns:
                seen.add(_normalize_col(raw.lstrip("\ufeff")))
        except Exception as exc:
            logger.debug("prescan falló %s: %s", csv_path.name, exc)
        finally:
            if is_temp and csv_path.exists():
                csv_path.unlink()

    priority  = [c for c in priority_cols if c in seen]
    mandatory = [c for c in ("agno", "_source_file") if c not in priority]
    extra     = sorted(seen - set(priority_cols) - set(meta_cols))
    return priority + mandatory + extra + [c for c in meta_cols if c not in priority + mandatory]


def write_chunk(
    chunk: pd.DataFrame,
    writer: csv.DictWriter,
    inferred_year: Optional[str],
    source_name: str,
    col_transforms: dict,
) -> int:
    chunk = chunk.copy()
    chunk.columns = [_normalize_col(c.lstrip("\ufeff")) for c in chunk.columns]

    if "agno" not in chunk.columns:
        chunk["agno"] = inferred_year or ""

    for col, fn in col_transforms.items():
        if col in chunk.columns:
            chunk[col] = chunk[col].apply(fn)

    chunk["_source_file"] = source_name
    chunk = chunk.reindex(columns=writer.fieldnames, fill_value="").fillna("")
    writer.writerows(chunk.to_dict("records"))
    return len(chunk)


def stream_source_to_writer(
    path: Path,
    writer: csv.DictWriter,
    chunk_size: int,
    col_transforms: dict,
    forced_enc: Optional[str] = None,
) -> Optional[int]:
    enc, sep = _sniff(path, forced_enc)
    if enc is None:
        logger.warning("No se pudo determinar encoding/separador: %s", path.name)
        return None

    year = _infer_year(path)
    rows_written = 0

    for attempt_enc in dict.fromkeys([enc, "latin-1", "cp1252"]):
        try:
            reader = pd.read_csv(
                path, sep=sep, encoding=attempt_enc,
                dtype=str, on_bad_lines="skip", chunksize=chunk_size,
            )
            for chunk in reader:
                rows_written += write_chunk(chunk, writer, year, path.name, col_transforms)
            if attempt_enc != enc:
                logger.info("  fallback encoding: %s", attempt_enc)
            logger.info("  ✓ %s — %d filas", path.name, rows_written)
            return rows_written
        except UnicodeDecodeError:
            rows_written = 0
            continue
        except Exception as exc:
            logger.error("  Error leyendo %s: %s", path.name, exc)
            return None

    logger.error("  Sin encoding válido: %s", path.name)
    return None