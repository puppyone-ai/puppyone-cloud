# Change: Extract hosted file operation commands

## Why

The accepted backend layout separates hosted file operations from HTTP adapters
and the Git publication kernel. The user approved moving the existing command
implementations first, with compatibility exports instead of a repository-wide
import migration.

## What Changes

- Move `commands.py` and `native_commands.py` into `src/file_operations` without
  changing operation semantics, request digests, byte paths or publication.
- Keep explicit compatibility exports at the existing Product adapter paths.
- Use the new paths in the Web write route, dependency assembly and the Product
  adapter's internal dispatch. Keep other callers unchanged.

## Impact

- Affected spec: `project-structure`.
- No API, schema, runtime/provider, CLI/MCP behavior or deployment changes.
- HTTP relocation and final adapter/kernel cleanup are later batches; this
  change does not complete the entire target directory migration.
