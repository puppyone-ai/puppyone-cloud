'use client';

import React, { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import {
  joinProjectViaShareToken,
  type ProjectShareJoinResult,
} from '@/lib/projectsApi';
import { Dots } from '@/components/loading';

/**
 * Share-link landing page — `/share/{token}`.
 *
 * This is one of only two surfaces a person can meet *before* they have
 * an account with us (the other is `/invite/{token}`), so it is held to
 * a stricter contract than an in-app screen:
 *
 *   1. **It must never spin forever.** Every asynchronous dependency —
 *      auth bootstrap and the join request — is behind a watchdog that
 *      resolves into a readable, retryable state. The previous version
 *      rendered a bare "Loading share link…" whenever `status` was
 *      `pending`, which was also the state it sat in while signed out;
 *      if `isAuthReady` never flipped (auth bootstrap failure sets
 *      `authError` and leaves `isAuthReady === false` forever) the page
 *      had no exit at all.
 *   2. **It must say what it is before asking for anything.** A signed
 *      out visitor sees the invitation card and clicks through to sign
 *      in. We deliberately do not auto-bounce to `/login`: a redirect
 *      with nothing on screen is indistinguishable from a broken link,
 *      and it is the behaviour that made this page feel hung.
 *   3. **Every failure is attributable.** Invalid/rotated token, wrong
 *      organization, expired session and transport failure each get
 *      their own copy and their own recovery action.
 *
 * Visual contract: `docs/frontend/product-visual-system.md`. The card
 * mirrors `/login` (380px column, centred on `--po-inset`, logo above
 * the heading) so arriving here and continuing to sign-in reads as one
 * surface rather than two.
 */

/** Auth bootstrap is local work plus, at most, one token refresh. */
const AUTH_BOOTSTRAP_TIMEOUT_MS = 12_000;
/** Matches the API client's own request timeout, plus headroom. */
const JOIN_TIMEOUT_MS = 35_000;
/** Long enough to read "Joined X", short enough not to feel stalled. */
const SUCCESS_DWELL_MS = 900;

export default function ShareAcceptPage() {
  return (
    <Suspense fallback={<ShareShell>{null}</ShareShell>}>
      <ShareAcceptInner />
    </Suspense>
  );
}

type Failure = {
  /** Short, human headline. */
  title: string;
  /** One sentence of plain explanation. */
  detail: string;
  /** Offer a retry only when retrying can plausibly change the result. */
  retryable: boolean;
};

type Status =
  | { kind: 'booting' }
  | { kind: 'signed-out' }
  | { kind: 'joining' }
  | { kind: 'joined'; result: ProjectShareJoinResult }
  | { kind: 'failed'; failure: Failure };

function ShareAcceptInner() {
  const router = useRouter();
  const params = useParams<{ token: string }>();
  const token =
    typeof params?.token === 'string'
      ? params.token
      : Array.isArray(params?.token)
        ? params.token[0]
        : '';

  const { session, isAuthReady, authError, signOut } = useAuth();

  const [status, setStatus] = useState<Status>({ kind: 'booting' });
  const [attempt, setAttempt] = useState(0);

  // Read the router through a ref inside the join effect. Keeping it in
  // the dependency array makes the effect re-subscribe on every render
  // if the router object identity is ever unstable, and because the
  // handlers below publish a freshly built status object each time,
  // that degenerates into a render loop.
  const routerRef = useRef(router);
  routerRef.current = router;

  // The path is stable for the lifetime of this page; `/share/{token}`
  // has no query string we need to carry.
  const sharePath = token ? `/share/${encodeURIComponent(token)}` : '';
  const loginHref = `/login?redirect=${encodeURIComponent(sharePath)}`;

  // ── Watchdog: auth bootstrap ──────────────────────────────────────
  // `SupabaseAuthProvider` reports an unrecoverable bootstrap failure by
  // setting `authError` and *leaving* `isAuthReady` false. Nothing else
  // ever resolves that, so the page has to own the deadline.
  useEffect(() => {
    if (isAuthReady) return;
    const timer = setTimeout(() => {
      setStatus(current =>
        current.kind === 'booting'
          ? {
              kind: 'failed',
              failure: {
                title: "Couldn't start the session",
                detail:
                  'We could not reach the sign-in service. Check your connection and try again.',
                retryable: true,
              },
            }
          : current
      );
    }, AUTH_BOOTSTRAP_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [isAuthReady, attempt]);

  // Surface a *reported* bootstrap failure immediately rather than
  // making the visitor wait out the watchdog.
  useEffect(() => {
    if (!authError) return;
    setStatus(current =>
      current.kind === 'booting'
        ? {
            kind: 'failed',
            failure: {
              title: "Couldn't start the session",
              detail: authError,
              retryable: true,
            },
          }
        : current
    );
  }, [authError]);

  // ── Resolve the signed-out state explicitly ───────────────────────
  useEffect(() => {
    if (!isAuthReady || session) return;
    setStatus(current => (current.kind === 'booting' ? { kind: 'signed-out' } : current));
  }, [isAuthReady, session]);

  // ── Join ──────────────────────────────────────────────────────────
  //
  // The request is owned by a ref, not by the effect. An effect that
  // both starts a POST and aborts it from its cleanup is wrong here:
  // Strict Mode's mount/unmount/mount replay — and any re-render that
  // changes the `session` object identity, such as a token refresh —
  // would cancel the only in-flight attempt and, with a once-only
  // guard in place, never start another. That is precisely how a page
  // ends up spinning forever.
  //
  // Instead the effect *adopts* the current attempt's promise and only
  // owns its own subscription. The POST fires once per `attempt`; the
  // handlers can be attached as many times as React re-runs the effect.
  const inflightRef = useRef<{ attempt: number; promise: Promise<ProjectShareJoinResult> } | null>(
    null
  );
  useEffect(() => {
    if (!isAuthReady || !session) return;
    if (!token) {
      setStatus({ kind: 'failed', failure: INVALID_LINK });
      return;
    }

    if (inflightRef.current?.attempt !== attempt) {
      setStatus({ kind: 'joining' });
      inflightRef.current = { attempt, promise: joinProjectViaShareToken(token) };
    }
    const { promise } = inflightRef.current;

    let cancelled = false;
    let dwell: ReturnType<typeof setTimeout> | undefined;
    const watchdog = setTimeout(() => {
      if (cancelled) return;
      setStatus(current =>
        current.kind === 'joining'
          ? {
              kind: 'failed',
              failure: {
                title: 'This is taking too long',
                detail: 'The workspace did not respond. Try again in a moment.',
                retryable: true,
              },
            }
          : current
      );
    }, JOIN_TIMEOUT_MS);

    promise
      .then(result => {
        if (cancelled) return;
        setStatus({ kind: 'joined', result });
        // Land inside the project rather than on /home: the visible
        // outcome should be "I can see this project now".
        dwell = setTimeout(
          () => routerRef.current.replace(`/projects/${result.project_id}/data`),
          SUCCESS_DWELL_MS
        );
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setStatus({ kind: 'failed', failure: describeJoinFailure(error) });
      })
      .finally(() => clearTimeout(watchdog));

    return () => {
      cancelled = true;
      clearTimeout(watchdog);
      clearTimeout(dwell);
    };
  }, [isAuthReady, session, token, attempt]);

  const retry = useCallback(() => {
    setStatus({ kind: 'booting' });
    setAttempt(n => n + 1);
  }, []);

  const useDifferentAccount = useCallback(() => {
    // signOut hard-navigates to /login; it never resolves here.
    void signOut().catch(() => undefined);
  }, [signOut]);

  // ── Render ────────────────────────────────────────────────────────

  if (status.kind === 'booting') {
    return (
      <ShareShell>
        <Heading title="Opening shared project" />
        <Progress label="Preparing your session" />
      </ShareShell>
    );
  }

  if (status.kind === 'signed-out') {
    return (
      <ShareShell>
        <Heading
          title="You've been invited to a project"
          subtitle="Sign in to PuppyOne to open it. If you don't have an account yet you can create one with the same link."
        />
        <PrimaryLink href={loginHref}>Sign in to continue</PrimaryLink>
        <Footnote>{`You'll join as a viewer. The project owner controls access.`}</Footnote>
      </ShareShell>
    );
  }

  if (status.kind === 'joining') {
    return (
      <ShareShell>
        <Heading title="Opening shared project" />
        <Progress label="Adding you to the project" />
      </ShareShell>
    );
  }

  if (status.kind === 'joined') {
    const { newly_joined, project_name } = status.result;
    return (
      <ShareShell>
        <Heading
          title={newly_joined ? `Joined ${project_name}` : `You're already in ${project_name}`}
        />
        <Progress label="Taking you in" />
      </ShareShell>
    );
  }

  const { failure } = status;
  return (
    <ShareShell>
      <Heading title={failure.title} subtitle={failure.detail} />
      <div className="flex flex-col gap-2">
        {failure.retryable && <PrimaryButton onClick={retry}>Try again</PrimaryButton>}
        {session ? (
          <>
            <SecondaryButton onClick={() => router.replace('/home')}>
              Go to my workspace
            </SecondaryButton>
            <SecondaryButton onClick={useDifferentAccount}>
              Use a different account
            </SecondaryButton>
          </>
        ) : (
          <SecondaryLink href={loginHref}>Sign in</SecondaryLink>
        )}
      </div>
      {session?.user?.email && <Footnote>Signed in as {session.user.email}</Footnote>}
    </ShareShell>
  );
}

// ── Failure classification ──────────────────────────────────────────
//
// `apiRequest` throws an Error carrying `status` (HTTP) plus the
// backend's own message. Map the ones this endpoint can actually
// produce; fall back to the server's message rather than inventing one.

const INVALID_LINK: Failure = {
  title: 'This share link no longer works',
  detail:
    'It was either revoked by the project owner or replaced by a newer link. Ask them to send you a fresh one.',
  retryable: false,
};

function describeJoinFailure(error: unknown): Failure {
  const status = (error as { status?: number } | null)?.status;
  const message = error instanceof Error ? error.message : '';

  if (status === 404) return INVALID_LINK;

  if (status === 403) {
    return {
      title: 'This link is for another organization',
      detail:
        message ||
        "Share links widen access inside one organization. Ask an owner to invite you to the organization first, then open this link again.",
      retryable: false,
    };
  }

  if (status === 401) {
    return {
      title: 'Your session expired',
      detail: 'Sign in again and reopen this link.',
      retryable: false,
    };
  }

  if ((error as { isNetworkError?: boolean } | null)?.isNetworkError) {
    return {
      title: "Couldn't reach PuppyOne",
      detail: 'The request did not get through. Check your connection and try again.',
      retryable: true,
    };
  }

  return {
    title: "Couldn't open this project",
    detail: message || 'Something went wrong on our side. Try again in a moment.',
    retryable: true,
  };
}

// ── Presentation ────────────────────────────────────────────────────
//
// Deliberately plain: one centred column on the app's inset surface,
// the product mark, a heading, and at most two actions. It borrows
// `/login`'s geometry so the sign-in hand-off is visually continuous.

function ShareShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen flex items-center justify-center bg-[var(--po-inset)] text-[var(--po-text)] p-6 font-sans">
      <div className="w-[380px] flex flex-col items-center gap-6">
        <img
          src="/puppyone-logo.svg"
          alt="PuppyOne"
          width={44}
          height={44}
          className="opacity-95"
        />
        {children}
      </div>
    </main>
  );
}

function Heading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="text-center">
      <h1 className="text-xl font-semibold leading-7 text-[var(--po-text)]">{title}</h1>
      {subtitle && (
        <p className="mt-2 text-sm leading-5 text-[var(--po-text-muted)]">{subtitle}</p>
      )}
    </div>
  );
}

function Progress({ label }: { label: string }) {
  return (
    <div
      className="flex items-center gap-2 text-sm text-[var(--po-text-muted)]"
      role="status"
      aria-live="polite"
    >
      <Dots size="xs" />
      <span>{label}</span>
    </div>
  );
}

const PRIMARY_CLASS =
  'w-full h-10 px-4 rounded-md border-none bg-[var(--po-text)] text-[var(--po-text-inverse)] text-sm font-semibold cursor-pointer inline-flex items-center justify-center transition-opacity hover:opacity-90';

const SECONDARY_CLASS =
  'w-full h-10 px-4 rounded-md border border-[var(--po-border)] bg-[var(--po-control)] text-[var(--po-text)] text-sm font-medium cursor-pointer inline-flex items-center justify-center transition-colors hover:bg-[var(--po-control-hover)] hover:border-[var(--po-border-strong)]';

function PrimaryLink({ href, children }: { href: string; children: React.ReactNode }) {
  // A real anchor, not a router push: this is the one action a visitor
  // with broken client-side routing still needs to work.
  return (
    <a href={href} className={PRIMARY_CLASS}>
      {children}
    </a>
  );
}

function SecondaryLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} className={SECONDARY_CLASS}>
      {children}
    </a>
  );
}

function PrimaryButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className={PRIMARY_CLASS}>
      {children}
    </button>
  );
}

function SecondaryButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button type="button" onClick={onClick} className={SECONDARY_CLASS}>
      {children}
    </button>
  );
}

function Footnote({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-[var(--po-text-subtle)] text-center">{children}</p>;
}
