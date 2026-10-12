# Supplemental process acceptance only; not provider lifecycle acceptance.
FROM puppyone-cloud-agent:issue-118-process-tests
COPY workbench-process.docker.test.mjs /opt/puppyone-agent/
RUN --network=none PUPPYONE_WORKBENCH_ACCEPTANCE=1 node --test /opt/puppyone-agent/workbench-process.docker.test.mjs
