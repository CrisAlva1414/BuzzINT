# BuzzINT — Convenciones de código

> Referencia rápida para el equipo. Para el detalle completo, ver `AGENT_SPEC.md`.

---

## Imports

```python
# Orden obligatorio: stdlib → third-party → proyecto
import logging
import os
from pathlib import Path
from typing import Literal

import pandas as pd
import psycopg2
from pydantic import BaseModel

from scraper.core.config import settings
from scraper.core.schemas import PipelineResult
from scraper.db.loaders.db import get_conn, transaction
```

---

## Logger

```python
# Al inicio de CADA módulo — nada más
logger = logging.getLogger(__name__)

# Usar siempre %s formatting, nunca f-strings
logger.info("Procesando %d filas en %s", len(df), path.name)
logger.error("Fallo al cargar: %s", exc)  # no: f"Fallo al cargar: {exc}"
```

---

## Settings

```python
# ✅ Correcto
from scraper.core.config import settings
conn = psycopg2.connect(host=settings.postgres_host, ...)

# ❌ Incorrecto
import os
host = os.getenv("PG_HOST", "localhost")
```

---

## Paths

```python
# ✅ Siempre Path, siempre resolver
def run(self, source_path: Path) -> PipelineResult:
    source_path = Path(source_path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

# ❌ Strings crudos
def run(self, source_path: str):
    if os.path.exists(source_path):
        ...
```

---

## Coerciones de tipo

```python
# ✅ Usar funciones de db.py
from scraper.db.loaders.db import _str, _int, _float, _rbd, _date

valor = _int(row.get("cod_depe"))     # None si vacío/nan
rbd   = _rbd(row.get("rbd"))          # "00001234" formato canónico
fecha = _date(row.get("fec_nac_alu")) # "2005-03-12"

# ❌ Coerciones inline sin validación
valor = int(row.get("cod_depe", 0))   # explota con "" o "nan"
```

---

## Carga en batch

```python
# ✅ execute_values para inserts en lote
from psycopg2.extras import execute_values

rows = [
    (_int(r.get("mrun")), _str(r.get("gen_alu")))
    for _, r in chunk.iterrows()
]
execute_values(
    cur,
    "INSERT INTO gold.dim_alumno (mrun, gen_alu) VALUES %s ON CONFLICT (mrun) DO NOTHING",
    rows,
)

# ❌ Loop fila a fila
for _, row in chunk.iterrows():
    cur.execute("INSERT INTO gold.dim_alumno ...", (...))
```

---

## run() en pipelines

```python
# ✅ Nunca lanza excepción — siempre retorna PipelineResult
def run(self, source_path: Path, **kwargs) -> PipelineResult:
    started = datetime.utcnow()
    try:
        # ... lógica ...
        return PipelineResult(status="ok", ...)
    except Exception as exc:
        logger.error("Pipeline falló: %s", exc)
        return PipelineResult(status="error", error=str(exc), ...)
    finally:
        if conn:
            conn.close()

# ❌ Lanza excepción al caller
def run(self, source_path: Path) -> None:
    # ... lógica que puede explotar ...
```

---

## Entrypoints (scripts standalone)

```python
# Al final de CADA módulo ejecutable como script
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/default/path")
    result = MiPipeline().run(src)
    print(f"Status: {result.status} | Insertadas: {result.rows_inserted:,}")
    sys.exit(0 if result.status == "ok" else 1)
```

---

## Nomenclatura

| Concepto | Convención | Ejemplo |
|---------|-----------|---------|
| Clases | PascalCase | `MineducPipeline`, `FileRegistry` |
| Funciones / métodos | snake_case | `run()`, `discover()`, `get_conn()` |
| Variables | snake_case | `source_path`, `rows_inserted` |
| Constantes | UPPER_SNAKE | `CHUNK_SIZE`, `LOADER_NAME` |
| Archivos de módulo | snake_case | `load_alumnos.py`, `base.py` |
| Clases de normalizer | PascalCase + sufijo | `SepAlumnosNormalizer` |
| Funciones helper privadas | _snake_case | `_str()`, `_rbd()`, `_detect_encoding()` |

---

## Nombres de columnas SQL vs Python

Las columnas en la DB usan `snake_case` minúsculas. Al leer CSVs, normalizar antes:

```python
# En normalizers — al leer el header del CSV
chunk.columns = [col.strip().lower().replace(" ", "_") for col in chunk.columns]
```

---

## Strings en queries SQL

```python
# ✅ Parametrización siempre — nunca interpolación
cur.execute(
    "SELECT establecimiento_id FROM gold.dim_establecimiento WHERE rbd = %s",
    (rbd_clean,),   # tupla, aunque sea un solo parámetro
)

# ❌ Interpolación de strings
cur.execute(f"SELECT ... WHERE rbd = '{rbd}'")  # SQL injection
cur.execute("SELECT ... WHERE rbd = '%s'" % rbd)  # también incorrecto
```

---

## Commits git

```
tipo(alcance): descripción corta en español

feat(loaders): agregar execute_values a load_cargos
fix(normalizer): corregir encoding fallback en Mineduc_alumnos
refactor(core): extraer get_logger() a logging.py
docs: actualizar ARCHITECTURE.md con Sprint 2
test(contracts): agregar test_pipeline_contract.py
```

Tipos: `feat` · `fix` · `refactor` · `docs` · `test` · `chore`