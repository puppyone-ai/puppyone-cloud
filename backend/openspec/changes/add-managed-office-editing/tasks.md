## 1. Backend implementation

- [x] 1.1 Add fail-closed managed Office configuration.
- [x] 1.2 Add Redis session repository and private S3 binary store adapter.
- [x] 1.3 Add authenticated session/status/result/force-save/close routes.
- [x] 1.4 Add capability-protected source and callback routes.
- [x] 1.5 Add callback JWT, origin, redirect, size, expiry, and ownership tests.

## 2. Desktop implementation

- [x] 2.1 Replace the local bridge adapter with authenticated PuppyOne API transport.
- [x] 2.2 Preserve native surface isolation, atomic writes, conflicts, and recovery.
- [x] 2.3 Remove Desktop Document Server, JWT, bridge, and Docker configuration.
- [x] 2.4 Add signed-out, offline, session, save, conflict, and cleanup tests.

## 3. Documentation and verification

- [x] 3.1 Update wrapper architecture and deployment documentation.
- [x] 3.2 Record that the repository has no local `openspec` validator executable.
- [x] 3.3 Run backend focused tests, Desktop full tests, and the production build.
- [ ] 3.4 Merge validated changes into local `qubits` branches and restart Desktop.
