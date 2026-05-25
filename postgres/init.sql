-- Initialize PostgreSQL database for MINEDUC Intelligence
-- Only extensions are created here; tables are managed by SQLAlchemy/Alembic

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
