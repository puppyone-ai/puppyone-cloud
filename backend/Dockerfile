# Hosted backend image: one build per commit, deployed unchanged to every
# backend role (api, mcp_server, agent_worker, upload/import/synchronize
# workers) and promoted unchanged between environments. SERVICE_ROLE selects
# the process at start; nothing environment-specific is baked in.
#
# Built and published by the `images` job in .github/workflows/_environment-release.yml.
# Dockerfile.local remains the self-hosted/Compose image (it adds the Docker CLI
# for the local sandbox provider).

FROM python:3.12.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# /opt/venv keeps the interpreter and the Fly client at the paths the hosted
# configuration already uses (CLOUD_AGENT_FLY_CLI=/opt/venv/bin/fly).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Dependencies first so source-only changes reuse this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev \
    && python scripts/install_flyctl.py /opt/venv/bin/fly \
    && chmod 0755 scripts/start-service.sh

# Build identity for execution records (read by the Agent dispatch module);
# Railway does not provide a commit for image deployments.
ARG SOURCE_SHA=unknown
ENV PUPPYONE_BUILD_ID=$SOURCE_SHA
LABEL org.opencontainers.image.source="https://github.com/puppyone-ai/puppyone-cloud" \
      org.opencontainers.image.revision=$SOURCE_SHA

CMD ["/app/scripts/start-service.sh"]
