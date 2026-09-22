import { ApiError } from "../../../api.js";
import { readFileSync } from "node:fs";
import { withErrors } from "../../../helpers.js";
import { createOutput } from "../../../output.js";
import { createApClient, extraHeaders } from "../lib/context.js";
import { errorPayload, finishWithPartialFailure, pathError } from "../lib/errors.js";
import { get } from "../lib/http.js";
import { parseIntegerOption, parseNonNegativeOption } from "../lib/options.js";
import { scopedPath } from "../lib/paths.js";

const GREP_MAX_BACKEND_LIMIT = 20000;

function collectOption(value, previous = []) {
  previous.push(value);
  return previous;
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function readPatternFiles(files) {
  const patterns = [];
  for (const file of files || []) {
    let text;
    try {
      text = readFileSync(file, "utf8");
    } catch (error) {
      throw new ApiError(0, "INVALID_PATTERN_FILE", `Cannot read pattern file ${file}: ${error.message}`);
    }
    const lines = text.split(/\r?\n/);
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    patterns.push(...lines);
  }
  return patterns;
}

function applyPatternMode(pattern, opts) {
  let next = pattern;
  let regex = true;
  if (opts.fixedStrings) {
    next = escapeRegExp(next);
  }
  if (opts.wordRegexp) {
    next = `(?<![A-Za-z0-9_])(?:${next})(?![A-Za-z0-9_])`;
    regex = true;
  } else if (opts.lineRegexp) {
    next = `^(?:${next})$`;
    regex = true;
  } else {
    regex = !opts.fixedStrings;
  }
  return { pattern: next, regex };
}

function buildPattern(pattern, opts) {
  const expressions = Array.isArray(opts.expression) ? opts.expression : [];
  const filePatterns = readPatternFiles(opts.file);
  const patterns = [...expressions, ...filePatterns];
  if (pattern != null) patterns.push(pattern);

  if (!patterns.length) {
    throw new ApiError(0, "INVALID_ARGUMENT", "grep requires a pattern, -e <pattern>, or -f <file>.");
  }
  if (opts.fixedStrings && (opts.extendedRegexp || opts.basicRegexp || opts.regexp || opts.perlRegexp)) {
    throw new ApiError(0, "INVALID_OPTION", "grep cannot combine -F with regexp mode flags.");
  }
  if (opts.perlRegexp) {
    throw new ApiError(0, "UNSUPPORTED_OPTION", "grep -P/--perl-regexp is not supported; use -E or -F.");
  }

  if (patterns.length === 1) return applyPatternMode(patterns[0], opts);
  const joined = patterns
    .map(item => opts.fixedStrings ? escapeRegExp(item) : item)
    .map(item => `(?:${item})`)
    .join("|");
  if (opts.wordRegexp || opts.lineRegexp) {
    return applyPatternMode(joined, { ...opts, fixedStrings: false });
  }
  return { pattern: joined, regex: true };
}

function normalizePaths(paths) {
  if (!paths?.length) return [""];
  return paths;
}

function joinPatterns(patterns) {
  return Array.isArray(patterns) && patterns.length ? patterns.join("\n") : "";
}

function unique(values) {
  return [...new Set(values)];
}

function hasMatches(results) {
  return results.some(result => (result.matches || []).length > 0);
}

function filesForResult(result) {
  if (Array.isArray(result.files)) return result.files;
  const counts = new Map();
  for (const match of result.matches || []) {
    counts.set(match.path, (counts.get(match.path) || 0) + 1);
  }
  return [...counts.entries()].map(([path, matchCount]) => ({
    path,
    match_count: matchCount,
  }));
}

function shouldShowFilename(result, results, opts) {
  if (opts.withFilename) return true;
  if (opts.filename === false) return false;
  return results.length > 1 || result.target_type === "folder";
}

function linePrefix(match, result, results, opts, separator = ":") {
  const parts = [];
  if (shouldShowFilename(result, results, opts)) parts.push(match.path || ".");
  if (opts.showLineNumber) parts.push(String(match.line_number));
  if (opts.byteOffset) {
    const offset = opts.onlyMatching ? match.match_byte_offset : match.byte_offset;
    parts.push(String(offset || 0));
  }
  return parts.length ? `${parts.join(separator)}${separator}` : "";
}

function formatMatch(match, result, results, opts) {
  const text = opts.onlyMatching ? (match.match_text ?? "") : (match.line_text ?? "");
  return `${linePrefix(match, result, results, opts)}${text}`;
}

function formatContextLine(context, match, result, results, opts) {
  return `${linePrefix({ ...match, ...context }, result, results, opts, "-")}${context.line_text ?? ""}`;
}

function renderMatches(out, result, results, opts) {
  const matches = result.matches || [];
  const withContext = (opts.beforeContext || opts.afterContext) && !opts.onlyMatching;
  let printedAny = false;
  for (const match of matches) {
    if (withContext && printedAny) out.raw("--");
    if (withContext) {
      for (const context of match.before_context || []) {
        out.raw(formatContextLine(context, match, result, results, opts));
      }
    }
    out.raw(formatMatch(match, result, results, opts));
    if (withContext) {
      for (const context of match.after_context || []) {
        out.raw(formatContextLine(context, match, result, results, opts));
      }
    }
    printedAny = true;
  }
}

function renderCount(out, result, results, opts) {
  const files = filesForResult(result);
  const showFilename = shouldShowFilename(result, results, opts);
  if (showFilename || files.length > 1) {
    for (const file of files) {
      out.raw(`${file.path || "."}:${file.match_count || 0}`);
    }
    return;
  }
  const count = files.length ? (files[0].match_count || 0) : (result.returned_count || 0);
  out.raw(String(count));
}

function renderFiles(out, results, opts) {
  const selected = [];
  for (const result of results) {
    for (const file of filesForResult(result)) {
      const count = file.match_count || 0;
      if (opts.filesWithMatches && count > 0) selected.push(file.path || ".");
      if (opts.filesWithoutMatch && count === 0) selected.push(file.path || ".");
    }
  }
  for (const path of unique(selected)) out.raw(path);
  return selected.length > 0;
}

function selectedFileModeHasOutput(results, opts) {
  for (const result of results) {
    for (const file of filesForResult(result)) {
      const count = file.match_count || 0;
      if (opts.filesWithMatches && count > 0) return true;
      if (opts.filesWithoutMatch && count === 0) return true;
    }
  }
  return false;
}

function positiveOrDefault(value, optionName) {
  const parsed = parseIntegerOption(value, optionName);
  if (parsed < 1) {
    throw new ApiError(0, "INVALID_OPTION", `${optionName} must be greater than zero.`);
  }
  return parsed;
}

function contextValue(value, optionName, defaultValue = 0) {
  if (value === true) return defaultValue;
  return parseNonNegativeOption(value, optionName);
}

export function registerGrepCommand(fs) {
  fs
    .command("grep")
    .description("Search text files within the access point scope")
    .argument("[pattern]", "regular expression pattern; use -F for fixed-string mode")
    .argument("[paths...]", "file or directory path(s) relative to the access point scope")
    .option("-e, --expression <pattern>", "pattern expression; may be repeated", collectOption, [])
    .option("-f, --file <file>", "read pattern expressions from a local file; may be repeated", collectOption, [])
    .option("-i, --ignore-case", "case-insensitive matching")
    .option("-E, --extended-regexp", "treat pattern as an extended regular expression (default)")
    .option("-G, --basic-regexp", "basic regular expression compatibility alias")
    .option("--regexp", "treat pattern as a regular expression (default)")
    .option("-F, --fixed-strings", "treat pattern as a fixed string")
    .option("-P, --perl-regexp", "recognize PCRE mode and fail with an explicit unsupported error")
    .option("-w, --word-regexp", "match only whole words")
    .option("-x, --line-regexp", "match only whole lines")
    .option("-v, --invert-match", "select non-matching lines")
    .option("-n, --line-number", "print 1-based line numbers")
    .option("--no-line-number", "suppress line numbers")
    .option("-b, --byte-offset", "print 0-based byte offsets")
    .option("-H, --with-filename", "print file names with output lines")
    .option("-h, --no-filename", "suppress file names with output lines")
    .option("-c, --count", "print a count of matching lines per file")
    .option("-l, --files-with-matches", "print only names of files with matches")
    .option("-L, --files-without-match", "print only names of files without matches")
    .option("-m, --max-count <n>", "stop after n selected lines per file")
    .option("-o, --only-matching", "print only the matched text")
    .option("-A, --after-context <n>", "print n trailing context lines")
    .option("-B, --before-context <n>", "print n leading context lines")
    .option("-C, --context [n]", "print n leading and trailing context lines (default: 2)")
    .option("-r, --recursive", "search directories recursively (accepted; directory paths recurse by default)")
    .option("-R, --dereference-recursive", "recursive compatibility alias")
    .option("-I, --binary-files-without-match", "ignore binary files (default)")
    .option("-a, --text", "binary-as-text compatibility flag; PuppyOne still searches text blobs only")
    .option("--binary-files <type>", "binary-file handling compatibility option")
    .option("-d, --directories <action>", "directory handling compatibility option")
    .option("-D, --devices <action>", "device handling compatibility option; devices are not modeled")
    .option("--color [when]", "color compatibility option; PuppyOne output is plain text")
    .option("--colour [when]", "color compatibility alias")
    .option("--line-buffered", "line-buffered compatibility option")
    .option("--label <label>", "stdin label compatibility option; stdin search is not modeled")
    .option("-s, --silent", "suppress per-path error messages")
    .option("--no-messages", "suppress per-path error messages")
    .option("-q, --quiet", "suppress normal output; exit status reports whether a match was found")
    .option("--include <glob>", "search only files whose path or basename matches glob", collectOption, [])
    .option("--exclude <glob>", "skip files whose path or basename matches glob", collectOption, [])
    .option("--exclude-dir <glob>", "skip files under directories whose basename matches glob", collectOption, [])
    .option("--hidden", "include entries whose names begin with .")
    .option("--all", "include hidden entries (PuppyOne alias)")
    .option("--max-depth <n>", "max directory recursion depth (-1 = unlimited)")
    .option("--limit <n>", "max matching lines returned before truncation")
    .option("--max-files <n>", "max file candidates scanned before truncation")
    .option("--max-bytes <n>", "max decoded text bytes scanned before truncation")
    .addHelpText("after", `
Examples:
  puppyone fs grep -n -i "todo" docs
  puppyone fs grep -R -n --include "*.md" --exclude-dir ".trash" "PuppyOne" .
  puppyone fs grep -F -e "literal one" -e "literal two" notes.md
  puppyone fs grep -C 2 -w "Access" docs/api-reference.md
  puppyone --json fs grep "pattern" . --max-depth 2 --limit 100

Notes:
  Default pattern mode is regexp; use -F for fixed strings.
  This is a scoped cloud live scan in V1. Prefer explicit paths and resource caps for broad searches.
  Unsupported/local-only grep semantics include -P/PCRE, null-data output, device/symlink behavior, compressed-file modes, and stdin streaming.
`)
    .action(withErrors(async (pattern, paths, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createApClient(cmd);
      const headers = await extraHeaders(cmd);
      const explicitPatternCount = (Array.isArray(opts.expression) ? opts.expression.length : 0)
        + (Array.isArray(opts.file) ? opts.file.length : 0);
      const pathArgs = explicitPatternCount && pattern != null
        ? [pattern, ...(paths || [])]
        : paths;
      const grepPattern = buildPattern(explicitPatternCount ? null : pattern, opts);
      const lineNumberSource = cmd.getOptionValueSource?.("lineNumber");
      const messagesSource = cmd.getOptionValueSource?.("messages");
      opts.showLineNumber = lineNumberSource === "cli" && opts.lineNumber !== false;
      opts.suppressMessages = !!opts.silent || (messagesSource === "cli" && opts.messages === false);
      opts.beforeContext = opts.context != null
        ? contextValue(opts.context, "-C/--context", 2)
        : opts.beforeContext != null
          ? contextValue(opts.beforeContext, "-B/--before-context")
          : 0;
      opts.afterContext = opts.context != null
        ? contextValue(opts.context, "-C/--context", 2)
        : opts.afterContext != null
          ? contextValue(opts.afterContext, "-A/--after-context")
          : 0;
      if (opts.filesWithMatches && opts.filesWithoutMatch) {
        throw new ApiError(0, "INVALID_OPTION", "grep cannot combine -l and -L.");
      }
      if (opts.binaryFiles && !["binary", "without-match", "text"].includes(opts.binaryFiles)) {
        throw new ApiError(0, "INVALID_OPTION", "--binary-files must be binary, without-match, or text.");
      }
      if (opts.directories && !["read", "recurse", "skip"].includes(opts.directories)) {
        throw new ApiError(0, "INVALID_OPTION", "--directories must be read, recurse, or skip.");
      }
      if (opts.directories === "skip") {
        process.exitCode = 1;
        return;
      }

      const queryBase = {
        pattern: grepPattern.pattern,
        regex: grepPattern.regex,
        ignore_case: !!opts.ignoreCase,
        invert_match: !!opts.invertMatch,
        only_matching: !!opts.onlyMatching,
        include_hidden: !!(opts.hidden || opts.all),
        include: joinPatterns(opts.include),
        exclude: joinPatterns(opts.exclude),
        exclude_dir: joinPatterns(opts.excludeDir),
        before_context: opts.beforeContext,
        after_context: opts.afterContext,
        include_offsets: !!opts.byteOffset,
      };
      if (opts.count || opts.filesWithoutMatch) queryBase.require_file_list = true;
      if (opts.maxDepth != null) queryBase.max_depth = parseIntegerOption(opts.maxDepth, "--max-depth");
      if (opts.maxCount != null) queryBase.max_count = parseNonNegativeOption(opts.maxCount, "-m/--max-count");
      if (opts.limit != null) queryBase.limit = positiveOrDefault(opts.limit, "--limit");
      if (opts.maxFiles != null) queryBase.max_files = positiveOrDefault(opts.maxFiles, "--max-files");
      if (opts.maxBytes != null) queryBase.max_bytes = positiveOrDefault(opts.maxBytes, "--max-bytes");
      if (opts.count || opts.filesWithMatches || opts.filesWithoutMatch || opts.quiet) {
        queryBase.limit = Math.max(queryBase.limit || 0, GREP_MAX_BACKEND_LIMIT);
      }

      const requestedPaths = normalizePaths(pathArgs);
      const results = [];
      const errors = [];

      // Cloud-only grep: the backend owns index readiness, fallback,
      // scope, and resource-limit policy. The CLI only maps grep syntax
      // into the canonical AP-FS grep request shape and renders output.
      for (const rawPath of requestedPaths) {
        const cleanPath = scopedPath(rawPath);
        try {
          results.push(await get(client, "/ap-fs/grep", {
            ...queryBase,
            path: cleanPath,
          }, headers));
        } catch (e) {
          errors.push(errorPayload(cleanPath, e));
          if (!out.json && !opts.suppressMessages && !opts.quiet) {
            console.error(pathError("grep", cleanPath, e));
          }
        }
      }

      const matched = hasMatches(results);
      const selectedFilesMatched = selectedFileModeHasOutput(results, opts);

      if (out.json) {
        if (errors.length) {
          console.log(JSON.stringify({
            success: false,
            results,
            errors,
          }, null, 2));
        } else if (results.length === 1) {
          out.success(results[0]);
        } else {
          const matches = results.flatMap(result => result.matches || []);
          out.success({
            results,
            matches,
            complete: results.every(result => result.complete !== false),
            truncated: results.some(result => result.truncated),
            returned_count: matches.length,
          });
        }
        if (!errors.length && !matched && !selectedFilesMatched) process.exitCode = 1;
        finishWithPartialFailure(errors);
        return;
      }

      if (!opts.quiet) {
        for (const result of results) {
          if (result.truncated && !opts.suppressMessages) {
            out.warn(`stdout is incomplete for ${result.path || "."}: ${result.truncation_reason || "limit_exceeded"}. Use --json to inspect complete=false.`);
          }
        }

        if (opts.filesWithMatches || opts.filesWithoutMatch) {
          renderFiles(out, results, opts);
        } else if (opts.count) {
          for (const result of results) renderCount(out, result, results, opts);
        } else {
          for (const result of results) renderMatches(out, result, results, opts);
        }
      }

      if (!errors.length && !matched && !selectedFilesMatched) process.exitCode = 1;
      finishWithPartialFailure(errors);
    }));
}
