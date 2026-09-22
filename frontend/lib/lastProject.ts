const LAST_PROJECT_KEY = 'puppyone:last-project-id';

export function rememberLastProject(projectId: string): void {
  if (typeof window === 'undefined' || !projectId) return;

  try {
    window.localStorage.setItem(LAST_PROJECT_KEY, projectId);
  } catch {
    // Local storage can be unavailable in private or hardened browser modes.
  }
}

export function getLastProjectId(): string | null {
  if (typeof window === 'undefined') return null;

  try {
    return window.localStorage.getItem(LAST_PROJECT_KEY);
  } catch {
    return null;
  }
}
