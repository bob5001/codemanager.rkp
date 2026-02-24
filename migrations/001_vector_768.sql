-- Migration 001: change embedding columns from vector(1536) to vector(768)
-- Reason: using nomic-embed-text via Ollama which produces 768-dim vectors.
--
-- Run with:
--   PGPASSWORD=rkp_password psql -h localhost -p 5433 -U rkp_user -d rkp_core \
--     -f migrations/001_vector_768.sql

BEGIN;

-- Drop ivfflat indexes before altering column type (can't alter type with active index)
DROP INDEX IF EXISTS codemanager.idx_cm_projects_embedding;
DROP INDEX IF EXISTS codemanager.idx_cm_snapshots_embedding;

-- Alter columns (NULL values survive; existing stub vectors are dropped)
ALTER TABLE codemanager.projects
    ALTER COLUMN embedding TYPE vector(768)
    USING NULL;

ALTER TABLE codemanager.snapshots
    ALTER COLUMN embedding TYPE vector(768)
    USING NULL;

-- Recreate ivfflat indexes at the new dimension
CREATE INDEX idx_cm_projects_embedding
    ON codemanager.projects
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX idx_cm_snapshots_embedding
    ON codemanager.snapshots
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

COMMIT;
