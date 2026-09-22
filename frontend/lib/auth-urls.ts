/**
 * Centralized auth redirect URL configuration.
 *
 * All Supabase auth calls that trigger emails (signUp, resetPassword, OTP)
 * MUST use these helpers so the email links always point to the correct app.
 *
 * Priority chain for resolving the app origin:
 *   1. NEXT_PUBLIC_APP_URL  (explicit, recommended for production)
 *   2. window.location.origin (client-side runtime origin)
 *
 * Supabase validates redirect URLs against the project's "Redirect URLs"
 * allowlist. Make sure to add each environment's URLs there:
 *   - ${NEXT_PUBLIC_APP_URL}/auth/confirm
 *   - ${NEXT_PUBLIC_APP_URL}/auth/callback
 */

function getAppOrigin(): string {
  const explicit = process.env.NEXT_PUBLIC_APP_URL;
  if (explicit) return normalizeAppOrigin(explicit);

  if (typeof window !== 'undefined') return window.location.origin;

  throw new Error('NEXT_PUBLIC_APP_URL must configure the frontend origin.');
}

function normalizeAppOrigin(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error('NEXT_PUBLIC_APP_URL must be a valid frontend origin.');
  }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (
    (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback))
    || url.username
    || url.password
    || (url.pathname !== '/' && url.pathname !== '')
    || url.search
    || url.hash
  ) {
    throw new Error(
      'NEXT_PUBLIC_APP_URL must be an HTTPS origin, or a loopback HTTP origin for local development.',
    );
  }
  return url.origin;
}

/** Where Supabase should redirect after email confirmation (signup / email change). */
export function getEmailConfirmUrl(): string {
  return `${getAppOrigin()}/auth/confirm`;
}

/**
 * Where Supabase should redirect after password-reset email link click.
 *
 * IMPORTANT: returns a clean URL with NO query string. The Supabase email
 * template appends `?token_hash=...&type=recovery&next=/reset-password`
 * itself (via `{{ .RedirectTo }}?...` in the template), so this URL must
 * end without a `?` to keep the resulting link well-formed across both
 * localhost dev and production.
 */
export function getPasswordResetRedirectUrl(): string {
  return `${getAppOrigin()}/auth/confirm`;
}

/** Where Supabase should redirect after OAuth provider authorization. */
export function getOAuthCallbackUrl(): string {
  return `${getAppOrigin()}/auth/callback`;
}
