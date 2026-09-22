'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useTransition, type ReactNode } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { parseWorkspaceLocation } from './routes';

type Guard = (target: string) => boolean | string;
type Options = { replace?: boolean; scroll?: boolean; from?: string };
type Navigation = {
  navigate: (href: string, options?: Options) => boolean;
  prefetch: (href: string) => void;
  register: (guard: Guard) => () => void;
};
const NavigationContext = createContext<Navigation | null>(null);
const PendingContext = createContext<string | null>(null);

/** One coordinator above both the project rail and project routes. The router
 * owns location; this boundary owns only leave checks and transition feedback. */
export function WorkspaceNavigationProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams()?.toString() ?? '';
  const currentHref = pathname + (search ? `?${search}` : '');
  const currentRef = useRef(currentHref);
  currentRef.current = currentHref;
  const guards = useRef(new Set<Guard>());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [isPending, startTransition] = useTransition();
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    if (!isPending) setTarget(null);
  }, [isPending, currentHref]);

  const register = useCallback((guard: Guard) => {
    guards.current.add(guard);
    return () => { guards.current.delete(guard); };
  }, []);
  const navigate = useCallback((href: string, options: Options = {}) => {
    // Application routes are local absolute paths. Never pass arbitrary URL
    // schemes to router.push, including values from compatibility queries.
    if (!mounted.current) return false;
    if (options.from && options.from !== currentRef.current) return false;
    if (!href.startsWith('/') || href.startsWith('//')) throw new Error('Expected an application route');
    if (href !== currentRef.current) {
      const warnings = new Set<string>();
      for (const guard of guards.current) {
        const result = guard(href);
        if (result === false) return false;
        if (typeof result === 'string') warnings.add(result);
      }
      if (warnings.size && !window.confirm([...warnings].join('\n\n'))) return false;
    }
    // Re-issue even the current URL: it may be the user's reversal of an
    // unfinished transition. Do not leave an earlier target stuck pending.
    setTarget(href);
    startTransition(() => {
      if (options.replace) router.replace(href, { scroll: options.scroll ?? false });
      else router.push(href, { scroll: options.scroll ?? false });
    });
    return true;
  }, [router]);
  const prefetch = useCallback((href: string) => router.prefetch(href), [router]);
  const value = useMemo(() => ({ navigate, prefetch, register }), [navigate, prefetch, register]);
  return <NavigationContext.Provider value={value}><PendingContext.Provider value={isPending ? target : null}>{children}</PendingContext.Provider></NavigationContext.Provider>;
}

export function useOptionalWorkspaceNavigation() { return useContext(NavigationContext); }
export function useWorkspaceNavigation() {
  const value = useOptionalWorkspaceNavigation();
  if (!value) throw new Error('Workspace navigation requires WorkspaceNavigationProvider');
  return value;
}
export function usePendingNavigation() { return useContext(PendingContext); }
/** Command-style counterpart to WorkspaceLink, with the same leave policy. */
export function useWorkspaceRouter() {
  const navigation = useWorkspaceNavigation();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams()?.toString() ?? '';
  const from = pathname + (search ? `?${search}` : '');
  return useMemo(() => ({
    ...router,
    push: (href: string, options?: { scroll?: boolean }) => navigation.navigate(href, { ...options, from }),
    replace: (href: string, options?: { scroll?: boolean }) => navigation.navigate(href, { ...options, replace: true, from }),
    prefetch: navigation.prefetch,
  }), [navigation, router, from]);
}
export function useWorkspaceLocation() {
  const pathname = usePathname();
  const search = useSearchParams()?.toString() ?? '';
  return useMemo(() => parseWorkspaceLocation(pathname ?? '', search), [pathname, search]);
}

export function useNavigationGuard(guard: Guard | null) {
  const navigation = useOptionalWorkspaceNavigation();
  const guardRef = useRef(guard);
  guardRef.current = guard;
  useEffect(() => navigation?.register(target => guardRef.current?.(target) ?? true), [navigation]);
}
