# Guía de Setup - MINEDUC Intelligence

El proyecto está completamente configurado. Esta guía describe cómo iniciar el desarrollo.

## ✅ Estado actual

- ✓ Estructura de directorios completa
- ✓ Virtual environment (.venv) creado y configurado
- ✓ Todas las dependencias instaladas
- ✓ Playwrite browser descargado
- ✓ Configuración base lista
- ✓ Modelos SQLAlchemy definidos
- ✓ FastAPI skeleton implementado

## 🚀 Próximos pasos

### 1. Generar Fernet Key para seguridad

```bash
source .venv/bin/activate
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copiar el output y pegarlo en `.env` como `FERNET_KEY`.

### 2. Configurar PostgreSQL

#### Opción A: PostgreSQL local (recomendado para dev)

```bash
# En Linux/macOS
brew install postgresql@16  # macOS
# o en Linux: sudo apt-get install postgresql-16

# Iniciar PostgreSQL
brew services start postgresql@16

# Crear base de datos y usuario
createdb mineduc_intelligence
createuser -P mineduc_admin
```

Luego actualizar `.env` con las credenciales.

#### Opción B: PostgreSQL con Docker

```bash
docker run -d \
  --name mineduc-postgres \
  -e POSTGRES_DB=mineduc_intelligence \
  -e POSTGRES_USER=mineduc_admin \
  -e POSTGRES_PASSWORD=your_password \
  -p 5432:5432 \
  postgres:16-alpine
```

### 3. Ejecutar migraciones Alembic

```bash
source .venv/bin/activate

# Inicializar Alembic (si no está hecho)
# alembic init alembic

# Generar migración inicial
alembic revision --autogenerate -m "Initial schema"

# Aplicar migraciones
alembic upgrade head
```

### 4. Iniciar el servidor de desarrollo

```bash
source .venv/bin/activate
uvicorn scraper.api.main:app --reload --host 0.0.0.0 --port 8000
```

Acceder a http://localhost:8000/docs para ver la documentación Swagger.

### 5. Correr tests

```bash
source .venv/bin/activate

# Todos los tests
pytest

# Solo unitarios (sin DB)
pytest -m unit

# Solo integración
pytest -m integration

# Con cobertura
pytest --cov=scraper --cov-report=html
```

## 📂 Estructura del proyecto

```
scraper/
├── api/              # FastAPI application
│   ├── main.py      # Entry point
│   ├── routers/     # API endpoints (director, sources, jobs)
│   └── schemas/     # Pydantic request/response models
├── extractors/       # Data extraction modules
│   └── base.py      # BaseExtractor abstract class
├── normalizers/      # Data normalization modules
│   └── base.py      # BaseNormalizer abstract class
├── db/
│   ├── models.py    # SQLAlchemy models
│   ├── session.py   # Database connection
│   └── migrations/  # Alembic migrations (auto-generated)
├── core/
│   ├── config.py    # Settings from .env
│   ├── crypto.py    # Encryption utilities
│   └── hashing.py   # Hashing utilities
└── scheduler/        # APScheduler jobs

tests/
├── conftest.py      # Shared fixtures
├── unit/            # Unit tests (no DB, no network)
└── integration/     # Integration tests (require PostgreSQL)
```

## 🔧 Desarrollo

### Crear un nuevo Extractor

1. Crear archivo en `scraper/extractors/`
2. Heredar de `BaseExtractor`
3. Implementar `extract()` método
4. Crear tests en `tests/unit/test_*.py`

```python
from scraper.extractors.base import BaseExtractor

class MiExtractor(BaseExtractor):
    async def extract(self, url: str) -> dict:
        contenido = await self.fetch(url)
        # Procesar contenido
        return {"rbd": "12345", "datos": ...}
```

### Crear un nuevo Normalizer

1. Crear archivo en `scraper/normalizers/`
2. Heredar de `BaseNormalizer`
3. Implementar `normalize()` método

```python
from scraper.normalizers.base import BaseNormalizer

class MiNormalizer(BaseNormalizer):
    async def normalize(self, raw_data: dict) -> dict:
        # Resolver RBD
        rbd = raw_data.get("rbd")
        if not self.validate_rbd(rbd):
            raise ValueError(f"RBD inválido: {rbd}")
        return {...}
```

## 🐳 Producción con Docker

```bash
# Build y correr
docker compose up --build

# Verificar salud
curl http://localhost:8000/health
```

## ⚠️ Notas importantes

- **No commitear `.env`** — solo `.env.example`
- **No commitear `.venv/`** — está en `.gitignore`
- **Nunca regenerar `FERNET_KEY`** en producción — rompe encriptación
- **Usar Alembic** para cambios de schema — nunca modificar tablas manualmente
- **Playwright headless** — siempre usar en producción (`PLAYWRIGHT_HEADLESS=true`)

## 📚 Recursos

- Docs del proyecto: Ver `AGENTS.md`
- FastAPI: https://fastapi.tiangolo.com/
- SQLAlchemy Async: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Alembic: https://alembic.sqlalchemy.org/
- Playwright: https://playwright.dev/python/

---

**¿Preguntas?** Revisar `AGENTS.md` para convenciones de código y mejores prácticas.
