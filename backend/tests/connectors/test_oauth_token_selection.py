from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.provider.oauth.gmail_service import GmailOAuthService
from src.provider.oauth.google_calendar_service import GoogleCalendarOAuthService
from src.provider.oauth.google_docs_service import GoogleDocsOAuthService
from src.provider.oauth.google_drive_service import GoogleDriveOAuthService
from src.provider.oauth.google_search_console_service import (
    GoogleSearchConsoleOAuthService,
)
from src.provider.oauth.google_sheets_service import GoogleSheetsOAuthService
from src.provider.oauth.models import OAuthConnection
from src.provider.oauth.repository import _select_preferred_connection_row


def test_select_preferred_connection_row_avoids_legacy_gateway_duplicate():
    rows = [
        {
            "id": 4,
            "user_id": "user-1",
            "provider": "gmail",
            "access_token": "legacy-token",
            "refresh_token": "legacy-refresh",
            "expires_at": None,
            "metadata": {"_legacy_gateway_id": "gw-1"},
            "created_at": "2026-04-11T09:14:20+00:00",
            "updated_at": "2026-04-11T09:14:20+00:00",
        },
        {
            "id": 1,
            "user_id": "user-1",
            "provider": "gmail",
            "access_token": "oauth-token",
            "refresh_token": "oauth-refresh",
            "expires_at": "2026-05-15T07:09:08+00:00",
            "metadata": {"user": {"email": "person@example.com"}},
            "created_at": "2026-02-19T08:33:19+00:00",
            "updated_at": "2026-02-19T08:33:19+00:00",
        },
    ]

    selected = _select_preferred_connection_row(rows)

    assert selected is not None
    assert selected["id"] == 1


@pytest.mark.parametrize(
    "service_cls",
    [
        GmailOAuthService,
        GoogleCalendarOAuthService,
        GoogleDocsOAuthService,
        GoogleDriveOAuthService,
        GoogleSearchConsoleOAuthService,
        GoogleSheetsOAuthService,
    ],
)
@pytest.mark.asyncio
async def test_google_oauth_services_refresh_legacy_rows_without_expiry(service_cls):
    service = object.__new__(service_cls)
    now = datetime.now(timezone.utc)

    async def get_connection(_user_id: str) -> OAuthConnection:
        return OAuthConnection(
            id=1,
            user_id="user-1",
            provider="google",
            access_token="stale-access-token",
            refresh_token="refresh-token",
            expires_at=None,
            metadata={},
            created_at=now,
            updated_at=now,
        )

    service.get_connection = get_connection

    assert await service.is_token_expired("user-1") is True


@pytest.mark.asyncio
async def test_google_oauth_services_keep_non_refreshable_rows_without_expiry():
    service = object.__new__(GmailOAuthService)
    now = datetime.now(timezone.utc)

    async def get_connection(_user_id: str) -> OAuthConnection:
        return OAuthConnection(
            id=1,
            user_id="user-1",
            provider="gmail",
            access_token="access-token",
            refresh_token=None,
            expires_at=None,
            metadata={},
            created_at=now,
            updated_at=now,
        )

    service.get_connection = get_connection

    assert await service.is_token_expired("user-1") is False


def test_oauth_repository_is_synchronous_and_keeps_upsert_semantics():
    import inspect

    from src.provider.oauth.models import OAuthConnectionCreate
    from src.provider.oauth.repository import OAuthRepository

    assert not any(
        inspect.iscoroutinefunction(member) for _, member in inspect.getmembers(OAuthRepository)
    )
    now = "2026-10-24T00:00:00+00:00"
    row = {
        "id": 7,
        "user_id": "user-1",
        "provider": "github",
        "access_token": "token",
        "created_at": now,
        "updated_at": now,
    }
    queries = []

    class Query:
        def __init__(self, data):
            self.data = data

        def __getattr__(self, _name):
            return lambda *args, **kwargs: self

        def execute(self):
            queries.append(self.data)
            return SimpleNamespace(data=self.data)

    class Client:
        def __init__(self):
            self.rows = []

        def table(self, name):
            assert name == "oauth_connections"
            data, self.rows = list(self.rows), [row]
            return Query(data)

    repository = OAuthRepository.__new__(OAuthRepository)
    repository.supabase = Client()

    created = repository.create(
        OAuthConnectionCreate(user_id="user-1", provider="github", access_token="token")
    )
    found = repository.get_by_user_and_provider("user-1", "github")
    deleted = repository.delete(7)

    assert created.id == found.id == 7
    assert deleted is True
    assert len(queries) == 4  # lookup + insert, lookup, delete


async def test_oauth_services_reach_the_repository_off_the_event_loop():
    import threading

    from src.provider.oauth.gmail_service import GmailOAuthService

    loop_thread = threading.get_ident()
    threads = []

    class Repository:
        def get_by_user_and_provider(self, user_id, provider):
            threads.append(threading.get_ident())
            assert (user_id, provider) == ("user-1", "gmail")

        def delete_by_user_and_provider(self, user_id, provider):
            threads.append(threading.get_ident())
            return True

    service = GmailOAuthService.__new__(GmailOAuthService)
    service.repository = Repository()
    assert await service.get_connection("user-1") is None
    assert await service.disconnect("user-1") is True
    assert threads and loop_thread not in threads
