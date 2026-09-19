import GitWorkspace from '@/features/history/GitWorkspace';

export default async function GitRoute({ params }: { params: Promise<{ projectId: string }> }) {
  return <GitWorkspace projectId={(await params).projectId} />;
}
