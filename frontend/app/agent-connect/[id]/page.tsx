'use client';

import { useParams } from 'next/navigation';
import { AgentConnectApproval } from '@/features/access/lifecycle/AgentConnectApproval';

export default function AgentConnectPage() {
  const { id } = useParams<{ id: string }>();
  return <AgentConnectApproval connectionId={id} />;
}
