import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import crypto from 'node:crypto';

const api = process.env.INSTALL_API;
const auth = process.env.INSTALL_AUTH;
const web = process.env.INSTALL_WEB;
const mail = process.env.INSTALL_MAIL;
const key = process.env.INSTALL_ANON_KEY;
const statePath = process.env.INSTALL_STATE;
for (const target of [api, auth, web, mail]) {
  if (!target || new URL(target).hostname !== '127.0.0.1') throw new Error('Installation tests only target an isolated loopback stack');
}

async function success(response) {
  expect(response.status(), new URL(response.url()).pathname).toBeLessThan(300);
  expect(response.status()).toBeGreaterThanOrEqual(200);
  return response.json();
}

async function signup(request, email, password) {
  const registered = await success(await request.post(`${auth}/auth/v1/signup`, {
    headers: { apikey: key }, data: { email, password },
  }));
  // CI enables confirmation. Receiving mail is part of the real Auth path.
  expect(registered.access_token).toBeUndefined();
  let messageId;
  await expect.poll(async () => {
    const messages = await success(await request.get(`${mail}/api/v1/messages`));
    messageId = messages.messages.find(m => m.To.some(t => t.Address === email))?.ID;
    return Boolean(messageId);
  }, { timeout: 30_000 }).toBe(true);
  const message = await success(await request.get(`${mail}/api/v1/message/${messageId}`));
  const confirmation = message.HTML.match(/href="([^\"]*\/verify[^\"]*)"/)?.[1]?.replaceAll('&amp;', '&');
  expect(confirmation).toBeTruthy();
  expect(new URL(confirmation).origin).toBe(auth);
  const confirmed = await request.get(confirmation, { maxRedirects: 0 });
  expect([302, 303]).toContain(confirmed.status());
  const session = await success(await request.post(`${auth}/auth/v1/token?grant_type=password`, {
    headers: { apikey: key }, data: { email, password },
  }));
  expect(session.access_token).toBeTruthy();
  const claims = JSON.parse(Buffer.from(session.access_token.split('.')[1], 'base64url').toString());
  expect(claims).toMatchObject({
    iss: `${auth}/auth/v1`, aud: 'authenticated', role: 'authenticated',
  });
  return session;
}

test('fresh installation and restart preserve real authenticated file operations', async ({ request, page }) => {
  let state;
  if (process.env.INSTALL_PHASE === 'seed') {
    const email = `install-${crypto.randomUUID()}@example.com`;
    const password = `Install-${crypto.randomUUID()}!`;
    const session = await signup(request, email, password);
    const headers = { Authorization: `Bearer ${session.access_token}` };
    const initialized = await success(await request.post(`${api}/api/v1/auth/initialize`, { headers }));
    const project = await success(await request.post(`${api}/api/v1/projects/`, {
      headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() },
      data: { name: 'Fresh installation test', org_id: initialized.data.org_id },
    }));
    const projectId = project.data.id;
    const content = `Persistent file ${crypto.randomUUID()}\n中文内容\n`;
    const written = await success(await request.post(`${api}/api/v1/content/${projectId}/write`, {
      headers, data: { path: 'install-check.md', content, node_type: 'markdown' },
    }));
    expect(written.data.commit_id).toBeTruthy();
    const outsider = await signup(request, `outsider-${crypto.randomUUID()}@example.com`, password);
    const forbidden = await request.get(`${api}/api/v1/content/${projectId}/cat?path=install-check.md`, {
      headers: { Authorization: `Bearer ${outsider.access_token}` },
    });
    expect([403, 404]).toContain(forbidden.status());
    state = { email, password, projectId, content, session };
    fs.writeFileSync(statePath, JSON.stringify(state), { mode: 0o600 });
  } else {
    state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    const refreshed = await success(await request.post(`${api}/api/v1/auth/refresh`, {
      data: { refresh_token: state.session.refresh_token },
    }));
    expect(refreshed.data.access_token).toBeTruthy();
    state.session = refreshed.data;
  }
  const headers = { Authorization: `Bearer ${state.session.access_token}` };
  const read = await success(await request.get(`${api}/api/v1/content/${state.projectId}/cat?path=install-check.md`, { headers }));
  expect(read.data.content_text).toBe(state.content);
  const raw = await request.get(`${api}/api/v1/content/${state.projectId}/raw?path=install-check.md`, { headers });
  expect(raw.status()).toBe(200);
  expect(await raw.text()).toBe(state.content);
  const anonymous = await request.get(`${api}/api/v1/content/${state.projectId}/cat?path=install-check.md`);
  expect([401, 403]).toContain(anonymous.status());

  // An empty browser context must log in through the shipped frontend.
  const destination = `/projects/${state.projectId}/data`;
  await page.goto(`${web}/login?next=${encodeURIComponent(destination)}`);
  await page.getByPlaceholder('Your email address').fill(state.email);
  const checkedEmail = page.waitForResponse(response =>
    new URL(response.url()).pathname.endsWith('/auth/check-email') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await success(await checkedEmail);
  await page.getByPlaceholder('Enter your password').fill(state.password);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await expect(page).toHaveURL(`${web}${destination}`, { timeout: 60_000 });
  await expect(page.getByText('install-check.md', { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  await page.reload();
  await expect(page).toHaveURL(`${web}${destination}`);
  await expect(page.getByText('install-check.md', { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  if (process.env.INSTALL_PHASE === 'verify') {
    await success(await request.post(`${api}/api/v1/content/${state.projectId}/rm`, {
      headers, data: { path: 'install-check.md' },
    }));
    expect((await request.get(`${api}/api/v1/content/${state.projectId}/cat?path=install-check.md`, { headers })).status()).toBe(404);
    await success(await request.delete(`${api}/api/v1/projects/${state.projectId}`, { headers }));
    await success(await request.post(`${api}/api/v1/auth/logout`, {
      headers, data: { refresh_token: state.session.refresh_token },
    }));
  }
});
