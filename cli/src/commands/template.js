import { createClient } from "../api.js";
import { saveConfig } from "../config.js";
import { formatSize, requireOrg, withErrors } from "../helpers.js";
import { createOutput } from "../output.js";

export function registerTemplate(program) {
  const template = program
    .command("template")
    .alias("tmpl")
    .description("Browse templates and create independent projects");

  template
    .command("ls")
    .alias("list")
    .description("List templates from the configured Registry")
    .option("-q, --query <text>", "search names, descriptions, authors, and tags")
    .option("-c, --category <category>", "filter by category")
    .option("--cursor <cursor>", "continue a paginated listing")
    .option("--limit <number>", "maximum templates to return", parsePositiveInt, 50)
    .action(withErrors(async (opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);
      const catalog = await client.get("/templates", {
        q: opts.query,
        category: opts.category,
        cursor: opts.cursor,
        limit: opts.limit,
      });
      const templates = catalog?.templates ?? [];

      out.table(
        templates.map((item) => ({
          name: item.name,
          id: item.id,
          version: item.current_release?.version ?? "-",
          category: item.category ?? "-",
          files: item.current_release?.file_count ?? "-",
        })),
        [
          { key: "name", label: "NAME" },
          { key: "id", label: "ID" },
          { key: "version", label: "VERSION" },
          { key: "category", label: "CATEGORY" },
          { key: "files", label: "FILES" },
        ],
      );
      if (!templates.length && catalog?.registry?.catalog_enabled === false) {
        out.info("Template Registry is disabled for this deployment.");
      } else if (catalog?.next_cursor) {
        out.info(`\nMore templates are available. Continue with --cursor ${catalog.next_cursor}`);
      }
      out.success({
        registry: catalog?.registry ?? null,
        templates,
        next_cursor: catalog?.next_cursor ?? null,
      });
    }));

  template
    .command("show")
    .description("Show one template release and its file inventory")
    .argument("<id>", "template ID")
    .action(withErrors(async (id, _opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);
      const detail = await client.get(`/templates/${encodeURIComponent(id)}`);
      const release = detail.current_release;

      out.kv([
        ["Name:", detail.name],
        ["ID:", detail.id],
        ["Version:", release?.version],
        ["Author:", detail.author ?? "-"],
        ["Category:", detail.category ?? "-"],
        ["Files:", release?.file_count],
        ["Size:", formatSize(release?.total_bytes)],
        ["Description:", detail.description],
      ]);
      const files = detail.file_tree ?? [];
      if (files.length) {
        out.info("\n  Included files:");
        out.list(files.slice(0, 50));
        if (files.length > 50) out.info(`  … and ${files.length - 50} more`);
      }
      out.success({ template: detail });
    }));

  template
    .command("use")
    .description("Create a new independent project from a template")
    .argument("<id>", "template ID")
    .option("--release <id>", "immutable release ID (defaults to current)")
    .option("--name <name>", "override the new project name")
    .option("-d, --description <description>", "override the project description")
    .option("--no-activate", "do not make the created project active")
    .action(withErrors(async (id, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createClient(cmd);
      const orgId = requireOrg(cmd);
      const result = await client.post(
        `/templates/${encodeURIComponent(id)}/instantiate`,
        {
          org_id: orgId,
          release_id: opts.release,
          name: opts.name,
          description: opts.description,
        },
      );
      const project = result.project;

      if (opts.activate) {
        saveConfig({
          active_project: { id: project.id, name: project.name },
        });
      }
      out.info(
        `Project created from ${result.template_id}@${result.release_id}: ${project.name} (${project.id})`,
      );
      if (opts.activate) out.info("Active project updated.");
      out.success({
        template_id: result.template_id,
        release_id: result.release_id,
        project,
        active: Boolean(opts.activate),
      });
    }));
}

function parsePositiveInt(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 100) {
    throw new Error("limit must be an integer between 1 and 100");
  }
  return parsed;
}
