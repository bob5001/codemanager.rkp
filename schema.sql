-- codemanager schema
-- All tables live in the "codemanager" schema — zero collision with existing public tables.
-- Prerequisites (already installed in public schema):
--   uuid-ossp 1.1  → uuid_generate_v4()
--   pgvector 0.8.1 → vector type

CREATE SCHEMA IF NOT EXISTS codemanager;

-- ────────────────────────────────────────────────────────────
-- agents
-- One row per registered AI agent / client.
-- ────────────────────────────────────────────────────────────
CREATE TABLE codemanager.agents (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name          TEXT NOT NULL,
    ecosystem     TEXT NOT NULL,           -- e.g. "anthropic", "autogen", "ollama", "cursor"
    api_key_hash  TEXT NOT NULL UNIQUE,    -- SHA-256 of the plaintext key
    capabilities  JSONB DEFAULT '[]'::jsonb,
    registered_at TIMESTAMPTZ DEFAULT now(),
    last_seen     TIMESTAMPTZ
);

-- ────────────────────────────────────────────────────────────
-- projects
-- One row per tracked codebase.
-- ────────────────────────────────────────────────────────────
CREATE TABLE codemanager.projects (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name          TEXT NOT NULL,
    path          TEXT,                    -- local filesystem path
    github_url    TEXT,                    -- remote GitHub URL
    description   TEXT,
    summary       TEXT,                    -- latest generated summary
    embedding     vector(1536),            -- pgvector embedding of summary
    status        TEXT NOT NULL DEFAULT 'registered',
    -- status values:
    --   registered | analyzing | partial | analyzed |
    --   in_development | alpha | testing | production |
    --   stuck | deprecated | archived
    status_note   TEXT,                    -- free-form: "stuck at auth middleware"
    last_analyzed TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- ────────────────────────────────────────────────────────────
-- snapshots
-- Point-in-time captures of a project's state.
-- ────────────────────────────────────────────────────────────
CREATE TABLE codemanager.snapshots (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id    UUID NOT NULL REFERENCES codemanager.projects(id) ON DELETE CASCADE,
    timestamp     TIMESTAMPTZ DEFAULT now(),
    file_tree     JSONB,
    key_findings  JSONB,
    embedding     vector(1536)
);

-- ────────────────────────────────────────────────────────────
-- agent_visits
-- Audit log of every agent interaction with a project.
-- ────────────────────────────────────────────────────────────
CREATE TABLE codemanager.agent_visits (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id    UUID NOT NULL REFERENCES codemanager.projects(id) ON DELETE CASCADE,
    agent_id      UUID NOT NULL REFERENCES codemanager.agents(id) ON DELETE CASCADE,
    query         TEXT,
    summary       TEXT,
    usefulness    SMALLINT,               -- 0=harmful 1=irrelevant 2=partial 3=useful 4=definitive
    confidence    FLOAT,                  -- agent self-reported 0.0-1.0
    model_used    TEXT,                   -- e.g. "claude-sonnet-4-6", "qwen2.5-coder"
    timestamp     TIMESTAMPTZ DEFAULT now()
);

-- ────────────────────────────────────────────────────────────
-- Indexes
-- Note: ivfflat indexes on empty tables are valid in pgvector 0.8.x.
-- In production they should be rebuilt (REINDEX) once sufficient
-- rows exist for the IVF lists to be meaningful.
-- ────────────────────────────────────────────────────────────
CREATE INDEX idx_cm_projects_status
    ON codemanager.projects(status);

CREATE INDEX idx_cm_projects_embedding
    ON codemanager.projects
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX idx_cm_snapshots_project_id
    ON codemanager.snapshots(project_id);

CREATE INDEX idx_cm_snapshots_embedding
    ON codemanager.snapshots
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX idx_cm_visits_project_id
    ON codemanager.agent_visits(project_id);

CREATE INDEX idx_cm_visits_agent_id
    ON codemanager.agent_visits(agent_id);

CREATE INDEX idx_cm_visits_timestamp
    ON codemanager.agent_visits(timestamp);
