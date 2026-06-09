-- ============================================================
-- BuzzINT — 03_patches.sql
-- Correcciones sobre el schema base (01_schema.sql).
-- Ejecutar UNA sola vez después de 01_schema.sql y 02_seed.sql.
-- Idempotente: todos los cambios usan IF NOT EXISTS / DO NOTHING.
-- ============================================================
SET search_path TO gold, public;


-- ── PATCH 1: fact_matricula ────────────────────────────────────────────────────
-- Problema: UNIQUE(alumno_id, establecimiento_id, tiempo_id) no cubre traslados.
-- Un alumno puede matricularse en dos establecimientos el mismo año/grado.
-- La clave real es (alumno, establecimiento, tiempo, enseñanza).

ALTER TABLE gold.fact_matricula DROP CONSTRAINT IF EXISTS fact_matricula_alumno_id_establecimiento_id_tiempo_id_key;

-- Columna cod_ense que faltaba para diferenciar traslados entre niveles
-- NOTA: Agregar ANTES del constraint que la usa
ALTER TABLE gold.fact_matricula
    ADD COLUMN IF NOT EXISTS cod_ense CHAR(2);

-- Nueva constraint que sí permite traslados
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_fact_matricula'
          AND conrelid = 'gold.fact_matricula'::regclass
    ) THEN
        ALTER TABLE gold.fact_matricula
            ADD CONSTRAINT uq_fact_matricula
            UNIQUE (alumno_id, establecimiento_id, tiempo_id, cod_ense);
    END IF;
END $$;


-- ── PATCH 2: etl_control — índices para queries de monitoreo ─────────────────
CREATE INDEX IF NOT EXISTS idx_etl_control_loader_status
    ON gold.etl_control (loader, status);

CREATE INDEX IF NOT EXISTS idx_etl_control_started
    ON gold.etl_control (started_at DESC);


-- ── PATCH 3: dim_tiempo_escolar — limpiar el hack de cod_grado=0 ─────────────
-- load_cargos insertaba (agno, 0, 'establecimiento') para registrar cargos
-- sin grado. La solución correcta es NULL en tiempo_id cuando no aplica.
-- Borramos las filas ficticias si existen.
DELETE FROM gold.dim_tiempo_escolar WHERE cod_grado = 0;

-- fact_docentes: permitir tiempo_id NULL (cargo sin grado específico)
ALTER TABLE gold.fact_docentes
    ALTER COLUMN tiempo_id DROP NOT NULL;


-- ── PATCH 4: tabla de configuración de entorno ────────────────────────────────
-- Permite que el código detecte si está en dev o prod y ajuste su comportamiento.
CREATE TABLE IF NOT EXISTS gold.env_profile (
    profile_id   SERIAL PRIMARY KEY,
    env          VARCHAR(20) NOT NULL DEFAULT 'dev',   -- 'dev' | 'prod'
    chunk_size   INT         NOT NULL DEFAULT 20000,
    pool_size    INT         NOT NULL DEFAULT 5,
    use_copy     BOOLEAN     NOT NULL DEFAULT TRUE,     -- FALSE en prod (OrangePi)
    notes        TEXT,
    set_at       TIMESTAMP   NOT NULL DEFAULT now()
);
COMMENT ON TABLE gold.env_profile IS
    'Perfil de entorno activo. El loader lee la última fila para ajustar estrategia.';

-- Insertar perfiles base si no existen
INSERT INTO gold.env_profile (env, chunk_size, pool_size, use_copy, notes)
SELECT 'dev',  50000, 10, TRUE,  '2× Xeon X5680 — ETL pesado, COPY masivo OK'
WHERE NOT EXISTS (SELECT 1 FROM gold.env_profile WHERE env = 'dev');

INSERT INTO gold.env_profile (env, chunk_size, pool_size, use_copy, notes)
SELECT 'prod', 500,   2,  FALSE, 'OrangePi 4GB — solo SIGE sync, execute_values'
WHERE NOT EXISTS (SELECT 1 FROM gold.env_profile WHERE env = 'prod');


-- ── PATCH 5: vista unpivot de calificaciones ──────────────────────────────────
-- fact_calificaciones tiene 25 columnas nota_1..nota_25 (wide).
-- Esta vista las convierte a formato long para queries analíticas.
-- El wide se mantiene para COPY masivo y memoria en OrangePi.
CREATE OR REPLACE VIEW gold.v_notas_long AS
SELECT
    fc.cal_id,
    fc.alumno_id,
    fc.establecimiento_id,
    fc.tiempo_id,
    fc.asignatura_id,
    fc.letra,
    fc.n_orden,
    fc.promedio,
    fc.asistencia_pct,
    fc.situacion_final,
    n.slot,
    n.nota
FROM gold.fact_calificaciones fc
CROSS JOIN LATERAL (
    VALUES
        (1,  fc.nota_1),  (2,  fc.nota_2),  (3,  fc.nota_3),
        (4,  fc.nota_4),  (5,  fc.nota_5),  (6,  fc.nota_6),
        (7,  fc.nota_7),  (8,  fc.nota_8),  (9,  fc.nota_9),
        (10, fc.nota_10), (11, fc.nota_11), (12, fc.nota_12),
        (13, fc.nota_13), (14, fc.nota_14), (15, fc.nota_15),
        (16, fc.nota_16), (17, fc.nota_17), (18, fc.nota_18),
        (19, fc.nota_19), (20, fc.nota_20), (21, fc.nota_21),
        (22, fc.nota_22), (23, fc.nota_23), (24, fc.nota_24),
        (25, fc.nota_25)
) AS n(slot, nota)
WHERE n.nota IS NOT NULL;

COMMENT ON VIEW gold.v_notas_long IS
    'Calificaciones en formato long (una fila por nota). Solo notas no nulas.';


-- ── PATCH 6: vista resumen de SIGE para OrangePi ─────────────────────────────
-- Query liviana para el dashboard — no toca las tablas pesadas.
CREATE OR REPLACE VIEW gold.v_sige_resumen AS
SELECT
    t.agno,
    t.cod_grado,
    t.grado_label,
    e.rbd,
    e.nom_rbd,
    fc.letra,
    COUNT(DISTINCT fc.alumno_id)                              AS n_alumnos,
    ROUND(AVG(fc.promedio)   FILTER (WHERE fc.promedio IS NOT NULL), 2) AS prom_curso,
    ROUND(AVG(fc.asistencia_pct::NUMERIC)
          FILTER (WHERE fc.asistencia_pct IS NOT NULL), 1)   AS asist_prom,
    SUM(CASE WHEN fc.situacion_final = 'APR' THEN 1 ELSE 0 END) AS aprobados,
    SUM(CASE WHEN fc.situacion_final = 'REP' THEN 1 ELSE 0 END) AS reprobados,
    SUM(CASE WHEN fc.situacion_final = 'RET' THEN 1 ELSE 0 END) AS retirados
FROM gold.fact_calificaciones fc
JOIN gold.dim_tiempo_escolar  t  ON t.tiempo_id = fc.tiempo_id
JOIN gold.dim_establecimiento e  ON e.establecimiento_id = fc.establecimiento_id
GROUP BY t.agno, t.cod_grado, t.grado_label, e.rbd, e.nom_rbd, fc.letra;

COMMENT ON VIEW gold.v_sige_resumen IS
    'Resumen de calificaciones SIGE por curso. Liviano para OrangePi (4GB).';


-- ── PATCH 7: índices adicionales para queries frecuentes ─────────────────────
CREATE INDEX IF NOT EXISTS idx_fact_cal_promedio
    ON gold.fact_calificaciones (establecimiento_id, tiempo_id)
    WHERE promedio IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_fact_matricula_ense
    ON gold.fact_matricula (establecimiento_id, tiempo_id, cod_ense);

CREATE INDEX IF NOT EXISTS idx_dim_estab_depe
    ON gold.dim_establecimiento (cod_depe, estado_estab);