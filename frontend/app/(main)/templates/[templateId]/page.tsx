import { TemplateDetailView } from '@/components/templates/TemplateDetailView';

export default async function TemplateDetailPage({
  params,
}: {
  params: Promise<{ templateId: string }>;
}) {
  const { templateId } = await params;
  return <TemplateDetailView templateId={templateId} />;
}
