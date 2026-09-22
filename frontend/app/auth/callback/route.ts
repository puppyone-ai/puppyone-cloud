import { NextResponse } from 'next/server';
import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';
import {
  getServerApiBaseUrl,
  getServerSupabaseUrl,
  getSupabaseAnonKey,
  getRequestOrigin,
  isSafeRelativePath,
} from '@/lib/server-env';

/**
 * Supabase Auth Callback - Route Handler (服务端)
 *
 * 仅处理 OAuth 登录回调（Google / GitHub）。
 * 邮件类流程（注册确认、密码重置）走 /auth/confirm。
 */
export async function GET(request: Request) {
  const requestUrl = new URL(request.url);
  const origin = getRequestOrigin(request);
  const code = requestUrl.searchParams.get('code');
  const apiUrl = getServerApiBaseUrl();

  if (!code) {
    return NextResponse.redirect(`${origin}/login`);
  }

  const cookieStore = await cookies();

  const supabase = createServerClient(
    getServerSupabaseUrl(),
    getSupabaseAnonKey(),
    {
      cookies: {
        get(name: string) {
          return cookieStore.get(name)?.value;
        },
        set(name: string, value: string, options: any) {
          cookieStore.set({ name, value, ...options });
        },
        remove(name: string, options: any) {
          cookieStore.delete({ name, ...options });
        },
      },
    }
  );

  const { data, error } = await supabase.auth.exchangeCodeForSession(code);

  if (error || !data.session) {
    console.error('Auth callback exchange failed:', error?.message, error?.status);
    return NextResponse.redirect(`${origin}/login?error=auth_callback_failed`);
  }

  // First-time sign-in seeds a "Get Started" demo project; if the backend
  // returns its id we land the user inside it instead of an empty dashboard.
  let demoProjectId: string | null = null;
  try {
    const token = data.session.access_token;
    const initRes = await fetch(`${apiUrl}/api/v1/auth/initialize`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (initRes.ok) {
      const initJson = await initRes.json();
      demoProjectId = initJson?.data?.demo_project_id ?? null;
    }
  } catch (e) {
    console.error('Auth initialization failed:', e);
  }

  // If the caller explicitly passed ?next=, honour it. Otherwise prefer
  // the demo project so first-time users see populated content; fall back
  // to /home for returning users (or if seeding failed).
  // Only honour ?next= when it's a safe same-origin relative path;
  // otherwise a value like `//evil.com` would escape the origin in the
  // `${origin}${target}` concatenation below (open redirect).
  const explicitNext = requestUrl.searchParams.get('next');
  const target = isSafeRelativePath(explicitNext)
    ? explicitNext
    : demoProjectId
      ? `/projects/${demoProjectId}/data`
      : '/home';

  return NextResponse.redirect(`${origin}${target}`);
}
