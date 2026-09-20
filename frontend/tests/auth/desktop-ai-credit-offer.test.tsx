import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { DesktopAICreditOffer } from '@/components/auth/DesktopAICreditOffer';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('shows the current server trial and top-up options without a hosting subscription', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    trial_credit_micro_usd: 1_000_000, packs: [{ price_cents: 500 }],
  }) });
  vi.stubGlobal('fetch', fetcher);
  render(<DesktopAICreditOffer />);
  await waitFor(() => expect(screen.getByText(/Sign in to claim \$1.00/)).toBeTruthy());
  expect(screen.getByText('Top up in Desktop: $5.00.')).toBeTruthy();
  expect(fetcher).toHaveBeenCalledWith('/api/backend/api/v1/ai/catalog', expect.objectContaining({ credentials: 'omit' }));
  expect(document.body.textContent).not.toMatch(/\$15|\$30|Cloud hosting/);
});

it.each([0, null])('does not promise trial credit when it is disabled or unavailable (%s)', async (trial) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: trial !== null, json: async () => ({ trial_credit_micro_usd: trial, packs: [] }) }));
  render(<DesktopAICreditOffer />);
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(document.body.textContent).not.toContain('Sign in to claim');
  expect(screen.getByText(/no subscription/)).toBeTruthy();
});
