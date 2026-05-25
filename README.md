# MINEDUC Intelligence Platform

> Plataforma de extracción, normalización y análisis de datos educativos públicos chilenos. Combina fuentes OSINT abiertas del Ministerio de Educación con acceso autenticado a sistemas institucionales para generar inteligencia accionable sobre establecimientos educacionales.

---

## ¿Qué hace este proyecto?

Este sistema automatiza la recolección y normalización de datos educativos provenientes de múltiples fuentes del ecosistema MINEDUC, permitiendo:

- **Monitoreo continuo** de indicadores educativos por establecimiento, comuna, provincia y región
- **Detección de cambios** en documentos y datasets mediante comparación por hash
- **Cruce de fuentes** heterogéneas (CSV, PDF, HTML scrapeado) bajo un modelo de datos canónico unificado
- **Exposición estructurada** para dashboards y RAG (Retrieval-Augmented Generation)

El sistema diferencia entre datos macro de acceso público (gestionados por administrador vía cron) y datos institucionales que requieren autenticación (gestionados por un rol director vía panel).

---

## Fuentes de datos

| Fuente | Tipo de acceso | Contenido principal |
|--------|---------------|---------------------|
| [Datos Abiertos MINEDUC](https://datosabiertos.mineduc.cl/) | Público | Matrícula, asistencia, rendimiento, dotación docente, infraestructura (CSV + PDF codebook) |
| [SIMCE](https://www.simce.cl/) | Público / Autenticado | Resultados por establecimiento: puntajes, distribución de estándares, ejes de habilidad |
| [SIGE](https://sige.mineduc.cl/) | Autenticado (ClaveÚnica) | Datos históricos de gestión escolar desde ~2010 |
| [Trayectorias](https://trayectorias.mineduc.gob.cl/) | Autenticado (ClaveÚnica) | Indicadores de trayectoria estudiantil |

La autenticación en sistemas institucionales utiliza [ClaveÚnica](https://claveunica.gob.cl/), el sistema centralizado de identidad digital del Estado de Chile.

---

## Arquitectura

El sistema sigue una arquitectura **Medallion de 3 capas** para garantizar trazabilidad completa desde el dato crudo hasta la visualización:

```
┌─────────────────────────────────────────────────────────┐
│  BRONZE (Raw)     — Datos tal como llegan, inmutables   │
│  SILVER (Staging) — Parseados, tipados, RBD canónico    │
│  GOLD (Canonical) — Entidades resueltas, listo para BI  │
└─────────────────────────────────────────────────────────┘
```

```
scraper-system/
├── scraper/
│   ├── api/                  # FastAPI — endpoints panel director
│   ├── extractors/           # httpx async + playwright (ClaveÚnica)
│   │   ├── datos_abiertos.py
│   │   ├── simce.py
│   │   ├── sige.py
│   │   └── trayectorias.py
│   ├── normalizers/          # CSV parsing, PDF codebook, staging
│   ├── scheduler/            # APScheduler — cron datos macro
│   ├── db/                   # SQLAlchemy models + Alembic migrations
│   └── core/                 # Config, Fernet crypto, hash utils
├── postgres/
│   └── init.sql
├── frontend/                 # Dashboard + RAG (etapa 3)
├── docker-compose.yml        # Solo producción
├── .env.example
└── README.md
```

### Componentes principales

| Componente | Tecnología | Rol |
|------------|-----------|-----|
| Extracción HTTP | `httpx` (async) | Descarga de CSVs, HTML y recursos públicos |
| Renderizado JS | `playwright` | Login ClaveÚnica y sitios con JS crítico |
| Scheduling | `APScheduler` | Cron jobs para datos macro |
| API interna | `FastAPI` | Panel director: credenciales + trigger manual |
| ORM / Migrations | `SQLAlchemy` + `Alembic` | Modelos y versionado de schema |
| Base de datos | `PostgreSQL 16` | Almacenamiento de las 3 capas |
| Cifrado credenciales | `cryptography` (Fernet) | Credenciales institucionales en reposo |
| Reverse proxy | `Caddy` | TLS automático, exposición por subdominio |
| Acceso remoto | `Tailscale` | Red privada sin exposición pública |

---

## Modelo de identidad de entidades

El identificador universal que une todas las fuentes es el **RBD (Rol Base de Datos)**, asignado por MINEDUC a cada establecimiento educacional. Aunque cada CSV usa nombres de columna distintos para referenciarlo, el pipeline de normalización lo resuelve siempre a `establecimientos.rbd` en la capa Gold.

```
datos_abiertos.csv["RBD"]     ─┐
sige.csv["cod_rbd"]            ├──→ establecimientos.rbd (Gold)
simce.html[td scrapeado]       ─┘
```

---

## Requisitos previos

### Ejecución local (desarrollo)

- Python `3.11+`
- PostgreSQL `16` corriendo localmente (o vía Docker standalone)
- `playwright` con browsers instalados (`playwright install chromium`)

### Producción (homelab / NAS)

- Docker Engine `24+`
- Docker Compose `v2`
- Arquitectura `ARM64` o `amd64`
- Caddy + Tailscale configurados en el host

---

## Instalación local (desarrollo)

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/mineduc-intelligence.git
cd mineduc-intelligence

# 2. Crear y activar entorno virtual
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

# 3. Instalar dependencias
pip install -r scraper/requirements.txt
pip install -r scraper/requirements-dev.txt   # testing + linting

# 4. Instalar browsers para playwright
playwright install chromium

# 5. Configurar variables de entorno
cp .env.example .env
# Editar .env con credenciales de desarrollo

# 6. Levantar solo la base de datos con Docker
docker compose up postgres -d

# 7. Aplicar migraciones
cd scraper
alembic upgrade head

# 8. Levantar el servicio en modo desarrollo
uvicorn scraper.api.main:app --reload --port 8000
```

---

## Despliegue en producción (Docker)

```bash
# 1. Copiar y configurar variables de entorno
cp .env.example .env
# Editar .env con credenciales reales

# 2. Build y deploy completo
docker compose up -d --build

# 3. Verificar estado
docker compose ps
docker compose logs scraper -f
```

El stack expone únicamente los puertos necesarios dentro de la red Docker interna. Caddy maneja el TLS y el ruteo externo.

---

## Variables de entorno

```bash
# .env.example

# Base de datos
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=mineduc_intel
POSTGRES_USER=scraper_user
POSTGRES_PASSWORD=changeme

# Cifrado de credenciales institucionales
FERNET_KEY=                    # generado con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Playwright
PLAYWRIGHT_HEADLESS=true

# Scheduler (cron expresiones)
CRON_DATOS_ABIERTOS=0 3 * * 1  # lunes 3am
CRON_SIMCE_PUBLICO=0 4 * * 1
```

---

## Testing

```bash
# Correr todos los tests
pytest

# Con cobertura
pytest --cov=scraper --cov-report=term-missing

# Solo un módulo
pytest tests/test_extractors.py -v

# Solo tests unitarios (sin integración, sin DB)
pytest -m unit
```

Los tests de integración requieren la base de datos activa. Usar el marcador `@pytest.mark.integration` para diferenciarlos de los unitarios.

---

## Roadmap

- [x] Arquitectura y modelo de datos
- [ ] Scaffold Docker + PostgreSQL + modelos SQLAlchemy
- [ ] Extractor `datos_abiertos` (httpx + CSV + hash)
- [ ] Extractor `simce` acceso público (apoderado)
- [ ] Autenticación ClaveÚnica con playwright
- [ ] Extractores SIGE + Trayectorias (director)
- [ ] APScheduler + cron macro data
- [ ] Normalizers CSV + PDF codebook
- [ ] FastAPI panel director (CRUD credenciales + trigger manual)
- [ ] Dashboard frontend
- [ ] Pipeline RAG

---

## Licencia

MIT License — ver [LICENSE](LICENSE) para detalles.

Los datos expuestos por este sistema son de carácter público o están sujetos a los términos de uso de cada plataforma MINEDUC. El uso de credenciales institucionales es responsabilidad del operador del sistema.