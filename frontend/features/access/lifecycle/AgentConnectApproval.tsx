'use client';

import { useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { accessRequest } from './api';
import styles from './lifecycle.module.css';

interface AgentConnectionReview {
  id: string; name: string; state: 'pending' | 'approved' | 'rejected' | 'expired' | 'revoked';
  project_name: string; grant_seconds: number;
}

const endpoint = (connection: string) => `/api/v1/agent-links/connections/${encodeURIComponent(connection)}`;

const outcomes: Record<Exclude<AgentConnectionReview['state'], 'pending'>, { title: string; detail: string }> = {
  approved: { title: 'Connected', detail: 'You can go back to your agent. Remove it any time from Share.' },
  rejected: { title: 'Request declined', detail: 'This agent was not given access.' },
  expired: { title: 'This request expired', detail: 'Ask your agent to start again.' },
  revoked: { title: 'Access was removed', detail: 'Ask your agent to start again if you still want it connected.' },
};

/** The link an Agent hands to its person. Only the Workspace's owner can act on it. */
export function AgentConnectApproval({ connectionId }: { connectionId: string }) {
  const { userId, session, isAuthReady } = useAuth();
  const signedIn = Boolean(userId && session && !session.user.is_anonymous);
  return <main className={styles.portal}><div className={`${styles.portalCard} ${styles.panel}`}>
    {!isAuthReady && <p className={styles.muted}>Loading…</p>}
    {isAuthReady && !signedIn && <>
      <h1 className={styles.title}>Connect your agent</h1>
      <p className={styles.muted}>Sign in to let your agent into your project.</p>
      <Link className={styles.button}
        href={`/login?redirect=${encodeURIComponent(`/agent-connect/${connectionId}`)}`}>Sign in</Link>
    </>}
    {isAuthReady && signedIn && <Review key={userId} connectionId={connectionId} userId={userId!} />}
  </div></main>;
}

function Review({ connectionId, userId }: { connectionId: string; userId: string }) {
  const { data, error, mutate } = useSWR(['agent-connection', userId, connectionId],
    () => accessRequest<AgentConnectionReview>('GET', `${endpoint(connectionId)}/review`),
    { revalidateOnFocus: false, shouldRetryOnError: false });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function decide(decision: 'approve' | 'reject') {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      await mutate(await accessRequest<AgentConnectionReview>('POST', `${endpoint(connectionId)}/decision`, { decision }),
        { revalidate: false });
    } catch {
      setFailed(true);
      await mutate();
    } finally { setBusy(false); }
  }

  if (error) return <>
    <h1 className={styles.title}>You can’t allow this agent</h1>
    <p className={styles.muted}>Only the owner of this hosted workspace can let an agent in.
      Sign in with that account, or ask them to open this link.</p>
    <Link className={styles.button}
      href={`/login?redirect=${encodeURIComponent(`/agent-connect/${connectionId}`)}`}>Use another account</Link>
  </>;
  if (!data) return <p className={styles.muted}>Loading…</p>;
  if (data.state !== 'pending') return <>
    <h1 className={styles.title}>{outcomes[data.state].title}</h1>
    <p className={styles.muted}>{outcomes[data.state].detail}</p>
  </>;
  const days = Math.round(data.grant_seconds / 86400);
  return <>
    <h1 className={styles.title}>Connect “{data.name}” to {data.project_name}?</h1>
    <p className={styles.muted}>It can run commands and edit every file in your hosted workspace, and wake the
      workspace when it’s asleep, for {days} days. You can remove it any time from Share.</p>
    <p className={styles.muted}>Only continue if you asked this agent to connect just now.</p>
    {failed && <p className={styles.error} role="alert">That didn’t work. Try again.</p>}
    <button type="button" className={styles.button} disabled={busy} onClick={() => void decide('approve')}>Connect</button>
    <button type="button" className={styles.button} disabled={busy} onClick={() => void decide('reject')}>Decline</button>
  </>;
}
