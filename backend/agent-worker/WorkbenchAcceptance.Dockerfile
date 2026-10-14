# Supplemental process/Git acceptance only; not provider lifecycle acceptance.
ARG WORKBENCH_AGENT_IMAGE=puppyone-cloud-agent:issue-118-process-tests
FROM ${WORKBENCH_AGENT_IMAGE}
# Always test candidate modules, not stale code in a previously built base tag.
COPY *.mjs /opt/puppyone-agent/
COPY providers/docker-recovery.mjs /opt/puppyone-agent/providers/
RUN --network=none PUPPYONE_WORKBENCH_ACCEPTANCE=1 node --test /opt/puppyone-agent/workbench-process.docker.test.mjs
RUN --network=none PUPPYONE_WORKBENCH_ACCEPTANCE=1 node --test /opt/puppyone-agent/workbench-git.docker.test.mjs
