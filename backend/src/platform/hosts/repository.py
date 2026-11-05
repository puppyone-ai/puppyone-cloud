"""Service-only database commands for device inventory; no direct table grants."""

from src.infra.supabase.dependencies import get_supabase_client

# Every service-only RPC the hosts module may call. A name missing here fails
# closed at runtime, so tests pin this set against the migration and callers.
HOST_COMMANDS = frozenset({
    "host_device_register", "host_device_catalog", "host_device_revoke",
    "host_folder_register", "host_folder_update", "host_folder_remove", "host_challenge_issue",
    "host_challenge_consume", "host_connection_open", "host_connection_renew",
    "host_connection_close", "host_folder_admit", "host_file_event_record",
})


class HostRepository:
    def __init__(self, client=None):
        self.client = client or get_supabase_client()

    def command(self, name: str, **params):
        if name not in HOST_COMMANDS:
            raise ValueError("Unknown host command")
        return self.client.rpc(name, params).execute().data
