import assert from "node:assert/strict";

import { buildMcpConnection } from "../src/mcp-config.js";
import { repositoryContractHeaders } from "../src/repository-contract.js";


const secret = "mcp_one_time_secret";
const connection = buildMcpConnection(
  {
    id: "endpoint-1",
    name: "Docs MCP",
    mcp_api_key: secret,
    mcp_server_url: "https://api.example.test/api/v1/mcp/proxy",
  },
  "https://ignored.example.test",
);

assert.ok(connection);
assert.equal(connection.serverUrl, "https://api.example.test/api/v1/mcp/proxy");
assert.equal(connection.authorization, `Bearer ${secret}`);
assert.equal(
  connection.clientConfig.mcpServers["docs-mcp"].headers.Authorization,
  `Bearer ${secret}`,
);
assert.equal(connection.serverUrl.includes(secret), false);
assert.equal(JSON.stringify(connection.clientConfig).includes("api_key="), false);
assert.equal(buildMcpConnection({ id: "endpoint-1" }, "https://api.example.test"), null);
assert.deepEqual(repositoryContractHeaders(), {
  "X-PuppyOne-Repository-Contract": "2",
});

console.log("mcp config contract: ok");
