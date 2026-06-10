"""
loaders/db.py
─────────────────────────────────────────────────────────────
Conexión, helpers de upsert y registro ETL compartidos por
todos los loaders de BuzzINT.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Generator

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values
from psycopg2.extensions import connection as PgConnection

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# DSN desde variables de entorno (valores defecto = docker-compose)
# ──────────────────────────────────────────────────────────────
def _conn_kwargs() -> dict:
    return dict(
        host=os.getenv("PG_HOST", "localhost"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname=os.getenv("PG_DB", "buzzint"),
        user=os.getenv("PG_USER", "buzzint"),
        password=os.getenv("PG_PASSWORD", "buzzint"),
        options="-c search_path=gold,public",
    )


def get_conn(retries: int = 5, delay: float = 3.0) -> PgConnection:
    """Obtiene conexión con reintentos (útil al arrancar junto a Docker)."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(**_conn_kwargs())
            conn.autocommit = False
            logger.debug("Conexión PostgreSQL establecida (intento %d)", attempt)
            return conn
        except psycopg2.OperationalError as exc:
            last_exc = exc
            logger.warning("DB no disponible (intento %d/%d): %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(delay)
    raise RuntimeError(f"No se pudo conectar a PostgreSQL tras {retries} intentos") from last_exc

@contextmanager
def transaction(conn: PgConnection) -> Generator[psycopg2.extras.DictCursor, None, None]:
    """Context manager: commit en éxito, rollback en excepción."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# ──────────────────────────────────────────────────────────────
# ETL Control
# ──────────────────────────────────────────────────────────────
class EtlRun:
    """Registra el ciclo de vida de una carga en etl_control."""

    def __init__(self, conn: PgConnection, loader: str, source_file: str) -> None:
        self.conn = conn
        self.loader = loader
        self.source_file = source_file
        self.run_id: int | None = None
        self.inserted = 0
        self.updated = 0
        self.skipped = 0
        self.read = 0

    def start(self) -> None:
        with transaction(self.conn) as cur:
            cur.execute(
                """
                INSERT INTO gold.etl_control (loader, source_file, status)
                VALUES (%s, %s, 'running')
                RETURNING run_id
                """,
                (self.loader, self.source_file),
            )
            self.run_id = cur.fetchone()[0]
        logger.info("[ETL] run_id=%s  %s → %s", self.run_id, self.loader, self.source_file)

    def finish(self) -> None:
        with transaction(self.conn) as cur:
            cur.execute(
                """
                UPDATE gold.etl_control
                SET status='ok', rows_read=%s, rows_inserted=%s,
                    rows_updated=%s, rows_skipped=%s, finished_at=now()
                WHERE run_id=%s
                """,
                (self.read, self.inserted, self.updated, self.skipped, self.run_id),
            )
        logger.info(
            "[ETL] run_id=%s finalizado — leídas=%d ins=%d upd=%d skip=%d",
            self.run_id, self.read, self.inserted, self.updated, self.skipped,
        )

    def fail(self, error: str) -> None:
        try:
            self.conn.rollback()
            with transaction(self.conn) as cur:
                cur.execute(
                    """
                    UPDATE gold.etl_control
                    SET status='error', error_msg=%s, finished_at=now()
                    WHERE run_id=%s
                    """,
                    (error[:2000], self.run_id),
                )
        except Exception as exc:
            logger.error("No se pudo registrar fallo en etl_control: %s", exc)


# ──────────────────────────────────────────────────────────────
# Helpers de lookup / upsert dimensional
# ──────────────────────────────────────────────────────────────
def upsert_territorio(cur, row: dict) -> int | None:
    """
    Inserta o actualiza dim_territorio por cod_com.
    Retorna territorio_id.
    """
    cod_com = _str(row.get("cod_com_rbd") or row.get("cod_com"))
    if not cod_com:
        return None

    cur.execute(
        """
        INSERT INTO gold.dim_territorio
            (cod_com, nom_com, cod_pro, cod_deprov, nom_deprov, cod_reg, nom_reg)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (cod_com) DO UPDATE SET
            nom_com   = EXCLUDED.nom_com,
            cod_pro   = EXCLUDED.cod_pro,
            cod_deprov = EXCLUDED.cod_deprov,
            nom_deprov = EXCLUDED.nom_deprov,
            cod_reg   = EXCLUDED.cod_reg,
            nom_reg   = EXCLUDED.nom_reg
        RETURNING territorio_id
        """,
        (
            cod_com.zfill(5),
            _str(row.get("nom_com_rbd") or row.get("nom_com")),
            _str(row.get("cod_pro_rbd") or row.get("cod_pro")),
            _str(row.get("cod_deprov_rbd") or row.get("cod_deprov")),
            _str(row.get("nom_deprov_rbd") or row.get("nom_deprov")),
            _str(row.get("cod_reg_rbd") or row.get("cod_reg")),
            _str(row.get("nom_reg_rbd_a") or row.get("nom_reg")),
        ),
    )
    return cur.fetchone()[0]


def upsert_establecimiento(cur, row: dict, territorio_id: int | None) -> int | None:
    """
    Inserta o actualiza dim_establecimiento (SCD Type-1).
    Retorna establecimiento_id.
    """
    rbd = _rbd(row.get("rbd"))
    if not rbd:
        return None

    cur.execute(
        """
        INSERT INTO gold.dim_establecimiento
            (rbd, dgv_rbd, nom_rbd, cod_depe, cod_depe2, rural_rbd,
             estado_estab, nombre_slep, convenio_pie, pace,
             ori_religiosa, territorio_id, _actualizado_en)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        ON CONFLICT (rbd) DO UPDATE SET
            dgv_rbd      = EXCLUDED.dgv_rbd,
            nom_rbd      = EXCLUDED.nom_rbd,
            cod_depe     = EXCLUDED.cod_depe,
            cod_depe2    = EXCLUDED.cod_depe2,
            rural_rbd    = EXCLUDED.rural_rbd,
            estado_estab = EXCLUDED.estado_estab,
            nombre_slep  = EXCLUDED.nombre_slep,
            convenio_pie = EXCLUDED.convenio_pie,
            pace         = EXCLUDED.pace,
            ori_religiosa = EXCLUDED.ori_religiosa,
            territorio_id = EXCLUDED.territorio_id,
            _actualizado_en = now()
        RETURNING establecimiento_id
        """,
        (
            rbd,
            _str(row.get("dgv_rbd")),
            _str(row.get("nom_rbd")),
            _int(row.get("cod_depe")),
            _int(row.get("cod_depe2")),
            _int(row.get("rural_rbd")),
            _int(row.get("estado_estab")),
            _str(row.get("nombre_slep")),
            _int(row.get("convenio_pie")),
            _int(row.get("pace")),
            _int(row.get("ori_religiosa")),
            territorio_id,
        ),
    )
    return cur.fetchone()[0]


def get_or_create_alumno(cur, row: dict) -> int | None:
    """Upsert dim_alumno por mrun. Retorna alumno_id."""
    mrun = _int(row.get("mrun"))
    if not mrun:
        return None

    cur.execute(
        """
        INSERT INTO gold.dim_alumno (mrun, gen_alu, fec_nac)
        VALUES (%s, %s, %s)
        ON CONFLICT (mrun) DO UPDATE SET
            gen_alu = COALESCE(EXCLUDED.gen_alu, dim_alumno.gen_alu),
            fec_nac = COALESCE(EXCLUDED.fec_nac, dim_alumno.fec_nac),
            _actualizado_en = now()
        RETURNING alumno_id
        """,
        (mrun, _int(row.get("gen_alu")), _date(row.get("fec_nac_alu"))),
    )
    return cur.fetchone()[0]


def get_or_create_docente(cur, row: dict) -> int | None:
    """Upsert dim_docente por mrun. Retorna docente_id."""
    mrun = _int(row.get("mrun"))
    if not mrun:
        return None

    cur.execute(
        """
        INSERT INTO gold.dim_docente (mrun, doc_genero, fec_nac)
        VALUES (%s, %s, %s)
        ON CONFLICT (mrun) DO UPDATE SET
            doc_genero = COALESCE(EXCLUDED.doc_genero, dim_docente.doc_genero),
            fec_nac    = COALESCE(EXCLUDED.fec_nac,    dim_docente.fec_nac),
            _actualizado_en = now()
        RETURNING docente_id
        """,
        (mrun, _int(row.get("doc_genero")), _date(row.get("doc_fec_nac"))),
    )
    return cur.fetchone()[0]


def get_or_create_asignatura(cur, cod_ense: str | None, subsector: str | None) -> int | None:
    """Lookup dim_asignatura por (cod_ense, subsector). Inserta si no existe."""
    if not cod_ense:
        return None

    cod_ense = cod_ense.strip().zfill(2)
    sub = (subsector or "").strip() or None

    cur.execute(
        "SELECT asignatura_id FROM gold.dim_asignatura WHERE cod_ense=%s AND COALESCE(subsector,'')=COALESCE(%s,'')",
        (cod_ense, sub),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO gold.dim_asignatura (cod_ense, subsector)
        VALUES (%s, %s)
        RETURNING asignatura_id
        """,
        (cod_ense, sub),
    )
    return cur.fetchone()[0]


def get_tiempo_id(cur, agno: int, cod_grado: int) -> int | None:
    """Lookup dim_tiempo_escolar por (agno, cod_grado)."""
    cur.execute(
        "SELECT tiempo_id FROM gold.dim_tiempo_escolar WHERE agno=%s AND cod_grado=%s",
        (agno, cod_grado),
    )
    r = cur.fetchone()
    return r[0] if r else None


def get_establecimiento_id(cur, rbd: str) -> int | None:
    """Lookup dim_establecimiento por rbd."""
    rbd_clean = _rbd(rbd)
    if not rbd_clean:
        return None
    cur.execute(
        "SELECT establecimiento_id FROM gold.dim_establecimiento WHERE rbd=%s",
        (rbd_clean,),
    )
    r = cur.fetchone()
    return r[0] if r else None


# ──────────────────────────────────────────────────────────────
# Coerciones de tipo
# ──────────────────────────────────────────────────────────────
def _str(v: Any) -> str | None:
    if v is None or str(v).strip() in ("", "nan", "None", "NaN"):
        return None
    return str(v).strip()


def _int(v: Any) -> int | None:
    s = _str(v)
    if s is None:
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _float(v: Any) -> float | None:
    s = _str(v)
    if s is None:
        return None
    # Normalizar separador decimal: coma → punto
    s = s.replace(",", ".")
    # Eliminar puntos de miles si hay más de uno (ej: "1.234.567" → "1234567")
    if s.count(".") > 1:
        parts = s.rsplit(".", 1)
        s = parts[0].replace(".", "") + "." + parts[1]
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _date(v: Any) -> str | None:
    """Acepta YYYY-MM-DD o YYYYMMDD o YYYYMM → retorna YYYY-MM-DD o None."""
    s = _str(v)
    if s is None:
        return None
    # Ya viene normalizado por el normalizer — YYYY-MM-DD
    if len(s) == 10 and s[4] == "-":
        return s
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}-{s[4:]}-01"
    return None


def _rbd(v: Any) -> str | None:
    """Normaliza RBD a CHAR(8) zero-padded."""
    s = _str(v)
    if s is None:
        return None
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return None
    return digits.zfill(8)


# ──────────────────────────────────────────────────────────────
# Configuración de loaders
# ──────────────────────────────────────────────────────────────
_ENV = os.getenv("BUZZINT_ENV", "dev").lower()
CHUNK_SIZE = 50_000 if _ENV == "dev" else 500
POOL_SIZE = 10 if _ENV == "dev" else 2
USE_COPY = _ENV == "dev"


def batch_upsert(cur, sql: str, rows: list) -> int:
    """Wrapper sobre execute_values. Retorna len(rows) o 0 si vacío."""
    if not rows:
        return 0
    execute_values(cur, sql, rows)
    return len(rows)
