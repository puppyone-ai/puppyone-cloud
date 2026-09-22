import { fireEvent } from '@testing-library/react';
import type { EditorView } from '@codemirror/view';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { preserveMarkdownScroll } from '@/features/files/editor/preserveMarkdownScroll';

let resize: () => void;
const disconnect = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { resize = callback; }
    observe() {}
    disconnect = disconnect;
  });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function editor() {
  let width = 390;
  let anchor = 'top';
  const dom = document.createElement('div');
  const scrollDOM = document.createElement('div');
  dom.append(scrollDOM);
  Object.defineProperty(dom, 'clientWidth', { get: () => width });
  const view = {
    dom, scrollDOM, state: { doc: {} },
    scrollSnapshot: vi.fn(() => ({ anchor })),
    dispatch: vi.fn(),
  };
  const dispose = preserveMarkdownScroll(view as unknown as EditorView);
  return {
    view, dispose,
    reflow(next: number) { width = next; resize(); vi.runAllTimers(); },
    scroll(to: string, user = true) {
      anchor = to;
      if (user) fireEvent.wheel(dom);
      fireEvent.scroll(scrollDOM);
      vi.runAllTimers();
    },
  };
}

it('restores the reading anchor after an expanded layout clamps native scrolling to the top', () => {
  const { view, scroll, reflow, dispose } = editor();
  scroll('paragraph 24');
  reflow(1280);
  scroll('top', false); // Browser clamping is not new reading intent.
  reflow(390);
  expect(view.dispatch.mock.calls.map(([update]) => update.effects.anchor)).toEqual(['paragraph 24', 'paragraph 24']);
  scroll('paragraph 10');
  reflow(820);
  expect(view.dispatch).toHaveBeenLastCalledWith({ effects: { anchor: 'paragraph 10' } });
  dispose();
});

it('leaves height-only keyboard/caret scrolling alone and discards stale document anchors', () => {
  const { view, scroll, reflow, dispose } = editor();
  scroll('paragraph 24');
  resize(); // Keyboard changed the height, but not the editor width.
  vi.runAllTimers();
  expect(view.dispatch).not.toHaveBeenCalled();
  view.state.doc = {};
  view.scrollSnapshot.mockReturnValueOnce({ anchor: 'new document' });
  reflow(820);
  expect(view.dispatch).not.toHaveBeenCalled();
  reflow(390);
  expect(view.dispatch).toHaveBeenLastCalledWith({ effects: { anchor: 'new document' } });
  dispose();
});

it('cancels queued layout work when the document/viewer is disposed', () => {
  const { view, dispose } = editor();
  fireEvent.wheel(view.dom);
  dispose();
  vi.runAllTimers();
  expect(view.scrollSnapshot).toHaveBeenCalledOnce();
  expect(view.dispatch).not.toHaveBeenCalled();
  expect(disconnect).toHaveBeenCalled();
});
