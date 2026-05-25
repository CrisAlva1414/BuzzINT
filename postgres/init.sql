-- Initialize PostgreSQL database for MINEDUC Intelligence

-- Create database (uncomment if running manually)
-- CREATE DATABASE mineduc_intelligence;

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Tables are managed by SQLAlchemy/Alembic
-- This script is for one-time database setup if needed

-- Create index on RBD for fast lookups
CREATE INDEX IF NOT EXISTS idx_establecimientos_rbd ON establecimientos(rbd);
CREATE INDEX IF NOT EXISTS idx_documentos_raw_fuente ON documentos_raw(fuente);
CREATE INDEX IF NOT EXISTS idx_datos_normalizados_rbd ON datos_normalizados(rbd);
