# Share a cloud computer across Agent tasks

## Why
ISSUE-118 and the user's 2026-10-13 decision define a workbench as another
computer. Multiple Agents, a terminal and the editor share its directory and
branch. Current Session recovery and UID-wide cleanup can kill another task or
roll back its files. The user approved implementation, with sandbox tests first.

## What Changes
- Separate durable workbench identity/resource lifecycle from individual Runs.
- Add task-scoped process ownership and shared-directory recovery semantics.
- Retain the existing dispatcher, PostgreSQL CAS, authorization and native Git
  publication funnel; keep existing clients compatible during rollout.
- Bind refs, interaction policy, activity and resource usage to the workbench.
- Test real sandbox processes and populated migration before activating paths.

## Impact
Cloud runtime/provider/controller, additive database migrations, authorization,
Version Engine's selected-ref contract, and canonical runtime documentation.
Desktop and Web UI are outside this backend change. No deployment is authorized.

