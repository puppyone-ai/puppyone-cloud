# Implementation design

The policy is validated twice: when GitHub prepares the protected configuration
and when the hosted coordinator starts, so a malformed policy fails before any
phase. Recovery preflight still runs before quiescence. Snapshot copies are made
in the existing backup phase, which follows quiescence, so they form a
consistent recovery point; the source listing is repeated after copying and any
difference aborts the release. Copies are streamed through a bounded spool and
verified by re-reading the destination. Restore verifies the manifest digest
against release evidence and each backup object against its manifest entry
before writing it back, and only reports objects created after the snapshot.
