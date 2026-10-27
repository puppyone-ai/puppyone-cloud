'use client';

import { useEffect, useRef, useState } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { accessRequest, readInvitation, invitationLink, type AccessRecord, type Registration } from './api';
import styles from './lifecycle.module.css';

const pendingKey = 'puppyone.access-invitation';

/** Only an invitation survives the login round-trip. Browser code never owns an Agent's renewal key. */
export function AccessInvitation({ onConsumeFragment }: { onConsumeFragment: () => void }) {
  const { userId, session, isAuthReady } = useAuth();
  const [invitation, setInvitation] = useState('');
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try {
      const fragment = window.location.hash;
      const previous = sessionStorage.getItem(pendingKey);
      if (fragment) {
        sessionStorage.removeItem(pendingKey);
        const secret = readInvitation(fragment);
        sessionStorage.setItem(pendingKey, JSON.stringify({ secret, expires: Date.now() + 3600_000 }));
        setInvitation(secret);
      } else if (previous) {
        const saved = JSON.parse(previous);
        if (saved.expires <= Date.now() || !/^pai_[A-Za-z0-9_-]{43,128}$/.test(saved.secret)) throw new Error('Open your invitation link again.');
        setInvitation(saved.secret);
      } else throw new Error('Open the invitation link supplied by the host.');
    } catch (cause) {
      sessionStorage.removeItem(pendingKey);
      setError(cause instanceof Error ? cause.message : 'Could not open invitation.');
    } finally {
      // Fragments do not reach the server. Remove them before subsequent navigation.
      onConsumeFragment();
      setReady(true);
    }
  }, [onConsumeFragment]);

  return <main className={styles.portal}><div className={`${styles.portalCard} ${styles.panel}`}>
    <h1 className={styles.title}>Connect your agent</h1>
    <p className={styles.muted}>Choose a registered agent and request the host’s approval.</p>
    {error && <p className={styles.error} role="alert">{error}</p>}
    {(!ready || !isAuthReady) && <p className={styles.muted}>Loading invitation…</p>}
    {ready && isAuthReady && invitation && (!session || session.user.is_anonymous)
      && <Link className={styles.button} href="/login?redirect=%2Fagent-access">Sign in to apply</Link>}
    {ready && userId && session && !session.user.is_anonymous && invitation
      && <Applicant key={userId} invitation={invitation} userId={userId} />}
  </div></main>;
}

function Applicant({ invitation, userId }: { invitation: string; userId: string }) {
  const { data: registrations, error: loadError } = useSWR(['access-registrations', userId],
    () => accessRequest<Registration[]>('GET', '/api/v1/access/registrations'));
  const [selected, setSelected] = useState('');
  const [application, setApplication] = useState<AccessRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(crypto.randomUUID());
  const inFlight = useRef(false);
  const active = registrations?.filter(record => record.state === 'active') ?? [];
  const client = active.find(record => record.id === selected)?.id ?? active[0]?.id;
  const { data: requests } = useSWR(application ? ['access-request', userId, application.registration_id] : null,
    () => accessRequest<AccessRecord[]>('GET', `/api/v1/access/registrations/${application!.registration_id}/requests`),
    { refreshInterval: (rows: AccessRecord[] | undefined) =>
      (rows?.find(record => record.id === application?.id)?.state ?? application?.state) === 'pending' ? 5000 : 0 });
  const current = requests?.find(record => record.id === application?.id) ?? application;
  useEffect(() => {
    if (current && current.state !== 'pending') sessionStorage.removeItem(pendingKey);
  }, [current]);

  async function apply() {
    if (!client || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      setApplication(await accessRequest<AccessRecord>('POST', '/api/v1/access/requests', {
        id: requestId.current, registration_id: client, invitation,
      }));
    } catch { setError('Could not apply. The invitation may have expired or been revoked.'); }
    finally { setBusy(false); inFlight.current = false; }
  }
  if (current) return <section className={styles.section} aria-live="polite">
    <h2 className={styles.heading}>{current.state === 'approved' ? 'Access approved' : current.state === 'rejected' ? 'Request declined' : 'Waiting for approval'}</h2>
    <p className={styles.muted}>Check this agent’s access with <code>puppyone access clients status</code>.</p>
    {current.state === 'approved' && current.grant_id && current.channels?.includes('ssh')
      && <code className={styles.code}>puppyone access ssh {current.grant_id} --client {current.registration_id}</code>}
  </section>;
  return <section className={styles.section}>
    {(error || loadError) && <p className={styles.error} role="alert">{error || 'Could not load your registered agents.'}</p>}
    {active.length > 0 ? <>
      <select className={styles.select} aria-label="Registered agent" value={client} disabled={busy}
        onChange={event => { setSelected(event.target.value); requestId.current = crypto.randomUUID(); }}>
        {active.map(record => <option key={record.id} value={record.id}>{record.name}</option>)}
      </select>
      <button className={styles.button} disabled={busy} onClick={() => void apply()}>{busy ? 'Applying…' : 'Request access'}</button>
    </> : registrations && <>
      <p className={styles.muted}>On your agent’s computer, sign in with Puppyone CLI and run:</p>
      <code className={styles.code}>puppyone access join &apos;{invitationLink(invitation, window.location.origin)}&apos;</code>
      <p className={styles.muted}>The CLI registers your agent and requests approval. Its private credentials stay on that computer.</p>
    </>}
  </section>;
}
