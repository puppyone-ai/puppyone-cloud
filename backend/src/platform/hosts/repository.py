"""Service-only database commands for device inventory; no direct table grants."""

from src.infra.supabase.dependencies import get_supabase_client


class HostRepository:
    def __init__(self, client=None):
        self.client = client or get_supabase_client()

    def command(self, name: str, **params):
        if name not in {
            "host_device_register", "host_device_catalog", "host_device_revoke",
            "host_folder_register", "host_folder_remove", "host_challenge_issue",
            "host_challenge_consume", "host_connection_open", "host_connection_renew",
            "host_connection_close", "host_folder_admit",
        }:
            raise ValueError("Unknown host command")
        return self.client.rpc(name, params).execute().data
