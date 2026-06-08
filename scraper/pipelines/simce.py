from __future__ import annotations

import logging
from pathlib import Path

from scraper.core.schemas import PipelineResult
from scraper.pipelines.base import BasePipeline, FileRegistry

logger = logging.getLogger(__name__)

_GRANULARIDADES = ("rbd", "comuna", "region")


class SimcePipeline(BasePipeline):
    source = "simce"


    def __init__(self, data_root: Path | str = "data") -> None:
        self._root     = Path(data_root)
        self._raw      = self._root / "simce" / "raw"
        self._proc     = self._root / "simce" / "processed"
        self._registry = FileRegistry.for_source(self._proc)


    def discover(self, source_dir: Path | None = None) -> list[Path]:
        directory = Path(source_dir) if source_dir else self._raw
        try:
            return self._registry.pending(directory, pattern="*.rar")
        except Exception as exc:
            logger.error("[simce] discover falló: %s", exc)
            return []


    def run(self, source_path: Path, step: str = "all", **kwargs) -> PipelineResult:
        source_path = Path(source_path)
        job_id      = self.new_job_id()
        started     = self.now()

        try:
            if step in ("all", "download"):
                self._download(**kwargs)
            if step in ("all", "normalize"):
                self._normalize(**kwargs)
            if step in ("all", "load"):
                self._load(**kwargs)
                self._registry.mark_done(source_path, job_id)
        except Exception as exc:
            logger.error("[simce] run falló: %s", exc)
            self._registry.mark_failed(source_path, job_id, str(exc))
            return PipelineResult(
                job_id=job_id, status="error", source=self.source,
                pipeline=step, error=str(exc),
                started_at=started, finished_at=self.now(),
            )

        return PipelineResult(
            job_id=job_id, status="ok", source=self.source,
            pipeline=step, started_at=started, finished_at=self.now(),
        )


    def _download(self, cat_min: int = 2, cat_max: int = 60,
                  dry_run: bool = False, **_) -> None:
        from scraper.extractors.simce_downloader import run
        logger.info("[simce] download cat %d-%d", cat_min, cat_max)
        run(cat_min=cat_min, cat_max=cat_max,
            output_dir=self._raw, dry_run=dry_run, only_rar=True)


    def _normalize(self, mode: str = "delta", **_) -> None:
        from scraper.normalizers.simce_normalizer import normalize
        logger.info("[simce] normalize → %s", self._proc)
        normalize(source_dir=self._raw, output_dir=self._proc, mode=mode)


    def _load(self, **_) -> None:
        from scraper.db.loaders.load_dev import load_simce
        for gran in _GRANULARIDADES:
            csv_path = self._proc / f"simce__{gran}.csv"
            if csv_path.exists():
                logger.info("[simce] load %s", csv_path.name)
                load_simce(csv_path, granularity=gran)