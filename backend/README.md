# puppyone Cloud backend

The API, version engine, workers and MCP service behind [puppyone Cloud](../README.md). Written in Python 3.12 with FastAPI.

## Run locally

To run the whole stack, use the [Docker setup](../README.md#self-host). To work on the backend alone:

```bash
cp .env.example .env
uv sync
uv run uvicorn src.main:app --host 0.0.0.0 --port 9090 --reload --log-level info --no-access-log
```

Fill in `.env` first. The API needs Supabase and S3-compatible storage, and [`.env.example`](.env.example) documents every variable. The API then listens on `http://localhost:9090`, with `/health` and `/ready` for status.

## Layout

| Path | Contents |
| --- | --- |
| `src/version_engine/` | Git-native version engine, the only way to publish a change |
| `src/file_operations/` | Hosted file commands and file rules |
| `src/platform/` | Product modules such as access, projects, organizations, agents, imports and synchronize |
| `src/provider/` | Adapters for external sources, including OAuth |
| `src/infra/` | Supabase, S3, LLM, search, security and scheduling |
| `src/composition/` | Composition root, the only code that may know every module |
| `mcp_service/` | MCP protocol service |
| `agent-worker/` | Worker that runs Cloud Agents in their workspaces |
| `tests/` | Backend tests |

## Service roles

Every backend process starts from the same code. `SERVICE_ROLE` selects which one runs, through [`scripts/start-service.sh`](scripts/start-service.sh).

| Role | Runs |
| --- | --- |
| `api` | HTTP API, the default |
| `mcp_server` | MCP protocol service |
| `agent_worker` | Agent runs and their workspaces |
| `upload_worker` | Upload completion and file processing |
| `import_worker` | One-time imports |
| `synchronize_worker` | Synchronize runs and GitHub pulls |
| `ssh_gateway` | SSH gateway |

## Test

```bash
uv run pytest -q -m 'not e2e and not integration and not network'
```

## Read more

- [Developer setup](../docs/getting-started.md)
- [Version Engine](../docs/architecture/01-version-engine.md)
- [Concurrency boundary](../docs/architecture/18-concurrency-boundary.md)
- [Database release rules](../docs/architecture/13-database-release-governance.md)
