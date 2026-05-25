# AGENTS.md — Copilot Agent Instructions

> Instrucciones persistentes para el agente de GitHub Copilot en este repositorio.
> Leer completo antes de generar cualquier código o modificar cualquier archivo.

---

## Contexto del proyecto

Sistema de scraping, normalización y análisis de datos educativos públicos chilenos (MINEDUC).
Pipeline Python puro organizado en 3 capas de datos (Bronze/Silver/Gold).
Stack: `httpx` + `playwright` + `FastAPI` + `SQLAlchemy` + `APScheduler` + `PostgreSQL`.

---

## Entorno de ejecución

### Regla fundamental: local ≠ Docker

| Modo | Cuándo se usa | Cómo se levanta |
|------|--------------|-----------------|
| **Local** | Desarrollo, debugging, tests | `uvicorn` directo + postgres standalone |
| **Docker** | Solo producción / deploy a NAS | `docker compose up --build` |

**Nunca generes instrucciones de `docker compose up` para tareas de desarrollo.**
**Nunca generes código que asuma que está corriendo dentro de un contenedor en modo dev.**

### Setup del entorno virtual

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r scraper/requirements.txt
pip install -r scraper/requirements-dev.txt
```

El venv **siempre** se llama `.venv` y vive en la raíz del repositorio.
El archivo `.venv/` está en `.gitignore` — nunca lo incluyas en commits.

Cuando el agente necesite correr código Python, siempre asumir que el venv está activado.
Si hay dudas, verificar con `which python` que apunte a `.venv/bin/python`.

---

## Estructura de directorios

```
mineduc-intelligence/
├── scraper/
│   ├── api/
│   │   ├── main.py            # Entry point FastAPI
│   │   ├── routers/           # Un archivo por dominio (director, sources, jobs)
│   │   └── schemas/           # Pydantic schemas de request/response
│   ├── extractors/            # Un módulo por fuente
│   │   ├── base.py            # Clase base abstracta para todos los extractors
│   │   ├── datos_abiertos.py
│   │   ├── simce.py
│   │   ├── sige.py
│   │   └── trayectorias.py
│   ├── normalizers/           # Un módulo por familia de datos
│   │   ├── base.py
│   │   ├── csv_normalizer.py
│   │   └── pdf_codebook.py
│   ├── scheduler/
│   │   └── jobs.py            # Definición de cron jobs APScheduler
│   ├── db/
│   │   ├── models.py          # Todos los modelos SQLAlchemy
│   │   ├── session.py         # Engine + SessionLocal + get_db
│   │   └── migrations/        # Alembic (autogenerado, no editar a mano)
│   ├── core/
│   │   ├── config.py          # Settings via pydantic-settings + .env
│   │   ├── crypto.py          # Fernet encrypt/decrypt para credenciales
│   │   └── hashing.py         # SHA-256 helpers para deduplicación
│   ├── requirements.txt
│   └── requirements-dev.txt
├── tests/
│   ├── conftest.py            # Fixtures compartidos (DB test, httpx mock)
│   ├── unit/                  # Sin DB, sin red
│   └── integration/           # Requieren postgres activo
├── postgres/
│   └── init.sql
├── docker-compose.yml
├── .env
├── .env.example
├── .gitignore
├── AGENTS.md                  # Este archivo
└── README.md
```

**Respetar esta estructura siempre.** No crear archivos fuera de los módulos indicados sin justificación explícita.

---

## Convenciones de código

### Python

- **Python 3.11+** — usar type hints en todas las funciones y métodos
- **async/await** para todo lo relacionado con I/O (httpx, SQLAlchemy async, playwright)
- **Pydantic v2** para schemas de API y validación
- **pydantic-settings** para configuración desde `.env`
- No usar `requests` — solo `httpx`
- No usar `selenium` — solo `playwright`
- No usar `print()` para logging — usar `logging` estándar con nivel apropiado

### Nombrado

```python
# Variables y funciones: snake_case
async def fetch_documento(url: str) -> bytes: ...

# Clases: PascalCase
class DatosAbiertosExtractor(BaseExtractor): ...

# Constantes: UPPER_SNAKE_CASE
MAX_RETRIES = 3

# Modelos SQLAlchemy: PascalCase, singular
class Establecimiento(Base): ...

# Tablas en DB: snake_case, plural
__tablename__ = "establecimientos"
```

### Estructura de un extractor

Todos los extractors **deben** heredar de `BaseExtractor` y respetar este contrato:

```python
class BaseExtractor:
    async def fetch(self, url: str) -> bytes: ...          # descarga raw
    async def compute_hash(self, content: bytes) -> str: ...  # SHA-256
    async def has_changed(self, url: str, hash: str) -> bool: ...  # vs DB
    async def save_raw(self, content: bytes, meta: dict) -> UUID: ...  # → Bronze
```

---

## Base de datos

### Migraciones

**Siempre usar Alembic.** Nunca modificar tablas manualmente en producción.

```bash
# Generar migración después de cambiar models.py
alembic revision --autogenerate -m "descripcion_del_cambio"

# Aplicar migraciones pendientes
alembic upgrade head

# Ver estado actual
alembic current
```

### Sessions

Usar siempre el patrón de dependency injection de FastAPI:

```python
from scraper.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

async def endpoint(db: AsyncSession = Depends(get_db)):
    ...
```

Nunca crear sessions manualmente dentro de endpoints.

### Identificador universal

El campo `rbd` (TEXT) es el identificador natural que une todas las fuentes.
Todo normalizer debe resolver su columna de establecimiento a `establecimientos.rbd` antes de escribir en staging.

---

## Testing

### Reglas generales

- **Unit tests** (`tests/unit/`): sin base de datos, sin red, sin playwright. Usar mocks.
- **Integration tests** (`tests/integration/`): requieren postgres local activo.
- Marcar tests con `@pytest.mark.unit` o `@pytest.mark.integration`.
- Cobertura mínima esperada: **70%** en módulos `extractors/` y `normalizers/`.

### Correr tests

```bash
# Todos
pytest

# Solo unitarios (sin DB)
pytest -m unit

# Solo integración
pytest -m integration

# Con cobertura
pytest --cov=scraper --cov-report=term-missing
```

### Fixtures disponibles (conftest.py)

```python
# DB de prueba (SQLite in-memory para unit, postgres test para integration)
@pytest.fixture
async def db_session(): ...

# Cliente httpx mockeado
@pytest.fixture
def mock_httpx_client(): ...

# Credenciales de prueba cifradas
@pytest.fixture
def sample_credentials(): ...
```

---

## Variables de entorno

**Nunca hardcodear credenciales o URLs en el código.**
Todo va en `.env` y se accede via `scraper.core.config.Settings`.

```python
from scraper.core.config import settings

settings.postgres_host   # ✓
"localhost"              # ✗ hardcodeado
```

El archivo `.env` **nunca** se commitea. Solo `.env.example` con valores vacíos o de ejemplo.

---

## Seguridad

- Las credenciales institucionales (ClaveÚnica) se **cifran con Fernet** antes de persistir en DB.
- La `FERNET_KEY` se genera una vez y nunca se regenera (rompe todos los datos cifrados).
- Playwright corre siempre en modo headless (`PLAYWRIGHT_HEADLESS=true`).
- El panel director no tiene autenticación en MVP — está protegido por Tailscale a nivel de red.

---

## Docker (solo producción)

El `docker-compose.yml` define:
- `postgres` — imagen `postgres:16-alpine` (ARM64 compatible)
- `scraper` — build desde `./scraper/Dockerfile`
- Red interna: `ministerial_net` (bridge)
- Volumen: `./data:/app/data` para PDFs y CSVs descargados

**No agregar** servicios a Docker Compose que no sean necesarios para producción.
**No exponer** puertos directamente — Caddy maneja el ruteo externo.

---

## Flujo de trabajo del agente

Antes de generar código para una nueva funcionalidad:

1. Identificar en qué módulo vive (`extractors/` | `normalizers/` | `api/` | `scheduler/`)
2. Verificar si existe una clase base que heredar
3. Generar el módulo con type hints completos
4. Generar el test unitario correspondiente en `tests/unit/`
5. Si toca DB: generar la migración Alembic necesaria
6. Actualizar `requirements.txt` si se agrega una dependencia nueva

---

## Lo que el agente NO debe hacer

- Crear archivos `.py` fuera de la estructura definida sin preguntar
- Usar `requests`, `selenium`, o `scrapy`
- Usar `print()` en lugar de `logging`
- Modificar `alembic/versions/` manualmente
- Hardcodear cualquier URL, credencial, o parámetro de conexión
- Generar código con `docker compose` para flujos de desarrollo
- Commitear `.env`, `.venv/`, `__pycache__/`, o archivos `*.pyc`