# BuzzINT — Arquitectura del Sistema

> **Audiencia:** Equipo de desarrollo, revisores académicos  
> **Estado:** MVP funcional — capa de datos completa, API pendiente  
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
│                  API FastAPI        (pendiente)       │
│  /pipelines  /jobs  /upload  /data                   │
└───────────────────┬─────────────────────────────────┘
                    │
┌───────────────────▼─────────────────────────────────┐
│               PIPELINES                              │
│  MineducPipeline · SimcePipeline · SigePipeline      │
│  BasePipeline ABC · FileRegistry                     │
└──────┬──────────────┬──────────────────┬────────────┘
       │              │                  │
┌──────▼──────┐ ┌─────▼──────┐ ┌────────▼───────┐
│  Extractors │ │ Normalizers│ │    Loaders     │
│  (download) │ │ (RAR→CSV)  │ │  (CSV→DB)      │
│  manifest   │ │  manifest  │ │ load_dev.py    │
│  .json      │ │  .json     │ │ load_alumnos   │
└─────────────┘ └────────────┘ │ load_sige.py   │
                                └────────┬───────┘
                                         │
┌────────────────────────────────────────▼───────────┐
│              CORE COMPARTIDO                         │
│  config · logging · schemas (Pydantic) · db session  │
└──────────────────────────────────────┬─────────────┘
                                       │
┌──────────────────────────────────────▼─────────────┐
│              INFRAESTRUCTURA                         │
│  PostgreSQL Gold Layer  │  File storage (raw/proc)  │
│  dim_* · fact_*         │  manifest.json por fuente │
│  etl_control            │  normalizer_manifest.json │
│  env_profile            │  pipeline_registry.json   │
└─────────────────────────────────────────────────────┘
```

---

## 3. Estructura de directorios

```
buzzint/
├── scraper/
│   ├── core/
│   │   ├── config.py          # Settings (pydantic-settings), único .env reader
│   │   ├── logging.py         # get_logger() factory + configure()
│   │   ├── schemas.py         # PipelineResult, JobStatus, RunRequest, etc.
│   │   ├── crypto.py          # Fernet — CONGELADO
│   │   └── hashing.py         # SHA-256 — CONGELADO
│   │
│   ├── pipelines/
│   │   ├── base.py            # BasePipeline ABC + FileRegistry
│   │   ├── mineduc.py         # MineducPipeline
│   │   ├── simce.py           # SimcePipeline
│   │   └── sige.py            # SigePipeline (solo en OrangePi)
│   │
│   ├── extractors/
│   │   ├── downloader_base.py # sha256, manifest helpers, streaming
│   │   ├── mineduc_downloader.py
│   │   ├── simce_downloader.py
│   │   └── sige_downloader.py
│   │
│   ├── normalizers/
│   │   ├── normalizer_base.py              # sniff, prescan, iter_sources, NormalizerManifest
│   │   ├── mineduc_alumnos_normalizer.py
│   │   ├── mineduc_cargos_normalizer.py
│   │   ├── mineduc_establecimientos_normalizer.py
│   │   ├── sige_normalizer.py
│   │   └── simce_normalizer.py
│   │
│   ├── db/
│   │   ├── inits/
│   │   │   ├── 01_schema.sql  # CONGELADO — Gold Layer schema
│   │   │   ├── 02_seed.sql    # CONGELADO — dimensiones sintéticas
│   │   │   └── 03_patches.sql # constraint traslados, índices, vistas OrangePi
│   │   └── loaders/
│   │       ├── db.py          # get_conn, EtlRun, batch_upsert, coerciones, env profile
│   │       ├── load_alumnos.py  # COPY masivo ~40M filas (dev)
│   │       ├── load_dev.py      # establecimientos + cargos + simce (dev)
│   │       └── load_sige.py     # calificaciones + profesores (prod/OrangePi)
│   │
│   ├── scripts/
│   │   ├── inspect_cod_csvs.py
│   │   ├── inspect_integrity_csvs.py
│   │   └── inspect_sep_csvs.py
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
├── tests/
│   ├── conftest.py
│   ├── core_test_.py          # schemas · logging · FileRegistry · BasePipeline
│   ├── downloaders_test_.py   # 3 downloaders paralelos
│   ├── normalizers_test_.py   # 5 normalizers + NormalizerManifest
│   └── db_test_.py            # loaders + coerciones + detección entorno
│
├── docs/
│   ├── ARCHITECTURE.md        # este archivo
│   ├── CONVENTIONS.md
│   ├── DECISIONS.md
│   ├── AGENT_SPEC.md
│   ├── CONTEXT_PRIMER.md
│   ├── SPRINTS.md
│   └── CODEBASE_STATE.md
│
├── data/
│   ├── mineduc/raw/  · processed/
│   ├── simce/raw/    · processed/
│   └── sige/raw/     · processed/
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

| Tabla | Granularidad | Volumen est. | Estrategia | Corre en |
|-------|-------------|-------------|------------|---------|
| `fact_simce` | (rbd, agno, grado) | ~200K/año | execute_values | dev |
| `fact_establecimiento_anual` | (rbd, agno) | ~50K/año | execute_values | dev |
| `fact_docentes` | (rbd, agno, mrun_doc) | ~500K/año | execute_values | dev |
| `fact_matricula` | (mrun, rbd, agno, grado, ense) | ~40M/año | COPY masivo | dev |
| `fact_calificaciones` | (rbd, agno, grado, letra, orden, asig) | ~50K/sync | execute_values | OrangePi |

### Vistas

| Vista | Propósito |
|-------|-----------|
| `v_simce_completo` | SIMCE con contexto geográfico y escolar |
| `v_simce_serie_rbd` | Serie histórica por establecimiento |
| `v_calificaciones_completo` | Calificaciones con alumno y asignatura |
| `v_notas_long` | Unpivot de las 25 notas (formato long para analytics) |
| `v_sige_resumen` | Resumen por curso — **liviano para OrangePi 4GB** |

---

## 5. Flujo de datos

### MINEDUC (cron job, PC dev, sin auth)
```
cron → MineducPipeline.run(step="all")
  → mineduc_downloader  (Playwright + httpx → raw/, manifest.json)
  → mineduc_*_normalizer (RAR → CSV, NormalizerManifest)
  → load_dev / load_alumnos (COPY masivo o execute_values)
  → PostgreSQL Gold Layer
```

### SIMCE (cron job, PC dev, sin auth)
```
cron → SimcePipeline.run(step="all")
  → simce_downloader   (httpx API REST → raw/*.rar, manifest.json)
  → simce_normalizer   (unrar → simce__rbd/comuna/region.csv)
  → load_dev simce     (execute_values por granularidad)
  → PostgreSQL Gold Layer
```

### SIGE (manual, OrangePi, requiere auth)
```
usuario → POST /upload/sige (PDFs) o sige_downloader (Playwright login)
  → SigePipeline.run_from_upload()
  → sige_normalizer    (pdfplumber → sige_calificaciones.csv)
  → load_sige          (execute_values, chunks 500, pool 2)
  → PostgreSQL Gold Layer
```

---

## 6. Adaptación por entorno

```bash
BUZZINT_ENV=dev   # PC desarrollo (2× X5680)
BUZZINT_ENV=prod  # OrangePi 4GB
```

| Parámetro | dev | prod |
|-----------|-----|------|
| CHUNK_SIZE | 50 000 | 500 |
| POOL_SIZE | 10 | 2 |
| USE_COPY | True | False |
| Loaders que corren | todos | solo load_sige |

---

## 7. Estado del proyecto

| Componente | Estado |
|-----------|--------|
| SQL Schema + patches | ✅ Completo |
| Extractors (3) | ✅ Completo |
| Normalizers (5 + base) | ✅ Completo |
| Loaders (3 archivos) | ✅ Completo |
| Core (config · schemas · logging) | ✅ Completo |
| Pipelines (Base + 3) | ✅ Completo |
| Tests (4 archivos, ~112 tests) | ✅ Completo |
| API FastAPI | ⏳ Sprint 3 |

Ver `docs/SPRINTS.md` para el detalle del siguiente sprint.