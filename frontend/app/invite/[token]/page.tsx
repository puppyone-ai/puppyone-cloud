'use client';

import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { acceptInvitation, type AcceptInvitationResult } from '@/lib/organizationsApi';
import { PulseGrid, Dots } from '@/components/loading';

/**
 * Invitation acceptance landing page.
 *
 * Flow (see ``docs/proposals/...`` if added; rooted in PUP-4):
 *   1. Invitee clicks the link ``/invite/{token}``.
 *   2. If signed out, we bounce to ``/login?redirect=/invite/{token}``;
 *      the login page honors the redirect and lands us back here after
 *      auth (sign-in OR sign-up of a fresh account).
 *   3. Once signed in, we POST the token to the backend accept
 *      endpoint, which adds an ``org_members`` row and returns the
 *      joined org metadata.
 *   4. On success, render a brief "Joined {org_name}" confirmation
 *      and bounce to /home so the user lands inside the workspace.
 *
 * The same page handles three failure modes the user can recover from:
 *   - Invalid/expired token (404 from accept) — show "this invite link
 *     is no longer valid" with a Sign in / Sign out link.
 *   - Already a member — surfaced as success by the backend; we still
 *     redirect to /home.
 *   - Wrong account signed in — we expose a "Use a different account"
 *     button that signs out and bounces back to the invite URL.
 *
 * The middleware does NOT add ``/invite`` to its protected list — the
 * page itself handles the auth gate, because middleware-driven redirect
 * loses the original URL on some browsers (it ends up in /login with
 * no redirect param when the session cookie is mid-refresh).
 */
export default function InviteAcceptPage() {
  return (
    <Suspense fallback={<InviteFallback />}>
      <InviteAcceptInner />
    </Suspense>
  );
}

function InviteFallback() {
  return (
    <div style={containerStyle}>
      <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} style={{ opacity: 0.5 }} />
      <Dots size="sm" />
    </div>
  );
}

type Status =
  | { kind: 'pending' }
  | { kind: 'accepting' }
  | { kind: 'success'; result: AcceptInvitationResult }
  | { kind: 'error'; message: string };

function InviteAcceptInner() {
  const router = useRouter();
  const params = useParams<{ token: string }>();
  const token = typeof params?.token === 'string' ? params.token : Array.isArray(params?.token) ? params.token[0] : '';
  const { session, isAuthReady, signOut } = useAuth();

  const [status, setStatus] = useState<Status>({ kind: 'pending' });
  const inviteUrl = typeof window !== 'undefined' ? `${window.location.pathname}` : '';

  // Auth gate. If we know there's no session, redirect to login with
  // a redirect-back param. Wait until isAuthReady so we don't bounce
  // unnecessarily during the first render's auth boot.
  useEffect(() => {
    if (!isAuthReady) return;
    if (!session && inviteUrl) {
      router.replace(`/login?redirect=${encodeURIComponent(inviteUrl)}`);
    }
  }, [isAuthReady, session, inviteUrl, router]);

  // Once signed in, fire the accept call. Gate on session + token so
  // we don't double-fire during the bounce-back render.
  const acceptedRef = React.useRef(false);
  useEffect(() => {
    if (!isAuthReady || !session || !token) return;
    if (acceptedRef.current) return;
    acceptedRef.current = true;
    setStatus({ kind: 'accepting' });
    acceptInvitation(token)
      .then((result) => {
        setStatus({ kind: 'success', result });
        // Brief confirmation, then bounce.
        setTimeout(() => {
          router.replace('/home');
        }, 1200);
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'Invitation could not be accepted';
        setStatus({ kind: 'error', message });
      });
  }, [isAuthReady, session, token, router]);

  const handleUseDifferentAccount = useCallback(async () => {
    // Sign out and bring the user back to this invite URL so they can
    // sign in as the right account.
    try {
      await signOut();
    } catch {
      // signOut redirects via window.location; ignore unreachable code.
    }
  }, [signOut]);

  // Render
  if (!isAuthReady || status.kind === 'pending') {
    return (
      <div style={containerStyle}>
        <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} style={{ opacity: 0.5 }} />
        <PulseGrid />
        <div style={mutedTextStyle}>Loading invitation…</div>
      </div>
    );
  }

  if (!session) {
    // The redirect effect above is firing; show a steady "redirecting"
    // overlay so the user isn't staring at a blank page.
    return (
      <div style={containerStyle}>
        <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} style={{ opacity: 0.5 }} />
        <Dots size="sm" />
        <div style={mutedTextStyle}>Sign in to accept this invitation…</div>
      </div>
    );
  }

  if (status.kind === 'accepting') {
    return (
      <div style={containerStyle}>
        <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} />
        <Dots size="sm" />
        <div style={mutedTextStyle}>Joining your new workspace…</div>
      </div>
    );
  }

  if (status.kind === 'success') {
    return (
      <div style={containerStyle}>
        <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} />
        <div style={{ ...mutedTextStyle, color: 'var(--po-text)' }}>
          Joined <strong>{status.result.org_name}</strong>
        </div>
        <div style={mutedTextStyle}>Taking you in…</div>
      </div>
    );
  }

  return (
    <div style={containerStyle}>
      <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} style={{ opacity: 0.6 }} />
      <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--po-text)' }}>
        Invitation could not be accepted
      </div>
      <div style={{ ...mutedTextStyle, maxWidth: 340, textAlign: 'center' }}>
        {status.message}
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        <button onClick={() => router.replace('/home')} style={primaryButtonStyle}>
          Go to home
        </button>
        <button onClick={handleUseDifferentAccount} style={secondaryButtonStyle}>
          Use a different account
        </button>
      </div>
      <div style={{ ...mutedTextStyle, fontSize: 11 }}>
        Signed in as {session.user?.email ?? 'your account'}.
      </div>
    </div>
  );
}

// ── styling (kept inline to match the login page's pattern; this page
//    sits outside the (main) layout so it can't piggy-back on the
//    shell's CSS variables wrapper).

const containerStyle: React.CSSProperties = {
  minHeight: '100vh',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 20,
  background: 'var(--po-inset)',
  padding: 24,
};

const mutedTextStyle: React.CSSProperties = {
  fontSize: 13,
  color: 'var(--po-text-muted)',
};

const primaryButtonStyle: React.CSSProperties = {
  height: 40,
  padding: '0 16px',
  borderRadius: 6,
  border: 'none',
  background: 'var(--po-text)',
  color: 'var(--po-text-inverse)',
  fontSize: 14,
  fontWeight: 600,
  cursor: 'pointer',
};

const secondaryButtonStyle: React.CSSProperties = {
  height: 40,
  padding: '0 16px',
  borderRadius: 6,
  border: '1px solid var(--po-border)',
  background: 'var(--po-control)',
  color: 'var(--po-text)',
  fontSize: 14,
  fontWeight: 500,
  cursor: 'pointer',
};
