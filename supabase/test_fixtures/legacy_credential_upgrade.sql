-- Minimal relational fixture for the immutable July credential migrations.
-- Synthetic values only. This is NOT a copy of production or a full schema.
CREATE TABLE public.projects (id text PRIMARY KEY, org_id text);
CREATE TABLE public.repo_scopes (
    id text PRIMARY KEY,
    project_id text NOT NULL REFERENCES public.projects(id),
    path text NOT NULL,
    mode text NOT NULL,
    access_key text UNIQUE NOT NULL,
    access_key_revoked_at timestamptz
);
CREATE TABLE public.access_surfaces (
    id text PRIMARY KEY,
    org_id text,
    project_id text NOT NULL REFERENCES public.projects(id),
    scope_id text,
    kind text NOT NULL,
    name text,
    status text NOT NULL DEFAULT 'active',
    principal_type text,
    principal_id text,
    config jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE public.access_surface_credentials (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    org_id text,
    project_id text NOT NULL REFERENCES public.projects(id),
    access_surface_id text NOT NULL REFERENCES public.access_surfaces(id),
    credential_type text NOT NULL,
    key_prefix text NOT NULL,
    key_last4 text NOT NULL,
    key_hash text NOT NULL,
    hash_alg text NOT NULL,
    status text NOT NULL
);
CREATE UNIQUE INDEX fixture_active_credential_hash
    ON public.access_surface_credentials(key_hash) WHERE status = 'active';

INSERT INTO public.projects VALUES ('fixture-project', 'fixture-org');
INSERT INTO public.repo_scopes (id, project_id, path, mode, access_key)
SELECT 'scope-' || lpad(n::text, 3, '0'), 'fixture-project',
       'folder-' || n, 'rw', 'cli_synthetic_release_fixture_' || n
FROM generate_series(1, 152) n;

-- Exercise both existing CLI surfaces and surfaces created by the migration.
INSERT INTO public.access_surfaces (
    id, org_id, project_id, scope_id, kind, name, principal_type, principal_id, config
)
SELECT 'surface-' || s.id, 'fixture-org', s.project_id, s.id, 'cli',
       'Fixture CLI', 'scope', s.id, jsonb_build_object('access_key', s.access_key)
FROM public.repo_scopes s ORDER BY s.id LIMIT 76;

INSERT INTO public.access_surface_credentials (
    id, org_id, project_id, access_surface_id, credential_type,
    key_prefix, key_last4, key_hash, hash_alg, status
)
SELECT 'existing-' || n, 'fixture-org', 'fixture-project',
       'surface-scope-00' || n, 'bearer_token', 'existing', 'test',
       repeat(n::text, 64), 'hmac_sha256_v1', 'active'
FROM generate_series(1, 2) n;
