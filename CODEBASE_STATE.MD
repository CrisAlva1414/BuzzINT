# BuzzINT — Estado del Codebase

> Actualizado: 2025-06 — Post refactoring Sprint 2, pre Sprint 3

---

## Resumen de estado actual

| Capa | Componente | Estado | Notas |
|------|-----------|--------|-------|
| SQL | 01_schema.sql | ✅ Congelado | Galaxy schema completo |
| SQL | 02_seed.sql | ✅ Congelado | dim_tiempo_escolar + dim_asignatura base |
| SQL | 03_patches.sql | ✅ Congelado | Constraints traslados, env_profile, vistas |
| SQL | analytics_establecimiento | ⏳ Pendiente | Ver SPRINTS.md Fase A |
| Core | config.py | ✅ Completo | Falta agregar RBD_PILOTO |
| Core | logging.py | ✅ Congelado | |
| Core | schemas.py | ✅ Completo | Faltan schemas analíticos Sprint 3 |
| Core | crypto.py | ✅ Congelado | |
| Core | hashing.py | ✅ Congelado | |
| Extractors | downloader_base.py | ✅ Congelado | |
| Extractors | mineduc_downloader.py | ✅ Completo | |
| Extractors | simce_downloader.py | ✅ Completo | |
| Extractors | sige_downloader.py | ✅ Completo | |
| Normalizers | normalizer_base.py | ✅ Congelado | |
| Normalizers | mineduc_alumnos_normalizer.py | ✅ Completo | |
| Normalizers | mineduc_cargos_normalizer.py | ✅ Completo | |
| Normalizers | mineduc_establecimientos_normalizer.py | ✅ Completo | |
| Normalizers | sige_normalizer.py | ✅ Completo | |
| Normalizers | simce_normalizer.py | ✅ Completo | |
| Loaders | db.py | ✅ Congelado | Coerciones + helpers dimensional |
| Loaders | load_alumnos.py | ✅ Completo | COPY masivo ~40M filas |
| Loaders | load_dev.py | ✅ Completo | establecimientos + cargos + simce |
| Loaders | load_sige.py | ✅ Completo | calificaciones + profesores |
| Pipelines | base.py | ✅ Congelado | BasePipeline ABC + FileRegistry |
| Pipelines | mineduc.py | ✅ Completo | |
| Pipelines | simce.py | ✅ Completo | |
| Pipelines | sige.py | ✅ Completo | |
| Pipelines | analytics.py | ⏳ Pendiente | Ver SPRINTS.md Fase A |
| Tests | core_test.py | ✅ Completo | ~30 tests |
| Tests | downloaders_test.py | ✅ Completo | ~25 tests |
| Tests | normalizers_test.py | ✅ Completo | ~35 tests |
| Tests | db_test.py | ✅ Completo | ~22 tests |
| Tests | analytics_test.py | ⏳ Pendiente | |
| API | main.py | ⏳ Pendiente | |
| API | dependencies.py | ⏳ Pendiente | |
| API | routers/pipelines.py | ⏳ Pendiente | |
| API | routers/jobs.py | ⏳ Pendiente | |
| API | routers/upload.py | ⏳ Pendiente | |
| API | routers/metrics.py | ⏳ Pendiente | |
| API | routers/insights.py | ⏳ Pendiente | |
| Dashboard | streamlit/app.py | ⏳ Pendiente | |
| Dashboard | pages/ (6 páginas) | ⏳ Pendiente | |
| Dashboard | components/ | ⏳ Pendiente | |

---

## Lo que está congelado y por qué

Congelado significa: no tocar salvo bug crítico. Ya pasaron sus tests.

- **normalizer_base.py** — la lógica de sniff/prescan/stream es estable y bien testeada
- **downloader_base.py** — sha256, manifest helpers: primitivos que no cambian
- **db.py** — coerciones (_str, _int, _rbd, etc.) y helpers dimensionales
- **01_schema.sql / 02_seed.sql** — gold layer validado
- **base.py** (pipelines) — ABC + FileRegistry

---

## Deuda técnica registrada

### Deuda conocida pre-Sprint 3

1. **config.py no tiene RBD_PILOTO**
   - Agregar `rbd_piloto: str` a Settings antes de implementar analytics.py
   - Agregar `RBD_PILOTO=00001234` a .env.example

2. **schemas.py no tiene schemas analíticos**
   - Agregar MetricaAnalitica, ProyeccionMetrica, AlertaMetrica
   - Ver SPRINTS.md para definición completa

3. **MineducPipeline y SimcePipeline no llaman a analytics**
   - Al final de _load() en ambos pipelines, agregar llamada a AnalyticsPipeline
   - Hacerlo solo cuando analytics.py esté testeado

4. **Dockerfile no tiene scipy ni scikit-learn**
   - Agregar a requirements.txt antes de build
   - scipy, scikit-learn, streamlit, plotly

### Deuda conocida post-MVP (no bloquea Sprint 3)

- Auth OAuth2 + JWT para la API (ADR-005)
- Particionado por agno en fact_matricula y fact_calificaciones (ADR-001)
- SCD Type-2 en dim_establecimiento si se necesita historial de dependencias

---

## Prerequisitos para arrancar Sprint 3

```bash
# 1. Verificar que PostgreSQL tiene el schema completo
psql -d buzzint -c "\dt gold.*"

# 2. Correr pipelines en orden
python -m scraper.pipelines.mineduc     # establecimientos primero
python -m scraper.pipelines.simce       # sin dependencias internas
python -m scraper.pipelines.mineduc     # cargos + alumnos
# sige: manual, requiere PDFs del colegio piloto

# 3. Verificar gold layer poblado
psql -d buzzint -c "SELECT COUNT(*) FROM gold.dim_establecimiento;"
psql -d buzzint -c "SELECT COUNT(*) FROM gold.fact_simce;"
psql -d buzzint -c "SELECT COUNT(*) FROM gold.fact_calificaciones;"

# 4. Revisar columnas reales — ajustar catálogo de métricas si difiere
psql -d buzzint -c "\d gold.fact_simce"
psql -d buzzint -c "\d gold.fact_calificaciones"

# 5. Recién entonces: implementar analytics.py
```

---

## Notas del refactoring Sprint 2

- Se eliminó el hack de cod_grado=0 en dim_tiempo_escolar (patch 03)
- fact_matricula ahora soporta traslados con constraint uq_fact_matricula
- fact_docentes.tiempo_id es ahora nullable (cargos sin grado específico)
- env_profile en BD permite que los loaders detecten dev vs prod automáticamente
- v_notas_long y v_sige_resumen agregadas en 03_patches.sql