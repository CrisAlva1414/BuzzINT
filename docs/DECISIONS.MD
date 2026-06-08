# BuzzINT — Decisiones de Diseño (ADR)

> **Formato:** Cada decisión registra el contexto, las opciones consideradas,
> la decisión tomada y sus consecuencias. Esto evita repetir debates ya resueltos.

---

## ADR-001 — Galaxy Schema en PostgreSQL

**Estado:** Aceptado  
**Fecha:** 2025-06

### Contexto

El proyecto necesita responder preguntas analíticas multi-dimensionales:
- ¿Cómo evolucionó el SIMCE de 4° básico de un RBD específico entre 2018-2023?
- ¿Qué establecimientos SLEP tienen mayor varianza en calificaciones promedio vs su GSE?
- ¿Hay correlación entre horas docentes (cargos) y resultados SIMCE en básica rural?

### Opciones consideradas

1. Modelo relacional OLTP normalizado (3NF)
2. Modelo plano (una tabla enorme por fuente)
3. Galaxy Schema (star schema con dimensiones conformadas)

### Decisión

Galaxy Schema con 6 dimensiones conformadas y 5 fact tables.

### Consecuencias

**Positivo:**
- Queries analíticas simples — joins directos sin subconsultas anidadas
- Dimensiones conformadas permiten cruzar fuentes (SIMCE con matrícula con cargos)
- `etl_control` da trazabilidad sin overhead de audit tables

**Negativo:**
- Más complejidad de carga (resolución FK en cada pipeline)
- SCD Type-1 en dim_establecimiento pierde historial de cambios de nombre/dependencia
- Fact_matricula con ~40M filas/año requiere estrategia de particionado en producción

### Mitigaciones pendientes

- Evaluar particionado por `agno` en `fact_matricula` y `fact_calificaciones`
- Considerar SCD Type-2 para `dim_establecimiento` si el historial de dependencias es necesario

---

## ADR-002 — COPY masivo para fact_matricula

**Estado:** Aceptado  
**Fecha:** 2025-06

### Contexto

`fact_matricula` recibe ~40M filas por año de ejecución. Los loops Python con `execute()` individual toman 8+ horas en pruebas preliminares.

### Opciones consideradas

1. `execute()` individual en loop Python
2. `executemany()` con psycopg2
3. `execute_values()` en chunks de 5000
4. `COPY FROM STDIN` con tabla staging UNLOGGED + resolución FK en SQL

### Decisión

COPY masivo con staging (opción 4) para `fact_matricula` y `fact_calificaciones`.  
`execute_values()` en chunks para el resto.

### Mediciones de referencia

| Estrategia | 1M filas | Estimado 40M |
|-----------|---------|-------------|
| execute individual | ~45 min | ~30 horas |
| executemany | ~8 min | ~5 horas |
| execute_values (chunks 5K) | ~2 min | ~80 min |
| COPY staging | ~25 seg | ~17 min |

### Consecuencias

**Positivo:** Carga de alumnos en ~17 min en lugar de horas.  
**Negativo:** La tabla staging es UNLOGGED (no se recupera en crash de DB). Esto es aceptable porque los datos fuente persisten en disco y se puede re-ejecutar.

---

## ADR-003 — psycopg2 en loaders, SQLAlchemy en API

**Estado:** Aceptado  
**Fecha:** 2025-06

### Contexto

Los loaders necesitan `COPY FROM STDIN` (no disponible nativamente en SQLAlchemy) y control granular de transacciones. La API necesita un ORM para queries dinámicas con filtros.

### Decisión

- **Loaders ETL:** psycopg2 directamente con helpers en `db/loaders/db.py`
- **API FastAPI:** SQLAlchemy 2.x con el mismo `DATABASE_URL` de `settings`

### Consecuencias

Dos clientes de DB distintos. No es un problema porque operan en contextos separados (ETL batch vs API request-response). El schema es la interfaz común.

---

## ADR-004 — SIGE como único pipeline manual

**Estado:** Aceptado  
**Fecha:** 2025-06

### Contexto

SIGE (Sistema de Información General de Educación) requiere credenciales del establecimiento y contiene datos sensibles: calificaciones individuales vinculadas a MRUN (identificador personal del alumno).

### Decisión

SIGE no tiene cron job automático. Solo se ejecuta cuando el usuario lo dispara explícitamente (upload de PDF o click en "sincronizar"). El pipeline de SIGE:
1. No persiste credenciales en el sistema (el usuario hace login en browser visible)
2. Requiere acción afirmativa del usuario en cada ejecución
3. Los PDFs descargados se almacenan localmente pero no se transmiten

### Consecuencias

Los datos SIGE siempre son más "frescos" que los de MINEDUC (que tienen latencia de 6-12 meses por la cadencia de publicación).

---

## ADR-005 — Sin auth en API (MVP)

**Estado:** Aceptado — Temporal  
**Fecha:** 2025-06

### Contexto

El MVP es para demostración académica en entorno controlado.

### Decisión

La API no implementa autenticación en el MVP. Todos los endpoints son públicos.

### Deuda técnica registrada

En producción se requiere:
- OAuth2 con JWT para la API
- Roles: admin (puede disparar todos los pipelines) vs viewer (solo consulta datos)
- Rate limiting en endpoints que disparan pipelines
- Audit log de quién disparó qué pipeline y cuándo

---

## ADR-006 — manifest.json como FileRegistry primario

**Estado:** Aceptado  
**Fecha:** 2025-06

### Contexto

Necesitamos saber qué archivos ya fueron procesados para evitar re-procesar en cada ejecución del pipeline.

### Opciones consideradas

1. Tabla de control en PostgreSQL
2. manifest.json en disco (ya existente en extractors)
3. Ambos (manifest.json como cache rápido, DB como fuente de verdad)

### Decisión

`manifest.json` como fuente primaria para el FileRegistry, respaldado por `etl_control` en DB.

**Razón:** El manifest.json ya existe y es mantenido por los extractors. Agregar una consulta a DB para saber si hay que procesar un archivo introduce latencia y acoplamiento innecesario en la fase de descubrimiento (que ocurre antes de abrir la conexión DB).

**Regla de desempate:** Si el manifest dice "procesado" pero el registro en DB no existe, se re-procesa. El manifest se actualiza solo cuando la DB confirma éxito.

---

## ADR-007 — Normalizers sin framework ETL externo

**Estado:** Aceptado  
**Fecha:** 2025-06

### Contexto

Opciones como Apache Airflow, Prefect o dbt podrían orquestar los pipelines con más sofisticación.

### Decisión

Sin frameworks ETL externos. La orquestación es Python puro con `orchestrate.py` y luego FastAPI BackgroundTasks.

**Razones:**
1. Overhead de infraestructura no justificado para un MVP académico
2. La complejidad de los pipelines es manejable con el patrón `BasePipeline`
3. `etl_control` provee el historial y trazabilidad básica sin Airflow DAGs

### Revisión futura

Si los pipelines crecen a más de 10 steps o requieren paralelismo complejo, evaluar Prefect 2.x (más liviano que Airflow, compatible con Python puro).