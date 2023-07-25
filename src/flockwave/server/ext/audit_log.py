"""Extension that provides other extensions with an append-only audit log
database where other extensions can register events.
"""

from .base import Extension

from typing import Any


class AuditLogExtension(Extension):
    """Extension that provides other extensions with an append-only audit log
    database where other extensions can register events.

    The audit log may then be post-processed later using external scripts to
    produce basic reports.

    As an end user, you typically won't need to enable this extension directly.
    Other extensions relying on the audit log will declare it as a dependency so
    it gets enabled automatically if needed.
    """

    def configure(self, configuration: dict[str, Any]) -> None:
        pass

    def exports(self) -> dict[str, Any]:
        return {}


construct = AuditLogExtension
description = "Audit log provider for other extensions"
schema = {}
