# AGENTS.md — Contrato del agente de código

> Instrucciones persistentes para sesiones agénticas en este repositorio.
> Leer completo antes de generar cualquier código o modificar cualquier archivo.
> Este archivo se actualiza al final de cada sesión significativa.

---

## Estado actual del proyecto

<!-- Actualizar al cierre de cada sesión agéntica -->

| Componente | Estado |
|---|---|
| Scaffold base (FastAPI + SQLAlchemy + Alembic) | ✅ completo |
| `simce_downloader.py` | ✅ funcional |
| `sige_downloader.py` | ✅ funcional |
| Extractores integrados al pipeline (Bronze) | 🔲 pendiente |
| Normalizers (Silver) | 🔲 pendiente |
| Capa Gold + resolución RBD | 🔲 pendiente |
| APScheduler jobs | 🔲 pendiente |
| FastAPI panel director | 🔲 pendiente |

---

## Entorno de ejecución

### Regla fundamental: local ≠ Docker

| Modo | Cuándo | Cómo |
|---|---|---|
| **Local** | Desarrollo, debugging, tests | `uvicorn` directo + postgres standalone |
| **Docker** | Solo producción / deploy a NAS | `docker compose up --build` |

- Nunca generar instrucciones de `docker compose up` para tareas de desarrollo.
- Nunca asumir que el código corre dentro de un contenedor en modo dev.
- El venv siempre se llama `.venv` y vive en la raíz del repositorio.
- Cuando el agente corra código Python: asumir venv activado. Verificar con `which python` si hay dudas.

---

## Estructura de directorios

```
mineduc-intelligence/
├── scraper/
│   ├── api/
│   │   ├── main.py
│   │   ├── routers/           # Un archivo por dominio (director, sources, jobs)
│   │   └── schemas/
│   ├── extractors/
│   │   ├── base.py            # Clase base abstracta
│   │   ├── datos_abiertos.py
│   │   ├── simce.py
│   │   ├── sige.py
│   │   └── trayectorias.py
│   ├── normalizers/
│   │   ├── base.py
│   │   ├── csv_normalizer.py
│   │   └── pdf_codebook.py
│   ├── scheduler/
│   │   └── jobs.py
│   ├── db/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── migrations/        # Alembic — no editar a mano
│   └── core/
│       ├── config.py
│       ├── crypto.py
│       └── hashing.py
├── postgres/
│   └── init.sql
├── docker-compose.yml
├── .env / .env.example
├── AGENTS.md
└── README.md
```

Respetar esta estructura. No crear archivos fuera de los módulos indicados sin justificación explícita.

---

## Convenciones de código

- **Python 3.11+** con type hints en todas las funciones y métodos
- **async/await** para todo I/O (httpx, SQLAlchemy async, playwright)
- **Pydantic v2** para schemas; **pydantic-settings** para config desde `.env`
- Solo `httpx` (no `requests`), solo `playwright` (no `selenium`)
- Solo `logging` estándar (no `print()`)

```python
# Nombrado
async def fetch_documento(url: str) -> bytes: ...   # snake_case
class DatosAbiertosExtractor(BaseExtractor): ...    # PascalCase
MAX_RETRIES = 3                                      # UPPER_SNAKE_CASE
class Establecimiento(Base): ...                     # modelos: singular
__tablename__ = "establecimientos"                   # tablas: plural
```

### Contrato de extractores

Todos heredan de `BaseExtractor`:

```python
class BaseExtractor:
    async def fetch(self, url: str) -> bytes: ...
    async def compute_hash(self, content: bytes) -> str: ...
    async def has_changed(self, url: str, hash: str) -> bool: ...
    async def save_raw(self, content: bytes, meta: dict) -> UUID: ...
```

Los scripts standalone actuales (`simce_downloader.py`, `sige_downloader.py`) son prototipos funcionales. Al integrarlos al pipeline deben refactorizarse para heredar de `BaseExtractor`.

---

## Base de datos

- **Siempre Alembic** para migraciones. Nunca modificar tablas manualmente.
- Sessions siempre via dependency injection de FastAPI (`Depends(get_db)`).
- El campo `rbd` (TEXT) es el identificador universal que une todas las fuentes. Todo normalizer debe resolver su columna de establecimiento a `establecimientos.rbd` antes de escribir en staging.

---

## Variables de entorno

Nunca hardcodear credenciales o URLs. Todo via `scraper.core.config.Settings`.

```python
from scraper.core.config import settings
settings.postgres_host   # ✓
"localhost"              # ✗
```

`.env` nunca se commitea. Solo `.env.example` con valores vacíos.

---

## Seguridad

- Credenciales institucionales (SIGE/Trayectorias) se cifran con **Fernet** antes de persistir.
- `FERNET_KEY` se genera una vez y nunca se regenera.
- Playwright corre siempre headless en producción (`PLAYWRIGHT_HEADLESS=true`). Modo visible solo para resolver captcha en login SIGE (flujo documentado en `sige_downloader.py`).
- Panel director sin autenticación en MVP — protegido por Tailscale a nivel de red.

---

## Decisiones de diseño registradas

<!-- Agregar entradas al cierre de sesiones donde se tome una decisión no trivial -->

| Fecha | Decisión | Razón |
|---|---|---|
| — | Login SIGE: browser visible para captcha → httpx para descarga | El captcha de SIGE no se puede resolver headless de forma confiable |
| — | SIMCE: scraping via REST JSON (`/rest/archivo/`) sin autenticación | La API pública expone UUIDs descargables directamente |

---

## Lo que el agente NO debe hacer

- Crear archivos `.py` fuera de la estructura sin preguntar
- Usar `requests`, `selenium`, `scrapy` o `print()`
- Modificar `alembic/versions/` manualmente
- Hardcodear URLs, credenciales o parámetros de conexión
- Commitear `.env`, `.venv/`, `__pycache__/`, archivos `*.pyc`
- Generar instrucciones de `docker compose` para flujos de desarrollo