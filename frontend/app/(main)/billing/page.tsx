import { CreditCard, Monitor } from 'lucide-react';
import { OrganizationPageShell } from '@/components/organization/OrganizationPageShell';

/**
 * The hosted Web billing writer is intentionally retired. Desktop is the only
 * commercial entry point and obtains every price, quote, and usage value from
 * the PuppyOne Billing BFF. Keeping this route informational prevents an old
 * cached Web bundle from creating an order with the retired price matrix.
 */
export default function BillingPage() {
  return (
    <OrganizationPageShell
      title="Billing"
      description="Billing changes are managed in PuppyOne Desktop."
    >
      <section className="overflow-hidden rounded-lg border border-[var(--po-border)] bg-[var(--po-panel)]">
        <div className="flex items-start gap-4 p-6">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--po-control)] text-[var(--po-text-muted)]">
            <Monitor size={20} aria-hidden="true" />
          </span>
          <div>
            <h2 className="text-[16px] font-semibold text-[var(--po-text)]">
              Continue in PuppyOne Desktop
            </h2>
            <p className="mt-2 max-w-[620px] text-[13px] leading-6 text-[var(--po-text-muted)]">
              Open the Billing section in the signed-in Desktop app to view the authoritative
              catalog, usage, seat quote, checkout, and customer portal. This Web route cannot
              create or change a subscription.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 border-t border-[var(--po-border-subtle)] px-6 py-3 text-[12px] text-[var(--po-text-subtle)]">
          <CreditCard size={14} aria-hidden="true" />
          Payment links are issued only after an owner confirms a server-generated quote.
        </div>
      </section>
    </OrganizationPageShell>
  );
}
