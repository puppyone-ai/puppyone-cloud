-- Additive: a stale entitlement revision is refused with HTTP 409 instead of
-- SQLSTATE 40001. PostgREST 14 treats 40001 as a transient serialization
-- failure and retries the transaction; a stale revision is stale on every
-- retry, so the request never returned and the database kept re-running it.
-- Cloud provisioning can send one: PuppyPay answers a retried provisioning
-- with its first snapshot for a day, while a purchase may have published a
-- newer revision meanwhile. Only the error code changes; the signature,
-- owner and grants stay as they are.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='2min';

CREATE OR REPLACE FUNCTION "public"."publish_organization_entitlement"("p_org_id" "text", "p_schema_version" "text", "p_plan_id" "text", "p_status" "text", "p_source" "text", "p_entitlements" "jsonb", "p_seat_quantity" integer, "p_catalog_version" "text", "p_source_revision" bigint, "p_effective_at" timestamp with time zone, "p_effective_until" timestamp with time zone, "p_current_period_end" timestamp with time zone, "p_payload_hash" "text", "p_source_event_id" "text" DEFAULT NULL::"text", "p_event_type" "text" DEFAULT 'entitlement.published'::"text") RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE
    current_row public.organization_entitlements%ROWTYPE;
    stored_row public.organization_entitlements%ROWTYPE;
    outcome text;
BEGIN
    IF NULLIF(btrim(p_org_id), '') IS NULL
       OR NULLIF(btrim(p_plan_id), '') IS NULL
       OR NULLIF(btrim(p_status), '') IS NULL THEN
        RAISE EXCEPTION 'organization, plan, and status are required'
            USING ERRCODE = '22023';
    END IF;
    IF p_source IS DISTINCT FROM 'puppypay' THEN
        RAISE EXCEPTION 'entitlement source must be puppypay'
            USING ERRCODE = '22023';
    END IF;
    IF NULLIF(btrim(p_catalog_version), '') IS NULL
       OR p_catalog_version = 'legacy'
       OR p_effective_at IS NULL THEN
        RAISE EXCEPTION 'catalog_version and effective_at are required'
            USING ERRCODE = '22023';
    END IF;
    IF p_source_revision <= 0 THEN
        RAISE EXCEPTION 'source_revision must be positive'
            USING ERRCODE = '22023';
    END IF;
    IF p_seat_quantity < 0 THEN
        RAISE EXCEPTION 'seat_quantity must be nonnegative'
            USING ERRCODE = '22023';
    END IF;
    IF p_schema_version IS NULL OR p_schema_version !~ '^1([.][0-9]+)?$' THEN
        RAISE EXCEPTION 'unsupported entitlement schema_version'
            USING ERRCODE = '22023';
    END IF;
    IF p_payload_hash IS NULL OR p_payload_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'payload_hash must be a lowercase sha256 hex digest'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_typeof(COALESCE(p_entitlements, '{}'::jsonb)) <> 'object' THEN
        RAISE EXCEPTION 'entitlements must be a JSON object'
            USING ERRCODE = '22023';
    END IF;

    -- FOR UPDATE cannot lock an absent row. A transaction-scoped advisory
    -- lock serializes the first publication for an organization as well, so a
    -- concurrent exact replay is acknowledged rather than surfacing a false
    -- revision race.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_org_id, 0));

    SELECT * INTO current_row
    FROM public.organization_entitlements
    WHERE org_id = p_org_id
    FOR UPDATE;

    IF FOUND AND p_source_revision < current_row.source_revision THEN
        -- Not 40001: PostgREST retries a serialization failure, and this one
        -- recurs on every retry, so the request never ends.
        RAISE EXCEPTION 'stale entitlement revision: incoming %, stored %',
            p_source_revision, current_row.source_revision
            USING ERRCODE = 'PT409';
    END IF;

    IF FOUND AND p_source_revision = current_row.source_revision THEN
        IF p_payload_hash <> current_row.payload_hash THEN
            RAISE EXCEPTION 'entitlement revision conflict for org % revision %',
                p_org_id, p_source_revision
                USING ERRCODE = '23505';
        END IF;
        RETURN jsonb_build_object(
            'outcome', 'idempotent',
            'source_revision', current_row.source_revision,
            'payload_hash', current_row.payload_hash,
            'snapshot', to_jsonb(current_row)
        );
    END IF;

    INSERT INTO public.organization_entitlements (
        org_id,
        schema_version,
        plan_id,
        status,
        source,
        entitlements,
        seat_quantity,
        catalog_version,
        source_revision,
        effective_at,
        effective_until,
        current_period_end,
        payload_hash
    )
    VALUES (
        p_org_id,
        p_schema_version,
        p_plan_id,
        p_status,
        p_source,
        COALESCE(p_entitlements, '{}'::jsonb),
        p_seat_quantity,
        p_catalog_version,
        p_source_revision,
        p_effective_at,
        p_effective_until,
        p_current_period_end,
        p_payload_hash
    )
    ON CONFLICT (org_id) DO UPDATE SET
        schema_version = EXCLUDED.schema_version,
        plan_id = EXCLUDED.plan_id,
        status = EXCLUDED.status,
        source = EXCLUDED.source,
        entitlements = EXCLUDED.entitlements,
        seat_quantity = EXCLUDED.seat_quantity,
        catalog_version = EXCLUDED.catalog_version,
        source_revision = EXCLUDED.source_revision,
        effective_at = EXCLUDED.effective_at,
        effective_until = EXCLUDED.effective_until,
        current_period_end = EXCLUDED.current_period_end,
        payload_hash = EXCLUDED.payload_hash
    WHERE public.organization_entitlements.source_revision < EXCLUDED.source_revision
    RETURNING * INTO stored_row;

    IF stored_row.org_id IS NULL THEN
        RAISE EXCEPTION 'entitlement publication lost a concurrent revision race'
            USING ERRCODE = '40001';
    END IF;

    outcome := CASE WHEN current_row.org_id IS NULL THEN 'inserted' ELSE 'updated' END;

    INSERT INTO public.organization_entitlement_events (
        org_id,
        source,
        source_event_id,
        event_type,
        old_plan_id,
        new_plan_id,
        old_entitlements,
        new_entitlements,
        schema_version,
        catalog_version,
        source_revision,
        seat_quantity,
        payload_hash,
        publication_outcome
    )
    VALUES (
        p_org_id,
        p_source,
        p_source_event_id,
        p_event_type,
        current_row.plan_id,
        stored_row.plan_id,
        current_row.entitlements,
        stored_row.entitlements,
        p_schema_version,
        p_catalog_version,
        p_source_revision,
        p_seat_quantity,
        p_payload_hash,
        outcome
    )
    ON CONFLICT (source_event_id) WHERE source_event_id IS NOT NULL DO NOTHING;

    RETURN jsonb_build_object(
        'outcome', outcome,
        'source_revision', stored_row.source_revision,
        'payload_hash', stored_row.payload_hash,
        'snapshot', to_jsonb(stored_row)
    );
END;
$_$;

COMMIT;
