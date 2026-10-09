import { chromium } from 'playwright';
import assert from 'node:assert/strict';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const f = JSON.parse(input);
const file = `/${f.project}/${encodeURIComponent('文档')}/${encodeURIComponent('hello #1.md')}`;
const browser = await chromium.launch({ headless: true, ...(process.env.PUPPYONE_TEST_CHROMIUM_BIN ? { executablePath: process.env.PUPPYONE_TEST_CHROMIUM_BIN } : {}) });
const errors = [];
async function client(person) {
  const context = await browser.newContext();
  if (person) {
    const session = { access_token: f.tokens[person], refresh_token: 'owned-test-refresh', token_type: 'bearer', expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      user: { id: f.users[person], email: f.emails[person], aud: 'authenticated', role: 'authenticated', app_metadata: { provider: 'email' }, user_metadata: {} } };
    await context.addCookies([{ name: 'sb-127-auth-token', value: 'base64-' + Buffer.from(JSON.stringify(session)).toString('base64url'), url: f.origin, sameSite: 'Lax' }]);
  }
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  return page;
}
async function api(page, person, method, path, body, status = 200) {
  const response = await page.context().request.fetch(`${f.origin}/api/backend/api/v1${path}`, {
    method, headers: { Authorization: `Bearer ${f.tokens[person]}` }, ...(body ? { data: body } : {}),
  });
  assert.equal(response.status(), status, await response.text());
  return (await response.json()).data;
}
try {
  const anonymous = await client();
  await anonymous.goto(f.origin + file);
  assert.equal(new URL(anonymous.url()).pathname, '/login');
  assert.equal(new URL(anonymous.url()).searchParams.get('redirect'), file);

  const pages = {};
  for (const person of ['owner', 'admin', 'editor', 'viewer', 'outsider', 'invitee']) pages[person] = await client(person);
  for (const person of ['owner', 'admin', 'editor', 'viewer']) {
    const reads = [];
    pages[person].on('request', r => { if (r.url().includes('/content/')) reads.push(r.url()); });
    await pages[person].goto(f.origin + file);
    await pages[person].getByText('team confidential content', { exact: true }).waitFor();
    assert.equal(reads.length, 1, `${person}: one request to open the file`);
  }
  await pages.outsider.goto(f.origin + file);
  await pages.outsider.getByRole('heading', { name: 'This resource is not available' }).waitFor();
  assert.ok(!(await pages.outsider.locator('body').innerText()).includes('team confidential content'));

  const invitation = await api(pages.owner, 'owner', 'POST', `/organizations/${f.org}/invite`, { email: f.emails.invitee, role: 'member' });
  await pages.invitee.goto(`${f.origin}/invite/${invitation.token}`);
  await pages.invitee.getByText('Joined', { exact: false }).waitFor();
  await pages.invitee.goto(f.origin + file);
  await pages.invitee.getByRole('heading', { name: 'This resource is not available' }).waitFor();
  await api(pages.admin, 'admin', 'POST', `/projects/${f.project}/members`, { user_id: f.users.invitee, role: 'editor' }, 201);
  await pages.invitee.reload();
  await pages.invitee.getByText('team confidential content', { exact: true }).waitFor();
  const write = { path: 'browser-edit.md', content: 'Browser team edit', node_type: 'markdown' };
  await api(pages.invitee, 'invitee', 'POST', `/content/${f.project}/write`, write);
  await api(pages.admin, 'admin', 'PUT', `/projects/${f.project}/members/${f.users.invitee}/role`, { role: 'viewer' });
  await api(pages.invitee, 'invitee', 'POST', `/content/${f.project}/write`, write, 403);
  await pages.invitee.reload();
  await pages.invitee.getByText('team confidential content', { exact: true }).waitFor();
  await api(pages.admin, 'admin', 'DELETE', `/projects/${f.project}/members/${f.users.invitee}`);
  await pages.invitee.reload();
  await pages.invitee.getByRole('heading', { name: 'This resource is not available' }).waitFor();
  assert.ok(!(await pages.invitee.locator('body').innerText()).includes('team confidential content'));
  await pages.invitee.screenshot({ path: `${f.artifacts}/revoked-workspace.png`, fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: 'passed', identities: 7, anonymousReturn: 'preserved', privateWorkspace: 'explicit-grant', downgrade: 'write-denied', revocation: 'same-session-denied', fileRequests: 1, browserErrors: errors }));
} finally {
  await browser.close();
}
