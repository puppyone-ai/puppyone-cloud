"""Persistent client identity, independent of Surface configuration and credentials."""

from .grant_repository import AccessRepository


class RegistrationRepository(AccessRepository):
    def command(self, action, user, **payload):
        return self.call("access_registration_command", action=action, user=user, payload=payload)
