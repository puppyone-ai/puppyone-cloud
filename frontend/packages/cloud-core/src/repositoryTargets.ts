export type ProjectRootTarget = {
  kind: "project_root";
  project_id: string;
};

export type ScopeTarget = {
  kind: "scope";
  project_id: string;
  scope_id: string;
};

export type RepositoryTarget = ProjectRootTarget | ScopeTarget;

export const REPOSITORY_TARGET_CONTRACT_HEADER = "X-PuppyOne-Repository-Contract";
export const REPOSITORY_TARGET_CONTRACT_VERSION = "2";

export type RepositoryView = {
  /** Presentation identity; inspect target.kind before using it as a Scope id. */
  id: string;
  target: RepositoryTarget;
  project_id: string;
  name: string;
  path: string;
  exclude: string[];
  max_mode: "r" | "rw";
  created_at: string;
  updated_at: string;
};

export function repositoryTargetKey(target: RepositoryTarget): string {
  return target.kind === "project_root"
    ? `project:${target.project_id}`
    : `scope:${target.scope_id}`;
}

export function sameRepositoryTarget(
  left: RepositoryTarget,
  right: RepositoryTarget,
): boolean {
  return repositoryTargetKey(left) === repositoryTargetKey(right)
    && left.project_id === right.project_id;
}

export function projectRootRepositoryView(projectId: string): RepositoryView {
  return {
    id: projectId,
    target: { kind: "project_root", project_id: projectId },
    project_id: projectId,
    name: "Project repository",
    path: "",
    exclude: [],
    max_mode: "rw",
    created_at: "",
    updated_at: "",
  };
}

export function repositoryScopeView(scope: {
  id: string;
  project_id: string;
  name: string;
  path: string;
  exclude: string[];
  max_mode: "r" | "rw";
  created_at: string;
  updated_at: string;
}): RepositoryView {
  return {
    ...scope,
    target: {
      kind: "scope",
      project_id: scope.project_id,
      scope_id: scope.id,
    },
  };
}

export function repositoryViewKey(view: Pick<RepositoryView, "target">): string {
  return repositoryTargetKey(view.target);
}

export function matchRepositoryViewForPath(
  path: string,
  views: readonly RepositoryView[],
): RepositoryView | null {
  const normalized = path.replaceAll(/^\/+|\/+$/g, "").replaceAll(/\/+/g, "/");
  if (!normalized) {
    return views.find((view) => view.target.kind === "project_root") ?? null;
  }
  return views.find(
    (view) => view.target.kind === "scope" && view.path === normalized,
  ) ?? null;
}
