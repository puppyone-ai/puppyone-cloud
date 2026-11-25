import { Suspense } from 'react';
import { OpenInPuppyOnePage } from '@/features/chat-apps/OpenInPuppyOnePage';

export default function Page() {
  return <Suspense><OpenInPuppyOnePage /></Suspense>;
}
