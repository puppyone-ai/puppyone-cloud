import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Command } from "commander";

import { registerFs } from "../src/commands/fs/index.js";

function makeProgram() {
  const program = new Command();
  program
    .name("puppyone")
    .exitOverride()
    .enablePositionalOptions()
    .option("-u, --api-url <url>", "PuppyOne API URL")
    .option("--json", "output as JSON");
  registerFs(program);
  return program;
}

function queryObject(url) {
  return Object.fromEntries(url.searchParams.entries());
}

function jsonResponse(data) {
  return new Response(JSON.stringify({ code: 0, data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function rawResponse(text, size = Buffer.byteLength(text)) {
  return new Response(text, {
    status: 200,
    headers: { "x-puppyone-size": String(size) },
  });
}

function defaultDataFor(path, query, body) {
  if (path === "/ap-fs/semantics") {
    return { fs_semantics: { summary: "remote semantics", guarantees: [] }, tools: [] };
  }
  if (path === "/ap-fs/stat") {
    const target = query.path || "";
    if (target === "missing.md") return { path: target, exists: false, type: "" };
    if (target === "docs" || target === "src") {
      return { path: target, exists: true, type: "folder", size_bytes: 10, scope_head_commit_id: "base" };
    }
    return { path: target, exists: true, type: "markdown", size_bytes: 12, scope_head_commit_id: "base" };
  }
  if (path === "/ap-fs/ls") {
    return {
      path: query.path || "",
      target_type: "folder",
      entries: [{ name: "a.md", path: "docs/a.md", type: "markdown", size_bytes: 12 }],
      complete: true,
      truncated: false,
    };
  }
  if (path === "/ap-fs/tree") {
    return {
      path: query.path || "",
      target_type: "folder",
      entries: [
        { name: "docs", path: "docs", type: "folder" },
        { name: "a.md", path: "docs/a.md", type: "markdown" },
      ],
      complete: true,
      truncated: false,
      returned_count: 2,
      limit: Number(query.limit || 5000),
    };
  }
  if (path === "/ap-fs/find") {
    return {
      path: query.path || "",
      entries: [
        { name: "a.md", path: "docs/a.md", type: "markdown" },
      ],
      complete: true,
      truncated: false,
      returned_count: 1,
      scanned_count: 2,
      limit: Number(query.limit || 5000),
      source: "live_tree",
    };
  }
  if (path === "/ap-fs/cat") {
    return { path: query.path, type: "markdown", content_text: "hello\n" };
  }
  if (path === "/ap-fs/grep") {
    return {
      path: query.path || "",
      target_type: "folder",
      matches: [{ path: "docs/a.md", line_number: 1, line_text: "hello", match_text: "hello" }],
      files: [{ path: "docs/a.md", match_count: 1 }, { path: "docs/b.md", match_count: 0 }],
      complete: true,
      truncated: false,
      returned_count: 1,
    };
  }
  if (path === "/ap-fs/write") {
    return { path: body.path, commit_id: "write-commit" };
  }
  if (path === "/ap-fs/mkdir") {
    return { path: body.path, commit_id: "mkdir-commit" };
  }
  if (path === "/ap-fs/touch") {
    return { paths: body.paths, commit_ids: ["touch-commit"] };
  }
  if (path === "/ap-fs/cp") {
    return { old_path: body.old_path, new_path: body.new_path, commit_id: "cp-commit", skipped: false };
  }
  if (path === "/ap-fs/mv") {
    return { old_path: body.old_path, new_path: body.new_path, commit_id: "mv-commit", skipped: false };
  }
  if (path === "/ap-fs/rm") {
    return { paths: body.paths, removed: true, commit_id: "rm-commit" };
  }
  if (path === "/ap-fs/rmdir") {
    return { paths: [body.path], removed_paths: [body.path], commit_id: "rmdir-commit" };
  }
  throw new Error(`Unhandled test path: ${path}`);
}

async function runFs(args, options = {}) {
  const calls = [];
  const logs = [];
  const errors = [];
  const originalFetch = globalThis.fetch;
  const originalLog = console.log;
  const originalError = console.error;
  const oldExitCode = process.exitCode;
  process.exitCode = 0;
  console.log = (value = "") => logs.push(String(value));
  console.error = (value = "") => errors.push(String(value));
  globalThis.fetch = async (rawUrl, request = {}) => {
    const url = new URL(rawUrl);
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const body = request.body ? JSON.parse(request.body) : null;
    const call = {
      method: request.method || "GET",
      path,
      query: queryObject(url),
      body,
      headers: request.headers || {},
    };
    calls.push(call);
    if (options.failFetch) throw new Error("fetch failed");
    if (path === "/ap-fs/raw") {
      return rawResponse("0123456789\nabcdefghij\n", 21);
    }
    const data = options.dataFor
      ? options.dataFor(path, call.query, body, call)
      : defaultDataFor(path, call.query, body, call);
    return jsonResponse(data);
  };

  try {
    await makeProgram().parseAsync([
      "node",
      "puppyone",
      "--json",
      "fs",
      "--access-key",
      "test-key",
      "--api-url",
      "http://unit.test",
      ...args,
    ]);
  } finally {
    globalThis.fetch = originalFetch;
    console.log = originalLog;
    console.error = originalError;
  }

  const exitCode = process.exitCode || 0;
  process.exitCode = oldExitCode;
  return {
    calls,
    logs,
    errors,
    exitCode,
    json: logs.length ? JSON.parse(logs.at(-1)) : null,
  };
}

function lastCall(result, path) {
  const matches = result.calls.filter(call => call.path === path);
  assert.ok(matches.length > 0, `Expected call to ${path}`);
  return matches.at(-1);
}

{
  const result = await runFs([
    "grep", "-n", "-i", "-F", "-C", "2", "--include", "*.md", "--exclude", "tmp*",
    "--exclude-dir", ".git", "--hidden", "--max-depth", "3", "--limit", "25",
    "--max-files", "50", "--max-bytes", "4096", "a+b", "docs",
  ]);
  const query = lastCall(result, "/ap-fs/grep").query;
  assert.equal(query.path, "docs");
  assert.equal(query.pattern, "a\\+b");
  assert.equal(query.regex, "false");
  assert.equal(query.ignore_case, "true");
  assert.equal(query.include_hidden, "true");
  assert.equal(query.include, "*.md");
  assert.equal(query.exclude, "tmp*");
  assert.equal(query.exclude_dir, ".git");
  assert.equal(query.before_context, "2");
  assert.equal(query.after_context, "2");
  assert.equal(query.max_depth, "3");
  assert.equal(query.limit, "25");
  assert.equal(query.max_files, "50");
  assert.equal(query.max_bytes, "4096");
}

{
  const result = await runFs(["grep", "-e", "alpha", "-e", "beta", "docs"]);
  const query = lastCall(result, "/ap-fs/grep").query;
  assert.equal(query.path, "docs");
  assert.equal(query.pattern, "(?:alpha)|(?:beta)");
  assert.equal(query.regex, "true");
}

{
  const dir = mkdtempSync(join(tmpdir(), "puppyone-grep-patterns-"));
  const patterns = join(dir, "patterns.txt");
  writeFileSync(patterns, "one.two\nthree+four\n", "utf8");
  const result = await runFs(["grep", "-F", "-f", patterns, "docs"]);
  const query = lastCall(result, "/ap-fs/grep").query;
  assert.equal(query.pattern, "(?:one\\.two)|(?:three\\+four)");
  assert.equal(query.regex, "true");
}

{
  const word = await runFs(["grep", "-w", "Access", "docs"]);
  assert.equal(
    lastCall(word, "/ap-fs/grep").query.pattern,
    "(?<![A-Za-z0-9_])(?:Access)(?![A-Za-z0-9_])",
  );
  const line = await runFs(["grep", "-x", "exact line", "docs"]);
  assert.equal(lastCall(line, "/ap-fs/grep").query.pattern, "^(?:exact line)$");
}

{
  const result = await runFs(["grep", "-v", "-o", "-A", "1", "-B", "2", "-b", "-m", "3", "needle", "docs"]);
  const query = lastCall(result, "/ap-fs/grep").query;
  assert.equal(query.invert_match, "true");
  assert.equal(query.only_matching, "true");
  assert.equal(query.include_offsets, "true");
  assert.equal(query.before_context, "2");
  assert.equal(query.after_context, "1");
  assert.equal(query.max_count, "3");
}

{
  const count = await runFs(["grep", "-c", "needle", "docs"]);
  assert.equal(lastCall(count, "/ap-fs/grep").query.require_file_list, "true");
  assert.equal(lastCall(count, "/ap-fs/grep").query.limit, "20000");
  const withoutMatch = await runFs(["grep", "-L", "needle", "docs"]);
  assert.equal(lastCall(withoutMatch, "/ap-fs/grep").query.require_file_list, "true");
  const withMatch = await runFs(["grep", "-l", "needle", "docs"]);
  assert.equal(lastCall(withMatch, "/ap-fs/grep").query.require_file_list, undefined);
  assert.equal(lastCall(withMatch, "/ap-fs/grep").query.limit, "20000");
}

{
  const result = await runFs(["grep", "--directories", "skip", "needle", "docs"]);
  assert.equal(result.exitCode, 1);
  assert.equal(result.calls.length, 0);
}

{
  const result = await runFs(["find", "docs", "-mindepth", "1", "-maxdepth", "2", "-name", "*.md", "-not", "-path", "docs/tmp/*", "--limit", "25"]);
  assert.equal(result.calls.length, 1);
  const query = lastCall(result, "/ap-fs/find").query;
  assert.equal(query.path, "docs");
  assert.equal(query.include_hidden, "true");
  assert.equal(query.mindepth, "1");
  assert.equal(query.max_depth, "2");
  assert.equal(query.limit, "25");
  assert.deepEqual(JSON.parse(query.conditions), [
    { kind: "name", value: "*.md", negate: false },
    { kind: "path", value: "docs/tmp/*", negate: true },
  ]);
}

{
  const result = await runFs(["ls", "-laR", "--sort", "size", "--limit", "20", "docs"]);
  const query = lastCall(result, "/ap-fs/tree").query;
  assert.equal(query.path, "docs");
  assert.equal(query.include_hidden, "true");
  assert.equal(query.include_size, "true");
  assert.equal(query.max_depth, "-1");
  assert.equal(query.limit, "20");
}

{
  const result = await runFs(["tree", "-d", "-L", "2", "-a", "--limit", "30", "docs"]);
  const query = lastCall(result, "/ap-fs/tree").query;
  assert.equal(query.path, "docs");
  assert.equal(query.max_depth, "1");
  assert.equal(query.include_hidden, "true");
  assert.equal(query.directories_only, "true");
  assert.equal(query.limit, "30");
}

{
  const result = await runFs(["find", "docs", "-maxdepth", "2", "-type", "f", "-name", "*.md", "--limit", "40"]);
  assert.equal(result.calls.length, 1);
  const query = lastCall(result, "/ap-fs/find").query;
  assert.equal(query.path, "docs");
  assert.equal(query.max_depth, "2");
  assert.equal(query.include_hidden, "true");
  assert.equal(query.limit, "40");
  assert.deepEqual(JSON.parse(query.conditions), [
    { kind: "type", value: "f", negate: false },
    { kind: "name", value: "*.md", negate: false },
  ]);
}

{
  const result = await runFs(["cat", "docs/a.md"]);
  const query = lastCall(result, "/ap-fs/cat").query;
  assert.equal(query.path, "docs/a.md");
  assert.equal(query.structured, "true");
}

{
  const result = await runFs(["head", "-c", "5", "docs/a.md"]);
  const raw = lastCall(result, "/ap-fs/raw");
  assert.equal(raw.query.path, "docs/a.md");
  assert.equal(raw.query.limit, "5");
}

{
  const result = await runFs(["tail", "-c", "4", "docs/a.md"]);
  const raw = lastCall(result, "/ap-fs/raw");
  assert.equal(result.calls[0].path, "/ap-fs/stat");
  assert.equal(raw.query.path, "docs/a.md");
  assert.equal(raw.query.start, "8");
  assert.equal(raw.query.limit, "4");
}

{
  const result = await runFs(["stat", "docs/a.md"]);
  assert.equal(lastCall(result, "/ap-fs/stat").query.path, "docs/a.md");
}

{
  const result = await runFs(["write", "docs/a.md", "--content", "hello", "--type", "markdown", "--base-commit", "abc", "-m", "msg"]);
  const body = lastCall(result, "/ap-fs/write").body;
  assert.deepEqual(body, {
    path: "docs/a.md",
    content: "hello",
    node_type: "markdown",
    base_commit_id: "abc",
    message: "msg",
  });
}

{
  const result = await runFs(["mkdir", "-p", "docs/new"]);
  const body = lastCall(result, "/ap-fs/mkdir").body;
  assert.equal(body.path, "docs/new");
  assert.equal(body.parents, true);
  assert.equal(body.base_commit_id, "base");
}

{
  const result = await runFs(["touch", "a.md", "b.md"]);
  const body = lastCall(result, "/ap-fs/touch").body;
  assert.deepEqual(body.paths, ["a.md", "b.md"]);
  assert.equal(body.path, "a.md");
  assert.equal(body.base_commit_id, "base");
}

{
  const result = await runFs(["cp", "-r", "-n", "-T", "src", "dst"]);
  const body = lastCall(result, "/ap-fs/cp").body;
  assert.equal(body.old_path, "src");
  assert.equal(body.new_path, "dst");
  assert.equal(body.recursive, true);
  assert.equal(body.no_clobber, true);
  assert.equal(body.no_target_directory, true);
}

{
  const result = await runFs(["mv", "-n", "-t", "docs", "a.md", "b.md"]);
  const posts = result.calls.filter(call => call.path === "/ap-fs/mv");
  assert.equal(posts.length, 2);
  assert.equal(posts[0].body.new_path, "docs");
  assert.equal(posts[0].body.target_directory, true);
  assert.equal(posts[0].body.no_clobber, true);
}

{
  const result = await runFs(["rm", "-r", "-f", "docs", "missing.md"]);
  const body = lastCall(result, "/ap-fs/rm").body;
  assert.deepEqual(body.paths, ["docs"]);
  assert.equal(body.recursive, true);
  assert.equal(body.force, true);
}

{
  const result = await runFs(["rmdir", "-p", "docs/empty"]);
  const body = lastCall(result, "/ap-fs/rmdir").body;
  assert.equal(body.path, "docs/empty");
  assert.equal(body.parents, true);
}

{
  const result = await runFs(["semantics"]);
  assert.equal(lastCall(result, "/ap-fs/semantics").method, "GET");
  assert.equal(result.json.fs_semantics.summary, "remote semantics");
}

{
  const result = await runFs(["semantics"], { failFetch: true });
  assert.equal(result.calls.length, 1);
  assert.equal(result.json.source, "local_fallback");
  assert.match(result.json.fs_semantics.summary, /PuppyOne FS/);
}
