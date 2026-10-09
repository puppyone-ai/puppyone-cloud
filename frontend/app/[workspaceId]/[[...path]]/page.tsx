import { notFound } from 'next/navigation';
import { isWorkspaceId, workspaceResourcePath, decodeWorkspacePath } from '@puppyone/cloud-core';
import { ResourcePreview } from '@/features/resource-preview/ResourcePreview';
import { WorkspaceNavigationProvider } from '@/features/workspace/navigation';

export const dynamic = 'force-dynamic';
export const metadata = {
  title: 'Workspace preview · Puppyone',
  robots: { index: false, follow: false },
};

export default async function WorkspaceResourcePage({ params, searchParams }: {
  params: Promise<{ workspaceId: string; path?: string[] }>;
  searchParams: Promise<{ path_bytes_b64?: string | string[] }>;
}) {
  const { workspaceId, path = [] } = await params;
  if (!isWorkspaceId(workspaceId)) notFound();
  // Next 15 passes URI-encoded dynamic params (getDynamicParam). Decode each
  // segment once; a literal "%2F" filename arrives as "%252F" and stays literal.
  let resourcePath: string;
  try {
    resourcePath = decodeWorkspacePath(path);
    workspaceResourcePath(workspaceId, resourcePath);
  } catch { notFound(); }
  const bytes = (await searchParams).path_bytes_b64;
  if (bytes && (typeof bytes !== 'string' || resourcePath || bytes.length > 2668 || !/^[A-Za-z0-9+/]+={0,2}$/.test(bytes))) notFound();
  return <WorkspaceNavigationProvider><ResourcePreview projectId={workspaceId.toLowerCase()} path={resourcePath} pathBytes={typeof bytes === 'string' ? bytes : undefined} /></WorkspaceNavigationProvider>;
}
