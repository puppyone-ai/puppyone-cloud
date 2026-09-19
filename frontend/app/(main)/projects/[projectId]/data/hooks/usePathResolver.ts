'use client';
import { usePathResolver as useFilePathResolver } from '@/features/files/usePathResolver';
import { setPendingActiveId } from '../components/explorer';

const onResolved = () => setPendingActiveId(null);
export function usePathResolver(projectId: string, path: string[], typeHint?: string) {
  return useFilePathResolver(projectId, path, typeHint, onResolved);
}
