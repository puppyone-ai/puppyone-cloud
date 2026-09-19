import { fireEvent, render, screen } from '@testing-library/react';
import { useRef } from 'react';
import { expect, it, vi } from 'vitest';
import { useSidebarSwipe } from '@/features/workspace/useSidebarSwipe';

function Sidebar({ side = 'right', enabled = true, onClose }: { side?: 'left' | 'right'; enabled?: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useSidebarSwipe(ref, enabled, side, onClose);
  return <div ref={ref} data-testid='sidebar'><header data-sidebar-swipe-surface data-testid='chrome'>Sidebar<input aria-label='Draft' /></header><pre data-testid='code'>Scrollable code</pre></div>;
}
function pointer(target: HTMLElement, type: string, x: number, y: number, pointerType = 'touch') {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y });
  Object.defineProperties(event, { pointerId: { value: 1 }, pointerType: { value: pointerType }, isPrimary: { value: true } });
  fireEvent(target, event);
}
function swipe(target: HTMLElement, dx: number, dy = 0, pointerType = 'touch', cancelled = false) {
  pointer(target, 'pointerdown', 160, 100, pointerType);
  pointer(target, 'pointermove', 160 + dx, 100 + dy, pointerType);
  pointer(target, cancelled ? 'pointercancel' : 'pointerup', 160 + dx, 100 + dy, pointerType);
}

it.each([['left', -80], ['right', 80]] as const)('dismisses the %s sidebar toward its own edge and resets its drag offset', (side, dx) => {
  const close = vi.fn();
  render(<Sidebar side={side} onClose={close} />);
  swipe(screen.getByTestId('chrome'), dx);
  expect(close).toHaveBeenCalledOnce();
  expect(screen.getByTestId('sidebar').style.getPropertyValue('--sidebar-drag-x')).toBe('');
  expect(screen.getByTestId('sidebar').hasAttribute('data-swiping')).toBe(false);
});

it('ignores vertical scrolling, opposite-direction swipes, short drags, cancellation and mouse selection', () => {
  const close = vi.fn();
  render(<Sidebar onClose={close} />);
  const chrome = screen.getByTestId('chrome');
  swipe(chrome, 80, 120);
  swipe(chrome, -80);
  swipe(chrome, 30);
  swipe(chrome, 80, 0, 'touch', true);
  swipe(chrome, 80, 0, 'mouse');
  expect(close).not.toHaveBeenCalled();
});

it('preserves editing/scrollable content and disables gesture handlers on desktop', () => {
  const close = vi.fn();
  const { rerender } = render(<Sidebar onClose={close} />);
  swipe(screen.getByLabelText('Draft'), 80);
  swipe(screen.getByTestId('code'), 80);
  rerender(<Sidebar enabled={false} onClose={close} />);
  swipe(screen.getByTestId('chrome'), 80);
  expect(close).not.toHaveBeenCalled();
});

it('keeps tracking when implicit touch capture transfers from a child to the panel', () => {
  const close = vi.fn();
  render(<Sidebar onClose={close} />);
  const chrome = screen.getByTestId('chrome');
  pointer(chrome, 'pointerdown', 160, 100);
  pointer(chrome, 'pointermove', 180, 100);
  pointer(chrome, 'lostpointercapture', 180, 100);
  pointer(screen.getByTestId('sidebar'), 'pointermove', 250, 100);
  pointer(screen.getByTestId('sidebar'), 'pointerup', 250, 100);
  expect(close).toHaveBeenCalledOnce();
});
