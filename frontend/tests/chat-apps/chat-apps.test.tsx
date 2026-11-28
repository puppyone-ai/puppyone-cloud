import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import messages from '@/messages/en.json';

const fixture = vi.hoisted(() => ({
  session: { user: { id: 'u1' } } as any, ready: true, query: '',
  getProject: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useParams: () => ({ projectId: 'p1' }),
  useSearchParams: () => new URLSearchParams(fixture.query),
}));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({
  useAuth: () => ({ session: fixture.session, isAuthReady: fixture.ready }),
}));
vi.mock('@/lib/projectsApi', () => ({ getProject: fixture.getProject }));

import { OpenInPuppyOnePage } from '@/features/chat-apps/OpenInPuppyOnePage';
import { desktopProjectUrl, desktopSchemeForHost, webAgentUrl } from '@/features/chat-apps/desktop-link';

const intl = (ui: ReactElement) => <NextIntlClientProvider locale="en" messages={messages}>{ui}</NextIntlClientProvider>;
const assign = vi.fn();
const replace = vi.fn();
let mobile = false;

beforeEach(() => {
  fixture.session = { user: { id: 'u1' } }; fixture.ready = true; fixture.query = 'session=s1';
  fixture.getProject.mockResolvedValue({ id: 'p1' });
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
