# Desktop email sign-in, end to end

A person signs in to PuppyOne Desktop with email through the Cloud `/login`
page. Nothing between the two products is simulated:

- **Desktop**: the real Main authentication service (PKCE, loopback listener,
  browser handoff receiver, Supabase client) from a Desktop checkout.
- **Web**: the Cloud frontend built with the release `Dockerfile` and served on
  `0.0.0.0`, as deployed. A loopback-only dev server hides request-URL rewrites
  that only happen on deployed servers.
- **Browser**: Chromium opens the URL Desktop opens, types the email and
  password, and submits.

The check passes only when Desktop ends up signed in as that account, has
stored exactly a refresh credential, reported no sign-in error, and can sign
out again. On failure, `results/` holds a screenshot, the page text and a step
log; tokens, state and codes are redacted.

## Where it runs

| Trigger | Web | Desktop |
|---|---|---|
| Cloud PR / push to `qubits` or `main` | this PR's `frontend/` | paired branch (`qubits` or `main`) |
| Desktop PR (via `workflow_call`) | paired Cloud branch | the Desktop PR's commit |

Workflow: [`.github/workflows/desktop-login-e2e.yml`](../../.github/workflows/desktop-login-e2e.yml).
It needs no secrets; Supabase is local, auth-only and gets a fresh confirmed
account for each run.

## Run it locally

Requires Docker, the Supabase CLI and a Desktop checkout (`puppyone-ai/puppyone`).

```bash
cd e2e/desktop-login
npm ci && npx playwright install chromium
supabase start --workdir .
eval "$(supabase status --workdir . -o env | grep -E '^(API_URL|ANON_KEY|SERVICE_ROLE_KEY)=')"

# A confirmed account in the local auth server.
E2E_EMAIL=desktop-login@example.test
E2E_PASSWORD="$(openssl rand -base64 24)"
curl -s -X POST "$API_URL/auth/v1/admin/users" \
  -H "apikey: $SERVICE_ROLE_KEY" -H "Authorization: Bearer $SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$E2E_EMAIL\",\"password\":\"$E2E_PASSWORD\",\"email_confirm\":true}"

# The Web as deployed: release image, listening on 0.0.0.0.
docker build -t desktop-login-e2e-web \
  --build-arg NEXT_PUBLIC_API_URL=http://localhost:9 \
  --build-arg NEXT_PUBLIC_APP_URL=http://localhost:3490 \
  --build-arg NEXT_PUBLIC_SUPABASE_URL="$API_URL" \
  --build-arg NEXT_PUBLIC_SUPABASE_ANON_KEY="$ANON_KEY" \
  --build-arg NEXT_PUBLIC_AUTH_EMAIL_ONLY=true ../../frontend
docker run -d --name desktop-login-e2e-web -p 127.0.0.1:3490:8080 \
  -e PORT=8080 -e HOSTNAME=0.0.0.0 desktop-login-e2e-web

DESKTOP_ROOT=../../../puppyone WEB_ORIGIN=http://localhost:3490 \
SUPABASE_URL="$API_URL" SUPABASE_ANON_KEY="$ANON_KEY" \
E2E_EMAIL="$E2E_EMAIL" E2E_PASSWORD="$E2E_PASSWORD" node run.mjs

docker rm -f desktop-login-e2e-web && supabase stop --workdir .
```

`HEADED=1` shows the browser.

### Against a deployed environment

Set `API_BASE` (for example `https://qubits-api.puppyone.ai/api/v1`) and
`WEB_ORIGIN` (`https://qubits-try.puppyone.ai`) instead of `SUPABASE_URL` /
`SUPABASE_ANON_KEY`, and use a dedicated test account. Desktop then reads its
Supabase configuration from that Cloud API exactly as the app does.
