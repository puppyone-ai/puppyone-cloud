-- Additive: grant service_role column-level UPDATE on projects.agent_collab_mode.
-- public.projects uses explicit per-column UPDATE grants for service_role so that
-- lifecycle_status remains protected by the project initialization control plane
-- while backend services can update mutable project settings including
-- agent_collab_mode.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

GRANT UPDATE ("agent_collab_mode") ON TABLE public.projects TO service_role;

COMMIT;
