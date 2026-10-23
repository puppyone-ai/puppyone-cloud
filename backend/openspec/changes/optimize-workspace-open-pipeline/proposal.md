# Change: Overlap workspace resume preparation and consolidate admission

## Why
The 2026-10-23 18:24 resume returned its authorized root in 22.20 seconds. Worker dispatch, database calls and provider startup took 8.95 seconds; the consuming connection then took 8.87 seconds. Three serial database calls and late private-route preparation remain avoidable.

## What Changes
- Claim an existing open intent, publish its initial phase and return current authorization facts in one fenced SQL transaction. Canonical Python authorization still precedes provider work.
- During bounded HTTP observation of an explicit opening, prepare the consumer's private route and credentials while the worker resumes the Machine. Do not connect SSH or read files until durable readiness handoff.
- Keep per-user/project observation bounded and coalesced, with paced database polling; remove Desktop's extra one-second sleep after slow responses. No new queue, Redis dependency or database connection held while waiting.
- Preserve older status clients, cold-path fallback, deadline/generation/operation fences, cancellation cleanup and final authorization. Record actual timing without promising an unmeasured target.

## Impact and approval
Workspace lifecycle/repository, opening coordinator, Fly provider, status adapter, Desktop opening client and regression/real-resource acceptance. The user explicitly approved this system-level scope in the current conversation. SQL follows protected Qubits CI/CD; local API and worker activation follows database success. No remote application deployment or resource migration is requested.
