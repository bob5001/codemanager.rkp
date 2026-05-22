-- Migration 002: add status column to codemanager.agents
-- Supports pending/active registration approval flow.
-- Existing agents are set to 'active' so nothing breaks on upgrade.
--
-- Run with:
--   PGPASSWORD=$DB_PASSWORD psql -h localhost -p 5433 -U rkp_user -d rkp_core \
--     -f migrations/002_agent_status.sql

BEGIN;

ALTER TABLE codemanager.agents
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

CREATE INDEX IF NOT EXISTS idx_cm_agents_status
    ON codemanager.agents(status);

COMMIT;
