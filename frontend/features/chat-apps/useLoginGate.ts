'use client';

import { useEffect } from 'react';
import { useAuth } from '@/contexts/SupabaseAuthProvider';

/** Signed-out visitors go through the existing login and come back to this exact URL. */
export function useLoginGate() {
  const { session, isAuthReady } = useAuth();
  useEffect(() => {
    if (!isAuthReady || session) return;
    const here = `${window.location.pathname}${window.location.search}`;
    window.location.replace(`/login?redirect=${encodeURIComponent(here)}`);
  }, [isAuthReady, session]);
  return { session, isAuthReady, userId: session?.user?.id };
}
