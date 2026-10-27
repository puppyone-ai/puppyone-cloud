## Implementation
- [x] Generalize Desktop intent, temporary context, relay and session hand-off into delegated clients.
- [x] Add the website intent, origin allowlist, callback relay and error copy.
- [x] Keep Desktop URLs, storage namespace and behavior unchanged; extend contract and page tests.
- [x] Wire `NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS` through Dockerfile, release workflow and examples.
- [x] Verify locally against real Next runtimes for Cloud and website with a Supabase protocol stub, including a browser email hand-off.
- [x] Update canonical documentation with implementation and verification evidence.

## Release acceptance (separate from implementation)

- Configure `NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS` for each Cloud environment and `PUPPYONE_WEB_ORIGIN` for the paired website.
- Real Google/GitHub consent and the hosted staging cross-site cookie path remain release gates.
