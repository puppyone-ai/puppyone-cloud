# PuppyOne CLI v0.1.0

> Historical release note. The secret-bearing Git URL shown below was the
> v0.1.0 contract and is now legacy-only. Current clients use
> `/git/{project_id}.git` or `/git/{project_id}/scopes/{scope_id}.git` with a
> separately issued HTTP credential; see
> [the current CLI guide](../architecture/03-cli.md).

PuppyOne CLI exposes two current access paths:

- `puppyone fs ...` for scoped AP-FS commands over the backend API.
- Stock `git` against `/git/ap/<access_key>.git` for local repository workflows.

The CLI no longer depends on a custom version protocol. Product writes,
frontend saves, AP-FS commands, and Git pushes all publish through the
Version Engine and its Git-native object model.

## Commands

```bash
puppyone auth login
puppyone project use "My Project"
puppyone ap login default
puppyone fs ls
puppyone fs cat docs/readme.md
puppyone fs write config.json --content '{"model":"gpt-4"}'
puppyone fs mkdir notes
puppyone fs mv old.md new.md
puppyone fs rm temp.json
```

For local repository workflows:

```bash
git clone https://<host>/git/ap/<access_key>.git workspace
cd workspace
git add .
git commit -m "update docs"
git push origin main
```

All CLI file commands support `--json` for scripts and agent tools.
