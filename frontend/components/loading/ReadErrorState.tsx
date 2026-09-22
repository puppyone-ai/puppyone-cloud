export function ReadErrorState({ label, onRetry }: { label: string; onRetry: () => void }) {
  return (
    <div role='alert' className='flex flex-wrap items-center gap-2 p-3 text-sm text-[var(--po-text-muted)]'>
      <span>{label}</span>
      <button type='button' onClick={onRetry} className='rounded px-2 py-1 underline hover:bg-[var(--po-hover)]'>Retry</button>
    </div>
  );
}
