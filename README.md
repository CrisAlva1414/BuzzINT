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
  -f scraper/db/inits/02_seed.sql

# Instalar
pip install -e ".[dev]"
playwright install chromium

# Verificar
python -m scraper.db.loaders.orchestrate --dry-run
```

---

## Documentación

| Documento | Para quién | Contenido |
|-----------|-----------|-----------|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Equipo + revisores | Visión completa del sistema |
| [`DECISIONS.md`](docs/DECISIONS.md) | Equipo | Por qué se eligió cada diseño |
| [`CONVENTIONS.md`](docs/CONVENTIONS.md) | Desarrolladores | Guía de estilo y patrones |
| [`AGENT_SPEC.md`](docs/AGENT_SPEC.md) | Agentes IA | Spec técnico para reescritura agentica |
| [`CONTEXT_PRIMER.md`](docs/CONTEXT_PRIMER.md) | Agentes IA | Contexto comprimido para inicio de sesión |
| [`SPRINTS.md`](docs/SPRINTS.md) | Equipo + agentes | Plan de sprints con prompts listos |
| [`CODEBASE_STATE.md`](docs/CODEBASE_STATE.md) | Equipo | Estado actual, deuda técnica, checklist |

---

## Estado del proyecto

```
✅ Schema PostgreSQL (Gold Layer completo)
✅ Extractors (3 downloaders)
✅ Normalizers (5 módulos)
✅ Loaders (5 módulos + orquestador CLI)

⏳ Sprint 1A — Core contracts (schemas, logging, pipeline base)
⏳ Sprint 1B — Refactor loaders (eliminar loops iterrows)
⏳ Sprint 2  — Pipeline layer (BasePipeline + 3 pipelines)
⏳ Sprint 3  — API FastAPI
⏳ Sprint 4  — Tests de contrato
```

---

## Variables de entorno requeridas

```bash
POSTGRES_PASSWORD=<requerido>
FERNET_KEY=<generar con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Solo para sincronizar SIGE
SIGE_USER=12345678-9
SIGE_PASSWORD=<password>
```

Ver `.env.example` para la lista completa.

---

## Licencia

Proyecto académico — Universidad de _____. 2025.