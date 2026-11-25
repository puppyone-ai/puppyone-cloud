import { render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { expect, it, vi } from 'vitest';
import messages from '@/messages/en.json';

const fixture = vi.hoisted(() => ({ items: [] as any[], options: undefined as any }));
vi.mock('@/lib/hooks/useActivity', () => ({
  useProjectActivity: (_id: string, options: unknown) => { fixture.options = options; return { items: fixture.items }; },
}));
import { ProjectChatAppActivity } from '@/features/chat-apps/ProjectChatAppActivity';

const view = () => render(<NextIntlClientProvider locale="en" messages={messages}><ProjectChatAppActivity projectId="p1" /></NextIntlClientProvider>);

it('shows chat app rows under an Activity heading and asks for non-active chat_app history', () => {
  fixture.items = [{ id: '1', kind: 'chat_app', project_id: 'p1', label: 'Design team', status: 'added', phase: 'slack' }];
  view();
  expect(screen.getByRole('heading', { name: 'Activity' })).toBeTruthy();
  expect(screen.getByText('Added to Design team (Slack)')).toBeTruthy();
  expect(fixture.options).toMatchObject({ kind: 'chat_app', activeOnly: false });
});

it('is hidden when there is no chat app activity', () => {
  fixture.items = [];
  const { container } = view();
  expect(container.innerHTML).toBe('');
});
