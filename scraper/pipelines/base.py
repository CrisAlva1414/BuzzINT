from __future__ import annotations

import hashlib
import json
import uuid
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from scraper.core.schemas import FileEntry, PipelineResult

logger = logging.getLogger(__name__)

_REGISTRY_FILE = "pipeline_registry.json"


class FileRegistry:

    def __init__(self, registry_path: Path) -> None:
        self._path   = registry_path
        self._entries: dict[str, dict] = {}
        self._load()

    @classmethod
    def for_source(cls, processed_dir: Path) -> "FileRegistry":
        processed_dir.mkdir(parents=True, exist_ok=True)
        return cls(processed_dir / _REGISTRY_FILE)

    def _load(self) -> None:
        if self._path.exists():
            with open(self._path, encoding="utf-8") as f:
                self._entries = json.load(f)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, ensure_ascii=False, indent=2)

    def is_processed(self, path: Path) -> bool:
        h = _sha256(path)
        return self._entries.get(h, {}).get("status") == "ok"

    def pending(self, source_dir: Path, pattern: str = "**/*") -> list[Path]:
        return [
            p for p in sorted(Path(source_dir).glob(pattern))
            if p.is_file() and not self.is_processed(p)
        ]

    def mark_done(self, path: Path, job_id: str) -> None:
        h = _sha256(path)
        self._entries[h] = {
            "path":         str(path),
            "sha256":       h,
            "size_bytes":   path.stat().st_size if path.exists() else 0,
            "status":       "ok",
            "job_id":       job_id,
            "processed_at": _now_iso(),
        }
        self._save()

    def mark_failed(self, path: Path, job_id: str, error: str) -> None:
        h = _sha256(path)
        self._entries[h] = {
            "path":       str(path),
            "sha256":     h,
            "status":     "error",
            "job_id":     job_id,
            "error":      error[:500],
            "failed_at":  _now_iso(),
        }
        self._save()

    def all_entries(self) -> list[dict]:
        return list(self._entries.values())


class BasePipeline(ABC):
    source: str  # "mineduc" | "simce" | "sige" — cada subclase lo define

    @abstractmethod
    def run(self, source_path: Path, **kwargs) -> PipelineResult:
        ...

    @abstractmethod
    def discover(self, source_dir: Path) -> list[Path]:
        ...

    def run_all(self, source_dir: Path, **kwargs) -> list[PipelineResult]:
        results = []
        try:
            pending = self.discover(source_dir)
        except Exception as exc:
            logger.error("[%s] discover() falló: %s", self.source, exc)
            return results

        logger.info("[%s] %d archivo(s) pendientes en %s", self.source, len(pending), source_dir)
        for path in pending:
            result = self.run(path, **kwargs)
            results.append(result)
            logger.info("[%s] %s → %s (%d ins)",
                        self.source, path.name, result.status, result.rows_inserted)
        return results

    @staticmethod
    def new_job_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()