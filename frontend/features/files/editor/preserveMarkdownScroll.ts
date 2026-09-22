import type { EditorView } from '@codemirror/view';

/** Keep a document anchor through width reflow, including an intermediate
 * layout whose content fits completely and clamps native scrollTop to zero.
 * Height-only keyboard changes retain CodeMirror's native caret scrolling. */
export function preserveMarkdownScroll(view: EditorView) {
  let width = view.dom.clientWidth;
  let snapshot = view.scrollSnapshot();
  let snapshotDocument = view.state.doc;
  let userIntent = false;
  let captureFrame = 0;
  let restoreFrame = 0;

  const capture = () => {
    if (!userIntent || view.dom.clientWidth !== width || captureFrame) return;
    captureFrame = requestAnimationFrame(() => {
      captureFrame = 0;
      if (!userIntent || view.dom.clientWidth !== width) return;
      snapshot = view.scrollSnapshot();
      snapshotDocument = view.state.doc;
    });
  };
  const interact = () => {
    userIntent = true;
    cancelAnimationFrame(restoreFrame);
    restoreFrame = 0;
    capture();
  };
  const observer = new ResizeObserver(() => {
    const nextWidth = view.dom.clientWidth;
    if (!nextWidth || nextWidth === width) return;
    width = nextWidth;
    userIntent = false;
    cancelAnimationFrame(captureFrame);
    captureFrame = 0;
    cancelAnimationFrame(restoreFrame);
    restoreFrame = requestAnimationFrame(() => {
      restoreFrame = 0;
      // A server update may replace the document without user input. Never
      // apply an anchor captured for a different document to that new content.
      if (view.state.doc !== snapshotDocument) {
        snapshot = view.scrollSnapshot();
        snapshotDocument = view.state.doc;
        return;
      }
      view.dispatch({ effects: snapshot });
    });
  });
  observer.observe(view.dom);
  view.scrollDOM.addEventListener('scroll', capture, { passive: true });
  const inputs = ['wheel', 'touchstart', 'pointerdown', 'keydown', 'input'] as const;
  inputs.forEach(type => view.dom.addEventListener(type, interact, { capture: true, passive: true }));
  return () => {
    observer.disconnect();
    cancelAnimationFrame(captureFrame);
    cancelAnimationFrame(restoreFrame);
    view.scrollDOM.removeEventListener('scroll', capture);
    inputs.forEach(type => view.dom.removeEventListener(type, interact, true));
  };
}
