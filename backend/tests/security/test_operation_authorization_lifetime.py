import asyncio
from unittest.mock import Mock

import pytest

from src.exceptions import NotFoundException
from src.platform.authorization.lifetime import OperationAuthorization
from src.platform.authorization.models import ProjectAction
from src.utils.request_context import project_access_cache_var
from tests.authorization_fakes import authorization_for


@pytest.mark.asyncio
async def test_job_reuses_facts_but_revocation_is_checked_after_time_budget():
    auth = authorization_for("p")
    auth.authorize_fresh = Mock(wraps=auth.authorize_fresh)
    now = [0.0]
    lease = OperationAuthorization(auth, "p", "u", ProjectAction.CONTENT_READ, clock=lambda: now[0])
    token = project_access_cache_var.set({})
    try:
        # Populate a stale HTTP cache, which must not authorize a long job forever.
        original = auth.authorize("p", "u", ProjectAction.CONTENT_READ)
        cached = project_access_cache_var.get().copy()
        assert await lease.current() == original
        for _ in range(100):
            assert await lease.current() == original
        assert auth.authorize_fresh.call_count == 1
        auth._repository._facts.clear()
        now[0] = 31.0
        with pytest.raises(NotFoundException):
            await lease.current()
        assert auth.authorize_fresh.call_count == 2
        assert project_access_cache_var.get() == cached
    finally:
        project_access_cache_var.reset(token)


@pytest.mark.asyncio
async def test_concurrent_steps_share_one_revalidation_and_keep_users_separate():
    auth = authorization_for("p")
    auth.authorize_fresh = Mock(wraps=auth.authorize_fresh)
    now = [0.0]
    first = OperationAuthorization(auth, "p", "u", ProjectAction.CONTENT_READ, clock=lambda: now[0])
    second = OperationAuthorization(auth, "p", "other", ProjectAction.CONTENT_READ, clock=lambda: now[0])
    assert (await first.current()).user_id == "u"
    now[0] = 31.0
    results = await asyncio.gather(*(first.current() for _ in range(35)))
    assert all(result.user_id == "u" for result in results)
    assert auth.authorize_fresh.call_count == 2
    assert (await second.current()).user_id == "other"
    assert auth.authorize_fresh.call_count == 3
