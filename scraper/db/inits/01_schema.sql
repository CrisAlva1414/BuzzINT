-- ============================================================
-- BuzzINT — Gold Layer Schema
-- Galaxy Schema: 6 dimensiones conformed + 5 fact tables
-- PostgreSQL 15+
-- ============================================================

-- Extensión para UUIDs (no la usamos como PK aquí, pero útil)
-- CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ──────────────────────────────────────────────────────────────
-- ESQUEMA
-- ──────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS gold;
SET search_path TO gold, public;

-- ──────────────────────────────────────────────────────────────
-- DIM_TERRITORIO
-- Granularidad: comuna (hoja de la jerarquía región→prov→deprov→comuna)
-- Fuente: mineduc_establecimientos.csv (SELECT DISTINCT sobre cols geo)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_territorio (
    territorio_id   SERIAL PRIMARY KEY,
    cod_com         CHAR(5)      NOT NULL UNIQUE,   -- código comuna MINEDUC (5 dígitos)
    nom_com         VARCHAR(80),
    cod_pro         CHAR(3),
    cod_deprov      CHAR(4),
    nom_deprov      VARCHAR(80),
    cod_reg         CHAR(2)      NOT NULL,
    nom_reg         VARCHAR(80),
    -- metadatos ETL
    _cargado_en     TIMESTAMP    NOT NULL DEFAULT now(),
    _fuente         VARCHAR(120) NOT NULL DEFAULT 'mineduc_establecimientos'
);
COMMENT ON TABLE gold.dim_territorio IS
    'Jerarquía geográfica MINEDUC: región → provincia → deprov → comuna';

-- ──────────────────────────────────────────────────────────────
-- DIM_ESTABLECIMIENTO  (SCD Type-1 — última versión gana)
-- Fuente autoritativa: mineduc_establecimientos.csv
-- Clave de negocio: rbd (8 dígitos, zero-padded)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_establecimiento (
    establecimiento_id  SERIAL PRIMARY KEY,
    rbd                 CHAR(8)      NOT NULL UNIQUE,
    dgv_rbd             CHAR(1),
    nom_rbd             VARCHAR(120),
    cod_depe            SMALLINT,    -- 1=Municipal 2=Part-subv 3=Part-pagado 4=Corp 5=SLEP
    cod_depe2           SMALLINT,
    rural_rbd           SMALLINT,    -- 0=urbano 1=rural
    estado_estab        SMALLINT,    -- 1=abierto 2=cerrado
    nombre_slep         VARCHAR(80),
    convenio_pie        SMALLINT,
    pace                SMALLINT,
    ori_religiosa       SMALLINT,
    territorio_id       INT REFERENCES gold.dim_territorio(territorio_id),
    -- metadatos ETL
    _cargado_en         TIMESTAMP    NOT NULL DEFAULT now(),
    _actualizado_en     TIMESTAMP    NOT NULL DEFAULT now(),
    _fuente             VARCHAR(120) NOT NULL DEFAULT 'mineduc_establecimientos'
);
COMMENT ON TABLE gold.dim_establecimiento IS
    'Establecimientos educacionales MINEDUC. SCD Type-1: sobrescritura.';
COMMENT ON COLUMN gold.dim_establecimiento.cod_depe IS
    '1=Municipal 2=Part-subvencionado 3=Part-pagado 4=Corp.municipal 5=SLEP';

-- ──────────────────────────────────────────────────────────────
-- DIM_TIEMPO_ESCOLAR  (generada sintéticamente)
-- Clave de negocio: (agno, cod_grado)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_tiempo_escolar (
    tiempo_id   SERIAL PRIMARY KEY,
    agno        SMALLINT     NOT NULL,
    cod_grado   SMALLINT     NOT NULL,   -- 1-8 básico, 9-12 medio (convención MINEDUC)
    grado_label VARCHAR(20),             -- '1° básico', '4° medio', etc.
    nivel       VARCHAR(10),             -- 'basico' | 'medio'
    ciclo       VARCHAR(20),             -- 'primer_ciclo' | 'segundo_ciclo' | 'EM'
    UNIQUE (agno, cod_grado)
);
COMMENT ON TABLE gold.dim_tiempo_escolar IS
    'Dimensión tiempo escolar: año × grado. Generada sintéticamente 2013-2030.';

-- ──────────────────────────────────────────────────────────────
-- DIM_ALUMNO  (deduplicada por mrun, attrs más recientes ganan)
-- Fuente: mineduc_alumnos.csv  +  sige_calificaciones.csv
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_alumno (
    alumno_id   SERIAL PRIMARY KEY,
    mrun        BIGINT       NOT NULL UNIQUE,
    gen_alu     SMALLINT,    -- 1=Masculino 2=Femenino
    fec_nac     DATE,
    -- metadatos ETL
    _cargado_en     TIMESTAMP NOT NULL DEFAULT now(),
    _actualizado_en TIMESTAMP NOT NULL DEFAULT now()
);
COMMENT ON TABLE gold.dim_alumno IS
    'Alumnos únicos por MRUN. Atributos estables (género, fecha nac).';

-- ──────────────────────────────────────────────────────────────
-- DIM_DOCENTE
-- Fuente: mineduc_cargos.csv
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_docente (
    docente_id      SERIAL PRIMARY KEY,
    mrun            BIGINT       NOT NULL UNIQUE,
    doc_genero      SMALLINT,
    fec_nac         DATE,
    -- metadatos ETL
    _cargado_en     TIMESTAMP NOT NULL DEFAULT now(),
    _actualizado_en TIMESTAMP NOT NULL DEFAULT now()
);
COMMENT ON TABLE gold.dim_docente IS
    'Docentes únicos por MRUN. Atributos estables.';

-- ──────────────────────────────────────────────────────────────
-- DIM_ASIGNATURA
-- Fuente: mineduc_cargos.csv + sige_profesores.csv
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.dim_asignatura (
    asignatura_id   SERIAL PRIMARY KEY,
    cod_ense        CHAR(2)      NOT NULL,
    cod_ense2       CHAR(2),
    cod_ense3       CHAR(2),
    subsector       VARCHAR(120),
    nom_ense        VARCHAR(120)
);
-- Unicidad expresiva: (cod_ense, subsector) tratando NULL como ''
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_asignatura_ense_sub
    ON gold.dim_asignatura (cod_ense, COALESCE(subsector, ''));
COMMENT ON TABLE gold.dim_asignatura IS
    'Asignaturas/subsectores curriculares MINEDUC.';

-- ──────────────────────────────────────────────────────────────
-- FACT_SIMCE
-- Granularidad: (rbd, agno, cod_grado) — nivel RBD
-- Medidas: puntajes por prueba, n_evaluados
-- Fuente: simce__rbd.csv
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.fact_simce (
    simce_id            BIGSERIAL PRIMARY KEY,
    -- claves dimensionales
    establecimiento_id  INT      NOT NULL REFERENCES gold.dim_establecimiento(establecimiento_id),
    tiempo_id           INT      NOT NULL REFERENCES gold.dim_tiempo_escolar(tiempo_id),
    -- medidas
    ptje_mat            NUMERIC(6,2),
    ptje_lect           NUMERIC(6,2),
    ptje_cie            NUMERIC(6,2),
    ptje_his            NUMERIC(6,2),
    ptje_ing            NUMERIC(6,2),
    n_eval_mat          SMALLINT,
    n_eval_lect         SMALLINT,
    n_eval_cie          SMALLINT,
    -- indicadores adicionales que vienen en el CSV SIMCE
    gse_predominante    VARCHAR(20),   -- GSE del establecimiento ese año
    cod_grupo           VARCHAR(10),   -- grupo socioeconómico SIMCE
    -- metadatos
    _source_rar         VARCHAR(200),
    _cargado_en         TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (establecimiento_id, tiempo_id)
);
COMMENT ON TABLE gold.fact_simce IS
    'Resultados SIMCE por establecimiento y año/grado. Granularidad RBD.';

CREATE INDEX IF NOT EXISTS idx_fact_simce_tiempo
    ON gold.fact_simce(tiempo_id);
CREATE INDEX IF NOT EXISTS idx_fact_simce_estab
    ON gold.fact_simce(establecimiento_id);

-- ──────────────────────────────────────────────────────────────
-- FACT_ESTABLECIMIENTO_ANUAL
-- Granularidad: (rbd, agno)
-- Medidas: matrícula, lat/lon vigentes, flags PIE/PACE
-- Fuente: mineduc_establecimientos.csv
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.fact_establecimiento_anual (
    estab_anual_id      BIGSERIAL PRIMARY KEY,
    establecimiento_id  INT      NOT NULL REFERENCES gold.dim_establecimiento(establecimiento_id),
    agno                SMALLINT NOT NULL,
    -- medidas
    mat_total           INT,
    latitud             NUMERIC(10,6),
    longitud            NUMERIC(10,6),
    convenio_pie        SMALLINT,
    pace                SMALLINT,
    pago_matricula      NUMERIC(10,2),
    pago_mensual        NUMERIC(10,2),
    -- enseñanzas habilitadas ese año (flags 0/1)
    ens_01 SMALLINT, ens_02 SMALLINT, ens_03 SMALLINT,
    ens_04 SMALLINT, ens_05 SMALLINT, ens_06 SMALLINT,
    ens_07 SMALLINT, ens_08 SMALLINT, ens_09 SMALLINT,
    ens_10 SMALLINT, ens_11 SMALLINT,
    -- metadatos
    _source_file        VARCHAR(200),
    _cargado_en         TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (establecimiento_id, agno)
);
COMMENT ON TABLE gold.fact_establecimiento_anual IS
    'Snapshot anual del establecimiento: matrícula, coordenadas, flags PIE/PACE.';

-- ──────────────────────────────────────────────────────────────
-- FACT_DOCENTES
-- Granularidad: (rbd, agno, mrun_docente, cod_ense)
-- Medidas: horas contratadas, tipo cargo, jornada
-- Fuente: mineduc_cargos.csv
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.fact_docentes (
    docente_cargo_id    BIGSERIAL PRIMARY KEY,
    establecimiento_id  INT      NOT NULL REFERENCES gold.dim_establecimiento(establecimiento_id),
    tiempo_id           INT      NOT NULL REFERENCES gold.dim_tiempo_escolar(tiempo_id),
    docente_id          INT      REFERENCES gold.dim_docente(docente_id),
    asignatura_id       INT      REFERENCES gold.dim_asignatura(asignatura_id),
    -- medidas
    n_horas             NUMERIC(5,1),
    tipo_cargo          VARCHAR(60),
    cod_cargo           SMALLINT,
    jornada             SMALLINT,
    -- metadatos
    _source_file        VARCHAR(200),
    _cargado_en         TIMESTAMP NOT NULL DEFAULT now()
);
COMMENT ON TABLE gold.fact_docentes IS
    'Cargos docentes por establecimiento, año y asignatura.';

CREATE INDEX IF NOT EXISTS idx_fact_docentes_estab
    ON gold.fact_docentes(establecimiento_id);
CREATE INDEX IF NOT EXISTS idx_fact_docentes_tiempo
    ON gold.fact_docentes(tiempo_id);
CREATE INDEX IF NOT EXISTS idx_fact_docentes_docente
    ON gold.fact_docentes(docente_id);

-- ──────────────────────────────────────────────────────────────
-- FACT_MATRICULA
-- Granularidad: (mrun, rbd, agno, cod_grado)
-- Medidas: criterios SEP, jornada, curso
-- Fuente: mineduc_alumnos.csv
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.fact_matricula (
    matricula_id        BIGSERIAL PRIMARY KEY,
    alumno_id           INT      NOT NULL REFERENCES gold.dim_alumno(alumno_id),
    establecimiento_id  INT      NOT NULL REFERENCES gold.dim_establecimiento(establecimiento_id),
    tiempo_id           INT      NOT NULL REFERENCES gold.dim_tiempo_escolar(tiempo_id),
    -- medidas / atributos deductivos
    let_cur             CHAR(2),     -- letra de curso (A, B, ...)
    cod_jor             SMALLINT,    -- jornada
    criterio_sep        SMALLINT,
    prioritario_alu     SMALLINT,
    preferente_alu      SMALLINT,
    ben_sep             SMALLINT,
    -- metadatos
    _source_file        VARCHAR(200),
    _cargado_en         TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (alumno_id, establecimiento_id, tiempo_id)
);
COMMENT ON TABLE gold.fact_matricula IS
    'Matrícula SEP: alumno × establecimiento × año/grado. Una fila por inscripción anual.';

CREATE INDEX IF NOT EXISTS idx_fact_matricula_alumno
    ON gold.fact_matricula(alumno_id);
CREATE INDEX IF NOT EXISTS idx_fact_matricula_estab
    ON gold.fact_matricula(establecimiento_id);

-- ──────────────────────────────────────────────────────────────
-- FACT_CALIFICACIONES  (columnas anchas — 25 slots de nota)
-- Granularidad: (rbd, agno, cod_grado, letra, n_orden, n_asig)
-- Medidas: nota_1..nota_25, promedio, asistencia, situacion_final
-- Fuente: sige_calificaciones.csv
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.fact_calificaciones (
    cal_id              BIGSERIAL PRIMARY KEY,
    alumno_id           INT      REFERENCES gold.dim_alumno(alumno_id),
    establecimiento_id  INT      NOT NULL REFERENCES gold.dim_establecimiento(establecimiento_id),
    tiempo_id           INT      NOT NULL REFERENCES gold.dim_tiempo_escolar(tiempo_id),
    asignatura_id       INT      REFERENCES gold.dim_asignatura(asignatura_id),
    -- identificadores de acta
    letra               CHAR(2)  NOT NULL,
    n_orden             SMALLINT,
    fecha_acta          VARCHAR(12),
    -- notas (25 slots — columnas anchas)
    nota_1  NUMERIC(4,2), nota_2  NUMERIC(4,2), nota_3  NUMERIC(4,2),
    nota_4  NUMERIC(4,2), nota_5  NUMERIC(4,2), nota_6  NUMERIC(4,2),
    nota_7  NUMERIC(4,2), nota_8  NUMERIC(4,2), nota_9  NUMERIC(4,2),
    nota_10 NUMERIC(4,2), nota_11 NUMERIC(4,2), nota_12 NUMERIC(4,2),
    nota_13 NUMERIC(4,2), nota_14 NUMERIC(4,2), nota_15 NUMERIC(4,2),
    nota_16 NUMERIC(4,2), nota_17 NUMERIC(4,2), nota_18 NUMERIC(4,2),
    nota_19 NUMERIC(4,2), nota_20 NUMERIC(4,2), nota_21 NUMERIC(4,2),
    nota_22 NUMERIC(4,2), nota_23 NUMERIC(4,2), nota_24 NUMERIC(4,2),
    nota_25 NUMERIC(4,2),
    -- medidas resumen
    promedio            NUMERIC(4,2),
    prom_literario      VARCHAR(3),    -- 'MB', 'B', 'S', 'I'
    asistencia_pct      SMALLINT,
    situacion_final     VARCHAR(4),    -- 'APR', 'REP', 'RET'
    observaciones       TEXT,
    -- metadatos
    _source_file        VARCHAR(200),
    _cargado_en         TIMESTAMP NOT NULL DEFAULT now()
);
-- Unicidad expresiva: asignatura_id NULL tratado como -1
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_calificaciones
    ON gold.fact_calificaciones (establecimiento_id, tiempo_id, letra, n_orden, COALESCE(asignatura_id, -1));
COMMENT ON TABLE gold.fact_calificaciones IS
    'Calificaciones SIGE por alumno, asignatura y acta. Columnas anchas (25 notas).';

CREATE INDEX IF NOT EXISTS idx_fact_cal_alumno
    ON gold.fact_calificaciones(alumno_id);
CREATE INDEX IF NOT EXISTS idx_fact_cal_estab
    ON gold.fact_calificaciones(establecimiento_id);
CREATE INDEX IF NOT EXISTS idx_fact_cal_tiempo
    ON gold.fact_calificaciones(tiempo_id);

-- ──────────────────────────────────────────────────────────────
-- STAGING TABLES — granularidades geo SIMCE agregadas
-- No son fact tables analíticas, solo staging para vistas
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.stg_simce_comuna (
    LIKE gold.fact_simce INCLUDING DEFAULTS EXCLUDING CONSTRAINTS
);
ALTER TABLE gold.stg_simce_comuna
    ADD COLUMN IF NOT EXISTS cod_com CHAR(5),
    DROP COLUMN IF EXISTS establecimiento_id;

CREATE TABLE IF NOT EXISTS gold.stg_simce_region (
    LIKE gold.fact_simce INCLUDING DEFAULTS EXCLUDING CONSTRAINTS
);
ALTER TABLE gold.stg_simce_region
    ADD COLUMN IF NOT EXISTS cod_reg CHAR(2),
    DROP COLUMN IF EXISTS establecimiento_id;

-- ──────────────────────────────────────────────────────────────
-- VISTAS ANALÍTICAS
-- ──────────────────────────────────────────────────────────────

-- Vista principal: SIMCE con contexto completo
CREATE OR REPLACE VIEW gold.v_simce_completo AS
SELECT
    fs.simce_id,
    t.agno,
    t.cod_grado,
    t.grado_label,
    t.nivel,
    e.rbd,
    e.nom_rbd,
    e.cod_depe,
    e.rural_rbd,
    e.nombre_slep,
    ter.cod_com,
    ter.nom_com,
    ter.cod_reg,
    ter.nom_reg,
    fs.ptje_mat,
    fs.ptje_lect,
    fs.ptje_cie,
    fs.n_eval_mat,
    fs.n_eval_lect,
    fs.gse_predominante
FROM gold.fact_simce fs
JOIN gold.dim_tiempo_escolar t   ON t.tiempo_id = fs.tiempo_id
JOIN gold.dim_establecimiento e  ON e.establecimiento_id = fs.establecimiento_id
LEFT JOIN gold.dim_territorio ter ON ter.territorio_id = e.territorio_id;

-- Vista: promedio SIMCE por establecimiento (serie histórica)
CREATE OR REPLACE VIEW gold.v_simce_serie_rbd AS
SELECT
    e.rbd,
    e.nom_rbd,
    t.agno,
    t.cod_grado,
    t.grado_label,
    ROUND(AVG(fs.ptje_mat)  FILTER (WHERE fs.ptje_mat  IS NOT NULL), 1) AS avg_mat,
    ROUND(AVG(fs.ptje_lect) FILTER (WHERE fs.ptje_lect IS NOT NULL), 1) AS avg_lect,
    ROUND(AVG(fs.ptje_cie)  FILTER (WHERE fs.ptje_cie  IS NOT NULL), 1) AS avg_cie,
    SUM(fs.n_eval_mat) AS total_eval
FROM gold.fact_simce fs
JOIN gold.dim_tiempo_escolar t  ON t.tiempo_id = fs.tiempo_id
JOIN gold.dim_establecimiento e ON e.establecimiento_id = fs.establecimiento_id
GROUP BY e.rbd, e.nom_rbd, t.agno, t.cod_grado, t.grado_label;

-- Vista: calificaciones con datos de alumno y asignatura
CREATE OR REPLACE VIEW gold.v_calificaciones_completo AS
SELECT
    fc.cal_id,
    t.agno,
    t.cod_grado,
    t.grado_label,
    e.rbd,
    e.nom_rbd,
    fc.letra,
    a.mrun,
    a.gen_alu,
    asig.subsector,
    asig.cod_ense,
    fc.promedio,
    fc.asistencia_pct,
    fc.situacion_final,
    fc.prom_literario
FROM gold.fact_calificaciones fc
JOIN gold.dim_tiempo_escolar t   ON t.tiempo_id = fc.tiempo_id
JOIN gold.dim_establecimiento e  ON e.establecimiento_id = fc.establecimiento_id
LEFT JOIN gold.dim_alumno a      ON a.alumno_id = fc.alumno_id
LEFT JOIN gold.dim_asignatura asig ON asig.asignatura_id = fc.asignatura_id;

-- ──────────────────────────────────────────────────────────────
-- TABLA DE CONTROL DE CARGA (para el orquestador)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gold.etl_control (
    run_id          BIGSERIAL PRIMARY KEY,
    loader          VARCHAR(80)  NOT NULL,   -- nombre del script loader
    source_file     VARCHAR(300) NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'running',  -- running|ok|error
    rows_read       INT,
    rows_inserted   INT,
    rows_updated    INT,
    rows_skipped    INT,
    error_msg       TEXT,
    started_at      TIMESTAMP    NOT NULL DEFAULT now(),
    finished_at     TIMESTAMP
);
COMMENT ON TABLE gold.etl_control IS
    'Registro de ejecuciones ETL. Usado por el orquestador para idempotencia.';
