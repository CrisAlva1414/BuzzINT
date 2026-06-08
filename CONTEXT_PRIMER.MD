# BuzzINT — Context Primer

> Este documento permite retomar el proyecto desde cero en cualquier
> sesión de trabajo sin perder contexto. Leerlo antes de cualquier
> implementación de Sprint 3.

---

## Qué es BuzzINT

MVP académico de inteligencia educativa para un colegio piloto chileno.
Integra fuentes públicas (MINEDUC, SIMCE) con datos internos (SIGE)
para dar a equipos directivos herramientas de análisis de desempeño.

**No es un SaaS. No es multi-tenant. No está expuesto a internet.**
Corre en un NAS con Docker en red local cerrada. Sin auth en MVP.

---

## Estado al iniciar Sprint 3

La capa de datos está completa:
- Gold layer PostgreSQL con Galaxy Schema (6 dims + 5 facts)
- 3 extractors (Playwright + httpx)
- 5 normalizers (RAR/CSV → CSV normalizado)
- 3 loaders (COPY masivo + execute_values)
- 3 pipelines orquestadores
- ~112 tests pasando

Lo que falta: analytics pipeline + API FastAPI + Dashboard Streamlit.

---

## El colegio piloto

- Un solo establecimiento educacional chileno
- RBD configurado en .env como RBD_PILOTO
- Datos SIGE son los únicos datos internos (calificaciones por curso)
- Datos MINEDUC y SIMCE son el contexto externo para benchmarking

---

## Decisiones de diseño ya tomadas

**Una sola API FastAPI** — no dos APIs separadas.
Ver ADR-003 para la razón de psycopg2 en loaders vs SQLAlchemy en API.

**Streamlit para el dashboard** — mismo repo Python, sin React.
Lee de la API (o directo de BD si conviene simplificar).

**Analytics pre-computado** — no corre en cada request.
AnalyticsPipeline corre al final de cada ETL pipeline y escribe en
gold.analytics_establecimiento. La API solo lee lo que ya está calculado.

**Sin auth** — MVP en red local, no justifica la complejidad.
Ver ADR-005 para la deuda técnica registrada.

---

## Las tres capas del producto final

### 1. Dashboard de métricas vivas
Streamlit con datos actualizados en cada sync SIGE o descarga MINEDUC.
Gráficos de SIMCE histórico, calificaciones internas, matrícula, docentes.

### 2. Módulos Explain
Páginas Streamlit que combinan dato + contexto + texto explicativo
en lenguaje no técnico para directores sin alfabetización estadística.
"Tu colegio está en el percentil 65 de colegios similares en tu región.
Eso significa que..."

### 3. Proyecciones
El sistema calcula una tendencia por regresión lineal sobre la serie
histórica de cada métrica y proyecta el valor esperado para el período
siguiente. El director ve: "Si seguís al ritmo actual, a fin de año
deberías tener un SIMCE de ~255 en matemática."
Las alertas se disparan cuando el valor real se desvía del esperado.

---

## Análisis disponibles con los datos actuales

Los siguientes análisis son posibles sin datos adicionales,
solo cruzando las tablas del gold layer:

**Desde fact_simce:**
- Serie histórica SIMCE por grado (4°, 8° básico, 2° medio)
- Benchmarking vs región y vs mismo GSE
- Brecha entre pruebas (mat vs lect, evolución de brechas)

**Desde fact_calificaciones × fact_simce:**
- Brecha entre rendimiento interno y SIMCE estandarizado
  (políticamente sensible — colegios que "inflan" notas internas)
- Correlación promedio interno → resultado SIMCE

**Desde fact_docentes × fact_simce:**
- Correlación horas docentes → puntaje SIMCE controlando por GSE
- Asignaturas con mayor/menor cobertura horaria

**Desde fact_matricula:**
- Evolución de matrícula total
- Composición SEP (prioritarios, preferentes)
- Tasa de retención año a año

**Desde fact_calificaciones:**
- Distribución de notas por asignatura y curso
- Tasa de aprobación/reprobación por grado
- Asistencia promedio y su relación con rendimiento

---

## Flujo de datos Sprint 3

```
ETL Pipeline (ya existe)
    ↓
gold layer PostgreSQL (ya existe)
    ↓
AnalyticsPipeline (Sprint 3 — Fase A)
    ↓
gold.analytics_establecimiento (Sprint 3 — tabla nueva)
    ↓
FastAPI /metrics + /insights (Sprint 3 — Fase B)
    ↓
Streamlit Dashboard (Sprint 3 — Fase C)
```

---

## Variables de entorno nuevas para Sprint 3

```bash
# Agregar a .env y .env.example
RBD_PILOTO=00001234          # RBD del colegio piloto
STREAMLIT_PORT=8501          # Puerto del dashboard (default Streamlit)
API_BASE_URL=http://localhost:8000  # Para que Streamlit llame a la API
```

---

## Dependencias nuevas para Sprint 3

```
# Agregar a scraper/requirements.txt
scipy>=1.12
scikit-learn>=1.4
streamlit>=1.32
plotly>=5.20
sqlalchemy>=2.0      # ya puede estar, verificar
```

---

## Lo que NO hacer en Sprint 3

- No implementar auth (ADR-005 — deuda técnica registrada)
- No escalar a multi-tenant (es un colegio piloto)
- No usar modelos ML complejos — regresión lineal es suficiente
  y explicable para el director
- No crear una segunda API separada para analytics
- No correr analytics en cada request HTTP
- No tocar archivos marcados como CONGELADOS en CODEBASE_STATE.md

---

## Cómo retomar en una nueva sesión

1. Leer este documento
2. Leer CODEBASE_STATE.md para ver qué está implementado
3. Leer SPRINTS.md para ver el diseño detallado de lo pendiente
4. Verificar que el gold layer tiene datos: `SELECT COUNT(*) FROM gold.fact_simce`
5. Implementar en el orden definido en SPRINTS.md sección "Orden de implementación"