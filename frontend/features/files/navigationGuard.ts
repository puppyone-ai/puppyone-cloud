export function confirmEditorNavigation(dirty: boolean) {
  return !dirty || typeof window === 'undefined' || window.confirm(
    'You have unsaved changes. Leave this file? Your local draft will be kept on this device.',
  );
}
