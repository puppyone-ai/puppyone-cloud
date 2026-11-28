# Strict A2 prerequisite only, not the product/API/provider lifecycle chain.
# Native file identity regression image; executed via `docker run --cap-add=LINUX_IMMUTABLE`
# on a real /workspace volume so HEAD immutability (`chattr +i`) is exercised as in production.
ARG WORKBENCH_AGENT_IMAGE=puppyone-cloud-agent:issue-118-process-tests
FROM ${WORKBENCH_AGENT_IMAGE}
COPY *.mjs *.py /opt/puppyone-agent/
COPY providers/docker-recovery.mjs /opt/puppyone-agent/providers/
