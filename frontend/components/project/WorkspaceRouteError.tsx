'use client';

import { ReadErrorState } from '@/components/loading/ReadErrorState';

/** Unexpected route failures replace only the body, keeping project chrome. */
export default function WorkspaceRouteError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <ReadErrorState label='Could not open this view.' onRetry={reset} />;
}
