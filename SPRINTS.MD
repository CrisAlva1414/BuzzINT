# BuzzINT — Sprints

> Ver `ARCHITECTURE.md` para el contexto general del sistema.

---

## Sprint 1 — Schema + Extractors ✅
## Sprint 2 — Normalizers + Loaders + Pipelines + Tests ✅
## Sprint 3 — Analytics + API + Dashboard (pendiente)

---

# Sprint 3 — Diseño completo

> **Prerequisito:** Gold layer poblado con datos reales.
> Correr pipelines en este orden antes de implementar:
> 1. establecimientos → 2. simce → 3. cargos → 4. alumnos → 5. sige

---

## Fase A — Analytics Pipeline

### Propósito

Calcular métricas derivadas y proyecciones que la API y el dashboard
van a consumir. Corre automáticamente al final de cada pipeline ETL
y escribe en `gold.analytics_establecimiento`.

El modelo no corre en cada request HTTP — se pre-computa y se almacena.
La API solo lee lo que analytics ya calculó.

### Nueva tabla en BD

```sql
CREATE TABLE IF NOT EXISTS gold.analytics_establecimiento (
    analytics_id    BIGSERIAL PRIMARY KEY,
    rbd             CHAR(8)      NOT NULL,
    agno            SMALLINT     NOT NULL,
    mes             SMALLINT,                    -- NULL para métricas anuales
    metrica         VARCHAR(60)  NOT NULL,        -- ver catálogo abajo
    valor_real      NUMERIC(10,4),
    valor_esperado  NUMERIC(10,4),               -- proyección del modelo
    desviacion_pct  NUMERIC(8,2),                -- (real - esperado) / esperado * 100
    percentil_reg   NUMERIC(5,2),                -- posición vs región (0-100)
    percentil_gse   NUMERIC(5,2),                -- posición vs mismo GSE (0-100)
    n_muestra       INT,                         -- establecimientos en el grupo de comparación
    computed_at     TIMESTAMP    NOT NULL DEFAULT now(),
    pipeline_run_id BIGINT REFERENCES gold.etl_control(run_id),
    UNIQUE (rbd, agno, mes, metrica)
);

COMMENT ON TABLE gold.analytics_establecimiento IS
    'Métricas pre-computadas y proyecciones por establecimiento.
     Escrita por analytics.py, leída por la API y Streamlit.';

CREATE INDEX IF NOT EXISTS idx_analytics_rbd_agno
    ON gold.analytics_establecimiento (rbd, agno);

CREATE INDEX IF NOT EXISTS idx_analytics_metrica
    ON gold.analytics_establecimiento (metrica, agno);
```

### Catálogo de métricas

| metrica | descripción | fuente | frecuencia |
|---------|-------------|--------|------------|
| `simce_mat` | Puntaje SIMCE matemática | fact_simce | anual |
| `simce_lect` | Puntaje SIMCE lectura | fact_simce | anual |
| `simce_cie` | Puntaje SIMCE ciencias | fact_simce | anual |
| `prom_interno` | Promedio de calificaciones internas | fact_calificaciones | mensual/anual |
| `asistencia` | Porcentaje de asistencia promedio | fact_calificaciones | mensual/anual |
| `tasa_aprobacion` | % alumnos con situacion_final=APR | fact_calificaciones | anual |
| `brecha_simce_interno` | Diferencia entre promedio interno y puntaje SIMCE normalizado | ambas | anual |
| `horas_docentes` | Total horas docentes contratadas | fact_docentes | anual |
| `ratio_horas_alumno` | Horas docentes / matrícula | fact_docentes + fact_matricula | anual |
| `matricula` | Total alumnos matriculados | fact_matricula | anual |
| `pct_prioritarios` | % alumnos prioritarios SEP | fact_matricula | anual |

### Módulo analytics.py

```
scraper/
└── pipelines/
    └── analytics.py    ← NUEVO
```

**Responsabilidades:**

1. **Calcular métricas reales** — leer del gold layer, agregar, escribir en analytics_establecimiento
2. **Calcular percentiles** — comparar el RBD piloto contra el universo de la región y su GSE
3. **Proyectar valores esperados** — regresión lineal simple sobre serie histórica propia
4. **Detectar desviaciones** — marcar cuando el valor real se aleja del esperado

**Lógica de proyección (regresión lineal sobre serie histórica):**

```python
# Pseudocódigo — implementación real en analytics.py
def proyectar(serie: list[tuple[int, float]]) -> float:
    """
    Recibe lista de (agno, valor) ordenada cronológicamente.
    Retorna el valor esperado para el próximo período.
    Usa scipy.stats.linregress sobre los últimos N años disponibles.
    Mínimo 3 puntos para proyectar, None si no hay suficiente historia.
    """
```

**Cálculo de percentiles:**

```python
# El RBD piloto se compara contra establecimientos con:
# - mismo cod_reg (región)
# - mismo cod_depe (dependencia: municipal, part-subv, etc.)
# - GSE similar (gse_predominante de fact_simce)
# El percentil indica: "este colegio está mejor que el X% de colegios similares"
```

**Integración con pipelines existentes:**

```python
# Al final de MineducPipeline.run() y SimcePipeline.run():
from scraper.pipelines.analytics import AnalyticsPipeline
AnalyticsPipeline(rbd=settings.rbd_piloto).run()

# Al final de SigePipeline.run():
# Solo métricas internas (prom_interno, asistencia, tasa_aprobacion)
AnalyticsPipeline(rbd=settings.rbd_piloto).run(scope="sige_only")
```

**Nueva variable en config/settings:**

```python
# .env
RBD_PILOTO=00001234    # RBD del colegio que usa el sistema
```

---

## Fase B — API FastAPI

### Diseño general

Una sola API FastAPI. Sin auth (MVP en red local cerrada).
Los routers ETL ya esbozados se mantienen igual.
Se agregan dos routers nuevos: /metrics y /insights.

```
scraper/api/
├── main.py
├── dependencies.py          # get_db() con SQLAlchemy session
└── routers/
    ├── pipelines.py         # ya esbozado — disparar ETL
    ├── jobs.py              # ya esbozado — monitorear jobs
    ├── upload.py            # ya esbozado — recibir PDFs SIGE
    ├── metrics.py           # NUEVO — datos crudos para gráficos
    └── insights.py          # NUEVO — análisis pre-computado
```

### Router /metrics

Sirve datos para que Streamlit grafique. Responde rápido porque
solo lee del gold layer sin cálculo adicional.

```
GET /metrics/simce?agno=2022&grado=4
    → Serie histórica SIMCE del RBD piloto
    → Respuesta: [{agno, grado, ptje_mat, ptje_lect, ptje_cie, n_eval_mat}]

GET /metrics/simce/benchmark?agno=2022&grado=4
    → SIMCE del piloto vs promedio regional y GSE
    → Respuesta: {piloto: {...}, region: {...}, gse: {...}}

GET /metrics/calificaciones?agno=2023&grado=4&letra=A
    → Distribución de notas y promedios por curso
    → Respuesta: [{asignatura, promedio, asistencia_pct, tasa_aprobacion}]

GET /metrics/calificaciones/tendencia?agno=2023
    → Evolución mensual de promedios internos
    → Respuesta: [{mes, promedio, asistencia_pct}]

GET /metrics/matricula?agno=2023
    → Composición de matrícula: prioritarios, GSE, grados
    → Respuesta: {total, pct_prioritarios, por_grado: [...]}

GET /metrics/docentes?agno=2023
    → Horas por asignatura y tipo de cargo
    → Respuesta: [{asignatura, n_horas, n_docentes}]
```

### Router /insights

Sirve el análisis pre-computado por analytics.py.
Incluye valor real, valor esperado, desviación y texto explicativo.

```
GET /insights/resumen?agno=2023
    → Panel general: todas las métricas del año con semáforo
    → Respuesta: [{metrica, valor_real, valor_esperado,
                   desviacion_pct, estado: "ok"|"alerta"|"critico",
                   texto_explicativo}]

GET /insights/simce/tendencia
    → Serie histórica SIMCE con línea de tendencia y proyección
    → Respuesta: {historico: [...], tendencia: [...], proyeccion_proximo: float}

GET /insights/brecha
    → Diferencia entre rendimiento interno y SIMCE
    → Respuesta: {brecha_mat, brecha_lect, interpretacion: str}

GET /insights/proyeccion?metrica=simce_mat&horizonte=2
    → "Si seguís al ritmo actual, en 2 años estaríais aquí"
    → Respuesta: {valor_actual, proyeccion, intervalo_confianza, supuestos}

GET /insights/alertas
    → Métricas que se están desviando negativamente del esperado
    → Respuesta: [{metrica, desviacion_pct, descripcion, urgencia}]

GET /insights/percentiles?agno=2023
    → Posición del colegio en su grupo de comparación
    → Respuesta: [{metrica, percentil_reg, percentil_gse, interpretacion}]
```

### Schemas de respuesta (Pydantic)

```python
# Agregar a scraper/core/schemas.py

class MetricaAnalitica(BaseModel):
    metrica:         str
    valor_real:      float | None
    valor_esperado:  float | None
    desviacion_pct:  float | None
    percentil_reg:   float | None
    percentil_gse:   float | None
    estado:          Literal["ok", "alerta", "critico"] | None
    texto_explicativo: str | None

class ProyeccionMetrica(BaseModel):
    metrica:              str
    valor_actual:         float
    proyeccion:           float
    intervalo_confianza:  tuple[float, float] | None
    horizonte_agno:       int
    n_puntos_historia:    int
    supuestos:            str

class AlertaMetrica(BaseModel):
    metrica:       str
    desviacion_pct: float
    descripcion:   str
    urgencia:      Literal["alta", "media", "baja"]
```

### Dependencias

```python
# scraper/api/dependencies.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from scraper.core.config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_rbd_piloto() -> str:
    return settings.rbd_piloto
```

---

## Fase C — Dashboard Streamlit

### Estructura

```
streamlit/
├── app.py                   # entrada principal, navegación
├── pages/
│   ├── 01_resumen.py        # panel general con semáforo
│   ├── 02_simce.py          # SIMCE histórico + benchmark
│   ├── 03_calificaciones.py # notas internas por curso/asignatura
│   ├── 04_matricula.py      # composición de matrícula
│   ├── 05_proyecciones.py   # "dónde deberías estar"
│   └── 06_explicaciones.py  # módulos explain para el director
└── components/
    ├── charts.py            # funciones de gráficos reutilizables
    ├── semaforo.py          # componente de estado visual
    └── explain.py           # textos explicativos contextualizados
```

### Módulos Explain

Cada módulo explain combina:
- Un gráfico del dato real del colegio
- El contexto regional/GSE para comparar
- Texto explicativo en lenguaje no técnico
- Una interpretación accionable ("qué hacer con esto")

```python
# Ejemplo de estructura de un módulo explain

def explain_simce(datos: dict) -> None:
    st.header("¿Qué nos dice el SIMCE?")

    st.markdown("""
    El SIMCE mide cuánto aprenden los estudiantes en matemática,
    lectura y ciencias respecto al currículo nacional.
    Un puntaje sobre 270 se considera satisfactorio en 4° básico.
    """)

    # Gráfico histórico del colegio
    chart_simce_historico(datos["historico"])

    # Comparación con pares
    st.markdown(f"""
    **Tu colegio en contexto:**
    Estás en el percentil {datos['percentil_gse']} de colegios
    con características similares en tu región.
    """)

    # Interpretación accionable
    if datos["desviacion_pct"] < -10:
        st.warning("Los resultados bajaron más de lo esperado este año.")
    elif datos["desviacion_pct"] > 5:
        st.success("Los resultados mejoraron respecto a la tendencia.")
```

### Dependencias adicionales

```
# requirements nuevos para Sprint 3
streamlit>=1.32
plotly>=5.20
scipy>=1.12          # regresiones en analytics.py
scikit-learn>=1.4    # clustering para grupos de comparación
sqlalchemy>=2.0      # ORM para la API
```

---

## Orden de implementación recomendado

```
1. Correr todos los pipelines ETL y verificar gold layer
2. Revisar columnas reales en BD — ajustar catálogo de métricas si es necesario
3. Crear gold.analytics_establecimiento (SQL)
4. Implementar analytics.py — primero métricas reales, después proyecciones
5. Verificar que analytics.py produce resultados coherentes
6. Implementar dependencies.py + schemas nuevos
7. Implementar router /metrics — el más simple, solo lectura
8. Verificar /metrics con datos reales
9. Implementar router /insights — lee de analytics_establecimiento
10. Implementar Streamlit — página por página
11. Integrar analytics.py al final de cada pipeline ETL
```

---

## Estado

| Componente | Estado |
|-----------|--------|
| gold.analytics_establecimiento | ⏳ Pendiente |
| analytics.py pipeline | ⏳ Pendiente |
| API /pipelines + /jobs + /upload | ⏳ Pendiente |
| API /metrics | ⏳ Pendiente |
| API /insights | ⏳ Pendiente |
| Streamlit dashboard | ⏳ Pendiente |
| Módulos explain | ⏳ Pendiente |