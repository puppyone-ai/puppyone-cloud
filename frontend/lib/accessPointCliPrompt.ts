export interface TerminalCliPromptInput {
  readonly apiBase: string;
  readonly accessKey: string;
  readonly profileName: string;
  readonly scopeName: string;
  readonly accessPointName?: string;
}

export interface TerminalCliPrompt {
  readonly installLine: string;
  readonly loginLine: string;
  readonly exploreLines: readonly string[];
  readonly fileLines: readonly string[];
  readonly prompt: string;
}

export interface GitSyncPromptInput {
  readonly gitUrl: string;
  readonly scopeName: string;
  readonly directoryName?: string;
  readonly accessPointName?: string;
}

export interface GitSyncPrompt {
  readonly cloneLines: readonly string[];
  readonly existingFolderLines: readonly string[];
  readonly workflowLines: readonly string[];
  readonly prompt: string;
}

export interface McpSetupPromptInput {
  readonly apiBase: string;
  readonly apiKey: string;
  readonly scopeName: string;
  readonly accessPointName?: string;
}

export interface McpSetupPrompt {
  readonly serverUrl: string;
  readonly authorizationLine: string;
  readonly serverName: string;
  readonly config: string;
  readonly prompt: string;
}

export function accessPointProfileSlug(name: string): string {
  return (
    name
      .toLowerCase()
      .replaceAll(/[^a-z0-9]+/g, '-')
      .replaceAll(/^-+|-+$/g, '') || 'folder'
  );
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export function buildGitSyncPrompt({
  gitUrl,
  scopeName,
  directoryName,
  accessPointName,
}: GitSyncPromptInput): GitSyncPrompt {
  const remote = gitUrl || '<git-url>';
  const dir = accessPointProfileSlug(directoryName || scopeName || 'workspace');
  const quotedRemote = shellQuote(remote);
  const quotedDir = shellQuote(dir);
  const cloneLines = [
    `git clone ${quotedRemote} ${quotedDir}`,
    `cd ${quotedDir}`,
  ];
  const existingFolderLines = [
    'cd /path/to/your/existing/folder',
    'git init',
    'git branch -M main',
    `git remote add origin ${quotedRemote}`,
    'git add .',
    'git commit -m "Initial sync"',
    'git push -u origin main',
  ];
  const workflowLines = [
    'git pull --ff-only origin main',
    '# ... edit files ...',
    'git add .',
    'git commit -m "describe changes"',
    'git push origin main',
  ];
  const prompt = [
    'Use this Puppyone Access Point as a Git remote.',
    '',
    accessPointName ? `Access Point: ${accessPointName}` : null,
    `Scope: ${scopeName}`,
    `Remote: ${remote}`,
    'Authentication: generate a Git credential in Puppyone, use username `x-puppyone-token`, and let an OS-backed Git credential helper store the password.',
    'Never put the password in the remote URL, command arguments, or `.git/config`.',
    '',
    'Clone to a new local folder:',
    '```bash',
    ...cloneLines,
    '```',
    '',
    'Or publish an existing local folder:',
    '```bash',
    ...existingFolderLines,
    '```',
    '',
    'Day-to-day workflow:',
    '```bash',
    ...workflowLines,
    '```',
    '',
    'Collaboration rules:',
    '- Puppyone is the source of truth for this scope.',
    '- Scope Git remotes follow normal Git fast-forward rules.',
    '- If push says the remote has newer work, run `git fetch origin` and rebase your work onto `origin/main`, then push again.',
    '- Do not use force push as a server-side merge proposal; Puppyone review/merge flows are explicit product actions.',
    '- If Puppyone says manual review is required, stop and resolve it from Puppyone.',
    '- This remote is scope-bound; commits that touch paths outside the scope are rejected.',
  ].filter((line): line is string => line != null).join('\n');

  return {
    cloneLines,
    existingFolderLines,
    workflowLines,
    prompt,
  };
}

export function buildTerminalCliPrompt({
  apiBase,
  accessKey,
  profileName,
  scopeName,
  accessPointName,
}: TerminalCliPromptInput): TerminalCliPrompt {
  const installLine = 'npm install -g puppyone@latest';
  const loginLine = [
    `printf '%s' ${shellQuote(accessKey || '<access-key>')}`,
    '|',
    'puppyone ap login',
    shellQuote(profileName || 'folder'),
    '--api-url',
    shellQuote(apiBase || '<api-url>'),
    '--access-key-stdin',
  ].join(' ');
  const exploreLines = [
    'puppyone fs grep <pattern>',
    'puppyone fs ls -la',
    'puppyone fs tree -L 2',
    'puppyone fs find --limit 200 . -maxdepth 2 -type f',
  ];
  const fileLines = [
    'puppyone fs cat <file.md>',
    'puppyone fs head -n 40 <file.md>',
    "printf 'hello\\n' | puppyone fs write notes/hello.md --type markdown",
  ];

  const prompt = [
    'Use this Puppyone Access Point from terminal or an AI coding agent.',
    '',
    accessPointName ? `Access Point: ${accessPointName}` : null,
    `Scope: ${scopeName}`,
    '',
    'Recommended path: scoped FS CLI commands through the Puppyone CLI. No local clone is needed.',
    'Install or update the CLI, then authenticate this scoped Access Point:',
    '```bash',
    installLine,
    loginLine,
    '```',
    '',
    'Use Unix-like scoped file commands:',
    '```bash',
    ...exploreLines,
    ...fileLines,
    '```',
    '',
    'Agent rules:',
    '- `puppyone fs` is scoped to this Access Point; do not create another Access Point unless I ask for one.',
    '- `puppyone fs cat` prints raw file content by default. Use `--json` only when structured metadata is needed.',
    '- Mutating commands (`write`, `mkdir`, `touch`, `cp`, `mv`, `rm`, `rmdir`, `upload`) are recorded in Puppyone version history and audit logs.',
    '- Prefer explicit paths. For recursive scans use `tree -L <n>`, `find ... -maxdepth <n>`, or `--limit`.',
    '- Default stdout is Unix-like; warnings and truncation diagnostics may appear on stderr.',
  ].filter((line): line is string => line != null).join('\n');

  return {
    installLine,
    loginLine,
    exploreLines,
    fileLines,
    prompt,
  };
}

export function buildMcpSetupPrompt({
  apiBase,
  apiKey,
  scopeName,
  accessPointName,
}: McpSetupPromptInput): McpSetupPrompt {
  const serverUrl = `${apiBase || '<api-url>'}/api/v1/mcp/proxy`;
  const key = apiKey || '<mcp-api-key>';
  const label = accessPointName || 'Puppyone MCP';
  const serverName = accessPointProfileSlug(label || scopeName || 'puppyone-mcp');
  const authorizationLine = `Authorization: Bearer ${key}`;
  const config = JSON.stringify(
    {
      mcpServers: {
        [serverName]: {
          type: 'http',
          url: serverUrl,
          headers: {
            Authorization: `Bearer ${key}`,
          },
        },
      },
    },
    null,
    2,
  );

  const prompt = [
    'Configure this Puppyone MCP Access Point for my coding agent.',
    '',
    accessPointName ? `Access Point: ${accessPointName}` : null,
    `Scope: ${scopeName}`,
    `Server URL: ${serverUrl}`,
    `Header: ${authorizationLine}`,
    '',
    'Use this MCP client config:',
    '```json',
    config,
    '```',
    '',
    'After setup, use the exposed MCP tools against this scoped Puppyone workspace.',
    'Do not create another Puppyone access point unless I ask for one.',
  ].filter((line): line is string => line != null).join('\n');

  return {
    serverUrl,
    authorizationLine,
    serverName,
    config,
    prompt,
  };
}
