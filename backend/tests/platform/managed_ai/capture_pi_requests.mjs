// Contract producer: use the Desktop's pinned SDK, never hand-written request fixtures.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const api = path.join(process.argv[2], "node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js");
const { stream } = await import(pathToFileURL(api).href);
const { createConnectionModelRuntime } = await import(pathToFileURL(path.join(process.argv[2],
  "electron/main/agent/runtimes/puppyone-agent/worker/model-configuration.mjs")).href);
const connectionId = "mc_11111111-1111-4111-8111-111111111111";
const runtime = await createConnectionModelRuntime({ schemaVersion: 1, connectionId, configGeneration: 1,
  baseUrl: "http://127.0.0.1:1/v1", auth: "bearer", apiKey: "synthetic-fixture-only", selectedModelId: "test/model",
  models: [{ id: "test/model", name: "Contract fixture", contextWindow: 32768, capabilities: { images: "unsupported" } }] });
const model = runtime.getModel(connectionId, "test/model");
if (process.argv[3] === "--error") {
  const envelope = JSON.parse(fs.readFileSync(0, "utf8"));
  let requests = 0;
  const result = await stream(model, { messages: [{ role: "user", content: "Synthetic request.", timestamp: 1 }] }, {
    apiKey: "synthetic-fixture-only", maxRetries: 0,
    fetch: async () => {
      requests++;
      return new Response(JSON.stringify(envelope), { status: 422, headers: { "content-type": "application/json" } });
    },
  }).result();
  assert.equal(result.stopReason, "error");
  assert.ok(result.errorMessage.includes(envelope.error.message), result.errorMessage);
  assert.ok(!result.errorMessage.includes("(no body)"));
  assert.equal(requests, 1);
  console.log(JSON.stringify({ readable: true, requests }));
  process.exit(0);
}
const cases = [];
for (const field of ["reasoning_content", "reasoning", "reasoning_text", "reasoning_details"]) {
  for (const tool of [false, true]) {
    const context = { messages: [{ role: "user", content: "Read the synthetic fixture.", timestamp: 1 }],
      tools: [{ name: "read", description: "Read a synthetic file", parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } }] };
    let count = 0;
    const fetch = async (_url, init) => {
      cases.push({ field, tool, round: count, body: JSON.parse(init.body) });
      const first = count++ === 0;
      const reasoning = field === "reasoning_details" ? [{ type: "reasoning.text", text: "Synthetic context.", signature: "test-signature", index: 0, format: "unknown" }] : "Synthetic context.";
      const delta = !first ? { content: "Continued." } : {
        [field]: reasoning,
        ...(tool ? { tool_calls: [{ index: 0, id: "call-read", type: "function", function: { name: "read", arguments: '{"path":"fixture.txt"}' } }] } : { content: "Ready." }),
      };
      const frame = (delta, finish_reason = null) => `data: ${JSON.stringify({ id: "gen-fixture", choices: [{ index: 0, delta, finish_reason }] })}\n\n`;
      return new Response(frame(delta) + frame({}, first && tool ? "tool_calls" : "stop") + "data: [DONE]\n\n", { headers: { "content-type": "text/event-stream" } });
    };
    const options = { apiKey: "synthetic-fixture-only", fetch, maxRetries: 0, maxTokens: 4096 };
    const first = await stream(model, context, options).result();
    assert.notEqual(first.stopReason, "error", first.errorMessage);
    context.messages.push(first, tool
      ? { role: "toolResult", toolCallId: "call-read", toolName: "read", content: [{ type: "text", text: "Synthetic tool result." }], isError: false, timestamp: 2 }
      : { role: "user", content: "Continue.", timestamp: 2 });
    const next = await stream(model, context, options).result();
    assert.notEqual(next.stopReason, "error", next.errorMessage);
    assert.equal(count, 2);
    assert.ok(cases.at(-1).body.messages.some(message => message[field] !== undefined));
  }
}
console.log(JSON.stringify(cases));
