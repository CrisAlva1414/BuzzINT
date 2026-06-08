from __future__ import annotations

import logging
from pathlib import Path

from scraper.core.schemas import PipelineResult
from scraper.pipelines.base import BasePipeline, FileRegistry

logger = logging.getLogger(__name__)


class MineducPipeline(BasePipeline):
    source = "mineduc"


    def __init__(self, data_root: Path | str = "data") -> None:
        self._root     = Path(data_root)
        self._raw      = self._root / "mineduc" / "raw"
        self._proc     = self._root / "mineduc" / "processed"
        self._registry = FileRegistry.for_source(self._proc)


    def discover(self, source_dir: Path | None = None) -> list[Path]:
        directory = Path(source_dir) if source_dir else self._proc
        try:
            return self._registry.pending(directory, pattern="*.csv")
        except Exception as exc:
            logger.error("[mineduc] discover falló: %s", exc)
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
                stats = self._load(source_path, **kwargs)
                self._registry.mark_done(source_path, job_id)
                return PipelineResult(
                    job_id=job_id, status="ok", source=self.source,
                    pipeline=f"load_{source_path.stem}",
                    rows_inserted=stats.get("rows_inserted", 0),
                    rows_read=stats.get("rows_read", 0),
                    rows_skipped=stats.get("rows_skipped", 0),
                    started_at=started, finished_at=self.now(),
                )
        except Exception as exc:
            logger.error("[mineduc] run falló: %s", exc)
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


    def _download(self, sources: list[str] | None = None, dry_run: bool = False, **_) -> None:
        from scraper.extractors.mineduc_downloader import run_source, SOURCES
        targets = sources or list(SOURCES.keys())
        for key in targets:
            logger.info("[mineduc] download → %s", key)
            run_source(key, SOURCES[key], self._raw, dry_run=dry_run)


    def _normalize(self, mode: str = "delta", **_) -> None:
        from scraper.normalizers.mineduc_alumnos_normalizer import normalize as norm_alumnos
        from scraper.normalizers.mineduc_cargos_normalizer import normalize as norm_cargos
        from scraper.normalizers.mineduc_establecimientos_normalizer import normalize as norm_estab

        self._proc.mkdir(parents=True, exist_ok=True)
        for norm_fn, src_name, out_name in [
            (norm_estab,   "establecimientos", "mineduc_establecimientos.csv"),
            (norm_alumnos, "alumnos",          "mineduc_alumnos.csv"),
            (norm_cargos,  "cargos",           "mineduc_cargos.csv"),
        ]:
            logger.info("[mineduc] normalize → %s", out_name)
            norm_fn(
                source_dir  = self._raw / src_name,
                output_path = self._proc / out_name,
                mode        = mode,
            )


    def _load(self, source_path: Path, dry_run: bool = False, **_) -> dict:
        name = source_path.name

        if "alumnos" in name:
            from scraper.db.loaders.load_alumnos import run
            return run(source_path, dry_run=dry_run)

        if "establecimientos" in name:
            from scraper.db.loaders.load_dev import load_establecimientos
            load_establecimientos(source_path)
            return {}

        if "cargos" in name:
            from scraper.db.loaders.load_dev import load_cargos
            load_cargos(source_path)
            return {}

        return {}