-- pgvector is not enabled in the Postgres.app source dump.
-- This runs only during first initialization of a fresh Docker volume.
CREATE EXTENSION IF NOT EXISTS vector;
