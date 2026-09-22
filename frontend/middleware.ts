import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { createServerClient } from '@supabase/ssr';
import { getServerSupabaseUrl, getSupabaseAnonKey } from '@/lib/server-env';

const LOCALES = new Set(['en', 'zh-CN']);

function detectLocale(request: NextRequest): string {
  const cookie = request.cookies.get('NEXT_LOCALE')?.value;
  if (cookie && LOCALES.has(cookie)) return cookie;
  const accept = request.headers.get('accept-language') ?? '';
  if (accept.toLowerCase().includes('zh')) return 'zh-CN';
  return 'en';
}

/**
 * Next.js Middleware - 统一处理认证和路由保护
 *
 * 优势：
 * 1. 服务端执行，无客户端闪烁
 * 2. 统一入口，避免每个页面重复 AuthGuard
 * 3. 更快的重定向（无需等待 JS 加载）
 */
export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // 创建 response 用于后续可能的 cookie 操作
  let response = NextResponse.next({
    request: {
      headers: request.headers,
    },
  });

  // 创建 Supabase 服务端客户端
  const supabase = createServerClient(
    getServerSupabaseUrl(),
    getSupabaseAnonKey(),
    {
      cookies: {
        get(name: string) {
          return request.cookies.get(name)?.value;
        },
        set(name: string, value: string, options: any) {
          request.cookies.set({ name, value, ...options });
          response = NextResponse.next({
            request: {
              headers: request.headers,
            },
          });
          response.cookies.set({ name, value, ...options });
        },
        remove(name: string, options: any) {
          request.cookies.set({ name, value: '', ...options });
          response = NextResponse.next({
            request: {
              headers: request.headers,
            },
          });
          response.cookies.set({ name, value: '', ...options });
        },
      },
    }
  );

  // 获取当前 session
  const {
    data: { session },
  } = await supabase.auth.getSession();

  // ============================================
  // 路由规则
  // ============================================

  // 1. 首页 "/" → 重定向到 canonical organization home.
  // Avoid the redirect-only `/projects` route; it can make the app
  // shell update before the page segment catches up during navigation.
  if (pathname === '/') {
    return NextResponse.redirect(new URL('/home', request.url));
  }

  // 2. 已登录用户访问 /login → 重定向到 canonical organization home,
  //    UNLESS they came in with a ``redirect=`` param (e.g. from the
  //    invite-accept flow that bounces through login). Honor the param
  //    so an in-flight accept lands the user where they were going,
  //    not at /home.
  if (session && pathname === '/login') {
    const desktopState = request.nextUrl.searchParams.get('desktop_state');
    if (request.nextUrl.searchParams.get('client') === 'desktop' && desktopState) {
      response.headers.set('x-next-intl-locale', detectLocale(request));
      return response;
    }
    const dest = request.nextUrl.searchParams.get('redirect');
    if (dest && dest.startsWith('/') && !dest.startsWith('//') && !dest.startsWith('/\\')) {
      return NextResponse.redirect(new URL(dest, request.url));
    }
    return NextResponse.redirect(new URL('/home', request.url));
  }

  // 3. 未登录用户访问受保护页面 → 重定向到 /login (with ``redirect=``
  //    so the original path is preserved through the auth bounce).
  //    ``/invite/*`` is intentionally NOT in this list — the invite
  //    page is reachable unauthenticated and handles the auth gate
  //    itself, which avoids a middleware-driven loop when the session
  //    cookie is in mid-refresh.
  const protectedRoutes = ['/projects', '/tools', '/connect', '/mcp', '/etl', '/home', '/settings', '/billing', '/team', '/tools-and-server'];
  const isProtectedRoute = protectedRoutes.some(
    route => pathname === route || pathname.startsWith(route + '/')
  );

  if (!session && isProtectedRoute) {
    const loginUrl = new URL('/login', request.url);
    // Preserve the original path + query so the user lands where they
    // intended after authenticating. Skip when the destination would
    // be /login itself (no self-redirect loop).
    const dest = pathname + (request.nextUrl.search || '');
    if (dest && dest !== '/login') {
      loginUrl.searchParams.set('redirect', dest);
    }
    return NextResponse.redirect(loginUrl);
  }

  // Tell next-intl which locale to use for this request
  const locale = detectLocale(request);
  response.headers.set('x-next-intl-locale', locale);

  return response;
}

/**
 * Matcher 配置 - 指定 middleware 生效的路由
 *
 * 排除：
 * - /_next (Next.js 内部资源)
 * - /api (API 路由)
 * - /auth/callback (Supabase OAuth 回调)
 * - /auth/confirm (邮件验证回调)
 * - /oauth/callback (Notion OAuth 回调)
 * - 静态文件
 */
export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$|auth/callback|auth/confirm|oauth/callback|api).*)',
  ],
};
