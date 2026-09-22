SELECT plan(1);

BEGIN;

INSERT INTO auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
    created_at, updated_at
) VALUES (
    '00000000-0000-0000-0000-000000000000'::uuid,
    '00000000-0000-0000-0000-000000033001'::uuid,
    'authenticated', 'authenticated', 'billing-control-plane@example.test', '',
    now(), '{}'::jsonb, '{}'::jsonb, now(), now()
), (
    '00000000-0000-0000-0000-000000000000'::uuid,
    '00000000-0000-0000-0000-000000033002'::uuid,
    'authenticated', 'authenticated', 'billing-viewer@example.test', '',
    now(), '{}'::jsonb, '{}'::jsonb, now(), now()
), (
    '00000000-0000-0000-0000-000000000000'::uuid,
    '00000000-0000-0000-0000-000000033003'::uuid,
    'authenticated', 'authenticated', 'billing-second-viewer@example.test', '',
    now(), '{}'::jsonb, '{}'::jsonb, now(), now()
);

INSERT INTO public.organizations (
    id, name, slug, type, plan, seat_limit, created_by
) VALUES (
    'billing-control-plane-org', 'Billing Control Plane Test',
    'billing-control-plane-test', 'team', 'free', 1,
    '00000000-0000-0000-0000-000000033001'::uuid
);

INSERT INTO public.org_members (org_id, user_id, role) VALUES
    (
        'billing-control-plane-org',
        '00000000-0000-0000-0000-000000033001'::uuid,
        'owner'
    ),
    (
        'billing-control-plane-org',
        '00000000-0000-0000-0000-000000033002'::uuid,
        'viewer'
    ),
    (
        'billing-control-plane-org',
        '00000000-0000-0000-0000-000000033003'::uuid,
        'viewer'
    );

INSERT INTO public.projects (
    id, name, description, org_id, created_by, share_token,
    lifecycle_status
) VALUES (
    'billing-control-plane-project',
    'Billing Control Plane Test Project',
    'Capability-derived seat test',
    'billing-control-plane-org',
    '00000000-0000-0000-0000-000000033001'::uuid,
    'billing-control-plane-share-token',
    'ready'
);

INSERT INTO public.project_members (
    org_id, project_id, user_id, role, granted_by
) VALUES (
    'billing-control-plane-org',
    'billing-control-plane-project',
    '00000000-0000-0000-0000-000000033001'::uuid,
    'admin',
    '00000000-0000-0000-0000-000000033001'::uuid
);

INSERT INTO public.project_members (
    org_id, project_id, user_id, role, granted_by
) VALUES (
    'billing-control-plane-org',
    'billing-control-plane-project',
    '00000000-0000-0000-0000-000000033002'::uuid,
    'editor',
    '00000000-0000-0000-0000-000000033001'::uuid
);

DO $$
DECLARE
    first_publish jsonb;
    second_publish jsonb;
    replay jsonb;
    usage_result jsonb;
    first_claims integer;
    second_claims integer;
    provision_enqueued integer;
    first_provision_claims integer;
    second_provision_claims integer;
    first_seat_claims integer;
    second_seat_claims integer;
    seat_admission jsonb;
    publication record;
    original_root text;
    post_rollback_root text;
    refreshed_full_reconciled_at timestamptz;
BEGIN
    provision_enqueued := public.enqueue_missing_entitlement_provisioning(25);
    SELECT count(*) INTO first_provision_claims
    FROM public.claim_entitlement_provisioning_batch(25, 60) claimed
    WHERE claimed.org_id = 'billing-control-plane-org';
    SELECT count(*) INTO second_provision_claims
    FROM public.claim_entitlement_provisioning_batch(25, 60) claimed
    WHERE claimed.org_id = 'billing-control-plane-org';
    IF provision_enqueued < 1
       OR first_provision_claims <> 1
       OR second_provision_claims <> 0 THEN
        RAISE EXCEPTION 'entitlement provisioning lease failed: enqueued %, first %, second %',
            provision_enqueued, first_provision_claims, second_provision_claims;
    END IF;
    UPDATE public.organization_billing_operations
    SET status = 'confirmed', completed_at = now()
    WHERE org_id = 'billing-control-plane-org'
      AND kind = 'entitlement_provision';

    BEGIN
        PERFORM public.reserve_billable_member_activation(
            'billing-control-plane-org',
            '00000000-0000-0000-0000-000000033003'::uuid,
            '00000000-0000-0000-0000-000000033001'::uuid,
            NULL, 'member',
            'billing-control-plane-seat-without-snapshot'
        );
        RAISE EXCEPTION 'seat admission succeeded without a snapshot'
            USING ERRCODE = 'XX000';
    EXCEPTION WHEN SQLSTATE 'P0001' THEN
        NULL;
    END;

    first_publish := public.publish_organization_entitlement(
        'billing-control-plane-org', '1.0', 'plus', 'active', 'puppypay',
        '{"features":{},"limits":{"seats.purchased":2,"storage.max_bytes":100}}'::jsonb,
        2, '2026-07-14', 1, now(), NULL, now() + interval '30 days',
        repeat('a', 64), 'billing-control-plane-event-1', 'subscription.active'
    );
    IF first_publish->>'outcome' <> 'inserted' THEN
        RAISE EXCEPTION 'expected inserted entitlement, got %', first_publish;
    END IF;

    replay := public.publish_organization_entitlement(
        'billing-control-plane-org', '1.0', 'plus', 'active', 'puppypay',
        '{"features":{},"limits":{"seats.purchased":2,"storage.max_bytes":100}}'::jsonb,
        2, '2026-07-14', 1, now(), NULL, now() + interval '30 days',
        repeat('a', 64), 'billing-control-plane-event-1', 'subscription.active'
    );
    IF replay->>'outcome' <> 'idempotent' THEN
        RAISE EXCEPTION 'expected idempotent entitlement replay, got %', replay;
    END IF;

    second_publish := public.publish_organization_entitlement(
        'billing-control-plane-org', '1.0', 'plus', 'cancel_scheduled', 'puppypay',
        '{"features":{},"limits":{"seats.purchased":2,"storage.max_bytes":100}}'::jsonb,
        2, '2026-07-14', 2, now(), NULL, now() + interval '30 days',
        repeat('b', 64), 'billing-control-plane-event-2', 'subscription.canceled'
    );
    IF second_publish->>'outcome' <> 'updated' THEN
        RAISE EXCEPTION 'expected updated entitlement, got %', second_publish;
    END IF;

    BEGIN
        PERFORM public.publish_organization_entitlement(
            'billing-control-plane-org', '1.0', 'plus', 'active', 'puppypay',
            '{}'::jsonb, 2, '2026-07-14', 1, now(), NULL, NULL,
            repeat('a', 64), 'billing-control-plane-stale', 'stale'
        );
        RAISE EXCEPTION 'stale entitlement revision was accepted';
    EXCEPTION WHEN SQLSTATE '40001' THEN
        NULL;
    END;

    BEGIN
        PERFORM public.publish_organization_entitlement(
            'billing-control-plane-org', '1.0', 'plus', 'cancel_scheduled',
            'puppypay', '{}'::jsonb, 2, '2026-07-14', 2, now(), NULL, NULL,
            repeat('c', 64), 'billing-control-plane-conflict', 'conflict'
        );
        RAISE EXCEPTION 'same-revision entitlement hash conflict was accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;

    usage_result := public.reconcile_organization_usage_counter(
        'billing-control-plane-org', 'storage.logical_bytes', 0, 0,
        'billing-test:zero', 'storage_reconciler', '{}'::jsonb
    );
    IF (usage_result->>'threshold_percent')::integer <> 0 THEN
        RAISE EXCEPTION 'expected empty zero-quota storage threshold 0, got %', usage_result;
    END IF;
    BEGIN
        PERFORM public.reconcile_organization_usage_counter(
            'billing-control-plane-org', 'storage.logical_bytes', 1, 0,
            'billing-test:zero', 'storage_reconciler', '{}'::jsonb
        );
        RAISE EXCEPTION 'mutated usage idempotency replay was accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;

    usage_result := public.reconcile_organization_usage_counter(
        'billing-control-plane-org', 'storage.logical_bytes', 80, 100,
        'billing-test:initial', 'storage_reconciler', '{}'::jsonb
    );
    IF (usage_result->>'threshold_percent')::integer <> 80 THEN
        RAISE EXCEPTION 'expected storage threshold 80, got %', usage_result;
    END IF;

    UPDATE public.organization_usage_counters
    SET full_reconciled_at = now() - interval '2 days',
        reconciliation_claimed_at = now()
    WHERE org_id = 'billing-control-plane-org'
      AND metric = 'storage.logical_bytes';
    usage_result := public.reconcile_organization_usage_counter(
        'billing-control-plane-org', 'storage.logical_bytes', 80, 100,
        'billing-test:initial', 'storage_reconciler', '{}'::jsonb
    );
    SELECT full_reconciled_at INTO refreshed_full_reconciled_at
    FROM public.organization_usage_counters
    WHERE org_id = 'billing-control-plane-org'
      AND metric = 'storage.logical_bytes';
    IF usage_result->>'outcome' <> 'idempotent'
       OR refreshed_full_reconciled_at < now() - interval '1 minute'
       OR EXISTS (
           SELECT 1 FROM public.organization_usage_counters
           WHERE org_id = 'billing-control-plane-org'
             AND metric = 'storage.logical_bytes'
             AND reconciliation_claimed_at IS NOT NULL
       ) THEN
        RAISE EXCEPTION 'idempotent full reconciliation did not refresh its lease';
    END IF;

    UPDATE public.organization_usage_counters
    SET full_reconciled_at = now() - interval '2 days',
        reconciliation_claimed_at = NULL
    WHERE org_id = 'billing-control-plane-org'
      AND metric = 'storage.logical_bytes';

    SELECT count(*) INTO first_claims
    FROM public.claim_storage_reconciliation_batch(25, 86400, 900) claimed
    WHERE claimed.org_id = 'billing-control-plane-org';
    SELECT count(*) INTO second_claims
    FROM public.claim_storage_reconciliation_batch(25, 86400, 900) claimed
    WHERE claimed.org_id = 'billing-control-plane-org';
    IF first_claims <> 1 OR second_claims <> 0 THEN
        RAISE EXCEPTION 'storage reconciliation lease failed: first %, second %',
            first_claims, second_claims;
    END IF;

    SELECT COALESCE(mut_root_hash, '') INTO original_root
    FROM public.projects
    WHERE id = 'billing-control-plane-project';
    SELECT * INTO publication
    FROM public.publish_version_project_update_with_usage(
        'billing-control-plane-project', original_root, repeat('1', 40),
        repeat('a', 40), 'billing-control-plane-test', 'storage accounting',
        'project_version_committed', '[]'::jsonb, NULL, now()::text,
        'user:00000000-0000-0000-0000-000000033001', '{}'::jsonb,
        'test', '', '', '', '', 'operation', '', '', '', NULL,
        'billing-control-plane-org', 80, 10, 1, true, 2
    );
    IF NOT publication.published OR publication.txn_id IS NULL THEN
        RAISE EXCEPTION 'atomic storage publication failed';
    END IF;
    IF (SELECT value FROM public.organization_usage_counters
        WHERE org_id = 'billing-control-plane-org'
          AND metric = 'storage.logical_bytes') <> 90 THEN
        RAISE EXCEPTION 'atomic storage publication did not update usage';
    END IF;

    -- A stale retry must return the canonical CAS miss before applying its
    -- old delta to the already-updated counter and producing a false denial.
    SELECT * INTO publication
    FROM public.publish_version_project_update_with_usage(
        'billing-control-plane-project', original_root, repeat('2', 40),
        repeat('b', 40), 'billing-control-plane-test', 'stale retry',
        'project_version_committed', '[]'::jsonb, NULL, now()::text,
        'user:00000000-0000-0000-0000-000000033001', '{}'::jsonb,
        'test', '', '', '', '', 'operation', '', '', '', NULL,
        'billing-control-plane-org', 80, 1000, 1000, true, 2
    );
    IF publication.published THEN
        RAISE EXCEPTION 'stale storage publication unexpectedly succeeded';
    END IF;

    -- Quota denial happens after the project CAS inside the same SQL
    -- transaction, so the exception must roll the canonical publication back.
    BEGIN
        PERFORM public.publish_version_project_update_with_usage(
            'billing-control-plane-project', repeat('1', 40), repeat('3', 40),
            repeat('c', 40), 'billing-control-plane-test', 'over quota',
            'project_version_committed', '[]'::jsonb, NULL, now()::text,
            'user:00000000-0000-0000-0000-000000033001', '{}'::jsonb,
            'test', '', '', '', '', 'operation', '', '', '', NULL,
            'billing-control-plane-org', 90, 11, 1000, true, 2
        );
        RAISE EXCEPTION 'over-quota storage publication was accepted';
    EXCEPTION WHEN SQLSTATE 'P0001' THEN
        NULL;
    END;
    SELECT COALESCE(mut_root_hash, '') INTO post_rollback_root
    FROM public.projects
    WHERE id = 'billing-control-plane-project';
    IF post_rollback_root <> repeat('1', 40) THEN
        RAISE EXCEPTION 'quota denial did not roll back project publication';
    END IF;

    IF has_function_privilege(
        'authenticated',
        'public.publish_organization_entitlement(text,text,text,text,text,jsonb,integer,text,bigint,timestamptz,timestamptz,timestamptz,text,text,text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'entitlement publication function is exposed to authenticated';
    END IF;
    IF has_function_privilege(
        'authenticated',
        'public.enqueue_missing_entitlement_provisioning(integer)',
        'EXECUTE'
    ) OR has_function_privilege(
        'authenticated',
        'public.claim_entitlement_provisioning_batch(integer,integer)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'entitlement provisioning functions are exposed to authenticated';
    END IF;

    IF NOT public.is_billable_organization_member(
        'billing-control-plane-org',
        '00000000-0000-0000-0000-000000033002'::uuid
    ) THEN
        RAISE EXCEPTION 'viewer with explicit Project Editor capability was not billable';
    END IF;
    IF public.count_billable_organization_members('billing-control-plane-org') <> 2 THEN
        RAISE EXCEPTION 'capability-derived billable seat count did not include both users';
    END IF;
    seat_admission := public.reserve_billable_member_activation(
        'billing-control-plane-org',
        '00000000-0000-0000-0000-000000033003'::uuid,
        '00000000-0000-0000-0000-000000033001'::uuid,
        NULL,
        'member',
        'billing-control-plane-seat-admission-1'
    );
    IF seat_admission->>'status' <> 'awaiting_confirmation'
       OR (seat_admission->>'target_seat_quantity')::integer <> 3 THEN
        RAISE EXCEPTION 'seat admission did not fail closed at paid capacity: %',
            seat_admission;
    END IF;
    SELECT count(*) INTO first_seat_claims
    FROM public.claim_seat_proposal_batch(25, 60) claimed
    WHERE claimed.org_id = 'billing-control-plane-org';
    SELECT count(*) INTO second_seat_claims
    FROM public.claim_seat_proposal_batch(25, 60) claimed
    WHERE claimed.org_id = 'billing-control-plane-org';
    IF first_seat_claims <> 1 OR second_seat_claims <> 0 THEN
        RAISE EXCEPTION 'seat proposal lease failed: first %, second %',
            first_seat_claims, second_seat_claims;
    END IF;
    IF has_function_privilege(
        'authenticated',
        'public.count_billable_organization_members(text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'billable seat count function is exposed to authenticated';
    END IF;
    IF has_function_privilege(
        'authenticated',
        'public.reserve_billable_member_activation(text,uuid,uuid,text,text,text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'seat admission function is exposed to authenticated';
    END IF;
    IF has_function_privilege(
        'authenticated',
        'public.claim_seat_proposal_batch(integer,integer)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'seat proposal claim function is exposed to authenticated';
    END IF;
END;
$$;

ROLLBACK;

SELECT pass('unified billing entitlement, storage, lease and privilege contracts passed');
SELECT * FROM finish();
