'use client';

import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import {
  joinProjectViaShareToken,
  type ProjectShareJoinResult,
} from '@/lib/projectsApi';
import { PulseGrid, Dots } from '@/components/loading';

/**
 * Share-link acceptance page.
 *
 * Counterpart of ``/invite/[token]`` (org invitations) but for the
 * per-project share-link MVP. Flow:
 *   1. User arrives at ``/share/{token}``.
 *   2. If signed out → bounce to ``/login?redirect=/share/{token}``.
 *      The login page already honors ``redirect``; after auth the
 *      user lands back here with a session.
 *   3. POST ``/api/v1/projects/share/{token}/join``. The endpoint
 *      adds them as ``viewer`` and is idempotent for existing
 *      members.
 *   4. Brief "Joined {name}" confirmation, then redirect into the
 *      project's data page.
 *
 * Mirrors the invite page's structure deliberately so future plugin
 * authors / new join flows can pattern-match.
 */
export default function ShareAcceptPage() {
  return (
    <Suspense fallback={<ShareFallback />}>
      <ShareAcceptInner />
    </Suspense>
  );
}

function ShareFallback() {
  return (
    <div style={containerStyle}>
      <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} style={{ opacity: 0.5 }} />
      <Dots size="sm" />
    </div>
  );
}

type Status =
  | { kind: 'pending' }
  | { kind: 'joining' }
  | { kind: 'success'; result: ProjectShareJoinResult }
  | { kind: 'error'; message: string };

function ShareAcceptInner() {
  const router = useRouter();
  const params = useParams<{ token: string }>();
  const token = typeof params?.token === 'string'
    ? params.token
    : Array.isArray(params?.token)
      ? params.token[0]
      : '';
  const { session, isAuthReady, signOut } = useAuth();

  const [status, setStatus] = useState<Status>({ kind: 'pending' });
  const sharePath = typeof window !== 'undefined' ? window.location.pathname : '';

  // Auth gate: bounce to /login?redirect=... when signed out.
  useEffect(() => {
    if (!isAuthReady) return;
    if (!session && sharePath) {
      router.replace(`/login?redirect=${encodeURIComponent(sharePath)}`);
    }
  }, [isAuthReady, session, sharePath, router]);

  // Join once we have a session + token. Guarded by a ref so the
  // bounce-back render doesn't double-fire.
  const joinedRef = React.useRef(false);
  useEffect(() => {
    if (!isAuthReady || !session || !token) return;
    if (joinedRef.current) return;
    joinedRef.current = true;
    setStatus({ kind: 'joining' });
    joinProjectViaShareToken(token)
      .then(result => {
        setStatus({ kind: 'success', result });
        // Land the user inside the project they just joined. We
        // redirect to the data page rather than /home so the visible
        // outcome is "now I see this project" rather than the
        // dashboard.
        setTimeout(() => {
          router.replace(`/projects/${result.project_id}/data`);
        }, 1200);
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'Share link could not be used';
        setStatus({ kind: 'error', message });
      });
  }, [isAuthReady, session, token, router]);

  const handleUseDifferentAccount = useCallback(async () => {
    try {
      await signOut();
    } catch {
      /* signOut already redirects via window.location */
    }
  }, [signOut]);

  if (!isAuthReady || status.kind === 'pending') {
    return (
      <div style={containerStyle}>
        <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} style={{ opacity: 0.5 }} />
        <PulseGrid />
        <div style={mutedTextStyle}>Loading share link…</div>
      </div>
    );
  }

  if (!session) {
    return (
      <div style={containerStyle}>
        <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} style={{ opacity: 0.5 }} />
        <Dots size="sm" />
        <div style={mutedTextStyle}>Sign in to use this share link…</div>
      </div>
    );
  }

  if (status.kind === 'joining') {
    return (
      <div style={containerStyle}>
        <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} />
        <Dots size="sm" />
        <div style={mutedTextStyle}>Joining the project…</div>
      </div>
    );
  }

  if (status.kind === 'success') {
    return (
      <div style={containerStyle}>
        <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} />
        <div style={{ ...mutedTextStyle, color: 'var(--po-text)' }}>
          {status.result.newly_joined ? 'Joined' : 'Already a member of'}{' '}
          <strong>{status.result.project_name}</strong>
        </div>
        <div style={mutedTextStyle}>Taking you in…</div>
      </div>
    );
  }

  return (
    <div style={containerStyle}>
      <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} style={{ opacity: 0.6 }} />
      <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--po-text)' }}>
        Share link could not be used
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

// ── styling (mirrors the /invite page) ────────────────────────────

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
