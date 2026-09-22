'use client';

import clsx from 'clsx';
import { forwardRef, type ButtonHTMLAttributes, type ReactNode, type SVGProps } from 'react';

type DesktopChromeActionProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
  active?: boolean;
  expanded?: boolean;
  icon: ReactNode;
  label: string;
  meta?: ReactNode;
};

/**
 * Web counterpart of Desktop's titlebar action contract.
 * Keep shell actions icon-first, borderless, and visually quiet until hover.
 */
export const DesktopChromeAction = forwardRef<HTMLButtonElement, DesktopChromeActionProps>(function DesktopChromeAction({
  active = false,
  expanded = false,
  icon,
  label,
  meta,
  className,
  ...props
}, ref) {
  return (
    <button
      {...props}
      ref={ref}
      type='button'
      title={props.title ?? label}
      aria-label={props['aria-label'] ?? label}
      aria-pressed={props['aria-pressed'] ?? active}
      data-chrome-action=''
      className={clsx(
        'inline-flex h-6 min-w-8 cursor-pointer appearance-none items-center justify-center rounded-[5px] border-0 px-1.5 text-[var(--po-text-muted)] outline-none transition-[background,color,box-shadow] duration-100 disabled:cursor-default disabled:opacity-60',
        'hover:bg-[var(--po-hover)] hover:text-[var(--po-text)] focus-visible:bg-[var(--po-hover)] focus-visible:text-[var(--po-text)] focus-visible:shadow-[inset_0_0_0_1px_var(--po-focus-ring)]',
        active && 'bg-[var(--po-selected)] text-[var(--po-text)]',
        meta !== undefined || expanded ? 'gap-1.5 px-2' : 'w-8',
        className,
      )}
    >
      <span className='flex h-[15px] w-[15px] flex-shrink-0 items-center justify-center' aria-hidden='true'>
        {icon}
      </span>
      {expanded && (
        <span data-chrome-label='' className='text-[12px] font-medium leading-none text-current'>
          {label}
        </span>
      )}
      {meta !== undefined && (
        <span className='text-[12px] font-normal leading-none tabular-nums text-current'>
          {meta}
        </span>
      )}
    </button>
  );
});

export function DesktopChromeDivider() {
  return <span aria-hidden='true' className='mx-[3px] h-[18px] w-px flex-shrink-0 bg-[var(--po-divider)]' />;
}

export function AgentEntryIcon({ size = 15, ...props }: SVGProps<SVGSVGElement> & { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox='0 0 24 24'
      fill='none'
      stroke='currentColor'
      strokeWidth='1.8'
      strokeLinecap='round'
      strokeLinejoin='round'
      {...props}
    >
      <path d='M6 3.5h12A4.5 4.5 0 0 1 22.5 8v5.5A4.5 4.5 0 0 1 18 18h-7l-3.9 3.05a1.1 1.1 0 0 1-1.77-.98l.25-2.55A4.5 4.5 0 0 1 1.5 13.5V8A4.5 4.5 0 0 1 6 3.5Z' />
    </svg>
  );
}

export function VersionControlIcon({ size = 15, ...props }: SVGProps<SVGSVGElement> & { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox='0 0 24 24'
      fill='none'
      stroke='currentColor'
      strokeWidth='2'
      strokeLinecap='round'
      strokeLinejoin='round'
      {...props}
    >
      <circle cx='5' cy='6' r='3' />
      <path d='M5 9v12' />
      <circle cx='19' cy='18' r='3' />
      <path d='m15 9-3-3 3-3' />
      <path d='M12 6h5a2 2 0 0 1 2 2v7' />
    </svg>
  );
}

export function AccessChainIcon({ size = 15, ...props }: SVGProps<SVGSVGElement> & { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox='0 0 24 24'
      fill='none'
      stroke='currentColor'
      strokeWidth='2'
      strokeLinecap='round'
      strokeLinejoin='round'
      {...props}
    >
      <path d='M9 17H7A5 5 0 0 1 7 7h2' />
      <path d='M15 7h2a5 5 0 1 1 0 10h-2' />
      <path d='M8 12h8' />
    </svg>
  );
}
