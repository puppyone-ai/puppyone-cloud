"""Product acceptance expectations, independent of ACTION_CAPABILITY.

Adding a named action requires an explicit choice in this reviewable matrix.
Organization membership alone never grants an Editor or Admin workspace role.
"""

ACTION_MINIMUM_ROLE = {
    "project.read": "viewer",
    "project.write": "admin",
    "project.settings.manage": "admin",
    "project.delete": "admin",
    "project.member.read": "admin",
    "project.member.manage": "admin",
    "project.share.manage": "admin",
    "content.read": "viewer",
    "content.write": "editor",
    "history.read": "viewer",
    "history.restore": "editor",
    "agent.read": "viewer",
    "agent.run": "editor",
    "agent.manage": "admin",
    "automation.run": "editor",
    "automation.manage": "admin",
    "access_surface.read": "viewer",
    "access_surface.manage": "admin",
    "access_surface.rotate_secret": "admin",
    "mcp.manage": "admin",
    "sandbox.manage": "admin",
    "scope.manage": "admin",
    "synchronize.manage": "admin",
    "import.source.manage": "admin",
    "ingest.write": "editor",
    "tool.use": "editor",
}

ROLE_LEVEL = {None: 0, "viewer": 1, "editor": 2, "admin": 3}


def expected_allowed(role, action):
    return ROLE_LEVEL[role] >= ROLE_LEVEL[ACTION_MINIMUM_ROLE[action]]
