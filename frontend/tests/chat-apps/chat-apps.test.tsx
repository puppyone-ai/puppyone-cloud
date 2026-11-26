import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import messages from '@/messages/en.json';

const fixture = vi.hoisted(() => ({
  session: { user: { id: 'u1' } } as any, ready: true, query: '',
  getProject: vi.fn(), link: vi.fn(), preview: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useParams: () => ({ projectId: 'p1' }),
  useSearchParams: () => new URLSearchParams(fixture.query),
}));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({
  useAuth: () => ({ session: fixture.session, isAuthReady: fixture.ready }),
}));
vi.mock('@/lib/projectsApi', () => ({ getProject: fixture.getProject }));
vi.mock('@/features/chat-apps/chatAppsApi', () => ({ linkChatIdentity: fixture.link, previewChatIdentity: fixture.preview }));

import { OpenInPuppyOnePage } from '@/features/chat-apps/OpenInPuppyOnePage';
import { AccountLinkPage } from '@/features/chat-apps/AccountLinkPage';
import { ChatAppActivityList } from '@/features/chat-apps/ChatAppActivityList';
import { desktopProjectUrl, desktopSchemeForHost, webAgentUrl } from '@/features/chat-apps/desktop-link';

const intl = (ui: ReactElement) => <NextIntlClientProvider locale="en" messages={messages}>{ui}</NextIntlClientProvider>;
const assign = vi.fn();
const replace = vi.fn();
let mobile = false;

beforeEach(() => {
  fixture.session = { user: { id: 'u1' } }; fixture.ready = true; fixture.query = 'session=s1';
  fixture.getProject.mockResolvedValue({ id: 'p1' });
  fixture.link.mockResolvedValue({});
  fixture.preview.mockResolvedValue({ platform: 'telegram', name: 'Mia', chat: 'Design team' });
  mobile = false;
  Object.defineProperty(window, 'location', { configurable: true, value: {
    pathname: '/open/projects/p1', search: '?session=s1', hostname: 'localhost', assign, replace } });
  Object.defineProperty(navigator, 'userAgent', { configurable: true, get: () => mobile ? 'iPhone Mobile' : 'Mozilla/5.0 Macintosh' });
});
afterEach(() => vi.useRealTimers());

it('picks the Desktop scheme per environment', () => {
  expect(desktopSchemeForHost('localhost')).toBe('puppyone-development');
  expect(desktopSchemeForHost('qubits.puppyone.ai')).toBe('puppyone-internal');
  expect(desktopSchemeForHost('staging.puppyone.ai')).toBe('puppyone-internal');
  expect(desktopSchemeForHost('www.puppyone.ai')).toBe('puppyone');
  expect(desktopProjectUrl('www.puppyone.ai', 'p1', 's 1')).toBe('puppyone://cloud/projects/p1?session=s%201');
  expect(desktopProjectUrl('www.puppyone.ai', 'p1')).toBe('puppyone://cloud/projects/p1');
  expect(webAgentUrl('p1', 's1')).toBe('/projects/p1/data?agent=open&session=s1');
});

it('signed out goes to login and returns to the exact URL', () => {
  fixture.session = null;
  render(intl(<OpenInPuppyOnePage />));
  expect(replace).toHaveBeenCalledWith('/login?redirect=%2Fopen%2Fprojects%2Fp1%3Fsession%3Ds1');
  expect(fixture.getProject).not.toHaveBeenCalled();
});

it('shows only the no-access message when the project cannot be read', async () => {
  fixture.getProject.mockRejectedValue({ status: 403 });
  render(intl(<OpenInPuppyOnePage />));
  await screen.findByText("You don't have access to this project.");
  expect(assign).not.toHaveBeenCalled();
  expect(screen.queryByRole('button')).toBeNull();
});

it('desktop opens the app, then falls back to the browser after 1.5s', async () => {
  vi.useFakeTimers();
  render(intl(<OpenInPuppyOnePage />));
  await act(async () => {});
  expect(screen.getByText('Opening PuppyOne…')).toBeTruthy();
  expect(assign).toHaveBeenCalledWith('puppyone-development://cloud/projects/p1?session=s1');
  expect(replace).not.toHaveBeenCalled();
  vi.spyOn(document, 'hasFocus').mockReturnValue(true);
  await act(() => vi.advanceTimersByTimeAsync(1600));
  expect(replace).toHaveBeenCalledWith('/projects/p1/data?agent=open&session=s1');
});

it('the browser button opens the Web Agent immediately', async () => {
  render(intl(<OpenInPuppyOnePage />));
  fireEvent.click(await screen.findByRole('button', { name: 'Open in the browser' }));
  expect(replace).toHaveBeenCalledWith('/projects/p1/data?agent=open&session=s1');
});

it('mobile goes straight to the Web Agent without the app scheme', async () => {
  mobile = true; fixture.query = '';
  render(intl(<OpenInPuppyOnePage />));
  await waitFor(() => expect(replace).toHaveBeenCalledWith('/projects/p1/data?agent=open'));
  expect(assign).not.toHaveBeenCalled();
});

it('link page says whose chat account it is and links only on confirm', async () => {
  fixture.query = 'code=abc';
  render(intl(<AccountLinkPage />));
  await screen.findByText('Link Telegram account “Mia” to your PuppyOne account?');
  screen.getByText('From Design team.');
  expect(fixture.preview).toHaveBeenCalledExactlyOnceWith('abc');
  expect(fixture.link).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Link account' }));
  await screen.findByText('Your account is linked. Your question will start in the chat.');
  expect(fixture.link).toHaveBeenCalledExactlyOnceWith('abc');
});

it('link page reports an expired code before asking', async () => {
  fixture.query = 'code=abc'; fixture.preview.mockRejectedValue({ status: 409 });
  render(intl(<AccountLinkPage />));
  await screen.findByText('This link expired. Ask the agent again in the chat to get a new one.');
  expect(fixture.link).not.toHaveBeenCalled();
});

it('link page sends signed-out users to login', () => {
  fixture.session = null; fixture.query = 'code=abc';
  Object.defineProperty(window.location, 'pathname', { value: '/chat-apps/link' });
  Object.defineProperty(window.location, 'search', { value: '?code=abc' });
  render(intl(<AccountLinkPage />));
  expect(replace).toHaveBeenCalledWith('/login?redirect=%2Fchat-apps%2Flink%3Fcode%3Dabc');
  expect(fixture.preview).not.toHaveBeenCalled();
  expect(fixture.link).not.toHaveBeenCalled();
});

it('renders chat_app activity rows', () => {
  const row = (id: string, status: string, phase = 'telegram') => ({
    id, kind: 'chat_app' as const, project_id: 'p1', label: 'Design team', status, phase, created_at: '2026-01-01T00:00:00Z',
  });
  render(intl(<ChatAppActivityList items={[
    row('1', 'added'), row('2', 'allowed'), row('3', 'updated'), row('4', 'taken_over'),
    row('5', 'removed'), row('6', 'removed_there'),
    { ...row('7', 'added'), kind: 'upload' as const },
  ]} />));
  for (const text of ['Added to Design team (Telegram)', 'Allowed Design team', 'Settings changed in Design team',
    'Took over Design team', 'Removed from Design team', 'Removed in Telegram: Design team'])
    expect(screen.getByText(text)).toBeTruthy();
  expect(screen.getAllByRole('listitem')).toHaveLength(6);
});
