#!/usr/bin/env python3
"""
Pipeline test runner with registry reset capability.

Usage:
    python run_pipeline_tests.py                 # Run tests with current state
    python run_pipeline_tests.py --reset         # Reset registries and run
    python run_pipeline_tests.py --status        # Show pipeline status only
    python run_pipeline_tests.py --clean         # Reset registries only
"""
import sys
import json
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tests.test_run_all_pipelines import PipelineTestRunner

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def reset_registries(data_root: Path = Path("data")) -> None:
    """Reset all pipeline registries to allow reprocessing."""
    logger.info("Resetting pipeline registries...")
    registries = [
        data_root / "mineduc" / "processed" / "pipeline_registry.json",
        data_root / "simce" / "processed" / "pipeline_registry.json",
        data_root / "sige" / "processed" / "pipeline_registry.json",
    ]

    for registry_path in registries:
        if registry_path.exists():
            registry_path.unlink()
            logger.info(f"✓ Removed: {registry_path.relative_to(data_root)}")
        else:
            logger.info(f"ℹ Already clean: {registry_path.relative_to(data_root)}")

    # Clean processed directories
    processed_dirs = [
        data_root / "mineduc" / "processed",
        data_root / "simce" / "processed",
        data_root / "sige" / "processed",
    ]

    for proc_dir in processed_dirs:
        if proc_dir.exists():
            # Keep the directory but remove CSV files (to reprocess)
            for csv_file in proc_dir.glob("*.csv"):
                csv_file.unlink()
                logger.info(f"✓ Removed: {csv_file.relative_to(data_root)}")


def show_status(data_root: Path = Path("data")) -> None:
    """Show pipeline status without running."""
    logger.info("\n" + "=" * 80)
    logger.info("PIPELINE STATUS")
    logger.info("=" * 80)

    sources = ["mineduc", "simce", "sige"]

    for source in sources:
        logger.info(f"\n{source.upper()}:")

        # Check raw directory
        raw_dir = data_root / source / "raw"
        raw_files = list(raw_dir.glob("**/*")) if raw_dir.exists() else []
        logger.info(f"  Raw files: {len([f for f in raw_files if f.is_file()])}")

        # Check processed directory
        proc_dir = data_root / source / "processed"
        proc_files = list(proc_dir.glob("*.csv")) if proc_dir.exists() else []
        logger.info(f"  Processed files: {len(proc_files)}")
        for f in proc_files:
            size_kb = f.stat().st_size / 1024
            logger.info(f"    - {f.name} ({size_kb:.1f} KB)")

        # Check registry
        registry_path = proc_dir / "pipeline_registry.json" if proc_dir else None
        if registry_path and registry_path.exists():
            with open(registry_path) as f:
                registry = json.load(f)
            logger.info(f"  Registry entries: {len(registry)}")
            for entry in registry.values():
                status = entry.get("status", "unknown")
                logger.info(f"    - {Path(entry.get('path', '')).name}: {status}")
        else:
            logger.info(f"  Registry: Not found")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run and test all BuzzINT pipelines")
    parser.add_argument("--reset", action="store_true", help="Reset registries before running")
    parser.add_argument("--clean", action="store_true", help="Reset registries only (don't run)")
    parser.add_argument("--status", action="store_true", help="Show status only (don't run)")
    parser.add_argument("--skip-db", action="store_true", help="Skip database operations (test normalizers only)")
    parser.add_argument("--create-samples", action="store_true", help="Create sample data if missing")
    parser.add_argument("--data-root", default="data", help="Path to data root directory")

    args = parser.parse_args()
    data_root = Path(args.data_root)

    # Handle clean operation
    if args.clean:
        reset_registries(data_root)
        logger.info("\n✓ Registries cleaned. Ready to rerun pipelines.")
        return 0

    # Handle status operation
    if args.status:
        show_status(data_root)
        return 0

    # Reset if requested
    if args.reset:
        reset_registries(data_root)

    # Run tests
    try:
        logger.info(f"Using data root: {data_root}")
        runner = PipelineTestRunner(
            data_root=data_root,
            skip_db=args.skip_db,
            create_samples=args.create_samples
        )
        report = runner.run_all()
        runner.save_report("pipeline_test_report.json")

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("FINAL STATUS")
        logger.info("=" * 80)
        logger.info(f"Errors: {len(runner.errors)}")
        logger.info(f"Warnings: {len(runner.warnings)}")

        # Save detailed report
        with open("pipeline_test_report.json") as f:
            full_report = json.load(f)

        logger.info("\nDetailed report saved to: pipeline_test_report.json")
        logger.info(f"By source:")
        for source, data in full_report.get("by_source", {}).items():
            executions = data.get("executions", 0)
            logger.info(f"  {source}: {executions} execution(s)")

        return 1 if runner.errors else 0

    except Exception as exc:
        logger.error(f"Fatal error: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
