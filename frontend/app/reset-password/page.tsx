'use client';

import React, { useState, useEffect } from 'react';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { useRouter } from 'next/navigation';
import { InlineLoading } from '@/components/loading';

type PageState = 'loading' | 'ready' | 'success' | 'no-session';

const SUCCESS_REDIRECT_DELAY_MS = 3000;

export default function ResetPasswordPage() {
  const router = useRouter();
  const { session, isAuthReady, updatePassword, signOut } = useAuth();

  const [pageState, setPageState] = useState<PageState>('loading');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [redirectIn, setRedirectIn] = useState(SUCCESS_REDIRECT_DELAY_MS / 1000);

  useEffect(() => {
    if (!isAuthReady) return;
    // Once we've reached a terminal state (success), Supabase auth events
    // (USER_UPDATED / SIGNED_OUT triggered by the password update + signOut
    // below) MUST NOT flip the UI back to the form — otherwise the user
    // sees an empty form and assumes the change failed.
    setPageState(prev => {
      if (prev === 'success') return prev;
      return session ? 'ready' : 'no-session';
    });
  }, [isAuthReady, session]);

  // Auto-redirect to /login a few seconds after success so the user is never
  // left wondering whether the change went through.
  useEffect(() => {
    if (pageState !== 'success') return;
    setRedirectIn(SUCCESS_REDIRECT_DELAY_MS / 1000);
    const tick = setInterval(() => {
      setRedirectIn(prev => Math.max(prev - 1, 0));
    }, 1000);
    const redirect = setTimeout(() => {
      router.push('/login?reset=success');
    }, SUCCESS_REDIRECT_DELAY_MS);
    return () => {
      clearInterval(tick);
      clearTimeout(redirect);
    };
  }, [pageState, router]);

  const validate = (): string | null => {
    if (password.length < 6) return 'Password must be at least 6 characters.';
    if (password !== confirmPassword) return 'Passwords do not match.';
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const validationError = validate();
    if (validationError) {
      setFormError(validationError);
      return;
    }

    // Capture the user's email NOW, before signOut wipes the session — we
    // hand it off to /login below so the email field can be pre-filled.
    const userEmail = session?.user?.email ?? null;

    setSubmitting(true);
    try {
      await updatePassword(password);
      // Mark success BEFORE signing out so the auth-state listener doesn't
      // flip the UI to 'no-session' and hide the confirmation card.
      setPageState('success');

      // Stash the email so /login?reset=success can pre-fill the form and
      // jump straight to the password step (avoids the email-first hop).
      if (userEmail && typeof window !== 'undefined') {
        try {
          window.sessionStorage.setItem('puppyone:reset-email', userEmail);
        } catch {
          // sessionStorage may be unavailable (private mode, quota, etc.) —
          // non-fatal; the user just has to retype their email on /login.
        }
      }

      // Invalidate the recovery session — force the user to sign in fresh
      // with the new password (defends against shared/public devices).
      try {
        await signOut();
      } catch {
        // Non-fatal: password is already changed; user can still sign in.
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to update password';
      setFormError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  if (pageState === 'loading') {
    return (
      <div style={containerStyle}>
        <div style={cardStyle}>
          <div style={{ display: 'flex', justifyContent: 'center' }}>
            <InlineLoading label="Verifying…" />
          </div>
        </div>
      </div>
    );
  }

  if (pageState === 'no-session') {
    return (
      <div style={containerStyle}>
        <div style={cardStyle}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 32, marginBottom: 16 }}>🔗</div>
            <div style={{ fontSize: 16, fontWeight: 500, color: 'var(--po-text)', marginBottom: 8 }}>
              Invalid or Expired Link
            </div>
            <div style={{ fontSize: 13, color: 'var(--po-text-muted)', marginBottom: 20, lineHeight: 1.5 }}>
              This password reset link is no longer valid. Please request a new one.
            </div>
            <button
              onClick={() => router.push('/login')}
              style={primaryBtnStyle}
              onMouseEnter={primaryHoverIn}
              onMouseLeave={primaryHoverOut}
            >
              Back to Sign In
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (pageState === 'success') {
    return (
      <div style={containerStyle}>
        <div style={cardStyle}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 32, marginBottom: 16 }}>✓</div>
            <div style={{ fontSize: 16, fontWeight: 500, color: 'var(--po-text)', marginBottom: 8 }}>
              Password Updated
            </div>
            <div style={{ fontSize: 13, color: 'var(--po-text-muted)', marginBottom: 20, lineHeight: 1.5 }}>
              Your password has been changed and you have been signed out.
              Please sign in with your new password.
            </div>
            <button
              onClick={() => router.push('/login?reset=success')}
              style={primaryBtnStyle}
              onMouseEnter={primaryHoverIn}
              onMouseLeave={primaryHoverOut}
            >
              {redirectIn > 0 ? `Continue to Sign In (${redirectIn})` : 'Continue to Sign In'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={containerStyle}>
      <div style={cardStyle}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ textAlign: 'center', marginBottom: 16 }}>
            <img
              src='/puppyone-logo.svg'
              alt='Puppyone'
              width={64}
              height={64}
              style={{ opacity: 0.95, display: 'block', margin: '0 auto' }}
            />
            <div style={{ marginTop: 10, fontSize: 16, fontWeight: 500, color: 'var(--po-text-subtle)' }}>
              Set a new password
            </div>
          </div>

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label style={labelStyle}>New Password</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="At least 6 characters"
                required
                minLength={6}
                disabled={submitting}
                style={inputStyle}
                autoFocus
              />
            </div>

            <div>
              <label style={labelStyle}>Confirm Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                placeholder="Re-enter your password"
                required
                minLength={6}
                disabled={submitting}
                style={inputStyle}
              />
            </div>

            <button
              type="submit"
              disabled={submitting}
              style={primaryBtnStyle}
              onMouseEnter={e => !submitting && primaryHoverIn(e)}
              onMouseLeave={e => !submitting && primaryHoverOut(e)}
            >
              {submitting ? 'Updating...' : 'Update Password'}
            </button>
          </form>

          {formError && (
            <div style={errorStyle}>{formError}</div>
          )}

          <div style={{ textAlign: 'center', marginTop: 4 }}>
            <button
              onClick={() => router.push('/login')}
              style={linkBtnStyle}
            >
              Back to sign in
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const containerStyle: React.CSSProperties = {
  minHeight: '100vh',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor: 'var(--po-inset)',
  color: 'var(--po-text)',
  padding: 24,
};

const cardStyle: React.CSSProperties = {
  width: 360,
  border: '1px solid var(--po-border)',
  borderRadius: 12,
  padding: 24,
  background: 'var(--po-overlay)',
  boxShadow: '0 12px 40px var(--po-shadow), 0 0 0 1px var(--po-hover)',
};

const primaryBtnStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 12px',
  borderRadius: 8,
  border: 'none',
  background: 'var(--po-text)',
  color: 'var(--po-text-inverse)',
  cursor: 'pointer',
  fontSize: 14,
  fontWeight: 600,
  transition: 'all 0.15s ease',
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 12px',
  borderRadius: 8,
  border: '1px solid var(--po-border)',
  background: 'var(--po-inset)',
  color: 'var(--po-text)',
  fontSize: 14,
  outline: 'none',
  boxSizing: 'border-box',
  transition: 'border-color 0.15s ease',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 12,
  fontWeight: 500,
  color: 'var(--po-text-muted)',
  marginBottom: 6,
};

const errorStyle: React.CSSProperties = {
  color: 'var(--po-danger)',
  fontSize: 12,
  textAlign: 'center',
  padding: '8px 12px',
  background: 'color-mix(in srgb, var(--po-danger) 10%, transparent)',
  borderRadius: 6,
};

const linkBtnStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--po-accent-text)',
  cursor: 'pointer',
  fontSize: 12,
  padding: 0,
  textDecoration: 'underline',
};

const primaryHoverIn = (e: React.MouseEvent<HTMLButtonElement>) => {
  e.currentTarget.style.background = 'var(--po-text)';
  e.currentTarget.style.opacity = '0.9';
  e.currentTarget.style.boxShadow = '0 4px 12px var(--po-border-strong)';
};

const primaryHoverOut = (e: React.MouseEvent<HTMLButtonElement>) => {
  e.currentTarget.style.background = 'var(--po-text)';
  e.currentTarget.style.opacity = '1';
  e.currentTarget.style.boxShadow = 'none';
};
