/** Pluggable workspace mount dispatcher for Fly Machines (booted as root). */

export const SUPPORTED_MOUNT_MODES = Object.freeze([
  'local',
  'fly_host_export',
  'juicefs_shared',
]);

const SAFE_TARGET = /^[a-zA-Z0-9._:-]{1,253}$/;
const SAFE_SCHEMA = /^[a-z][a-z0-9_]{0,62}$/;

/**
 * Resolve how `/workspace` is attached for this Machine boot.
 *
 * - `local` (Tier 1 shared_machine and legacy isolated_branch): bind-mount the
 *   Machine's local NVMe `/data/workspace` onto `/workspace`.
 * - `fly_host_export` (Tier 2 pluggable slot): mount `/workspace/repo` over
 *   Fly 6pn from the Project default Workspace machine (SSHFS / NFS-Ganesha).
 * - `juicefs_shared` (Tier 3 pluggable slot): mount a per-Project JuiceFS POSIX
 *   volume backed by isolated metadata schema and prefix-scoped S3 credentials.
 */
export function resolveMountPlan(env = process.env) {
  const mode = (env.PUPPYONE_WORKSPACE_MOUNT_MODE || 'local').trim();
  if (mode === 'local') {
    return {
      mode: 'local',
      implemented: true,
      commands: [['mount', ['--bind', '/data/workspace', '/workspace']]],
    };
  }
  if (mode === 'fly_host_export') {
    const protocol = (env.PUPPYONE_HOST_EXPORT_PROTOCOL || 'sshfs').trim();
    const target = (env.PUPPYONE_HOST_EXPORT_TARGET || '').trim();
    if (!['sshfs', 'nfs_ganesha'].includes(protocol)) {
      throw new Error(`Unsupported fly_host_export protocol: ${protocol}`);
    }
    if (!SAFE_TARGET.test(target)) {
      throw new Error('PUPPYONE_HOST_EXPORT_TARGET is required for fly_host_export');
    }
    return {
      mode: 'fly_host_export',
      implemented: false,
      protocol,
      target,
      commands: [],
    };
  }
  if (mode === 'juicefs_shared') {
    const schema = (env.PUPPYONE_JUICEFS_SCHEMA || '').trim();
    const metaUrlFile = (env.PUPPYONE_JUICEFS_META_URL_FILE || '').trim();
    if (!SAFE_SCHEMA.test(schema)) {
      throw new Error('PUPPYONE_JUICEFS_SCHEMA is required for juicefs_shared');
    }
    if (!metaUrlFile.startsWith('/run/puppyone-workbench/')) {
      throw new Error('PUPPYONE_JUICEFS_META_URL_FILE must live under /run/puppyone-workbench/');
    }
    return {
      mode: 'juicefs_shared',
      implemented: false,
      schema,
      metaUrlFile,
      commands: [],
    };
  }
  throw new Error(`Unsupported workspace mount mode: ${mode}`);
}

export function mountWorkspace(env, exec) {
  const plan = resolveMountPlan(env);
  if (!plan.implemented) {
    throw new Error(`Workspace mount mode '${plan.mode}' is not enabled in this worker artifact`);
  }
  for (const [binary, args] of plan.commands) {
    exec(binary, args);
  }
  return plan;
}
