"""Concrete Template Registry providers."""

from .builtin import BuiltinTemplateRegistryProvider
from .remote import RemoteTemplateRegistryProvider

__all__ = ["BuiltinTemplateRegistryProvider", "RemoteTemplateRegistryProvider"]
