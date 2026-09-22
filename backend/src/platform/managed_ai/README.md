# Desktop Agent Inference Gateway

This package serves model inference for the locally executing Desktop Agent.
It does not execute Agent tools or enable hosted Runtime billing.

- `router.py`: HTTP/auth boundary, personal-credit proxy and internal usage recovery.
- `schemas.py`: bounded text/function-tool inputs; no caller-supplied provider, actor or price.
- `service.py`: reservation → single execution claim → provider stream → actual usage settlement.
- `../billing/gateway.py`: existing allow-listed PuppyPay service client.
- `../auth/`: existing human identity and Desktop sign-in flow.

PuppyPay owns every balance write. Provider identity must be durable before
forwarding content; incomplete usage stays reserved for Worker recovery.
The current service still contains OpenRouter HTTP and FastAPI SSE details;
separating these adapters is follow-up, not completed layering.

Canonical architecture, data ownership and remaining work live in
[`puppy-issues/document/puppyone/architecture/control-plane/desktop-agent-inference.md`](https://github.com/puppyone-ai/puppy-issues/blob/main/document/puppyone/architecture/control-plane/desktop-agent-inference.md).
The [sandbox runbook](https://github.com/puppyone-ai/puppy-issues/blob/main/document/puppypay/operations/desktop-agent-sandbox.md)
owns environment addresses and paid-user acceptance. These documents accompany
the local `codex/local-agent-billing` delivery; publication is separate.

From `backend/`, run `.venv/bin/pytest tests/platform/managed_ai tests/platform/billing tests/auth`.
