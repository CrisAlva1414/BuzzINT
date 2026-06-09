#!/bin/bash
set -euo pipefail

# ============================================================================
# Robust ETL Runner with Better Error Handling & Connection Management
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
DATA_ROOT="${BUZZINT_DATA_ROOT:-$(realpath "$SCRIPT_DIR/data")}"
PG_PORT="${PG_PORT:-5432}"

export PG_HOST=localhost
export PG_PORT
export PG_DB=buzzint
export PG_USER=buzzint
export PG_PASSWORD=buzzint
export BUZZINT_DATA_ROOT="$DATA_ROOT"

# Colors for output
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ============================================================================
# 1. Docker Management
# ============================================================================
info "Checking Docker containers..."

RUNNING=$(docker-compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | wc -l)
if [ "$RUNNING" -eq 0 ]; then
    info "Starting PostgreSQL container..."
    docker-compose -f "$COMPOSE_FILE" up -d
    ok "Container started"
else
    ok "Container already running"
fi

# ============================================================================
# 2. Wait for PostgreSQL with Health Check
# ============================================================================
info "Waiting for PostgreSQL to be ready..."

MAX_WAIT=120
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if docker exec buzzint-postgres pg_isready -U buzzint -d buzzint -q 2>/dev/null; then
        ok "PostgreSQL ready (${WAITED}s)"
        break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    echo -n "."
done

if [ $WAITED -ge $MAX_WAIT ]; then
    error "PostgreSQL did not become ready in ${MAX_WAIT}s"
    docker-compose -f "$COMPOSE_FILE" logs --tail=50 postgres
    exit 1
fi

# ============================================================================
# 3. Apply DDL (idempotent)
# ============================================================================
info "Applying database schema..."

for sql_file in 01_schema.sql 02_seed.sql 03_patches.sql; do
    sql_path="$SCRIPT_DIR/scraper/db/inits/$sql_file"
    if [ ! -f "$sql_path" ]; then
        error "Missing $sql_file"
        exit 1
    fi

    info "  Applying $sql_file..."
    if docker exec -i buzzint-postgres psql -U buzzint -d buzzint -q < "$sql_path"; then
        ok "  ✓ $sql_file"
    else
        error "  ✗ $sql_file failed"
        exit 1
    fi
done

# ============================================================================
# 4. Python Dependencies
# ============================================================================
info "Verifying Python dependencies..."

cd "$SCRIPT_DIR"
if ! pip install -q -r requirements.txt 2>/dev/null; then
    warn "pip install with --break-system-packages failed, trying without..."
    pip install -q -r requirements.txt || {
        error "Failed to install dependencies"
        exit 1
    }
fi
ok "Dependencies ready"

# ============================================================================
# 5. Run ETL with Robust Error Handling
# ============================================================================
info "Starting ETL pipeline..."
info "Data root: $DATA_ROOT"

EXTRA_ARGS=()
if [ $# -gt 0 ]; then
    EXTRA_ARGS=("$@")
fi

cd "$SCRIPT_DIR"
set +e  # Don't exit on Python error - let us handle it

python -m scraper.db.loaders.orchestrate \
    --data-root "$DATA_ROOT" \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}" 2>&1 | tee etl_run.log

ETL_EXIT_CODE=$?
set -e

if [ $ETL_EXIT_CODE -ne 0 ]; then
    error "ETL failed with exit code $ETL_EXIT_CODE"
    error "Check etl_run.log for details"
    error ""
    error "Common solutions:"
    error "  1. Container crashed: docker logs buzzint-postgres"
    error "  2. Schema issues: docker exec buzzint-postgres psql -U buzzint -d buzzint -c '\\dt gold.*'"
    error "  3. Retry: $0 --skip-download"
    exit $ETL_EXIT_CODE
fi

# ============================================================================
# 6. Post-Run Verification
# ============================================================================
ok "ETL completed successfully!"
echo ""
echo "Connection details:"
echo "  psql -h localhost -p $PG_PORT -U buzzint -d buzzint"
echo ""
echo "Useful queries:"
echo "  \\dt gold.*           (list tables)"
echo "  SELECT COUNT(*) FROM gold.fact_matricula;  (verify data load)"
echo "  SELECT * FROM gold.etl_control ORDER BY run_id DESC LIMIT 5;  (load history)"
