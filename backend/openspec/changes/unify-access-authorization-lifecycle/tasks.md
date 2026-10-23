## Implementation

- [x] Add durable identity, invitation, grant, credential renewal and session schema with backend-only transitions.
- [x] Implement shared policy types and lifecycle services with authenticated APIs.
- [x] Integrate SSH and repository credential admission without broadening legacy credentials.
- [x] Connect session revocation and cleanup to the existing Workspace owner.
- [x] Verify policy, protocol, real SQL races, populated upgrade and existing regressions.
- [x] Update canonical architecture and record exact acceptance and deployment boundaries.

## Client and product acceptance

- [x] Add checked request-status and human-readable management reads.
- [x] Implement durable CLI registration, invitation application, crash-safe renewal and pinned SSH connection; package publication remains separate.
- [x] Add invitation/approval/revocation UI in the existing Web Access feature and recipient portal.
- [x] Verify client recovery, UI isolation, SQL authorization and complete SSH acceptance; update canonical documentation.

- [x] Run registered-Agent and standalone CLI scenarios against disposable real Fly workspaces using the previously published compatible image. See `verification.md` for the exact scope, failed attempts and cleanup.
