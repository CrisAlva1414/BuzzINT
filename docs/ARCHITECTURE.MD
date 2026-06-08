# BuzzINT — Arquitectura del Sistema

> **Audiencia:** Equipo de desarrollo, revisores académicos  
> **Estado:** MVP en construcción — capa de datos completa, API pendiente  
> **Última actualización:** 2025-06

---

## 1. Propósito

BuzzINT es una plataforma de inteligencia educativa que integra fuentes OSINT públicas del sistema escolar chileno con datos internos de un establecimiento, permitiendo comparar el desempeño propio contra el contexto regional y nacional.

**Fuentes de datos:**
- **MINEDUC Datos Abiertos** — directorio de establecimientos, matrícula SEP, cargos docentes (acceso público sin auth)
- **SIMCE / Agencia de Calidad** — resultados de pruebas estandarizadas por RBD, comuna y región (archivos RAR públicos)
- **SIGE** — actas de calificaciones internas del establecimiento (requiere credenciales, solo el usuario puede sincronizar)

---

## 2. Capas del sistema

```
┌─────────────────────────────────────────────────────┐
│                    CLIENTES                          │
│         Dashboard · CLI · Cron jobs externos         │
└───────────────────┬─────────────────────────────────┘
                    │ HTTP
┌───────────────────▼─────────────────────────────────┐
│                  API FastAPI                          │
│  /pipelines  /jobs  /upload  /data                   │
└───────────────────┬─────────────────────────────────┘
                    │
┌───────────────────▼─────────────────────────────────┐
│               CORE COMPARTIDO                        │
│  config · logging · schemas (Pydantic) · db session  │
└──────┬──────────────┬──────────────────┬────────────┘
       │              │                  │
┌──────▼──────┐ ┌─────▼──────┐ ┌────────▼───────┐
│  Pipeline   │ │  Pipeline  │ │   Pipeline     │
│  MINEDUC    │ │   SIMCE    │ │    SIGE        │
│  (cron)     │ │  (cron)    │ │ (upload/sync)  │
│ Downloader  │ │ Downloader │ │  Normalizer    │
│ Normalizer  │ │ Normalizer │ │  (PDF→CSV)     │
│  Loader     │ │  Loader    │ │   Loader       │
└──────┬──────┘ └─────┬──────┘ └────────┬───────┘
       │              │                  │
┌──────▼──────────────▼──────────────────▼───────────┐
│              INFRAESTRUCTURA                         │
│  PostgreSQL Gold Layer  │  File storage (raw/proc)  │
│  dim_* · fact_*         │  manifest.json por fuente │
│  etl_control            │                           │
└─────────────────────────────────────────────────────┘
```

---

## 3. Estructura de directorios

```
buzzint/
├── scraper/
│   ├── core/
│   │   ├── config.py          # Settings (pydantic-settings), único .env reader
│   │   ├── logging.py         # get_logger() factory — NO basicConfig aquí
│   │   ├── schemas.py         # PipelineResult, JobStatus, etc. (Pydantic v2)
│   │   └── db.py              # SQLAlchemy engine + session factory
│   │
│   ├── pipelines/             # PENDIENTE — reescritura Sprint 2
│   │   ├── base.py            # BasePipeline ABC + FileRegistry
│   │   ├── mineduc.py
│   │   ├── simce.py
│   │   └── sige.py
│   │
│   ├── extractors/            # EXISTENTE — funcional, interfaz a limpiar
│   │   ├── mineduc_downloader.py
│   │   ├── simce_downloader.py
│   │   └── sige_downloader.py
│   │
│   ├── normalizers/           # EXISTENTE — funcional, interfaz a limpiar
│   │   ├── Ingestor.py
│   │   ├── Mineduc_alumnos_normalizer.py
│   │   ├── Mineduc_cargos_normalizer.py
│   │   ├── Mineduc_establecimientos_normalizer.py
│   │   ├── Sige_normalizer.py
│   │   └── Simce_normalizer.py
│   │
│   ├── db/
│   │   ├── inits/
│   │   │   ├── 01_schema.sql  # COMPLETO — Gold Layer schema
│   │   │   └── 02_seed.sql    # COMPLETO — dimensiones sintéticas
│   │   └── loaders/           # EXISTENTE — funcional, loops a refactorizar
│   │       ├── db.py
│   │       ├── load_alumnos.py
│   │       ├── load_cargos.py
│   │       ├── load_establecimientos.py
│   │       ├── load_sige.py
│   │       ├── load_simce.py
│   │       └── orchestrate.py
│   │
│   └── api/                   # PENDIENTE — Sprint 3
│       ├── main.py
│       ├── dependencies.py
│       └── routers/
│           ├── pipelines.py
│           ├── jobs.py
│           ├── upload.py
│           └── data.py
│
├── data/
│   ├── mineduc/
│   │   ├── raw/               # Descargado por extractors
│   │   │   ├── alumnos/
│   │   │   ├── establecimientos/
│   │   │   ├── cargos/
│   │   │   └── evaluacion/
│   │   └── processed/         # Normalizado por normalizers
│   ├── simce/
│   │   ├── raw/
│   │   └── processed/
│   └── sige/
│       ├── raw/
│       └── processed/
│
├── docker-compose.yml
├── .env.example
└── pyproject.toml
```

---

## 4. Base de datos — Gold Layer (Galaxy Schema)

### Dimensiones

| Tabla | Clave de negocio | Fuente | Estrategia |
|-------|-----------------|--------|------------|
| `dim_territorio` | `cod_com` (CHAR 5) | mineduc_establecimientos | upsert |
| `dim_establecimiento` | `rbd` (CHAR 8) | mineduc_establecimientos | SCD Type-1 |
| `dim_tiempo_escolar` | `(agno, cod_grado)` | seed sintético | insert only |
| `dim_alumno` | `mrun` (BIGINT) | mineduc_alumnos + sige | upsert COALESCE |
| `dim_docente` | `mrun` (BIGINT) | mineduc_cargos + sige | upsert COALESCE |
| `dim_asignatura` | `(cod_ense, subsector)` | seed + cargos + sige | upsert |

### Facts

| Tabla | Granularidad | Volumen estimado | Estrategia de carga |
|-------|-------------|------------------|---------------------|
| `fact_simce` | (rbd, agno, cod_grado) | ~200K filas/año | COPY staging → upsert |
| `fact_establecimiento_anual` | (rbd, agno) | ~50K filas/año | chunk upsert |
| `fact_docentes` | (rbd, agno, mrun_doc, cod_ense) | ~500K filas/año | chunk upsert |
| `fact_matricula` | (mrun_alu, rbd, agno, cod_grado) | ~40M filas/año | COPY masivo |
| `fact_calificaciones` | (rbd, agno, cod_grado, letra, n_orden, asig_id) | ~10M filas/año | COPY staging |

### Control ETL

```sql
gold.etl_control   -- registro de cada ejecución de pipeline
  run_id, loader, source_file, status, rows_*, error_msg, started_at, finished_at
```

---

## 5. Flujo de datos por fuente

### MINEDUC (cron job, sin auth)
```
cron trigger
  → mineduc_downloader.py   (Playwright → httpx, manifest.json)
  → Mineduc_*_normalizer.py (RAR/CSV → CSV unificado, UTF-8-sig)
  → load_establecimientos / load_alumnos / load_cargos
  → PostgreSQL Gold Layer
```

### SIMCE (cron job, sin auth)
```
cron trigger
  → simce_downloader.py     (httpx API REST, manifest.json)
  → Simce_normalizer.py     (RAR → CSV por granularidad: rbd/comuna/region)
  → load_simce.py
  → PostgreSQL Gold Layer
```

### SIGE (manual / sincronización por usuario)
```
usuario sube PDFs o ejecuta sync
  → sige_downloader.py      (Playwright login + httpx PDFs)
  → Sige_normalizer.py      (PDF → sige_calificaciones.csv + sige_profesores.csv)
  → load_sige.py
  → PostgreSQL Gold Layer
```

---

## 6. Contratos de interfaz (estado objetivo)

### PipelineResult (Pydantic)
```python
class PipelineResult(BaseModel):
    job_id: str
    status: Literal["ok", "error", "partial"]
    source: str                    # "mineduc" | "simce" | "sige"
    pipeline: str                  # nombre del sub-pipeline
    rows_read: int
    rows_inserted: int
    rows_updated: int
    rows_skipped: int
    error: str | None
    started_at: datetime
    finished_at: datetime
```

### BasePipeline (ABC)
```python
class BasePipeline(ABC):
    @abstractmethod
    def run(self, source_path: Path, **kwargs) -> PipelineResult: ...
    
    @abstractmethod
    def discover(self, source_dir: Path) -> list[Path]:
        """Retorna archivos pendientes de procesar (no en FileRegistry)."""
        ...
```

### Endpoints API (diseño)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/pipelines/{source}/run` | Dispara pipeline completo (download + normalize + load) |
| `POST` | `/pipelines/{source}/run?step=normalize` | Dispara solo un paso |
| `GET` | `/pipelines/{source}/status` | Estado actual del pipeline |
| `GET` | `/jobs` | Historial de jobs (desde etl_control) |
| `GET` | `/jobs/{job_id}` | Detalle de un job |
| `POST` | `/upload/sige` | Sube archivos PDF SIGE para normalizar y cargar |
| `GET` | `/data/simce` | Query datos SIMCE con filtros |
| `GET` | `/data/establecimientos` | Query establecimientos |

---

## 7. Decisiones de diseño

**¿Por qué Galaxy Schema y no modelo relacional simple?**  
Las preguntas analíticas del dominio requieren joins multi-dimensionales (¿cómo evolucionó el SIMCE de 4° básico de un establecimiento específico comparado con su GSE regional entre 2018 y 2023?). Un modelo estrella/galaxy permite estas queries sin subconsultas anidadas.

**¿Por qué COPY masivo en fact_matricula?**  
Con ~40M filas/año, los loops Python fila a fila toman horas. `COPY FROM STDIN` con tabla staging temporal (sin índices, UNLOGGED) + resolución FK en SQL puro es 10-50x más rápido.

**¿Por qué SIGE es el único pipeline manual?**  
SIGE requiere credenciales del establecimiento y contiene datos sensibles (calificaciones individuales con MRUN). No debe automatizarse sin consentimiento explícito del usuario en cada ejecución.

**¿Por qué manifest.json por fuente?**  
Permite deduplicación por hash SHA-256 sin consultar la base de datos. El FileRegistry futuro lo envuelve pero el formato raw es legible y recuperable independientemente.

---

## 8. Estado actual y próximos sprints

| Componente | Estado | Sprint |
|-----------|--------|--------|
| SQL Schema (01_schema.sql) | ✅ Completo | — |
| SQL Seed (02_seed.sql) | ✅ Completo | — |
| Extractors (3 downloaders) | ✅ Funcional | Sprint 1: limpiar interfaz |
| Normalizers (5 módulos) | ✅ Funcional | Sprint 1: limpiar interfaz |
| Loaders (5 módulos + orchestrate) | ✅ Funcional | Sprint 1: eliminar loops fila a fila |
| core/schemas.py | ❌ Pendiente | Sprint 1 |
| core/logging.py | ❌ Pendiente | Sprint 1 |
| pipelines/ (ABC + 3 pipelines) | ❌ Pendiente | Sprint 2 |
| API FastAPI | ❌ Pendiente | Sprint 3 |

Ver `SPRINTS.md` para el detalle de cada sprint.