set -euo pipefail

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

# ── Colores ───────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Argumentos ───────────────────────────────────────────────
RESET=0
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --reset) RESET=1; shift ;;
        *)       EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# ── 1. docker-compose ─────────────────────────────────────────
info "Iniciando contenedor Postgres..."

if [[ $RESET -eq 1 ]]; then
    warn "--reset: eliminando volumen anterior"
    docker-compose -f "$COMPOSE_FILE" down -v 2>/dev/null || true
fi

docker-compose -f "$COMPOSE_FILE" up -d

# ── 2. Esperar a que Postgres esté listo ──────────────────────
info "Esperando a que Postgres esté listo (puerto $PG_PORT)..."
MAX_WAIT=60
WAITED=0
until pg_isready -h localhost -p "$PG_PORT" -U buzzint -d buzzint -q 2>/dev/null; do
    sleep 2
    WAITED=$((WAITED + 2))
    if [[ $WAITED -ge $MAX_WAIT ]]; then
        error "Postgres no respondió en ${MAX_WAIT}s"
        docker-compose -f "$COMPOSE_FILE" logs --tail=30 postgres
        exit 1
    fi
done
ok "Postgres listo (${WAITED}s)"

# ── 3. Aplicar DDL si no se aplicó automáticamente ───────────
# docker-entrypoint-initdb.d solo se ejecuta en bases nuevas.
# Para bases existentes usamos psql directamente (idempotente por IF NOT EXISTS).
info "Aplicando DDL y seed (idempotente)..."
PSQL="psql -h localhost -p $PG_PORT -U buzzint -d buzzint -q"

$PSQL -f "$SCRIPT_DIR/scraper/db/inits/01_schema.sql" \
    && ok "01_schema.sql aplicado" \
    || { error "Fallo en 01_schema.sql"; exit 1; }

$PSQL -f "$SCRIPT_DIR/scraper/db/inits/02_seed.sql" \
    && ok "02_seed.sql aplicado" \
    || { error "Fallo en 02_seed.sql"; exit 1; }

# ── 4. Instalar dependencias Python ──────────────────────────
info "Verificando dependencias Python..."
pip install -q -r "$SCRIPT_DIR/requirements.txt" --break-system-packages 2>/dev/null \
    || pip install -q -r "$SCRIPT_DIR/requirements.txt"

# ── 5. Ejecutar orquestador ───────────────────────────────────
info "Iniciando carga Gold Layer..."
info "Data root: $DATA_ROOT"

cd "$SCRIPT_DIR"
python -m scraper.db.loaders.orchestrate \
    --data-root "$DATA_ROOT" \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"

echo ""
ok "ETL completado. Conectar con:"
echo "  psql -h localhost -p $PG_PORT -U buzzint -d buzzint"
echo "  \\dt gold.*"