# Getting Started (Developers & Contributors)

This guide is for people who clone the repo to **run PuppyOne locally** or **open a pull request**. For product onboarding (CLI, first project, connectors), see the [README](../README.md).

## Prerequisites

| Path | What you need |
|------|----------------|
| **Docker (recommended)** | [Docker](https://www.docker.com/) only — full stack via Compose |
| **Native dev** | Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 18+, npm |

## Option A: Self-hosted stack (Docker)

Fastest way to run everything without wiring cloud services manually:

```bash
git clone https://github.com/puppyone-ai/puppyone-cloud.git
cd puppyone/docker
cp .env.example .env
docker compose up -d
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:9090 |
| Supabase API | http://localhost:8000 |

First startup can take 1–2 minutes. Optional: add `ANTHROPIC_API_KEY` and OAuth provider keys to `docker/.env` for agent chat and SaaS connectors (see [README](../README.md#3-optional-enable-more-features)).

## Option B: Native backend + frontend

Use this when you are changing application code and want hot reload.

### 1. Environment files

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Fill in Supabase, S3/LocalStack, and other values described in the example files. **Never commit `.env`.**

For a minimal local API without full cloud deps, some teams use `SKIP_AUTH=true` in `backend/.env` during development — only on trusted machines.

### 2. Backend

```bash
cd backend
uv sync
uv run uvicorn src.main:app --host 0.0.0.0 --port 9090 --reload --log-level info --no-access-log
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. Set `NEXT_PUBLIC_API_URL=http://localhost:9090` in `frontend/.env` if it is not already the default.

### 4. CLI (optional)

```bash
npm install -g puppyone
puppyone auth login
# Self-hosted API: choose Local or pass -u http://localhost:9090
```

## Run tests

From `backend/`:

```bash
uv run pytest -m "unit"          # fast, no external services
uv run pytest -m "not e2e"       # exclude full-stack e2e
```

See [CONTRIBUTING.md](../CONTRIBUTING.md#testing) for unit / integration / contract / e2e layers.

## Repository layout (active code)

| Directory | Role |
|-----------|------|
| `backend/` | FastAPI API, MUT engine, connectors |
| `frontend/` | Next.js web app |
| `cli/` | `puppyone` CLI |
| `docs/` | Architecture and design docs |
| `docker/` | Local Compose stack |

**Do not modify** deprecated trees: `PuppyEngine/`, `PuppyFlow/`, `PuppyStorage/`, `tools/` (see root [AGENTS.md](../AGENTS.md)).

## Next steps

| Goal | Document |
|------|----------|
| Architecture & module map | [AGENTS.md](../AGENTS.md) |
| Branch model, PR target, CI | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| MUT / Access Point / CLI design | [docs/README.md](README.md) |
| End-user product flow | [README](../README.md) |

## Opening your first PR

1. Fork `puppyone-ai/puppyone-cloud` on GitHub (if you are an external contributor).
2. Branch from **`qubits`**, not `main`:

   ```bash
   git fetch origin
   git checkout -b docs/my-change origin/qubits
   ```

3. Keep the PR small (docs-only PRs are a good first contribution).
4. Open the PR with **base branch `qubits`** and fill in the [PR template](../.github/PULL_REQUEST_TEMPLATE.md).

Production (`main`) only receives changes through maintainer release PRs; contributor PRs to `qubits` do not deploy to production directly.
