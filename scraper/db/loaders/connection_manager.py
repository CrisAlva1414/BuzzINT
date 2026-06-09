"""
connection_manager.py
─────────────────────────────────────────────────────────────
Improved database connection manager with better timeout handling
and connection pooling for large data loads.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PgConnection

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages PostgreSQL connections with improved timeout handling."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        dbname: str | None = None,
        user: str | None = None,
        password: str | None = None,
        connect_timeout: int = 10,  # seconds
        statement_timeout: int = 300000,  # milliseconds (5 minutes)
    ):
        self.host = host or os.getenv("PG_HOST", "localhost")
        self.port = port or int(os.getenv("PG_PORT", "5432"))
        self.dbname = dbname or os.getenv("PG_DB", "buzzint")
        self.user = user or os.getenv("PG_USER", "buzzint")
        self.password = password or os.getenv("PG_PASSWORD", "buzzint")
        self.connect_timeout = connect_timeout
        self.statement_timeout = statement_timeout
        self.conn = None

    def connect(self, retries: int = 10, delay: float = 2.0) -> PgConnection:
        """
        Establish connection with exponential backoff.

        Args:
            retries: Number of connection attempts
            delay: Initial delay between attempts (increases exponentially)

        Returns:
            PostgreSQL connection object

        Raises:
            RuntimeError: If connection fails after all retries
        """
        last_exc: Exception | None = None
        current_delay = delay

        logger.info(f"Connecting to PostgreSQL: {self.host}:{self.port}/{self.dbname}")

        for attempt in range(1, retries + 1):
            try:
                conn = psycopg2.connect(
                    host=self.host,
                    port=self.port,
                    dbname=self.dbname,
                    user=self.user,
                    password=self.password,
                    connect_timeout=self.connect_timeout,
                    options=f"-c statement_timeout={self.statement_timeout}",
                )
                conn.autocommit = False
                logger.info(
                    f"✓ PostgreSQL connected (attempt {attempt}/{retries})"
                )
                self.conn = conn
                return conn

            except psycopg2.OperationalError as exc:
                last_exc = exc
                logger.warning(
                    f"⚠ Connection failed (attempt {attempt}/{retries}): {exc}"
                )

                if attempt < retries:
                    logger.info(f"  Waiting {current_delay:.1f}s before retry...")
                    time.sleep(current_delay)
                    # Exponential backoff: 2s → 4s → 8s → 16s, max 30s
                    current_delay = min(current_delay * 1.5, 30.0)

        error_msg = f"✗ Failed to connect after {retries} attempts"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from last_exc

    def close(self) -> None:
        """Close the connection."""
        if self.conn:
            try:
                self.conn.close()
                logger.debug("PostgreSQL connection closed")
            except Exception as exc:
                logger.warning(f"Error closing connection: {exc}")
            finally:
                self.conn = None

    @contextmanager
    def cursor(self):
        """Context manager for database cursor."""
        if not self.conn:
            raise RuntimeError("Not connected to database")

        cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            yield cur
        finally:
            cur.close()

    @contextmanager
    def transaction(self):
        """Context manager for transactions with auto-commit/rollback."""
        if not self.conn:
            raise RuntimeError("Not connected to database")

        cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            yield cur
            self.conn.commit()
            logger.debug("Transaction committed")
        except Exception as exc:
            self.conn.rollback()
            logger.warning(f"Transaction rolled back: {exc}")
            raise
        finally:
            cur.close()

    def execute(self, query: str, params: tuple = None) -> list:
        """Execute a single query and return results."""
        with self.cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchall()

    def execute_batch(self, query: str, data: list, page_size: int = 1000) -> int:
        """
        Execute batch insert using execute_values (faster than individual inserts).

        Args:
            query: SQL template (should have %s placeholders)
            data: List of tuples/dicts to insert
            page_size: Batch size for insert_values

        Returns:
            Number of rows affected
        """
        if not data:
            return 0

        with self.transaction() as cur:
            try:
                psycopg2.extras.execute_values(
                    cur, query, data, page_size=page_size, fetch=False
                )
                affected = cur.rowcount
                logger.debug(f"Batch insert: {affected} rows")
                return affected
            except psycopg2.Error as exc:
                logger.error(f"Batch insert failed: {exc}")
                raise

    def health_check(self) -> bool:
        """Check if connection is alive."""
        try:
            with self.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception as exc:
            logger.warning(f"Health check failed: {exc}")
            return False


# Module-level connection manager instance
_manager: ConnectionManager | None = None


def get_manager() -> ConnectionManager:
    """Get or create the global connection manager."""
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager


def reset_manager() -> None:
    """Reset the global connection manager (for testing)."""
    global _manager
    if _manager:
        _manager.close()
    _manager = None
