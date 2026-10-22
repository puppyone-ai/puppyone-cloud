# Adopt standard Git pack storage for native push

## Why

The native adapter inflated the complete incoming object set into a raw spool,
then encoded every object again into loose bytes and converted those bytes to
POB bundles. Wire size, decoded work, temporary disk and memory were conflated.
This defeated Git delta compression and made a compressed repository fail a
small aggregate expansion threshold.

## Approval and scope

Approved by the user on 2026-10-22: implement the first storage slice removing
custom incoming unpack/delta code and repeated object encoding, while retaining
PostgreSQL publication. This follows the accepted architecture in
`puppy-issues:document/puppyone/architecture/05-storage-contracts.md`.
It supersedes the no-Git-process constraint of `remove-native-git-materialization`
and `add-scalable-git-pack-ingestion` for pack storage. Their full-repository
materialization prohibition, authority, authorization and fencing remain.

## What changes

- Version Engine owns bounded stock Git indexing, thin-base completion and indexed reads.
- Native push persists a self-contained standard `.pack`; complete non-thin pack bytes remain identical.
- S3 multipart uses byte-sized parts, existing I/O credits and publication/capacity fences.
- New-format cold reads coexist with loose, POB and chunked locations.
- An additive SQL expansion admits `.pack` keys in existing location RPCs.
- Logical traversal/publication budgets share the existing setting, separate from RAM/scratch limits.

The first slice retains per-object PG compatibility metadata, HTTP input spooling,
physical S3 readback, existing fetch encoding and Product write formats. Full
repository SSD caching, pack-level catalog, validated maximum-size capacity,
pipelined receive/upload and hosted <5-second acceptance are separate work.

The follow-up corrects conflicting receive limits and separates incoming policy
from completed-pack/cold-read budgets. The user subsequently fixed the single-push ceiling at 2 GiB on 2026-10-22,
without automatic multi-push splitting. Configuration can only tighten this
ceiling. The implementation validates the related scratch profile and tests data above the former
128 MiB cap. It does not claim acceptance at the full configured maximum, raise
the single-object profile, introduce LFS, or change PG publication authority.
