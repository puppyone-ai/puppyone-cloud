#!/bin/sh
# Single start dispatcher for every backend role.
#
# Used by the hosted image (backend/Dockerfile CMD) and by Railway services
# that still build from the repository (backend/railway.toml startCommand), so
# both deployment paths start exactly the same process for a role.
#
# Every role is exec'd: the service process replaces this shell, receives the
# platform's SIGTERM directly and can drain within drainingSeconds. The Agent
# worker relies on this to hand off Runs before its leases expire.
set -eu

case "${SERVICE_ROLE:-api}" in
  api)
    exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:?PORT must be set for the HTTP role}" \
      --workers "${WEB_CONCURRENCY:-2}" --no-access-log
    ;;
  mcp_server)
    exec uvicorn mcp_service.server:app --host 0.0.0.0 --port "${PORT:?PORT must be set for the HTTP role}" --no-access-log
    ;;
  agent_worker)
    exec python -m src.platform.access.adapters.agent.runtime.worker
    ;;
  upload_worker)
    exec arq src.platform.upload.worker.WorkerSettings
    ;;
  import_worker)
    exec arq src.platform.imports.worker.WorkerSettings
    ;;
  synchronize_worker)
    exec arq src.platform.synchronize.worker.WorkerSettings
    ;;
  ssh_gateway)
    exec python -m src.platform.access.adapters.ssh.gateway
    ;;
  *)
    echo "Unsupported SERVICE_ROLE; upgrade worker configuration" >&2
    exit 64
    ;;
esac
