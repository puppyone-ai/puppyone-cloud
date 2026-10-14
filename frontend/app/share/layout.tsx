import type { Metadata } from 'next';
import type { ReactNode } from 'react';

/**
 * Share links are pasted into chat apps, so this route needs its own
 * link-preview card. The root layout's generic "Context base for AI
 * agents" title made a shared project look like a stray marketing URL.
 *
 * The copy stays deliberately generic: the token is the only thing in
 * the URL, and anyone who can render an unfurl preview (a chat server,
 * a link scanner) is not necessarily authorized to learn the project
 * name. Resolving the name server-side here would leak it to every
 * bot that touches the message.
 */
export const metadata: Metadata = {
  title: 'Shared project | PuppyOne',
  description: 'Open a project that was shared with you on PuppyOne.',
  robots: { index: false, follow: false },
  openGraph: {
    title: 'You’ve been invited to a project',
    description: 'Open a project that was shared with you on PuppyOne.',
    siteName: 'PuppyOne',
    type: 'website',
  },
};

export default function ShareLayout({ children }: { children: ReactNode }) {
  return children;
}
