# BuzzINT

Plataforma de inteligencia educativa para establecimientos chilenos.  
Integra fuentes OSINT públicas del sistema escolar con datos internos para análisis comparativo.

---

## ¿Qué hace?

Descarga, normaliza y carga automáticamente datos de tres fuentes:

| Fuente | Datos | Frecuencia |
|--------|-------|-----------|
| MINEDUC Datos Abiertos | Directorio de establecimientos, matrícula SEP, cargos docentes | Cron job (anual) |
| SIMCE / Agencia de Calidad | Resultados por RBD, comuna y región (2013-present) | Cron job (al publicarse) |
| SIGE | Actas de calificaciones del propio establecimiento | Manual (usuario) |

Los datos se almacenan en un Galaxy Schema PostgreSQL optimizado para queries analíticas.

---

## Stack

- **Backend:** Python 3.12 · FastAPI · psycopg2 · SQLAlchemy 2.x
- **Datos:** PostgreSQL 15 · Pydantic v2 · pandas
- **Scraping:** Playwright · httpx
- **Archivos:** rarfile · pdfplumber · charset-normalizer

---

## Inicio rápido

```bash
# Clonar y configurar
git clone <repo>
cd buzzint
cp .env.example .env   # editar con credenciales

# Base de datos
docker compose up -d db
psql -h localhost -U mineduc_admin -d mineduc_intelligence \
  -f scraper/db/inits/01_schema.sql \
  -f scraper/db/inits/02_seed.sql   \
  -f scraper/db/inits/03_patches.sql

# Instalar
pip install -e ".[dev]"
playwright install chromium

# Verificar (sin tocar la DB)
BUZZINT_ENV=dev python -m scraper.db.loaders.load_dev alumnos --dry-run

# Correr tests
pytest tests/ -v
```

---

## Uso por capa

### Extractors (descarga)

```bash
# MINEDUC — todas las fuentes
python -m scraper.extractors.mineduc_downloader --dry-run

# SIMCE — categorías 2 a 10
python -m scraper.extractors.simce_downloader --cat-min 2 --cat-max 10 --dry-run

# SIGE — requiere credenciales en .env
python -m scraper.extractors.sige_downloader --years 2023 2024
```

### Normalizers (RAR/PDF → CSV)

```bash
python -m scraper.normalizers.mineduc_alumnos_normalizer --mode delta
python -m scraper.normalizers.simce_normalizer --mode delta
python -m scraper.normalizers.sige_normalizer --mode delta
```

### Loaders (CSV → PostgreSQL)

```bash
# ETL pesado — correr en PC de desarrollo (BUZZINT_ENV=dev)
BUZZINT_ENV=dev python -m scraper.db.loaders.load_dev alumnos
BUZZINT_ENV=dev python -m scraper.db.loaders.load_dev establecimientos
BUZZINT_ENV=dev python -m scraper.db.loaders.load_dev cargos
BUZZINT_ENV=dev python -m scraper.db.loaders.load_dev simce --gran rbd

# SIGE sync — corre en OrangePi (BUZZINT_ENV=prod)
BUZZINT_ENV=prod python -m scraper.db.loaders.load_sige
```

### Pipelines (todo junto)

```python
from scraper.pipelines.mineduc import MineducPipeline
from scraper.pipelines.simce   import SimcePipeline
from scraper.pipelines.sige    import SigePipeline

# Normalizar y cargar alumnos nuevos
MineducPipeline().run(Path("data/mineduc/processed/mineduc_alumnos.csv"), step="load")

# Sincronización SIGE completa
SigePipeline().run_all(Path("data/sige/raw"))
```

---

## Documentación

| Documento | Para quién | Contenido |
|-----------|-----------|-----------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Equipo + revisores | Visión completa del sistema |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Equipo | Por qué se eligió cada diseño |
| [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) | Desarrolladores | Guía de estilo y patrones |
| [`docs/AGENT_SPEC.md`](docs/AGENT_SPEC.md) | Agentes IA | Spec técnico para sesiones agenticas |
| [`docs/CONTEXT_PRIMER.md`](docs/CONTEXT_PRIMER.md) | Agentes IA | Contexto comprimido (~180 líneas) |
| [`docs/SPRINTS.md`](docs/SPRINTS.md) | Equipo + agentes | Plan de sprints con prompts listos |
| [`docs/CODEBASE_STATE.md`](docs/CODEBASE_STATE.md) | Equipo | Estado actual y checklist |

---

## Estado del proyecto

```
✅ Schema PostgreSQL (Gold Layer + 03_patches.sql)
✅ Extractors        (3 downloaders con base compartida)
✅ Normalizers       (5 módulos con base compartida + NormalizerManifest)
✅ Loaders           (load_dev.py + load_alumnos.py + load_sige.py)
✅ Core              (config · schemas · logging · pipelines ABC)
✅ Pipelines         (MineducPipeline · SimcePipeline · SigePipeline)
✅ Tests             (core · downloaders · normalizers · db — 112 tests)

⏳ API FastAPI       (Sprint 3 — ver docs/SPRINTS.md)
```

---

## Variables de entorno

```bash
# Requeridas
POSTGRES_PASSWORD=<requerido>
FERNET_KEY=<generar: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Entorno de ejecución
BUZZINT_ENV=dev   # dev=X5680 (COPY masivo, chunks 50K) | prod=OrangePi (chunks 500)

# Solo para sincronizar SIGE
SIGE_USER=12345678-9
SIGE_PASSWORD=<password>
```

Ver `.env.example` para la lista completa.

---

## Licencia

Proyecto académico — Universidad de _____. 2025.