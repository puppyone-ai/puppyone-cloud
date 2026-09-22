/**
 * Side-effect import barrel for Needs Action kinds.
 *
 * Each kind module ``registerKind`` at module-eval time. The Section
 * component imports this barrel so the registry is populated in a
 * predictable order before it iterates kinds.
 *
 * Adding a new plugin kind = add one ``import`` line here. The kind's
 * registration runs as a side effect; nothing else changes.
 */
import '@/features/history/components/items/conflictKind';
import '@/features/history/components/items/failedSyncKind';
import '@/features/history/components/items/pendingReviewKind';
import '@/features/history/components/items/riskyDeleteKind';
