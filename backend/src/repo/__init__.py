"""Repository surface module — scopes, connectors, identity, permissions.

The repository surface is split into four orthogonal sub-modules:

    scope_*       — repository_scopes path geometry CRUD
    identity_*    — project URL + prompt_template (the "access point")
    connector_*   — compatibility facade over access_surfaces + connections

Git smart-HTTP and PuppyOne CLI resolve credentials through Access Surfaces;
``repository_scopes`` contributes only optional path geometry.
"""
