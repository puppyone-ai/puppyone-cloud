import assert from "node:assert/strict";
import { Command } from "commander";

import { registerTemplate } from "../src/commands/template.js";

function makeProgram() {
  const program = new Command();
  program
    .name("puppyone")
    .exitOverride()
    .enablePositionalOptions()
    .option("-u, --api-url <url>")
    .option("-k, --api-key <key>")
    .option("--json")
    .option("-o, --org <id>");
  registerTemplate(program);
  return program;
}

async function run(args, responseData) {
  const calls = [];
  const logs = [];
  const oldFetch = globalThis.fetch;
  const oldLog = console.log;
  globalThis.fetch = async (rawUrl, options = {}) => {
    const url = new URL(rawUrl);
    calls.push({
      path: url.pathname.replace(/^\/api\/v1/, ""),
      query: Object.fromEntries(url.searchParams),
      method: options.method,
      body: options.body ? JSON.parse(options.body) : null,
    });
    return new Response(JSON.stringify({ code: 0, data: responseData }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  console.log = (value = "") => logs.push(String(value));
  try {
    await makeProgram().parseAsync([
      "node",
      "puppyone",
      "--json",
      "--api-url",
      "http://unit.test",
      "--api-key",
      "test-token",
      "--org",
      "org-1",
      ...args,
    ]);
  } finally {
    globalThis.fetch = oldFetch;
    console.log = oldLog;
  }
  return { calls, json: JSON.parse(logs.at(-1)) };
}

{
  const result = await run(
    ["template", "ls", "--query", "agent", "--limit", "12"],
    {
      registry: { source: "remote", catalog_enabled: true },
      templates: [{
        id: "agent-kit",
        name: "Agent Kit",
        category: "agents",
        current_release: { id: "1.0.0", version: "1.0.0", file_count: 3 },
      }],
      next_cursor: "page-2",
    },
  );
  assert.deepEqual(result.calls[0], {
    path: "/templates",
    query: { q: "agent", limit: "12" },
    method: "GET",
    body: null,
  });
  assert.equal(result.json.success, true);
  assert.equal(result.json.templates[0].id, "agent-kit");
}

{
  const result = await run(
    [
      "template",
      "use",
      "agent-kit",
      "--release",
      "1.0.0",
      "--name",
      "My agents",
      "--no-activate",
    ],
    {
      template_id: "agent-kit",
      release_id: "1.0.0",
      project: { id: "project-1", name: "My agents", org_id: "org-1" },
    },
  );
  assert.deepEqual(result.calls[0], {
    path: "/templates/agent-kit/instantiate",
    query: {},
    method: "POST",
    body: {
      org_id: "org-1",
      release_id: "1.0.0",
      name: "My agents",
    },
  });
  assert.equal(result.json.success, true);
  assert.equal(result.json.active, false);
  assert.equal(result.json.project.id, "project-1");
}

console.log("template command tests passed");
