<div align="center">
  <img src="assets/puppyone-cloud.svg" alt="puppyone Cloud logo" width="88" height="88" />

  <h1>puppyone Cloud</h1>

  <p><b>The cloud infrastructure behind <a href="https://github.com/puppyone-ai/puppyone">puppyone</a>.</b></p>
  <p>Hosts your projects as Git repositories, keeps them online, and controls which people and agents can reach them.</p>

  <p>
    <a href="https://www.puppyone.ai/download"><b>Get puppyone</b></a> ·
    <a href="#self-host"><b>Self-host</b></a> ·
    <a href="https://www.puppyone.ai/doc"><b>Docs</b></a> ·
    <a href="https://discord.gg/zwJ9Y3Uvpd"><b>Discord</b></a>
  </p>
</div>

[puppyone](https://github.com/puppyone-ai/puppyone) is the folder your agents live in. It runs on your computer and needs no account. Cloud is the optional part: put a folder on Cloud and you get a cloud project that stays online after you close your laptop, where your team and agents can work together.

Every cloud project is a real Git repository, so each change is recorded and can be rolled back. People and agents reach it through the web app, Git, MCP, the CLI or the REST API.

This repository contains that infrastructure: the API, the Cloud web app, the version engine and the database migrations.

| You want to | Go to |
| --- | --- |
| Use puppyone | [Download the app](https://www.puppyone.ai/download) |
| Use Cloud without running anything | [puppyone.ai](https://www.puppyone.ai) |
| Drive Cloud from a terminal | `npm install --global puppyone` |
| Run or change the infrastructure | You are in the right place |

## What it does

- **Stays on.** A cloud project stays available to your team and agents around the clock.
- **Speaks real Git.** Stock `git` clones, fetches and pushes. No plugin, no special client.
- **Keeps one history.** A push, a web edit and an API write all publish through the same engine, so you can review and roll back any of them.
- **Connects your agents.** Each MCP, Git or CLI connection has its own credential that you can pause or revoke.
- **Brings sources in.** GitHub, Gmail, Google Drive, Docs, Sheets, Calendar, Search Console and web pages.
- **Works for teams.** Organizations, Project roles, invitations and audit logs.
- **Runs agents in the cloud.** Hosted workspaces keep working when your laptop is closed. Early Access.

> Credentials are limited to one Project today. Limiting an agent to part of a Project is being rebuilt for the native version engine and is not available yet.

## How it works

```text
Web app ─┐
Git ─────┤
MCP ─────┼──> Access ──> Version Engine ──┬──> PostgreSQL   refs, audit
CLI ─────┤    who may      the only way   └──> S3           immutable Git objects
REST ────┘    do what      to publish
```

Git owns the version facts. puppyone owns the collaboration rules around them. PostgreSQL refs and immutable Git objects in S3 are the only record of what a Project contains, so no entry point can drift from the others. Read the [Version Engine architecture](docs/architecture/01-version-engine.md).

| Path | Contents |
| --- | --- |
| [`backend/`](backend) | Python 3.12 and FastAPI: API, version engine, workers, MCP service |
| [`frontend/`](frontend) | Next.js web app |
| [`supabase/`](supabase) | Schema and data migrations |
| [`docker/`](docker) | Self-hosted stack |
| [`docs/`](docs/README.md) | Architecture and operations |
| [`e2e/`](e2e/README.md) | Tests that need several parts running together |

The CLI is developed separately and published to npm as [`puppyone`](https://www.npmjs.com/package/puppyone).

## Self-host

You need [Docker](https://www.docker.com/).

```bash
git clone https://github.com/puppyone-ai/puppyone-cloud.git
cd puppyone-cloud/docker
cp .env.example .env
docker compose up --build -d --wait
```

The first build takes several minutes. Then open <http://localhost:3000>. If startup fails, run `docker compose ps -a` and `docker compose logs migrate storage-init api`.

This stack runs the API and the web app with their data services. The features that need another backend process are not part of it yet:

| Feature | In `docker compose up` | What it needs |
| --- | --- | --- |
| Accounts, Projects, file reads and writes, history | Yes | Nothing |
| File upload pipeline | No | `upload_worker` |
| Imports and sources | No | `import_worker`, `synchronize_worker`, provider credentials |
| MCP | No | `mcp_server`, `MCP_SERVER_URL` |
| Agent runs and hosted workspaces | No | `agent_worker`, Managed AI, a workspace provider |

> [!WARNING]
> This stack is for local use. It ships with default secrets, and the API container can reach the host Docker daemon.

Existing PostgreSQL 15 volumes need an explicit upgrade. Read [installation and upgrade validation](docs/architecture/14-self-hosted-installation.md) before you change the image or delete a volume.

## Contributing

Open an issue for larger changes. Pull requests target the `qubits` branch.

- [Developer setup](docs/getting-started.md)
- [Contributing guide](CONTRIBUTING.md)
- [Database release rules](docs/architecture/13-database-release-governance.md)
- [Security policy](SECURITY.md)

## License

Code is licensed under the [Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE) for third-party components.

The puppyone name and logo are trademarks and are not licensed under Apache 2.0. If you redistribute a fork, use your own name and logo. The hosted service at [puppyone.ai](https://www.puppyone.ai) has its own Terms of Service.
