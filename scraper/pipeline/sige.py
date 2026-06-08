from __future__ import annotations

import logging
from pathlib import Path

from scraper.core.schemas import PipelineResult
from scraper.pipelines.base import BasePipeline, FileRegistry

logger = logging.getLogger(__name__)


class SigePipeline(BasePipeline):
    source = "sige"


    def __init__(self, data_root: Path | str = "data") -> None:
        self._root     = Path(data_root)
        self._raw      = self._root / "sige" / "raw"
        self._proc     = self._root / "sige" / "processed"
        self._registry = FileRegistry.for_source(self._proc)


    def discover(self, source_dir: Path | None = None) -> list[Path]:
        directory = Path(source_dir) if source_dir else self._raw
        try:
            return self._registry.pending(directory, pattern="**/*.pdf")
        except Exception as exc:
            logger.error("[sige] discover falló: %s", exc)
            return []


    def run(self, source_path: Path, step: str = "all", **kwargs) -> PipelineResult:
        source_path = Path(source_path)
        job_id      = self.new_job_id()
        started     = self.now()
        rows_cal    = 0

        try:
            # SIGE no tiene step "download" interno
            if step in ("all", "normalize"):
                rows_cal = self._normalize(source_path, **kwargs)
            if step in ("all", "load"):
                self._load(**kwargs)
                self._registry.mark_done(source_path, job_id)
        except Exception as exc:
            logger.error("[sige] run falló: %s", exc)
            self._registry.mark_failed(source_path, job_id, str(exc))
            return PipelineResult(
                job_id=job_id, status="error", source=self.source,
                pipeline=step, error=str(exc),
                started_at=started, finished_at=self.now(),
            )

        return PipelineResult(
            job_id=job_id, status="ok", source=self.source,
            pipeline=step, rows_inserted=rows_cal,
            started_at=started, finished_at=self.now(),
        )


    def run_from_upload(self, upload_dir: Path, **kwargs) -> PipelineResult:
        job_id  = self.new_job_id()
        started = self.now()
        total   = 0

        try:
            pdfs = sorted(upload_dir.rglob("*.pdf"))
            logger.info("[sige] %d PDF(s) recibidos en upload", len(pdfs))
            self._normalize_dir(upload_dir, **kwargs)
            self._load(**kwargs)
            for pdf in pdfs:
                self._registry.mark_done(pdf, job_id)
                total += 1
        except Exception as exc:
            logger.error("[sige] upload run falló: %s", exc)
            return PipelineResult(
                job_id=job_id, status="error", source=self.source,
                pipeline="upload", error=str(exc),
                started_at=started, finished_at=self.now(),
            )

        return PipelineResult(
            job_id=job_id, status="ok", source=self.source,
            pipeline="upload", rows_inserted=total,
            started_at=started, finished_at=self.now(),
        )


    def _normalize(self, source_path: Path, mode: str = "delta", **_) -> int:
        from scraper.normalizers.sige_normalizer import normalize
        # Si es un PDF individual, procesarlo en su directorio padre
        src_dir = source_path if source_path.is_dir() else source_path.parent
        stats   = normalize(source_dir=src_dir, output_dir=self._proc, mode=mode)
        return stats.get("processed", 0)


    def _normalize_dir(self, source_dir: Path, mode: str = "full", **_) -> None:
        from scraper.normalizers.sige_normalizer import normalize
        normalize(source_dir=source_dir, output_dir=self._proc, mode=mode)


    def _load(self, dry_run: bool = False, **_) -> None:
        from scraper.db.loaders.load_sige import run_calificaciones, run_profesores
        cal  = self._proc / "sige_calificaciones.csv"
        prof = self._proc / "sige_profesores.csv"
        if cal.exists():
            logger.info("[sige] load calificaciones")
            run_calificaciones(cal, dry_run=dry_run)
        if prof.exists():
            logger.info("[sige] load profesores")
            run_profesores(prof, dry_run=dry_run)