/**
 * Per-method metadata used by MethodCard / MethodIcon.
 *
 * The single-source accent colour drives the icon tile + prompt button so
 * each method gets a consistent identity colour.
 */

export type MethodId = 'terminal' | 'sync' | 'agent';

export interface MethodMeta {
  id: MethodId;
  title: string;
  subtitle: string;
  /** Single-source accent for this method — used by icon + prompt button. */
  accent: string;
  accentBg: string;
  accentBorder: string;
}

export const METHOD_META: Record<MethodId, MethodMeta> = {
  terminal: {
    id: 'terminal',
    title: 'Puppyone CLI',
    subtitle: 'Direct terminal access',
    accent: 'var(--po-accent-text)',
    accentBg: 'color-mix(in srgb, var(--po-accent) 14%, transparent)',
    accentBorder: 'color-mix(in srgb, var(--po-accent) 24%, transparent)',
  },
  sync: {
    id: 'sync',
    title: 'Git Remote',
    subtitle: 'Native Git clone/push',
    accent: 'var(--po-success)',
    accentBg: 'color-mix(in srgb, var(--po-success) 14%, transparent)',
    accentBorder: 'color-mix(in srgb, var(--po-success) 24%, transparent)',
  },
  agent: {
    id: 'agent',
    title: 'AI Agent',
    subtitle: 'Scoped chat agent',
    accent: 'var(--po-file-accent-audio)',
    accentBg: 'color-mix(in srgb, var(--po-file-accent-audio) 14%, transparent)',
    accentBorder: 'color-mix(in srgb, var(--po-file-accent-audio) 24%, transparent)',
  },
};
