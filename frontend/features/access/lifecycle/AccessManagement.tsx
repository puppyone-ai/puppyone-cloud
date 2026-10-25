'use client';

import { useRef, useState } from 'react';
import useSWR from 'swr';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { workspaceStatus } from '@/lib/workspaceFiles';
import { accessRequest, loadAccess, projectAccess, newInvitation, invitationLink, type Invitation, type AccessRecord } from './api';
import styles from './lifecycle.module.css';

const describe = (record: AccessRecord) => record.target_kind === 'workspace'
  ? `Hosted workspace · files and shell${record.capabilities?.includes('workspace.wake') ? ' · may resume it' : ''}`
  : `Repository · ${record.capabilities?.includes('repository.write') ? 'read and write' : 'read only'}`;

export function AccessManagement({ projectId }: { projectId: string }) {
  const { userId, session } = useAuth();
  return userId && session ? <Management key={`${projectId}:${userId}:${session.access_token}`} projectId={projectId} userId={userId} /> : null;
}

function Management({ projectId, userId }: { projectId: string; userId: string }) {
  const { data, error, isLoading, mutate } = useSWR(userId ? ['access-lifecycle', userId, projectId] : null,
    () => loadAccess(projectId), { refreshInterval: 10_000 });
  const [target, setTarget] = useState('repository-read');
  const [allowWake, setAllowWake] = useState(false);
  const [link, setLink] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [durations, setDurations] = useState<Record<string, string>>({});
  const pendingInvitation = useRef<Invitation>();
  const inFlight = useRef(false);
  const base = projectAccess(projectId);

  async function perform(action: () => Promise<unknown>) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setActionError('');
    try { await action(); await mutate(); }
    catch (error) { setActionError(error instanceof Error && error.message === 'Open your hosted workspace first.'
      ? error.message : 'Could not update access. Your permissions may have changed. Retry or refresh.'); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function createLink() {
    if (!pendingInvitation.current) {
      const workspace = target === 'workspace' ? (await workspaceStatus(projectId)).workspace : null;
      if (target === 'workspace' && !workspace) throw new Error('Open your hosted workspace first.');
      pendingInvitation.current = newInvitation(workspace?.id, target !== 'repository-write', allowWake);
    }
    const invitation = pendingInvitation.current;
    await accessRequest('POST', `${base}/invitations`, invitation);
    setLink(invitationLink(invitation.secret, window.location.origin));
    pendingInvitation.current = undefined;
  }

  return <div className={styles.panel}>
    <section className={styles.section} aria-label="Invite an agent">
      <h3 className={styles.heading}>Invite an agent</h3>
      <div className={styles.actions}>
        <select className={styles.select} aria-label="Access target" value={target} disabled={busy}
          onChange={event => { setTarget(event.target.value); pendingInvitation.current = undefined; setLink(''); }}>
          <option value="repository-read">Repository · read only</option>
          <option value="repository-write">Repository · read and write</option>
          <option value="workspace">My hosted workspace · files and shell</option>
        </select>
        <button className={styles.button} disabled={busy} onClick={() => void perform(createLink)}>Create invitation</button>
      </div>
      <p className={styles.muted}>One applicant, valid for 7 days. You approve access after they apply.</p>
      {target === 'workspace' && <p className={styles.muted}>The agent can run commands and edit all files in your hosted workspace. Open it first if it has not been created.</p>}
      {target === 'workspace' && <label className={styles.muted}>
        <input type="checkbox" checked={allowWake} disabled={busy}
          onChange={event => { setAllowWake(event.target.checked); pendingInvitation.current = undefined; setLink(''); }} />
        {' '}Allow it to resume my workspace when paused (uses my compute)
      </label>}
      {link && <div className={styles.actions}>
        <input className={styles.link} aria-label="Invitation link" readOnly value={link} onFocus={event => event.target.select()} />
        <button className={styles.button} onClick={() => void navigator.clipboard.writeText(link).catch(() => setActionError('Select and copy the invitation link.'))}>Copy link</button>
      </div>}
    </section>
    {(error || actionError) && <p role="alert" className={styles.error}>{actionError || 'Could not load access.'}
      <button className={styles.button} onClick={() => void mutate()}>Refresh</button></p>}
    {isLoading && <p role="status" className={styles.muted}>Loading access…</p>}
    <section className={styles.section} aria-label="Access requests">
      <h3 className={styles.heading}>Requests</h3>
      {data?.requests.filter(item => item.state === 'pending').map(item => {
        const duration = durations[item.id] ?? (item.max_grant_seconds ? 'day' : 'ongoing');
        return <div className={styles.card} key={item.id}>
          <strong>{item.registration_name}</strong>
          <p className={styles.muted}>{describe(item)}</p>
          <p className={styles.muted}>Applicant: {item.applicant_user_id}</p>
          <div className={styles.actions}>
            <select className={styles.select} aria-label={`Duration for ${item.registration_name}`} value={duration}
              onChange={event => setDurations({ ...durations, [item.id]: event.target.value })} disabled={busy}>
              {!item.max_grant_seconds && <option value="ongoing">Until revoked</option>}
              <option value="day">Up to 24 hours</option>
              {item.channels?.length === 1 && item.channels[0] === 'ssh' && <option value="session">One session</option>}
            </select>
            <button className={styles.button} disabled={busy} onClick={() => void perform(() => accessRequest('POST', `${base}/requests/${item.id}/decision`, {
              decision: 'approve', single_session: duration === 'session',
              ...(duration !== 'ongoing' ? { expires_at: new Date(Date.now() + Math.min(86400, item.max_grant_seconds ?? 86400) * 1000).toISOString() } : {}),
            }))}>Approve</button>
            <button className={styles.button} disabled={busy} onClick={() => void perform(() => accessRequest('POST', `${base}/requests/${item.id}/decision`, { decision: 'reject' }))}>Reject</button>
          </div>
        </div>;
      })}
      {data && !data.requests.some(item => item.state === 'pending') && <p className={styles.muted}>No pending requests.</p>}
    </section>
    <section className={styles.section} aria-label="Approved agents">
      <h3 className={styles.heading}>Approved agents</h3>
      {data?.grants.filter(item => item.state !== 'revoked').map(item => <div className={styles.card} key={item.id}>
        <div className={styles.row}><strong>{item.registration_name}</strong><span className={styles.muted}>{item.state}</span></div>
        <p className={styles.muted}>{describe(item)} · {item.expires_at ? new Date(item.expires_at).toLocaleString() : 'Until revoked'}</p>
        <div className={styles.actions}>
          <button className={styles.button} disabled={busy} onClick={() => void perform(() => accessRequest('PATCH', `${base}/grants/${item.id}`, { state: item.state === 'active' ? 'suspended' : 'active' }))}>{item.state === 'active' ? 'Suspend' : 'Resume'}</button>
          <button className={styles.button} disabled={busy} onClick={() => void perform(() => accessRequest('PATCH', `${base}/grants/${item.id}`, { state: 'revoked' }))}>Revoke</button>
        </div>
      </div>)}
      {data && !data.grants.some(item => item.state !== 'revoked') && <p className={styles.muted}>No approved agents.</p>}
    </section>
    <section className={styles.section} aria-label="Agent connections">
      <h3 className={styles.heading}>Connections</h3>
      {data?.sessions.filter(item => ['active', 'stopping'].includes(item.state)).map(item => <div className={styles.row} key={item.id}>
        <span>{item.registration_name} · {item.channel} · {item.state}</span>
        <button className={styles.button} disabled={busy || item.state !== 'active'} onClick={() => void perform(() => accessRequest('DELETE', `${base}/sessions/${item.id}`))}>Disconnect</button>
      </div>)}
      {data && !data.sessions.some(item => ['active', 'stopping'].includes(item.state)) && <p className={styles.muted}>No active connections.</p>}
    </section>
    <section className={styles.section} aria-label="Invitation links">
      <h3 className={styles.heading}>Invitation links</h3>
      {data?.invitations.filter(item => item.state === 'active').map(item => <div className={styles.row} key={item.id}>
        <span className={styles.muted}>{describe(item)} · expires {new Date(item.expires_at!).toLocaleDateString()}</span>
        <button className={styles.button} disabled={busy} onClick={() => void perform(() => accessRequest('DELETE', `${base}/invitations/${item.id}`))}>Revoke link</button>
      </div>)}
    </section>
  </div>;
}
