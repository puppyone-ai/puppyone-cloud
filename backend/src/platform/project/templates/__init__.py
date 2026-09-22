"""Project templates — built-in starter content for new projects.

Public API (stable, used by ``router.py`` and ``auth/initialization.py``):

- ``TEMPLATES``               — dict[id, ProjectTemplate], loaded at import
- ``get_template(id)``        — single lookup
- ``list_templates()``        — picker metadata for the frontend
- ``seed_template_content()`` — write a template into a fresh project
- ``ProjectTemplate``         — the dataclass (for typing)

To add a new built-in template, drop a folder under ``builtin/`` — see
``README.md`` for the layout. No code change required.
"""

from .loader import (
    TEMPLATES,
    get_template,
    get_template_detail,
    list_templates,
    seed_template_content,
)
from .schema import ProjectTemplate

__all__ = [
    "ProjectTemplate",
    "TEMPLATES",
    "get_template",
    "get_template_detail",
    "list_templates",
    "seed_template_content",
]
