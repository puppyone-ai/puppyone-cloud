-- Organization admission is one transaction. A stale application read must
-- never revive a revoked invitation, exceed the local seat limit, or replace
-- an existing member's role. This additive RPC is backend-only.
BEGIN;

CREATE FUNCTION public.accept_organization_invitation(
    p_token text, p_user_id uuid, p_user_email text,
    p_enforce_local_seat_limit boolean DEFAULT false
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    invitation public.org_invitations%ROWTYPE;
    member public.org_members%ROWTYPE;
    org public.organizations%ROWTYPE;
    invitation_org text;
BEGIN
    SELECT org_id INTO invitation_org FROM public.org_invitations WHERE token = p_token;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'unavailable'); END IF;

    -- Use the existing commercial admission and owner-transfer lock namespaces.
    PERFORM pg_advisory_xact_lock(hashtextextended(invitation_org || ':billable-seats', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(invitation_org || ':owner', 0));
    -- Organization deletion also locks the Organization before its members.
    SELECT * INTO org FROM public.organizations WHERE id = invitation_org FOR SHARE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'unavailable'); END IF;
    SELECT * INTO invitation FROM public.org_invitations WHERE token = p_token FOR UPDATE;
    IF NOT FOUND OR invitation.status <> 'pending' THEN
        RETURN jsonb_build_object('outcome', 'unavailable');
    END IF;
    IF invitation.expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome', 'expired');
    END IF;
    IF coalesce(btrim(invitation.email), '') = ''
       OR lower(btrim(invitation.email)) IS DISTINCT FROM lower(btrim(p_user_email))
       OR NOT EXISTS (
           SELECT 1 FROM auth.users u WHERE u.id = p_user_id
           AND lower(btrim(u.email)) = lower(btrim(invitation.email))
       ) THEN
        RETURN jsonb_build_object('outcome', 'wrong_email');
    END IF;
    PERFORM 1 FROM public.org_members
        WHERE org_id = invitation.org_id AND user_id = invitation.invited_by AND role = 'owner'
        FOR SHARE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'unavailable'); END IF;
    SELECT * INTO member FROM public.org_members
        WHERE org_id = invitation.org_id AND user_id = p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        IF p_enforce_local_seat_limit AND invitation.role = 'member'
           AND org.seat_limit > 0
           AND public.count_billable_organization_members(org.id) >= org.seat_limit THEN
            RETURN jsonb_build_object('outcome', 'seat_limit', 'seat_limit', org.seat_limit);
        END IF;
        INSERT INTO public.org_members(org_id, user_id, role)
            VALUES (invitation.org_id, p_user_id, invitation.role)
            RETURNING * INTO member;
    END IF;
    UPDATE public.org_invitations SET status = 'accepted' WHERE id = invitation.id;
    RETURN jsonb_build_object('outcome', 'accepted', 'member', to_jsonb(member), 'organization', to_jsonb(org));
END;
$$;
REVOKE ALL ON FUNCTION public.accept_organization_invitation(text, uuid, text, boolean)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.accept_organization_invitation(text, uuid, text, boolean)
    TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
