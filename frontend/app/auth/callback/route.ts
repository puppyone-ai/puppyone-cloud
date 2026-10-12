import { NextResponse } from 'next/server';
import { createAuthServerClient } from '@/features/auth/supabase/server-client';
import { cookies } from 'next/headers';
import {
  getServerApiBaseUrl,
  getRequestOrigin,
} from '@/lib/server-env';
import {
  loginReturnPath,
  safeNext,
} from '@/features/auth/login-intent';
import { desktopCallbackTarget } from '@/features/auth/supabase/desktop-callback';
import { completeWebLogin } from '@/features/auth/login-coordinator';

export async function GET(request: Request) {
  const url = new URL(request.url);
  const origin = getRequestOrigin(request);
  const redirect = (path: string) => {
    const response = NextResponse.redirect(new URL(path, origin));
    response.headers.set('Cache-Control', 'private, no-store');
    response.headers.set('Referrer-Policy', 'no-referrer');
    return response;
  };
  if (url.searchParams.has('desktop_state') || url.searchParams.has('desktop_callback')) {
    try { return redirect(desktopCallbackTarget(url)); }
    catch { return redirect('/login?error=auth_request_expired'); }
  }
  const intent = { kind: 'web' as const, next: safeNext(url.searchParams.get('next')) };
  const login = new URL(loginReturnPath(intent), origin);
  const code = url.searchParams.get('code');
  if (!code || url.searchParams.has('error')) {
    login.searchParams.set(
      'error',
      url.searchParams.get('error') === 'access_denied' ? 'auth_cancelled' : 'auth_callback_failed'
    );
    return redirect(login.pathname + login.search);
  }
  const cookieStore = await cookies();
  const supabase = createAuthServerClient({
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: values =>
        values.forEach(({ name, value, options }) => cookieStore.set(name, value, options)),
    },
  });
  try {
    const { data, error } = await supabase.auth.exchangeCodeForSession(code);
    if (error || !data.session) throw new Error('exchange failed');
    const target = await completeWebLogin(
      data.session,
      intent.next,
      `${getServerApiBaseUrl()}/api/v1`
    );
    return redirect(target);
  } catch {
    login.searchParams.set('error', 'auth_callback_failed');
    return redirect(login.pathname + login.search);
  }
}
