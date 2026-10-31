"""PuppyOne's Git-native Version Engine.

The Version Engine turns Product writes, AP-FS/CLI commands, Git pushes,
connector imports, and agent write-back into Git-compatible version facts
plus PuppyOne collaboration facts: scope policy, conflict state, audit,
history, and durable derived-work events.

The package layout mirrors the write architecture:

  domain/          Shared intents, conflict records, and errors.
  entrypoints/     L1 protocol surfaces: HTTP and Git smart HTTP.
  admission/       Repository view facts and file policy. Request admission,
                   permission checks and validation live in platform/access.
  adapters/        L4 request-to-intent adapters for product, Git, and batch.
  write_engine/    L5 write authority and Git object/tree/merge primitives.
  derived/         L5 follow-up/repair: hooks, outbox, projections, indexes, GC.
  storage/         L6 physical object-storage substrate.
  read/            Read models and read-side helpers outside the write path.
  infrastructure/  Supabase, S3, and Git transport-cache implementations.
  bootstrap/       App/worker containers and FastAPI dependency factories.
"""
