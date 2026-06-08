# BuzzINT — Especificación técnica para agentes

> **LEER PRIMERO.** Este documento es el prompt maestro para cualquier sesión agentica de reescritura.  
> Contiene los contratos, convenciones y reglas que TODOS los módulos deben cumplir.  
> Antes de escribir cualquier línea de código, confirma que entiendes cada sección.

---

## CONTEXTO DEL PROYECTO

BuzzINT es un pipeline ETL + API para inteligencia educativa chilena.

**Fuentes:** MINEDUC Datos Abiertos · SIMCE (Agencia de Calidad) · SIGE (actas PDF)  
**Stack:** Python 3.12 · FastAPI · PostgreSQL 15 · psycopg2 (loaders) · SQLAlchemy (API) · Playwright · Pydantic v2  
**Base de datos:** Galaxy schema en esquema `gold` — ver `db/inits/01_schema.sql`

**Invariante absoluta:** El SQL en `db/inits/` NO se modifica. Los loaders se adaptan al schema, nunca al revés.

---

## REGLA 0: QUÉ NO CAMBIAR

Los siguientes archivos están congelados. No los modifiques bajo ninguna circunstancia:

- `db/inits/01_schema.sql` — schema Gold Layer
- `db/inits/02_seed.sql` — seed de dimensiones sintéticas
- `core/crypto.py` — encriptación Fernet (no tocar)
- `core/hashing.py` — SHA-256 (no tocar)

---

## CONVENCIÓN 1: CONFIGURACIÓN

**Un único punto de verdad.** Todos los módulos importan settings desde:

```python
from scraper.core.config import settings
```

**Nombres canónicos de variables** (usar SIEMPRE estos, no os.getenv directo):

```python
settings.postgres_host      # str, default "localhost"
settings.postgres_port      # int, default 5432
settings.postgres_db        # str, default "mineduc_intelligence"
settings.postgres_user      # str
settings.postgres_password  # str
settings.database_url       # property — postgresql+psycopg://...
settings.fernet_key         # str
settings.data_raw_path      # str, default "data_raw"
settings.playwright_headless # bool, default True
settings.log_level          # str, default "INFO"
```

**PROHIBIDO:** `os.getenv("PG_HOST")`, `os.environ.get(...)`, leer `.env` directamente.  
**PROHIBIDO:** Variables de entorno con nombres distintos a los de Settings.

---

## CONVENCIÓN 2: LOGGING

**Patrón en cada módulo:**

```python
import logging
logger = logging.getLogger(__name__)
```

**Formato único** — solo se configura en entrypoints:

```python
# En api/main.py y en if __name__ == "__main__" de scripts standalone:
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
```

**PROHIBIDO:**
- `logging.basicConfig(...)` dentro de funciones o al importar un módulo
- `print()` para mensajes de estado (solo para output de CLI al usuario final)
- Niveles inventados o handlers adicionales sin justificación

**Niveles esperados:**
- `DEBUG` — detalle de filas, chunks, paths intermedios
- `INFO` — inicio/fin de fases, conteos relevantes
- `WARNING` — archivo no encontrado pero recuperable, columna ausente, encoding fallback
- `ERROR` — excepción capturada, fila inválida no recuperable
- `CRITICAL` — solo para fallos que impiden arrancar el sistema

---

## CONVENCIÓN 3: SCHEMAS PYDANTIC

Todos los modelos de datos de la API y pipelines se definen en `core/schemas.py`.  
Usar **Pydantic v2** (`from pydantic import BaseModel, Field`).

### Modelos requeridos

```python
from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime
from pathlib import Path

class PipelineResult(BaseModel):
    job_id: str
    status: Literal["ok", "error", "partial"]
    source: Literal["mineduc", "simce", "sige"]
    pipeline: str                    # nombre del sub-pipeline, e.g. "load_alumnos"
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    error: str | None = None
    started_at: datetime
    finished_at: datetime

class JobStatus(BaseModel):
    job_id: str
    status: Literal["running", "ok", "error", "partial"]
    source: str
    pipeline: str
    progress_pct: int | None = None  # 0-100, None si no disponible
    started_at: datetime
    finished_at: datetime | None = None
    result: PipelineResult | None = None

class FileEntry(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    processed: bool
    processed_at: datetime | None = None
    job_id: str | None = None
```

---

## CONVENCIÓN 4: INTERFAZ DE PIPELINE

Cada pipeline implementa `BasePipeline` de `pipelines/base.py`.

```python
from abc import ABC, abstractmethod
from pathlib import Path
from scraper.core.schemas import PipelineResult

class BasePipeline(ABC):
    
    @abstractmethod
    def run(self, source_path: Path, **kwargs) -> PipelineResult:
        """
        Ejecuta el pipeline completo para un archivo fuente.
        Debe ser idempotente: ejecutar dos veces el mismo archivo
        produce el mismo resultado final en la DB.
        """
        ...
    
    @abstractmethod
    def discover(self, source_dir: Path) -> list[Path]:
        """
        Retorna lista de archivos en source_dir que aún no han sido
        procesados exitosamente (según FileRegistry).
        Nunca lanza excepción — retorna lista vacía si no hay nada.
        """
        ...
```

**Reglas de implementación:**
- `run()` nunca lanza excepción al caller. Captura internamente y retorna `status="error"`.
- `run()` siempre registra en `gold.etl_control` al inicio y al fin.
- `discover()` usa `FileRegistry.is_processed(path)` — no consulta la DB directamente.
- Todo pipeline acepta `source_path` como `Path`, no como `str`.

---

## CONVENCIÓN 5: ESTRATEGIA DE CARGA EN DB

### Decisión por volumen

| Tabla | Estrategia |
|-------|-----------|
| `fact_matricula` | COPY masivo vía tabla staging UNLOGGED |
| `fact_calificaciones` | COPY masivo vía tabla staging UNLOGGED |
| `fact_simce` | `execute_values` en chunks de 5000 |
| `fact_establecimiento_anual` | `execute_values` en chunks de 5000 |
| `fact_docentes` | `execute_values` en chunks de 5000 |
| `dim_*` | upsert individual (volumen bajo) |

### PROHIBIDO en loaders

```python
# ❌ NUNCA — loop fila a fila con execute individual
for _, row in chunk.iterrows():
    cur.execute("INSERT INTO ...", (...))

# ✅ SIEMPRE — batch con execute_values
from psycopg2.extras import execute_values
execute_values(cur, "INSERT INTO ... VALUES %s ON CONFLICT ...", rows_list)
```

### Patrón COPY staging

```python
# 1. CREATE UNLOGGED TABLE stg_* (sin FK, sin índices)
# 2. COPY FROM STDIN → staging
# 3. INSERT INTO fact_* SELECT ... FROM stg_* JOIN dim_* ON ...
# 4. DROP TABLE stg_*
```

---

## CONVENCIÓN 6: MANEJO DE TIPOS EN LOADERS

Las funciones de coerción canónicas están en `db/loaders/db.py`. Usar SIEMPRE:

```python
from scraper.db.loaders.db import _str, _int, _float, _date, _rbd
```

**Comportamiento garantizado:**
- `_str(None)` → `None`
- `_str("nan")` → `None`
- `_str("  texto  ")` → `"texto"`
- `_int("3.0")` → `3`
- `_int("")` → `None`
- `_float("1.234,56")` → correctamente normalizado
- `_rbd("123")` → `"00000123"` (8 dígitos, zero-padded)
- `_date("20150312")` → `"2015-03-12"`

**PROHIBIDO:** Coerciones inline `int(row.get(...))` sin pasar por estas funciones.

---

## CONVENCIÓN 7: MANEJO DE ARCHIVOS

### FileRegistry

```python
class FileRegistry:
    def __init__(self, manifest_path: Path): ...
    def is_processed(self, path: Path) -> bool: ...
    def mark_processed(self, path: Path, result: PipelineResult) -> None: ...
    def pending(self, source_dir: Path, pattern: str = "*") -> list[Path]: ...
    def save(self) -> None: ...
```

El manifest persiste en `data/{source}/manifest.json`.  
La clave es el SHA-256 del archivo, no el nombre.  
`is_processed` retorna True solo si el último job terminó con `status="ok"`.

### Paths

```python
# SIEMPRE usar Path, nunca strings crudos
source_path = Path(settings.data_raw_path) / "mineduc" / "alumnos"

# SIEMPRE resolver a absoluto al inicio de cada run()
source_path = source_path.expanduser().resolve()

# SIEMPRE verificar existencia antes de operar
if not source_path.exists():
    raise FileNotFoundError(f"Fuente no encontrada: {source_path}")
```

---

## CONVENCIÓN 8: ESTRUCTURA DE MÓDULOS

### Imports en cada archivo

```python
# 1. stdlib
import logging
from pathlib import Path

# 2. third-party
import pandas as pd
import psycopg2

# 3. proyecto — siempre con prefijo completo
from scraper.core.config import settings
from scraper.core.schemas import PipelineResult
from scraper.db.loaders.db import get_conn, transaction, EtlRun
```

### if __name__ == "__main__"

Cada módulo que puede ejecutarse como script mantiene este bloque:

```python
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # Argumentos mínimos necesarios
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/...")
    result = MiPipeline().run(src)
    print(f"Status: {result.status} | Insertadas: {result.rows_inserted}")
```

---

## CONVENCIÓN 9: GESTIÓN DE ERRORES

```python
# Patrón estándar en run()
try:
    # operación
except SpecificException as exc:
    logger.error("Descripción del fallo: %s", exc)
    run_ctx.fail(str(exc))
    return PipelineResult(status="error", error=str(exc), ...)
finally:
    conn.close()
```

**Reglas:**
- Nunca silenciar excepciones con `except Exception: pass`
- Siempre loggear con `%s` formatting, nunca f-strings en logger (evita evaluación innecesaria)
- El finally siempre cierra la conexión
- Cleanup de archivos temporales en finally, no en except

---

## APÉNDICE: Checklist antes de hacer PR

- [ ] No hay `os.getenv()` directo — usar `settings.*`
- [ ] No hay `logging.basicConfig()` fuera de entrypoints
- [ ] No hay loops `iterrows()` con `execute()` individual en loaders
- [ ] Todas las funciones de coerción usan `_str`, `_int`, `_float`, `_rbd`, `_date`
- [ ] `run()` nunca lanza excepción al caller
- [ ] `run()` registra en `etl_control` al inicio y al fin
- [ ] Paths son `Path`, no `str`
- [ ] Imports ordenados: stdlib → third-party → proyecto
- [ ] `if __name__ == "__main__"` presente si el módulo es ejecutable como script
- [ ] Tests: al menos un test de contrato con fixture de 10 filas