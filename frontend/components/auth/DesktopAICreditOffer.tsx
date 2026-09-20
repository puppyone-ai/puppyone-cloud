'use client';

import { useEffect, useState } from 'react';

/** Desktop sign-in is an account entry point, independent of hosting subscriptions. */
export function DesktopAICreditOffer() {
  const [offer, setOffer] = useState<{ trial: number; packs: number[] } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetch('/api/backend/api/v1/ai/catalog', { signal: controller.signal, credentials: 'omit' })
      .then(async response => {
        if (!response.ok) return;
        const value = await response.json();
        if (controller.signal.aborted) return;
        setOffer({
          trial: Number.isSafeInteger(value.trial_credit_micro_usd) && value.trial_credit_micro_usd > 0
            ? value.trial_credit_micro_usd / 1_000_000 : 0,
          packs: Array.isArray(value.packs) ? value.packs.flatMap((pack: { price_cents?: number }) =>
            Number.isSafeInteger(pack.price_cents) && (pack.price_cents ?? 0) > 0 ? [pack.price_cents! / 100] : []) : [],
        });
      }).catch(() => { /* Login remains usable when the price catalog is unavailable. */ });
    return () => controller.abort();
  }, []);
  const money = (amount: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount);
  return <section aria-label="PuppyOne AI balance" className="mb-5 rounded-lg border border-[var(--po-border)] p-4 text-sm">
    <h2 className="font-semibold">Your PuppyOne account</h2>
    <p className="mt-2 text-[var(--po-text-muted)]">Use PuppyOne AI in Desktop. Pay as you go, with no subscription.</p>
    {offer && offer.trial > 0 && <p className="mt-2">Sign in to claim {money(offer.trial)} in AI credit, once per account.</p>}
    {offer && offer.packs.length > 0 && <p className="mt-2 text-[var(--po-text-muted)]">Top up in Desktop: {offer.packs.map(money).join(' · ')}.</p>}
  </section>;
}
