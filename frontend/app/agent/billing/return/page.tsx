export default function AgentPaymentReturn() {
  return (
    <main className="min-h-screen flex items-center justify-center bg-[var(--po-bg-base)] px-6 text-[var(--po-text)]">
      <section className="max-w-md space-y-4 text-center">
        <h1 className="text-2xl font-semibold">Return to PuppyOne</h1>
        <p>Your payment is being confirmed. Open the desktop app and refresh your Agent balance.</p>
        <p className="text-sm opacity-70">Credit appears after payment confirmation. You can then continue your local conversation.</p>
      </section>
    </main>
  );
}
