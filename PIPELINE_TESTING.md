# Pipeline Testing Guide

This document describes how to run and test all BuzzINT pipelines (MINEDUC, SIMCE, SIGE).

## Overview

The project includes two comprehensive testing systems:

1. **`test_run_all_pipelines.py`** — Full end-to-end pipeline runner with detailed reporting
2. **`test_pipeline_validation.py`** — Unit tests validating pipeline structure and discovery

## Quick Start

### Run All Pipelines

```bash
# Run pipelines with current data state
python run_pipeline_tests.py

# Run with sample data (if no real data exists)
python run_pipeline_tests.py --create-samples

# Reset registries and run again
python run_pipeline_tests.py --reset

# Skip database operations (test normalizers only)
python run_pipeline_tests.py --skip-db

# Combine options
python run_pipeline_tests.py --reset --create-samples --skip-db
```

### Check Pipeline Status

```bash
# Show status of all pipelines without running
python run_pipeline_tests.py --status

# Clean registries and allow reprocessing
python run_pipeline_tests.py --clean
```

### Run Validation Tests

```bash
# Run all validation tests
python -m pytest tests/test_pipeline_validation.py -v

# Run specific test class
python -m pytest tests/test_pipeline_validation.py::TestPipelineDiscovery -v

# Run with coverage
python -m pytest tests/test_pipeline_validation.py --cov=scraper.pipelines
```

## What's Tested

### Integration Tests (`test_run_all_pipelines.py`)

✓ **Data Structure Validation**
  - Verifies all required directories exist
  - Creates missing directories
  - Reports warnings for missing data

✓ **Pipeline Discovery**
  - MINEDUC: Finds CSV files in `data/mineduc/raw/**/*.csv`
  - SIMCE: Finds RAR files in `data/simce/raw/**/*.rar`
  - SIGE: Finds PDF files in `data/sige/raw/**/*.pdf`

✓ **Pipeline Execution**
  - Runs each pipeline's discover phase
  - Executes normalization step
  - Skips database load if `--skip-db` is used
  - Captures results and errors

✓ **Report Generation**
  - JSON report saved to `pipeline_test_report.json`
  - Shows execution time, rows processed, errors
  - Details results by source (MINEDUC, SIMCE, SIGE)

### Unit Tests (`test_pipeline_validation.py`)

✓ **Discovery Tests**
  - Validates pipelines discover from raw/ directory
  - Ensures recursive discovery works (`**/*.ext`)

✓ **Structure Tests**
  - Confirms correct pipeline source names
  - Validates registry initialization

✓ **Directory Tests**
  - Checks all required data directories exist

## Features

### Sample Data Creation

When using `--create-samples`, the script creates minimal test files:

```
data/
├── mineduc/raw/
│   ├── alumnos/alumnos.csv
│   ├── cargos/cargos.csv
│   └── establecimientos/establecimientos.csv
├── simce/raw/
└── sige/raw/
```

These are valid CSV/RAR files that pipelines can process.

### Registry Management

Pipelines track processed files using `pipeline_registry.json` in each `processed/` directory.

```bash
# Reset registries to allow reprocessing
python run_pipeline_tests.py --reset

# Clean registries only
python run_pipeline_tests.py --clean
```

After resetting, files will be marked as "pending" again.

### Database Mode

By default, pipelines attempt to load data to PostgreSQL. If you only want to test data normalization:

```bash
python run_pipeline_tests.py --skip-db
```

This runs:
- ✓ Download phase (if applicable)
- ✓ Normalize phase
- ✗ Database load phase (skipped)

## Report Output

Example output from `pipeline_test_report.json`:

```json
{
  "timestamp": "2026-06-08T23:22:28.740000",
  "duration_seconds": 0.37,
  "total_pipeline_executions": 3,
  "successful": 3,
  "failed": 0,
  "errors": [],
  "warnings": [],
  "by_source": {
    "mineduc": {
      "executions": 3,
      "results": [
        {
          "pipeline": "load_alumnos",
          "status": "ok",
          "rows_inserted": 2,
          "rows_read": 2,
          "error": null
        }
      ]
    }
  }
}
```

## Common Commands

### Development

```bash
# Test with fresh data
python run_pipeline_tests.py --reset --create-samples --skip-db

# Validate fixes
python -m pytest tests/test_pipeline_validation.py -v

# Full test suite
python -m pytest tests/ -v
```

### Debugging

```bash
# Show current pipeline status
python run_pipeline_tests.py --status

# Run verbose output
python run_pipeline_tests.py --skip-db 2>&1 | grep -E "^[0-9]|✓|✗|⚠"

# Check what's in processed directories
ls -lh data/*/processed/*.csv
```

### Production

```bash
# Run full ETL with real data
./run_etl.sh

# With reset
./run_etl.sh --reset
```

## Bugs Fixed

### ✓ Bug 1: MINEDUC Wrong Discovery Directory
**File:** `scraper/pipelines/mineduc.py:26`

```python
# BEFORE (Wrong)
directory = Path(source_dir) if source_dir else self._proc

# AFTER (Fixed)
directory = Path(source_dir) if source_dir else self._raw
```

**Issue:** Pipeline was searching for source files in the already-processed directory instead of raw input directory.

### ✓ Bug 2: MINEDUC Pattern Doesn't Search Subdirectories
**File:** `scraper/pipelines/mineduc.py:26`

```python
# BEFORE (Limited)
return self._registry.pending(directory, pattern="*.csv")

# AFTER (Fixed)
return self._registry.pending(directory, pattern="**/*.csv")
```

**Issue:** Pattern `*.csv` only found files in root directory. Data is in subdirectories like `alumnos/`, `cargos/`, etc.

### ✓ Bug 3: SIMCE Pattern Doesn't Search Subdirectories
**File:** `scraper/pipelines/simce.py:28`

```python
# BEFORE (Limited)
return self._registry.pending(directory, pattern="*.rar")

# AFTER (Fixed)
return self._registry.pending(directory, pattern="**/*.rar")
```

**Issue:** Same pattern issue as MINEDUC.

## Testing Strategy

### Unit Level
- Test individual normalizers with mock data
- Test schema validation
- Run: `pytest tests/core_test.py tests/normalizers_test.py -v`

### Integration Level
- Test full pipelines from discovery to load
- Run: `python run_pipeline_tests.py --skip-db`

### End-to-End
- Test with real PostgreSQL (requires Docker)
- Run: `./run_etl.sh`

## Troubleshooting

### "No pending files found"

```bash
# Check what's in raw directories
ls -R data/*/raw/

# Reset registries
python run_pipeline_tests.py --reset
```

### Database connection errors

```bash
# Use --skip-db to test without database
python run_pipeline_tests.py --skip-db

# Check Docker
docker ps
docker-compose -f docker-compose.yml logs postgres
```

### Files not being discovered

```bash
# Check discovery with status
python run_pipeline_tests.py --status

# Verify file patterns
ls data/mineduc/raw/**/*.csv
ls data/simce/raw/**/*.rar
ls data/sige/raw/**/*.pdf
```

## Next Steps

- Add SIMCE sample RAR generation
- Add SIGE sample PDF generation
- Implement automated test schedule
- Add CI/CD pipeline integration
