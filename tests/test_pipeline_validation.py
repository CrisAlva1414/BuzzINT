"""
Pipeline validation tests - check correctness of pipeline execution.
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper.pipelines.mineduc import MineducPipeline
from scraper.pipelines.simce import SimcePipeline
from scraper.pipelines.sige import SigePipeline


class TestPipelineDiscovery:
    """Test that pipelines can discover files correctly."""

    def test_mineduc_discovers_raw_files(self):
        """MINEDUC should discover files in raw/ directory."""
        pipeline = MineducPipeline(data_root="data")
        # Should look in raw, not processed
        assert pipeline._raw.name == "raw"

    def test_mineduc_discover_recursive(self):
        """MINEDUC discover should find files in subdirectories."""
        pipeline = MineducPipeline(data_root="data")
        # Create test file
        test_file = pipeline._raw / "alumnos" / "test.csv"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("test")

        try:
            # Should discover the file
            files = pipeline.discover()
            assert any("test.csv" in str(f) for f in files), \
                f"Expected to find test.csv, got: {files}"
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_simce_discovers_raw_files(self):
        """SIMCE should discover files in raw/ directory."""
        pipeline = SimcePipeline(data_root="data")
        assert pipeline._raw.name == "raw"

    def test_simce_discover_recursive(self):
        """SIMCE discover should find files in subdirectories."""
        pipeline = SimcePipeline(data_root="data")
        # Create test RAR file
        test_file = pipeline._raw / "2023" / "test.rar"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("RAR_TEST")

        try:
            files = pipeline.discover()
            assert any("test.rar" in str(f) for f in files), \
                f"Expected to find test.rar, got: {files}"
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_sige_discovers_raw_files(self):
        """SIGE should discover files in raw/ directory."""
        pipeline = SigePipeline(data_root="data")
        assert pipeline._raw.name == "raw"

    def test_sige_discover_recursive(self):
        """SIGE discover should find files in subdirectories."""
        pipeline = SigePipeline(data_root="data")
        # Create test PDF file
        test_file = pipeline._raw / "2023" / "test.pdf"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("PDF_TEST")

        try:
            files = pipeline.discover()
            assert any("test.pdf" in str(f) for f in files), \
                f"Expected to find test.pdf, got: {files}"
        finally:
            if test_file.exists():
                test_file.unlink()


class TestPipelineStructure:
    """Test that pipelines have correct structure."""

    def test_mineduc_has_registry(self):
        """MINEDUC should initialize registry."""
        pipeline = MineducPipeline(data_root="data")
        assert pipeline._registry is not None
        assert pipeline._registry._path.name == "pipeline_registry.json"

    def test_simce_has_registry(self):
        """SIMCE should initialize registry."""
        pipeline = SimcePipeline(data_root="data")
        assert pipeline._registry is not None

    def test_sige_has_registry(self):
        """SIGE should initialize registry."""
        pipeline = SigePipeline(data_root="data")
        assert pipeline._registry is not None

    def test_mineduc_source_name(self):
        """MINEDUC class should have correct source."""
        assert MineducPipeline.source == "mineduc"

    def test_simce_source_name(self):
        """SIMCE class should have correct source."""
        assert SimcePipeline.source == "simce"

    def test_sige_source_name(self):
        """SIGE class should have correct source."""
        assert SigePipeline.source == "sige"


class TestDataDirectories:
    """Test that required data directories exist."""

    def test_mineduc_directories_exist(self):
        """MINEDUC should have raw and processed directories."""
        pipeline = MineducPipeline(data_root="data")
        assert pipeline._raw.exists(), f"Missing {pipeline._raw}"
        assert pipeline._proc.exists(), f"Missing {pipeline._proc}"

    def test_simce_directories_exist(self):
        """SIMCE should have raw and processed directories."""
        pipeline = SimcePipeline(data_root="data")
        assert pipeline._raw.exists(), f"Missing {pipeline._raw}"
        assert pipeline._proc.exists(), f"Missing {pipeline._proc}"

    def test_sige_directories_exist(self):
        """SIGE should have raw and processed directories."""
        pipeline = SigePipeline(data_root="data")
        assert pipeline._raw.exists(), f"Missing {pipeline._raw}"
        assert pipeline._proc.exists(), f"Missing {pipeline._proc}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
