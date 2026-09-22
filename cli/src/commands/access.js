/**
 * puppyone access <subcommand>
 *
 * THE unified entry point for managing ALL Access Points:
 * SaaS datasources, agents, MCP endpoints, sandbox endpoints, direct access,
 * and database connectors.
 *
 * Product connector state lives in the backend connector/scope registry.
 *
 * Config is passed via generic mechanisms — no per-provider code:
 *   --set key=value   (repeatable, auto type-coerced)
 *   --config '{}'     (JSON, lower priority than --set)
 *   access schema <p> (discover available fields from backend)
 */

import { createClient } from "../api.js";
import { buildMcpConnection } from "../mcp-config.js";
import { createOutput } from "../output.js";
import { requireProject, withErrors, formatDate } from "../helpers.js";

// ── Provider alias map ──────────────────────────────────────

const PROVIDER_ALIASES = {
  gcal: "google_calendar", "google-calendar": "google_calendar",
  gh: "github",
  gdrive: "google_drive", "google-drive": "google_drive",
  gdocs: "google_docs", "google-docs": "google_docs",
  gsheets: "google_sheets", "google-sheets": "google_sheets",
  gsc: "google_search_console", "google-search-console": "google_search_console",
  web: "url",
  db: "database",
};

const PLATFORM_TYPES = new Set(["agent", "mcp", "sandbox", "direct"]);

const OAUTH_ENDPOINTS = {
  github: "github",
  google_drive: "google-drive",
  google_docs: "google-docs",
  google_sheets: "google-sheets",
  gmail: "gmail",
  google_calendar: "google-calendar",
  google_search_console: "google-search-console",
};

function resolveProvider(input) {
  const lower = input.toLowerCase().replace(/[\s-]+/g, "_");
  return PROVIDER_ALIASES[lower] || lower;
}

function isPlatformType(provider) {
  return PLATFORM_TYPES.has(provider);
}

// ── Helpers ─────────────────────────────────────────────────

const STATUS_ICONS = { active: "\u25CF", syncing: "\u25CF", paused: "\u25CB", error: "\u2717" };
function statusLabel(s) { return `${STATUS_ICONS[s] || "\u25CF"} ${s}`; }
function maskKey(key) {
  if (!key || key.length < 8) return key || "\u2014";
  const idx = key.indexOf("_");
  const pre = idx > 0 ? idx + 1 : 4;
  return key.slice(0, pre) + "..." + key.slice(-4);
}
function timeAgo(isoString) {
  if (!isoString) return "never";
  const diff = Date.now() - new Date(isoString).getTime();
  if (diff < 0) return "just now";
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function parseSetValues(setArgs) {
  if (!setArgs || setArgs.length === 0) return {};
  const config = {};
  for (const pair of setArgs) {
    const eqIdx = pair.indexOf("=");
    if (eqIdx < 1) continue;
    const key = pair.slice(0, eqIdx).trim();
    let val = pair.slice(eqIdx + 1).trim();
    if (val === "true") val = true;
    else if (val === "false") val = false;
    else if (val === "null") val = null;
    else if (val !== "" && !isNaN(Number(val))) val = Number(val);
    config[key] = val;
  }
  return config;
}

let _connectorCache = null;
async function fetchConnectorSpecs(client) {
  if (_connectorCache) return _connectorCache;
  try {
    const data = await client.get("/sync/connectors");
    _connectorCache = Array.isArray(data) ? data : (data?.data ?? data?.connectors ?? []);
    return _connectorCache;
  } catch {
    return [];
  }
}

function findSpec(specs, provider) {
  return specs.find(s => s.provider === provider);
}

const DEFAULT_NAMES = {
  agent: "Agent", mcp: "MCP Endpoint", sandbox: "Sandbox",
  direct: "Direct Access", database: "Database",
};
function _defaultName(provider) {
  return DEFAULT_NAMES[provider] || provider;
}

function _scopeMode(permission) {
  const p = String(permission || "rw").toLowerCase();
  if (p === "read" || p === "r" || p === "readonly") return "r";
  return "rw";
}

// ── OAuth helpers (from sync.js) ────────────────────────────

async function fetchProviderMeta(client) {
  try {
    const data = await client.get("/sync/connectors");
    const specs = Array.isArray(data) ? data : data?.items ?? [];
    const map = {};
    for (const s of specs) {
      map[s.provider] = {
        name: s.display_name ?? s.provider,
        auth: s.auth ?? "none",
        oauth_type: s.oauth_type ?? null,
        oauth_ui_type: s.oauth_ui_type ?? null,
      };
    }
    return map;
  } catch {
    return null;
  }
}

// ═════════════════════════════════════════════════════════════
// Register
// ═════════════════════════════════════════════════════════════

export function registerAccess(program) {
  const access = program
    .command("access")
    .description("Manage Access Points — unified entry for all access types");

  // ── schema ────────────────────────────────────────────────

  access
    .command("schema")
    .description("Show config fields for a provider (fetched from backend)")
    .argument("<provider>", "provider name (gmail, gcal, notion, github, ...)")
    .action(withErrors(async (providerArg, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);
      const provider = resolveProvider(providerArg);

      if (isPlatformType(provider)) {
        out.info(`\n  "${provider}" is a platform type.`);
        out.info("  These do not have configurable sync fields.");
        out.info("  Use `puppyone access add <type> --name <name>` to create.\n");
        return;
      }

      const specs = await fetchConnectorSpecs(client);
      const spec = findSpec(specs, provider);

      if (!spec) {
        out.error("UNKNOWN_PROVIDER", `Unknown provider: "${providerArg}".`,
          "Run `puppyone access providers` to see available providers.");
        return;
      }

      out.info("");
      out.info(`  ${spec.display_name}  (${spec.provider})`);
      out.info(`  Auth: ${spec.auth}`);
      if (spec.oauth_type) out.info(`  OAuth type: ${spec.oauth_type}`);
      out.info(`  Sync modes: ${(spec.supported_sync_modes || []).join(", ")}`);
      out.info("");

      const fields = spec.config_fields || [];
      if (fields.length > 0) {
        out.info("  Config fields (use --set key=value):");
        out.info("");
        out.table(fields.map(f => ({
          key: f.key,
          type: f.type || "text",
          label: f.label,
          default: f.default !== undefined && f.default !== null ? String(f.default) : "\u2014",
          required: f.required ? "yes" : "",
          placeholder: f.placeholder || "",
        })), [
          { key: "key", label: "KEY" },
          { key: "type", label: "TYPE" },
          { key: "label", label: "DESCRIPTION" },
          { key: "default", label: "DEFAULT" },
          { key: "required", label: "REQ" },
          { key: "placeholder", label: "EXAMPLE" },
        ]);
      } else {
        out.info("  No configurable fields. Only general options apply.");
      }

      out.info("");
      out.info("  General options (all sync providers):");
      out.info("    --set source_url=<url>    source URL (if applicable)");
      out.info("    --name <name>             access point name");
      out.info("    --folder <path>           target folder in project");
      out.info("    --mode <mode>             import_once | manual | scheduled");
      out.info("    --config <json>           raw JSON config (merged, lower priority)");

      if (spec.auth === "oauth") {
        out.info("");
        out.info("  OAuth required. Authorize first:");
        out.info(`    puppyone access auth ${providerArg}`);
      }

      out.info("");
      out.info("  Example:");
      if (fields.some(f => f.key === "source_url")) {
        out.info(`    puppyone access add ${providerArg} <url>`);
      } else if (fields.length > 0) {
        const example = fields.map(f => {
          const val = f.placeholder || f.default || `<${f.key}>`;
          return `--set ${f.key}=${val}`;
        }).join(" ");
        out.info(`    puppyone access add ${providerArg} ${example}`);
      } else {
        out.info(`    puppyone access add ${providerArg}`);
      }
      out.info("");

      out.success?.({ provider: spec.provider, config_fields: fields, auth: spec.auth });
    }));

  // ── providers ─────────────────────────────────────────────

  access
    .command("providers")
    .description("List all supported provider types")
    .action(withErrors(async (opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);

      let projectId;
      try { projectId = requireProject(cmd); } catch { projectId = null; }

      try {
        if (!projectId) throw new Error("skip");
        const data = await client.get("/sync/connectors", { project_id: projectId });
        const connectors = Array.isArray(data) ? data : data?.items ?? [];
        if (connectors.length) {
          out.info("\n  SaaS Data Sources (server pull):\n");
          out.table(
            connectors.map(c => ({
              provider: c.provider ?? c.name ?? c.id,
              name: c.display_name ?? c.name ?? "-",
              auth: c.auth_type ?? c.auth ?? "-",
            })),
            [
              { key: "provider", label: "PROVIDER" },
              { key: "name", label: "NAME" },
              { key: "auth", label: "AUTH" },
            ]
          );
        }
      } catch { /* fall through */ }

      out.info("\n  Built-in access types:\n");
      out.table([
        { provider: "direct", name: "Direct Access", description: "Scoped Context Drive access" },
        { provider: "agent", name: "AI Agent", description: "LLM agent with scoped read/write access" },
        { provider: "mcp", name: "MCP Endpoint", description: "Model Context Protocol server" },
        { provider: "sandbox", name: "Sandbox", description: "Isolated code execution" },
        { provider: "database", name: "Database", description: "External DB access" },
      ], [
        { key: "provider", label: "PROVIDER" },
        { key: "name", label: "NAME" },
        { key: "description", label: "DESCRIPTION" },
      ]);

      out.info("\n  Create: puppyone access add <provider> [source] [options]");
      out.info("  Schema: puppyone access schema <provider>\n");
    }));

  // ── auth (OAuth) ──────────────────────────────────────────

  access
    .command("auth")
    .description("Authorize an OAuth provider")
    .argument("<provider>", "provider name (github, gdrive, gmail, ...)")
    .action(withErrors(async (providerArg, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);
      const provider = resolveProvider(providerArg);
      const dynamicProviders = await fetchProviderMeta(client);
      const meta = dynamicProviders?.[provider] ?? null;

      if (!meta) {
        out.error("UNKNOWN_PROVIDER", `Unknown provider: ${providerArg}`,
          "Run `puppyone access providers` to see the list.");
        return;
      }

      if (meta.auth !== "oauth") {
        out.info(`${meta.name} doesn't use OAuth. Use \`puppyone access add ${providerArg}\` directly.`);
        return;
      }

      const oauthPath = OAUTH_ENDPOINTS[provider] ?? meta.oauth_ui_type?.replace(/_/g, "-");
      if (!oauthPath) {
        out.error("NO_OAUTH", `OAuth not configured for ${meta.name}.`);
        return;
      }

      const data = await client.get(`/oauth/${oauthPath}/authorize`);
      const authUrl = data?.url ?? data?.authorization_url ?? data;

      out.info("Open this URL to authorize:");
      out.info("");
      out.info(`  ${authUrl}`);
      out.info("");
      out.info("Then check status:");
      out.info(`  puppyone access auth-status ${providerArg}`);
      out.success({ provider, authorization_url: authUrl });
    }));

  // ── auth-status ───────────────────────────────────────────

  access
    .command("auth-status")
    .description("Check OAuth authorization status")
    .argument("<provider>", "provider name")
    .action(withErrors(async (providerArg, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);
      const provider = resolveProvider(providerArg);
      const oauthPath = OAUTH_ENDPOINTS[provider];

      if (!oauthPath) {
        out.error("NOT_OAUTH", `${providerArg} doesn't use OAuth.`);
        return;
      }

      const data = await client.get(`/oauth/${oauthPath}/status`);
      const connected = data?.connected ?? data?.is_connected ?? false;

      if (connected) {
        out.info(`${providerArg}: authorized \u2713`);
      } else {
        out.info(`${providerArg}: not authorized \u2717`);
        out.info(`  Run \`puppyone access auth ${providerArg}\` to authorize.`);
      }
      out.success({ provider, connected });
    }));

  // ── add ───────────────────────────────────────────────────

  access
    .command("add")
    .description("Add a new Access Point (any provider type)")
    .argument("<type>", "provider: github | gmail | notion | agent | mcp | sandbox | database | direct | ...")
    .argument("[source]", "source URL, path, or name (depends on type)")
    .option("--name <name>", "access point name")
    .option("--folder <folder>", "target folder path in project (for syncs)")
    .option("--scope <scope>", "Context Drive path scope (default: /)")
    .option("--permission <perm>", "permission: read | write | rw", "rw")
    .option("--mode <mode>", "sync mode: import_once | manual | scheduled", "import_once")
    .option("--model <model>", "LLM model (for agent type)")
    .option("--system-prompt <prompt>", "system prompt (for agent type)")
    .option("--type <subtype>", "sub-type: chat | devbox (agent), e2b | docker (sandbox)")
    .option("--set <kv...>", "config key=value (repeatable)")
    .option("--config <json>", "provider-specific config as JSON (merged, lower priority than --set)")
    .option("--gateway <id>", "Gateway ID (required for datasource providers, auto-detected if only one)")
    .action(withErrors(async (type, source, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);
      const projectId = requireProject(cmd);
      const provider = resolveProvider(type);
      const scope = opts.scope || opts.folder || null;

      // ── All types go through POST /access (unified backend) ──

      const config = {};
      if (opts.config) {
        try { Object.assign(config, JSON.parse(opts.config)); } catch {
          out.error("INVALID_JSON", "Invalid --config JSON.");
          return;
        }
      }
      Object.assign(config, parseSetValues(opts.set));

      if (isPlatformType(provider) || provider === "database") {
        if (provider === "direct") {
          const existingScope = typeof config.scope === "object" && config.scope ? config.scope : {};
          config.scope = {
            ...existingScope,
            path: scope ?? existingScope.path ?? "",
            exclude: Array.isArray(existingScope.exclude) ? existingScope.exclude : [],
            mode: _scopeMode(opts.permission),
          };
        }

        const body = {
          project_id: projectId,
          provider,
          name: opts.name || source || _defaultName(provider),
          path: scope,
          config,
        };

        if (provider === "agent") {
          if (opts.type) config.type = opts.type;
          if (opts.model) config.model = opts.model;
          if (opts.systemPrompt) config.system_prompt = opts.systemPrompt;
          body.config = config;
        }

        out.step(`Creating ${provider} access point...`);
        const created = await client.post("/access", body);
        out.done("done");

        out.info(`  ${provider} created: ${created.name || created.id}`);
        _showProviderGuidance(out, { ...created, provider }, client.baseUrl);
        out.success?.({ access: created });
        return;
      }

      // ── SaaS datasource providers ──

      const specs = await fetchConnectorSpecs(client);
      const spec = findSpec(specs, provider);

      if (!spec) {
        out.error("UNKNOWN_PROVIDER", `Unknown provider: "${type}".`,
          "Run `puppyone access providers` to see available types.\nRun `puppyone access schema <provider>` for config details.");
        return;
      }

      // OAuth check
      if (spec.auth === "oauth" && spec.oauth_type) {
        out.step(`Checking ${spec.display_name} authorization...`);
        try {
          const oauthPath = spec.oauth_type.replace(/_/g, "-");
          const oauthStatus = await client.get(`/oauth/${oauthPath}/status`);
          const connected = oauthStatus?.connected ?? oauthStatus?.is_connected ?? false;
          if (!connected) {
            out.info("");
            out.info(`  ${spec.display_name} is not authorized yet.`);
            out.info("");
            out.info("  Step 1: Authorize");
            out.info(`    puppyone access auth ${type}`);
            out.info("");
            out.info("  Step 2: Then re-run this command");
            out.info(`    puppyone access add ${type}${source ? " " + source : ""}`);
            out.info("");
            try {
              const authData = await client.get(`/oauth/${oauthPath}/authorize`);
              const authUrl = authData?.url ?? authData?.authorization_url ?? authData;
              if (authUrl && typeof authUrl === "string" && authUrl.startsWith("http")) {
                out.info("  Opening browser for authorization...");
                out.info(`  ${authUrl}`);
                try {
                  const { exec: execCb } = await import("node:child_process");
                  const openCmd = process.platform === "darwin" ? "open" : process.platform === "win32" ? "start" : "xdg-open";
                  execCb(`${openCmd} "${authUrl}"`);
                } catch {}
              }
            } catch {}
            return;
          }
          out.info("authorized \u2713");
        } catch {
          out.info("(could not check OAuth status, proceeding)");
        }
      }

      if (source) config.source_url = source;

      // Validate required fields from spec
      const fields = spec.config_fields || [];
      for (const f of fields) {
        if (!f.required) continue;
        if (!config[f.key] && config[f.key] !== 0) {
          out.error("MISSING_FIELD", `${spec.display_name} requires "${f.key}" (${f.label}).`,
            `Use --set ${f.key}=<value> or run \`puppyone access schema ${type}\` for details.`);
          return;
        }
      }

      // Datasource: target path is required (where synced data lands)
      const targetPath = scope || source;
      if (!targetPath) {
        out.error("MISSING_PATH", `${spec.display_name} requires a target path (--scope or --folder).`,
          `Example: puppyone access add ${type} --scope /my-data`);
        return;
      }

      // Resolve gateway for datasource providers
      let gatewayId = opts.gateway || null;
      if (!gatewayId) {
        // Auto-detect: if user has exactly one gateway for this provider, use it
        try {
          const gateways = await client.get("/gateways", { params: { provider } });
          const gwList = gateways?.data || gateways || [];
          if (Array.isArray(gwList) && gwList.length === 1) {
            gatewayId = gwList[0].id;
            out.info(`  Using gateway: ${gwList[0].name || gwList[0].id}`);
          } else if (Array.isArray(gwList) && gwList.length > 1) {
            out.info(`  Multiple ${provider} gateways found. Use --gateway <id> to specify:`);
            for (const gw of gwList) {
              out.info(`    ${gw.id}  ${gw.name || "—"}`);
            }
            return;
          }
        } catch {
          // Gateway API not available yet, proceed without
        }
      }

      const body = {
        project_id: projectId,
        provider,
        name: opts.name || spec.display_name,
        path: targetPath,
        config,
        sync_mode: opts.mode || "import_once",
        gateway_id: gatewayId,
      };

      out.step(`Adding ${spec.display_name} sync...`);
      const created = await client.post("/access", body);
      out.done("done");

      out.info(`  Created: ${created.name || created.id}`);
      out.info("");
      out.info(`  View:    puppyone access ls --provider ${provider}`);
      out.info(`  Refresh: puppyone access refresh ${created.id}`);
      out.info(`  Schema:  puppyone access schema ${type}`);
      out.success?.({ access: created });
    }));

  // ── ls ────────────────────────────────────────────────────

  access
    .command("ls")
    .description("List all Access Points in the current project")
    .option("--provider <provider>", "filter by provider")
    .option("--status <status>", "filter by status (active, paused, error, syncing)")
    .action(withErrors(async (opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);
      const projectId = requireProject(cmd);

      const query = { project_id: projectId };
      if (opts.provider) query.provider = resolveProvider(opts.provider);
      if (opts.status) query.status = opts.status;

      const connections = await client.get("/access", query);
      out.success?.({ connections });

      if (!connections || connections.length === 0) {
        out.info("\n  No access points found.\n  Run `puppyone access add <type> ...` to create one.\n");
        return;
      }

      out.info(`\n  Access Points (${connections.length})\n`);

      out.table(connections.map(c => ({
        id: c.id.slice(0, 8) + "...",
        provider: c.provider,
        name: (c.name || "").slice(0, 25),
        direction: c.direction || "\u2014",
        status: statusLabel(c.status),
        // The list endpoint no longer returns the raw access_key (ISSUE-002);
        // prefer the masked has_key/key_last4 fields, and fall back to masking a
        // full key if an older backend still sends one. Use `access info <id>`
        // to reveal the full key.
        key: c.access_key ? maskKey(c.access_key) : (c.has_key ? `...${c.key_last4}` : "\u2014"),
        lastSync: c.last_synced_at ? timeAgo(c.last_synced_at) : "\u2014",
      })), [
        { key: "id", label: "ID" },
        { key: "provider", label: "PROVIDER" },
        { key: "name", label: "NAME" },
        { key: "direction", label: "DIR" },
        { key: "status", label: "STATUS" },
        { key: "key", label: "KEY" },
        { key: "lastSync", label: "LAST SYNC" },
      ]);

      const byProvider = {};
      for (const c of connections) byProvider[c.provider] = (byProvider[c.provider] || 0) + 1;
      const summary = Object.entries(byProvider).map(([k, v]) => `${v} ${k}`).join(", ");
      const errCount = connections.filter(c => c.status === "error").length;
      out.info(`\n  ${connections.length} total: ${summary}${errCount > 0 ? ` (${errCount} error)` : ""}\n`);
    }));

  // ── info ──────────────────────────────────────────────────

  access
    .command("info")
    .description("Show detailed info for an Access Point")
    .argument("<id>", "access point ID")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);
      const c = await client.get(`/access/${id}`);
      out.success?.({ access: c });

      out.info("");
      out.kv([
        ["ID", c.id],
        ["Provider", c.provider],
        ["Name", c.name || "\u2014"],
        ["Project", c.project_id],
        ["Path / Scope", c.path || "\u2014"],
        ["Direction", c.direction || "\u2014"],
        ["Status", statusLabel(c.status)],
        ["Access Key", c.access_key || "\u2014"],
        ["Trigger", c.trigger ? JSON.stringify(c.trigger) : "\u2014"],
        ["Last Sync", c.last_synced_at ? formatDate(c.last_synced_at) : "never"],
        ["Error", c.error_message || "\u2014"],
        ["Created", formatDate(c.created_at)],
        ["Updated", formatDate(c.updated_at)],
      ]);
      out.info("");

      if (c.config && Object.keys(c.config).length > 0) {
        out.info("  Config:");
        for (const [k, v] of Object.entries(c.config)) {
          if (k === "api_key" || k === "access_key") { out.info(`    ${k}: ***`); continue; }
          const display = typeof v === "object" ? JSON.stringify(v) : String(v);
          out.info(`    ${k}: ${display.slice(0, 80)}`);
        }
        out.info("");
      }

      _showProviderGuidance(out, c, client.baseUrl);
    }));

  // ── pause / resume / rm / key / refresh / trigger ────────

  access.command("pause").description("Pause an Access Point").argument("<id>")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd); const client = createClient(cmd);
      await client.patch(`/access/${id}`, { status: "paused" });
      out.success?.({ id, status: "paused" }); out.done(`Access point ${id} paused.`);
    }));

  access.command("resume").description("Resume a paused Access Point").argument("<id>")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd); const client = createClient(cmd);
      await client.patch(`/access/${id}`, { status: "active" });
      out.success?.({ id, status: "active" }); out.done(`Access point ${id} resumed.`);
    }));

  access.command("rm").description("Delete an Access Point").argument("<id>")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd); const client = createClient(cmd);
      await client.del(`/access/${id}`);
      out.success?.({ id, deleted: true }); out.done(`Access point ${id} deleted.`);
    }));

  access.command("key").description("Show or regenerate access key").argument("<id>").option("--regenerate")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd); const client = createClient(cmd);
      if (opts.regenerate) {
        const result = await client.post(`/access/${id}/regenerate-key`);
        out.success?.({ id, access_key: result.access_key }); out.done(`New key: ${result.access_key}`);
      } else {
        const c = await client.get(`/access/${id}`);
        out.success?.({ id, access_key: c.access_key });
        c.access_key ? out.raw(c.access_key) : out.info("  No access key set.");
      }
    }));

  access.command("refresh").description("Trigger a manual sync refresh").argument("<id>")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd); const client = createClient(cmd);
      await client.post(`/sync/syncs/${id}/refresh`);
      out.success?.({ id, refreshed: true }); out.done(`Refresh triggered for ${id}.`);
    }));

  access.command("trigger").description("Update trigger mode").argument("<id>").argument("<mode>", "manual | import_once | scheduled")
    .action(withErrors(async (id, mode, opts, cmd) => {
      const out = createOutput(cmd); const client = createClient(cmd);
      await client.patch(`/access/${id}`, { trigger: { type: mode } });
      out.success?.({ id, trigger: { type: mode } }); out.done(`Trigger mode set to "${mode}" for ${id}.`);
    }));

  // ── update ────────────────────────────────────────────────

  access
    .command("update")
    .description("Update config of an existing Access Point")
    .argument("<id>", "access point ID")
    .option("--set <kv...>", "config key=value (repeatable)")
    .option("--config <json>", "full config JSON (merged)")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);

      const c = await client.get(`/access/${id}`);
      const existingConfig = c.config || {};

      const updates = {};
      if (opts.config) {
        try { Object.assign(updates, JSON.parse(opts.config)); } catch {
          out.error("INVALID_JSON", "Invalid --config JSON.");
          return;
        }
      }
      Object.assign(updates, parseSetValues(opts.set));

      if (Object.keys(updates).length === 0) {
        out.error("NO_CHANGES", "No updates provided. Use --set or --config.");
        return;
      }

      const newConfig = { ...existingConfig, ...updates };
      await client.patch(`/access/${id}`, { config: newConfig });
      out.done(`Access point ${id} config updated.`);

      const changedKeys = Object.keys(updates).join(", ");
      out.info(`  Updated keys: ${changedKeys}`);
      out.success?.({ id, updated_keys: Object.keys(updates) });
    }));

  // ── logs ──────────────────────────────────────────────────

  access
    .command("logs")
    .description("Show execution history for an Access Point")
    .argument("<id>", "access point ID")
    .option("--limit <n>", "max runs to show", "10")
    .option("--run <runId>", "show full details for a specific run")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);

      if (opts.run) {
        const run = await client.get(`/sync/runs/${opts.run}`);
        out.info("");
        out.kv([
          ["Run ID", run.id],
          ["Access Point ID", run.access_point_id || run.sync_id],
          ["Status", run.status],
          ["Trigger", run.trigger_type || "\u2014"],
          ["Started", run.started_at || "\u2014"],
          ["Finished", run.finished_at || "\u2014"],
          ["Duration", run.duration_ms != null ? `${run.duration_ms}ms` : "\u2014"],
          ["Exit Code", run.exit_code != null ? String(run.exit_code) : "\u2014"],
          ["Summary", run.result_summary || "\u2014"],
          ["Error", run.error || "\u2014"],
        ]);
        if (run.stdout) {
          out.info("\n  \u2500\u2500 stdout \u2500\u2500\n");
          out.info(run.stdout.slice(0, 5000));
          if (run.stdout.length > 5000) out.info("\n  ... (truncated)");
        }
        out.info("");
        out.success?.({ run });
        return;
      }

      const limit = Number(opts.limit) || 10;
      const runs = await client.get(`/sync/syncs/${id}/runs`, { limit });
      out.success?.({ runs });

      if (!runs || runs.length === 0) {
        out.info("\n  No execution history yet.\n  Run `puppyone access refresh <id>` to trigger one.\n");
        return;
      }

      out.info(`\n  Execution history (${runs.length})\n`);

      out.table(runs.map(r => ({
        id: r.id.slice(0, 8) + "...",
        status: r.status,
        trigger: r.trigger_type || "\u2014",
        started: r.started_at ? timeAgo(r.started_at) : "\u2014",
        duration: r.duration_ms != null ? `${r.duration_ms}ms` : "\u2014",
        summary: (r.result_summary || r.error || "").slice(0, 40),
      })), [
        { key: "id", label: "RUN" },
        { key: "status", label: "STATUS" },
        { key: "trigger", label: "TRIGGER" },
        { key: "started", label: "STARTED" },
        { key: "duration", label: "DURATION" },
        { key: "summary", label: "SUMMARY" },
      ]);
      out.info("");
    }));

  // ── run ───────────────────────────────────────────────────

  access
    .command("run")
    .description("Trigger execution and show result")
    .argument("<id>", "access point ID")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);

      out.step("Executing...");
      const result = await client.post(`/sync/syncs/${id}/refresh`);
      out.done("done");

      const data = result?.data;
      if (data && data.synced > 0 && data.results?.length > 0) {
        const r = data.results[0];
        out.info(`  Provider: ${r.provider}`);
        if (r.commit_id) {
          out.info(`  Commit:   @${String(r.commit_id).slice(0, 8)}`);
        }
        out.info(`  Summary:  ${r.summary || "\u2014"}`);
        if (r.run_id) {
          out.info(`  Run ID:   ${r.run_id}`);
          out.info(`\n  View logs: puppyone access logs ${id} --run ${r.run_id}`);
        }
      } else {
        out.info("  No changes detected (content unchanged).");
      }
      out.info("");
      out.success?.({ result: data });
    }));
}


// ── Type-specific guidance ──────────────────────────────────

function _showAgentGuidance(out, agent, baseUrl) {
  const connection = buildMcpConnection(agent, baseUrl);
  if (!connection) return;
  out.info(`\n  MCP API Key: ${connection.apiKey}`);
  out.info("  (save this key — it won't be shown again)\n");
  out.info("  Chat:");
  out.info(`    puppyone chat ${agent.id}\n`);
  _showMcpClientConfig(out, connection);
}

function _showMcpGuidance(out, endpoint, baseUrl) {
  const connection = buildMcpConnection(endpoint, baseUrl);
  if (!connection) return;
  out.info(`\n  API Key: ${connection.apiKey}`);
  out.info("  (save this key — it won't be shown again)\n");
  _showMcpClientConfig(out, connection);
}

function _showMcpClientConfig(out, connection) {
  out.info(`  Server URL: ${connection.serverUrl}`);
  out.info(`  Header: Authorization: ${connection.authorization}\n`);
  out.info("  Claude Desktop / Cursor HTTP MCP config:");
  out.info(JSON.stringify(connection.clientConfig, null, 2));
}

function _showSandboxGuidance(out, endpoint) {
  const key = endpoint.api_key || endpoint.access_key;
  if (!key) return;
  out.info(`\n  Access Key: ${key}\n`);
  out.info("  Execute commands:");
  out.info(`    (use via Agent or API)`);
}

function _profileSlug(name) {
  return (name || "access-point")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "access-point";
}

function _shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\\''")}'`;
}

function _showFsGuidance(out, connection, baseUrl, profileFallback = "file-access") {
  const key = connection.access_key;
  if (!key) return;
  const profile = _profileSlug(connection.path || connection.name || profileFallback);
  out.info(`\n  Access Key: ${key}`);
  out.info("  (save this key — it won't be shown again)\n");
  out.info("  \u2500 Use from PuppyOne CLI:");
  out.info("    npm install -g puppyone@latest");
  out.info(`    printf '%s' ${_shellQuote(key)} | puppyone ap login ${_shellQuote(profile)} --api-url ${_shellQuote(baseUrl)} --access-key-stdin`);
  out.info("    puppyone fs semantics");
  out.info("    puppyone fs ls -la");
  out.info("    puppyone fs tree -L 2");
  out.info("    puppyone fs cat <path-from-ls>");
  out.info("");
}

function _showProviderGuidance(out, connection, baseUrl) {
  const { provider, access_key: key, id } = connection;
  if (provider === "agent") {
    out.info("  \u2500 How to use this agent:");
    _showAgentGuidance(out, connection, baseUrl);
  } else if (provider === "mcp") {
    out.info("  \u2500 How to connect:");
    _showMcpGuidance(out, connection, baseUrl);
  } else if (provider === "sandbox") {
    out.info("  \u2500 Execute via Agent or API.\n");
  } else if (provider === "direct" && key) {
    _showFsGuidance(out, connection, baseUrl, "direct");
  } else {
    out.info("  \u2500 Management:");
    out.info(`    Refresh: puppyone access refresh ${id}`);
    out.info(`    Pause:   puppyone access pause ${id}`);
    out.info(`    Schema:  puppyone access schema ${provider}\n`);
  }
}
