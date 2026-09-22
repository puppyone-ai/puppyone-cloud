/**
 * The Access-provider registry now lives in `@puppyone/cloud-core` (shared with
 * the desktop cloud panel, ISSUE-022). This is a compatibility re-export so the
 * existing `@/lib/accessProviderRegistry` imports keep working; new code can
 * import from `@puppyone/cloud-core/accessProviders` directly.
 */
export * from '@puppyone/cloud-core/accessProviders';
