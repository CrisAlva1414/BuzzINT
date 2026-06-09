# BuzzINT - Data Schema & Analysis Guide

Complete documentation of all CSV files in the BuzzINT data warehouse, including column names, data ranges, and temporal coverage.

---

## Overview

| File | Rows | Size | Years | Columns |
|------|------|------|-------|---------|
| mineduc_alumnos | 41,360,332 | 8.2 GB | 2008-2025 | 42 |
| mineduc_cargos | 5,356,748 | 2.1 GB | 2003-2025 | 162 |
| mineduc_establecimientos | 204,001 | 49 MB | 2013-2025 | 67 |

---

## 1. mineduc_alumnos.csv

**Primary Source:** MINEDUC (Chilean Ministry of Education)  
**Granularity:** Student enrollment records  
**Temporal Coverage:** 2008-2025 (18 years)  
**Row Count:** 41,360,332  
**File Size:** 8.2 GB

### Column Descriptions

| # | Column | Type | Description | Notes |
|---|--------|------|-------------|-------|
| 1 | agno | INT | School year | 2008-2025 |
| 2 | mrun | BIGINT | Student ID (RUN) | Unique identifier |
| 3 | gen_alu | SMALLINT | Student gender | 1=Male, 2=Female |
| 4 | fec_nac_alu | DATE | Birth date | Student's date of birth |
| 5 | fec_defun_alu | DATE | Death date | If applicable (NULL otherwise) |
| 6 | criterio_sep | SMALLINT | SEP criteria | Chilean educational support flag |
| 7 | prioritario_alu | SMALLINT | Priority student flag | 0/1 |
| 8 | preferente_alu | SMALLINT | Preferred student flag | 0/1 |
| 9 | ben_sep | SMALLINT | SEP benefits | 0/1 |
| 10 | rbd | CHAR(8) | School RBD code | 8-digit zero-padded identifier |
| 11 | dgv_rbd | CHAR(1) | RBD check digit | Verification digit |
| 12 | nom_rbd | VARCHAR(120) | School name | Official name |
| 13 | cod_reg_rbd | CHAR(2) | Region code | 2-digit region code |
| 14 | nom_reg_rbd_a | VARCHAR(80) | Region name | Region official name |
| 15 | cod_pro_rbd | CHAR(3) | Province code | 3-digit province code |
| 16 | cod_com_rbd | CHAR(5) | Municipality code | 5-digit municipality code |
| 17 | nom_com_rbd | VARCHAR(80) | Municipality name | City/town name |
| 18 | cod_deprov_rbd | CHAR(4) | Subprovince code | Sub-region code |
| 19 | nom_deprov_rbd | VARCHAR(80) | Subprovince name | Sub-region name |
| 20 | cod_depe | SMALLINT | School dependency | 1=Public, 2=Subsidized, 3=Private, 4=Corp |
| 21 | cod_depe2 | SMALLINT | Alternative dependency code | Secondary classification |
| 22 | rural_rbd | SMALLINT | Urban/Rural flag | 0=Urban, 1=Rural |
| 23 | estado_estab | SMALLINT | School status | 1=Active, 2=Closed |
| 24 | nombre_slep | VARCHAR(80) | SLEP name | School network name |
| 25 | convenio_sep | SMALLINT | SEP agreement | 0/1 |
| 26 | año_ingreso_sep | SMALLINT | SEP entry year | Year joined SEP |
| 27 | clasificacion_sep | VARCHAR(20) | SEP classification | SEP category |
| 28 | ee_gratuito | SMALLINT | Free school flag | 0/1 |
| 29 | cod_ense | CHAR(2) | Teaching level code | Education level (e.g., '10'=Primary) |
| 30 | cod_ense2 | CHAR(2) | Alternative teaching level | Secondary level code |
| 31 | cod_ense3 | CHAR(2) | Tertiary teaching level | Third level code |
| 32 | cod_grado | SMALLINT | Grade code | 1-8 (primary), 9-12 (secondary) |
| 33 | cod_grado2 | SMALLINT | Alternative grade code | Secondary grade |
| 34 | let_cur | CHAR(2) | Class letter | A, B, C, etc. |
| 35 | cod_jor | SMALLINT | School schedule | 1=Morning, 2=Afternoon, 3=Full-day |
| 36 | grado_sep | SMALLINT | SEP grade | Educational stage |
| 37 | let_rbd | CHAR(2) | School letter code | School identifier |
| 38 | num_rbd | VARCHAR(20) | School number | Numeric portion of RBD |
| 39 | _source_file | VARCHAR(200) | Source file | ETL tracking |
| 40 | alumno_id | INT | Normalized student ID | Database surrogate key |
| 41 | año | SMALLINT | Academic year | Normalized year field |
| 42 | nombre | VARCHAR(120) | Student name | Full name |

### Key Insights

- **Geographic Coverage:** Full Chile (all 16 regions)
- **School Types:** Municipal, subsidized, private, and corporation-managed schools
- **Student Population:** 41+ million enrollment records across 18 years
- **Gender Distribution:** `gen_alu` shows male/female split
- **Socioeconomic Filters:** SEP, priority, and preferent flags for vulnerable students

### Data Quality Notes

- **BOM Character:** Column 1 may have UTF-8 BOM (`﻿agno`)
- **NULL Values:** Common in `fec_defun_alu`, alternative codes
- **Duplicate Keys:** Multiple enrollments per student possible (school transfers)
- **Temporal Gaps:** Some years may have incomplete data

---

## 2. mineduc_cargos.csv

**Primary Source:** MINEDUC (Docent/Teacher Registry)  
**Granularity:** Teacher positions and academic credentials  
**Temporal Coverage:** 2003-2025 (23 years)  
**Row Count:** 5,356,748  
**File Size:** 2.1 GB

### Column Overview (162 columns total)

#### Core Identifiers (Cols 1-24)
| # | Column | Type | Range |
|---|--------|------|-------|
| 1 | agno | INT | 2003-2025 |
| 2-14 | geo_* | CHAR/VARCHAR | Region, province, municipality codes |
| 15 | clave | VARCHAR | Teacher unique identifier |
| 16 | mrun | BIGINT | Teacher RUN |
| 17 | doc_genero | SMALLINT | 1=M, 2=F |
| 18 | doc_fec_nac | DATE | Birth date |
| 19 | _source_file | VARCHAR | ETL tracking |

#### Academic History (Cols 20-34)
- `agno_nombramiento`: Appointment year
- `ano_servicio_*`: Years of service tracking
- `ano_titulacion_*`: Graduation year
- `bienios_carr_docente`: Career biennial count

#### Qualifications (Cols 35-72)
- `grado.*_1/2`: Degree levels (10-19 scale, 1=Bachelor, 2=Master, etc.)
- `cod_ens_*`: Subject teaching codes
- `esp_id_*`: Specialization identifiers
- `tip_tit_id_*`: Degree type identifiers

#### Assignments (Cols 73-87)
- `horas_*`: Contract hours breakdown
  - `horas_aula`: Classroom teaching hours
  - `horas_contrato`: Total contracted hours
  - `horas_direct`: Administrative hours
  - `horas_dentro/fuera_estab`: On/off-campus
  - `horas_tec_ped`: Technical/pedagogical hours

#### Mencion (Subject Specialty) Flags (Cols 88-142)
Binary flags (0/1) indicating teacher qualifications:
- `men_lenguaje_*`: Language & Literature
- `men_mate_*`: Mathematics
- `men_naturales_*`: Sciences
- `men_sociales_*`: Social Studies
- `men_ingles_*`: English
- `men_educacion_fisica_*`: Physical Education
- `men_educacion_musica_*`: Music
- `men_educacion_parv_*`: Early Childhood
- `men_religion_*`: Religious Studies
- And 30+ additional subject areas

#### Administrative Info (Cols 143-162)
- `mes_nombramiento`: Appointment month
- `modalidad_estudio_*`: Study method (Full-time, Part-time)
- `sector_*`: Teaching sector
- `nivel_*`: Educational level
- `subrogante`: Acting/substitute flag
- `subsector_*`: Curriculum subsector
- `tramo_carr_docente`: Career stage/bracket

### Key Insights

- **Longest Historical Coverage:** 23 years (2003-2025)
- **Teacher Population:** 5.3+ million position records
- **Qualification Spectrum:** Comprehensive academic credentials and specializations
- **Subject Coverage:** 35+ teaching specializations tracked
- **Contract Flexibility:** Detailed hours breakdown enables cost analysis

---

## 3. mineduc_establecimientos.csv

**Primary Source:** MINEDUC (School Registry)  
**Granularity:** School characteristics (annual snapshot)  
**Temporal Coverage:** 2013-2025 (13 years)  
**Row Count:** 204,001  
**File Size:** 49 MB

### Column Descriptions

| # | Column | Type | Description | Notes |
|---|--------|------|-------------|-------|
| 1 | agno | INT | School year | 2013-2025 |
| 2 | rbd | CHAR(8) | School RBD code | 8-digit identifier |
| 3 | dgv_rbd | CHAR(1) | Check digit | RBD verification |
| 4 | nom_rbd | VARCHAR(120) | School name | Official name |
| 5 | mrun | BIGINT | Principal's RUN | School leader ID |
| 6 | rut_sostenedor | CHAR(12) | Sponsor RUT | School owner ID |
| 7 | p_juridica | VARCHAR(60) | Legal entity type | Corporate structure |
| 8-18 | cod_reg/pro/com/deprov | CHAR | Geographic codes | Region hierarchy |
| 19-26 | nom_* | VARCHAR | Geographic names | Full names |
| 27 | cod_depe | SMALLINT | Dependency | 1=Public, 2=Subsidized, 3=Private |
| 28 | cod_depe2 | SMALLINT | Alternative | Secondary classification |
| 29 | rural_rbd | SMALLINT | Location | 0=Urban, 1=Rural |
| 30 | latitud | NUMERIC(12,6) | Latitude | Geographic coordinate |
| 31 | longitud | NUMERIC(12,6) | Longitude | Geographic coordinate |
| 32 | convenio_pie | SMALLINT | PIE agreement | Special education flag |
| 33 | pace | SMALLINT | PACE program | Acceleration program flag |
| 34-44 | ens_01 to ens_11 | SMALLINT | Education levels | Binary flags (0/1) |
| 45 | mat_total | INT | Total enrollment | Students count |
| 46 | matricula | INT | Enrollment | Alternative field |
| 47 | estado_estab | SMALLINT | School status | 1=Active, 2=Closed |
| 48 | ori_religiosa | SMALLINT | Religious affiliation | 0=None, 1=Catholic, etc. |
| 49 | pago_matricula | NUMERIC(10,2) | Enrollment fee | Monthly payment (CLP) |
| 50 | pago_mensual | NUMERIC(10,2) | Monthly fee | Tuition (CLP) |
| 51 | _source_file | VARCHAR(200) | ETL source | Tracking field |
| 52 | dv_rbd | CHAR(1) | Alternative check digit | Duplicate field |
| 53 | ens_12 | SMALLINT | Education level 12 | Additional level |
| 54-64 | espe_01 to espe_11 | SMALLINT | Specializations | Binary flags |
| 65-71 | mat_ens_* | INT | Enrollment by level | Per-grade counts |
| 72 | nombre | VARCHAR(120) | School name | Duplicate field |
| 73 | num_rbd | VARCHAR(20) | RBD numeric | Numeric portion |
| 74 | ori_otro_glosa | VARCHAR(200) | Religion description | Text explanation |
| 75 | provincia | VARCHAR(80) | Province | Geographic unit |
| 76 | región | VARCHAR(80) | Region | Geographic unit |
| 77 | agno_dup | INT | Duplicate year | Data artifact |

### Education Levels (ens_XX flags)

Bit flags indicating which teaching levels are offered:

| Code | Level | Description |
|------|-------|-------------|
| ens_01 | 10 | Primary (Educación Básica) |
| ens_02 | 110 | Secondary (Educación Media) |
| ens_03 | 310 | Pre-kindergarten |
| ens_04 | 320 | Kindergarten |
| ens_05 | 410 | Technical Secondary |
| ens_06 | 500 | Adult Education |
| ens_07-11 | Various | Special programs |
| ens_12 | Specialized | Additional programs |

### Key Insights

- **School Snapshot Data:** Annual point-in-time captures
- **Geographic Completeness:** All school locations with coordinates
- **Fee Tracking:** Monthly costs in Chilean pesos (CLP)
- **Program Participation:** Flags for special programs (PIE, PACE, etc.)
- **Shorter History:** Only 13 years vs. 23 years for teacher data

### Data Quality Notes

- **Duplicate Columns:** `nom_rbd`, `agno`, `dv_rbd` appear multiple times
- **Gaps:** Some schools may not appear every year
- **Coordinates:** Some schools may have missing/null geo data
- **Currency:** Fees in CLP; inflation adjustments needed for year-over-year analysis

---

## Analysis Recommendations for LLM Consumption

### For Data Exploration Tasks

1. **Always specify the year range** (e.g., "analyze 2015-2020 data")
2. **Filter by dependency** when needed (public vs. private schools respond differently)
3. **Group by geography** (region/municipality) for regional analysis
4. **Normalize by enrollment** (use mat_total or count(*) in FROM clause)

### For Time Series Analysis

- **mineduc_cargos**: Best for long-term trends (2003-2025)
- **mineduc_alumnos**: Best for broad population analysis (2008-2025)
- **mineduc_establecimientos**: Best for school characteristics (2013-2025)

### For Quality Assurance

- Watch for **NULL values** in optional fields
- **Deduplicate** on (rbd, agno) for establecimientos
- **Handle transferencias** (student school changes) in enrollment analysis
- **Verify coordinates** before mapping

### Performance Tips

- **Filter early** on year/region to reduce dataset size
- **Use indexes** on (rbd, agno), (mrun), (cod_reg)
- **Aggregate in database**, not in application layer
- **Chunk large exports** to avoid memory issues

---

## Data Completeness Matrix

| Dimension | mineduc_alumnos | mineduc_cargos | mineduc_establecimientos |
|-----------|-----------------|-----------------|--------------------------|
| Years | 2008-2025 (18) | 2003-2025 (23) | 2013-2025 (13) |
| Students | ✓ Full | - | ✓ Implicit |
| Teachers | - | ✓ Full | - |
| Schools | ✓ Via RBD | ✓ Via RBD | ✓ Full |
| Geography | ✓ Complete | ✓ Complete | ✓ Complete |
| Financials | - | ✓ Salaries | ✓ Fees |

---

## Example SQL Queries for Analysis

```sql
-- Student growth by year
SELECT agno, COUNT(*) as enrollments 
FROM alumnos 
GROUP BY agno 
ORDER BY agno;

-- Teacher-to-student ratio by region
SELECT a.cod_reg_rbd, a.agno, 
  COUNT(DISTINCT a.mrun) as students,
  COUNT(DISTINCT c.mrun) as teachers,
  COUNT(DISTINCT a.mrun) * 1.0 / NULLIF(COUNT(DISTINCT c.mrun), 0) as ratio
FROM alumnos a
LEFT JOIN cargos c ON a.rbd = c.rbd AND a.agno = c.agno
GROUP BY a.cod_reg_rbd, a.agno;

-- Schools by fee level and region
SELECT región, cod_depe,
  COUNT(*) as school_count,
  ROUND(AVG(pago_mensual), 0) as avg_monthly_fee,
  ROUND(AVG(mat_total), 0) as avg_enrollment
FROM establecimientos
WHERE agno = 2025
GROUP BY región, cod_depe;
```

---

**Last Updated:** 2026-06-09  
**Data Source:** MINEDUC (Chile Ministry of Education)  
**Note:** For large-scale analysis, use database directly. CSV analysis should be chunked.
