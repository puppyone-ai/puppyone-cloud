"""Landing-tool ingest: the reusable wrapper behind the marketing site's
"X → MCP" tools (pdf-to-mcp, etc.).

Two generic, tool-kind-parameterized endpoints (see ``router.py``):

* ``POST /landing/preview`` — public, login-free. Parses an uploaded source
  (per the tool's :class:`~src.platform.landing.registry.ToolSpec`) into
  markdown, stashes it in S3 under a short-TTL prefix, and returns a signed
  *ticket* + a preview. No Supabase user, no project/org rows are created.

* ``POST /landing/claim`` — requires a real (logged-in) user. Validates the
  ticket and runs the standard create-chain (project → scope → write content →
  mcp-endpoint) so the artifacts are *born owned* by that user. No
  re-parenting / ownership migration.

Why this shape (Option C): ``created_by`` is a hard FK to ``auth.users``, so
real artifacts can only be created under a real user. Deferring creation to
login avoids enabling Supabase anonymous sign-ins, anonymous orphan rows, an
abuse surface, and any claim/migration code. Abandoned previews expire via S3
lifecycle + ticket ``exp``.
"""
