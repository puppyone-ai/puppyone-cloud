function trimTrailingSlashes(value) {
  return String(value || "").replace(/\/+$/, "");
}

export function buildMcpConnection(connection, apiBase) {
  const apiKey = connection?.mcp_api_key || connection?.api_key || connection?.access_key;
  if (!apiKey) return null;

  const serverUrl = connection?.mcp_server_url
    || `${trimTrailingSlashes(apiBase)}/api/v1/mcp/proxy`;
  const name = String(connection?.name || connection?.id || "puppyone-mcp")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "puppyone-mcp";

  return {
    apiKey,
    serverUrl,
    authorization: `Bearer ${apiKey}`,
    clientConfig: {
      mcpServers: {
        [name]: {
          type: "http",
          url: serverUrl,
          headers: {
            Authorization: `Bearer ${apiKey}`,
          },
        },
      },
    },
  };
}
