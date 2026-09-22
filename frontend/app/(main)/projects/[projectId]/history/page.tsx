import { redirect } from 'next/navigation';
import { gitHref } from '@/features/workspace/navigation/routes';

export default async function LegacyHistoryRoute({ params }: { params: Promise<{ projectId: string }> }) {
  redirect(gitHref((await params).projectId));
}
