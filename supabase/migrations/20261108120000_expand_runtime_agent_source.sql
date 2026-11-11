-- Agent work in a hosted workspace is metered under its own runtime kind, so
-- the billing authority can charge an agent's time while a person's time in
-- their workspace stays a platform cost. Additive: every existing kind stays valid.
BEGIN;

ALTER TABLE public.runtime_billing_runs DROP CONSTRAINT runtime_billing_runs_kind_check;
ALTER TABLE public.runtime_billing_runs ADD CONSTRAINT runtime_billing_runs_kind_check
    CHECK (runtime_kind = ANY (ARRAY['agent'::text, 'automation'::text, 'sandbox'::text,
                                     'workspace'::text, 'connector'::text]));

COMMIT;
