'use client';

import Link from 'next/link';
import { forwardRef, type ComponentProps } from 'react';
import { useOptionalWorkspaceNavigation } from './NavigationProvider';

type Props = Omit<ComponentProps<typeof Link>, 'href'> & { href: string };

/** Keep native link semantics, including modifier clicks and prefetching. */
export const WorkspaceLink = forwardRef<HTMLAnchorElement, Props>(function WorkspaceLink({ href, onClick, replace, scroll, ...props }, ref) {
  const navigation = useOptionalWorkspaceNavigation();
  return <Link {...props} ref={ref} href={href} replace={replace} scroll={scroll} onClick={event => {
    onClick?.(event);
    if (!navigation || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || props.download || (props.target && props.target !== '_self') || !href.startsWith('/') || href.startsWith('//')) return;
    event.preventDefault();
    navigation.navigate(href, { replace, scroll });
  }} />;
});
