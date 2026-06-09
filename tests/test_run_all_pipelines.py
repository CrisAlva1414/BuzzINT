"""
Comprehensive pipeline integration test.
Runs all pipelines (mineduc, simce, sige) and validates they work end-to-end.
"""
import sys
import csv
import logging
import json
from pathlib import Path
from datetime import datetime
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper.pipelines.mineduc import MineducPipeline
from scraper.pipelines.simce import SimcePipeline
from scraper.pipelines.sige import SigePipeline
from scraper.core.schemas import PipelineResult

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def _create_sample_mineduc_csv(path: Path, name: str) -> None:
    """Create sample MINEDUC CSV for testing."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if "alumnos" in name:
        rows = [
            {"rbd": "1001", "alumno_id": "A001", "nombre": "Test Student", "año": "2023"},
            {"rbd": "1001", "alumno_id": "A002", "nombre": "Test Student 2", "año": "2023"},
        ]
    elif "establecimientos" in name:
        rows = [
            {"rbd": "1001", "nombre": "Escuela Test", "región": "13", "provincia": "131"},
        ]
    else:  # cargos
        rows = [
            {"rbd": "1001", "cargo_id": "C001", "cargo": "Docente", "año": "2023"},
        ]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _create_sample_simce_rar(path: Path) -> None:
    """Create minimal SIMCE RAR file for testing (just metadata)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create a simple RAR file with test marker
    path.write_text("SIMCE_TEST_DATA")


class PipelineTestRunner:
    """Runs all pipelines and validates outputs."""

    def __init__(self, data_root: Path | str = "data", skip_db: bool = False, create_samples: bool = False):
        self.data_root = Path(data_root)
        self.skip_db = skip_db
        self.create_samples = create_samples
        self.results = {
            "mineduc": [],
            "simce": [],
            "sige": [],
        }
        self.errors = []
        self.warnings = []
        self.start_time = None
        self.end_time = None

    def run_all(self) -> dict[str, Any]:
        """Run all pipelines and collect results."""
        self.start_time = datetime.now()
        logger.info("=" * 80)
        logger.info("Starting comprehensive pipeline test")
        logger.info(f"Config: skip_db={self.skip_db}, create_samples={self.create_samples}")
        logger.info("=" * 80)

        # Validate data structure first
        self._validate_data_structure()

        # Create sample data if requested
        if self.create_samples:
            self._create_sample_data()

        # Run each pipeline
        self._run_mineduc_pipeline()
        self._run_simce_pipeline()
        self._run_sige_pipeline()

        self.end_time = datetime.now()
        return self._generate_report()

    def _validate_data_structure(self) -> None:
        """Validate that all required data directories exist."""
        logger.info("\nValidating data structure...")
        required_dirs = [
            "mineduc/raw", "mineduc/processed",
            "simce/raw", "simce/processed",
            "sige/raw", "sige/processed",
        ]

        for dir_path in required_dirs:
            full_path = self.data_root / dir_path
            if full_path.exists():
                logger.info(f"✓ {dir_path}")
            else:
                logger.warning(f"⚠ Missing: {dir_path}")
                self.warnings.append(f"Missing directory: {dir_path}")
                full_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"  → Created: {dir_path}")

    def _create_sample_data(self) -> None:
        """Create sample data files for testing."""
        logger.info("\nCreating sample data...")

        # MINEDUC samples
        mineduc_raw = self.data_root / "mineduc" / "raw"
        _create_sample_mineduc_csv(mineduc_raw / "establecimientos" / "establecimientos.csv", "establecimientos")
        logger.info("✓ Created: mineduc/raw/establecimientos/establecimientos.csv")

        _create_sample_mineduc_csv(mineduc_raw / "alumnos" / "alumnos.csv", "alumnos")
        logger.info("✓ Created: mineduc/raw/alumnos/alumnos.csv")

        _create_sample_mineduc_csv(mineduc_raw / "cargos" / "cargos.csv", "cargos")
        logger.info("✓ Created: mineduc/raw/cargos/cargos.csv")

    def _run_mineduc_pipeline(self) -> None:
        """Run MINEDUC pipeline."""
        logger.info("\n" + "=" * 80)
        logger.info("MINEDUC PIPELINE")
        logger.info("=" * 80)

        try:
            pipeline = MineducPipeline(data_root=self.data_root)
            logger.info("✓ MineducPipeline initialized")

            # Check if raw data exists
            raw_dir = self.data_root / "mineduc" / "raw"
            if not raw_dir.exists():
                self.warnings.append("MINEDUC raw directory does not exist")
                logger.warning(f"⚠ Raw directory missing: {raw_dir}")
                return

            # Discover files
            try:
                pending = pipeline.discover()
            except Exception as e:
                logger.warning(f"⚠ Discovery failed: {e}")
                self.warnings.append(f"MINEDUC discovery failed: {e}")
                pending = []

            logger.info(f"✓ Discovery found {len(pending)} pending file(s)")

            # If no pending files, check processed directory
            if not pending:
                proc_dir = self.data_root / "mineduc" / "processed"
                if proc_dir.exists():
                    logger.info(f"ℹ No pending files (already processed in {proc_dir})")
                else:
                    self.warnings.append("MINEDUC: No pending or processed files found")
                return

            # Run each discovered file
            for file_path in pending:
                logger.info(f"\nProcessing: {file_path.name}")
                try:
                    if self.skip_db:
                        result = pipeline.run(file_path, step="normalize")
                    else:
                        result = pipeline.run(file_path)
                    self.results["mineduc"].append(result)
                    self._validate_result(result, "mineduc")
                except Exception as e:
                    logger.warning(f"⚠ Failed to process {file_path.name}: {e}")
                    if not self.skip_db:
                        raise

            logger.info("✓ MINEDUC pipeline completed")

        except Exception as exc:
            error_msg = f"MINEDUC pipeline failed: {exc}"
            self.errors.append(error_msg)
            logger.error(f"✗ {error_msg}")

    def _run_simce_pipeline(self) -> None:
        """Run SIMCE pipeline."""
        logger.info("\n" + "=" * 80)
        logger.info("SIMCE PIPELINE")
        logger.info("=" * 80)

        try:
            pipeline = SimcePipeline(data_root=self.data_root)
            logger.info("✓ SimcePipeline initialized")

            # Check if raw data exists
            raw_dir = self.data_root / "simce" / "raw"
            if not raw_dir.exists():
                self.warnings.append("SIMCE raw directory does not exist")
                logger.warning(f"⚠ Raw directory missing: {raw_dir}")
                return

            # Discover files
            pending = pipeline.discover()
            logger.info(f"✓ Discovery found {len(pending)} pending file(s)")

            # If no pending files, log and skip
            if not pending:
                proc_dir = self.data_root / "simce" / "processed"
                if proc_dir.exists():
                    logger.info(f"ℹ No pending files (already processed in {proc_dir})")
                else:
                    self.warnings.append("SIMCE: No pending or processed files found")
                return

            # Run each discovered file
            for file_path in pending:
                logger.info(f"\nProcessing: {file_path.name}")
                result = pipeline.run(file_path)
                self.results["simce"].append(result)
                self._validate_result(result, "simce")

            logger.info("✓ SIMCE pipeline completed")

        except Exception as exc:
            error_msg = f"SIMCE pipeline failed: {exc}"
            self.errors.append(error_msg)
            logger.error(f"✗ {error_msg}")

    def _run_sige_pipeline(self) -> None:
        """Run SIGE pipeline."""
        logger.info("\n" + "=" * 80)
        logger.info("SIGE PIPELINE")
        logger.info("=" * 80)

        try:
            pipeline = SigePipeline(data_root=self.data_root)
            logger.info("✓ SigePipeline initialized")

            # Check if raw data exists
            raw_dir = self.data_root / "sige" / "raw"
            if not raw_dir.exists():
                self.warnings.append("SIGE raw directory does not exist")
                logger.warning(f"⚠ Raw directory missing: {raw_dir}")
                return

            # Discover files
            pending = pipeline.discover()
            logger.info(f"✓ Discovery found {len(pending)} pending file(s)")

            # If no pending files, log and skip
            if not pending:
                proc_dir = self.data_root / "sige" / "processed"
                if proc_dir.exists():
                    logger.info(f"ℹ No pending files (already processed in {proc_dir})")
                else:
                    self.warnings.append("SIGE: No pending or processed files found")
                return

            # Run each discovered file
            for file_path in pending:
                logger.info(f"\nProcessing: {file_path.name}")
                result = pipeline.run(file_path)
                self.results["sige"].append(result)
                self._validate_result(result, "sige")

            logger.info("✓ SIGE pipeline completed")

        except Exception as exc:
            error_msg = f"SIGE pipeline failed: {exc}"
            self.errors.append(error_msg)
            logger.error(f"✗ {error_msg}")

    def _validate_result(self, result: PipelineResult, source: str) -> None:
        """Validate a pipeline result."""
        if result.status == "error":
            error_msg = f"{source.upper()} {result.pipeline} error: {result.error}"
            self.errors.append(error_msg)
            logger.error(f"✗ {error_msg}")
        elif result.status == "ok":
            logger.info(f"✓ {result.pipeline}: {result.rows_inserted} rows")
        else:
            logger.info(f"⚠ {result.pipeline}: {result.status}")

    def _generate_report(self) -> dict[str, Any]:
        """Generate comprehensive test report."""
        logger.info("\n" + "=" * 80)
        logger.info("TEST SUMMARY")
        logger.info("=" * 80)

        duration = (self.end_time - self.start_time).total_seconds()

        # Count results
        total_results = sum(len(v) for v in self.results.values())
        error_results = sum(1 for r in self._all_results() if r.status == "error")
        ok_results = sum(1 for r in self._all_results() if r.status == "ok")

        report = {
            "timestamp": self.start_time.isoformat(),
            "duration_seconds": duration,
            "total_pipeline_executions": total_results,
            "successful": ok_results,
            "failed": error_results,
            "errors": self.errors,
            "warnings": self.warnings,
            "by_source": {
                "mineduc": self._format_source_results("mineduc"),
                "simce": self._format_source_results("simce"),
                "sige": self._format_source_results("sige"),
            }
        }

        # Log summary
        logger.info(f"Total executions: {total_results}")
        logger.info(f"Successful: {ok_results}")
        logger.info(f"Failed: {error_results}")
        logger.info(f"Duration: {duration:.2f}s")

        if self.errors:
            logger.error(f"\n❌ {len(self.errors)} ERROR(S) FOUND:")
            for i, err in enumerate(self.errors, 1):
                logger.error(f"  {i}. {err}")
        else:
            logger.info("\n✓ All pipelines completed without errors!")

        if self.warnings:
            logger.warning(f"\n⚠ {len(self.warnings)} WARNING(S):")
            for i, warn in enumerate(self.warnings, 1):
                logger.warning(f"  {i}. {warn}")

        logger.info("\n" + "=" * 80)
        return report

    def _format_source_results(self, source: str) -> dict[str, Any]:
        """Format results for a specific source."""
        results = self.results[source]
        if not results:
            return {"executions": 0, "results": []}

        formatted = []
        for r in results:
            formatted.append({
                "pipeline": r.pipeline,
                "status": r.status,
                "rows_inserted": r.rows_inserted,
                "rows_read": r.rows_read,
                "error": r.error,
            })

        return {
            "executions": len(results),
            "results": formatted,
        }

    def _all_results(self) -> list[PipelineResult]:
        """Get all results."""
        return sum(self.results.values(), [])

    def save_report(self, output_path: Path | str = "pipeline_test_report.json") -> None:
        """Save report to JSON file."""
        output_path = Path(output_path)
        report = self._generate_report()

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"\nReport saved to: {output_path}")


def main() -> int:
    """Main entry point."""
    try:
        runner = PipelineTestRunner(data_root="data")
        report = runner.run_all()
        runner.save_report("pipeline_test_report.json")

        # Exit code based on errors
        return 1 if runner.errors else 0

    except Exception as exc:
        logger.error(f"Fatal error: {exc}")
        return 1


if __name__ == "__main__":
    exit(main())
