"""The two chat app command functions on real PostgreSQL with every migration.

Intake deduplicates by platform message identity; leases and fences decide who
may write; a chat is connected to one project; only managers change chats; and
management actions appear in project activity without changing the old kinds.
"""

import pytest

from tests.platform.chat_apps.native.support import CommandError, code, member, project


def knock_telegram(db, project_id, user, chat="-100", n=1, name="Design team"):
    link = db.human(
        "start_link",
        user,
        project_id=project_id,
        platform="telegram",
        purpose="knock",
        code_hash=code(n),
    )
    result = db.gateway(
        "knock",
        platform="telegram",
        code_hash=code(n),
        external_chat_id=chat,
        chat_kind="group",
        display_name=name,
        owner_external_id="tg-alex",
    )
    return link, result


def allowed_chat(db, n=1, chat="-100"):
    project_id, user, org = project(db)
    _, result = knock_telegram(db, project_id, user, chat=chat, n=n)
    db.human(
        "decide",
        user,
        project_id=project_id,
        conversation_id=result["conversation_id"],
        decision="allow",
    )
    return project_id, user, org, result["conversation_id"]


def receive(db, key, chat="-100", **extra):
    values = dict(
        platform="telegram",
        message_key=key,
        via="message",
        kind="question",
        external_chat_id=chat,
        asker_external_id="tg-alex",
        asker_label="Alex",
        payload={"text": "hi"},
    )
    return db.gateway("receive", **{**values, **extra})


def drain_inbox(db):
    while (row := db.gateway("claim_inbox", worker="drain")) is not None:
        db.gateway(
            "finish_inbox",
            worker="drain",
            id=row["id"],
            fence=row["fence"],
            state="done",
            outcome="test",
        )


def test_the_same_platform_message_is_stored_once(db):
    first = receive(db, "-100:7")
    again = receive(db, "-100:7", via="guest")
    assert first["duplicate"] is False and again["duplicate"] is True
    assert db.sql("SELECT count(*) FROM chat_app_inbox WHERE message_key='-100:7'") == "1"


def test_knock_then_allow_activates_the_chat_and_says_hello(db):
    project_id, user, _ = project(db)
    link, result = knock_telegram(db, project_id, user, n=11, chat="-111")
    assert link["state"] == "waiting" and result["knocked"] is True
    status = db.human("link_status", user, project_id=project_id, link_id=link["id"])
    assert status["state"] == "knocked"
    assert status["conversation"]["display_name"] == "Design team"
    assert status["conversation"]["state"] == "asking"
    chat = db.human(
        "decide",
        user,
        project_id=project_id,
        conversation_id=result["conversation_id"],
        decision="allow",
    )
    assert chat["state"] == "active" and chat["who_can_ask"] == "owner"
    assert chat["daily_limit_units"] == 60 and chat["owner_name"] == "Alex"
    assert (
        db.sql(f"SELECT notice_code FROM chat_app_replies WHERE conversation_id='{chat['id']}'")
        == "welcome"
    )


def test_a_used_or_wrong_code_does_not_knock(db):
    project_id, user, _ = project(db)
    knock_telegram(db, project_id, user, n=21, chat="-121")
    again = db.gateway(
        "knock",
        platform="telegram",
        code_hash=code(21),
        external_chat_id="-122",
        chat_kind="group",
        display_name="Other",
        owner_external_id="x",
    )
    wrong = db.gateway(
        "knock",
        platform="telegram",
        code_hash=code(22),
        external_chat_id="-123",
        chat_kind="group",
        display_name="Other",
        owner_external_id="x",
    )
    assert again == {"knocked": False} and wrong == {"knocked": False}


def test_one_chat_is_connected_to_one_project(db):
    allowed_chat(db, n=31, chat="-131")
    other, user, _ = project(db)
    _, result = knock_telegram(db, other, user, n=32, chat="-131")
    assert result == {"knocked": False, "reason": "already_connected"}


def test_people_without_agent_manage_cannot_change_chats(db):
    project_id, user, org, chat = allowed_chat(db, n=41, chat="-141")
    editor = member(db, project_id, org, "editor")
    listed = db.human("list", editor, project_id=project_id)
    assert listed["can_manage"] is False and listed["connections"] == []
    assert [c["id"] for c in listed["conversations"]] == [chat]
    for action, payload in [
        ("update", {"conversation_id": chat, "enabled": False}),
        ("remove", {"conversation_id": chat}),
        ("start_link", {"platform": "telegram", "purpose": "knock", "code_hash": code(42)}),
    ]:
        with pytest.raises(CommandError, match="chat_app_denied"):
            db.human(action, editor, project_id=project_id, **payload)
    outsider, _, _ = project(db)
    with pytest.raises(CommandError, match="chat_app_denied"):
        db.human("list", user, project_id=outsider)


def test_settings_change_and_are_validated(db):
    project_id, user, _, chat = allowed_chat(db, n=51, chat="-151")
    changed = db.human(
        "update",
        user,
        project_id=project_id,
        conversation_id=chat,
        who_can_ask="project",
        daily_limit_units=None,
        display_name="  Launch  ",
    )
    assert changed["who_can_ask"] == "project" and changed["daily_limit_units"] is None
    assert changed["display_name"] == "Launch"
    for bad in [
        {"who_can_ask": "everyone"},
        {"display_name": "x" * 65},
        {"daily_limit_units": 0},
        {},
    ]:
        with pytest.raises(CommandError, match="chat_app_invalid_request"):
            db.human("update", user, project_id=project_id, conversation_id=chat, **bad)


def test_questions_in_one_thread_are_claimed_in_order(db):
    drain_inbox(db)
    _, _, _, chat = allowed_chat(db, n=61, chat="-161")
    thread = db.gateway("thread", conversation_id=chat, external_thread_key="t1")
    receive(db, "-161:1", chat="-161")
    first = db.gateway("claim_inbox")
    db.gateway(
        "finish_inbox",
        id=first["id"],
        fence=first["fence"],
        state="done",
        outcome="submitted",
        conversation_id=chat,
        thread_id=thread["id"],
        reply={"run_id": "00000000-0000-0000-0000-0000000000a1"},
    )
    receive(db, "-161:2", chat="-161")
    receive(db, "-161:3", chat="-161")
    second = db.gateway("claim_inbox")
    assert second["message_key"] == "-161:2"
    db.gateway(
        "finish_inbox",
        id=second["id"],
        fence=second["fence"],
        state="waiting",
        outcome="queued",
        conversation_id=chat,
        thread_id=thread["id"],
        reply={"notice_code": "queued"},
    )
    third = db.gateway("claim_inbox")
    db.gateway(
        "finish_inbox",
        id=third["id"],
        fence=third["fence"],
        state="waiting",
        outcome="queued",
        conversation_id=chat,
        thread_id=thread["id"],
        reply={"notice_code": "queued"},
    )
    # The thread is busy: neither waiting question can be claimed.
    assert db.gateway("claim_inbox") is None
    db.sql(
        f"UPDATE chat_app_replies SET state='done' WHERE thread_id='{thread['id']}' "
        "AND run_id IS NOT NULL"
    )
    assert db.gateway("claim_inbox")["message_key"] == "-161:2"


def test_an_expired_lease_is_claimed_again_and_the_old_fence_is_refused(db):
    drain_inbox(db)
    allowed_chat(db, n=71, chat="-171")
    receive(db, "-171:1", chat="-171")
    held = db.gateway("claim_inbox", worker="gateway-a", lease_seconds=30)
    assert db.gateway("claim_inbox", worker="gateway-b") is None
    db.sql(
        f"UPDATE chat_app_inbox SET lease_expires_at=now()-interval '1 second' WHERE id='{held['id']}'"
    )
    taken = db.gateway("claim_inbox", worker="gateway-b")
    assert taken["id"] == held["id"] and taken["fence"] == held["fence"] + 1
    with pytest.raises(CommandError, match="chat_app_stale_fence"):
        db.gateway(
            "finish_inbox",
            worker="gateway-a",
            id=held["id"],
            fence=held["fence"],
            state="done",
            outcome="late",
        )
    db.gateway(
        "finish_inbox",
        worker="gateway-b",
        id=taken["id"],
        fence=taken["fence"],
        state="done",
        outcome="ok",
    )


def test_one_wecom_bot_has_one_connection_holder(db):
    project_id, user, _ = project(db)
    db.human(
        "connect",
        user,
        project_id=project_id,
        platform="wecom",
        external_id="bot-1",
        secret="ciphertext-placeholder",
    )
    other, other_user, _ = project(db)
    with pytest.raises(CommandError, match="chat_app_connection_taken"):
        db.human(
            "connect",
            other_user,
            project_id=other,
            platform="wecom",
            external_id="bot-1",
            secret="ciphertext-placeholder",
        )
    held = None
    while (claimed := db.gateway("claim_connection", worker="gateway-a")) is not None:
        if claimed["external_id"] == "bot-1":
            held = claimed
    assert held is not None
    assert db.gateway("claim_connection", worker="gateway-b") is None
    with pytest.raises(CommandError, match="chat_app_stale_fence"):
        db.gateway("heartbeat", worker="gateway-b", id=held["id"], fence=held["fence"])
    db.gateway(
        "mark_connection",
        worker="gateway-a",
        id=held["id"],
        fence=held["fence"],
        status="displaced",
    )
    listed = db.human("list", user, project_id=project_id)
    assert [x["status"] for x in listed["connections"]] == ["displaced"]
    assert "secret" not in listed["connections"][0]


def test_wecom_knocks_only_with_the_code_for_that_bot(db):
    project_id, user, _ = project(db)
    bot = db.human(
        "connect",
        user,
        project_id=project_id,
        platform="wecom",
        external_id="bot-81",
        secret="ciphertext-placeholder",
    )
    db.sql(f"UPDATE chat_app_connections SET status='active' WHERE id='{bot['id']}'")
    db.human(
        "start_link",
        user,
        project_id=project_id,
        platform="wecom",
        purpose="knock",
        connection_id=bot["id"],
        code_hash=code(81),
    )
    missing = db.gateway(
        "knock",
        platform="wecom",
        connection_id=bot["id"],
        code_hash=code(82),
        external_chat_id="g1",
        chat_kind="group",
        display_name="WeCom group",
    )
    assert missing == {"knocked": False}
    ok = db.gateway(
        "knock",
        platform="wecom",
        connection_id=bot["id"],
        code_hash=code(81),
        external_chat_id="g1",
        chat_kind="group",
        display_name="WeCom group",
        knock_hint="482731",
        owner_external_id="enc-alex",
    )
    assert ok["knocked"] is True


def test_the_owner_leaving_stops_the_chat_until_someone_takes_over(db):
    project_id, creator, org = project(db)
    owner = member(db, project_id, org, "admin")
    _, result = knock_telegram(db, project_id, owner, n=91, chat="-191")
    chat = result["conversation_id"]
    db.human("decide", owner, project_id=project_id, conversation_id=chat, decision="allow")
    db.sql(f"UPDATE project_members SET role='viewer' WHERE user_id='{owner}'")
    context = db.gateway(
        "context", platform="telegram", external_chat_id="-191", asker_external_id="tg-alex"
    )
    assert context["conversation"]["stopped_reason"] == "owner_left"
    with pytest.raises(CommandError, match="chat_app_conflict"):
        db.human("update", creator, project_id=project_id, conversation_id=chat, enabled=False)
    taken = db.human("take_over", creator, project_id=project_id, conversation_id=chat)
    assert taken["owner_user_id"] == creator and taken["stopped_reason"] is None


def test_removing_a_chat_says_goodbye_and_names_runs_to_stop(db):
    project_id, user, _, chat = allowed_chat(db, n=101, chat="-201")
    run = "00000000-0000-0000-0000-0000000000b1"
    db.sql(
        f"INSERT INTO chat_app_replies(conversation_id,run_id,state) VALUES ('{chat}','{run}','active')"
    )
    removed = db.human("remove", user, project_id=project_id, conversation_id=chat)
    assert removed["state"] == "removed" and removed["stop_runs"] == [run]
    assert (
        db.sql(
            f"SELECT count(*) FROM chat_app_replies WHERE conversation_id='{chat}' AND notice_code='removed'"
        )
        == "1"
    )
    # The chat can be added again, even to another project.
    other, other_user, _ = project(db)
    _, again = knock_telegram(db, other, other_user, n=102, chat="-201")
    assert again["knocked"] is True


def test_slack_install_and_uninstall(db):
    project_id, user, _ = project(db)
    db.human(
        "start_link",
        user,
        project_id=project_id,
        platform="slack",
        purpose="slack_install",
        code_hash=code(111),
    )
    installed = db.gateway(
        "install_slack",
        code_hash=code(111),
        external_id="T1",
        display_name="Acme",
        secret="ciphertext-placeholder",
        installer_external_id="U-alex",
    )
    listed = db.human("list", user, project_id=project_id)
    assert listed["connections"][0]["linked_me"] is True
    db.human(
        "start_link",
        user,
        project_id=project_id,
        platform="slack",
        purpose="slack_wait",
        connection_id=installed["id"],
        code_hash=code(112),
    )
    knocked = db.gateway(
        "knock",
        platform="slack",
        connection_id=installed["id"],
        external_chat_id="C1",
        chat_kind="group",
        display_name="#launch",
        owner_external_id="U-alex",
    )
    db.human(
        "decide",
        user,
        project_id=project_id,
        conversation_id=knocked["conversation_id"],
        decision="allow",
    )
    db.gateway("revoke_connection", platform="slack", external_id="T1")
    chat = db.human("list", user, project_id=project_id)["conversations"][0]
    assert chat["stopped_reason"] == "removed_there"
    assert db.sql("SELECT secret IS NULL FROM chat_app_connections WHERE external_id='T1'") == "t"


def test_linking_an_identity_releases_the_held_question(db):
    drain_inbox(db)
    project_id, _, org, chat = allowed_chat(db, n=121, chat="-221")
    mia = member(db, project_id, org, "editor")
    db.gateway(
        "receive",
        platform="telegram",
        message_key="-221:1",
        via="message",
        kind="question",
        external_chat_id="-221",
        asker_external_id="tg-mia",
        asker_label="Mia",
    )
    row = db.gateway("claim_inbox")
    db.gateway(
        "finish_inbox",
        id=row["id"],
        fence=row["fence"],
        state="held",
        outcome="link_account",
        conversation_id=chat,
    )
    db.gateway(
        "identity_code",
        platform="telegram",
        scope="telegram",
        external_user_id="tg-mia",
        code_hash=code(122),
    )
    assert db.gateway("claim_inbox") is None
    db.human("link_identity", mia, code_hash=code(122))
    assert db.gateway("claim_inbox")["id"] == row["id"]
    context = db.gateway(
        "context", platform="telegram", external_chat_id="-221", asker_external_id="tg-mia"
    )
    assert context["asker_user_id"] == mia and context["asker_can_run"] is True
    with pytest.raises(CommandError, match="chat_app_link_expired"):
        db.human("link_identity", mia, code_hash=code(122))


def test_activity_keeps_the_old_kinds_and_adds_chat_app(db):
    project_id, user, _, chat = allowed_chat(db, n=131, chat="-231")
    db.human("update", user, project_id=project_id, conversation_id=chat, enabled=False)
    rows = db.json(
        "SELECT jsonb_agg(jsonb_build_object('status',status,'phase',phase,'label',label) "
        f"ORDER BY created_at) FROM context_activity_items WHERE project_id='{project_id}'"
    )
    assert [r["status"] for r in rows] == ["added", "allowed", "updated"]
    assert {r["phase"] for r in rows} == {"telegram"} and rows[0]["label"] == "Design team"
    columns = db.sql(
        "SELECT string_agg(column_name||':'||data_type, ',' ORDER BY ordinal_position) "
        "FROM information_schema.columns WHERE table_name='context_activity_items'"
    )
    assert columns == (
        "id:text,kind:text,project_id:text,created_by:uuid,label:text,status:text,phase:text,"
        "progress:integer,message:text,error_message:text,result_path:text,result_commit_id:text,"
        "created_at:timestamp with time zone,completed_at:timestamp with time zone"
    )
    kinds = db.sql("SELECT pg_get_viewdef('context_activity_items'::regclass)")
    for kind in ("'upload'", "'import'", "'synchronize_run'", "'chat_app'"):
        assert kind in kinds


def test_clients_have_no_direct_access(db):
    grants = db.sql(
        "SELECT count(*) FROM information_schema.role_table_grants "
        "WHERE table_name LIKE 'chat_app_%' AND grantee IN ('anon','authenticated')"
    )
    assert grants == "0"
    assert (
        db.sql(
            "SELECT bool_and(relrowsecurity) FROM pg_class WHERE relname LIKE 'chat_app_%' AND relkind='r'"
        )
        == "t"
    )
