-- ============================================================
-- BuzzINT — 04_analytics_schema.sql
-- Analytics Layer: métricas pre-computadas por establecimiento
-- Ejecutar DESPUÉS de 01_schema.sql, 02_seed.sql, 03_patches.sql
-- Idempotente: usa IF NOT EXISTS / CREATE OR REPLACE
-- ============================================================
SET search_path TO gold, public;


-- ──────────────────────────────────────────────────────────────
-- ANALYTICS_CONFIG
-- Parámetros del pipeline de analytics: umbrales, segmentos, flags.
-- Una fila por clave. El pipeline lee esto al arrancar.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.analytics_config (
    key         VARCHAR(80)  PRIMARY KEY,
    value       TEXT         NOT NULL,
    description TEXT,
    updated_at  TIMESTAMP    NOT NULL DEFAULT now()
);
COMMENT ON TABLE gold.analytics_config IS
    'Parámetros del AnalyticsPipeline. Leer al inicio de cada run.';

INSERT INTO gold.analytics_config (key, value, description) VALUES
    ('hiatus_start',      '2019',   'Primer año del hiatus (estallido + pandemia)'),
    ('hiatus_end',        '2021',   'Último año del hiatus'),
    ('post_hiatus_start', '2022',   'Primer año post-hiatus con datos SIMCE'),
    ('alert_threshold',   '1.5',    'Multiplicador de RMSE para disparar alerta'),
    ('min_points_trend',  '3',      'Mínimo de puntos para calcular tendencia'),
    ('min_points_corr',   '4',      'Mínimo de puntos para calcular correlación interno-SIMCE'),
    ('rbd_piloto',        '',       'RBD del colegio piloto — se sobreescribe desde .env')
ON CONFLICT (key) DO NOTHING;


-- ──────────────────────────────────────────────────────────────
-- ANALYTICS_ESTABLECIMIENTO
-- Granularidad: (rbd, agno, metrica, grado, segmento)
--
-- Una fila = una métrica calculada para un establecimiento,
-- en un año, para un grado (puede ser NULL si es agregado),
-- en un segmento temporal (pre_hiatus | post_hiatus | full).
--
-- El pipeline escribe aquí; la API solo lee.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.analytics_establecimiento (
    analytics_id    BIGSERIAL    PRIMARY KEY,

    -- Contexto
    rbd             CHAR(8)      NOT NULL,
    agno            SMALLINT,        -- NULL para métricas agregadas multi-año
    grado           VARCHAR(10),     -- '4b','8b','2m','2b','6b' | NULL si no aplica
    segmento        VARCHAR(20)  NOT NULL DEFAULT 'post_hiatus',
                                     -- 'pre_hiatus' | 'post_hiatus' | 'full' | 'current'

    -- Identificación de la métrica
    metrica         VARCHAR(80)  NOT NULL,
                                     -- ver catálogo abajo
    asignatura      VARCHAR(40),     -- 'mat','lect','cie','his','ing' | NULL si no aplica
    fuente          VARCHAR(20)  NOT NULL,
                                     -- 'simce' | 'sige' | 'cruce'

    -- Valores calculados
    valor_real      NUMERIC(10,4),   -- valor observado (último punto o promedio)
    valor_proyectado NUMERIC(10,4),  -- valor esperado según tendencia
    tendencia_slope  NUMERIC(10,6),  -- pendiente de la regresión (puntos/año)
    tendencia_r2     NUMERIC(6,4),   -- R² de la regresión (calidad del ajuste)
    rmse             NUMERIC(10,4),  -- RMSE histórico de la regresión
    percentil_gse    NUMERIC(5,2),   -- percentil vs establecimientos mismo GSE+depe
    percentil_comuna NUMERIC(5,2),   -- percentil vs establecimientos misma comuna
    alerta           BOOLEAN      NOT NULL DEFAULT FALSE,
                                     -- TRUE si |real - proyectado| > threshold * RMSE
    n_puntos         SMALLINT,       -- cuántos puntos usó el modelo
    confianza        VARCHAR(20),    -- 'alta'(n>=6) | 'media'(n=4-5) | 'baja'(n=3) | 'insuficiente'

    -- Metadatos del run
    calculado_en    TIMESTAMP    NOT NULL DEFAULT now(),
    pipeline_version VARCHAR(20) NOT NULL DEFAULT '1.0',

    -- Unicidad: una métrica por contexto completo
    UNIQUE (rbd, agno, grado, segmento, metrica, asignatura)
);

COMMENT ON TABLE gold.analytics_establecimiento IS
    'Métricas analytics pre-computadas. Escribe AnalyticsPipeline, lee la API.';
COMMENT ON COLUMN gold.analytics_establecimiento.metrica IS
    'Catálogo: simce_puntaje | simce_tendencia | simce_percentil_gse |
     simce_percentil_comuna | simce_brecha_mat_lect |
     sige_promedio | sige_tendencia | sige_proyeccion |
     sige_tasa_aprobacion | sige_tasa_asistencia |
     cruce_brecha_interno_simce | cruce_correlacion_interno_simce';
COMMENT ON COLUMN gold.analytics_establecimiento.confianza IS
    'Calidad del modelo: alta(n>=6), media(n=4-5), baja(n=3), insuficiente(n<3).
     Mostrar al director cuando sea baja o insuficiente.';

-- Índices para los queries más frecuentes de la API
CREATE INDEX IF NOT EXISTS idx_analytics_rbd_metrica
    ON gold.analytics_establecimiento (rbd, metrica, segmento);

CREATE INDEX IF NOT EXISTS idx_analytics_rbd_agno
    ON gold.analytics_establecimiento (rbd, agno);

CREATE INDEX IF NOT EXISTS idx_analytics_alerta
    ON gold.analytics_establecimiento (rbd, alerta)
    WHERE alerta = TRUE;


-- ──────────────────────────────────────────────────────────────
-- ANALYTICS_SIMCE_SERIE
-- Serie histórica completa de puntajes SIMCE del colegio piloto.
-- Tabla liviana — solo los puntos de la serie, sin cálculos.
-- Permite que el dashboard grafique sin recalcular.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.analytics_simce_serie (
    serie_id        BIGSERIAL   PRIMARY KEY,
    rbd             CHAR(8)     NOT NULL,
    agno            SMALLINT    NOT NULL,
    grado           VARCHAR(10) NOT NULL,   -- '4b','8b','2m','2b','6b'
    asignatura      VARCHAR(10) NOT NULL,   -- 'mat','lect','cie','his','ing'
    puntaje         NUMERIC(6,2),
    n_evaluados     SMALLINT,
    en_hiatus       BOOLEAN     NOT NULL DEFAULT FALSE,
    -- benchmarks del mismo año
    prom_comuna     NUMERIC(6,2),
    prom_nacional   NUMERIC(6,2),
    dif_nacional    NUMERIC(6,2),           -- puntaje - prom_nacional
    calculado_en    TIMESTAMP   NOT NULL DEFAULT now(),
    UNIQUE (rbd, agno, grado, asignatura)
);
COMMENT ON TABLE gold.analytics_simce_serie IS
    'Serie SIMCE del establecimiento piloto con benchmarks. Para graficar.';

CREATE INDEX IF NOT EXISTS idx_simce_serie_rbd_grado
    ON gold.analytics_simce_serie (rbd, grado, asignatura);


-- ──────────────────────────────────────────────────────────────
-- ANALYTICS_SIGE_SERIE
-- Serie histórica de promedios SIGE por asignatura y grado.
-- Agregada a nivel establecimiento-año (no por alumno).
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.analytics_sige_serie (
    serie_id        BIGSERIAL   PRIMARY KEY,
    rbd             CHAR(8)     NOT NULL,
    agno            SMALLINT    NOT NULL,
    grado           VARCHAR(10) NOT NULL,   -- '4b','8b', etc. o cod_grado int como '4','8'
    asignatura      VARCHAR(80),            -- subsector SIGE (puede ser largo)
    prom_notas      NUMERIC(4,2),
    tasa_aprobacion NUMERIC(5,2),           -- porcentaje 0-100
    tasa_asistencia NUMERIC(5,2),           -- porcentaje 0-100
    n_alumnos       SMALLINT,
    calculado_en    TIMESTAMP   NOT NULL DEFAULT now(),
    UNIQUE (rbd, agno, grado, asignatura)
);
COMMENT ON TABLE gold.analytics_sige_serie IS
    'Serie SIGE del establecimiento piloto. Agrega fact_calificaciones por año/grado/asig.';

CREATE INDEX IF NOT EXISTS idx_sige_serie_rbd_grado
    ON gold.analytics_sige_serie (rbd, grado, asignatura);


-- ──────────────────────────────────────────────────────────────
-- ANALYTICS_RUN_LOG
-- Registro de ejecuciones del AnalyticsPipeline.
-- Permite saber cuándo se calculó por última vez y si hubo errores.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.analytics_run_log (
    run_id          BIGSERIAL   PRIMARY KEY,
    rbd             CHAR(8)     NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'running',  -- running|ok|error
    modules_run     TEXT[],                  -- qué módulos corrieron
    rows_written    INT         NOT NULL DEFAULT 0,
    error_msg       TEXT,
    started_at      TIMESTAMP   NOT NULL DEFAULT now(),
    finished_at     TIMESTAMP,
    pipeline_version VARCHAR(20) NOT NULL DEFAULT '1.0'
);
COMMENT ON TABLE gold.analytics_run_log IS
    'Log de ejecuciones del AnalyticsPipeline. Equivalente a etl_control para analytics.';

CREATE INDEX IF NOT EXISTS idx_analytics_run_rbd
    ON gold.analytics_run_log (rbd, started_at DESC);