# Strict A2 prerequisite only, not the product/API/provider lifecycle chain.
# This target must remain red while private same-user saves are inaccessible.
ARG WORKBENCH_AGENT_IMAGE=puppyone-cloud-agent:issue-118-process-tests
FROM ${WORKBENCH_AGENT_IMAGE}
COPY *.mjs /opt/puppyone-agent/
COPY providers/docker-recovery.mjs /opt/puppyone-agent/providers/
RUN --network=none PUPPYONE_WORKBENCH_ACCEPTANCE=1 node --test /opt/puppyone-agent/workbench-files.acceptance.mjs
