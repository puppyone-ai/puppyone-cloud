import { withErrors } from "../../../helpers.js";
import { createOutput } from "../../../output.js";
import { createApClient, extraHeaders } from "../lib/context.js";
import { get } from "../lib/http.js";

const SEMANTICS = {
  summary: "PuppyOne FS is a Unix-like scoped cloud Context Drive, not a local POSIX filesystem.",
  guarantees: [
    "Mutating fs commands apply directly to the cloud Context Drive and are recorded in PuppyOne version history and audit logs.",
    "Read commands are scoped by the active Access Point and respect excluded paths.",
    "rm removes paths from the current tree; recovery is through PuppyOne version history/rollback, not a .trash directory.",
  ],
  differences: [
    "chmod/chown/chgrp, inode/device semantics, symlinks, sockets, and hard links are not POSIX-modeled.",
    "mtime/ctime are derived from PuppyOne version history, not local filesystem timestamps.",
    "size fields may require blob metadata; avoid large recursive size scans unless needed.",
    "Recursive tree/find/ls -R responses expose complete/truncated/returned_count/limit fields in --json and should be narrowed with -L, -maxdepth, or --limit.",
    "grep is a scoped cloud text search: CLI flags map to backend request fields, and the backend owns index readiness, fallback, scope, and resource-limit policy.",
    "upload/download are PuppyOne bridge commands, so their recursive forms expose --max-depth and --limit as resource controls.",
  ],
  resource_guidance: [
    "Default output keeps stdout Unix-like; warnings and truncation diagnostics go to stderr.",
    "--json output exposes complete/truncated/returned_count/limit/truncation_reason for scripts and agents.",
    "Prefer explicit paths over scanning the access point root.",
    "Use tree -L <n>, find -maxdepth <n>, and --limit for recursive commands.",
    "Use head/tail for previews; raw file reads support range reads when available.",
  ],
  discovery_guidance: [
    "Run `puppyone fs --help` to list scoped filesystem commands.",
    "Run `puppyone fs <command> --help` before using non-trivial flags.",
    "Use `puppyone fs semantics --json` when an agent needs machine-readable capability notes.",
  ],
  command_guidance: [
    "ls: Unix-like directory listing with multi-path support and familiar flags such as -l, -a, -R, -1, -h, -t, -d, -F; JSON exposes completeness metadata.",
    "tree: directory tree rendering with -L/--level, directory-only mode, hidden-entry support, and scan limits.",
    "find: scoped path discovery with Unix-like expressions such as -maxdepth, -mindepth, -type, -name, -iname, and --limit.",
    "cat/head/tail: raw file reads; cat prints raw content by default, while head/tail are safer for previews and support line/byte bounds.",
    "stat: metadata lookup for the scoped path; root stat reports the current Access Point scope state.",
    "grep: cloud text search through the canonical backend grep operation; see the grep guidance below and `puppyone fs grep --help` for flags.",
    "write: writes or replaces one cloud file from --content, stdin, or local input depending on flags; mutates version history and audit logs.",
    "mkdir/touch: create directories or empty files; mkdir supports -p/--parents.",
    "cp/mv: scoped copy/move/rename with multi-source behavior, directory target handling, no-clobber support, and versioned mutations.",
    "rm/rmdir: rm removes files or recursive trees according to its flags; rmdir removes empty directories and can remove parent chains.",
    "upload/download: bridge local filesystem and the scoped cloud filesystem; recursive forms should use --max-depth and --limit.",
  ],
  grep_guidance: [
    "Run `puppyone fs grep --help` for the full supported flag list.",
    "Common grep flags supported: -E, -G, -F, -e, -f, -i, -v, -n, -H, -h, -b, -c, -l, -L, -m, -o, -A, -B, -C, -r, -R, -I, -a, -s, -q, -w, -x, --include, --exclude, --exclude-dir.",
    "Default pattern mode is regexp; use -F for literal strings and -e when passing multiple patterns.",
    "Use --max-depth, --limit, --max-files, and --max-bytes to keep broad cloud searches bounded.",
    "The backend decides whether the text index is authoritative and falls back to a live tree scan when needed.",
    "Use --json when you need complete/truncated/scanned_files/scanned_bytes/skipped metadata.",
    "-P/PCRE, null-data/null-delimited output, local device/symlink behavior, compressed-file modes, and stdin streaming are not modeled in V1.",
  ],
};

async function loadRemoteSemantics(cmd) {
  try {
    const client = createApClient(cmd);
    const headers = await extraHeaders(cmd);
    return await get(client, "/ap-fs/semantics", {}, headers);
  } catch {
    return null;
  }
}

function semanticsFrom(payload) {
  return payload?.fs_semantics || SEMANTICS;
}

export function registerSemanticsCommand(fs) {
  fs
    .command("semantics")
    .description("Show PuppyOne FS Unix-compatibility notes for agents")
    .action(withErrors(async (opts, cmd) => {
      const out = createOutput(cmd);
      const payload = await loadRemoteSemantics(cmd);
      const semantics = semanticsFrom(payload);
      if (out.json) {
        out.success(payload || { fs_semantics: semantics, source: "local_fallback" });
        return;
      }

      out.raw([
        semantics.summary,
        "",
        "Guarantees:",
        ...(semantics.guarantees || []).map(item => `  - ${item}`),
        "",
        "Differences from local Unix:",
        ...(semantics.differences || []).map(item => `  - ${item}`),
        "",
        "Discovery:",
        ...(semantics.discovery_guidance || []).map(item => `  - ${item}`),
        "",
        "Command guidance:",
        ...(semantics.command_guidance || []).map(item => `  - ${item}`),
        "",
        "Resource guidance:",
        ...(semantics.resource_guidance || []).map(item => `  - ${item}`),
        "",
        "grep guidance:",
        ...(semantics.grep_guidance || []).map(item => `  - ${item}`),
      ].join("\n"));
    }));
}
