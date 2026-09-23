'use client';

import React, { createContext, useContext, useMemo, useState, useEffect, useRef } from 'react';
import { usePathname } from 'next/navigation';
import { createBrowserLogin } from '@/features/auth/supabase/browser-login';
import type { LoginIntent, SignInProvider } from '@/features/auth/login-intent';
import { loginReturnPath } from '@/features/auth/login-intent';
import { Session, SupabaseClient } from '@supabase/supabase-js';
import { getEmailConfirmUrl, getPasswordResetRedirectUrl } from '@/lib/auth-urls';

type AuthContextValue = {
  supabase: SupabaseClient | null;
  session: Session | null;
  userId: string | null;
  isAuthReady: boolean;
  authError: string | null;
  availableProviders: SignInProvider[];
  loginIntent: LoginIntent | null;
  finishSignIn: () => Promise<string>;
  signInWithProvider: (provider: 'google' | 'github') => Promise<void>;
  signInWithOtp: (email: string) => Promise<void>;
  signInWithEmail: (email: string, password: string) => Promise<void>;
  signUpWithEmail: (
    email: string,
    password: string
  ) => Promise<{ needsEmailConfirmation: boolean }>;
  verifyEmailOtp: (email: string, token: string) => Promise<{ accessToken: string }>;
  resendConfirmation: (email: string) => Promise<void>;
  resetPassword: (email: string) => Promise<void>;
  updatePassword: (newPassword: string) => Promise<void>;
  signOut: () => Promise<void>;
  getAccessToken: () => Promise<string | null>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function SupabaseAuthProvider({ children }: { children: React.ReactNode }) {
  const [supabase, setSupabase] = useState<SupabaseClient | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [isAuthReady, setIsAuthReady] = useState(false);

  const pathname = usePathname();
  const runtimeRef = useRef<{ path: string; login: ReturnType<typeof createBrowserLogin> } | null>(
    null
  );
  const [authError, setAuthError] = useState<string | null>(null);
  const [availableProviders, setAvailableProviders] = useState<SignInProvider[]>([]);
  const [loginIntent, setLoginIntent] = useState<LoginIntent | null>(null);

  useEffect(() => {
    // Connector authorization codes belong to the connector, not Supabase Auth.
    if (window.location.pathname.startsWith('/oauth/')) {
      setIsAuthReady(true);
      return;
    }
    let active = true;
    setIsAuthReady(false);
    setAuthError(null);
    let unsubscribe = () => {};
    try {
      // Keep the same coordinator through Strict Mode's effect replay. In
      // particular, an OAuth authorization code must only be exchanged once.
      const path = window.location.pathname;
      if (!runtimeRef.current || runtimeRef.current.path !== path) {
        runtimeRef.current = {
          path,
          login: createBrowserLogin(new URL(window.location.href), window.sessionStorage),
        };
      }
      const login = runtimeRef.current.login;
      // The browser adapter already captured the code; remove transient
      // credentials from history at this UI boundary before further requests.
      if (new URL(window.location.href).searchParams.has('auth_code')) {
        window.history.replaceState(window.history.state, '', loginReturnPath(login.intent));
      }
      setSupabase(login.client);
      setLoginIntent(login.intent);
      const subscription = login.client.auth.onAuthStateChange((_event, nextSession) => {
        if (active) setSession(nextSession);
      });
      unsubscribe = () => subscription.data.subscription.unsubscribe();
      void login
        .initialize()
        .then(async providers => {
          const { data, error } = await login.client.auth.getSession();
          if (error) throw error;
          if (!active) return;
          setAvailableProviders(providers);
          setSession(data.session);
          setIsAuthReady(true);
        })
        .catch(error => {
          if (!active) return;
          setAuthError(
            error instanceof Error
              ? error.message
              : 'Unable to prepare sign-in. Please reload this page or start sign-in again.'
          );
          setIsAuthReady(false);
        });
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Unable to prepare sign-in.');
    }
    return () => {
      active = false;
      unsubscribe();
    };
  }, [pathname]);

  const signInWithProvider = async (provider: 'google' | 'github') => {
    if (!isAuthReady || !runtimeRef.current) throw new Error('Auth client is not ready.');
    await runtimeRef.current.login.signInWithProvider(provider);
  };

  const finishSignIn = async () => {
    if (!isAuthReady || !runtimeRef.current) throw new Error('Auth client is not ready.');
    return runtimeRef.current.login.finish();
  };

  const signInWithOtp = async (email: string) => {
    if (!supabase) {
      throw new Error('Supabase is not configured');
    }
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: {
        emailRedirectTo: getEmailConfirmUrl(),
      },
    });
    if (error) {
      throw error;
    }
  };

  const signInWithEmail = async (email: string, password: string) => {
    if (!supabase) {
      throw new Error('Supabase is not configured');
    }
    const { error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });
    if (error) {
      throw error;
    }
  };

  const signUpWithEmail = async (email: string, password: string) => {
    if (!supabase) {
      throw new Error('Supabase is not configured');
    }

    const { data, error } = await supabase.auth.signUp({
      email,
      password,
    });

    if (error) {
      throw error;
    }

    // 检查是否需要邮箱验证
    // 如果 session 为 null 且 user 存在，说明需要验证邮箱
    const needsEmailConfirmation = !data.session && !!data.user;

    return { needsEmailConfirmation };
  };

  const verifyEmailOtp = async (email: string, token: string) => {
    if (!supabase) {
      throw new Error('Supabase is not configured');
    }
    const { data, error } = await supabase.auth.verifyOtp({
      email,
      token,
      type: 'email',
    });
    if (error) {
      throw error;
    }
    if (!data.session) {
      throw new Error('Verification succeeded but no session was returned.');
    }
    return { accessToken: data.session.access_token };
  };

  const resendConfirmation = async (email: string) => {
    if (!supabase) {
      throw new Error('Supabase is not configured');
    }
    const { error } = await supabase.auth.resend({
      type: 'signup',
      email,
    });
    if (error) {
      throw error;
    }
  };

  const resetPassword = async (email: string) => {
    if (!supabase) {
      throw new Error('Supabase is not configured');
    }

    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: getPasswordResetRedirectUrl(),
    });

    if (error) {
      throw error;
    }
  };

  const updatePassword = async (newPassword: string) => {
    if (!supabase) {
      throw new Error('Supabase is not configured');
    }
    const { error } = await supabase.auth.updateUser({ password: newPassword });
    if (error) {
      throw error;
    }
  };

  const signOut = async () => {
    // We hard-redirect to /login in ``finally`` even if Supabase fails
    // or hangs. Soft-routing via ``router.push`` leaves stale identity
    // state in memory in components that read it directly off
    // ``window`` / module-level caches; a full reload nukes that,
    // re-runs middleware against the now-cookieless request, and lands
    // the user on /login deterministically. The cookie-clear races
    // against the navigation but middleware re-checks the session
    // server-side, so the outcome is safe either way.
    try {
      if (supabase) {
        // Revoke this Supabase session, including its refresh token, without
        // revoking independent sessions such as Desktop. This makes a network
        // request; it is not merely a local cookie deletion.
        await supabase.auth.signOut({ scope: 'local' });
      }
    } catch (err) {
      console.error('signOut failed (continuing to /login anyway):', err);
    } finally {
      if (typeof window !== 'undefined') {
        window.location.href = '/login';
      }
    }
  };

  const getAccessToken = async (): Promise<string | null> => {
    if (!supabase) return null;
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  };

  const value = useMemo<AuthContextValue>(
    () => ({
      supabase,
      session,
      userId: session?.user?.id ?? null,
      isAuthReady,
      authError,
      availableProviders,
      loginIntent,
      finishSignIn,
      signInWithProvider,
      signInWithOtp,
      signInWithEmail,
      signUpWithEmail,
      verifyEmailOtp,
      resendConfirmation,
      resetPassword,
      updatePassword,
      signOut,
      getAccessToken,
    }),
    [supabase, session, isAuthReady, authError, availableProviders, loginIntent]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within SupabaseAuthProvider');
  return ctx;
}
