## Implementation

- [x] Add durable identity, invitation, grant, credential renewal and session schema with backend-only transitions.
- [x] Implement shared policy types and lifecycle services with authenticated APIs.
- [x] Integrate SSH and repository credential admission without broadening legacy credentials.
- [x] Connect session revocation and cleanup to the existing Workspace owner.
- [x] Verify policy, protocol, real SQL races, populated upgrade and existing regressions.
- [x] Update canonical architecture and record exact acceptance and deployment boundaries.

## Remaining acceptance

- [ ] Add checked request-status and human-readable management reads.
- [ ] Ship durable CLI registration, invitation application, crash-safe renewal and pinned SSH connection.
- [ ] Add invitation/approval/revocation UI in the existing Access feature and recipient portal.
- [ ] Verify client recovery, UI isolation, SQL authorization and complete SSH acceptance; update canonical documentation.

- [ ] Run the registered-Agent real SSH/VM scenario once the local Docker start failure is resolved, or with an explicitly configured disposable Fly fixture. See `verification.md`; local SQL/HTTP success is not live-provider acceptance.
