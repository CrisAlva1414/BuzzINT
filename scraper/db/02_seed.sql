-- ============================================================
-- BuzzINT — Seed data para dimensiones sintéticas
-- Ejecutar DESPUÉS de 01_schema.sql
-- ============================================================
SET search_path TO gold, public;

-- ──────────────────────────────────────────────────────────────
-- DIM_TIEMPO_ESCOLAR  (2013-2030, grados 1-12)
-- cod_grado 1-8  = Educación Básica
-- cod_grado 9-12 = Educación Media
-- ──────────────────────────────────────────────────────────────
INSERT INTO gold.dim_tiempo_escolar (agno, cod_grado, grado_label, nivel, ciclo)
SELECT
    a.agno,
    g.cod_grado,
    CASE
        WHEN g.cod_grado BETWEEN 1 AND 8
            THEN g.cod_grado || '° básico'
        ELSE (g.cod_grado - 8) || '° medio'
    END                                                     AS grado_label,
    CASE WHEN g.cod_grado <= 8 THEN 'basico' ELSE 'medio' END AS nivel,
    CASE
        WHEN g.cod_grado BETWEEN 1 AND 4 THEN 'primer_ciclo'
        WHEN g.cod_grado BETWEEN 5 AND 8 THEN 'segundo_ciclo'
        ELSE 'EM'
    END                                                     AS ciclo
FROM
    generate_series(2013, 2030) AS a(agno),
    generate_series(1, 12)      AS g(cod_grado)
ON CONFLICT (agno, cod_grado) DO NOTHING;

-- ──────────────────────────────────────────────────────────────
-- DIM_ASIGNATURA — catálogo base MINEDUC (cod_ense conocidos)
-- Fuente: Resolución MINEDUC sobre tipos de enseñanza
-- Se completa/actualiza al cargar cargos y SIGE
-- ──────────────────────────────────────────────────────────────
INSERT INTO gold.dim_asignatura (cod_ense, cod_ense2, cod_ense3, nom_ense, subsector)
VALUES
    ('10', NULL, NULL, 'Educación Parvularia',              NULL),
    ('11', NULL, NULL, 'Educación Básica',                  NULL),
    ('14', NULL, NULL, 'Ed. Básica Especial Diferencial',   NULL),
    ('16', NULL, NULL, 'Ed. Media Adultos',                 NULL),
    ('23', NULL, NULL, 'Humanístico-Científica Diurna',     NULL),
    ('24', NULL, NULL, 'Humanístico-Científica Nocturna',   NULL),
    ('25', NULL, NULL, 'Técnico-Profesional',               NULL),
    ('26', NULL, NULL, 'Artística',                         NULL),
    ('31', NULL, NULL, 'Ed. Especial (Escuela Especial)',   NULL),
    ('63', NULL, NULL, 'Ed. de Adultos Básica',             NULL),
    ('73', NULL, NULL, 'Ed. de Adultos Media',              NULL),
    ('96', NULL, NULL, 'Ed. Parvularia Especial',           NULL)
ON CONFLICT DO NOTHING;

-- Subsectores SIGE frecuentes (se amplían al cargar sige_profesores.csv)
INSERT INTO gold.dim_asignatura (cod_ense, subsector, nom_ense)
VALUES
    ('11', 'Lenguaje y Comunicación',      'Ed. Básica — Lenguaje'),
    ('11', 'Matemática',                   'Ed. Básica — Matemática'),
    ('11', 'Ciencias Naturales',           'Ed. Básica — Ciencias'),
    ('11', 'Historia, Geografía y C.S.',   'Ed. Básica — Historia'),
    ('11', 'Educación Física',             'Ed. Básica — Ed. Física'),
    ('11', 'Artes Visuales',               'Ed. Básica — Artes Visuales'),
    ('11', 'Música',                       'Ed. Básica — Música'),
    ('11', 'Inglés',                       'Ed. Básica — Inglés'),
    ('11', 'Tecnología',                   'Ed. Básica — Tecnología'),
    ('11', 'Religión',                     'Ed. Básica — Religión'),
    ('11', 'Orientación',                  'Ed. Básica — Orientación'),
    ('23', 'Lengua y Literatura',          'HC — Lengua y Literatura'),
    ('23', 'Matemática',                   'HC — Matemática'),
    ('23', 'Biología',                     'HC — Biología'),
    ('23', 'Química',                      'HC — Química'),
    ('23', 'Física',                       'HC — Física'),
    ('23', 'Historia, Geografía y C.S.',   'HC — Historia'),
    ('23', 'Inglés',                       'HC — Inglés'),
    ('23', 'Educación Física y Salud',     'HC — Ed. Física'),
    ('23', 'Artes Visuales',               'HC — Artes Visuales'),
    ('23', 'Música',                       'HC — Música'),
    ('23', 'Filosofía',                    'HC — Filosofía'),
    ('23', 'Orientación',                  'HC — Orientación')
ON CONFLICT DO NOTHING;
