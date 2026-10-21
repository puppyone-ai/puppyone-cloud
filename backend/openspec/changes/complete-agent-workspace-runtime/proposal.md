# Change: Complete the live Agent workspace runtime

## Why
The modular Loop/Execution/Filesystem ports still leave product reads on published Git and provider dispatch inside the Pi channel. Interactive sessions need one user/project workspace with live native files and a backend-selected provider.

## What Changes
- Complete shared workspace admission/lifecycle and live file APIs; isolate automations and other users.
- Keep Loop, Execution and Filesystem independent; compose concrete sandbox providers below them.
- Add explicit Docker/E2B/Fly adapters and capabilities. Persist provider/artifact/resource bindings; configuration affects new allocations only.
- Route the cloud file surface to the selected live workspace, preserving dirty drafts and surfacing save conflicts; history remains Git-backed.
- Reuse the independent-ref publication work from ISSUE-118 and its adversarial acceptance cases.

## Impact
Affected specs: agent-chat. Affected code: Agent runtime, scope_sandbox execution, cloud workbench lifecycle, file UI and additive database migrations.
The user explicitly approved implementation and subsequently requested real Fly end-to-end testing, including the disposable provider resources necessary for that test. Production activation and production schema changes remain outside this test.
