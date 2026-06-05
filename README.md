# MINEDUC Intelligence Platform

> Pipeline de extracción, normalización y análisis de datos educativos públicos chilenos.  
> Foco: diagnóstico causal del desempeño SIMCE por establecimiento, efecto docente y benchmarking territorial.

---

## ¿Qué es esto?

Un sistema que recolecta datos del ecosistema MINEDUC desde múltiples fuentes — algunas abiertas, otras con autenticación institucional — los normaliza bajo un modelo canónico y los expone para análisis y visualización.

El foco es **inteligencia accionable por establecimiento**: cruzar resultados SIMCE, datos de matrícula, dotación docente, notas históricas por asignatura y profesor, e indicadores contextuales bajo un único identificador (el RBD), para responder preguntas que hoy ningún director puede responder solo mirando el PDF de resultados.

---

## Preguntas que el sistema responde

En orden de prioridad para el usuario final (Director / Jefe UTP):

1. **Diagnóstico causal** — ¿Qué factores explicaron nuestro último puntaje SIMCE? ¿Cuáles son accionables?
2. **Efecto docente** — ¿Qué profesores generan más valor agregado en sus cursos, controlando por composición del grupo?
3. **Benchmarking territorial** — ¿Cómo nos comparamos con establecimientos similares en la comuna, el SLEP y la región?
4. **Proyección de tendencia** — Si mantenemos los patrones actuales, ¿hacia dónde va nuestro puntaje?

---

## Modelo analítico

### Unidad de análisis

**Establecimiento × Año** (`rbd`, `anio`). Es el nivel en que el SIMCE se emite y en que la mayoría de las variables exógenas están disponibles. El efecto docente se calcula internamente como subcomponente, agregando desde la granularidad nota × alumno × asignatura × semestre que provee el SIGE.

### Variable dependiente (Y)

Puntaje SIMCE promedio por establecimiento y asignatura (Lectura, Matemática) para 4° y 6° básico, en cada año de medición disponible. Se trabaja con la serie histórica completa (≥ 5 años por establecimiento).

### Modelo base: Panel con Efectos Fijos + Control NSE

```
SIMCE(rbd, t) = α(rbd) + β · X_endo(rbd, t-lag) + γ · X_exo(rbd, t) + δ · NSE(rbd, t) + ε
```

Donde:
- `α(rbd)` es el efecto fijo por establecimiento (absorbe todo lo no observable estable: cultura escolar, liderazgo histórico, infraestructura).
- `X_endo` son las variables endógenas del establecimiento con rezago temporal.
- `X_exo` son las variables exógenas públicas del MINEDUC.
- `NSE` es el control socioeconómico (% alumnos prioritarios SEP), indispensable para no sesgar todos los demás coeficientes.
- `ε` son errores con corrección de heterocedasticidad (robustos).

### Efecto acumulado rezagado (distributed lag)

El SIMCE de 4° básico no mide lo que ocurrió en ese año: mide el resultado de 4-6 años de escolaridad. Por lo tanto, las variables endógenas (notas, asistencia, continuidad docente) se incluyen con rezagos `t-1` a `t-4`, con pesos decrecientes en el tiempo. Esto es especialmente relevante para las notas históricas del SIGE: una nota de 1° básico que rinde prueba en 4° básico tiene peso, aunque menor que la nota de 3° básico.

### Efecto docente (Teacher Value-Added)

Con la granularidad nota × alumno × asignatura × semestre × docente que entrega el SIGE, se puede estimar el efecto docente aislado del efecto composición del curso:

```
Nota(alumno, asignatura, semestre) = μ(docente) + φ · Composición_curso + η
```

`μ(docente)` es el valor agregado del profesor: cuánto mejora o empeora el rendimiento de sus alumnos respecto a lo esperado dado el grupo que tiene. Este indicador se cruza luego con los puntajes SIMCE para validar su poder predictivo.

### Benchmarking territorial por anillos

El benchmarking no usa puntajes brutos (que reflejan principalmente NSE). Usa los **residuos del modelo**: la diferencia entre el SIMCE observado y el SIMCE predicho para ese establecimiento dado su composición. Ese residuo mide eficiencia pedagógica neta.

Los residuos se comparan en tres anillos:
- **Anillo 1 — Vecinos cercanos:** establecimientos de la misma comuna con dependencia similar.
- **Anillo 2 — Vecinos intermedios:** establecimientos del mismo SLEP.
- **Anillo 3 — Vecinos lejanos:** establecimientos de la misma región con IVE similar.

El resultado para el director: "Tu puntaje bruto es el esperado para tu NSE, pero estás en el percentil 28 de eficiencia pedagógica dentro del SLEP. Estos 3 establecimientos similares tienen residuos positivos consistentes — ¿qué están haciendo diferente?"

---

## Variables del modelo

### Variables de control obligatorias (no accionables, pero sin ellas el modelo está sesgado)

| Variable | Fuente | Notas |
|---|---|---|
| % alumnos prioritarios SEP | Datos Abiertos MINEDUC | Proxy NSE más robusto disponible. Incluir siempre. |
| Dependencia (municipal / part. subv. / part. pagado) | Directorio establecimientos | Dummy categórico. |
| Ruralidad | Directorio establecimientos | Dummy. |
| Tamaño matrícula | Resumen matrícula por establecimiento | Controla efectos de escala. |

### Variables endógenas accionables (fuente: SIGE)

| Variable | Granularidad | Rezago |
|---|---|---|
| Nota promedio por asignatura × curso | Semestral × alumno × docente | t-1 a t-4 |
| Tasa de aprobación por nivel | Anual × curso | t-1, t-2 |
| Asistencia promedio del tramo | Anual × curso | t-1 a t-3 |
| Continuidad docente en el tramo | Calculada: mismo RUT docente ≥ 2 años en asignatura | t-1 a t-4 |
| Valor agregado docente (μ) | Calculado del modelo de efecto docente | t-1 |

### Variables exógenas accionables (fuente: Datos Abiertos MINEDUC)

| Variable | Dataset MINEDUC | Prioridad de scraping |
|---|---|---|
| % alumnos prioritarios SEP + monto subvención | Alumnos SEP/prioritarios + Subvenciones | **Alta — sin esto el modelo está sesgado** |
| Resultado evaluación docente (promedio RBD) | Evaluación Docente | Alta — refuerza el análisis de capital humano |
| Resultado AVDI / AEP docentes del establecimiento | AVDI + AEP | Media — complementa evaluación docente |
| Tasa retención docente (Δ cargos año a año) | Cargos Docentes | Alta — proxy de estabilidad del equipo |
| Tasa asistencia anual consolidada | Asistencia anual por estudiante | Alta — uno de los predictores más robustos |
| Tasa rendimiento (aprobación/reprobación) | Rendimiento por estudiante | Alta |
| Puntaje SNED | SNED | Media — útil como validación cruzada del modelo |
| PME activo en el tramo | PME | Baja — difícil de operacionalizar; usar como dummy binario |

### Variables excluidas del modelo (y por qué)

| Dataset | Razón de exclusión |
|---|---|
| Matrícula educación parvularia | Upstream excesivo para el tramo 4°/6° básico |
| Notas y egresados EM | Downstream del scope |
| SAE (admisión escolar) | Afecta composición futura, no el puntaje histórico |
| ENDDEIE (Encuesta digital) | Muy reciente, pocas observaciones, colineal con NSE |
| Financiamiento compartido (FICOM) | Alta colinealidad con % SEP. Incluir solo en segmentación por dependencia |
| Jornada Escolar Completa (JEC) | Hoy casi universalizada, varianza insuficiente |
| Matrícula longitudinal | Agrega lo que ya se tiene con más ruido |
| Asistencia mensual declarada | Reemplazada por asistencia anual; agregar ambas solo suma ruido |
| Vacunación por curso | Sin mecanismo causal plausible en este modelo |
| Validación de estudios | Fuera del scope de educación básica |
| Titulados educación superior | Completamente fuera del scope |

---

## Fuentes de datos

### Agencia de Calidad de la Educación — SIMCE

- **URL:** `https://informacionestadistica.agenciaeducacion.cl`
- **Acceso:** público (sin autenticación)
- **Mecanismo:** API REST JSON (`/rest/archivo/getAllByCategoriaVistaPublica/{cat_id}`) que expone UUIDs descargables. Iteramos sobre rangos de categorías (actualmente cat 2–60) y descargamos los archivos asociados (principalmente `.rar` con CSVs internos).
- **Contenido:** resultados SIMCE por establecimiento — puntajes, distribución de estándares de aprendizaje, ejes de habilidad, series históricas.
- **Rol en el modelo:** variable dependiente Y; serie histórica ≥ 5 años por establecimiento.
- **Script:** `scraper/extractors/simce_downloader.py`

### SIGE — Sistema de Información General de Estudiantes (MINEDUC)

- **URL:** `https://sige.mineduc.cl`
- **Acceso:** autenticado (credenciales institucionales propias de cada establecimiento)
- **Mecanismo:** login en browser visible (el operador resuelve el captcha manualmente una vez), luego las cookies de sesión se traspasan a `httpx` para la descarga masiva sin browser. Los PDFs de actas históricas se obtienen via POST a `/Sige/Reportes/ImprimirActasHisto`.
- **Contenido:** actas históricas por establecimiento desde 2009: notas parciales × asignatura × semestre × alumno, con RUT docente asociado.
- **Rol en el modelo:** fuente principal de variables endógenas; única fuente que permite estimar efecto docente.
- **Script:** `scraper/extractors/sige_downloader.py`

### Datos Abiertos MINEDUC

- **URL:** `https://datosabiertos.mineduc.cl`
- **Acceso:** público
- **Mecanismo:** descarga directa de CSV por dataset. Un único scraper unificado itera sobre las URLs de descarga de los datasets priorizados.
- **Datasets a extraer (en orden de prioridad):**
  1. Alumnos preferentes, prioritarios y beneficiarios SEP
  2. Subvenciones a establecimientos educacionales
  3. Evaluación Docente
  4. Cargos Docentes
  5. AVDI (Asignación Variable al Desempeño Individual)
  6. AEP (Asignación a la Excelencia Pedagógica)
  7. Rendimiento por estudiante
  8. Asistencia anual por estudiante
  9. Directorio de establecimientos educacionales
  10. SNED (Sistema Nacional de Evaluación del Desempeño)
  11. PME (Planes de Mejoramiento Educativo) — baja prioridad
- **Script:** `scraper/extractors/datos_abiertos.py` (pendiente)

### Trayectorias Estudiantiles

- **URL:** `https://trayectorias.mineduc.gob.cl`
- **Acceso:** autenticado (ClaveÚnica)
- **Contenido:** indicadores de trayectoria estudiantil.
- **Rol en el modelo:** por definir — potencialmente útil para validar el rezago temporal.
- **Script:** `scraper/extractors/trayectorias.py` (pendiente)

---

## Pipeline de datos

```
Fuentes externas
      │
      ▼
  BRONZE ── datos tal como llegan, inmutables, con hash de contenido
      │      (CSV, RAR, PDF — sin tocar)
      ▼
  SILVER ── parseados, tipados, RBD canónico resuelto, variables calculadas
      │      (tasa aprobación, asistencia promedio, continuidad docente)
      ▼
   GOLD  ── entidades resueltas con rezagos temporales construidos,
            listo para el modelo de regresión, BI y RAG
```

El identificador que une todo es el **RBD** (Rol Base de Datos). Cada fuente lo llama distinto (`RBD`, `cod_rbd`, columna scrapeada); el normalizer siempre lo resuelve a `establecimientos.rbd` antes de escribir en Silver.

---

## Deduplicación

Cada archivo descargado se verifica con SHA-256 contra el manifest local. Si el hash coincide con la descarga anterior, el archivo se saltea. Esto permite correr el scraper repetidamente sin re-descargar contenido que no cambió.

---

## Scheduling

Los datos públicos (SIMCE, Datos Abiertos) se actualizan via cron automático.  
Los datos autenticados (SIGE, Trayectorias) se disparan manualmente desde el panel director o via API.

---

## Panel director

Interfaz mínima para:
- Registrar y gestionar credenciales institucionales (almacenadas cifradas)
- Disparar descargas manualmente por fuente o establecimiento
- Ver estado de los últimos jobs
- Visualizar diagnóstico causal, efecto docente y benchmarking territorial

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
| Modelo estadístico | `statsmodels` (Panel FE) + `scikit-learn` (regularización) |

---

## Roadmap

- [x] Scaffold Docker + PostgreSQL + modelos SQLAlchemy
- [x] `simce_downloader` — descarga pública Agencia de Calidad
- [x] `sige_downloader` — descarga autenticada actas históricas
- [ ] Integrar downloaders al pipeline Bronze (heredar `BaseExtractor`)
- [ ] `datos_abiertos.py` — scraper unificado para los 11 datasets priorizados
- [ ] Normalizers CSV + PDF codebook (Silver)
  - [ ] Parser notas SIGE → granularidad alumno × asignatura × semestre × docente
  - [ ] Construcción de rezagos temporales (t-1 a t-4) en capa Gold
  - [ ] Cálculo de variables derivadas: continuidad docente, tasa retención, valor agregado μ
- [ ] Capa Gold + resolución canónica de RBD
- [ ] Modelo Panel FE con control NSE + variables endógenas rezagadas
- [ ] Modelo efecto docente (Teacher Value-Added)
- [ ] Benchmarking territorial por anillos (comuna → SLEP → región) sobre residuos del modelo
- [ ] APScheduler + cron datos macro
- [ ] FastAPI panel director
- [ ] Dashboard / visualización
- [ ] Pipeline RAG

---

## Licencia

MIT. Los datos expuestos son de carácter público o están sujetos a los términos de uso de cada plataforma MINEDUC. El uso de credenciales institucionales es responsabilidad del operador del sistema.