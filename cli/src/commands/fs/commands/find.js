import { withErrors } from "../../../helpers.js";
import { createOutput } from "../../../output.js";
import { createApClient, extraHeaders } from "../lib/context.js";
import { parseFindArgs } from "../lib/find-expr.js";
import { helpBlock, LIMIT_NOTE, READ_STDOUT_NOTE, SCOPE_NOTE } from "../lib/help.js";
import { get } from "../lib/http.js";
import { parseIntegerOption } from "../lib/options.js";
import { scopedPath } from "../lib/paths.js";

export function registerFindCommand(fs) {
  fs
    .command("find")
    .description("Find paths within the access point scope")
    .argument("[args...]", "path and expression, e.g. . -name '*.md' -type f")
    .option("--limit <n>", "max tree entries scanned before truncation")
    .allowUnknownOption(true)
    .addHelpText("after", `${helpBlock({
      examples: [
        "puppyone fs find . -maxdepth 2 -type f",
        "puppyone fs find docs -name '*.md'",
        "puppyone fs find . -path 'notes/*' --limit 200",
        "puppyone --json fs find . -type f",
      ],
      notes: [
        SCOPE_NOTE,
        READ_STDOUT_NOTE,
        LIMIT_NOTE,
      ],
    })}

Expressions:
  -name <pattern>       match basename with wildcard pattern
  -iname <pattern>      case-insensitive -name
  -path <pattern>       match the full scoped path
  -type <f|d>           filter by file or directory
  -mindepth <n>         minimum search depth
  -maxdepth <n>         maximum search depth
  -not, !               negate the next predicate
  -print                accepted for Unix compatibility`)
    .action(withErrors(async (args, opts, cmd) => {
      const out = createOutput(cmd);
      const client = createApClient(cmd);
      const headers = await extraHeaders(cmd);
      const { path, filters } = parseFindArgs(args || []);
      const cleanPath = scopedPath(path);
      const limit = opts.limit != null ? parseIntegerOption(opts.limit, "--limit") : null;
      const query = {
        path: cleanPath,
        include_hidden: true,
      };
      if (limit != null) query.limit = limit;
      if (filters.mindepth != null) query.mindepth = parseIntegerOption(filters.mindepth, "-mindepth");
      if (filters.maxdepth != null) query.max_depth = parseIntegerOption(filters.maxdepth, "-maxdepth");
      if (filters.conditions?.length) query.conditions = JSON.stringify(filters.conditions);
      const result = await get(client, "/ap-fs/find", query, headers);
      const entries = result.entries || [];
      if (out.json) {
        out.success(result);
        return;
      }
      if (result.truncated) {
        out.warn(`stdout is incomplete: scanned ${result.scanned_count ?? result.returned_count ?? entries.length} tree entries because the ${result.limit ?? "configured"} entry limit was reached. Use -maxdepth, a narrower path, or --limit; use --json to inspect complete=false.`);
      }
      out.raw(entries.map(entry => entry.path || ".").join("\n"));
    }));
}
