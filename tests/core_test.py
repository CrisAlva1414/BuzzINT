import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from pydantic import ValidationError


class TestPipelineResult:

    def _make(self, **kwargs):
        from scraper.core.schemas import PipelineResult
        now = datetime.now(timezone.utc)
        defaults = dict(
            job_id="test-123", status="ok", source="mineduc",
            pipeline="load_alumnos", started_at=now, finished_at=now,
        )
        return PipelineResult(**{**defaults, **kwargs})

    def test_instancia_minima(self):
        r = self._make()
        assert r.status == "ok"
        assert r.rows_read == 0

    def test_status_invalido_lanza_error(self):
        with pytest.raises(ValidationError):
            self._make(status="pendiente")

    def test_source_invalido_lanza_error(self):
        with pytest.raises(ValidationError):
            self._make(source="otro")

    def test_error_puede_ser_none(self):
        r = self._make(error=None)
        assert r.error is None

    def test_status_error_con_mensaje(self):
        r = self._make(status="error", error="timeout al conectar")
        assert r.status == "error"
        assert "timeout" in r.error

    def test_status_partial(self):
        r = self._make(status="partial", rows_inserted=100, rows_skipped=5)
        assert r.status == "partial"
        assert r.rows_inserted == 100


class TestJobStatus:

    def test_job_running_sin_finished_at(self):
        from scraper.core.schemas import JobStatus
        now = datetime.now(timezone.utc)
        j   = JobStatus(
            job_id="abc", status="running", source="sige",
            pipeline="load_sige_cal", started_at=now,
        )
        assert j.finished_at is None
        assert j.progress_pct is None

    def test_progress_pct_acepta_none_y_entero(self):
        from scraper.core.schemas import JobStatus
        now = datetime.now(timezone.utc)
        j1 = JobStatus(job_id="a", status="running", source="simce",
                       pipeline="p", started_at=now, progress_pct=None)
        j2 = JobStatus(job_id="b", status="running", source="simce",
                       pipeline="p", started_at=now, progress_pct=75)
        assert j1.progress_pct is None
        assert j2.progress_pct == 75


class TestRunRequest:

    def test_defaults(self):
        from scraper.core.schemas import RunRequest
        r = RunRequest()
        assert r.step == "all"
        assert r.dry_run is False
        assert r.source_path is None

    def test_step_invalido(self):
        from scraper.core.schemas import RunRequest
        with pytest.raises(ValidationError):
            RunRequest(step="borrar_todo")

    def test_dry_run(self):
        from scraper.core.schemas import RunRequest
        r = RunRequest(step="load", dry_run=True)
        assert r.step == "load"
        assert r.dry_run is True


class TestLogging:

    def test_get_logger_retorna_logger(self):
        from scraper.core.logging import get_logger
        log = get_logger("scraper.test")
        assert isinstance(log, logging.Logger)
        assert log.name == "scraper.test"

    def test_get_logger_usa_nombre_modulo(self):
        from scraper.core.logging import get_logger
        log1 = get_logger("a.b.c")
        log2 = get_logger("a.b.c")
        assert log1 is log2  # mismo objeto (Python cache)

    def test_log_format_definido(self):
        from scraper.core import logging as core_log
        assert "%(levelname)s" in core_log.LOG_FORMAT
        assert "%(name)s" in core_log.LOG_FORMAT

    def test_configure_no_lanza(self):
        from scraper.core.logging import configure
        configure("WARNING")  # no debe lanzar


class TestFileRegistry:

    def test_nuevo_archivo_no_esta_procesado(self, tmp_path, sample_file):
        from scraper.pipelines.base import FileRegistry
        reg = FileRegistry.for_source(tmp_path)
        assert not reg.is_processed(sample_file)

    def test_mark_done_marca_como_procesado(self, tmp_path, sample_file):
        from scraper.pipelines.base import FileRegistry
        reg = FileRegistry.for_source(tmp_path)
        reg.mark_done(sample_file, "job-abc")
        assert reg.is_processed(sample_file)

    def test_persiste_entre_instancias(self, tmp_path, sample_file):
        from scraper.pipelines.base import FileRegistry
        reg1 = FileRegistry.for_source(tmp_path)
        reg1.mark_done(sample_file, "job-1")

        reg2 = FileRegistry.for_source(tmp_path)
        assert reg2.is_processed(sample_file)

    def test_mark_failed_no_bloquea_retry(self, tmp_path, sample_file):
        from scraper.pipelines.base import FileRegistry
        reg = FileRegistry.for_source(tmp_path)
        reg.mark_failed(sample_file, "job-fail", "timeout")
        assert not reg.is_processed(sample_file)  # status=error → no procesado

    def test_pending_retorna_archivos_no_procesados(self, tmp_path):
        from scraper.pipelines.base import FileRegistry
        f1 = tmp_path / "a.csv"
        f2 = tmp_path / "b.csv"
        f1.write_text("col\nval")
        f2.write_text("col\nval")

        reg = FileRegistry.for_source(tmp_path / "processed")
        reg.mark_done(f1, "job-1")

        pending = reg.pending(tmp_path, pattern="*.csv")
        assert f2 in pending
        assert f1 not in pending

    def test_pending_directorio_vacio_retorna_vacio(self, tmp_path):
        from scraper.pipelines.base import FileRegistry
        reg = FileRegistry.for_source(tmp_path / "processed")
        assert reg.pending(tmp_path, pattern="*.csv") == []

    def test_contenido_cambiado_invalida_procesado(self, tmp_path, sample_file):
        from scraper.pipelines.base import FileRegistry
        reg = FileRegistry.for_source(tmp_path)
        reg.mark_done(sample_file, "job-1")
        assert reg.is_processed(sample_file)

        # Modificar el archivo — el hash cambia
        sample_file.write_text("contenido diferente\n")
        assert not reg.is_processed(sample_file)

    def test_all_entries_retorna_lista(self, tmp_path, sample_file):
        from scraper.pipelines.base import FileRegistry
        reg = FileRegistry.for_source(tmp_path)
        reg.mark_done(sample_file, "job-1")
        entries = reg.all_entries()
        assert len(entries) == 1
        assert entries[0]["status"] == "ok"


class TestBasePipelineContract:

    def test_dummy_implementa_interfaz(self):
        pipeline = _DummyPipeline()
        assert hasattr(pipeline, "run")
        assert hasattr(pipeline, "discover")
        assert hasattr(pipeline, "run_all")
        assert hasattr(pipeline, "source")

    def test_run_retorna_pipeline_result(self, tmp_path, sample_file):
        from scraper.core.schemas import PipelineResult
        pipeline = _DummyPipeline()
        result   = pipeline.run(sample_file)
        assert isinstance(result, PipelineResult)
        assert result.status in ("ok", "error", "partial")

    def test_run_nunca_lanza_excepcion(self, tmp_path):
        pipeline = _DummyPipeline(fail=True)
        path     = tmp_path / "noexiste.csv"
        result   = pipeline.run(path)       # no debe explotar
        assert result.status == "error"
        assert result.error is not None

    def test_discover_retorna_lista(self, tmp_path):
        pipeline = _DummyPipeline()
        result   = pipeline.discover(tmp_path)
        assert isinstance(result, list)

    def test_discover_nunca_lanza_excepcion(self):
        pipeline = _DummyPipeline()
        result   = pipeline.discover(Path("/ruta/que/no/existe"))
        assert result == []

    def test_new_job_id_es_unico(self):
        ids = {_DummyPipeline.new_job_id() for _ in range(10)}
        assert len(ids) == 10

    def test_run_all_procesa_multiples_archivos(self, tmp_path):
        for i in range(3):
            (tmp_path / f"f{i}.csv").write_text("col\nval")
        pipeline = _DummyPipeline()
        results  = pipeline.run_all(tmp_path)
        assert len(results) == 3
        assert all(r.status == "ok" for r in results)


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "sample.csv"
    f.write_text("agno,mrun\n2023,11111111\n")
    return f


class _DummyPipeline(BasePipeline):
    source = "mineduc"

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    def run(self, source_path: Path, **kwargs):
        from scraper.core.schemas import PipelineResult
        started = self.now()
        if self._fail:
            return PipelineResult(
                job_id=self.new_job_id(), status="error", source=self.source,
                pipeline="dummy", error="fallo simulado",
                started_at=started, finished_at=self.now(),
            )
        return PipelineResult(
            job_id=self.new_job_id(), status="ok", source=self.source,
            pipeline="dummy", rows_inserted=1,
            started_at=started, finished_at=self.now(),
        )

    def discover(self, source_dir: Path):
        try:
            return sorted(Path(source_dir).glob("*.csv"))
        except Exception:
            return []


if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=str(Path(__file__).parent.parent),
    )
    sys.exit(result.returncode)