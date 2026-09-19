'use client';

import { AccessChainIcon, DesktopChromeAction } from '@/components/chrome/DesktopChrome';

/** Project-level Access entry rendered with the same titlebar grammar as Desktop. */
export function AccessPointsHeaderButton({
  scopeCount,
  isOpen,
  onClick,
}: {
  scopeCount: number;
  isOpen: boolean;
  onClick: () => void;
}) {
  return (
    <DesktopChromeAction
      active={isOpen}
      icon={<AccessChainIcon />}
      label='Access'
      meta={scopeCount}
      onClick={onClick}
      title={`${scopeCount} access ${scopeCount === 1 ? 'point' : 'points'} in this project`}
      aria-label='Manage access points'
    />
  );
}
