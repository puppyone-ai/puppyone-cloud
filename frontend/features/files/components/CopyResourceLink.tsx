'use client';

import { useState } from 'react';
import { Link2 } from 'lucide-react';
import { workspaceResourceUrl } from '@puppyone/cloud-core';
import { DesktopChromeAction } from '@/components/chrome/DesktopChrome';

export function CopyResourceLink({ projectId, path, directory }: {
  projectId: string; path: string; directory: boolean;
}) {
  const [message, setMessage] = useState('');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(workspaceResourceUrl(window.location.origin, projectId, path, directory));
      setMessage('Link copied. Workspace access is required.');
    } catch { setMessage('Unable to copy this link. Please try again.'); }
  };
  return <>
    <DesktopChromeAction icon={<Link2 size={15} />} label="Copy link" title={message || 'Copy preview link'} onClick={() => void copy()} />
    <span role="status" className="sr-only">{message}</span>
  </>;
}
