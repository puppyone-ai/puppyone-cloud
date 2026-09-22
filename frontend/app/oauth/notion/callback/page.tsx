'use client';

import { useEffect, useState, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { notionCallback } from '@/lib/oauthApi';
import { PageLoading } from '@/components/loading';

function NotionCallbackContent() {
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading');
  const [message, setMessage] = useState<string>('');

  useEffect(() => {
    const handleCallback = async () => {
      const code = searchParams?.get('code');
      // CSRF nonce — backend's OAuthStateRepository.consume() requires it.
      // Note: this used to be a `provider` query-param hack stuffed into
      // the body's `state` field — but the backend now uses `state` for
      // CSRF validation, so we read the real OAuth `state` instead.
      const state = searchParams?.get('state') || undefined;
      const error = searchParams?.get('error');

      if (error) {
        setStatus('error');
        setMessage(`Authorization failed: ${error}`);
        setTimeout(() => window.close(), 3000);
        return;
      }

      if (!code) {
        setStatus('error');
        setMessage('No authorization code received');
        setTimeout(() => window.close(), 3000);
        return;
      }

      try {
        const result = await notionCallback(code, state);

        if (result.success) {
          setStatus('success');
          setMessage(result.message || 'Successfully connected to Notion!');
          setTimeout(() => window.close(), 2000);
        } else {
          setStatus('error');
          setMessage(result.message || 'Failed to connect to Notion');
          setTimeout(() => window.close(), 3000);
        }
      } catch (err) {
        setStatus('error');
        setMessage(err instanceof Error ? err.message : 'An unexpected error occurred');
        setTimeout(() => window.close(), 3000);
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
              Connecting to Notion...
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

export default function NotionCallbackPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <NotionCallbackContent />
    </Suspense>
  );
}
