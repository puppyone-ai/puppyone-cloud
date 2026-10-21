/**
 * Folder-upload policy — frontend mirror of the backend single source
 * of truth.
 *
 * See ``docs/proposals/PUP-3-folder-upload-policy.md`` for the product
 * contract this implements. The backend lives at
 * ``backend/src/platform/upload/policy.py``. A third mirror lives
 * in the separate ``puppyone-ai/puppyone-cli`` repository at
 * ``src/commands/fs/lib/upload-policy.js``; any constant change here
 * MUST be applied to both, and
 * ``backend/tests/ingest/test_upload_policy.py`` enforces the parity.
 *
 * Why duplicate instead of import-from-shared-JSON: cross-language
 * shared constants require a build step or runtime JSON parsing, and
 * both add weight for very rarely-changing values. We accept the drift
 * risk and have parity tests that catch constant divergence.
 */

// ── Hardcoded blocklist (Q1 minimum) ──────────────────────────────
//
// Each entry is a single path *segment* name (not a glob). Policy
// rejects a file when ANY segment of its relative path matches one
// of these names.
export const DEFAULT_BLOCKLIST_SEGMENTS: ReadonlySet<string> = new Set([
  // Version-control internals
  '.git',
  '.svn',
  '.hg',
  // OS junk
  '.DS_Store',
  'Thumbs.db',
  // Dependency / virtualenv directories
  'node_modules',
  '__pycache__',
  '.venv',
  'venv',
  'env',
  // Build output
  '.next',
  'dist',
  'build',
  '.cache',
  '.pytest_cache',
  '.mypy_cache',
  'target',
]);

// ── Thresholds (Q4) ────────────────────────────────────────────────
export const PER_FILE_MAX_BYTES = 100 * 1024 * 1024; // 100 MB
export const PER_BATCH_MAX_FILES = 5000;
export const PER_BATCH_MAX_BYTES = 1024 * 1024 * 1024; // 1 GB
export const PREFLIGHT_FILE_THRESHOLD = 50;
export const PREFLIGHT_BYTES_THRESHOLD = 100 * 1024 * 1024; // 100 MB

// ── Public helpers ─────────────────────────────────────────────────

export function isBlockedSegment(segment: string): boolean {
  return DEFAULT_BLOCKLIST_SEGMENTS.has(segment);
}

/**
 * Walk every '/' separated segment of ``path``; return the FIRST hit
 * (most useful for surfacing a reason to the user) or null if none.
 *
 * Segment-based, not substring: a file legitimately named
 * ``my.gitignore`` doesn't trip the ``.git`` block.
 */
export function pathBlockedSegment(path: string): string | null {
  for (const raw of path.split('/')) {
    const seg = raw.trim();
    if (!seg) continue;
    if (isBlockedSegment(seg)) return seg;
  }
  return null;
}

export function isDotfilePath(path: string): boolean {
  for (const raw of path.split('/')) {
    const seg = raw.trim();
    if (seg && seg.startsWith('.')) return true;
  }
  return false;
}

// ── .gitignore / .puppyignore parsing ─────────────────────────────

export interface IgnoreRule {
  pattern: string;
  negate: boolean;
  dirOnly: boolean;
}

export function parseIgnoreLine(line: string): IgnoreRule | null {
  let s = line.trim();
  if (!s || s.startsWith('#')) return null;
  let negate = false;
  if (s.startsWith('!')) {
    negate = true;
    s = s.slice(1);
  }
  const dirOnly = s.endsWith('/');
  if (dirOnly) s = s.slice(0, -1);
  if (!s) return null;
  return { pattern: s, negate, dirOnly };
}

export function parseIgnoreText(text: string): IgnoreRule[] {
  const rules: IgnoreRule[] = [];
  for (const line of text.split(/\r?\n/)) {
    const r = parseIgnoreLine(line);
    if (r) rules.push(r);
  }
  return rules;
}

function escapeRegex(s: string): string {
  return s.replace(/[.+^${}()|[\]\\]/g, '\\$&');
}

function globToRegex(pattern: string): RegExp {
  const anchored = pattern.includes('/');
  let i = 0;
  let body = '';
  while (i < pattern.length) {
    if (pattern[i] === '*' && pattern[i + 1] === '*') {
      body += '(?:.*/)?';
      i += 2;
      if (pattern[i] === '/') i++;
    } else if (pattern[i] === '*') {
      body += '[^/]*';
      i++;
    } else if (pattern[i] === '?') {
      body += '[^/]';
      i++;
    } else {
      body += escapeRegex(pattern[i]);
      i++;
    }
  }
  return anchored
    ? new RegExp(`^${body}(?:/.*)?$`)
    : new RegExp(`(?:^|.*/)(${body})(?:/.*)?$`);
}

function fnmatchSegment(segment: string, pattern: string): boolean {
  // Single-segment glob match — no '/' support needed for directory
  // rule evaluation. Translate * and ? only.
  const re = new RegExp(
    '^' +
      pattern
        .split('')
        .map((c) => {
          if (c === '*') return '[^/]*';
          if (c === '?') return '[^/]';
          return escapeRegex(c);
        })
        .join('') +
      '$',
  );
  return re.test(segment);
}

export function matchesIgnoreRules(
  path: string,
  rules: IgnoreRule[],
  opts: { isDir?: boolean } = {},
): boolean {
  let ignored = false;
  for (const rule of rules) {
    if (rule.dirOnly && !opts.isDir) {
      // Directory rule applied to a file => match on any ancestor seg.
      const segs = path.split('/');
      for (let i = 0; i < segs.length - 1; i++) {
        if (fnmatchSegment(segs[i], rule.pattern)) {
          ignored = !rule.negate;
          break;
        }
      }
      continue;
    }
    if (globToRegex(rule.pattern).test(path)) {
      ignored = !rule.negate;
    }
  }
  return ignored;
}

// ── Policy entry point ────────────────────────────────────────────

export interface PolicyOptions {
  includeBlocklist?: boolean;
  includeIgnored?: boolean;
  includeHidden?: boolean;
  extraRules?: IgnoreRule[];
}

export type PolicyReason =
  | 'ok'
  | 'too_large'
  | 'gitignore'
  | 'hidden'
  | `blocklist:${string}`;

export interface PolicyDecision {
  accept: boolean;
  reason: PolicyReason;
}

export function evaluateFile(
  relativePath: string,
  sizeBytes: number,
  rules: IgnoreRule[],
  options: PolicyOptions = {},
): PolicyDecision {
  if (sizeBytes > PER_FILE_MAX_BYTES) {
    return { accept: false, reason: 'too_large' };
  }

  const blockedSeg = pathBlockedSegment(relativePath);
  if (blockedSeg && !options.includeBlocklist) {
    return { accept: false, reason: `blocklist:${blockedSeg}` };
  }

  const combined = [...rules, ...(options.extraRules ?? [])];
  if (combined.length && matchesIgnoreRules(relativePath, combined)) {
    if (!options.includeIgnored) {
      return { accept: false, reason: 'gitignore' };
    }
  }

  if (isDotfilePath(relativePath) && !options.includeHidden) {
    return { accept: false, reason: 'hidden' };
  }

  return { accept: true, reason: 'ok' };
}

// ── Batch entry point: file list → accept/skip with reason counts ─

export interface BatchPolicySkippedReason {
  blocklist: number;
  gitignore: number;
  hidden: number;
  tooLarge: number;
}

export type BatchPolicyLimitKind = 'file_count' | 'total_bytes';

export interface BatchPolicyLimitViolation {
  kind: BatchPolicyLimitKind;
  actual: number;
  limit: number;
}

export interface BatchPolicyResult {
  accepted: File[];
  skipped: { file: File; reason: PolicyReason }[];
  totalAcceptedBytes: number;
  reasonCounts: BatchPolicySkippedReason;
  /** Hard caps are evaluated after blocklist / ignore / hidden filtering. */
  limitViolations: BatchPolicyLimitViolation[];
  /** True when the count or total bytes warrants a preflight modal. */
  shouldPreflight: boolean;
}

/**
 * Extract the relative path from a ``File``. The browser sets
 * ``webkitRelativePath`` for folder picks (and we synthesize it in
 * dropFiles.ts for drag-and-drop). When absent, fall back to ``name``.
 */
function fileRelativePath(file: File): string {
  const wrp = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  return wrp && wrp.length > 0 ? wrp : file.name;
}

export interface ApplyPolicyArgs {
  files: File[];
  options?: PolicyOptions;
  /** Pre-parsed .gitignore / .puppyignore rules from the upload root. */
  rules?: IgnoreRule[];
}

export function applyPolicy({ files, options = {}, rules = [] }: ApplyPolicyArgs): BatchPolicyResult {
  const accepted: File[] = [];
  const skipped: { file: File; reason: PolicyReason }[] = [];
  let totalAcceptedBytes = 0;
  const reasonCounts: BatchPolicySkippedReason = {
    blocklist: 0,
    gitignore: 0,
    hidden: 0,
    tooLarge: 0,
  };

  for (const file of files) {
    const rel = fileRelativePath(file);
    const decision = evaluateFile(rel, file.size, rules, options);
    if (decision.accept) {
      accepted.push(file);
      totalAcceptedBytes += file.size;
    } else {
      skipped.push({ file, reason: decision.reason });
      if (decision.reason.startsWith('blocklist:')) reasonCounts.blocklist++;
      else if (decision.reason === 'gitignore') reasonCounts.gitignore++;
      else if (decision.reason === 'hidden') reasonCounts.hidden++;
      else if (decision.reason === 'too_large') reasonCounts.tooLarge++;
    }
  }

  const shouldPreflight =
    accepted.length >= PREFLIGHT_FILE_THRESHOLD ||
    totalAcceptedBytes >= PREFLIGHT_BYTES_THRESHOLD ||
    skipped.length > 0;
  const limitViolations: BatchPolicyLimitViolation[] = [];
  if (accepted.length > PER_BATCH_MAX_FILES) {
    limitViolations.push({
      kind: 'file_count',
      actual: accepted.length,
      limit: PER_BATCH_MAX_FILES,
    });
  }
  if (totalAcceptedBytes > PER_BATCH_MAX_BYTES) {
    limitViolations.push({
      kind: 'total_bytes',
      actual: totalAcceptedBytes,
      limit: PER_BATCH_MAX_BYTES,
    });
  }

  return {
    accepted,
    skipped,
    totalAcceptedBytes,
    reasonCounts,
    limitViolations,
    shouldPreflight,
  };
}

/**
 * Walk the dropped File list looking for ``.gitignore`` /
 * ``.puppyignore`` AT THE UPLOAD ROOT — i.e. files whose relative path
 * has exactly one segment, named ".gitignore" or ".puppyignore".
 *
 * We don't honor nested ignore files (Git's full spec) because the
 * compounding gets tricky and the common-case fix is the root one.
 * Returns the parsed rules in document order; later rules win
 * (gitignore semantics).
 */
export async function collectIgnoreRulesFromDrop(files: File[]): Promise<IgnoreRule[]> {
  const rules: IgnoreRule[] = [];
  for (const file of files) {
    const rel = fileRelativePath(file);
    if (rel === '.gitignore' || rel === '.puppyignore') {
      try {
        const text = await file.text();
        rules.push(...parseIgnoreText(text));
      } catch {
        // Unreadable ignore file — fall back to no rules from it.
      }
    } else if (
      // Also accept a single nested level: ``<dropped>/.gitignore``.
      // This handles the common case of dropping a repo folder where
      // .gitignore is at the repo root, one level below the drop.
      (rel.endsWith('/.gitignore') || rel.endsWith('/.puppyignore')) &&
      rel.split('/').length === 2
    ) {
      try {
        const text = await file.text();
        rules.push(...parseIgnoreText(text));
      } catch {
        // ignore
      }
    }
  }
  return rules;
}
