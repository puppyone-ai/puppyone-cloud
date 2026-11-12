# Sandbox images by Profile (backend/src/platform/sandbox/catalog.py).
# The default build is "base"; `--target desktop` adds the Cloud Computer desktop.
FROM node:22.22.3-bookworm-slim@sha256:e21fc383b50d5347dc7a9f1cae45b8f4e2f0d39f7ade28e4eef7d2934522b752 AS base
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates fd-find ripgrep rsync util-linux python3 nftables && ln -s /usr/bin/fdfind /usr/local/bin/fd && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/puppyone-agent
COPY package.json ./
COPY package-lock.json ./
COPY patch-dependencies.mjs ./
RUN npm ci --omit=dev --ignore-scripts && node patch-dependencies.mjs && npm cache clean --force
COPY controller.mjs protocol.mjs git.mjs git-relay.mjs worker.mjs loop.mjs tool-execution.mjs tools.mjs filesystem.mjs execution.mjs ./
COPY workbench-shell.mjs workbench-shell.py ./
COPY workbench-controller.mjs workbench-task.mjs workbench-signals.mjs workbench-signals.py workbench-git.mjs ./
COPY shared-git.mjs workbench-git-layout.mjs workbench-state.mjs workbench-initialize.mjs workbench-lifecycle.mjs ./
COPY workbench-files.mjs workbench-files.py workbench-file-client.mjs workbench-files-supervisor.mjs workbench-machine.mjs fly-init.mjs fly-egress.mjs ./
COPY desktop-supervisor.mjs desktop-relay.mjs ./
COPY sprites-init.mjs sprites-control.py ./
COPY providers/docker-recovery.mjs ./providers/
COPY runtime-contract.mjs write-runtime-manifest.mjs sandbox-profiles.json ./
RUN node write-runtime-manifest.mjs
RUN mkdir -p /workspace /home/node/.pi && chown node:node /workspace /home/node/.pi
USER root
WORKDIR /workspace
ENV HOME=/home/node NODE_ENV=production
ENTRYPOINT ["sleep", "infinity"]

# Desktop: TigerVNC display, a lean XFCE session and Chromium. System files
# come from the image; user state lives in /home/node on the workspace disk.
FROM base AS desktop
RUN apt-get update && apt-get install -y --no-install-recommends \
      tigervnc-standalone-server dbus-x11 x11-xserver-utils xauth \
      xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-settings xfce4-terminal thunar \
      adwaita-icon-theme chromium fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
RUN cd /opt/puppyone-agent && PUPPYONE_SANDBOX_PROFILE=desktop node write-runtime-manifest.mjs

# The last stage is the default build target.
FROM base AS workspace
