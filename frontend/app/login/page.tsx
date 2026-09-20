'use client';

import React, { Suspense, useState, useCallback, useEffect, useRef } from 'react';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { useRouter, useSearchParams } from 'next/navigation';
import { PulseGrid, Dots } from '@/components/loading';

const URL_ERROR_MESSAGES: Record<string, string> = {
  signup_link_deprecated:
    'Email verification now uses a 6-digit code. Please sign in (or create your account) to receive a fresh code.',
  confirmation_failed:
    'That confirmation link is invalid or has expired. Please request a new one.',
  invalid_confirmation_link:
    'That confirmation link is invalid. Please try again.',
  auth_callback_failed:
    'Sign-in failed. Please try again.',
};

const URL_SUCCESS_MESSAGES: Record<string, string> = {
  reset: 'Password updated. Please sign in with your new password.',
};

type AuthView = 'main' | 'signin' | 'signup' | 'verify-otp' | 'forgot';

const BACKEND_API_BASE = '/api/backend/api/v1';
const RESEND_COOLDOWN_SECONDS = 300; // 5 minutes

function backendApiUrl(path: string): string {
  return `${BACKEND_API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

function getBackendErrorMessage(payload: any, fallback: string): string {
  const detail = payload?.detail;
  if (typeof payload?.message === 'string' && payload.message) return payload.message;
  if (typeof detail === 'string' && detail) return detail;
  if (typeof detail?.message === 'string' && detail.message) return detail.message;
  if (typeof payload?.error === 'string' && payload.error) return payload.error;
  return fallback;
}

/**
 * Default export wraps the inner page in <Suspense> because
 * useSearchParams() forces client-side rendering and Next.js 15
 * requires an explicit Suspense boundary during prerender.
 * Without this wrapper the production build fails with:
 *   "useSearchParams() should be wrapped in a suspense boundary at page /login"
 */
export default function LoginPage() {
  return (
    <Suspense fallback={<LoginPageFallback />}>
      <LoginPageInner />
    </Suspense>
  );
}

function LoginPageFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--po-inset)]">
      <PulseGrid />
    </div>
  );
}

/**
 * Full-screen overlay shown after a successful sign-in / sign-up / OTP
 * verification, while the client-side router loads the (main) layout
 * and home page chunks. Sits on top of the form so the UI doesn't
 * snap back to an idle "Sign In" button during the navigation gap.
 */
function PostAuthRedirectingScreen({ message }: { message: string }) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        background: 'var(--po-inset)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 24,
      }}
    >
      <img
        src="/puppyone-logo.svg"
        alt="Puppyone"
        width={48}
        height={48}
        className="opacity-95"
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <PulseGrid />
        <span style={{ fontSize: 14, color: 'var(--po-text-muted)' }}>
          {message}
        </span>
      </div>
    </div>
  );
}

function LoginPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const {
    supabase,
    session,
    signInWithProvider,
    signInWithEmail,
    signUpWithEmail,
    verifyEmailOtp,
    resendConfirmation,
    resetPassword,
    getAccessToken,
  } = useAuth();

  // Honor ``?redirect=...`` so callers that bounce through login
  // (e.g. /invite/[token]) land back at the originally requested URL
  // after auth. Only accept same-origin relative paths to prevent
  // open-redirect abuse.
  const redirectAfterAuth = React.useMemo(() => {
    const raw = searchParams?.get('redirect') ?? null;
    if (!raw) return null;
    if (!raw.startsWith('/')) return null;
    if (raw.startsWith('//')) return null; // protocol-relative
    if (raw.startsWith('/\\')) return null; // backslash → protocol-relative
    return raw;
  }, [searchParams]);
  const desktopAuthState = React.useMemo(() => {
    if (searchParams?.get('client') !== 'desktop') return null;
    const state = searchParams.get('desktop_state') ?? searchParams.get('state');
    return state?.trim() || null;
  }, [searchParams]);

  const [view, setView] = useState<AuthView>('main');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [otpCode, setOtpCode] = useState('');
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);

  // True the moment auth succeeds and we trigger router.push('/home').
  // Until the new page chunks load + (main)/layout mounts, Next.js keeps
  // the old login page on screen. Without a dedicated overlay the user
  // sees the form snap back to its idle state and thinks nothing happened.
  const [redirecting, setRedirecting] = useState(false);
  const cooldownTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoSubmittedRef = useRef(false);
  const autoOAuthProviderRef = useRef<'google' | 'github' | null>(null);
  const autoDesktopCompleteRef = useRef(false);

  const clearFeedback = useCallback(() => {
    setError(null);
    setMessage(null);
  }, []);

  const startResendCooldown = useCallback(() => {
    setResendCooldown(RESEND_COOLDOWN_SECONDS);
    if (cooldownTimerRef.current) clearInterval(cooldownTimerRef.current);
    cooldownTimerRef.current = setInterval(() => {
      setResendCooldown(prev => {
        if (prev <= 1) {
          if (cooldownTimerRef.current) {
            clearInterval(cooldownTimerRef.current);
            cooldownTimerRef.current = null;
          }
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  }, []);

  useEffect(() => {
    return () => {
      if (cooldownTimerRef.current) clearInterval(cooldownTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const errorCode = searchParams?.get('error');
    const resetFlag = searchParams?.get('reset');
    if (!errorCode && !resetFlag) return;
    if (errorCode) {
      setError(URL_ERROR_MESSAGES[errorCode] ?? 'Something went wrong. Please try again.');
    }
    if (resetFlag && URL_SUCCESS_MESSAGES.reset) {
      setMessage(URL_SUCCESS_MESSAGES.reset);

      // /reset-password stashes the user's email in sessionStorage right
      // before signing out. If we have it, pre-fill the form and jump
      // straight to the password step so the user doesn't have to retype
      // their email (and never sees the empty "missing email" state).
      let storedEmail: string | null = null;
      if (typeof window !== 'undefined') {
        try {
          storedEmail = window.sessionStorage.getItem('puppyone:reset-email');
          if (storedEmail) {
            window.sessionStorage.removeItem('puppyone:reset-email');
          }
        } catch {
          // sessionStorage unavailable — fall back to the email-first view.
        }
      }
      if (storedEmail) {
        setEmail(storedEmail);
        setView('signin');
      }
      // If no stored email, leave view as 'main' so the user can type it in.
    }
    // Clean up the URL so the banner doesn't reappear on every
    // navigation. Preserve ``redirect=`` if present so the post-auth
    // bounce destination survives this cleanup — without this, an
    // unrelated ?error= or ?reset= arriving alongside the invite
    // bounce would wipe the redirect target and dump the user at /home.
    const preservedRedirect = searchParams?.get('redirect');
    if (preservedRedirect) {
      router.replace(`/login?redirect=${encodeURIComponent(preservedRedirect)}`);
    } else {
      router.replace('/login');
    }
  }, [searchParams, router]);

  const goBack = useCallback(() => {
    setView('main');
    setPassword('');
    setOtpCode('');
    setNeedsVerification(false);
    clearFeedback();
  }, [clearFeedback]);

  const completeDesktopAuth = useCallback(async () => {
    if (!desktopAuthState) return false;
    if (!supabase) throw new Error('Auth client is not ready.');

    const { data } = await supabase.auth.getSession();
    let currentSession = data.session;
    if (!currentSession?.access_token || !currentSession.refresh_token) {
      throw new Error('Sign-in succeeded but no session was returned.');
    }

    const submit = (accessToken: string, refreshToken: string) => fetch(
      backendApiUrl('/auth/desktop/complete'),
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          state: desktopAuthState,
          access_token: accessToken,
          refresh_token: refreshToken,
          expires_in: currentSession?.expires_in,
          user_email: currentSession?.user?.email ?? null,
        }),
      },
    );

    let resp = await submit(currentSession.access_token, currentSession.refresh_token);
    if (resp.status === 401) {
      // A browser can briefly expose an old session while Supabase finishes
      // rotating it after sign-in. Refresh once and retry once; never spin a
      // render-driven request loop against the desktop completion endpoint.
      const refreshed = await supabase.auth.refreshSession();
      currentSession = refreshed.data.session;
      if (!refreshed.error && currentSession?.access_token && currentSession.refresh_token) {
        resp = await submit(currentSession.access_token, currentSession.refresh_token);
      }
    }
    const json = await resp.json().catch(() => null);
    if (!resp.ok) {
      throw new Error(getBackendErrorMessage(json, 'Desktop sign-in failed.'));
    }
    const redirectUrl = json?.data?.redirect_url;
    if (typeof redirectUrl !== 'string' || !redirectUrl) {
      throw new Error('Desktop sign-in did not return a callback URL.');
    }
    window.location.href = redirectUrl;
    return true;
  }, [desktopAuthState, supabase]);

  const handleOAuthSignIn = async (provider: 'google' | 'github') => {
    if (process.env.NEXT_PUBLIC_AUTH_EMAIL_ONLY === 'true') return;
    clearFeedback();
    setLoading(provider);
    try {
      if (desktopAuthState) {
        const url = backendApiUrl(
          `/auth/desktop/login?state=${encodeURIComponent(desktopAuthState)}&provider=${encodeURIComponent(provider)}`,
        );
        window.location.href = url;
        return;
      }
      await signInWithProvider(provider);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Sign-in failed');
    } finally {
      setLoading(null);
    }
  };

  useEffect(() => {
    const queryEmail = searchParams?.get('email')?.trim();
    if (!queryEmail) return;
    setEmail((current) => current || queryEmail);
  }, [searchParams]);

  useEffect(() => {
    const provider = searchParams?.get('provider');
    if (process.env.NEXT_PUBLIC_AUTH_EMAIL_ONLY === 'true') return;
    if (provider !== 'google' && provider !== 'github') return;
    if (autoOAuthProviderRef.current === provider) return;

    autoOAuthProviderRef.current = provider;
    void handleOAuthSignIn(provider);
  }, [searchParams]);

  useEffect(() => {
    if (!desktopAuthState || !session || redirecting || autoDesktopCompleteRef.current) return;
    autoDesktopCompleteRef.current = true;
    setRedirecting(true);
    completeDesktopAuth().catch((e: unknown) => {
      setRedirecting(false);
      setError(e instanceof Error ? e.message : 'Desktop sign-in failed.');
    });
  }, [desktopAuthState, session, redirecting, completeDesktopAuth]);

  const handleContinue = async (e: React.FormEvent) => {
    e.preventDefault();
    clearFeedback();
    setLoading('continue');
    try {
      const resp = await fetch(backendApiUrl('/auth/check-email'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      const json = await resp.json();
      if (resp.status === 429) throw new Error('Too many attempts. Please wait a moment and try again.');
      if (!resp.ok) throw new Error(getBackendErrorMessage(json, 'Failed to check email'));
      const exists = json.data?.exists;
      setView(exists ? 'signin' : 'signup');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setLoading(null);
    }
  };

  // Switch to OTP verification view and reset relevant state.
  const goToVerifyOtp = useCallback((withMessage?: string) => {
    setOtpCode('');
    autoSubmittedRef.current = false;
    setView('verify-otp');
    setError(null);
    setMessage(withMessage ?? null);
  }, []);

  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    clearFeedback();
    setNeedsVerification(false);
    setLoading('password');
    try {
      await signInWithEmail(email, password);
      if (desktopAuthState) {
        setRedirecting(true);
        await completeDesktopAuth();
        return;
      }
      // Show full-screen overlay BEFORE router.push — the navigation is
      // async (chunk load + (main) layout init + projects fetch) and we
      // need a continuous loading UI for the entire gap. Don't reset
      // `loading` either; we want to stay in a non-interactive state
      // until this component unmounts.
      setRedirecting(true);
      router.push(redirectAfterAuth ?? '/home');
      return;
    } catch (e: unknown) {
      setRedirecting(false);
      const msg = e instanceof Error ? e.message : 'Sign-in failed';
      setError(msg);
      if (msg.toLowerCase().includes('email not confirmed')) {
        setNeedsVerification(true);
      }
    } finally {
      setLoading(null);
    }
  };

  // From signin error: send a fresh code and jump to OTP view.
  const handleStartVerification = async () => {
    clearFeedback();
    setNeedsVerification(false);
    setLoading('start-verify');
    try {
      await resendConfirmation(email);
      startResendCooldown();
      goToVerifyOtp(`We sent a 6-digit code to ${email}. Enter it below to finish signing in.`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to send verification code');
    } finally {
      setLoading(null);
    }
  };

  const handleResendCode = async () => {
    if (resendCooldown > 0) return;
    clearFeedback();
    setLoading('resend');
    try {
      await resendConfirmation(email);
      startResendCooldown();
      setMessage('A new code is on its way to your inbox.');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to resend code');
    } finally {
      setLoading(null);
    }
  };

  const handleSignUp = async (e: React.FormEvent) => {
    e.preventDefault();
    clearFeedback();
    setLoading('signup');
    try {
      const result = await signUpWithEmail(email, password);
      if (result.needsEmailConfirmation) {
        startResendCooldown();
        goToVerifyOtp(`We sent a 6-digit code to ${email}.`);
        setPassword('');
      } else {
        if (desktopAuthState) {
          setRedirecting(true);
          await completeDesktopAuth();
          return;
        }
        // Auto-confirmed signup (rare in our default config) — initialize
        // and go straight to the seeded demo project so first-time UX
        // mirrors the OTP / OAuth paths.
        let demoProjectId: string | null = null;
        try {
          const token = await getAccessToken();
          if (token) {
            const res = await fetch(backendApiUrl('/auth/initialize'), {
              method: 'POST',
              headers: { Authorization: `Bearer ${token}` },
            });
            if (res.ok) {
              const json = await res.json();
              demoProjectId = json?.data?.demo_project_id ?? null;
            }
          }
        } catch (initErr) {
          console.error('Auth initialization failed:', initErr);
        }
        setRedirecting(true);
        // If the user was bounced here from /invite/[token] (or any
        // other gated route), honor that destination rather than the
        // first-run demo path — they came here to accept an invite,
        // not to play with the demo.
        router.push(
          redirectAfterAuth
            ?? (demoProjectId ? `/projects/${demoProjectId}/data` : '/home'),
        );
        return;
      }
    } catch (e: unknown) {
      setRedirecting(false);
      setError(e instanceof Error ? e.message : 'Sign-up failed');
    } finally {
      setLoading(null);
    }
  };

  const handleVerifyOtp = useCallback(async (code: string) => {
    if (loading !== null) return;
    if (code.length !== 6) {
      setError('Please enter all 6 digits.');
      return;
    }
    clearFeedback();
    setLoading('verify');
    try {
      await verifyEmailOtp(email, code);
      if (desktopAuthState) {
        setRedirecting(true);
        await completeDesktopAuth();
        return;
      }
      // Initialize profile + org (idempotent — same as OAuth callback).
      // On first sign-in this also seeds a "Get Started" demo project so
      // we can land the user inside it instead of an empty dashboard.
      let demoProjectId: string | null = null;
      try {
        const token = await getAccessToken();
        if (token) {
          const res = await fetch(backendApiUrl('/auth/initialize'), {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
          });
          if (res.ok) {
            const json = await res.json();
            demoProjectId = json?.data?.demo_project_id ?? null;
          }
        }
      } catch (initErr) {
        console.error('Auth initialization failed:', initErr);
      }
      setRedirecting(true);
      // Honor redirect= for the OTP path too — covers the case where
      // someone clicked an invite link, hit "sign up", and verified
      // their email here. They wanted the invite, not the demo.
      router.push(
        redirectAfterAuth
          ?? (demoProjectId ? `/projects/${demoProjectId}/data` : '/home'),
      );
      return;
    } catch (e: unknown) {
      setRedirecting(false);
      const msg = e instanceof Error ? e.message : 'Invalid or expired code';
      setError(msg);
      setOtpCode('');
      autoSubmittedRef.current = false;
    } finally {
      setLoading(null);
    }
  }, [loading, clearFeedback, verifyEmailOtp, email, desktopAuthState, completeDesktopAuth, getAccessToken, router, redirectAfterAuth]);

  // Auto-submit when 6 digits are entered (industry-standard UX).
  useEffect(() => {
    if (
      view === 'verify-otp' &&
      otpCode.length === 6 &&
      loading === null &&
      !autoSubmittedRef.current
    ) {
      autoSubmittedRef.current = true;
      void handleVerifyOtp(otpCode);
    }
  }, [view, otpCode, loading, handleVerifyOtp]);

  const handleForgotPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    clearFeedback();
    setLoading('forgot');
    try {
      await resetPassword(email);
      setMessage('Check your email for the password reset link.');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to send reset link');
    } finally {
      setLoading(null);
    }
  };

  const disabled = loading !== null || redirecting;

  if (redirecting) {
    return <PostAuthRedirectingScreen message="Signing you in..." />;
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--po-inset)] text-[var(--po-text)] p-6 font-sans">
      <div className="w-[380px] p-6">
        <div className="flex flex-col gap-2.5">
          {/* Back navigation — above logo, only on sub-views */}
          {view !== 'main' && (
            <div className="mb-2">
              <BackButton
                onClick={view === 'forgot' ? () => { clearFeedback(); setView('signin'); } : goBack}
                label={view === 'forgot' ? 'Back' : 'All sign in options'}
              />
            </div>
          )}

          {/* Global Logo */}
          <div className="flex justify-center mb-4">
            <img
              src="/puppyone-logo.svg"
              alt="Puppyone"
              width={48}
              height={48}
              className="opacity-95"
            />
          </div>

          {/* ── Main View ── */}
          {view === 'main' && (
            <div className="animate-fade-in">
              <div className="mb-8 text-center">
                <h1 className="text-2xl font-semibold text-[var(--po-text)]">Sign in or sign up</h1>
              </div>

              {process.env.NEXT_PUBLIC_AUTH_EMAIL_ONLY !== 'true' && <><div className="flex flex-col gap-3">
                <ProviderButton
                  icon={<GoogleIcon />}
                  label="Continue with Google"
                  loadingLabel="Redirecting..."
                  isLoading={loading === 'google'}
                  disabled={disabled}
                  onClick={() => handleOAuthSignIn('google')}
                />
                <ProviderButton
                  icon={<GithubIcon />}
                  label="Continue with GitHub"
                  loadingLabel="Redirecting..."
                  isLoading={loading === 'github'}
                  disabled={disabled}
                  onClick={() => handleOAuthSignIn('github')}
                />
              </div>

              <AuthDivider /></>}

              <div>
                <form onSubmit={handleContinue} className="flex flex-col gap-3">
                  <InputField
                    label="Email"
                    type="email"
                    value={email}
                    onChange={setEmail}
                    placeholder="Your email address"
                    disabled={disabled}
                  />
                  <SubmitButton disabled={disabled} loading={loading === 'continue'}>
                    {loading === 'continue' ? 'Checking…' : 'Continue'}
                  </SubmitButton>
                </form>
              </div>

              <Feedback error={error} message={message} />
            </div>
          )}

          {/* ── Sign In View ── */}
          {view === 'signin' && (
            <div className="animate-fade-in">
              <div className="mb-6 text-center">
                <h2 className="text-xl font-semibold text-[var(--po-text)]">Welcome back</h2>
                <p className="mt-1 text-sm text-[var(--po-text-muted)]">{email}</p>
              </div>

              <form onSubmit={handleSignIn} className="flex flex-col gap-3">
                <InputField
                  label="Password"
                  type="password"
                  value={password}
                  onChange={setPassword}
                  placeholder="Enter your password"
                  disabled={disabled}
                  minLength={6}
                  autoFocus
                />
                <SubmitButton disabled={disabled} loading={loading === 'password'}>
                  {loading === 'password' ? 'Signing in…' : 'Sign In'}
                </SubmitButton>
              </form>

              <Feedback error={error} message={message} />

              {needsVerification && (
                <div className="mt-3">
                  <button
                    onClick={handleStartVerification}
                    disabled={disabled}
                    className="w-full h-10 px-4 rounded-md border border-[var(--po-border)] bg-[var(--po-control)] text-[var(--po-text)] cursor-pointer text-sm font-medium transition-all hover:bg-[var(--po-control-hover)] hover:border-[var(--po-border-strong)] disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
                  >
                    {loading === 'start-verify' && <Dots size="xs" />}
                    {loading === 'start-verify' ? 'Sending code…' : 'Verify your email'}
                  </button>
                </div>
              )}

              <div className="mt-4 text-center">
                <button
                  onClick={() => { clearFeedback(); setView('forgot'); }}
                  className="inline-flex h-[30px] items-center justify-center bg-transparent border-none text-[var(--po-text-subtle)] hover:text-[var(--po-text)] cursor-pointer text-sm px-1 transition-colors"
                >
                  Forgot password?
                </button>
              </div>
            </div>
          )}

          {/* ── Verify OTP View ── */}
          {view === 'verify-otp' && (
            <div className="animate-fade-in">
              <div className="mb-6 text-center">
                <h2 className="text-xl font-semibold text-[var(--po-text)]">Check your email</h2>
                <p className="mt-1 text-sm text-[var(--po-text-muted)]">
                  Enter the 6-digit code we sent to
                </p>
                <p className="text-sm text-[var(--po-text)] font-medium">{email}</p>
              </div>

              <form
                onSubmit={(e) => { e.preventDefault(); void handleVerifyOtp(otpCode); }}
                className="flex flex-col gap-3"
              >
                <OtpInput
                  value={otpCode}
                  onChange={(v) => {
                    setOtpCode(v);
                    if (error) setError(null);
                  }}
                  disabled={disabled}
                  autoFocus
                />
                <SubmitButton disabled={disabled || otpCode.length !== 6} loading={loading === 'verify'}>
                  {loading === 'verify' ? 'Verifying…' : 'Verify & Continue'}
                </SubmitButton>
              </form>

              <Feedback error={error} message={message} />

              <div className="mt-4 text-center">
                <button
                  onClick={handleResendCode}
                  disabled={disabled || resendCooldown > 0}
                  className="h-[30px] bg-transparent border-none text-[var(--po-text-subtle)] enabled:hover:text-[var(--po-text)] disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer text-sm px-1 transition-colors inline-flex items-center justify-center gap-2"
                >
                  {loading === 'resend' && <Dots size="xs" />}
                  {resendCooldown > 0
                    ? `Resend code in ${formatCooldown(resendCooldown)}`
                    : loading === 'resend'
                      ? 'Sending…'
                      : "Didn't get a code? Resend"}
                </button>
              </div>
            </div>
          )}

          {/* ── Sign Up View ── */}
          {view === 'signup' && (
            <div className="animate-fade-in">
              <div className="mb-6 text-center">
                <h2 className="text-xl font-semibold text-[var(--po-text)]">Create your account</h2>
                <p className="mt-1 text-sm text-[var(--po-text-muted)]">{email}</p>
              </div>

              <form onSubmit={handleSignUp} className="flex flex-col gap-3">
                <InputField
                  label="Password"
                  type="password"
                  value={password}
                  onChange={setPassword}
                  placeholder="Create a password"
                  disabled={disabled}
                  minLength={6}
                  autoFocus
                />
                <SubmitButton disabled={disabled} loading={loading === 'signup'}>
                  {loading === 'signup' ? 'Creating account…' : 'Create Account'}
                </SubmitButton>
              </form>

              <Feedback error={error} message={message} />
            </div>
          )}

          {/* ── Forgot Password View ── */}
          {view === 'forgot' && (
            <div className="animate-fade-in">
              <div className="mb-6 text-center">
                <h2 className="text-xl font-semibold text-[var(--po-text)]">Reset your password</h2>
                <p className="mt-1 text-sm text-[var(--po-text-muted)]">{email}</p>
              </div>

              <form onSubmit={handleForgotPassword} className="flex flex-col gap-3">
                <SubmitButton disabled={disabled} loading={loading === 'forgot'}>
                  {loading === 'forgot' ? 'Sending…' : 'Send Reset Link'}
                </SubmitButton>
              </form>

              <Feedback error={error} message={message} />
            </div>
          )}

          {/* Terms */}
          <p className="text-[11px] text-[var(--po-text-subtle)] text-center mt-4">
            By continuing you agree to our Terms and Privacy Policy.
          </p>
        </div>
      </div>
    </div>
  );
}

/* ─── Shared UI Components ─── */

function ProviderButton({
  icon, label, loadingLabel, isLoading, disabled, onClick,
}: {
  icon: React.ReactNode;
  label: string;
  loadingLabel?: string;
  isLoading?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="relative w-full h-10 px-4 rounded-md border border-[var(--po-border)] bg-[var(--po-control)] text-[var(--po-text)] cursor-pointer text-sm font-medium transition-all hover:bg-[var(--po-control-hover)] hover:border-[var(--po-border-strong)] disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center"
    >
      <span className="absolute left-4 top-1/2 -translate-y-1/2 inline-flex h-5 w-5 items-center justify-center">
        {icon}
      </span>
      <span>{isLoading ? loadingLabel : label}</span>
    </button>
  );
}

function AuthDivider() {
  return (
    <div className="my-5 flex items-center gap-3 text-xs font-medium text-[var(--po-text-subtle)]">
      <div className="h-px flex-1 bg-[var(--po-border)]" />
      <span>or</span>
      <div className="h-px flex-1 bg-[var(--po-border)]" />
    </div>
  );
}

function InputField({
  label, type, value, onChange, placeholder, disabled, minLength, autoFocus,
}: {
  label: string;
  type: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  disabled?: boolean;
  minLength?: number;
  autoFocus?: boolean;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-[var(--po-text-muted)] mb-1.5">{label}</label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        required
        disabled={disabled}
        minLength={minLength}
        autoFocus={autoFocus}
        className="w-full h-10 px-3 rounded-md border border-[var(--po-border-strong)] bg-[var(--po-inset)] text-[var(--po-text)] text-sm outline-none box-border transition-colors focus:border-[var(--po-focus-ring)] placeholder:text-[var(--po-text-disabled)]"
      />
    </div>
  );
}

function SubmitButton({
  disabled,
  loading,
  children,
}: {
  disabled?: boolean;
  /** When true, prepends a <Dots /> spinner inside the button so the
   *  loading state visually matches the rest of the product (button
   *  spinners use Dots — see components/loading). */
  loading?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="submit"
      disabled={disabled}
      className="w-full h-10 px-4 rounded-md border-none bg-[var(--po-text)] text-[var(--po-text-inverse)] cursor-pointer text-sm font-semibold transition-all hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
    >
      {loading && <Dots size="xs" />}
      {children}
    </button>
  );
}

function BackButton({ onClick, label = 'All sign in options' }: { onClick: () => void; label?: string }) {
  return (
    <button
      onClick={onClick}
      className="flex h-[30px] items-center gap-1.5 bg-transparent border-none text-[var(--po-text-subtle)] hover:text-[var(--po-text)] cursor-pointer text-sm px-1 transition-colors self-start"
    >
      <ArrowLeftIcon />
      <span>{label}</span>
    </button>
  );
}

function OtpInput({
  value, onChange, disabled, autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  const handleChange = (raw: string) => {
    // Strip non-digits and clamp to 6 chars (handles paste of "123 456" etc.)
    const digits = raw.replace(/\D/g, '').slice(0, 6);
    onChange(digits);
  };
  return (
    <div>
      <label className="block text-sm font-medium text-[var(--po-text-muted)] mb-1.5">
        Verification code
      </label>
      <input
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="\d{6}"
        maxLength={6}
        value={value}
        onChange={e => handleChange(e.target.value)}
        placeholder="123456"
        required
        disabled={disabled}
        autoFocus={autoFocus}
        className="w-full h-12 px-3 rounded-md border border-[var(--po-border-strong)] bg-[var(--po-inset)] text-[var(--po-text)] text-center text-2xl font-sans tracking-[0.5em] outline-none box-border transition-colors focus:border-[var(--po-focus-ring)] placeholder:text-[var(--po-border-strong)]"
      />
    </div>
  );
}

function formatCooldown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function Feedback({ error, message }: { error: string | null; message: string | null }) {
  if (!error && !message) return null;
  return (
    <div className="mt-3">
      {error && (
        <div className="text-[var(--po-danger)] text-sm text-center px-3 py-2 bg-[color-mix(in_srgb,var(--po-danger)_10%,transparent)] rounded-lg">
          {error}
        </div>
      )}
      {message && (
        <div className="text-[var(--po-success)] text-sm text-center px-3 py-2 bg-[color-mix(in_srgb,var(--po-success)_10%,transparent)] rounded-lg">
          {message}
        </div>
      )}
    </div>
  );
}

/* ─── Icons ─── */

function GoogleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 533.5 544.3" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path fill="#4285f4" d="M533.5 278.4c0-17.6-1.6-34.4-4.6-50.4H272v95.3h147c-6.4 34.6-25.8 63.9-55 83.6l89 69.4c51.8-47.7 80.5-118 80.5-198z"/>
      <path fill="#34a853" d="M272 544.3c74.7 0 137.5-24.8 183.3-67.4l-89-69.4c-24.7 16.6-56.3 26.3-94.3 26.3-72.5 0-134-49-155.9-114.9l-92 71.6c41.6 82.5 127.1 153.8 247.9 153.8z"/>
      <path fill="#fbbc04" d="M116.1 318.9c-10-29.8-10-62.1 0-91.9l-92-71.6C4 211 0 240.9 0 272.4s4 61.4 24.1 116.9l92-70.4z"/>
      <path fill="#ea4335" d="M272 107.7c39.7-.6 77.6 14.7 105.8 42.9l77.5-77.5C395.1 24 334.2 0 272 0 151.2 0 65.7 71.3 24.1 155.5l92 71.6C138 161.3 199.5 107.7 272 107.7z"/>
    </svg>
  );
}

function GithubIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">
      <path d="M12 1C6 1 1.5 5.5 1.5 11.5c0 4.6 3 8.5 7.2 9.9.5.1.7-.2.7-.5v-1.9c-2.9.6-3.5-1.2-3.5-1.2-.5-1.2-1.2-1.6-1.2-1.6-1-.7.1-.7.1-.7 1.1.1 1.7 1.1 1.7 1.1 1 1.7 2.6 1.2 3.2.9.1-.7.4-1.2.7-1.5-2.4-.3-4.9-1.2-4.9-5.3 0-1.2.4-2.1 1.1-2.9-.1-.3-.5-1.4.1-2.9 0 0 .9-.3 3 .1 1-.3 2-.4 3.1-.4s2.1.1 3.1.4c2.1-1.4 3-.1 3-.1.6 1.5.2 2.6.1 2.9.7.8 1.1 1.7 1.1 2.9 0 4.1-2.6 5.1-5 5.4.4.3.7 1 .7 2v3c0 .3.2.6.7.5 4.2-1.4 7.2-5.3 7.2-9.9C22.5 5.5 18 1 12 1z"/>
    </svg>
  );
}

function ArrowLeftIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}
