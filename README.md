# MINEDUC Intelligence Platform

> Pipeline de extracción, normalización y análisis de datos educativos públicos chilenos.  
> Foco: diagnóstico causal del desempeño SIMCE por establecimiento, efecto docente y benchmarking territorial.

---

## ¿Qué es esto?

Un sistema que recolecta datos del ecosistema MINEDUC desde múltiples fuentes — algunas abiertas, otras con autenticación institucional — los normaliza bajo un modelo canónico y los expone para análisis y visualización.

El foco es **inteligencia por establecimiento**: cruzar resultados SIMCE, notas históricas por asignatura y profesor, y variables de contexto institucional bajo un único identificador (el RBD), para responder preguntas que hoy ningún director puede responder mirando solo el PDF de resultados.

---

## Preguntas que el sistema responde

En orden de prioridad para el usuario final (Director / Jefe UTP):

1. **Diagnóstico causal** — ¿Qué factores explican nuestro puntaje SIMCE? ¿Cuáles son internos al establecimiento?
2. **Efecto docente** — ¿Qué profesores generan más valor agregado en sus cursos, controlando por composición del grupo?
3. **Benchmarking territorial** — ¿Cómo nos comparamos con establecimientos similares en la comuna, el SLEP y la región?
4. **Proyección de tendencia** — Si mantenemos los patrones actuales, ¿hacia dónde va nuestro puntaje?

---

## Modelo analítico

### Unidad de análisis

**Establecimiento × Año** (`rbd`, `anio`). Es el nivel en que el SIMCE se emite y en que las variables de contexto están disponibles. El efecto docente se calcula internamente como subcomponente, agregando desde la granularidad nota × alumno × asignatura × semestre que provee el SIGE.

### Variable dependiente (Y)

Puntaje SIMCE promedio por establecimiento y asignatura (Lectura, Matemática) para 4° y 6° básico. Se trabaja con la serie histórica completa, con cobertura desde ~2008–2010 según disponibilidad por fuente.

### Modelo base: Panel con Efectos Fijos

```
SIMCE(rbd, t) = α(rbd) + β · X_endo(rbd, t-lag) + γ · X_exo(rbd, t) + ε
```

Donde:
- `α(rbd)` es el efecto fijo por establecimiento (absorbe todo lo no observable estable: cultura escolar, liderazgo histórico, infraestructura).
- `X_endo` son las variables endógenas del SIGE con rezago temporal.
- `X_exo` son las cuatro variables exógenas del modelo, descritas abajo.
- `ε` son errores con corrección de heterocedasticidad (robustos).

**Por qué Panel FE y no OLS:** con serie histórica desde ~2008 el efecto fijo por RBD absorbe todo lo estable no observable. Sin eso, variables como cultura de gestión o calidad directiva histórica contaminan los coeficientes endógenos.

### Efecto acumulado rezagado

El SIMCE de 4° básico no mide lo que ocurrió ese año: mide el resultado de 4–6 años de escolaridad. Las variables endógenas se incluyen con rezagos `t-1` a `t-4`, con pesos decrecientes. La ventana histórica del SIGE desde 2009 permite construir estos rezagos con cobertura completa.

### Efecto docente (Teacher Value-Added)

Con la granularidad nota × alumno × asignatura × semestre × docente que entrega el SIGE, se estima el efecto docente aislado de la composición del curso:

```
Nota(alumno, asignatura, semestre) = μ(docente) + φ · Composición_curso + η
```

`μ(docente)` es el valor agregado del profesor: cuánto mejora o empeora el rendimiento de sus alumnos respecto a lo esperado dado el grupo que tiene. Este indicador alimenta el modelo principal como variable endógena y se cruza con el resultado de Evaluación Docente como validación externa.

### Benchmarking territorial por anillos

El benchmarking usa los **residuos del modelo** — la diferencia entre el SIMCE observado y el predicho — no los puntajes brutos (que reflejan principalmente NSE). El residuo mide eficiencia pedagógica neta y es comparable entre establecimientos de distinto contexto.

Los residuos se comparan en tres anillos concéntricos:
- **Anillo 1 — Vecinos cercanos:** establecimientos de la misma comuna con dependencia similar.
- **Anillo 2 — Vecinos intermedios:** establecimientos del mismo SLEP.
- **Anillo 3 — Vecinos lejanos:** establecimientos de la misma región con IVE similar.

---

## Variables del modelo

### Variables exógenas (4 — criterio de inclusión: confounding o causalidad directa no capturada por SIGE)

| Variable | Dataset MINEDUC | Justificación | Cobertura histórica |
|---|---|---|---|
| % alumnos prioritarios SEP | Alumnos SEP/prioritarios | Control NSE. Sin esto todos los coeficientes endógenos están sesgados. No negociable. | ~2008 |
| Dependencia, ruralidad, tamaño matrícula | Directorio de establecimientos | Necesario para construir los anillos de benchmarking y controlar efectos de escala. | ~2008 |
| Resultado Evaluación Docente (promedio RBD) | Evaluación Docente | El SIGE entrega notas del alumno, no la evaluación externa del docente. Señales distintas que no se solapan. | ~2008 |
| Tasa de retención docente (Δ cargos año a año) | Cargos Docentes | La rotación a nivel establecimiento es una señal de inestabilidad institucional que el SIGE no captura. | ~2010 |

### Variables endógenas (fuente: SIGE — la señal principal del modelo)

| Variable | Granularidad | Rezago |
|---|---|---|
| Nota promedio por asignatura × curso | Semestral × alumno × docente | t-1 a t-4 |
| Tasa de aprobación por nivel | Anual × curso | t-1, t-2 |
| Continuidad docente en el tramo | Calculada: mismo RUT docente ≥ 2 años en asignatura | t-1 a t-4 |
| Valor agregado docente (μ) | Calculado del submodelo de efecto docente | t-1 |

### Variables excluidas del modelo v1

Todo lo que no está arriba queda fuera. Su señal está capturada por las variables activas, su mecanismo causal es débil, o son redundantes dado el efecto fijo por establecimiento. Candidatos para clustering no supervisado en fases futuras: AVDI, AEP, SNED, PME, Subvenciones, Asistencia anual, Rendimiento por estudiante.

---

## Fuentes de datos

### Agencia de Calidad de la Educación — SIMCE

- **URL:** `https://informacionestadistica.agenciaeducacion.cl`
- **Acceso:** público
- **Mecanismo:** API REST JSON (`/rest/archivo/getAllByCategoriaVistaPublica/{cat_id}`). Se itera sobre rangos de categorías (cat 2–60) y se descargan archivos `.rar` con CSVs internos.
- **Contenido:** puntajes SIMCE por establecimiento, distribución de estándares, series históricas.
- **Rol en el modelo:** variable dependiente Y.
- **Cobertura:** ~2008 en adelante.
- **Script:** `scraper/extractors/simce_downloader.py`

### SIGE — Sistema de Información General de Estudiantes

- **URL:** `https://sige.mineduc.cl`
- **Acceso:** autenticado (credenciales institucionales por establecimiento)
- **Mecanismo:** login con Playwright (captcha manual una vez), cookies traspasadas a `httpx` para descarga masiva. PDFs de actas vía POST a `/Sige/Reportes/ImprimirActasHisto`.
- **Contenido:** notas parciales × asignatura × semestre × alumno con RUT docente asociado, desde 2009.
- **Rol en el modelo:** fuente principal de variables endógenas y única fuente para estimar efecto docente.
- **Cobertura:** 2009 en adelante.
- **Script:** `scraper/extractors/sige_downloader.py`

### Datos Abiertos MINEDUC — datasets activos

- **URL:** `https://datosabiertos.mineduc.cl`
- **Acceso:** público
- **Mecanismo:** scraper unificado que descarga únicamente los cuatro datasets necesarios para el modelo v1.
- **Datasets:**
  1. Alumnos preferentes, prioritarios y beneficiarios SEP (~2008)
  2. Directorio de establecimientos educacionales (~2008)
  3. Evaluación Docente (~2008)
  4. Cargos Docentes (~2010)
- **Script:** `scraper/extractors/datos_abiertos.py` (pendiente)

### Trayectorias Estudiantiles *(pendiente de evaluación)*

- **URL:** `https://trayectorias.mineduc.gob.cl`
- **Acceso:** autenticado (ClaveÚnica)
- **Rol potencial:** validar el rezago temporal del modelo.
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
      │      (tasa aprobación, continuidad docente, μ docente por submodelo)
      ▼
   GOLD  ── rezagos temporales construidos (t-1 a t-4),
            listo para el modelo Panel FE y benchmarking territorial
```

El identificador que une todo es el **RBD**. Cada fuente lo llama distinto (`RBD`, `cod_rbd`, columna scrapeada); el normalizer siempre lo resuelve a `establecimientos.rbd` antes de escribir en Silver.

---

## Deduplicación

Cada archivo descargado se verifica con SHA-256 contra el manifest local. Si el hash coincide con la descarga anterior, el archivo se saltea.

---

## Scheduling

Los datos públicos (SIMCE, Datos Abiertos) se actualizan vía cron automático.  
Los datos autenticados (SIGE, Trayectorias) se disparan manualmente desde el panel director o vía API.

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
- [ ] `datos_abiertos.py` — scraper unificado (SEP + Directorio + Evaluación Docente + Cargos Docentes)
- [ ] Normalizers Silver
  - [ ] Parser notas SIGE → granularidad alumno × asignatura × semestre × docente
  - [ ] Construcción de rezagos temporales (t-1 a t-4) en capa Gold
  - [ ] Cálculo de variables derivadas: continuidad docente, tasa retención, valor agregado μ
- [ ] Capa Gold + resolución canónica de RBD
- [ ] Submodelo efecto docente (Teacher Value-Added)
- [ ] Modelo Panel FE con variables endógenas rezagadas + 4 exógenas
- [ ] Benchmarking territorial por anillos (residuos del modelo)
- [ ] APScheduler + cron datos macro
- [ ] FastAPI panel director
- [ ] Dashboard / visualización
- [ ] Pipeline RAG
- [ ] Clustering no supervisado (variables excluidas del modelo v1)

---

## Licencia

MIT. Los datos expuestos son de carácter público o están sujetos a los términos de uso de cada plataforma MINEDUC. El uso de credenciales institucionales es responsabilidad del operador del sistema.