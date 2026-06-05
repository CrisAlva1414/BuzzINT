# MINEDUC Intelligence Platform

> Pipeline de extracción, normalización y análisis de datos educativos públicos chilenos.

---

## ¿Qué es esto?

Un sistema que recolecta datos del ecosistema MINEDUC desde múltiples fuentes — algunas abiertas, otras con autenticación institucional — los normaliza bajo un modelo canónico y los expone para análisis y visualización.

El foco inicial es **inteligencia por establecimiento**: cruzar resultados SIMCE, datos de matrícula, dotación docente e indicadores de trayectoria bajo un único identificador (el RBD).

---

## Fuentes que scrapeamos

### Agencia de Calidad de la Educación — SIMCE
- **URL:** `https://informacionestadistica.agenciaeducacion.cl`
- **Acceso:** público (sin autenticación)
- **Mecanismo:** API REST JSON (`/rest/archivo/getAllByCategoriaVistaPublica/{cat_id}`) que expone UUIDs descargables. Iteramos sobre rangos de categorías (actualmente cat 2–60) y descargamos los archivos asociados (principalmente `.rar` con CSVs internos).
- **Contenido:** resultados SIMCE por establecimiento — puntajes, distribución de estándares de aprendizaje, ejes de habilidad, series históricas.
- **Script actual:** `scraper/extractors/simce_downloader.py`

### SIGE — Sistema de Información General de Estudiantes (MINEDUC)
- **URL:** `https://sige.mineduc.cl`
- **Acceso:** autenticado (credenciales institucionales propias de cada establecimiento)
- **Mecanismo:** login en browser visible (el operador resuelve el captcha manualmente una vez), luego las cookies de sesión se traspasan a `httpx` para la descarga masiva sin browser. Los PDFs de actas históricas se obtienen via POST a `/Sige/Reportes/ImprimirActasHisto`.
- **Contenido:** actas históricas de resultados por establecimiento, desde 2009 en adelante.
- **Script actual:** `scraper/extractors/sige_downloader.py`

### Datos Abiertos MINEDUC
- **URL:** `https://datosabiertos.mineduc.cl`
- **Acceso:** público
- **Contenido:** matrícula, asistencia, rendimiento, dotación docente, infraestructura (CSV + PDF codebook).
- **Script actual:** pendiente (`scraper/extractors/datos_abiertos.py`)

### Trayectorias Estudiantiles
- **URL:** `https://trayectorias.mineduc.gob.cl`
- **Acceso:** autenticado (ClaveÚnica)
- **Contenido:** indicadores de trayectoria estudiantil.
- **Script actual:** pendiente (`scraper/extractors/trayectorias.py`)

---

## Cómo funciona el pipeline

```
Fuentes externas
      │
      ▼
  BRONZE ── datos tal como llegan, inmutables, con hash de contenido
      │
      ▼
  SILVER ── parseados, tipados, RBD canónico resuelto
      │
      ▼
   GOLD  ── entidades resueltas, listo para BI / RAG
```

El identificador que une todo es el **RBD** (Rol Base de Datos), número asignado por MINEDUC a cada establecimiento. Cada fuente lo llama distinto (`RBD`, `cod_rbd`, columna scrapeada); el normalizer siempre lo resuelve a `establecimientos.rbd` antes de escribir en Silver.

---

## Deduplicación

Cada archivo descargado se verifica con SHA-256 contra el manifest local. Si el hash coincide con la descarga anterior, el archivo se saltea. Esto permite correr el scraper repetidamente sin re-descargar contenido que no cambió.

---

## Scheduling

<!-- Detallar cuando APScheduler esté integrado -->

Los datos públicos (SIMCE, Datos Abiertos) se actualizan via cron automático.  
Los datos autenticados (SIGE, Trayectorias) se disparan manualmente desde el panel director o via API.

---

## Panel director

<!-- Detallar cuando la API FastAPI esté operativa -->

Interfaz mínima para:
- Registrar y gestionar credenciales institucionales (almacenadas cifradas)
- Disparar descargas manualmente por fuente o establecimiento
- Ver estado de los últimos jobs

Accesible únicamente dentro de la red Tailscale.

---

## Stack técnico

| Capa | Tecnología |
|---|---|
| Extracción HTTP | `httpx` async |
| Navegación JS / captcha | `playwright` (Chromium) |
| Scheduling | `APScheduler` |
| API interna | `FastAPI` |
| ORM / Migraciones | `SQLAlchemy` + `Alembic` |
| Base de datos | `PostgreSQL 16` |
| Cifrado credenciales | `cryptography` (Fernet) |
| Acceso remoto | Tailscale |
| Reverse proxy | Caddy |

---

## Roadmap

- [x] Scaffold Docker + PostgreSQL + modelos SQLAlchemy
- [x] `simce_downloader` — descarga pública Agencia de Calidad
- [x] `sige_downloader` — descarga autenticada actas históricas
- [ ] Integrar downloaders al pipeline Bronze (heredar `BaseExtractor`)
- [ ] Normalizers CSV + PDF codebook (Silver)
- [ ] Capa Gold + resolución canónica de RBD
- [ ] APScheduler + cron datos macro
- [ ] FastAPI panel director
- [ ] Dashboard / visualización
- [ ] Pipeline RAG

---

## Licencia

MIT. Los datos expuestos son de carácter público o están sujetos a los términos de uso de cada plataforma MINEDUC. El uso de credenciales institucionales es responsabilidad del operador del sistema.