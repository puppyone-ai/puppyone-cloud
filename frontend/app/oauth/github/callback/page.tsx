'use client';

import { useEffect, useState, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { githubCallback } from '@/lib/oauthApi';
import { PageLoading } from '@/components/loading';

const OAUTH_RETURN_KEY = 'oauth_return_to';

/** Close the window if we were a popup, otherwise redirect back to the
 *  page that started the OAuth flow.
 *
 *  Background: ``window.close()`` only works on windows that JavaScript
 *  itself opened (via ``window.open``). The full-page-navigation flow
 *  (``connectGithub`` → ``window.location.href = …``) cannot be closed
 *  programmatically, so the user used to see a stuck "This window will
 *  close automatically…" page after a successful connection.
 *
 *  We try ``close()`` first (popup case wins) and after a short grace
 *  period redirect back to the URL we stashed in ``sessionStorage``
 *  before kicking off the dance. Falls back to ``/home`` for the cold
 *  case where someone hits ``/oauth/github/callback`` directly. */
function _dismissOrReturn(): void {
  try {
    globalThis.close();
  } catch {
    /* popup-blocker / not-a-popup: ignore */
  }
  setTimeout(() => {
    if (globalThis.window === undefined || globalThis.closed) return;
    let returnTo: string | null = null;
    try {
      returnTo = sessionStorage.getItem(OAUTH_RETURN_KEY);
      sessionStorage.removeItem(OAUTH_RETURN_KEY);
    } catch {
      /* sessionStorage may be blocked */
    }
    globalThis.location.href = returnTo || '/home';
  }, 200);
}

function GithubCallbackContent() {
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading');
  const [message, setMessage] = useState<string>('');

  useEffect(() => {
    const handleCallback = async () => {
      const code = searchParams?.get('code');
      // CSRF nonce — backend's OAuthStateRepository.consume() requires it.
      const state = searchParams?.get('state') || undefined;
      const error = searchParams?.get('error');

      if (error) {
        setStatus('error');
        setMessage(`Authorization failed: ${error}`);
        setTimeout(_dismissOrReturn, 3000);
        return;
      }

      if (!code) {
        setStatus('error');
        setMessage('No authorization code received');
        setTimeout(_dismissOrReturn, 3000);
        return;
      }

      try {
        const result = await githubCallback(code, state);

        if (result.success) {
          setStatus('success');
          setMessage(result.message || 'Successfully connected to GitHub!');
          setTimeout(_dismissOrReturn, 2000);
        } else {
          setStatus('error');
          setMessage(result.message || 'Failed to connect to GitHub');
          setTimeout(_dismissOrReturn, 3000);
        }
      } catch (err) {
        setStatus('error');
        setMessage(err instanceof Error ? err.message : 'An unexpected error occurred');
        setTimeout(_dismissOrReturn, 3000);
      }
    };

    handleCallback();
  }, [searchParams]);

  return (
    <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      minHeight: '100vh',
      background: 'var(--po-inset)',
      color: 'var(--po-text)',
    }}>
      <div style={{ textAlign: 'center', maxWidth: 400, padding: 32 }}>
        {status === 'loading' && (
          <>
            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 16 }}>
              Connecting to GitHub...
        </div>
            <div style={{ fontSize: 13, color: 'var(--po-text-muted)' }}>
              Please wait while we complete the authorization
            </div>
          </>
          )}

          {status === 'success' && (
          <>
            <div style={{ fontSize: 40, marginBottom: 16, color: 'var(--po-success)' }}>✓</div>
            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8, color: 'var(--po-success)' }}>
              Success!
              </div>
            <div style={{ fontSize: 13, color: 'var(--po-text-muted)' }}>{message}</div>
            <div style={{ fontSize: 12, color: 'var(--po-text-subtle)', marginTop: 16 }}>
              This window will close automatically...
            </div>
          </>
          )}

          {status === 'error' && (
          <>
            <div style={{ fontSize: 40, marginBottom: 16, color: 'var(--po-danger)' }}>✗</div>
            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8, color: 'var(--po-danger)' }}>
              Access Setup Failed
              </div>
            <div style={{ fontSize: 13, color: 'var(--po-text-muted)' }}>{message}</div>
            <div style={{ fontSize: 12, color: 'var(--po-text-subtle)', marginTop: 16 }}>
              This window will close automatically...
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function GithubCallbackPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <GithubCallbackContent />
    </Suspense>
  );
}
