# Share a cloud computer across Agent tasks

## Why
ISSUE-118 and the user's 2026-10-13 decision define a workbench as another
computer private to one user. That user's Agents, terminal and editor share its
directory and branch; different users never share the live workspace, even when
they can access the same Project. Platform provisioning happens on workspace
creation, not on Chat submission. Current Session recovery and UID-wide cleanup can kill another task or
roll back its files. The user approved implementation, with sandbox tests first.

## What Changes
- Separate durable workbench identity/resource lifecycle from individual Runs.
- Workspace creation owns single-flight allocation and initial repository preparation;
  opening/waking an existing workspace owns resume. Chat only attaches to a ready
  workspace and has no create, restore or wake capability.
- Use one user filesystem identity within a workspace; isolate task termination
  independently rather than treating same-user Agents as separate file tenants.
- Add task-scoped process ownership and shared-directory recovery semantics.
- Retain the existing dispatcher, PostgreSQL CAS, authorization and native Git
  publication funnel; make the selected workbench the only new execution contract.
- **BREAKING**: the user confirmed on 2026-10-14 that pre-launch Chats need not
  be migrated or retained. Remove the Session-owned execution fallback rather
  than maintaining two permanent runtimes. Historical migrations remain immutable;
  any existing provider resource must still have confirmed cleanup ownership.
- Bind refs, interaction policy, activity and resource usage to the workbench.
- Test real sandbox processes and populated migration before activating paths.

## Impact
Cloud runtime/provider/controller, additive database migrations, authorization,
Version Engine's selected-ref contract, and canonical runtime documentation.
Desktop and Web UI are outside this backend change. No deployment is authorized.
The user approved completing all A1–A6 after the 2026-10-14 architecture review.
No paid provider/model resources, hosted mutation or remote push are authorized.
Implementation, local validation, provider acceptance and closure remain distinct.

