/**
 * Icons used across the access page.
 *
 * Two flavours coexist:
 *  - Provider logos (Gmail / Sheets / GitHub / Notion image URLs +
 *    inline SVG fallbacks for cli / agent / mcp / sandbox / generic).
 *  - Action / chrome glyphs (Pause / Play / Retry / Edit / More /
 *    Folder / File / Copy / Chevron) — used by buttons, badges,
 *    tree-preview rows.
 *
 * Kept in one file because they're all small SVGs that share the
 * same currentColor pattern; splitting per-icon would add 12 files
 * for ~10 lines each.
 */

import { T } from '@/features/access/lib/tokens';
import {
  isCliProvider,
  isGitRemoteProvider,
  normalizeConnectorProvider,
} from '@/lib/accessProviderRegistry';
import { resolveProviderIconUrl } from '@/lib/providerIcons';

export function ProviderIcon({
  provider,
  size = 16,
  variant = 'brand',
}: {
  readonly provider: string;
  readonly size?: number;
  readonly variant?: 'brand' | 'mono';
}) {
  const normalizedProvider = normalizeConnectorProvider(provider);
  const logo = resolveProviderIconUrl({ provider: normalizedProvider });
  if (logo) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={logo} alt={normalizedProvider} width={size} height={size} style={{ display: 'block', borderRadius: 2 }} />;
  }
  if (isCliProvider(normalizedProvider)) {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="4 17 10 11 4 5" />
        <line x1="12" y1="19" x2="20" y2="19" />
      </svg>
    );
  }
  if (isGitRemoteProvider(normalizedProvider)) {
    if (variant === 'mono') {
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M7.3 6.4 12 11.1v6.6M12 11.1l4.7 4.5"
            stroke="currentColor"
            strokeWidth="2.15"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <circle cx="7.3" cy="6.4" r="2.05" fill="currentColor" />
          <circle cx="12" cy="11.1" r="2.05" fill="currentColor" />
          <circle cx="12" cy="17.7" r="2.05" fill="currentColor" />
          <circle cx="16.7" cy="15.6" r="2.05" fill="currentColor" />
        </svg>
      );
    }
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src="/assets/brand/git-icon-inverse.svg"
        alt="Git"
        width={size}
        height={size}
        style={{ display: 'block', borderRadius: 5 }}
      />
    );
  }
  if (normalizedProvider === 'agent') {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="var(--po-file-accent-audio)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v6m0 10v6m11-11h-6m-10 0H1m17.07-7.07l-4.24 4.24m-5.66 5.66l-4.24 4.24m12.73 0l-4.24-4.24m-5.66-5.66L1.93 4.93" />
      </svg>
    );
  }
  if (normalizedProvider === 'mcp') {
    return (
      <svg width={size} height={size} viewBox="0 0 186 195" fill="none" aria-hidden>
        <path
          d="M25 97.8528L92.8822 29.9706C102.255 20.598 117.451 20.598 126.823 29.9706C136.196 39.3431 136.196 54.5391 126.823 63.9117L75.5581 115.177"
          stroke="currentColor"
          strokeWidth="12"
          strokeLinecap="round"
        />
        <path
          d="M76.2652 114.47L126.823 63.9117C136.196 54.5391 151.392 54.5391 160.765 63.9117L161.118 64.2652C170.491 73.6378 170.491 88.8338 161.118 98.2063L99.7248 159.6C96.6006 162.724 96.6006 167.789 99.7248 170.913L112.331 183.52"
          stroke="currentColor"
          strokeWidth="12"
          strokeLinecap="round"
        />
        <path
          d="M109.853 46.9411L59.6482 97.1457C50.2756 106.518 50.2756 121.714 59.6482 131.087C69.0208 140.459 84.2167 140.459 93.5893 131.087L143.794 80.8822"
          stroke="currentColor"
          strokeWidth="12"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  if (normalizedProvider === 'sandbox') {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="var(--po-warning)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M9 9l2 2-2 2M13 15h2" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="var(--po-text-subtle)" strokeWidth="2">
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}

export const PauseIcon = ({ size = 11 }: { readonly size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
);
export const PlayIcon = ({ size = 11 }: { readonly size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
);
export const RetryIcon = ({ size = 11 }: { readonly size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
  </svg>
);
export const EditIcon = ({ size = 10 }: { readonly size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4z" /></svg>
);
export const ChevronRightIcon = ({ size = 10 }: { readonly size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
);
export const MoreVerticalIcon = ({ size = 12 }: { readonly size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="12" cy="19" r="1.6" /></svg>
);
export const FolderGlyph = ({ size = 11, color = 'var(--po-accent)' }: { readonly size?: number; readonly color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
    <path
      d="M4 20H20C21.1046 20 22 19.1046 22 18V8C22 6.89543 21.1046 6 20 6H13.8284C13.298 6 12.7893 5.78929 12.4142 5.41421L10.5858 3.58579C10.2107 3.21071 9.70201 3 9.17157 3H4C2.89543 3 2 3.89543 2 5V18C2 19.1046 2.89543 20 4 20Z"
      fill={color}
      fillOpacity="0.45"
    />
  </svg>
);

// The same shape ExplorerSidebar uses for an open folder row. We share
// the glyph (not the component, to avoid a cross-page import) so the
// scope card visibly says "this AP is mounted under that node in your
// data tree" — instead of using a generic stroke icon that has nothing
// to do with the sidebar's vocabulary. ONLY used for the right-pane
// mount-point card. The access sidebar now uses text + connector
// logo chips instead of this pin.
export const ScopeFolderGlyph = ({ size = 16 }: { readonly size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
    <path
      d="M4 20H20C21.1046 20 22 19.1046 22 18V8C22 6.89543 21.1046 6 20 6H13.8284C13.298 6 12.7893 5.78929 12.4142 5.41421L10.5858 3.58579C10.2107 3.21071 9.70201 3 9.17157 3H4C2.89543 3 2 3.89543 2 5V18C2 19.1046 2.89543 20 4 20Z"
      fill="var(--po-accent)"
      fillOpacity="0.25"
    />
    <path
      d="M 9.5 10 L 23 10 Q 24 10 23.5 11 L 19.5 19 Q 19 20 18 20 L 4.5 20 Q 3.5 20 4 19 L 8 11 Q 8.5 10 9.5 10 Z"
      fill="var(--po-accent)"
      fillOpacity="0.55"
    />
  </svg>
);

/**
 * Sidebar-only glyph for an access-point row.
 *
 * Reusing the data view's filled-blue folder here would have read as
 * "this is a folder" — but the *access page* sidebar is a list of
 * mount endpoints, not a file tree. To keep the surfaces visually
 * distinct (folder = data view's primary subject; access endpoint =
 * access view's primary subject) we use a "scope pin": same blue
 * accent so the sidebars feel like the same product, but a circular
 * node-marker shape that can never be confused with a folder. The
 * outer ring + inner dot is a common cartography / network-node
 * idiom and reads as "an endpoint at this path".
 */
export const ScopePinGlyph = ({ size = 16 }: { readonly size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden>
    <circle cx="8" cy="8" r="5.25" fill="var(--po-accent)" fillOpacity="0.18" stroke="var(--po-accent)" strokeOpacity="0.75" strokeWidth="1.25" />
    <circle cx="8" cy="8" r="1.75" fill="var(--po-accent)" fillOpacity="0.95" />
  </svg>
);
export const FileGlyph = ({ size = 11, color = T.text3 }: { readonly size?: number; readonly color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
  </svg>
);
export const CopyIcon = ({ size = 12 }: { readonly size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
);
